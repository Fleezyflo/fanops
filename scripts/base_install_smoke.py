#!/usr/bin/env python3
"""base_install_smoke.py — prove the BASE install (no [framing] extra) behaves correctly.

Why this exists: the CI `unit` job now installs the [framing] extra (opencv), because smart_framing
defaults ON and the render path HARD-REQUIRES cv2. That means the unit lane no longer exercises the
base install without opencv. This smoke restores that coverage in its own clean-venv CI job.

Run in a venv where `pip install .` was done WITHOUT any extra (so cv2 is genuinely absent). Asserts:
  1. `import fanops` succeeds (base deps are enough to import the package).
  2. `import cv2` FAILS (the [framing] extra is genuinely NOT installed — this is the whole point).
  3. smart_framing ON  -> _resolve_framing RAISES ToolchainMissingError (loud refusal, not a silent crop).
  4. smart_framing OFF -> _resolve_framing returns (None, None, None) (centered crop, cv2 never consulted).

Exit 0 = all pass; exit 1 = a smoke assertion failed (prints which). stdlib only.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"[base-install-smoke] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    # 1. base package imports
    try:
        import fanops  # noqa: F401
        from fanops.config import Config
        from fanops.clip import _resolve_framing
        from fanops.errors import ToolchainMissingError
    except Exception as e:  # pragma: no cover - the smoke IS the coverage
        _fail(f"base `import fanops` chain failed: {type(e).__name__}: {e}")

    # 2. cv2 must be ABSENT (proves this really is a no-[framing] install, not a false pass)
    try:
        import cv2  # noqa: F401
        _fail("cv2 IS importable — this is not a clean base install; run without the [framing] extra")
    except ImportError:
        pass  # expected

    # a minimal source stand-in for _resolve_framing (it only reads these attributes)
    class _Src:
        source_path = "/nonexistent/base-smoke.mp4"
        width = 1920
        height = 1080
        id = "base-smoke-src"
        source_id = "base-smoke-src"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 3. smart_framing ON (the production default) -> must REFUSE, not silently centre-crop
        cfg_on = Config(root=root)
        if not cfg_on.smart_framing:
            _fail("smart_framing is not ON by default — the smoke's premise is broken")
        try:
            _resolve_framing(cfg_on, _Src(), 0.0, 6.0)
            _fail("smart_framing ON + cv2 absent did NOT raise — the render would silently centre-crop")
        except ToolchainMissingError:
            pass  # expected: loud refusal

        # 4. smart_framing OFF -> centered crop, byte-identical to today, cv2 never consulted
        os.environ["FANOPS_SMART_FRAMING"] = "0"
        try:
            cfg_off = Config(root=root)
            res = _resolve_framing(cfg_off, _Src(), 0.0, 6.0)
            if res != (None, None, None):
                _fail(f"smart_framing OFF should centre-crop (None,None,None); got {res!r}")
        finally:
            os.environ.pop("FANOPS_SMART_FRAMING", None)

    print("[base-install-smoke] OK: base install imports, cv2 absent, ON refuses, OFF centers")


if __name__ == "__main__":
    main()
