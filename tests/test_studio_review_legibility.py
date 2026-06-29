# tests/test_studio_review_legibility.py — S4: make Review legible. The card already shows WHAT each surface
# is (length/cut/framing/hook); S4 shows WHY by consuming S2's _prov cause_chip macro, NAMES the accounts a
# batch target excludes (not a bare count), and gives every empty matrix "—" cell a reason (off-target /
# budget / no-platform, deterministic precedence). Additive: under the OFF firewall (legacy accounts, no pins)
# the card surface-spec renders nothing new. Mix of pure unit (helper + builder) and route-render assertions.
import json
from datetime import datetime, timezone
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Account, Accounts
from fanops.models import (Source, Moment, Clip, Post, Render, Platform, PostState, ClipState,
                           MomentState, RenderState, Fmt, Batch)
from fanops.studio import views

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# ── the empty-cell reason helper: off-target > budget > no-platform, else None ─────────────────────
def test_empty_cell_reason_off_target_wins():
    acct = Account(handle="@a", account_id="1", platforms=[Platform.instagram])
    # off-target AND uncast AND on-platform -> off-target is the highest-precedence reason
    r = views._empty_cell_reason("@a", "instagram", targets=["@b"], affinities=["@b"], acct=acct)
    assert r == "off-target"


def test_empty_cell_reason_budget_when_in_target_but_uncast():
    acct = Account(handle="@a", account_id="1", platforms=[Platform.instagram])
    r = views._empty_cell_reason("@a", "instagram", targets=["@a", "@b"], affinities=["@b"], acct=acct)
    assert r == "budget"                                   # in the batch target, but the cast didn't pick it


def test_empty_cell_reason_no_platform_when_in_scope_and_cast():
    acct = Account(handle="@a", account_id="1", platforms=[Platform.instagram])
    r = views._empty_cell_reason("@a", "tiktok", targets=["@a"], affinities=["@a"], acct=acct)
    assert r == "no tiktok"                                # in target + cast, but the account has no TikTok


def test_empty_cell_reason_none_when_in_scope_on_platform_cast():
    acct = Account(handle="@a", account_id="1", platforms=[Platform.instagram])
    assert views._empty_cell_reason("@a", "instagram", targets=[], affinities=[], acct=acct) is None
    assert views._empty_cell_reason("@a", "instagram", targets=["@a"], affinities=["@a"], acct=acct) is None


def test_empty_cell_reason_never_raises_on_odd_acct():
    assert views._empty_cell_reason("@a", "instagram", targets=[], affinities=[], acct=None) is None


# ── the card names the excluded accounts (not just a count) ─────────────────────────────────────────
def _seed_batch_excluded(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"},
        {"handle": "@c", "account_id": "3", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="bat_1", name="Launch", target_accounts=["@a"]))   # only @a targeted -> @b,@c excluded
        led.add_source(Source(id="s", source_path="/v.mp4", batch_id="bat_1"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, batch_id="bat_1", public_url=f"dryrun://p"))


def test_card_carries_excluded_names(tmp_path):
    cfg = Config(root=tmp_path); _seed_batch_excluded(cfg)
    led = Ledger.load(cfg); accounts = Accounts.load(cfg)
    cards = views.review_buckets(led, accounts, cfg, now=NOW)
    card = next(c for c in cards if c.bucket == "editable")
    assert card.batch_excluded_names == ["@b", "@c"]        # NAMED + sorted, not a bare count
    assert card.batch_excluded == 2                         # the legacy count is preserved


def test_review_html_names_excluded_accounts(tmp_path):
    cfg = Config(root=tmp_path); _seed_batch_excluded(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "@b" in html and "@c" in html and "excluded" in html   # the names reach the worklist, not just "2"


# ── the card surface-spec consumes the _prov cause_chip macro (not a parallel hand-rolled chip) ─────
def _seed_persona_cut(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@long", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "persona_id": "hype", "clip_profile": "long", "framing": "top"}]}))
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    # M3d: a persona no longer PINS clip_profile — it DERIVES the cut from content_focus (storytelling -> long),
    # which still sets persona_owns_profile=True so the card attributes the length to the persona.
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "hype", "voice": "hype", "content_focus": ["storytelling"]}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); r = cfg.clips / "r.mp4"; r.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped, affinities=["@long"]))
        led.add_clip(Clip(id="c", parent_id="m", path=str(r), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_render(Render(id="r1", clip_id="c", account="@long", surface_key="@long/instagram",
                              hook_text="H", path=str(r), state=RenderState.rendered, is_account_cut=True))
        led.add_post(Post(id="p", parent_id="c", account="@long", account_id="1", platform=Platform.instagram,
                          caption="c", state=PostState.awaiting_approval, render_id="r1", clip_profile="long", public_url=f"dryrun://p"))


def test_card_renders_cause_via_macro(tmp_path):
    cfg = Config(root=tmp_path); _seed_persona_cut(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "28–45s" in html                                # the long band label still shows
    assert 'class="cause"' in html                         # the _prov macro's inline cause marker is present
    assert "persona long" in html                          # the WHY: the persona owns the length
    assert "clip length band" not in html                  # the OLD hand-rolled parallel chip title is GONE


# ── OFF firewall: legacy accounts (no pins/persona) render no new spec chips ────────────────────────
def _seed_legacy(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url=f"dryrun://p"))


def test_off_firewall_no_cause_chips(tmp_path, monkeypatch):
    # a STRICTLY legacy account (no pins, no persona) under both flags OFF: the differentiation row is fully absent,
    # exactly as before S4. (A pinned account under OFF is a DIFFERENT contract — see the next test.)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_legacy(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert 'class="cause"' not in html                     # no attribution markers on a legacy surface
    assert "surface-spec" not in html                      # the whole differentiation chip row stays absent


def _seed_pin_only(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, clip_profile="long", public_url=f"dryrun://p"))


def test_off_with_pin_shows_cause_additively(tmp_path, monkeypatch):
    # the HONEST contract (not "byte-identical"): an account that PINS clip_profile already showed a length chip
    # pre-S4; S4 ADDITIVELY attributes it. The length band stays visible AND now carries its cause — a true fact
    # (the account pins long) independent of the differentiation flags. No NEW chip type, no shipped-artifact change.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_pin_only(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "28–45s" in html                                # the length chip is still visible (existing behavior)
    assert "@a long" in html and 'class="cause"' in html   # S4's additive attribution: the account pins long
    assert "shared-cut" not in html                        # but no per-account-cut WARN under OFF (firewall holds)


def _seed_recent_no_cut(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.published, clip_profile="long",
                          scheduled_time=datetime.now(timezone.utc).isoformat(), public_url="dryrun://p"))   # recent-bucket card: seed relative to REAL now (the /review route uses datetime.now), not the fixed NOW — else it ages out of RECENT_WINDOW_HOURS and the test time-bombs


def test_shared_cut_warn_suppressed_off_the_editable_worklist(tmp_path):
    # audit HIGH: a SHIPPED (recent) card with no per-account cut, creative_variation ON. The ⚠ shared-cut WARN is
    # an editable-worklist signal (act before approval) — it must NOT appear on a recent card you can't change.
    cfg = Config(root=tmp_path); _seed_recent_no_cut(cfg)          # creative_variation defaults ON
    html = _client(cfg).get("/review").data.decode()
    assert "28–45s" in html                                # the recent card's length chip still renders
    assert "shared-cut" not in html                        # the bucket gate suppresses the actionable warn here


def _seed_editable_no_cut(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, clip_profile="long", public_url=f"dryrun://p"))


def test_shared_cut_warn_still_fires_on_editable_card(tmp_path):
    # the gate's other half: on the EDITABLE worklist (?view=list cards) under creative_variation ON, a surface
    # with no per-account cut DOES show the actionable ⚠ shared-cut — the warn isn't gone, just scoped.
    cfg = Config(root=tmp_path); _seed_editable_no_cut(cfg)        # creative_variation defaults ON
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "shared-cut" in html                            # fires where it's actionable


def test_pivot_off_firewall_no_shared_cut_warn(tmp_path, monkeypatch):
    # audit LOW: the account-pivot (?view=account) hand-rolled shared-cut chip lacked the creative_variation gate,
    # so under OFF every non-cut surface wrongly warned. S4 gates it — under OFF the warn is absent.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_editable_no_cut(cfg)
    html = _client(cfg).get("/review?view=account&account=@a").data.decode()
    assert "shared-cut" not in html                        # a shared cut under OFF is expected, never a warning
