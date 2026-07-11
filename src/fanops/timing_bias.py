# src/fanops/timing_bias.py
"""Leg 3 (Culmination) — the TIMING actuator. aggregate_by_dim (digest.py) already computes per-dim reach;
this ranks the `publish_hour` dim and, when one operator-local hour clearly out-reaches the rest, biases the
schedule slot toward it. A schedule-slot bias, NOT an amplify (timing's actuator shape differs from
p4_dim_bias's re-mine) — but it reuses p4_dim_bias's DISCIPLINE exactly: gated on learning_validated +
p4_unlocked('publish_hour'), a comparative reach winner (leader beats runner-up by p4_min_reach_gap), a
default-OFF kill switch (FANOPS_TIMING_BIAS), VALIDATION-FROZEN (inert until proven even with the switch ON),
and FAIL-SAFE (any exception -> logged once, the prior file left untouched; no winner -> no file -> the
schedule is byte-identical to today).

tz-correctness (plan Task 3): publish_hour is stamped AND ranked in the SAME operator-local tz
(timeutil.publish_buckets uses cfg.operator_tz) — a UTC-bucketed winning hour is noise when the audience is
one region. window-clamp: the biased hour must land inside the account's allowed posting window
(cfg.account_window) — surface_time does not consult it today, so a winner outside the window is SKIPPED
(never propose a slot the cadence layer later rejects). NO hour variance in the published set => no runner-up
=> no winner => no-op (stated, not hidden — a fixed schedule has nothing to learn from).

C1-safe: this module NEVER retires / cascades / touches track — it reads reach and writes ONE prior file.
Deterministic: reach_mean desc + hour asc, so a re-run on the same ledger is idempotent."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.digest import aggregate_by_dim
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.validation_gate import p4_unlocked

_DIM = "publish_hour"


def timing_bias_winner(led, cfg) -> "dict | None":
    """Pure, read-only. Return {publish_hour:int, reach_mean:float} when publish_hour is UNLOCKED
    (learning_validated + >=8 attributed posts across >=2 hours) AND one hour's mean reach beats the
    runner-up by >= cfg.p4_min_reach_gap. None on any doubt (frozen, thin, single-hour, or no clear lead)
    — the no-op path. No I/O, no mutation."""
    if not p4_unlocked(led, cfg, _DIM):
        return None
    agg = aggregate_by_dim(led, _DIM)                    # {"18": {n, reach_sum, reach_mean, ...}, ...}
    if len(agg) < 2:
        return None                                      # need a runner-up to be comparative (no variance -> no-op)
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["reach_mean"], kv[0]))   # reach desc, hour asc
    leader_hour, leader_row = ranked[0]
    diff = leader_row["reach_mean"] - ranked[1][1]["reach_mean"]
    if diff <= 0 or diff < cfg.p4_min_reach_gap:
        return None                                      # not a clear lead (exact tie excluded)
    try:
        return {"publish_hour": int(leader_hour), "reach_mean": leader_row["reach_mean"]}
    except (ValueError, TypeError):
        return None                                      # a non-int hour bucket -> unrankable, no-op


def _in_window(hour: int, window) -> bool:
    """True iff `hour` falls inside the account's (open, close) posting window. None window => 24h open
    (True). Handles a wrap-around window (open > close, e.g. 22->6) as a night span."""
    if window is None:
        return True
    lo, hi = window
    if lo == hi:
        return True                                      # degenerate/24h -> open
    if lo < hi:
        return lo <= hour < hi
    return hour >= lo or hour < hi                        # wrap-around (spans midnight)


def timing_bias_hour_for(led, cfg, handle: str) -> "int | None":
    """The window-CLAMPED timing suggestion for one account: the reach-winning hour IF it lands inside
    cfg.account_window(handle), else None (skip the bias rather than propose a rejected slot). Fail-safe:
    no winner -> None. Read-only."""
    win = timing_bias_winner(led, cfg)
    if win is None:
        return None
    hour = win["publish_hour"]
    try:
        window = cfg.account_window(handle)
    except Exception:
        window = None                                    # unknown window -> treat as 24h open (fail-open)
    return hour if _in_window(hour, window) else None


def apply_timing_bias(led: Ledger, cfg: Config) -> Ledger:
    """Actuator. When the kill switch is ON and learning is validated, compute the reach-winning hour and
    persist it as a durable prior (cfg.timing_bias_path) that surface_time's caller consumes to nudge the
    schedule slot. Inert when FANOPS_TIMING_BIAS is off (the default) OR until learning_validated (FROZEN,
    logged). FAIL-SAFE: any exception -> log once, prior file left untouched, return led. No winner -> the
    prior file is CLEARED (a stale winner must not linger once the signal fades) — but a clear that itself
    errors is swallowed. Returns led unchanged (the prior lives in a control file, not the ledger)."""
    if not cfg.timing_bias:
        return led                                       # kill switch / default OFF -> inert
    from fanops.validation_gate import learning_validated
    if not learning_validated(cfg):
        get_logger(cfg)("timing_bias", "-", "skipped_unvalidated",
                        hint="auto-unfreezes on the first real non-degraded live metric")
        return led
    try:
        win = timing_bias_winner(led, cfg)
    except Exception as e:
        get_logger(cfg)("timing_bias", "-", "error", err=f"winner: {str(e)[:110]}")   # fail-SAFE
        return led
    try:
        if win is None:
            # No trusted winner (fixed schedule / thin / faded) -> remove any stale prior so the schedule
            # is byte-identical to today. A missing file is the no-bias state.
            if cfg.timing_bias_path.exists():
                cfg.timing_bias_path.unlink()
            return led
        cfg.timing_bias_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.timing_bias_path.write_text(json.dumps({"publish_hour": win["publish_hour"],
                                                    "reach_mean": win["reach_mean"]}, sort_keys=True))
    except OSError as e:
        get_logger(cfg)("timing_bias", "-", "error", err=f"persist: {str(e)[:110]}")   # fail-SAFE
    return led
