"""M5 RED — the Posted tab must distinguish a dryrun-success row from a live-success row.

The operator's verbatim complaint: 'the system says posted when nothing is posted — dry run has to
be disabled now.' Today the Posted tab renders any `PostState.published` row identically,
regardless of whether the row came from a real platform publish (live) or a synthetic dryrun
no-op (where DryRunPoster never set `public_url`).

The bad path: a published row with `public_url is None` reads as 'pending — link fills in later'
in the template; the operator cannot tell scanning the cockpit that no real platform ever saw the
post. The fix is structural: every PostedRow carries `posted_via in {"live", "dryrun"}` derived
deterministically from `public_url`, and the template renders a distinct chip per channel + a
global mode banner above the table."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import iso_z
from fanops.studio.views_results import posted_library

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)


def _seed_accounts(cfg: Config) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"}]}))


def _seed_clip(led: Ledger) -> Clip:
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080,
                          duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    return clip


def _seed_post(led: Ledger, clip: Clip, *, post_id: str, public_url: str | None) -> str:
    p = Post(id=post_id, parent_id=clip.id, account="@a", account_id="ia",
             platform=Platform.instagram, caption="c", state=PostState.published,
             scheduled_time=FIXED_ISO, media_urls=[f"file:///clip_1_9x16.mp4"],
             public_url=public_url, published_at=FIXED_ISO)
    led.add_post(p)
    return p.id


def test_posted_row_with_no_public_url_labels_dryrun(tmp_path, monkeypatch):
    """RED: a published post without a real public_url is a dryrun-success — the only path that
    flips to PostState.published without setting public_url is the DryRunPoster->publish_post
    transition. The PostedRow MUST surface this as posted_via='dryrun' so the operator can see at
    a glance that no real platform saw the post."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    _seed_post(led, clip, post_id="p_dry", public_url=None)        # the dryrun signature
    led.save()

    rows = posted_library(Ledger.load(cfg), cfg)
    assert len(rows) == 1
    assert hasattr(rows[0], "posted_via"), (
        "PostedRow has no posted_via field — the operator cannot tell dryrun from live")
    assert rows[0].posted_via == "dryrun", (
        f"public_url=None should label 'dryrun', got posted_via={rows[0].posted_via!r}")


def test_posted_row_with_https_public_url_labels_live(tmp_path, monkeypatch):
    """RED: a published post with a real https public_url is a live-success — only reconcile.py
    sets public_url from a real provider permalink. posted_via='live' so the operator can see at
    a glance that the platform actually saw the post."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    _seed_post(led, clip, post_id="p_live", public_url="https://www.instagram.com/p/ABCDEFG/")
    led.save()

    rows = posted_library(Ledger.load(cfg), cfg)
    assert len(rows) == 1
    assert rows[0].posted_via == "live", (
        f"https public_url should label 'live', got posted_via={rows[0].posted_via!r}")


def test_posted_row_with_dryrun_scheme_labels_dryrun(tmp_path, monkeypatch):
    """Belt-and-braces: a (future) writer that stamps a synthetic dryrun:// URL still labels as
    dryrun. The classifier is on the URL SCHEME, not just on presence — a dryrun:// is dryrun
    even though it's truthy."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    _seed_post(led, clip, post_id="p_dry2", public_url="dryrun://p_dry2")
    led.save()

    rows = posted_library(Ledger.load(cfg), cfg)
    assert len(rows) == 1
    assert rows[0].posted_via == "dryrun", (
        f"dryrun:// scheme should label 'dryrun', got posted_via={rows[0].posted_via!r}")


def test_posted_template_renders_channel_chip(tmp_path, monkeypatch):
    """RED: the rendered Posted page must show a visible channel chip per row, identifiable by
    a data-testid='posted-channel-chip' attribute so the operator (and Playwright) can read it.
    Mixed dryrun + live rows render different chip text ('dryrun' vs 'live')."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    _seed_post(led, clip, post_id="p_dry", public_url=None)
    _seed_post(led, clip, post_id="p_live", public_url="https://www.instagram.com/p/X/")
    led.save()

    from fanops.studio.app import create_app
    app = create_app(cfg)
    client = app.test_client()
    resp = client.get("/posted")
    assert resp.status_code == 200, f"Posted page returned {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert 'data-testid="posted-channel-chip"' in body, (
        "Posted template does not render a per-row channel chip with data-testid='posted-channel-chip'")
    # Both labels must appear in the page (one for each row).
    assert ">dryrun<" in body, "no 'dryrun' chip rendered for the dryrun row"
    assert ">live<" in body, "no 'live' chip rendered for the live row"


def test_posted_template_renders_global_mode_banner(tmp_path, monkeypatch):
    """RED: the Posted page must carry a global mode banner so the operator knows the SYSTEM mode
    (live vs dryrun) at a glance, separately from the per-row channel chip. The banner is keyed
    on cfg.is_live, identifiable by data-testid='posted-mode-banner'."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    # No posts needed — the banner shows regardless.
    from fanops.studio.app import create_app
    app = create_app(cfg)
    client = app.test_client()
    resp = client.get("/posted")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-testid="posted-mode-banner"' in body, (
        "Posted template does not render a global mode banner with data-testid='posted-mode-banner'")
    # System is dryrun (FANOPS_POSTER=dryrun, no FANOPS_LIVE), so banner must say so.
    assert "dryrun" in body.lower(), "banner does not surface the dryrun mode"
