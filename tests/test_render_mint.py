# tests/test_render_mint.py — Stage B of the per-account Render foundation: crosspost mints a
# content-addressed Render per distinct (clip, hook) under creative_variation, files it under
# clips/{batch}/{source}/, and stamps Post.render_id. Dedup: two surfaces with the SAME hook on the
# SAME (aspect-)clip share ONE Render/file. Empty persona hook falls back to the shared m.hook (never a
# textless ship). cv OFF / hookless -> no Render, render_id None, byte-identical. Caption read UNCHANGED.
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _mock_burn(mocker):
    # isolate from ffmpeg: write the out file (so Render.path exists) and report a real burn.
    def burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"V"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)

def _seed_clip(led, cfg, *, hooks_by_persona=None, m_hook=None, surfaces=("@a/instagram",), batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook, hooks_by_persona=hooks_by_persona or {}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _run(cfg):
    led = Ledger.load(cfg)
    return crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")


# ---- per-account renders minted, content-addressed, render_id stamped ----
def test_distinct_hooks_mint_distinct_renders(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "hook A", "@b": "hook B"},
               surfaces=("@a/instagram", "@b/instagram")); led.save()
    led = _run(cfg)
    assert len(led.renders) == 2                                     # two distinct hooks -> two renders
    posts = {p.account: p for p in led.posts.values()}
    assert posts["@a"].render_id and posts["@b"].render_id and posts["@a"].render_id != posts["@b"].render_id
    assert led.get_render(posts["@a"].render_id).hook_text == "hook A"
    assert posts["@a"].variant_hook == "hook A"                      # mirror
    assert posts["@a"].media_urls == [f"file://{led.get_render(posts['@a'].render_id).path}"]

def test_same_hook_same_clip_dedups_to_one_render(tmp_path, monkeypatch, mocker):
    # one account on TWO 9:16 platforms (ig+tiktok) with the SAME per-account hook -> SAME aspect-clip,
    # SAME hook -> ONE content-addressed Render + ONE file, two posts pointing at it (anti-explosion).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"],
                          "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "one hook"},
               surfaces=("@a/instagram", "@a/tiktok")); led.save()
    led = _run(cfg)
    assert len(led.renders) == 1                                     # deduped to a single render/file
    rids = {p.render_id for p in led.posts.values()}
    assert len(rids) == 1 and None not in rids                       # both posts share the one render

def test_empty_persona_hook_falls_back_to_shared_hook(tmp_path, monkeypatch, mocker):
    # @b has NO persona hook but the moment has a shared m.hook -> @b still gets its OWN render burning
    # the shared hook (never a silent textless base ship).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "a only"}, m_hook="SHARED",
               surfaces=("@b/instagram",)); led.save()
    led = _run(cfg)
    p = next(iter(led.posts.values()))
    assert p.render_id is not None and led.get_render(p.render_id).hook_text == "SHARED"
    assert p.variant_hook == "SHARED" and p.media_urls

def test_render_filed_under_batch_source_dirs(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",), batch_id="batch_xy"); led.save()
    led = _run(cfg)
    r = next(iter(led.renders.values()))
    rp = Path(r.path)
    assert cfg.clips in rp.parents and "batch_xy" in rp.parts and "src_1" in rp.parts and rp.exists()
    assert r.batch_id == "batch_xy" and r.source_id == "src_1"


# ---- default-safe: cv OFF and hookless are byte-identical (no renders) ----
def test_cv_off_mints_no_renders(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); _mock_burn(mocker)   # M3d: default flipped ON — pin OFF to prove the OFF path still mints nothing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    led = _run(cfg)
    assert led.renders == {}
    p = next(iter(led.posts.values()))
    assert p.render_id is None and p.media_urls == []

def test_hookless_moment_mints_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={}, m_hook=None, surfaces=("@a/instagram",)); led.save()
    led = _run(cfg)
    assert led.renders == {}
    p = next(iter(led.posts.values()))
    assert p.render_id is None and p.media_urls == []


# ---- AUDIT M1: a failed shared-clip burn leaves a breadcrumb (the ON mint no longer discards the return) ----
def test_burn_failure_emits_hook_burn_failed_breadcrumb(tmp_path, monkeypatch, mocker):
    # On a default-band account the per-account hook burns onto the shared clip; if that burn FAILS (degraded
    # ffmpeg / no text filter) the hookless ship MUST leave a breadcrumb. The OFF path flagged this via
    # hook_burn_failed; the default-ON mint previously discarded burn_hook_only's return and shipped silent.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    mocker.patch("fanops.overlay.burn_hook_only", return_value=False)     # burn fails (no libass)
    _run(cfg)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_burn_failed" in log and "@a/instagram" in log

def test_successful_burn_emits_no_hook_burn_failed(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)   # burn succeeds
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    _run(cfg)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_burn_failed" not in log                                 # no false breadcrumb on success


# ---- account_render_spec: the SINGLE source of the render id + cut decision (crosspost == reburn) ----
def test_account_render_spec_bare_for_default_tagged_for_override(tmp_path):
    from fanops.crosspost import account_render_spec
    from fanops.ids import child_id
    from fanops.bands import band_for
    from fanops.models import Clip, Fmt
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16)
    class _A:                                                            # duck-typed account
        def __init__(self, **kw): self.__dict__.update(kw)
    rid0, cut0, prof0, top0 = account_render_spec(cfg, clip=clip, hook="H", acct=None)
    assert cut0 is False and prof0 == cfg.clip_profile                   # None acct -> global defaults, no cut
    assert rid0 == child_id("render", "clip_1", "H")                     # bare-hook id (byte-identical to shared)
    rid1, cut1, prof1, _ = account_render_spec(cfg, clip=clip, hook="H", acct=_A(clip_profile="short"))
    b = band_for("short")
    assert cut1 is True and prof1 == "short"                             # override -> wants a cut
    assert rid1 == child_id("render", "clip_1", f"H\x1fband:{b.lo:g}-{b.hi:g}") and rid1 != rid0   # band-tagged, distinct
