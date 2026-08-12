"""Native Studio window. Optional extra (`pip install -e '.[desktop]'`); lazy-imports pywebview.

Does not start Flask — the server is `fanops studio --install`. This module only opens a window
onto an already-serving URL.
"""
from __future__ import annotations
import sys


def open_studio_window(url: str) -> int:
    """Open a native window onto `url`. Fail closed if pywebview is missing. Never starts Flask."""
    try:
        import webview
    except ImportError:
        print(
            "REFUSED: pywebview is not installed. "
            "Install the desktop extra: pip install -e '.[desktop]'",
            file=sys.stderr,
        )
        return 2
    webview.create_window("FanOps Studio", url)
    webview.start()
    return 0
