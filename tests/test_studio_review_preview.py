# tests/test_studio_review_preview.py — the Review card must show PER-ACCOUNT video + burned hook so
# persona differentiation is visible (not one shared clip with caption-only rows). RED until the card
# switcher + SurfacePost.variant_hook land.
import json
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")    # Studio is an optional [studio] extra — skip cleanly when Flask is absent
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt, Render, RenderState

NOW = datetime.now(timezone.utc).replace(microsecond=0)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config["TESTING"] = True
    return app.test_client()

def _seed_personas(cfg, *, hooks=True, two_clips=False):
    """Two awaiting surfaces on ONE clip (markmakmouly + perca.late), each with its OWN burned-hook variant
    mp4 — the persona-differentiation shape the Review card must display. hooks=False => OFF mode (no
    variant_hook, no media_urls, shared base clip). two_clips=True => a 2nd card to test radio-name uniqueness."""
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "markmakmouly", "account_id": "", "platforms": ["instagram"], "status": "active", "access": "postiz", "persona": "craft", "integrations": {"instagram": "ig1"}},
        {"handle": "perca.late", "account_id": "", "platforms": ["instagram"], "status": "active", "access": "postiz", "persona": "raw", "integrations": {"instagram": "ig2"}}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    va = cfg.clips / "va.mp4"; va.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANTA")
    vb = cfg.clips / "vb.mp4"; vb.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANTB")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    def _post(pid, acct, media, hook):
        return Post(id=pid, parent_id="clip_1", account=acct, account_id="", platform=Platform.instagram,
                    caption=f"#{acct[:4]}tag", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=5)),
                    media_urls=([f"file://{media}"] if hooks else []), variant_hook=(hook if hooks else None))
    led.add_post(_post("p_mark", "markmakmouly", va, "WATCH THE CRAFT"))
    led.add_post(_post("p_perc", "perca.late", vb, "RAW BARS NO POLISH"))
    if two_clips:
        led.add_clip(Clip(id="clip_2", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p_two", parent_id="clip_2", account="markmakmouly", account_id="", platform=Platform.instagram,
                          caption="#x", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=6)),
                          media_urls=[f"file://{va}"], variant_hook="SECOND CARD"))
    led.save()

def test_card_renders_per_account_media(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg)
    html = _client(cfg).get("/review").data.decode()
    # each account's OWN video is addressable in the card (not just one shared /clips/{id})
    assert "/media/p_mark" in html and "/media/p_perc" in html
    # each burned hook text is shown so the operator can compare personas
    assert "WATCH THE CRAFT" in html and "RAW BARS NO POLISH" in html

def test_card_degrades_when_no_variant(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg, hooks=False)
    html = _client(cfg).get("/review").data.decode()
    assert "/media/p_mark" in html and "/media/p_perc" in html   # still per-post (serves base clip), no crash
    assert "WATCH THE CRAFT" not in html                          # no hook line when nothing was burned

def test_preview_tabs_unique_per_card(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg, two_clips=True)
    html = _client(cfg).get("/review").data.decode()
    assert "preview-clip_1" in html and "preview-clip_2" in html  # per-card radio group name -> no cross-toggle

def test_preview_tab_a11y(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg)
    html = _client(cfg).get("/review").data.decode()
    # each account tab carries an accessible name referencing the account (aria-label or visible label text)
    assert "markmakmouly" in html and "perca.late" in html
    assert 'aria-label="show markmakmouly' in html or 'aria-label="preview markmakmouly' in html

def test_card_shows_per_account_length_cut_and_framing(tmp_path):
    # M3a: the operator SEES the per-account differentiation — clip LENGTH band, a CUT marker for a genuine
    # per-account cut, and the pinned FRAMING — rendered on the surface in the Review HTML.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@long", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "clip_profile": "long", "framing": "top"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    r = cfg.clips / "r.mp4"; r.write_bytes(b"\x00\x00\x00\x18ftypmp42CUT")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(r), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_render(Render(id="r1", clip_id="clip_1", account="@long", surface_key="@long/instagram",
                          hook_text="H", path=str(r), state=RenderState.rendered, is_account_cut=True))
    led.add_post(Post(id="p_long", parent_id="clip_1", account="@long", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, render_id="r1", clip_profile="long",
                      scheduled_time=_z(NOW + timedelta(hours=5)))); led.save()
    html = _client(cfg).get("/review").data.decode()
    assert "28–45s" in html                                    # the long band length label
    assert "a real per-account cut" in html                    # the cut chip (title) — genuine per-account render
    assert "vertical crop framing" in html and ">top<" in html  # the pinned framing chip
