"""Real [asr] toolchain smoke — nightly only (MOL-197 / CI-17).

The `[asr]` extra (demucs + faster-whisper + torch) is too heavy for the PR path, so it is NEVER installed
by the PR unit/e2e jobs and this file is EXCLUDED from them (the `asr` marker is deselected in ci.yml). The
nightly workflow (.github/workflows/nightly.yml) installs `[asr]` and runs `-m "integration and asr"` with
FANOPS_REQUIRE_E2E=1, so any skip here becomes a failure — proving the heavy toolchain actually installs and
the vocal-isolation entry point is wired, which was never verified in CI before.
"""
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asr]


def test_asr_libraries_import():
    # the real gap: torch-based demucs + faster-whisper resolving/installing at all in CI
    import demucs           # noqa: F401
    import faster_whisper   # noqa: F401


def test_demucs_module_command_shape():
    # isolate_vocals shells `python -m demucs` via the same interpreter as fanops — not PATH
    import demucs           # noqa: F401
    from fanops import vocals
    cmd = vocals.demucs_cmd("in.wav", "out_dir")
    assert cmd[:3] == [sys.executable, "-m", "demucs"]
    assert "--two-stems=vocals" in cmd and "in.wav" in cmd and "out_dir" in cmd
