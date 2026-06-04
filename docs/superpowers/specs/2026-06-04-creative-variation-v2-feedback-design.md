# Creative Variation v2 — Closing the Learning Loop (Feedback) — Design Spec

**Date:** 2026-06-04 · **Backlog item:** (j) follow-up · **Status:** design settled, ready for implementation plan
**Builds on:** `2026-06-04-per-account-creative-variation-design.md` (v1, observe-only, shipped PR #9)

## Problem

v1 (PR #9) generates per-account creative variants and **measures** which wins (the digest's
"Lift by variant" section). But nothing **acts** on that measurement: a human must read the
digest and manually decide to reuse a winning hook. If no one reads it, the system fires
near-arbitrary hooks forever and never compounds — an A/B test with no feedback. The loop is
open.

v1 left this gap **on purpose** (spec §"Out of scope" line 60): the only existing place to
"act on a winner" is the `amplify` / `classify_outcomes` / `_delete_moment_cascade` machinery,
which has a CRITICAL cascade-delete bug history (audit C1). Auto-propagating an early, noisy
"variant A is winning" signal through *that* path could delete real rendered content. With 2
accounts and a handful of posts, `lift_score` is statistically meaningless for the first dozen
data points — auto-acting on it would amplify noise and destroy work.

So v1 was the right call. But the *fix* is not "wire the winner into amplify" (still risky).
The fix is a **separate, safe feedback path** that the team conflated with the scary one and
therefore never built.

## The design (settled)

**Close the loop on the CHEAP, REVERSIBLE side — bias the next caption request — and gate it
on enough data to mean something. Never touch amplify/the delete cascade.**

Three decisions, all settled (no further input needed):

1. **Action = bias the next caption.** When a variant has earned a trustworthy win, append a
   learned hint to the caption-agent request payload (the existing `guidance` field at
   `caption.py:80`): *"Recent on-screen hooks that performed best for this account/platform:
   `<winning hook(s)>`. Lean toward this style (tone, length, angle) — do not copy verbatim."*
   The caption agent **already** returns a per-surface `hook`; we are biasing what it returns,
   not changing the contract. This is cheap (one extra string in a request we already write),
   reversible (drop the hint, behavior reverts), generalizes across clips (style, not verbatim),
   and touches **none** of `amplify`/`classify_outcomes`/`_delete_moment_cascade`.
   - *Rejected:* verbatim-reuse (too rigid — no generalization across clips); full
     auto-amplify (the C1-risk path v1 correctly avoided — explicitly still out of scope).

2. **Trust gate = min posts AND min lift gap.** A hint is only emitted for a (account, platform)
   surface when its leading variant has **≥ `FANOPS_VARIANT_MIN_POSTS` analyzed posts** (default
   3) carrying a `lift_score`, **AND** the leader's mean lift beats the runner-up's mean lift by
   **≥ `FANOPS_VARIANT_MIN_GAP`** (default a margin, not noise). Below either threshold → **no
   hint** (the loop stays open for that surface until data accrues). This is the entire point:
   with 2 accounts, acting on 2 data points is the noise-amplification trap.
   - *Rejected:* "pick the current leader however little data" — that is exactly the early-noise
     failure mode v1 was built to avoid.

3. **Process = spec → plan → TDD,** default **OFF** behind its own flag
   (`FANOPS_VARIANT_LEARNING`, independent of `FANOPS_CREATIVE_VARIATION`), **fail-open**:
   any error building the hint → no hint → today's behavior. Purely additive.

## Architecture

The read side already exists (`digest.py:61` collects analyzed posts with `variant_key` +
`lift_score`). v2 factors that collection into a reusable scorer and feeds its verdict into the
caption request.

**Data flow (new arrows in CAPS):**
```
... v1 ... → publish → track → analyzed  (lift_score per Post, per variant — UNCHANGED)
  → variant_learning.best_hooks(led, cfg, account, platform):           # NEW (pure, read-only)
        gather analyzed posts with variant_key for THIS surface
        group by variant_hook → mean lift_score per hook
        IF leader has >= MIN_POSTS AND (leader_mean - runner_up_mean) >= MIN_GAP:
            return [leader hook(s)]   ELSE return []                     # gated
  → request_captions:                                                     # MODIFIED
        guidance = _guidance(cfg)
        IF FANOPS_VARIANT_LEARNING and best_hooks non-empty:
            guidance += learned-hint block (the winning hook style)       # the loop CLOSES here
        payload["guidance"] = guidance
  → caption agent returns per-surface hooks BIASED toward what won        # compounding
```

**Why this is safe:** the only state read is `Post.metrics["lift_score"]` + `variant_*`
(already there). The only state written is the caption *request payload* — an agent-input file,
not the ledger, not a unit's state, not the amplify path. A wrong hint at worst nudges one
caption generation; it cannot delete or retire anything. Fully reversible.

## Units / interfaces (what changes)

- **`variant_learning.py`** (NEW module) — `best_hooks(led, cfg, account, platform) -> list[str]`:
  pure, read-only. Gathers `analyzed` posts with a `variant_key` for the given surface, groups by
  `variant_hook`, computes mean `lift_score` per hook, applies the MIN_POSTS + MIN_GAP gate,
  returns the winning hook(s) or `[]`. No I/O, no mutation — trivially testable.
- **`caption.py`** — `request_captions` appends the learned-hint block to `guidance` when
  `cfg.variant_learning` is on and `best_hooks(...)` is non-empty, per surface. Fail-open: any
  exception in the learning call is swallowed (logged once) → no hint.
- **`config.py`** — `FANOPS_VARIANT_LEARNING` (default OFF), `FANOPS_VARIANT_MIN_POSTS`
  (default 3), `FANOPS_VARIANT_MIN_GAP` (default e.g. 10.0, same lift_score scale as `lift_floor`).
- **`prompts.py`** — `caption_prompt` renders the learned-hint block when present in the payload
  (a labelled "what worked recently" section the model leans toward but is told not to copy).
- **`digest.py`** — (optional, cheap) note in the "Lift by variant" section whether a surface has
  crossed the trust gate ("learning ACTIVE" vs "gathering data"), so the operator sees the loop's
  state. Reuses `variant_learning` so the gate logic lives in one place.

## Testing strategy

- `variant_learning.best_hooks`: unit — (a) below MIN_POSTS → `[]`; (b) enough posts but gap <
  MIN_GAP → `[]` (noise guard); (c) clear winner over threshold → that hook; (d) ties / empty /
  no-variant posts → `[]`; (e) determinism: same ledger → same result. This is the load-bearing
  test (the gate is the whole safety argument).
- `request_captions`: with learning ON + a surface past the gate → the request payload's
  `guidance` CONTAINS the winning hook; with learning OFF, OR surface below gate → payload is
  byte-identical to today (no hint). Fail-open: a raising `best_hooks` → request still written,
  no hint, clip still advances.
- `caption_prompt`: hint present in payload → rendered in the prompt (and the "don't copy
  verbatim" instruction present); absent → prompt byte-identical to today.
- **Adversarial / amplify-isolation:** `grep` proof that `variant_learning` is imported by
  `caption.py`/`digest.py` ONLY — never by `track.py`/`pipeline.py` (the amplify path stays
  blind to it, mirroring v1's invariant).
- Backward-compat: learning OFF leaves the full suite green; an old ledger (posts without
  `variant_key`) → `best_hooks` returns `[]`, no crash.
- **Real integration:** seed a ledger where account A's hook clearly out-lifts B's over ≥
  MIN_POSTS, run `request_captions` with learning ON, assert the real request file on disk
  carries A's winning hook in its guidance — the loop closing, proven end-to-end.

## Out of scope (v2)

- **Automated propagation into amplify** (`_delete_moment_cascade` / `classify_outcomes`) — still
  the C1-risk path, still deferred. v2 deliberately stays on the caption-bias side of the line.
- Cross-surface / cross-account learning transfer (account A's winner informing account B) — v2
  learns per-surface only; transfer is a later optimization once per-surface data is rich.
- Bayesian / multi-armed-bandit exploration-exploitation scheduling — v2 is a simple
  gated-greedy bias; smarter allocation is a follow-up if it proves worth it.
- Learning the caption BODY style (not just the hook) — possible later; v2 scopes to the hook,
  the axis v1 already varies and attributes.

## Risks / guardrails

- **Early-noise (the core risk):** the MIN_POSTS + MIN_GAP gate IS the mitigation. Defaults are
  conservative (3 posts, real margin); a surface below threshold simply gets no hint. Test (b)
  above locks this in.
- **Amplify-cascade isolation (C1):** v2 touches NONE of `amplify`/`classify_outcomes`/
  `_delete_moment_cascade`. Enforced by the grep-isolation test. The loop closes on the
  agent-request side only.
- **Fail-open everywhere:** learning off, gate not met, any exception, old ledger → today's
  behavior. A variant-learning failure can never block a caption, hold a clip, or fail a post.
- **Determinism:** `best_hooks` is a pure function of ledger state — no `random`, no `hash()`,
  no wall-clock — so a re-run yields the identical hint (consistent with the content-addressed
  invariant).
- **Reversibility:** the only written artifact is the caption *request* payload. Flip the flag
  off and the very next request reverts. Nothing persisted needs unwinding.
