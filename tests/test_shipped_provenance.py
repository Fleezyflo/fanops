# tests/test_shipped_provenance.py — P3: record what each account actually SHIPPED on the EXISTING Render row.
# Two facts were computed at crosspost then thrown away: (1) was the on-screen hook this account's OWN
# (per_account) or a shared-moment fallback (shared_fallback) — the OR at crosspost.py:169 collapsed them;
# (2) the REALIZED seconds of the per-account cut — render_account_cut returned a bare bool and ce-cs was
# discarded. Now Render.hook_source + Render.cut_seconds carry them. No new entity; additive v7->v8 migration.
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt, HookSource
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips


def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle="@a", **extra):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active", **extra}

def _seed_clip(cfg, *, hooks_by_persona, moment_hook=None):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=moment_hook, hooks_by_persona=hooks_by_persona))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "cap", "hashtags": ["#x"]}}
    led.add_clip(clip)
    return led

def _mock_burn(mocker, *, cut=(True, 11.5), burn=True):
    mocker.patch("fanops.crosspost.render_account_cut", return_value=cut)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=burn)

def _render(led):
    return next(iter(led.renders.values()))


# ---- hook_source: own vs shared fallback vs none ----
def test_hook_source_per_account(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    led = _seed_clip(cfg, hooks_by_persona={"@a": "MY OWN HOOK"})   # this account's OWN authored hook
    _mock_burn(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert _render(led).hook_source is HookSource.per_account

def test_hook_source_shared_fallback(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    led = _seed_clip(cfg, hooks_by_persona={}, moment_hook="SHARED MOMENT HOOK")   # no own hook -> shared fallback
    _mock_burn(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert _render(led).hook_source is HookSource.shared_fallback


# ---- cut_seconds: realized window recorded for a real cut; None on a failed/absent cut ----
def test_cut_seconds_recorded_for_account_cut(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct(clip_profile="short")])   # band differs -> a real cut fires
    led = _seed_clip(cfg, hooks_by_persona={"@a": "H"})
    _mock_burn(mocker, cut=(True, 11.5))
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    r = _render(led)
    assert r.cut_seconds == 11.5 and r.is_account_cut is True

def test_failed_cut_records_none(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct(clip_profile="short")])
    led = _seed_clip(cfg, hooks_by_persona={"@a": "H"})
    _mock_burn(mocker, cut=(False, None), burn=True)   # cut fails -> shared burn; realized None
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    r = _render(led)
    assert r.cut_seconds is None and r.is_account_cut is False


# ---- additive migration: a pre-P3 ledger (render without the 2 fields) loads clean + re-stamps v8 ----
def test_pre_p3_ledger_migrates_clean(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 7, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "renders": {"render_x": {"id": "render_x", "clip_id": "c", "account": "@a",
                                    "surface_key": "@a/instagram", "path": "/p.mp4"}},  # NO hook_source / cut_seconds
           "selection_facts": {}, "batches": {}, "stitch_plans": {}}
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))
    led = Ledger.load(cfg)                                       # must NOT raise
    r = led.renders["render_x"]
    assert r.hook_source is HookSource.none and r.cut_seconds is None   # defaults supplied
    led.save()
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION   # re-stamped to v8


# ---- OFF firewall: no Render minted, no new field written, byte-identical ----
def test_off_firewall_mints_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    led = _seed_clip(cfg, hooks_by_persona={"@a": "H"}, moment_hook="S")
    _mock_burn(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert led.renders == {}                                     # OFF -> no render, no provenance fields written
