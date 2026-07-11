# tests/test_produce.py — errored source warming via infer_resume_stage
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.produce import _produce_one


def test_produce_warms_errored_source_with_warm_transcript(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 1, "text": "warm"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.error,
                              error_reason="TimeoutExpired: ffmpeg hung"))
    sig_calls = []
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        sig_calls.append(joined)
        if cmd[0] == "ffmpeg" and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 16.0")
            return R()
        class R: returncode=0; stderr=""; stdout=""
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake)
    logs = []
    _produce_one(cfg, "s1", set(), log=lambda *a, **k: logs.append((a, k)))
    assert any("warm_resume" in str(x) for x in logs)
    assert (cfg.agent_io / "signals" / "s1.json").exists()
    assert sig_calls, "signals ffmpeg should run to warm sidecar"
