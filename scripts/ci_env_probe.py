#!/usr/bin/env python3
"""ci_env_probe.py — report the actual runtime env of a CI lane (not inferred from workflow YAML).

Prints python/platform/machine, whether ffmpeg is on PATH (shutil.which — the SAME check the render path
uses), whether cv2 is importable, and PATH. Used to make the unit lane's ffmpeg-absent + cv2-present
contract EXPLICIT and MEASURED, rather than assumed from the absence of an `apt install ffmpeg` step
(GitHub-hosted runner images are versioned and can change). stdlib + optional cv2 probe only.
"""
from __future__ import annotations
import os
import platform
import shutil
import sys


def main() -> None:
    print("== CI env probe ==")
    print("python:", sys.version.replace("\n", " "))
    print("platform:", platform.platform())
    print("machine:", platform.machine())
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    print("ffmpeg:", ff)
    print("ffprobe:", fp)
    try:
        import cv2  # noqa: F401
        print("cv2:", getattr(cv2, "__version__", "present"))
    except Exception as e:
        print("cv2: ABSENT (" + type(e).__name__ + ")")
    print("PATH:", os.environ.get("PATH", ""))
    # The unit lane's smart-framing thesis depends on ffmpeg being absent (detection fails open to centered).
    # Report it loudly so a future runner-image change that ADDS ffmpeg is visible in the log, not silent.
    if ff is not None:
        print("NOTE: ffmpeg IS on PATH in this lane — the 'detection fails open (no ffmpeg)' assumption does "
              "NOT hold here; real frame extraction can run. Revisit the unit-lane framing contract.")
    else:
        print("OK: ffmpeg is NOT on PATH in this lane — frame extraction cannot run; detection fails open "
              "to centered as the unit-lane framing contract assumes.")


if __name__ == "__main__":
    main()
