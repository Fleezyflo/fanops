# src/fanops/router.py
"""M2 (structural-hooks): the hook-strategy ROUTER — a read-only, Moment-level classifier that runs
AFTER the specificity critic (hookjudge.ingest_hook_judge) and BEFORE the render loop. It records a
per-Moment `hook_strategy` reason and RENDERS NOTHING; the existing render loop reads the annotation.
Default-OFF (cfg.hook_router): observe-only, so an additive annotation is the sole delta (non-regression).
M2 emits text | clean_final | clean_awaiting_strategy:impact_cut; `stitch:<format>` only becomes
reachable when a format handler ships (M4 impact-cut). A clean_awaiting_strategy Moment is preserved
from reconcile_moments GC (ledger.py) so a future strategy can re-route it."""
from __future__ import annotations
from typing import TYPE_CHECKING
from fanops.models import MomentState
if TYPE_CHECKING:                                # annotation-only (router is imported BY ledger; avoid a cycle)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Moment

# Proven structural-hook families, reserved NOW so clean_awaiting_strategy:<key> can name a
# reserved-but-unbuilt strategy (PRD resolved decision, 2026-06-17). MVP builds impact_cut (M4) first,
# then intro_tease (M6); the rest are reserved slots filled in M9.
STRATEGY_KEYS: tuple[str, ...] = ("impact_cut", "intro_tease", "cold_open", "open_loop",
                                  "before_after", "pov_card", "loop", "reaction")

# Routing reasons recorded on Moment.hook_strategy:
TEXT = "text"                                # the on-screen text hook survived the critic — no structural hook
CLEAN_FINAL = "clean_final"                  # clean clip, no structural strategy applies — ship bare
CLEAN_AWAITING = "clean_awaiting_strategy"   # clean clip reserved for a strategy not yet built (GC-preserved)
STITCH = "stitch"                            # a format handler acted on the reservation: stitch:<format> (M4)

def awaiting(key: str) -> str:
    """`clean_awaiting_strategy:<key>` — reserve a clean clip for a not-yet-built strategy."""
    return f"{CLEAN_AWAITING}:{key}"

def stitched(key: str) -> str:
    """`stitch:<key>` — a format handler produced a stitch plan for this moment (M4 impact-cut on)."""
    return f"{STITCH}:{key}"


def _has_peak_in_window(led: "Ledger", m: "Moment") -> bool:
    """True if the source has a signal peak whose time falls inside the moment's [start, end] window —
    the deterministic precondition for an impact-cut. Guards a non-numeric `t` (signal_peaks is an
    UNVALIDATED on-disk sidecar — mirror clip.py's t/score guard; a bad peak is skipped, never raises)."""
    src = led.sources.get(m.parent_id)
    if src is None or not src.signal_peaks: return False
    for p in src.signal_peaks:
        try: t = float(p.get("t"))
        except (TypeError, ValueError): continue
        if m.start <= t <= m.end: return True
    return False


def route_moments(led: "Ledger", cfg: "Config", *, hold_hooks: bool = False, hold_judge: bool = False) -> "Ledger":
    """Classify each `decided` Moment whose hook is FINAL this pass into a `hook_strategy` reason and
    return led. RENDERS / persists nothing else (observe-only). A held moment (still awaiting the feed
    editor or the critic this pass) is left UNROUTED so it is classified on its final hook — never a
    seed about to be rewritten, nor a hook the critic may still reject. M2 emits text | clean_final |
    clean_awaiting_strategy:impact_cut; `stitch:<format>` becomes reachable only when a format handler
    ships (M4). cfg is the stage-convention handle (reserved for per-format gating in later milestones)."""
    for m in led.moments.values():
        if m.state is not MomentState.decided: continue
        if (hold_hooks and not m.hook_edited) or (hold_judge and not m.hook_judged): continue
        if m.hook:                                            # the text hook survived the critic -> no stitch
            led.moments[m.id].hook_strategy = TEXT
        elif _has_peak_in_window(led, m):                     # clean + a peak in window -> reserve impact_cut
            led.moments[m.id].hook_strategy = awaiting("impact_cut")
        else:                                                 # clean, nothing reservable -> ship bare
            led.moments[m.id].hook_strategy = CLEAN_FINAL
    return led
