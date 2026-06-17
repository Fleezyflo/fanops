# src/fanops/stitch_render.py
"""M4 (structural-hooks): the impact-cut PRODUCER — the two ledger-side steps that turn a router
reservation into an operator-approved, rendered impact-cut.

  suggest_impact_cuts(led, cfg)      -> creates `suggested` StitchPlans for routed moments (in-lock safe;
                                        renders nothing). Re-routes the moment to `stitch:impact_cut`.
  render_approved_stitches(led, cfg) -> renders `approved` plans into `stitch_draft` clips (T4).

Both are gated by the caller on `cfg.impact_cut` (default OFF). suggest only mutates the ledger, so it
runs inside the advance transaction; the heavy render in render_approved_stitches runs LOCK-FREE."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState
from fanops.router import awaiting, stitched
from fanops.impact_cut import make_stitch_plan

# A bare clip in any of these states is not a valid impact-cut base: error/retired are broken/gone, and a
# stitch_draft is itself a stitch (never stitch a stitch).
_NON_BASE_STATES = (ClipState.error, ClipState.retired, ClipState.stitch_draft)
_IMPACT_AWAITING = awaiting("impact_cut")        # "clean_awaiting_strategy:impact_cut"
_IMPACT_STITCHED = stitched("impact_cut")        # "stitch:impact_cut"


def _read_fingerprint(cfg: Config, clip_id: str) -> str | None:
    """The base clip's pinned render fingerprint (from its {cid}.render.json sidecar), or None if absent/
    unreadable. None pins nothing — a later render then can't detect base drift, so it renders as-pinned."""
    try:
        return json.loads((cfg.clips / f"{clip_id}.render.json").read_text()).get("fp")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def suggest_impact_cuts(led: Ledger, cfg: Config) -> Ledger:
    """For each moment the router reserved `clean_awaiting_strategy:impact_cut`, create a suggested
    impact-cut StitchPlan for each valid bare clip (idempotent via the content-addressed id; the base
    fingerprint is pinned for the supersede check), then re-route the moment to `stitch:impact_cut`.
    A degenerate cut yields no plan and the moment stays reserved (re-tried next pass). Renders nothing."""
    for m in list(led.moments.values()):
        if (m.hook_strategy or "") != _IMPACT_AWAITING:
            continue
        src = led.sources.get(m.parent_id)
        if src is None:
            continue
        produced = False
        for c in list(led.clips.values()):
            if c.parent_id != m.id or c.state in _NON_BASE_STATES:
                continue
            plan = make_stitch_plan(c, m, src, base_fp=_read_fingerprint(cfg, c.id))
            if plan is None:
                continue
            led.add_stitch_plan(plan)            # idempotent: setdefault by content-addressed id (dedup re-emit)
            produced = True
        if produced:
            led.moments[m.id].hook_strategy = _IMPACT_STITCHED   # the format handler acted
    return led
