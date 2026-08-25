"""Tests for trial-reels RTL captions + cover OCR QA."""

import sys
from pathlib import Path

# trial-reels/lib is not an installed package; add parent for `import lib.*`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
