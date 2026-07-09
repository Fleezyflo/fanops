# tests/test_env_perms.py — MOL-361: .env owner-only-at-rest floor on set_env_var write
from __future__ import annotations
import os
import sys

import pytest

from fanops import autopilot


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits")
def test_set_env_var_floors_mode_to_owner_only(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FANOPS_RESPONDER=manual\n")
    os.chmod(env, 0o644)                                   # simulate a loose pre-existing .env
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    assert os.stat(env).st_mode & 0o077 == 0                # no group/other access (0600)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits")
def test_set_env_var_creates_owner_only_env(tmp_path):
    env = tmp_path / ".env"
    autopilot.set_env_var(env, "POSTIZ_URL", "https://p.example.com")
    assert env.exists()
    assert os.stat(env).st_mode & 0o077 == 0


def test_set_env_var_chmod_failure_still_persists(tmp_path, monkeypatch):
    """Best-effort chmod must not break persistence on a non-POSIX FS (ledger posture)."""
    env = tmp_path / ".env"
    real_chmod = os.chmod

    def flaky_chmod(path, mode):
        if str(path).endswith(".tmp"):
            raise OSError("chmod not supported")
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", flaky_chmod)
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    assert "FANOPS_RESPONDER=llm" in env.read_text()
