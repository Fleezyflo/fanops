#!/usr/bin/env python3
"""MOL-164: bulk-update test fixtures from @-prefixed account handles to canonical bare handles."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests"

KEEP_LINE = re.compile(
    r'add_account\(|write_integration\(|resolve_account_id\(|set_status\(|remove_account\(|'
    r'link_persona\(|ensure_channel\(|set_backend\(|set_clip_profile\(|set_persona\(|'
    r'set_ig_user_id\(|validate_account_handle\(|{"handle":\s*"@|handle":\s*"@|'
    r'ARTIST_HANDLE|@mohflowmusic|@accounts'
)

# Strip @ after opening quote when followed by a lowercase test-account handle token.
STRIP_AT = re.compile(r'(?<=")@(?=[a-z][a-z0-9_-]*(?:/|\||"))')

def transform_line(line: str) -> str:
    if KEEP_LINE.search(line):
        return line
    return STRIP_AT.sub("", line)

def main():
    changed = 0
    for path in sorted(ROOT.rglob("*.py")):
        text = path.read_text()
        lines = text.splitlines(keepends=True)
        new_lines = [transform_line(ln) for ln in lines]
        new_text = "".join(new_lines)
        if new_text != text:
            path.write_text(new_text)
            changed += 1
    print(f"updated {changed} test files")

if __name__ == "__main__":
    main()
