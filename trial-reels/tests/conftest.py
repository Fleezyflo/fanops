"""Pytest helpers for trial-reels (hyphenated path → import via sys.path)."""

from __future__ import annotations

import sys
from pathlib import Path

_TRIAL_ROOT = Path(__file__).resolve().parents[1]
if str(_TRIAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TRIAL_ROOT))
