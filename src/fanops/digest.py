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
from fanops.variant_learning import best_hooks, _hook_for_post
# Creative-variation v3: when FANOPS_VARIANT_UCB is on, the digest reports the bandit's pick for
# the surface instead of the greedy gate wording. SAME read-only safe side; fail-open. Bound at
# module scope so the fail-open path is unit-patchable.
from fanops.variant_learning import ucb_rank
# Transfer (v2 follow-up): the SAME read-only safe side. Used to annotate a COLD surface that is
# receiving a borrowed cross-surface prior. Fail-open like best_hooks; does NOT touch the C1 path.
from fanops.variant_transfer import transferred_hooks
from fanops.validation_gate import learning_validated   # transfer is VALIDATION-FROZEN -> reflect it in the label

logger = logging.getLogger(__name__)

def _counts(units) -> str:
    c = Counter(u.state.value for u in units)
    return "".join(f"  - {s}: {n}\n" for s, n in sorted(c.items())) or "  (none)\n"


# P3 attribution: which creative decision earns REACH. The signals the algorithm rewards (saves/shares/
# retention) feed lift_score, but REACH (impressions) is the operator's objective — and lift weights it
# 0.001 (inert), so this reports the RAW reach per stamped dim value, never lift_score. P4's INPUT.
_ENGAGEMENT_KEYS = ("saves", "shares", "retention")

def aggregate_by_dim(led: Ledger, dim: str) -> dict:
    """Group ANALYZED posts by one stamped creative dim (first_frame_kind | clip_profile
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
        elif cfg.variant_transfer and learning_validated(cfg) and accounts is not None and \
                transferred_hooks(led, cfg, accounts, account, platform):
            state = "borrowing platform signal"     # only once VALIDATION-FROZEN gate opens (matches caption inject)
        else:
            state = "gathering data"
    except Exception:
        logger.warning("variant gate-state degraded to 'gathering data' (fail-open)", exc_info=True)
        state = "gathering data"
    if _cache is not None:
        _cache[key] = state
    return state

def _holds(led: Ledger) -> list[str]:
    holds = [f"- clip `{c.id}` (moment {c.parent_id}): {c.held_reason or '(no reason given)'}"
             for c in led.clips.values() if c.held]
    return ["\n## Brand-risk holds (need Moh)\n" + "\n".join(holds) + "\n"] if holds else []


def _failures(led: Ledger) -> list[str]:
    fails = ([f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
              for p in led.posts.values()
              if p.state in (PostState.failed, PostState.error)] +          # M4: error too
             [f"- {kind} `{u.id}`: {u.error_reason or '(no reason given)'}"
              for kind, store in (("source", led.sources), ("moment", led.moments),
                                  ("clip", led.clips), ("stitch", led.stitch_plans))  # M3 structural-hooks
              for u in store.values() if u.state.value == "error"])         # M3: drop getattr
    return ["\n## Failures (need attention)\n" + "\n".join(fails) + "\n"] if fails else []


def _needs_reconcile(led: Ledger) -> list[str]:
    # Needs reconcile (AUDIT C1): an ambiguous publish failure (5xx / network timeout after the
    # body was sent) — the post MAY be live on the platform. It is deliberately NOT in Failures
    # (re-queueing a failed post is safe; re-queueing this one could double-post). Surface it on
    # its own so the operator verifies via the backend's per-post status endpoint before any
    # resubmit. This is a manual step by design — there is no idempotency key to make it automatic.
    reconcile = [f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
                 for p in led.posts.values() if p.state is PostState.needs_reconcile]
    return (["\n## Needs reconcile (may be live — verify before resubmit)\n"
             + "\n".join(reconcile) + "\n"] if reconcile else [])


def _unmeasured(led: Ledger) -> list[str]:
    # Published but never measured: track.py flips published->analyzed only when a metrics row
    # matches by submission_id, so a post that shipped but the backend never returned metrics for
    # stays 'published' with empty metrics forever. Surface it so the operator notices (the
    # one stuck-state the pipeline can't auto-resolve — you can't fabricate metrics).
    unmeasured = [f"- post `{p.id}` ({p.platform.value}): published, no metrics yet"
                  for p in led.posts.values()
                  if p.state is PostState.published and not p.metrics]
    return (["\n## Published but unmeasured (shipped, never measured)\n"
             + "\n".join(unmeasured) + "\n"] if unmeasured else [])


def _variant_lift(led: Ledger, cfg: Config, accounts=None) -> list[str]:
    # Creative-variation observability (v1): rank analyzed posts that carry a variant by lift_score,
    # so the operator sees which per-account creative treatment performs. v2 annotates each surface's
    # LEARNING-LOOP state ("learning ACTIVE" once it crossed the trust gate, else "gathering data")
    # via the SAME gated scorer request_captions uses (one gate-logic home). Still observe-only on the
    # amplify side — no automated propagation (that touches the amplify machinery, deferred / C1).
    variant_posts = [p for p in led.posts.values()
                     if _hook_for_post(led, p) and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    if not variant_posts:
        return []
    rows = sorted(variant_posts, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True)
    gate_cache: dict[tuple[str, str], str] = {}     # one best_hooks call per surface per render
    lines = [f"- `{_hook_for_post(led, p) or p.id}` ({p.account}/{p.platform.value}): "
             f"lift {p.metrics.get(LIFT_SCORE, 0.0)}"
             # T4: surface the honest-lift marker — a degraded score (a primary metric absent from the
             # row) is partial, so flag it inline + name the missing keys instead of letting the operator
             # trust a reach/shares-dominated scalar as a full-objective number.
             + (f" [DEGRADED: missing {', '.join(p.metrics.get('lift_missing_keys') or [])}]"
                if p.metrics.get("lift_degraded") else "")
             + f" — {gate_state(led, cfg, p.account, p.platform, gate_cache, accounts)}"
             for p in rows]
    return ["\n## Lift by variant (which creative is winning)\n" + "\n".join(lines) + "\n"]


def _variant_amplify(led: Ledger, cfg: Config) -> list[str]:
    # variant-amplify (v3) observability: per surface, the sustained-win streak toward the amplify
    # gate. "amplified" = currently a full-gate candidate; "building streak (n/MIN)" = a winner whose
    # lead has not yet sustained across enough windows; "gathering data" = no current streak. Fail-open
    # and gated on the flag (section absent when v3 is off). Read-only: reuses the streak state +
    # amplify_candidates (one gate home in variant_amplify), never mutates / never touches retire.
    if not cfg.variant_amplify:
        return []
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
            return ["\n## Variant amplification (v3 — proven winners → more reach)\n"
                    + "\n".join(alines) + "\n"]
    except Exception:
        logger.warning("variant-amplify digest section degraded (fail-open)", exc_info=True)
    return []


def _reach_by_dim(led: Ledger, cfg: Config) -> list[str]:
    # P3 surface (#7): for each stamped creative dim, once P4 is UNLOCKED for it (cutover-confirmed plumbing
    # AND enough attributed signal — validation_gate.p4_unlocked, per dim), surface aggregate_by_dim's REACH-
    # FIRST rollup. READ-ONLY observability, NOT an actuator — it never biases generation (the P4 ranker is a
    # separate default-OFF feature). Gated per dim so a thin/unconfirmed dim stays hidden; the whole section is
    # absent when nothing qualifies (byte-identical to today's digest). Fail-open like the v3 block above.
    try:
        from fanops.validation_gate import p4_unlocked
        dlines = []
        for dim in ("first_frame_kind", "clip_profile", "top_bias"):   # Leg 3: framing joins the rollup
            if not p4_unlocked(led, cfg, dim): continue
            for value, row in sorted(aggregate_by_dim(led, dim).items(),
                                     key=lambda kv: kv[1]["reach_mean"], reverse=True):
                dlines.append(f"- {dim} `{value}`: reach mean {row['reach_mean']} (n={row['n']}, sum {row['reach_sum']})")
        if dlines:
            return ["\n## Reach by creative dim (P3 — what's earning reach)\n" + "\n".join(dlines) + "\n"]
    except Exception:
        logger.warning("P3 reach-by-dim digest section degraded (fail-open)", exc_info=True)
    return []


def _culmination(led: Ledger, cfg: Config) -> list[str]:
    # Leg 3 (legibility): the loop's EFFECT, not just attribution. For each structural dim (framing +
    # length + first-frame via p4_dim_bias, timing via timing_bias) render the trusted winner AND whether
    # it is ACTIVELY biasing generation/schedule. Honest states:
    #   "<value> -> ACTIVE (biasing)"        a gated winner AND the dim's kill switch is ON
    #   "<value> -> winner found (bias OFF)" a gated winner but the kill switch is OFF (never claim ACTIVE)
    #   "gathering data"                     no gated winner (frozen / thin / no clear lead)
    # READ-ONLY — reuses the actuators' OWN gated winner fns (dim_bias_candidates / timing_bias_winner) so
    # the digest can never disagree with what the actuator would do. Fail-open: any error -> section absent
    # (byte-identical to today's digest), like the v3 block. The whole section is omitted when nothing
    # qualifies, so a fresh/degraded ledger reads exactly as before.
    try:
        from fanops.p4_dim_bias import dim_bias_candidates
        from fanops.timing_bias import timing_bias_winner
        lines: list[str] = []
        # framing / length / first-frame: p4_dim_bias's candidates, keyed by dim. Kill switch: cfg.p4_dim_bias.
        cands = {c["dim"]: c for c in dim_bias_candidates(led, cfg)}
        _P4_LABELS = {"top_bias": "framing", "clip_profile": "length", "first_frame_kind": "first-frame"}
        for dim, label in _P4_LABELS.items():
            cand = cands.get(dim)
            if cand is None:
                lines.append(f"- {label}: gathering data")
                continue
            val = cand["winning_value"]
            if dim == "top_bias":                                    # render the bool naturally
                val = "top-anchored" if val == "True" else "centered"
            state = "ACTIVE (biasing)" if cfg.p4_dim_bias else "winner found (bias OFF)"
            lines.append(f"- {label}: {val} -> {state}")
        # timing: timing_bias's winner (reach-by-hour). Kill switch: cfg.timing_bias.
        twin = timing_bias_winner(led, cfg)
        if twin is None:
            lines.append("- timing: gathering data")
        else:
            state = "ACTIVE (biasing)" if cfg.timing_bias else "winner found (bias OFF)"
            lines.append(f"- timing: hour {twin['publish_hour']} -> {state}")
        # Only render the section if at least one dim has a real winner (else it is pure "gathering data"
        # noise on a cold ledger — omit it for byte-identical-to-today behaviour).
        if cands or twin is not None:
            return ["\n## Culmination (what the learning loop is biasing)\n" + "\n".join(lines) + "\n"]
    except Exception:
        logger.warning("culmination digest section degraded (fail-open)", exc_info=True)
    return []


def _pending_gates(cfg: Config) -> list[str]:
    # ECC fix #16: compute the pending-gate lists ONCE (was 4 pending() filesystem scans producing
    # two byte-identical sections). Both sections below reuse this single read.
    gates = ([f"- moments: {k}" for k in pending(cfg, kind="moments")] +
             [f"- moment_hooks: {k}" for k in pending(cfg, kind="moment_hooks")] +
             [f"- captions: {k}" for k in pending(cfg, kind="captions")])
    if not gates:
        return []
    body = "\n".join(gates)
    # Two sections share the SAME gate list: "Awaiting" + E3's explicit "Pending agent gates" (the word
    # 'pending' is the searchable signal a monitor/operator greps for). Both gated on the same keys.
    return ["\n## Awaiting agent (request written, no response yet)\n" + body + "\n",
            "\n## Pending agent gates (responder has not cleared)\n" + body + "\n"]


def render_digest(led: Ledger, cfg: Config, accounts=None) -> str:
    out = ["# FAN OPS Ledger Digest\n"]
    out.append(f"\n**Sources** ({len(led.sources)}):\n" + _counts(led.sources.values()))
    out.append(f"\n**Moments** ({len(led.moments)}):\n" + _counts(led.moments.values()))
    out.append(f"\n**Clips** ({len(led.clips)}):\n" + _counts(led.clips.values()))
    out.append(f"\n**Posts** ({len(led.posts)}):\n" + _counts(led.posts.values()))
    out += _holds(led)
    out += _failures(led)
    out += _needs_reconcile(led)
    out += _unmeasured(led)
    out += _variant_lift(led, cfg, accounts)
    out += _variant_amplify(led, cfg)
    out += _reach_by_dim(led, cfg)
    out += _culmination(led, cfg)
    out += _pending_gates(cfg)
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
    # FAIL-OPEN: the digest is a convenience artifact written AFTER the ledger is committed. An OSError
    # here (disk full / permissions) must NOT abort advance()/the CLI verb (a non-zero exit respins the
    # daemon against the same disk) — log it and move on, exactly like _archive_published (post/run.py).
    try:
        cfg.digest_path.write_text(render_digest(led, cfg, accounts=accounts))
    except OSError:
        logger.warning("digest write skipped (fail-open)", exc_info=True)
