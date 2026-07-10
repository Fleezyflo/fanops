#!/usr/bin/env python3
"""Count Config.refresh_env invocations during a pytest run (investigation only)."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PATCH = '''
import os
_COUNT = {"refresh_env": 0, "refresh_all": 0}
_orig_refresh = None

def _patch():
    from fanops.config import Config
    import tests.conftest as cf
    global _orig_refresh
    _orig_refresh = Config.refresh_env
    def counting_refresh(self):
        _COUNT["refresh_env"] += 1
        return _orig_refresh(self)
    Config.refresh_env = counting_refresh
    _orig_all = cf._refresh_all_config_env
    def counting_all():
        _COUNT["refresh_all"] += 1
        return _orig_all()
    cf._refresh_all_config_env = counting_all

def _report():
    print(f"REFRESH_ENV_COUNT={_COUNT['refresh_env']}", flush=True)
    print(f"REFRESH_ALL_COUNT={_COUNT['refresh_all']}", flush=True)

_patch()
'''

def main():
    root = Path(__file__).resolve().parents[1]
    marker = sys.argv[1] if len(sys.argv) > 1 else "not integration and not slow"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(PATCH)
        patch_path = f.name
    env = os.environ.copy()
    env["FANOPS_REQUIRE_STUDIO"] = "1"
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + str(root)
    # Run via pytest with early conftest hook via -p
    cmd = [
        str(root / ".venv/bin/python"), "-c",
        f"exec(open({patch_path!r}).read()); import pytest; raise SystemExit(pytest.main({['-q','-m',marker,'--tb=no']!r}))"
    ]
    # Simpler: prepend patch to conftest via env
    proc = subprocess.run(
        [str(root / ".venv/bin/python"), "-m", "pytest", "-q", "-m", marker, "--tb=no"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    # Use inline monkeypatch at start - actually need to patch before conftest loads
    # Better approach: run with a tiny plugin
    plugin = root / "scripts" / "_ci_perf_count_plugin.py"
    plugin.write_text('''
import pytest
from fanops.config import Config
import tests.conftest as cf

_count = {"refresh_env": 0, "refresh_all": 0}
_orig_re = Config.refresh_env
_orig_all = cf._refresh_all_config_env

def _wrap_refresh(self):
    _count["refresh_env"] += 1
    return _orig_re(self)

def _wrap_all():
    _count["refresh_all"] += 1
    return _orig_all()

Config.refresh_env = _wrap_refresh
cf._refresh_all_config_env = _wrap_all

def pytest_sessionfinish(session, exitstatus):
    print(f"\\nREFRESH_ENV_COUNT={_count['refresh_env']}")
    print(f"REFRESH_ALL_COUNT={_count['refresh_all']}")
''')
    proc = subprocess.run(
        [str(root / ".venv/bin/python"), "-m", "pytest", "-q", "-m", marker, "--tb=no", "-p", "scripts._ci_perf_count_plugin"],
        cwd=root,
        env=env,
        capture_output=False,
    )
    return proc.returncode

if __name__ == "__main__":
    raise SystemExit(main())
