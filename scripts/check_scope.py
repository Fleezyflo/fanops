#!/usr/bin/env python3
"""Map changed src/tests .py paths -> scoped pytest files for scripts/check.sh.

Convention first (studio/, post/ subdirs, test_studio_* names), then a small override table
for modules whose tests use a different basename. Stdlib-only — safe to call from bash."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Modules with no tests/test_<stem>.py — pick the most focused existing test file.
_OVERRIDES: dict[str, tuple[str, ...]] = {
    "src/fanops/_fwrun.py": ("tests/test_fwrun.py",),
    "src/fanops/audit.py": ("tests/test_audit_trail.py",),
    "src/fanops/controlio.py": ("tests/test_controlio.py",),
    "src/fanops/config_introspect.py": ("tests/test_config_verb.py",),
    "src/fanops/cli.py": ("tests/test_cli_wipe.py",),
    "src/fanops/cutover_postiz.py": ("tests/test_cutover.py",),
    "src/fanops/errors.py": ("tests/test_cli.py", "tests/test_swallow_ratchet.py"),
    "src/fanops/framing.py": ("tests/test_smart_framing.py",),
    "src/fanops/gate_keys.py": ("tests/test_pipeline_status.py",),
    "src/fanops/ledger_sqlite.py": ("tests/test_ledger_sqlite_store.py",),
    "src/fanops/ledger_bridge.py": ("tests/test_ledger_json_to_sqlite_bridge.py",),
    "src/fanops/ledger.py": ("tests/test_ledger.py", "tests/test_ledger_store_interface.py",
                             "tests/test_ledger_backend_parity.py",),
    "src/fanops/paths_rebase.py": ("tests/test_media_path_integrity.py",),
    "src/fanops/persona_research.py": ("tests/test_corpus_research.py",),
    "src/fanops/persona_store.py": ("tests/test_persona_levers.py",),
    "src/fanops/produce.py": ("tests/test_publish_post.py",),
    "src/fanops/settings.py": ("tests/test_config.py",),
    "src/fanops/timing_bias.py": ("tests/test_culmination_coverage.py",),
    "src/fanops/post/run.py": ("tests/test_post_run.py",),
    "src/fanops/studio/actions_approve.py": ("tests/test_studio_approval.py",),
    "src/fanops/studio/actions_common.py": ("tests/test_studio_golive.py",),
    "src/fanops/studio/actions_run.py": ("tests/test_studio_run.py", "tests/test_studio_upload.py", "tests/test_upload_chunked.py"),
    "src/fanops/studio/actions_segments.py": ("tests/test_moments_segments.py",),
    "src/fanops/studio/actions_wipe.py": ("tests/test_studio_wipe.py",),
    "src/fanops/studio/app_routes_review.py": ("tests/test_studio_app.py",),
    "src/fanops/studio/app_routes_schedule.py": ("tests/test_studio_schedule_cockpit.py",),
    "src/fanops/studio/preview_media.py": ("tests/test_studio_gaps_closure.py",),
    "src/fanops/studio/thumb_media.py": ("tests/test_thumb_routes.py", "tests/test_studio_thumb.py"),
    "src/fanops/studio/views_common.py": ("tests/test_bulk_approve_spread.py",),
    "src/fanops/studio/views_library.py": ("tests/test_source_progress.py", "tests/test_studio_library.py"),
    "src/fanops/studio/views_live.py": ("tests/test_studio_live_library.py",),
    "src/fanops/studio/views_results.py": ("tests/test_studio_views.py",),
    "src/fanops/studio/views_review.py": ("tests/test_studio_views.py",),
}


def _exists(rel: str) -> str | None:
    p = ROOT / rel
    return rel if p.is_file() else None


def _convention_candidates(src: str) -> list[str]:
    """Ordered candidate test paths for a changed src/fanops/... module."""
    p = Path(src)
    if len(p.parts) < 3 or p.parts[0] != "src" or p.parts[1] != "fanops":
        return []
    rel = Path(*p.parts[2:])  # fanops/...
    stem = rel.stem
    parts = rel.parts
    cands: list[str] = []
    if len(parts) == 1:
        cands.append(f"tests/test_{stem}.py")
    elif parts[0] == "studio":
        cands.append(f"tests/test_studio_{stem}.py")
        if stem.startswith("actions_"):
            cands.append(f"tests/test_{stem}.py")
        elif stem.startswith("app_routes_"):
            route = stem.removeprefix("app_routes_")
            cands.append(f"tests/test_studio_{route}.py")
        elif stem.startswith("views_"):
            view = stem.removeprefix("views_")
            cands.append(f"tests/test_studio_{view}.py")
    elif parts[0] == "post":
        cands.append(f"tests/test_post_{stem}.py")
        cands.append(f"tests/test_{stem}.py")
    out: list[str] = []
    for c in cands:
        hit = _exists(c)
        if hit and hit not in out:
            out.append(hit)
    return out


def resolve_tests(changed: list[str]) -> list[str]:
    """Return sorted unique pytest files to run for the given changed paths."""
    want: dict[str, None] = {}
    for f in changed:
        if f.startswith("tests/") and (ROOT / f).is_file():
            want[f] = None
            continue
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        hits = _convention_candidates(f)
        extra = [h for h in _OVERRIDES.get(f, ()) if _exists(h)]
        if not hits:
            hits = extra
        else:
            for h in extra:
                if h not in hits:
                    hits.append(h)
        for t in hits:
            want[t] = None
    return sorted(want)


def orphan_src_modules(changed: list[str]) -> list[str]:
    """Return changed src/fanops/*.py paths (excl __init__) with no scoped test mapping."""
    out: list[str] = []
    for f in changed:
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        if f.endswith("__init__.py"):
            continue
        if not resolve_tests([f]):
            out.append(f)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args and args[0] == "--orphans":
        for o in orphan_src_modules(args[1:]):
            print(o)
        return 0
    for t in resolve_tests(args):
        print(t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
