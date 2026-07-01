"""Leg 3 Task 4 (the heaviest actuator) — the reach prior casting SELECTION never had.

The casting gate (casting.request_moment_casting) picks each account its OWN moments off its persona
plus casting._learned_account_signal (a deterministic per-account HISTORY hint). That hint says WHAT an
account historically took; it does NOT say what an account actually REACHES on. This module supplies the
missing half: a per-(account, clip_profile) REACH prior that leans the selector toward the account proven
to reach on a given content type.

Discipline (mirrors p4_dim_bias / timing_bias exactly):
  - BIAS-ONLY (audit C1): a READ-ONLY brief hint, never a ledger mutation, never retire/cascade/track. It
    is consumed at brief-build time by request_moment_casting (the sibling of _learned_account_signal),
    so there is no cmd_run control-file — the prior is recomputed from the live ledger each gate.
  - Own KILL SWITCH: cfg.casting_bias, DEFAULT OFF. When off, request_moment_casting never calls in here.
  - VALIDATION-FROZEN: casting_reach_prior returns {} until validation_gate.learning_validated (the live
    metric shape is proven) — the casting brief is byte-identical until then, even with the switch ON.
  - FAIL-SAFE: any internal error degrades the prior to {} (casting stays byte-identical), logged once.

Explore-guard (crux #6 — load-bearing, prevents reach-monoculture):
  - The prior only ever ADDS a lean for a PROVEN (account, clip_profile) cell. A cell below the min
    attributed floor is UNPROVEN, not losing — it is OMITTED from the prior (never emitted as a negative).
  - Because it is a hint (never a filter), it can NOT remove an account from the candidate pool: every
    active account stays in the brief and keeps getting cast, so an under-exposed account can still prove
    itself. The lean nudges an otherwise-tie; it never starves.
"""
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.config import Config
from fanops.models import PostState, normalize_account_handle
from fanops.validation_gate import learning_validated, _MIN_ATTRIBUTED_N
from fanops.log import get_logger


def reach_by_account_type(led: Ledger) -> dict:
    """Group ANALYZED posts by the composite (account, clip_profile) cell and report per cell: n + raw
    reach (mean). The (account, clip_profile) key aggregate_by_dim can't express (it groups by a single
    stamped attribute). REACH-FIRST — the primary number is the raw `reach` metric, mirroring
    aggregate_by_dim. Posts not analyzed, or missing account/clip_profile, are skipped. Pure + empty-safe."""
    buckets: dict[tuple[str, str], list] = {}
    for p in led.posts.values():
        if p.state is not PostState.analyzed:
            continue
        acct = getattr(p, "account", None)
        prof = getattr(p, "clip_profile", None)
        if not acct or not prof:
            continue
        buckets.setdefault((acct, prof), []).append(p)
    out: dict = {}
    for key, posts in buckets.items():
        n = len(posts)
        reach_sum = sum(float(p.metrics.get("reach", 0.0) or 0.0) for p in posts)
        out[key] = {"n": n, "reach_mean": round(reach_sum / n, 4)}
    return out


def casting_reach_prior(led: Ledger, cfg: Config, handles: list[str]) -> dict:
    """The gated, fail-safe per-account reach hint for the casting brief: {handle: {clip_profile: reach_mean}}.

    Emits an entry for a (handle, clip_profile) cell ONLY when:
      - learning is validated (plumbing proven — validation-frozen half), AND
      - the cell has >= _MIN_ATTRIBUTED_N analyzed posts (the PER-CELL floor = the explore-guard: an
        unproven cell is OMITTED, never emitted as a negative).
    This ANNOTATES each proven cell (a lean), it does NOT pick a single comparative winner the way p4/timing
    do — so the _MIN_VALUES ">= 2 distinct values to rank" gate does NOT apply here: one proven account is a
    valid lean ("@a reaches on talk"), and an unproven account simply gets no annotation (the LLM still sees
    it in `personas` and casts it -> no starvation). An account with no proven cell is OMITTED. Fail-open -> {}."""
    try:
        if not learning_validated(cfg):
            return {}                                          # validation-frozen: byte-identical until proven
        wanted = {normalize_account_handle(h): h for h in handles}
        agg = reach_by_account_type(led)
        proven = {(a, prof): row for (a, prof), row in agg.items()
                  if normalize_account_handle(a) in wanted and row.get("n", 0) >= _MIN_ATTRIBUTED_N}
        out: dict = {}
        for (a, prof), row in proven.items():
            out.setdefault(wanted[normalize_account_handle(a)], {})[prof] = row["reach_mean"]
        return out
    except Exception as e:                                      # FAIL-SAFE: the prior is best-effort, never fatal
        get_logger(cfg)("casting_bias", "-", "error", err=str(e)[:120])   # logged once; caller gets {}
        return {}
