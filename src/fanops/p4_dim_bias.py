# src/fanops/p4_dim_bias.py
"""P4(b) — the cross-account REACH actuator. aggregate_by_dim (digest.py) already computes per-creative-
dim reach; this is the missing piece that BIASES generation from it. When a creative dim
(first_frame_kind | clip_profile) has a value whose mean REACH clearly leads — and the per-dim P4 unlock
is satisfied (live metrics confirmed + enough attributed signal) — re-open a moment request on a
representative source of that winning value, injecting the dim as guidance so the next batch leans that way.

SAFETY (the whole point, audit C1): this module is AMPLIFY-ONLY, exactly like variant_amplify. It imports
`amplify` and NEVER retire / _delete_moment_cascade / retire_clip / set_moment_state / set_clip_state. A
dim that fails the gate is simply not amplified (never retired as a consequence). Default OFF
(FANOPS_P4_DIM_BIAS); VALIDATION-FROZEN — inert until learning_validated even with the kill switch ON.
FAIL-SAFE: any exception is logged once and the ledger CONTENT is left byte-identical. Deterministic: no
random/hash/wall-clock — reach_mean desc + value-string secondary + lowest-post_id representative, so a
re-run on the same ledger is idempotent (and amplify's MAX_AMPLIFY_PER_SOURCE budget bounds re-runs)."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.adjust import amplify          # AMPLIFY-ONLY: import amplify, NEVER retire (C1 / D1)
from fanops.digest import aggregate_by_dim
from fanops.log import get_logger
from fanops.models import PostState
from fanops.validation_gate import learning_validated, p4_unlocked

# The creative dims P4 ranks by reach. Stamped at crosspost (first_frame_kind, clip_profile, top_bias). NOT
# variation_axis (that is the variant_* loop's territory) — reach attribution here is dim-level. Leg 3 added
# top_bias (framing) — an additive creative choice that rides this EXISTING autonomous amplify, no new wiring.
_P4_DIMS = ("first_frame_kind", "clip_profile", "top_bias")


def dim_bias_candidates(led, cfg) -> list[dict]:
    """Pure, read-only. For each P4 dim that is UNLOCKED (live metrics confirmed + >= 8 attributed posts
    across >= 2 values), rank its values by mean REACH and, when the leader beats the runner-up by
    >= cfg.p4_min_reach_gap, emit ONE candidate: {dim, winning_value, post_id, reach_mean}. post_id is a
    representative analyzed post of the winning value (lowest post_id, deterministic) to hand to amplify.
    [] on any doubt. No I/O, no mutation."""
    out: list[dict] = []
    for dim in _P4_DIMS:
        if not p4_unlocked(led, cfg, dim):
            continue
        agg = aggregate_by_dim(led, dim)                     # {value: {n, reach_sum, reach_mean, ...}}
        if len(agg) < 2:
            continue                                         # need a runner-up to be comparative
        ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["reach_mean"], kv[0]))   # reach desc, value asc
        leader_value, leader_row = ranked[0]
        if leader_row["reach_mean"] - ranked[1][1]["reach_mean"] < cfg.p4_min_reach_gap:
            continue                                         # not a clear lead
        reps = sorted(p.id for p in led.posts.values()
                      if p.state is PostState.analyzed and str(getattr(p, dim, None)) == leader_value)
        if not reps:
            continue
        out.append({"dim": dim, "winning_value": leader_value, "post_id": reps[0],
                    "reach_mean": leader_row["reach_mean"]})
    return out


def apply_p4_dim_bias(led: Ledger, cfg: Config) -> Ledger:
    """Actuator. For each gated dim-bias candidate, amplify a representative source — injecting the
    winning dim as extra guidance. AMPLIFY-ONLY: never calls retire/_delete_moment_cascade. Inert when
    the kill switch (FANOPS_P4_DIM_BIAS) is off (the default) OR until learning_validated (VALIDATION-
    FROZEN, logged). FAIL-SAFE: any exception -> log once, NO partial mutation beyond what amplify already
    committed, return led — an autonomous run never sees the raise (mirrors apply_variant_amplify)."""
    if not cfg.p4_dim_bias:
        return led                                  # kill switch / default OFF -> inert
    if not learning_validated(cfg):
        # unfreezes AUTOMATICALLY on the first real non-degraded live metric (track auto-stamps
        # metrics_confirmed) — NOT an operator step; `fanops cutover metrics` is only an optional early probe.
        get_logger(cfg)("p4_dim_bias", "-", "skipped_unvalidated",
                        hint="auto-unfreezes on the first real non-degraded live metric (optional early: `fanops cutover metrics`)")
        return led
    try:
        candidates = dim_bias_candidates(led, cfg)
    except Exception as e:
        get_logger(cfg)("p4_dim_bias", "-", "error", err=f"candidates: {str(e)[:110]}")   # fail-SAFE
        return led
    for cand in candidates:
        # Per-candidate isolation (fail-SAFE, not fail-silent): one dim's amplify failure must NOT abort
        # the other dim, and the log must name WHICH dim failed (a generic 'error' hides whether 0 or 1
        # amplified). MAX_AMPLIFY_PER_SOURCE is enforced INSIDE amplify, so re-runs stay bounded.
        try:
            # framing is a bool dim; render it as a natural phrase ("top-anchored"/"centered") instead of
            # the raw "top bias = 'True'". Other dims keep the generic "<dim> = '<value>'" wording.
            if cand["dim"] == "top_bias":
                choice = "top-anchored framing" if cand["winning_value"] == "True" else "centered framing"
                what = choice
            else:
                what = f"{cand['dim'].replace('_', ' ')} = '{cand['winning_value']}'"
            hint = (f"Reach data favors {what} for this artist (highest mean reach across accounts). "
                    f"Lean toward that creative choice where it fits the source — do not force it.")
            amplify(led, cfg, [cand["post_id"]], extra_guidance=hint)   # the existing C1-fixed path
        except Exception as e:
            get_logger(cfg)("p4_dim_bias", cand.get("dim", "-"), "error", err=str(e)[:120])
    return led
