"""On-demand start for the self-hosted Postiz Docker stack.

Pairs with ~/postiz-selfhost/postiz-ondemand.sh + the launchd reaper that STOPS the stack
when idle. FanOps calls ensure_up() at each publish/reconcile entry so the heavy stack
(postiz + temporal + elasticsearch) is up exactly when it is needed and lapses afterward,
instead of holding RAM 24/7.

Safe by construction — ensure_up() does NOTHING unless ALL hold:
  * not running under pytest (never shell docker during the test suite),
  * FANOPS_POSTIZ_AUTOSTART != '0' (operator kill-switch),
  * the active poster backend is 'postiz',
  * POSTIZ_URL points at a LOCAL stack (localhost/127.0.0.1) — a hosted/remote Postiz is
    not ours to start,
  * the on-demand script exists on disk.
Any failure is swallowed-then-returned (fail-open): a still-down Postiz then surfaces through
the normal connection error in the poster, exactly as before this module existed.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

_SCRIPT = Path.home() / "postiz-selfhost" / "postiz-ondemand.sh"
_WAIT_S = 150


def _is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def _backend_is_postiz(cfg) -> bool:
    b = getattr(cfg, "poster_backend", "")
    return getattr(b, "value", b) == "postiz"


def _should_autostart(cfg) -> bool:
    if "pytest" in sys.modules:
        return False
    if os.getenv("FANOPS_POSTIZ_AUTOSTART", "1") == "0":
        return False
    if not _backend_is_postiz(cfg):
        return False
    if not _is_local(os.getenv("POSTIZ_URL", "")):
        return False
    return _SCRIPT.exists() and shutil.which("docker") is not None


def ensure_up(cfg) -> None:
    """Best-effort: bring the local Postiz stack up (and wait until its API answers) before a
    publish/reconcile that needs it. No-op unless _should_autostart(cfg). Never raises."""
    if not _should_autostart(cfg):
        return
    try:
        subprocess.run(["bash", str(_SCRIPT), "ensure"], timeout=_WAIT_S,
                       capture_output=True, check=False)
    except Exception as e:  # fail-open: publishing proceeds; a down stack surfaces normally
        sys.stderr.write(f"[postiz_lifecycle] ensure_up skipped ({type(e).__name__}): {e}\n")
