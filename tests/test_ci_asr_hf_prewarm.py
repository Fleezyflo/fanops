# tests/test_ci_asr_hf_prewarm.py — REL-CI-ASR.
#
# Nightly run 38868030626 died in [asr] toolchain smoke: faster-whisper cold-started `tiny` from the
# Hugging Face Hub with no hub cache, no prewarm, and no HF_TOKEN, so fanops._fwrun exited 1 and
# transcribe_engine parked the source (`whisper produced no JSON (rc=1)`). The moments gate never
# opened. ci-e2e.yml already caches openai-whisper under ~/.cache/whisper; nightly only cached
# Demucs under ~/.cache/torch. This pins the asr-smoke contract: hub cache + a prewarm of the same
# model string tests/integration/test_e2e_real.py pins, before the smoke step.
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_NIGHTLY = _ROOT / ".github" / "workflows" / "nightly.yml"
_E2E_REAL = _ROOT / "tests" / "integration" / "test_e2e_real.py"
_SMOKE = "ASR smoke (must run, not skip)"
_HF_TOKEN = "${{ secrets.HF_TOKEN }}"


def _pinned_whisper_model() -> str:
    tree = ast.parse(_E2E_REAL.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_PINNED_WHISPER_MODEL":
                value = ast.literal_eval(node.value)
                assert isinstance(value, str) and value
                return value
    raise AssertionError("_PINNED_WHISPER_MODEL missing from tests/integration/test_e2e_real.py")


def _asr_steps() -> list[dict]:
    doc = yaml.safe_load(_NIGHTLY.read_text(encoding="utf-8")) or {}
    job = (doc.get("jobs") or {}).get("asr-smoke")
    assert isinstance(job, dict), "job asr-smoke vanished from nightly.yml"
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _named(steps: list[dict], name: str) -> tuple[int, dict]:
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i, step
    raise AssertionError(f"step {name!r} vanished from asr-smoke")


def test_asr_smoke_caches_hf_hub_and_prewarms_pinned_model():
    """Hub cache + prewarm of _PINNED_WHISPER_MODEL before the smoke; blank HF_TOKEN stays anonymous."""
    model = _pinned_whisper_model()
    steps = _asr_steps()
    smoke_i, smoke = _named(steps, _SMOKE)

    caches = [s for s in steps if str(s.get("uses") or "").startswith("actions/cache")]
    hf = next((s for s in caches if ".cache/huggingface" in str((s.get("with") or {}).get("path") or "")), None)
    assert hf is not None, "asr-smoke has no actions/cache step covering ~/.cache/huggingface"
    demucs = next((s for s in caches if ".cache/torch" in str((s.get("with") or {}).get("path") or "")), None)
    assert demucs is not None and hf.get("uses") == demucs.get("uses"), (
        "HF hub cache must use the same pinned actions/cache as the Demucs cache")
    spec = hf.get("with") or {}
    key = str(spec.get("key") or "")
    assert f"-{model}-" in f"-{key}-", f"cache key {key!r} is not tied to pinned model {model!r}"
    assert "hashFiles('pyproject.toml')" in key or 'hashFiles("pyproject.toml")' in key, (
        f"cache key {key!r} is not tied to pyproject.toml")
    prefixes = [ln.strip() for ln in str(spec.get("restore-keys") or "").splitlines() if ln.strip()]
    assert any(key.startswith(p) for p in prefixes), (
        f"restore-keys {prefixes!r} are not prefixes of key {key!r} — actions/cache will not restore")
    assert steps.index(hf) < smoke_i, "HF hub cache must restore before ASR smoke"

    prewarms = [(i, s) for i, s in enumerate(steps)
                if "WhisperModel" in (s.get("run") or "") and "certifi_ssl_env" in (s.get("run") or "")]
    assert len(prewarms) == 1, "asr-smoke needs exactly one faster-whisper prewarm step"
    pre_i, pre = prewarms[0]
    assert steps.index(hf) < pre_i < smoke_i, "prewarm must run after the hub cache restore and before ASR smoke"
    run = " ".join((pre.get("run") or "").split())
    assert "fanops.config" in run and run.find("certifi_ssl_env") < run.find("WhisperModel")
    assert re.search(rf"WhisperModel\(\s*['\"]{re.escape(model)}['\"]", run), (
        f"prewarm must load pinned model {model!r} via WhisperModel")
    assert re.search(r"device\s*=\s*['\"]cpu['\"]", run)
    assert re.search(r"compute_type\s*=\s*['\"]int8['\"]", run)

    for step in (pre, smoke):
        assert (step.get("env") or {}).get("HF_TOKEN") == _HF_TOKEN, (
            f"{step.get('name')!r} must pass HF_TOKEN from secrets.HF_TOKEN")
        assert "unset HF_TOKEN" not in (step.get("run") or ""), (
            f"{step.get('name')!r} must not fall back to anonymous Hub access")
        assert "secrets.HF_TOKEN is empty" in (step.get("run") or "")
    assert any(s.get("name") == "Require HF_TOKEN on schedule" for s in steps)
    assert (smoke.get("env") or {}).get("FANOPS_REQUIRE_E2E") == "1"
    assert 'python -m pytest -q -m "integration and asr" -rs' in (smoke.get("run") or "")
    raw = _NIGHTLY.read_text(encoding="utf-8")
    assert "not fetched anonymously" in raw
