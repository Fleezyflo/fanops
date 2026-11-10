# tests/test_ci_asr_hf_prewarm.py — REL-CI-ASR.
#
# Nightly runs 38868030626 and 39012855146 died in [asr] toolchain smoke: fanops._fwrun exited 1,
# transcribe_engine parked the source (`whisper produced no JSON (rc=1)`), and the moments gate never
# opened. The cause was PyAV 19: faster-whisper 1.2.1 decode_audio calls av.open(metadata_errors=...),
# which PyAV 19 removed, so every transcription raised before writing JSON. pyproject.toml [asr] now
# bounds av<19. The Hugging Face "unauthenticated requests" text that opened the logged stderr is a
# warning printed while the model loads, not the error. A token requirement built on that reading
# failed every schedule from 2026-10-18 on a secret the repo never had. The product fetches the
# model anonymously (fanops._fwrun._load_model passes no token), so the smoke does too. This pins
# the asr-smoke contract: a hub cache and a prewarm of the model string
# tests/integration/test_e2e_real.py pins, both before the smoke step, and no credential in the job.
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_NIGHTLY = _ROOT / ".github" / "workflows" / "nightly.yml"
_E2E_REAL = _ROOT / "tests" / "integration" / "test_e2e_real.py"
_SMOKE = "ASR smoke (must run, not skip)"
# Any secret, or the env names huggingface_hub reads a token from, means the job needs a credential.
_CREDENTIAL = ("secrets.", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")


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


def _asr_job() -> tuple[dict, dict]:
    doc = yaml.safe_load(_NIGHTLY.read_text(encoding="utf-8")) or {}
    job = (doc.get("jobs") or {}).get("asr-smoke")
    assert isinstance(job, dict), "job asr-smoke vanished from nightly.yml"
    return doc, job


def _named(steps: list[dict], name: str) -> tuple[int, dict]:
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i, step
    raise AssertionError(f"step {name!r} vanished from asr-smoke")


def test_asr_smoke_caches_hf_hub_and_prewarms_pinned_model():
    """Hub cache + prewarm of _PINNED_WHISPER_MODEL before the smoke; no step needs or refuses a token."""
    model = _pinned_whisper_model()
    doc, job = _asr_job()
    steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
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

    # The smoke fetches the model the way production does: anonymously. Workflow env + the whole job
    # (env, if:, every step) carry no secret and no Hub token name, so no step can need or refuse one.
    job_view = yaml.safe_dump({"env": doc.get("env"), "job": job})
    for needle in _CREDENTIAL:
        assert needle not in job_view, f"asr-smoke must not pass or require a Hub credential: found {needle!r}"
    assert (smoke.get("env") or {}).get("FANOPS_REQUIRE_E2E") == "1"
    assert 'python -m pytest -q -m "integration and asr" -rs' in (smoke.get("run") or "")
