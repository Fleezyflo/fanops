"""Stop hook must load without completion_evidence and still block hedge tells.

#1145 deleted .claude/hooks/completion_evidence.py. A module-level import of
unbacked_claim_reason crashed the Stop hook before fail-open. Hedge regex stays;
the completion-claim guard stays gone.
"""
import importlib.util
import json, subprocess, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_HOOK = _ROOT / ".claude" / "hooks" / "block-hedge-on-stop.py"


def _load_hook():
    sys.modules.pop("completion_evidence", None)
    spec = importlib.util.spec_from_file_location("block_hedge_on_stop", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(payload):
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )


def _assistant_transcript(tmp_path, text):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": text}}) + "\n",
        encoding="utf-8",
    )
    return path


def test_import_without_completion_evidence():
    mod = _load_hook()
    assert "completion_evidence" not in sys.modules
    assert not hasattr(mod, "unbacked_claim_reason")
    assert not (_ROOT / ".claude" / "hooks" / "completion_evidence.py").exists()


def test_empty_stdin_fails_open():
    out = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert out.returncode == 0
    assert not out.stdout.strip()
    assert "block-hedge-on-stop: bad stdin" in out.stderr


def test_hedge_tell_still_blocks(tmp_path):
    path = _assistant_transcript(tmp_path, "Would you like me to push the branch?")
    out = _run({"transcript_path": str(path)})
    assert out.returncode == 0
    decision = json.loads(out.stdout)
    assert decision["decision"] == "block"
    assert "option-deflection" in decision["reason"]


def test_plain_verdict_without_hedge_does_not_block(tmp_path):
    path = _assistant_transcript(tmp_path, "123 passed. Branch pushed.")
    out = _run({"transcript_path": str(path)})
    assert out.returncode == 0
    assert not out.stdout.strip()
