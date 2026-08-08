# src/fanops/studio/actions_provenance.py
"""Studio half of the one-shot provenance backfill (MOL-756). Plan -> explicit confirm, over the SAME
engine the `fanops origin-backfill` verb calls, so the cockpit and the headless verb cannot drift.

Shaped on `actions_wipe`: a read-only plan whose fingerprint the confirm must carry back, so a confirm
that never planned, or planned a ledger that has since moved, is refused SERVER-side rather than by a
template that happens to hide the button. It is deliberately NOT a copy of the wipe's typed-word gate:
that word guards an IRREVERSIBLE deletion. This writes one enum field on rows that were already going to
be read wrong, takes a snapshot first, and is idempotent — a typed word here would be friction that
teaches an operator to type past a warning.

No `Ledger.transaction` in this module: the engine owns the single transaction (studio/CLAUDE.md —
`actions*.py` mutate through exactly ONE, and here the one lives one layer down, as with actions_wipe).
"""
from __future__ import annotations

from fanops import origin_backfill
from fanops.config import Config
from fanops.log import get_logger
from fanops.studio.actions_common import ActionResult


def preview_backfill(cfg: Config, *, days) -> ActionResult:
    """Read-only plan. NEVER mutates: a pure survey over a lock-free `Ledger.load`. With no day selected
    it still returns the whole measurement — that is what puts the day histogram and the unlabelled count
    in front of the operator BEFORE they have chosen anything, which is the point of the panel."""
    try:
        return ActionResult(ok=True, detail=origin_backfill.backfill_origin(cfg, days=days, apply=False))
    except Exception as exc:
        get_logger(cfg)("origin_backfill", "-", "plan_failed", level="error", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"ledger unreadable: {str(exc)[:160]}. Run `fanops doctor` first.")


def confirm_backfill(cfg: Config, *, days, token: str) -> ActionResult:
    """Apply the plan the operator was shown. Refuses — never adapts — when the token is absent (no plan
    was rendered) or stale (the ledger moved), and when any STOP invariant fails, carrying the measured
    values back so the refusal is readable without a shell."""
    if not (token or "").strip():
        get_logger(cfg)("origin_backfill", "-", "refused_no_plan", level="error")
        return ActionResult(ok=False, error="show the plan first — nothing was labelled.")
    try:
        rep = origin_backfill.backfill_origin(cfg, days=days, apply=True, plan_token=token.strip())
    except Exception as exc:
        get_logger(cfg)("origin_backfill", "-", "apply_failed", level="error", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"backfill failed: {str(exc)[:160]}. Nothing was labelled.")
    if rep["refused"] == "plan_moved":
        get_logger(cfg)("origin_backfill", "-", "refused_stale_plan", level="error", token=token[:16])
        return ActionResult(ok=False, detail=rep,
                            error="the plan is stale (the ledger changed) — show the plan again. Nothing was labelled.")
    if rep["refused"]:
        get_logger(cfg)("origin_backfill", "-", "refused_invariant", level="error", stops=rep["stops"])
        return ActionResult(ok=False, detail=rep,
                            error="refused: " + " · ".join(rep["stops"]) + " — nothing was labelled.")
    get_logger(cfg)("origin_backfill", "-", "applied", labelled=rep["labelled"], snapshot=rep["snapshot"])
    return ActionResult(ok=True, detail=rep)
