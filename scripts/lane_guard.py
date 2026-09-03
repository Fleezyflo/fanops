#!/usr/bin/env python3
"""Lane file-ownership guard — thin CLI wrapper over scripts/guards/.

Consumed by .githooks/pre-push (local, fail-open on infra errors) and
.github/workflows/lane-guard.yml (CI, authoritative). Source of truth for lanes +
hot-file ownership is .agents/lanes.json.

Usage:
  lane_guard.py [--branch REF] [--base REF] [--lane NAME] [--manifest PATH] [--changed a,b,c] [--use-linear]
"""
from guards.lane_resolve import (  # noqa: F401 — re-exported for tests
    _lane_from_issue_fields,
    _parse_issue_payload,
    evaluate,
    lane_for_branch,
    lane_for_ticket,
    lane_from_linear,
    main,
    mol_id_from_branch,
    strays,
)
from guards.manifest import load_manifest  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
