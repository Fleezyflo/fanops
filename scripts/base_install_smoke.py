#!/usr/bin/env python3
"""base_install_smoke.py — prove the BASE install (no [framing] extra) behaves correctly.

Why this exists: the CI `unit` job installs the [framing] extra (opencv) because smart_framing defaults
ON and the render path HARD-REQUIRES cv2. That means the unit lane no longer exercises the base install
without opencv. This smoke, run in a LITERAL clean venv from its own CI job, restores that coverage.

Run in a venv where `pip install .` was done WITHOUT any extra (so cv2 is genuinely absent). Asserts:
  1. `import cv2` FAILS (the [framing] extra is genuinely NOT installed — this is the whole point).
  2. `import fanops` and the fanops CLI module import successfully (base deps are enough).
  3. A REPRESENTATIVE non-render operation works in the clean base env: `fanops --help` exits 0.
  4. A render prerequisite check RAISES ToolchainMissingError (loud refusal, not a silent centre-crop).
  5. No import-time crash, and the missing prerequisite did NOT degrade to a centered fallback.

This smoke does NOT assert any smart_framing-OFF behavior: the off-switch policy is a SEPARATE product
decision (Follow-up F3) and must not be entrenched here.

Exit 0 = all pass; exit 1 = a smoke assertion failed (prints which). stdlib only.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"[base-install-smoke] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    # 1. cv2 must be ABSENT (proves this really is a no-[framing] install, not a false pass)
    try:
        import cv2  # noqa: F401
        _fail("cv2 IS importable — this is not a clean base install; run without the [framing] extra")
    except ImportError:
        pass  # expected

    # 2. base package + CLI import
    try:
        import fanops  # noqa: F401
        import fanops.cli  # noqa: F401  — the CLI entrypoint module must import on base deps alone
        from fanops.config import Config
        from fanops.clip import _resolve_framing
        from fanops.errors import ToolchainMissingError
    except Exception as e:  # pragma: no cover - the smoke IS the coverage
        _fail(f"base `import fanops`/CLI chain failed: {type(e).__name__}: {e}")

    # 3. a REPRESENTATIVE non-render operation in the clean base env: the CLI entrypoint runs `--help`
    #    (fanops = "fanops.cli:main"; argparse --help prints usage then raises SystemExit(0)). Called
    #    in-process so it does not depend on the console script being on PATH or on a __main__ module.
    import contextlib
    import io as _io
    from fanops.cli import main as _cli_main
    _buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
            _cli_main(["--help"])
    except SystemExit as se:
        if se.code not in (0, None):
            _fail(f"`fanops --help` exited {se.code} in the base env — CLI not usable without extras")
    except Exception as e:
        _fail(f"`fanops --help` crashed in the base env: {type(e).__name__}: {e}")
    if "usage" not in _buf.getvalue().lower():
        _fail("`fanops --help` produced no usage text — CLI did not run correctly")

    # a minimal source stand-in for _resolve_framing (it only reads these attributes)
    class _Src:
        source_path = "/nonexistent/base-smoke.mp4"
        width = 1920
        height = 1080
        id = "base-smoke-src"
        source_id = "base-smoke-src"

    # 4. + 5. a render prerequisite check must REFUSE loudly (smart_framing defaults ON), NOT centre-crop.
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(root=Path(td))
        if not cfg.smart_framing:
            _fail("smart_framing is not ON by default — the smoke's prerequisite premise is broken")
        try:
            res = _resolve_framing(cfg, _Src(), 0.0, 6.0)
        except ToolchainMissingError:
            pass  # expected: loud refusal on the missing prerequisite
        else:
            _fail(f"missing prerequisite did NOT refuse — got {res!r} (would be a silent centered fallback)")

    print("[base-install-smoke] OK: cv2 absent, fanops+CLI import, `fanops --help` runs, render prereq refuses")


if __name__ == "__main__":
    main()
