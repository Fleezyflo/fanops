# src/fanops/studio/app_routes_provenance.py
"""Provenance route group (MOL-756): the operator-triggered origin backfill, mounted on Home's health
section because that is where the cockpit already reports "your ledger has rows in a state you cannot
see". `register_provenance_routes(app, cfg)` registers the two POSTs; create_app calls it and the Home
GET primes the panel with a plan for no days (the histogram + the unlabelled count, nothing selected).

Both POSTs return ONLY the panel partial, swapped into `#provenance-panel` — Home is a heavy page and
the panel is the only thing that changes.
"""
from __future__ import annotations
from flask import render_template, request
from fanops.studio import actions_provenance
from fanops.studio.actions_common import ActionResult


def _days_arg() -> list[str]:
    """The selected post-birth days. An ARGUMENT off the form, never a literal: the panel offers exactly
    the days the ledger actually has, so a day cannot be requested that nothing was born on."""
    return [d for d in request.form.getlist("day") if d.strip()]


def register_provenance_routes(app, cfg):
    def _panel(*, preview, result=None):
        return render_template("_provenance_panel.html", provenance=preview, provenance_result=result)

    @app.post("/provenance/plan")
    def do_provenance_plan():
        # READ-ONLY: re-measures the day histogram, the selection and all four STOP invariants LIVE at
        # click time, and renders them. The confirm form appears only after this, carrying this plan's token.
        days = _days_arg()
        # A plan for nothing is not the same as the un-clicked panel, and re-rendering the picker unchanged
        # would read as a dead button. Say so instead.
        note = None if days else ActionResult(ok=False, error="pick at least one day — nothing was measured.")
        return _panel(preview=actions_provenance.preview_backfill(cfg, days=days), result=note)

    @app.post("/provenance/confirm")
    def do_provenance_confirm():
        days = _days_arg()
        res = actions_provenance.confirm_backfill(cfg, days=days, token=request.form.get("plan_token", ""))
        # Re-plan after applying so the operator sees the post-state selection (already_labelled == the
        # whole selection on success), not the plan they clicked.
        return _panel(preview=actions_provenance.preview_backfill(cfg, days=days), result=res)
