"""M1 + M2 + M7 RED — operator timezone, realistic 2-3h cadence, per-account window seam.

These three milestones close out the realistic-per-account-scheduling PRD. They are bundled in
ONE file because they share fixtures (a config + an account) and they each test a single, narrow
property — three tiny files would dwarf the assertions in boilerplate.

M1: every scheduled time displays / parses in the operator's CONFIGURED timezone, not the
    server's silent astimezone() default. Storage stays canonical UTC; conversion happens at the
    web boundary.

M2: the per-account cadence engine spaces approved posts on a 2-3h human cadence (PRD: "leaning
    jittered 2-3h"). The M4 floor (30 min) was the SAFE LOWER BOUND for any bulk-approve; M2
    widens the DEFAULT to the human-readable 2-3h band so a Reschedule produces a believable feed.

M7: cfg.account_window(handle) returns (open_hour, close_hour) | None — None == 24h open
    (default). The cadence engine reads this seam so a future analytics surface can populate it
    without re-architecting."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import iso_z, parse_iso, to_local_display, to_local_input, local_input_to_utc_z

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)


# ────────────────────────────────────────────────────────────────────────────
# M1 — explicit operator timezone
# ────────────────────────────────────────────────────────────────────────────

def test_cfg_operator_tz_default_is_utc(tmp_path, monkeypatch):
    """RED: an unset FANOPS_OPERATOR_TZ -> cfg.operator_tz == 'UTC'. NOT the server's astimezone()
    default (which is the actual bug: a server in PST silently rendered every time in PST without
    labelling it). UTC is the safe documented fallback."""
    monkeypatch.delenv("FANOPS_OPERATOR_TZ", raising=False)
    cfg = Config(root=tmp_path)
    assert hasattr(cfg, "operator_tz"), "Config has no operator_tz property — M1 not built"
    assert cfg.operator_tz == "UTC", f"unset default should be UTC, got {cfg.operator_tz!r}"


def test_cfg_operator_tz_reads_env(tmp_path, monkeypatch):
    """RED: setting FANOPS_OPERATOR_TZ=America/New_York reads through cfg.operator_tz."""
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "America/New_York")
    cfg = Config(root=tmp_path)
    assert cfg.operator_tz == "America/New_York"


def test_to_local_display_uses_configured_tz_not_system(tmp_path, monkeypatch):
    """RED: to_local_display renders in cfg.operator_tz, not the process's system tz. NY at the
    fixed UTC noon is 08:00 EDT (DST on 2026-06-29)."""
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "America/New_York")
    cfg = Config(root=tmp_path)
    rendered = to_local_display(FIXED_ISO, cfg=cfg)         # new signature: cfg kwarg
    assert "08:00" in rendered, (
        f"NY at UTC noon should render 08:00 EDT, got {rendered!r}")
    assert "EDT" in rendered or "-04" in rendered or "EST" in rendered, (
        f"rendered label should carry an NY tz marker, got {rendered!r}")


def test_local_input_to_utc_z_uses_configured_tz(tmp_path, monkeypatch):
    """RED: an operator types '2026-07-01T09:00' in NY local; the parser writes
    '2026-07-01T13:00:00Z' (EDT = UTC-4)."""
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "America/New_York")
    cfg = Config(root=tmp_path)
    z = local_input_to_utc_z("2026-07-01T09:00", cfg=cfg)
    assert z == "2026-07-01T13:00:00Z", (
        f"NY 09:00 EDT should be UTC 13:00, got {z!r}")


def test_unset_operator_tz_renders_utc_label(tmp_path, monkeypatch):
    """RED: with operator_tz unset (UTC default), to_local_display renders the UTC label —
    never falls through to the server's silent astimezone() default."""
    monkeypatch.delenv("FANOPS_OPERATOR_TZ", raising=False)
    cfg = Config(root=tmp_path)
    rendered = to_local_display(FIXED_ISO, cfg=cfg)
    assert "12:00" in rendered, f"UTC label should keep 12:00, got {rendered!r}"
    assert "UTC" in rendered, f"label should explicitly say UTC, got {rendered!r}"


# ────────────────────────────────────────────────────────────────────────────
# M2 — realistic per-account cadence (2-3h jittered)
# ────────────────────────────────────────────────────────────────────────────

def _seed_accounts(cfg: Config) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"}]}))


def _seed_clip(led: Ledger) -> Clip:
    led.add_source(Source(id="src_1", source_path="/s.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    return clip


def _seed_queued_posts(led: Ledger, clip: Clip, *, n: int, base_iso: str) -> list[str]:
    ids: list[str] = []
    for k in range(n):
        pid = f"p_{k}"
        led.add_post(Post(id=pid, parent_id=clip.id, account="@a", account_id="ia",
                          platform=Platform.instagram, caption="c", state=PostState.queued,
                          scheduled_time=base_iso, media_urls=["file:///clip_1_9x16.mp4"], public_url=f"dryrun://ia"))
        ids.append(pid)
    return ids


def test_reschedule_cadence_2_to_3_hours_jittered(tmp_path, monkeypatch):
    """RED: when cfg.realistic_cadence is ON, reschedule_bucket spaces posts 2-3h apart, not the
    M4 floor's 30 min. Same cumulative-walk floor-by-construction property — every consecutive
    gap >= 2h, <= 3h + jitter ceiling."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_REALISTIC_CADENCE", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    assert cfg.realistic_cadence is True, "FANOPS_REALISTIC_CADENCE=1 not reading through cfg"

    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    yesterday_iso = iso_z(FIXED_DT - timedelta(days=1))
    _seed_queued_posts(led, clip, n=5, base_iso=yesterday_iso)
    led.save()

    from fanops.studio.actions import reschedule_bucket
    res = reschedule_bucket(cfg, now=FIXED_DT)
    assert res.ok is True and res.detail["rescheduled"] == 5, (
        f"expected 5 respread, got {res.detail}")
    reloaded = Ledger.load(cfg)
    dts = sorted(parse_iso(reloaded.posts[f"p_{k}"].scheduled_time) for k in range(5))
    gaps_min = [(b - a).total_seconds() / 60.0 for a, b in zip(dts, dts[1:])]
    assert all(g >= 120.0 for g in gaps_min), (
        f"realistic cadence gap floor 2h violated: gaps_min={gaps_min}")
    assert all(g <= 180.0 + 15.0 for g in gaps_min), (         # 3h + jitter ceiling
        f"realistic cadence ceiling 3h+jitter violated: gaps_min={gaps_min}")


def test_realistic_cadence_default_off_preserves_m4_floor(tmp_path, monkeypatch):
    """RED (default-OFF firewall): unset FANOPS_REALISTIC_CADENCE -> cfg.realistic_cadence False ->
    the M4 30-min floor still applies. Byte-identical behaviour to today; M2 is opt-in."""
    monkeypatch.delenv("FANOPS_REALISTIC_CADENCE", raising=False)
    cfg = Config(root=tmp_path)
    assert hasattr(cfg, "realistic_cadence"), "cfg.realistic_cadence not defined"
    assert cfg.realistic_cadence is False


# ────────────────────────────────────────────────────────────────────────────
# M7 — per-account daily window seam
# ────────────────────────────────────────────────────────────────────────────

def test_cfg_account_window_default_is_none(tmp_path, monkeypatch):
    """RED: cfg.account_window('@a') returns None for an account with no daily_window field —
    None == 24h open (PRD: 'default open, populated later')."""
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    assert hasattr(cfg, "account_window"), "cfg.account_window not built — M7 seam missing"
    win = cfg.account_window("@a")
    assert win is None, f"unset daily_window should be None (24h open), got {win!r}"


def test_cfg_account_window_reads_accounts_json(tmp_path, monkeypatch):
    """RED: an accounts.json account with daily_window=[9, 23] is read through
    cfg.account_window('@a') as (9, 23)."""
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]}]}))
    win = cfg.account_window("@a")
    assert win == (9, 23), f"daily_window=[9,23] should read as (9, 23), got {win!r}"


def test_cfg_account_window_unknown_handle_returns_none(tmp_path, monkeypatch):
    """RED: an unknown handle (typo / removed account) -> None, never raises. Fail-open in the
    seam: an analytics surface that names a handle the cadence engine doesn't recognise gets a
    24h-open answer, not a 500."""
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    assert cfg.account_window("@nope") is None
