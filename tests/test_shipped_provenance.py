# tests/test_shipped_provenance.py — P3 provenance; P7 m.hook is sole hook truth.
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt, HookSource
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips

def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _seed_clip(cfg, *, moment_hook=None):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=moment_hook))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"a/instagram": {"caption": "cap", "hashtags": ["#x"]}}
    led.add_clip(clip); led.save(); return led

def test_hook_source_shared_fallback(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    _seed_clip(cfg, moment_hook="SHARED")
    mocker.patch("fanops.crosspost.render_account_cut", return_value=(True, 11.5))
    mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save()
    from fanops.studio.actions_approve import approve_posts
    approve_posts(cfg, [p.id for p in led.posts.values()])
    assert next(iter(Ledger.load(cfg).renders.values())).hook_source is HookSource.shared_fallback

def test_pre_p3_ledger_migrates_clean(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 7, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "renders": {"render_x": {"id": "render_x", "clip_id": "c", "account": "a",
                                    "surface_key": "a/instagram", "path": "/p.mp4"}},
           "selection_facts": {}, "batches": {}, "stitch_plans": {}}
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))
    led = Ledger.load(cfg)
    assert led.renders["render_x"].hook_source is HookSource.none
    led.save()
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
