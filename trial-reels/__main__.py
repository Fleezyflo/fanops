"""CLI entry: python trial-reels run …  or  python trial-reels cover-qa …"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from lib.runner import main as run_main

        return run_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in {"cover-qa", "qa"}:
        from lib.cover_qa import main as qa_main

        return qa_main(sys.argv[2:])
    from lib.cover_qa import main as qa_main

    return qa_main()


if __name__ == "__main__":
    raise SystemExit(main())
