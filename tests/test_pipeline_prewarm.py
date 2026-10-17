# tests/test_pipeline_prewarm.py
"""Phase D: the slow subprocess stages (whisper / ffmpeg signals / ffmpeg render) must run OUTSIDE
the ledger lock. advance() ingests in a short transaction, warms the slow artifacts lock-free, then
commits state under the main transaction. A crash mid-render commits no partial clipped state."""
import json
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState
from fanops.pipeline import advance


def _accts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))


def _seed_renderable(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0,
                          state=SourceState.moments_requested, meta={"transcribed": True}, transcript=[]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10, end=28,
                          reason="r", state=MomentState.decided, hook=None))
    led.save()


def test_render_subprocess_runs_with_no_ledger_lock_held(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    cfg = Config(root=tmp_path); _accts(cfg); _seed_renderable(cfg)
    acquired = {"ok": False}
    def render_run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            try:
                with Ledger.transaction(cfg, timeout=2.0):
                    acquired["ok"] = True
            except Exception:
                acquired["ok"] = False
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=render_run)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert acquired["ok"], "render ran while the ledger lock was held — Phase D starvation regression"


def test_crash_mid_render_commits_no_clipped_state(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    cfg = Config(root=tmp_path); _accts(cfg); _seed_renderable(cfg)
    def fail_run(cmd, **kw):
        class R: returncode = 1; stderr = "boom: corrupt input"
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fail_run)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    saved = Ledger.load(cfg)
    assert saved.moments["mom_1"].state is MomentState.decided
    assert not any(c.state.value == "rendered" for c in saved.clips.values())
