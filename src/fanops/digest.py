"""Human-readable digest: unit counts by state, brand-risk holds, FAILURES (posts in failed +
units in error — FIX F51), and the agent steps awaiting a response."""
from __future__ import annotations
import logging
from collections import Counter
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, Platform, PostState
from fanops.agentstep import pending
# Creative-variation v2: reuse the gated scorer so the digest's per-surface "learning ACTIVE / gathering
# data" annotation and request_captions' caption-bias share ONE gate-logic home (they can never drift).
# The digest is a READ-ONLY observability surface — importing the (pure, read-only) learner here is safe
# and does NOT touch the C1 amplify/delete-cascade path (track.py/pipeline.py stay blind to it; the
# isolation grep test enforces that). Bound at module scope so the fail-open path is unit-patchable.
from fanops.variant_learning import best_hooks
# Creative-variation v3: when FANOPS_VARIANT_UCB is on, the digest reports the bandit's pick for
# the surface instead of the greedy gate wording. SAME read-only safe side; fail-open. Bound at
# module scope so the fail-open path is unit-patchable.
from fanops.variant_learning import ucb_rank
# Transfer (v2 follow-up): the SAME read-only safe side. Used to annotate a COLD surface that is
# receiving a borrowed cross-surface prior. Fail-open like best_hooks; does NOT touch the C1 path.
from fanops.variant_transfer import transferred_hooks

logger = logging.getLogger(__name__)

def _counts(units) -> str:
    c = Counter(u.state.value for u in units)
    return "".join(f"  - {s}: {n}\n" for s, n in sorted(c.items())) or "  (none)\n"


# P3 attribution: which creative decision earns REACH. The signals the algorithm rewards (saves/shares/
# retention) feed lift_score, but REACH (impressions) is the operator's objective — and lift weights it
# 0.001 (inert), so this reports the RAW reach per stamped dim value, never lift_score. P4's INPUT.
_ENGAGEMENT_KEYS = ("saves", "shares", "retention")

def aggregate_by_dim(led: Ledger, dim: str) -> dict:
    """Group ANALYZED posts by one stamped creative dim (hook_pattern | first_frame_kind | clip_profile
    | variation_axis) and report per value: n, raw reach (sum+mean), and mean engagement context. REACH-
    FIRST — the primary number is the raw `reach` metric, not the engagement-skewed lift_score. Posts
    missing the dim (None) or not yet analyzed are skipped. Pure + empty-safe ({} when nothing matches)."""
    buckets: dict[str, list] = {}
    for p in led.posts.values():
        if p.state is not PostState.analyzed:
            continue
        value = getattr(p, dim, None)
        if value is None:
            continue
        buckets.setdefault(str(value), []).append(p)
    out: dict = {}
    for value, posts in buckets.items():
        n = len(posts)
        reaches = [float(p.metrics.get("reach", 0.0) or 0.0) for p in posts]
        reach_sum = round(sum(reaches), 4)
        row = {"n": n, "reach_sum": reach_sum, "reach_mean": round(reach_sum / n, 4)}
        for k in _ENGAGEMENT_KEYS:
            vals = [float(p.metrics.get(k, 0.0) or 0.0) for p in posts]
            row[f"{k}_mean"] = round(sum(vals) / n, 4)
        out[value] = row
    return out

def gate_state(led: Ledger, cfg: Config, account: str, platform: Platform,
               _cache: dict[tuple[str, str], str] | None = None, accounts=None) -> str:
    """The learning-loop state for one (account, platform) surface, for the "Lift by variant" digest
    section. "learning ACTIVE" iff the surface has its OWN gated winner (variant_learning.best_hooks
    — the SAME scorer request_captions biases on). Else, if transfer is on and the surface would
    receive a borrowed cross-surface prior, "borrowing platform signal". Otherwise "gathering data"
    (the loop is still open here). FAIL-OPEN: any error degrades to "gathering data" (the safe
    default). Memoised per render via the optional _cache. PUBLIC on purpose (stage-6 audit):
    Studio's Lift view consumes it too — it was a private name load-bearing across modules."""
    key = (account, platform.value)
    if _cache is not None and key in _cache:
        return _cache[key]
    try:
        if cfg.variant_ucb:                                # v3: the active allocator reports its pick
            picked = ucb_rank(led, cfg, account, platform)
            state = f'UCB -> "{picked[0]}"' if picked else "gathering data"
        elif best_hooks(led, cfg, account, platform):
            state = "learning ACTIVE"
        elif cfg.variant_transfer and accounts is not None and \
                transferred_hooks(led, cfg, accounts, account, platform):
            state = "borrowing platform signal"
        else:
            state = "gathering data"
    except Exception:
        logger.warning("variant gate-state degraded to 'gathering data' (fail-open)", exc_info=True)
        state = "gathering data"
    if _cache is not None:
        _cache[key] = state
    return state

def render_digest(led: Ledger, cfg: Config, accounts=None) -> str:
    out = ["# FAN OPS Ledger Digest\n"]
    out.append(f"\n**Sources** ({len(led.sources)}):\n" + _counts(led.sources.values()))
    out.append(f"\n**Moments** ({len(led.moments)}):\n" + _counts(led.moments.values()))
    out.append(f"\n**Clips** ({len(led.clips)}):\n" + _counts(led.clips.values()))
    out.append(f"\n**Posts** ({len(led.posts)}):\n" + _counts(led.posts.values()))

    holds = [f"- clip `{c.id}` (moment {c.parent_id}): {c.held_reason or '(no reason given)'}"
             for c in led.clips.values() if c.held]
    if holds:
        out.append("\n## Brand-risk holds (need Moh)\n" + "\n".join(holds) + "\n")

    fails = ([f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
              for p in led.posts.values()
              if p.state in (PostState.failed, PostState.error)] +          # M4: error too
             [f"- {kind} `{u.id}`: {u.error_reason or '(no reason given)'}"
              for kind, store in (("source", led.sources), ("moment", led.moments),
                                  ("clip", led.clips), ("stitch", led.stitch_plans))  # M3 structural-hooks
              for u in store.values() if u.state.value == "error"])         # M3: drop getattr
    if fails:
        out.append("\n## Failures (need attention)\n" + "\n".join(fails) + "\n")

    # Needs reconcile (AUDIT C1): an ambiguous publish failure (5xx / network timeout after the
    # body was sent) — the post MAY be live on the platform. It is deliberately NOT in Failures
    # (re-queueing a failed post is safe; re-queueing this one could double-post). Surface it on
    # its own so the operator verifies via GET /v2/posts/:id (or my.blotato.com/failed) before any
    # resubmit. This is a manual step by design — there is no idempotency key to make it automatic.
    reconcile = [f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
                 for p in led.posts.values() if p.state is PostState.needs_reconcile]
    if reconcile:
        out.append("\n## Needs reconcile (may be live — verify before resubmit)\n"
                   + "\n".join(reconcile) + "\n")

    # Published but never measured: track.py flips published->analyzed only when a metrics row
    # matches by submission_id, so a post that shipped but Blotato never returned metrics for
    # stays 'published' with empty metrics forever. Surface it so the operator notices (the
    # one stuck-state the pipeline can't auto-resolve — you can't fabricate metrics).
    unmeasured = [f"- post `{p.id}` ({p.platform.value}): published, no metrics yet"
                  for p in led.posts.values()
                  if p.state is PostState.published and not p.metrics]
    if unmeasured:
        out.append("\n## Published but unmeasured (shipped, never measured)\n"
                   + "\n".join(unmeasured) + "\n")

    # Creative-variation observability (v1): rank analyzed posts that carry a variant by lift_score,
    # so the operator sees which per-account creative treatment performs. v2 annotates each surface's
    # LEARNING-LOOP state ("learning ACTIVE" once it crossed the trust gate, else "gathering data")
    # via the SAME gated scorer request_captions uses (one gate-logic home). Still observe-only on the
    # amplify side — no automated propagation (that touches the amplify machinery, deferred / C1).
    variant_posts = [p for p in led.posts.values()
                     if p.variant_key and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    if variant_posts:
        rows = sorted(variant_posts, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True)
        gate_cache: dict[tuple[str, str], str] = {}     # one best_hooks call per surface per render
        lines = [f"- `{p.variant_hook or p.variant_key}` ({p.account}/{p.platform.value}): "
                 f"lift {p.metrics.get(LIFT_SCORE, 0.0)}"
                 # T4: surface the honest-lift marker — a degraded score (a primary metric absent from the
                 # row) is partial, so flag it inline + name the missing keys instead of letting the operator
                 # trust a reach/shares-dominated scalar as a full-objective number.
                 + (f" [DEGRADED: missing {', '.join(p.metrics.get('lift_missing_keys') or [])}]"
                    if p.metrics.get("lift_degraded") else "")
                 + f" — {gate_state(led, cfg, p.account, p.platform, gate_cache, accounts)}"
                 for p in rows]
        out.append("\n## Lift by variant (which creative is winning)\n" + "\n".join(lines) + "\n")

    # variant-amplify (v3) observability: per surface, the sustained-win streak toward the amplify
    # gate. "amplified" = currently a full-gate candidate; "building streak (n/MIN)" = a winner whose
    # lead has not yet sustained across enough windows; "gathering data" = no current streak. Fail-open
    # and gated on the flag (section absent when v3 is off). Read-only: reuses the streak state +
    # amplify_candidates (one gate home in variant_amplify), never mutates / never touches retire.
    if cfg.variant_amplify:
        try:
            from fanops.variant_amplify import amplify_candidates, _surfaces
            cands = amplify_candidates(led, cfg)
            cand_sources = {c["source_id"] for c in cands}
            alines = []
            for account, platform in sorted(_surfaces(led), key=lambda s: (s[0], s[1].value)):
                entry = led.variant_streaks.get(f"{account}|{platform.value}", {})
                hook = entry.get("hook")
                streak = int(entry.get("streak", 0))
                # is THIS surface's winning hook a current candidate? (match by hook AND source so a
                # cross-surface candidate can't mislabel this row)
                is_amplified = bool(hook) and any(
                    c["winning_hook"] == hook and c["source_id"] in cand_sources for c in cands)
                state = ("amplified" if is_amplified
                         else f"building streak ({streak}/{cfg.variant_amplify_min_streak})" if streak
                         else "gathering data")
                alines.append(f"- `{hook or '-'}` ({account}/{platform.value}): {state}")
            if alines:
                out.append("\n## Variant amplification (v3 — proven winners → more reach)\n"
                           + "\n".join(alines) + "\n")
        except Exception:
            logger.warning("variant-amplify digest section degraded (fail-open)", exc_info=True)

    # ECC fix #16: compute the pending-gate lists ONCE (was 4 pending() filesystem scans producing
    # two byte-identical sections). Both sections below reuse this single read.
    gates = ([f"- moments: {k}" for k in pending(cfg, kind="moments")] +
             [f"- captions: {k}" for k in pending(cfg, kind="captions")])
    if gates:
        out.append("\n## Awaiting agent (request written, no response yet)\n"
                   + "\n".join(gates) + "\n")

    # E3: an explicit "Pending agent gates" section (the word 'pending' is the searchable signal a
    # monitor/operator greps for) — same per-kind list as Awaiting, gated on the same pending keys
    # so an empty ledger renders neither. These are the gates a responder has NOT yet cleared.
    if gates:
        out.append("\n## Pending agent gates (responder has not cleared)\n"
                   + "\n".join(gates) + "\n")
    return "".join(out)

def write_digest(led: Ledger, cfg: Config) -> None:
    cfg.digest_path.parent.mkdir(parents=True, exist_ok=True)
    # Self-load the account registry so the "borrowing platform signal" annotation works for every
    # write_digest caller (pipeline + all CLI verbs) WITHOUT threading accounts through 7 call sites.
    # Only needed when transfer is on; FAIL-OPEN — a missing/corrupt registry must never blank or
    # crash the digest, so degrade to None (no borrowing label, exactly v2 behavior).
    accounts = None
    if cfg.variant_transfer:
        try:
            from fanops.accounts import Accounts
            accounts = Accounts.load(cfg)
        except Exception:
            logger.warning("digest accounts load skipped (fail-open)", exc_info=True)
            accounts = None
    cfg.digest_path.write_text(render_digest(led, cfg, accounts=accounts))
