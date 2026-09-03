#!/usr/bin/env python3
"""Cross-open-PR hot-file collision guard — thin CLI wrapper over scripts/guards/.

Runs in the `lane-guard` CI job via the GitHub CLI (authenticated by the workflow's GITHUB_TOKEN).

Usage:
  pr_collision_guard.py --pr N --repo owner/name
  pr_collision_guard.py --this-files "a,b" --others-json '{"7":["a"]}'   # offline / tests
"""
from guards.collision import find_collisions, hot_set, main  # noqa: F401 — re-exported for tests

if __name__ == "__main__":
    raise SystemExit(main())
