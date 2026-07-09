# tests/test_shipped_provenance.py — P3 provenance; P7 m.hook is sole hook truth.
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt, HookSource

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

def test_hook_source_shared_fallback(tmp_path, mocker):
    from fanops.crosspost import render_moment_file
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path)
    led = _seed_clip(cfg, moment_hook="SHARED")
    clip = next(c for c in led.clips.values())
    src = led.sources["src_1"]
    post = Post(id="p1", parent_id=clip.id, account="a", account_id="1", platform=Platform.instagram,
                caption="cap", state=PostState.awaiting_approval)
    mocker.patch("fanops.crosspost.render_account_cut", return_value=(True, 11.5))
    mocker.patch("fanops.overlay.burn_hook_only", return_value=True)
    plan = render_moment_file(led, cfg, post=post, target_clip=clip, src=src)
    assert plan.hook_source is HookSource.shared_fallback

def test_pre_p3_ledger_migrates_clean(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 7, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "renders": {"render_x": {"id": "render_x", "clip_id": "c", "account": "a",
                                    "surface_key": "a/instagram", "path": "/p.mp4"}},
           "selection_facts": {}, "batches": {}, "stitch_plans": {}}
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.legacy_ledger_json_path.parent.mkdir(parents=True, exist_ok=True); cfg.ledger_path.unlink(missing_ok=True); cfg.legacy_ledger_json_path.write_text(json.dumps(raw))
    led = Ledger.load(cfg)
    assert led.renders["render_x"].hook_source is HookSource.none
    led.save()
    assert Ledger.load(cfg)._to_doc()["schema_version"] == SCHEMA_VERSION
