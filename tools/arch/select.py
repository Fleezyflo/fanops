"""CI path selection — EXPLICIT, and TESTED.

The fast gate (drift + policy + registries) costs ~2s and runs on EVERY pull request.
The negative controls cost ~44s (they build 17 isolated fixture trees) and are PATH-SELECTED.

The rule, stated plainly: **the negative controls prove that the VALIDATORS work. They need
re-proving when the validators — or the artifacts they read — change.** A pure `src/` change does
not alter the validators, and the fast gate already catches its drift.

FAIL OPEN. If the changed-file list cannot be determined, the deep gate RUNS. A selection rule that
fails closed silently skips the check that proves the whole system is not decorative — which is the
most expensive possible way to be wrong.
"""
from __future__ import annotations

_DEEP_PREFIXES = (
    "tools/arch/",                               # the validators themselves
    ".reports/architecture/kb/",                 # canonical DECLARED architecture
    ".reports/architecture/contract/",           # canonical DECLARED implementation contract
    ".reports/architecture/governance/",         # policy, baselines, exceptions, unknowns
)
_DEEP_FILES = (
    "tests/test_swallow_ratchet.py",             # the ratchet baselines the policy cross-checks
    "tests/test_internal_prints_routed.py",
    "tests/test_arch_governance.py",
    ".github/workflows/architecture.yml",
)


def deep_required(changed: list[str] | None) -> tuple[bool, str]:
    """(run the deep gate?, why). FAILS OPEN."""
    if changed is None:
        return True, "changed-file list unavailable — failing OPEN, running the deep gate"
    if not changed:
        return False, "no files changed"

    hits = sorted({f for f in changed
                   if f.startswith(_DEEP_PREFIXES) or f in _DEEP_FILES})
    if hits:
        return True, ("the validators or the artifacts they read changed: "
                      + ", ".join(hits[:5]) + (f" (+{len(hits)-5} more)" if len(hits) > 5 else ""))
    return False, ("only source/other files changed — the validators are unchanged, so the fast "
                   "gate (drift + policy) is sufficient; the deep gate also runs nightly")
