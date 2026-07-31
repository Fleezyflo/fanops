# tests/test_ci_log_producer_guards.py — MOL-720. Two guards for one incident.
#
# Scheduled run 30610781235 died at `pip install --require-hashes -r requirements/ci-e2e.txt`
# ("triton>=2 ... not pinned with =="), then EVERY log-reading step after it tracebacked on a
# pytest-*.log that was never written, burying the real error. Both halves are pinned here:
#
#   1. the e2e lock must carry the linux/x86_64 closure openai-whisper declares — its absence is
#      the fingerprint of a lock compiled off-linux (dba7e63c dropped triton + 18 cuda/nvidia
#      pins that way, and the nightly e2e lane went red the next morning);
#   2. a step that reads a pytest log must gate on an ALLOWLIST of producer outcomes, never a
#      `!= 'cancelled'` denylist — a producer that never ran reports `skipped`, which the
#      denylist admits.
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CI_YML = _ROOT / ".github" / "workflows" / "ci.yml"
_E2E_LOCK = _ROOT / "requirements" / "ci-e2e.txt"

# consumer step name -> (producer step id, the log it reads)
_CONSUMERS = {
    "CI duration report (unit)": ("unit-tests", "pytest-unit.log"),
    "Unit pytest SLO gate (blocking — MOL-460)": ("unit-tests", "pytest-unit.log"),
    "CI duration report (e2e integration)": ("e2e-integration", "pytest-e2e-integration.log"),
    "CI duration report (e2e slow)": ("e2e-slow", "pytest-e2e-slow.log"),
}


def _pinned(lock: str, name: str) -> bool:
    return re.search(rf"(?m)^{re.escape(name)}==", lock) is not None


def _steps() -> dict[str, dict]:
    doc = yaml.safe_load(_CI_YML.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and step.get("name"): out[step["name"]] = step
    return out


def test_e2e_lock_pins_the_linux_only_whisper_closure():
    """openai-whisper requires triton>=2 on linux/x86_64; --require-hashes refuses it unpinned."""
    lock = _E2E_LOCK.read_text(encoding="utf-8")
    if not _pinned(lock, "openai-whisper"): pytest.skip("openai-whisper no longer in the e2e lock")
    assert _pinned(lock, "triton"), (
        "requirements/ci-e2e.txt pins openai-whisper but not triton — the lock was compiled off "
        "linux/x86_64, so its marker-gated deps were dropped and CI's --require-hashes install "
        "will refuse them. Regenerate with ./scripts/lock-deps.sh on linux/py3.12.")


@pytest.mark.parametrize("consumer", sorted(_CONSUMERS))
def test_log_reading_step_allowlists_its_producer_outcome(consumer):
    producer, log = _CONSUMERS[consumer]
    step = _steps().get(consumer)
    assert step is not None, f"step {consumer!r} vanished from ci.yml — update _CONSUMERS"
    assert log in (step.get("run") or ""), f"{consumer!r} no longer reads {log}"
    cond = " ".join((step.get("if") or "").split())
    outcome = f"steps.{producer}.outcome"
    assert f"{outcome} == 'success'" in cond and f"{outcome} == 'failure'" in cond, (
        f"{consumer!r} must gate on {outcome} being success OR failure — those are the only two "
        f"outcomes that mean the producer ran and teed {log}.")
    assert f"{outcome} !=" not in cond, (
        f"{consumer!r} gates on a {outcome} DENYLIST; a producer that never ran reports 'skipped', "
        f"which a denylist admits — that is how a failed install produced a missing-{log} traceback.")
