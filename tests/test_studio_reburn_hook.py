# tests/test_studio_reburn_hook.py — Face 4: inline editable on-screen HOOK with re-burn (NO LLM). The
# operator edits the literal per-account hook text; actions.reburn_hook re-burns it via ffmpeg
# (overlay.burn_hook_only) onto the SAME deterministic variant path /media serves, then flips
# Post.variant_hook + Post.media_urls ONLY (carried by repost_post — survives 'Post again'). It NEVER
# writes clip.meta_captions['hook'] (dead key). Gated on creative_variation; fail-open (warn, no rollback).
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt,
                           PLATFORM_ASPECT)
from fanops.accounts import Accounts
from fanops.ids import child_id
from fanops.studio.actions import reburn_hook
from fanops.studio.app import _media_path_for_post


def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
FUTURE = "2099-01-01T00:00:00Z"        # stays editable (not imminent) under real wall-clock too
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg, *, platform=Platform.instagram, state=PostState.awaiting_approval, hook="OLD HOOK",
          meta=None):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16,
                      state=ClipState.captioned, meta_captions=(meta or {})))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1", platform=platform,
                      caption="c", state=state, variant_hook=hook, scheduled_time=FUTURE, public_url="dryrun://p_edit"))
    led.save(); return led

def _expected_render(cfg, post, hook):
    # Stage D: the hook EDIT yields a content-addressed Render id (child_id of clip+hook); reburn files it
    # via cfg.render_path (src_1 has no batch -> "unbatched"/"src_1"). The post points at it via render_id.
    rid = child_id("render", post.parent_id, hook)
    aspect = PLATFORM_ASPECT.get(post.platform, Fmt.r9x16)
    return rid, cfg.render_path(None, "src_1", rid, aspect)


def test_reburn_mints_render_sets_render_id_and_mirror(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    burn = mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is True and res.detail["hook_burned"] is True
    led = Ledger.load(cfg); p = led.posts["p_edit"]
    rid, exp = _expected_render(cfg, p, "NEW HOOK")
    # the Render is the single source of truth: its hook_text == the burned hook; post mirrors it.
    assert p.render_id == rid and led.get_render(rid) is not None and led.get_render(rid).hook_text == "NEW HOOK"
    assert p.variant_hook == "NEW HOOK"                # read-only mirror (no drift vs the render)
    assert p.media_urls == [f"file://{exp}"] and led.get_render(rid).path == exp
    burn.assert_called_once()                          # ffmpeg re-burn happened (lock-free)

def test_reburn_path_is_deterministic_media_path_non_9x16(tmp_path, monkeypatch, mocker):
    # YouTube (16:9) surface: the render path derives from the content-addressed render id, so /media (via
    # _media_path_for_post -> render_id -> Render.path) serves exactly the file written.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg, platform=Platform.youtube)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    assert reburn_hook(cfg, "p_edit", "YT HOOK").ok is True
    p = Ledger.load(cfg).posts["p_edit"]
    rid, exp = _expected_render(cfg, p, "YT HOOK")
    assert p.render_id == rid and p.media_urls == [f"file://{exp}"]
    assert _media_path_for_post(Ledger.load(cfg), "p_edit") == exp     # /media serves the re-burned render

def test_reburn_does_not_touch_meta_captions(tmp_path, monkeypatch, mocker):
    # the dead-key lock (A1): reburn writes post-level fields ONLY, never clip.meta_captions['hook'].
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    seeded = {"a/instagram": {"caption": "c", "hashtags": ["#x"]}}
    cfg = Config(root=tmp_path); _seed(cfg, meta=seeded)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    reburn_hook(cfg, "p_edit", "NEW HOOK")
    mc = Ledger.load(cfg).clips["clip_1"].meta_captions
    assert mc == seeded and "hook" not in mc.get("a/instagram", {})   # untouched, no dead-key write

def test_reburn_hook_burn_failed_warns_not_rollback(tmp_path, monkeypatch, mocker):
    # burn returns False (no libass / nothing burnable) -> ok=True, detail.hook_burned=False, and the
    # variant_hook IS still updated (an EDIT, surfaced, never silent) — unlike approve_with_hook (rollback).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=False)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is True and res.detail["hook_burned"] is False
    assert Ledger.load(cfg).posts["p_edit"].variant_hook == "NEW HOOK"

def test_reburn_rejected_when_variation_off(tmp_path, mocker, monkeypatch):
    # M3d: creative_variation now DEFAULTS ON, so pin it OFF here -> per-surface hooks don't exist -> reject.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed(cfg)
    burn = mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False and "variation" in (res.error or "").lower()
    burn.assert_not_called()                           # no ffmpeg work on the rejected path

def test_reburn_rejects_non_editable(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False and ("published" in (res.error or "") or "editable" in (res.error or ""))

def test_reburn_unknown_post(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reburn_hook(cfg, "nope", "NEW HOOK")
    assert res.ok is False and "no such post" in (res.error or "").lower()


# ---- AUDIT H1: re-burn of an OVERRIDE account preserves the per-account CUT (no silent revert) ----
def test_reburn_override_account_preserves_per_account_cut(tmp_path, monkeypatch, mocker):
    # For an OVERRIDE account (its own clip_profile/framing differs from global), editing the on-screen hook
    # must RE-CUT the source at the account's band+crop — NOT silently revert to a bare-hook, global-length,
    # centred shared-clip burn. The reburn render_id MUST equal the crosspost mint's tagged id (single source:
    # account_render_spec), so the post keeps pointing at its own length/framing. Reachable because
    # creative_variation defaults ON. Default accounts are byte-identical (covered by the bare-hook tests above).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                     "status": "active", "clip_profile": "short"}])     # band 8-15s != global talk 12-22s -> a CUT
    cut = mocker.patch("fanops.clip.render_account_cut", return_value=(True, 12.0))   # P3: (produced, realized_seconds)
    burn = mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is True
    led = Ledger.load(cfg); p = led.posts["p_edit"]
    from fanops.crosspost import account_render_spec
    acct = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    exp_rid, wants_cut, _, _ = account_render_spec(cfg, clip=led.clips["clip_1"], hook="NEW HOOK", acct=acct)
    assert wants_cut is True                                            # sanity: this account genuinely wants a cut
    assert p.render_id == exp_rid                                       # parity with the crosspost mint id (band-tagged)
    assert exp_rid != child_id("render", "clip_1", "NEW HOOK")          # NOT the bare-hook id (the H1 bug)
    cut.assert_called_once()                                            # the per-account CUT ran...
    burn.assert_not_called()                                           # ...NOT the global shared-clip burn
    assert led.get_render(exp_rid).is_account_cut is True               # provenance truthful


# ---- route ----
def test_reburn_route_swaps_edit_field(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    monkeypatch.setattr("fanops.overlay.burn_hook_only", lambda *a, **k: True)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/p_edit", data={"hook": "ROUTED HOOK"})
    assert r.status_code == 200 and b"ROUTED HOOK" in r.data
    assert Ledger.load(cfg).posts["p_edit"].variant_hook == "ROUTED HOOK"

def test_reburn_route_unknown_post_clean_error(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/nope", data={"hook": "x"})
    assert r.status_code == 200 and b"no such post" in r.data
