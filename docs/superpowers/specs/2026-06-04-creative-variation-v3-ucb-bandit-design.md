# Creative Variation v3 — Deterministic UCB Bandit Allocation (Explore vs. Exploit) — Design Spec

**Date:** 2026-06-04 · **Backlog item:** v2 follow-up (3 of 3 — the LOWEST-priority one) · **Status:** design settled, ready for implementation plan
**Builds on:** `2026-06-04-creative-variation-v2-feedback-design.md` (v2, gated-greedy caption-bias, shipped PR #14, `5f275fd`)
**Prereq (verified):** v2 merged to `main` (`5f275fd`); `variant_learning.best_hooks` present; suite at 387/1.

## Problem

v2 closed the A/B learning loop on the safe side: when a hook variant earned a *trustworthy*
win (≥ `variant_min_posts` analyzed posts **AND** mean lift beating the runner-up by ≥
`variant_min_gap`), `request_captions` biases the next caption toward it. That gate is a
**hard cutoff with two failure modes**:

1. **Pure-exploit lock-in once the gate is met.** `best_hooks` returns the single highest-mean
   hook and nothing else. An early winner — possibly winning by luck over a handful of posts —
   becomes *the* bias and stays the bias. The system stops trying other hooks on that surface,
   so a genuinely better hook that simply hasn't been tried as often can never overtake it. The
   A/B test collapses into A-only.
2. **Silence below the gate wastes early signal.** Until a surface clears the comparative
   `min_gap`, `best_hooks` returns `[]` — *no* bias at all. With only ~2 accounts and a trickle
   of posts, surfaces sit below the gate for a long time, during which the accumulating lift
   data is simply ignored. The loop stays fully open exactly when cheap directional signal is
   most useful.

Both are symptoms of the same thing: **gated-greedy has no notion of exploration.** It either
locks onto a proven leader (1) or does nothing (2). It never *balances* trying under-sampled
hooks against favoring proven ones.

## The fix (settled)

**Replace the gated-greedy allocation with a deterministic multi-armed-bandit allocation —
UCB1 (Upper Confidence Bound) — over the hook variants of each (account, platform) surface.**
It keeps the *exact same caption-bias seam* v2 built (the single allocation hint injected into
the caption request `guidance`), inherits v2's *entire* safety story (caption-payload-only,
amplify/C1-blind, fail-open, default-OFF flag), and changes *only which hook the bias points at*
on each draw. It is never silent once any variant data exists, and it never permanently locks.

### Why UCB1 and not Thompson sampling — the central constraint

The codebase is **content-addressed and forbids non-determinism**: no `random`, no `hash()`, no
wall-clock. A re-run of the pipeline over the same ledger MUST reproduce byte-identical output
(idempotency is the #1 historical bug class). Classic **Thompson sampling draws from posterior
distributions — it needs a live RNG**, which is banned outright.

**UCB1 needs no randomness at all.** It is a deterministic formula:

```
                                    ┌──────────────┐
  score(hook) = mean_lift(hook) + c·│ √(ln N / nₕ) │
                └── exploit ──┘      └─ explore ────┘
```

- `mean_lift(hook)` — the hook's mean `lift_score` over its analyzed posts on this surface
  (the *exploit* term: favor what has performed well).
- `nₕ` — the number of analyzed posts for this hook on this surface (its "pull count").
- `N` — the total analyzed variant posts across all hooks on this surface (`N = Σ nₕ`).
- `c` — the exploration weight (the only tunable; default √2 ≈ 1.41421356, the UCB1 standard).
- The bandit picks `argmax score(hook)` — that hook becomes the single bias hint.

The exploration bonus `c·√(ln N / nₕ)` is **large for rarely-tried hooks** (small `nₕ`) and
**shrinks as a hook accrues data** (`nₕ` grows). So a hook that has only been tried twice gets an
optimism boost that can let it out-score a proven-but-stale leader — *that is the exploration*
v2 lacks — and as evidence accumulates the bonus decays and the term that dominates is the real
mean. **No seed, no PRNG, no clock**: the score is a pure arithmetic function of integer counts
and float means already in the ledger, so the same ledger yields the identical pick on every
re-run. Determinism is *structural*, not "seeded-and-hope" — this is precisely why UCB1 is the
right deterministic bandit and Thompson is not (seeded-Thompson was considered and **rejected**:
it would add a documented seed-derivation surface and a seeded-PRNG dependency to audit, for no
benefit over UCB1's structural determinism here).

### Cold-start and degenerate surfaces (the math edges that need a rule)

**Key framing: an arm only exists once it has data.** v3 scores over exactly the hooks that have
≥ 1 *analyzed* post on the surface — the same data pool `best_hooks` reads (guaranteed by the
shared `_collect_lifts` helper below). A hook variant that has been *requested* but whose post is
not yet `analyzed` simply isn't an arm yet — it carries no `lift_score`, so there is nothing to
score. This is deliberate and it dissolves the textbook "un-pulled arm = +∞" cold-start branch:
**by construction every arm has `nₕ ≥ 1`**, so `ln N / nₕ` is always defined and finite. (Textbook
UCB needs the +∞ rule because it enumerates all arms up front and must try each once; here arms
materialize only on first analyzed result, so "try every arm once" already happened before the arm
appears. No +∞ branch, no infinity to tie-break.) The remaining edges are about *how many arms /
how much data*, not zero-count arms:

- **`N == 0`** (no analyzed variant posts on the surface at all → no arms) → `[]`. Nothing to
  allocate; the loop stays open for this surface (identical to v2's empty case).
- **`N == 1`** (exactly one analyzed post, hence one arm, `nₕ == 1`): `ln N = ln 1 = 0`, so the
  bonus is `c·√(0/1) = 0` and the score is just that arm's bare mean → return that hook. The
  degenerate case falls out of the formula correctly with **no special-casing** — UCB with one arm
  and one pull *is* "use that arm." (The implementation still guards `N == 0` explicitly to avoid
  `ln 0`; `N ≥ 1` flows through the formula unguarded.)
- **A single hook with multiple posts** (`N > 1`, one distinct `variant_hook`): only one arm, so
  `argmax` is that arm regardless of the bonus. UCB returns it. This is intentionally **less strict
  than v2** — v2 returned `[]` for a lone variant (no runner-up to beat). v3 *does* surface a lone
  hook as the bias, because the whole point is to stop being silent when there is directional
  signal; a single consistently-tried hook IS the signal.

So the only explicit guard the implementation needs is `N == 0 → []` (avoids `ln 0`); every
non-empty surface flows through the UCB formula directly. No `+∞`, no insertion-order ambiguity.
The one place a deterministic tie-break is still required is **two distinct arms with identical
`(nₕ, mean_lift)`** (hence identical scores) — resolved by picking the sorted-string-lower hook,
never by insertion order or randomness.

There is **no `min_gap` in v3.** UCB's confidence interval *is* the noise guard: an early leader
with a thin lead does not lock, because under-sampled challengers carry a large optimism bonus
that keeps them in contention until enough data accrues to settle it. Replacing the blunt
`min_gap` floor with the bandit's native uncertainty handling is the entire reason v3 exists
(it directly kills failure mode 2 — "wastes early signal").

### Why this is still safe (inherited from v2, unchanged)

The safety argument is **identical to v2's** and does not rest on the gate that v3 removes:

- The **only state read** is `Post.metrics["lift_score"]` + `variant_key`/`variant_hook` +
  `account`/`platform`/`state` — all already recorded by v1/v2, all read-only.
- The **only state written** is the caption *request payload* (`guidance` / `learned_hooks`) —
  an agent-input file. Not the ledger, not a unit's `state`, not the amplify path. A wrong
  allocation at worst nudges one caption generation toward a sub-optimal hook style; **it cannot
  delete, retire, or amplify anything.** Fully reversible (drop the hint → behavior reverts).
- **NONE of `amplify` / `classify_outcomes` / `_delete_moment_cascade` (C1)** is touched. The
  module-import isolation invariant v2 established (AST data-flow test + best_hooks/ucb-caller
  lock — only `caption.py` / `digest.py` may call the scorer) extends verbatim to the new
  function. The amplify path stays blind to the learner.
- **Fail-open everywhere:** flag off, any exception, old ledger (no variant posts), or a
  degenerate surface → today's behavior (v2 gated-greedy when its flag is on, else no hint). A
  bandit failure can never block a caption, hold a clip, or fail a post.

So removing `min_gap` does **not** weaken the C1/cascade safety — that safety was always
"caption-payload-only, amplify-blind," never the gate. `min_gap` was v2's *exploration-noise*
mitigation because v2 had no other exploration mechanism; UCB's confidence term is the
principled replacement for exactly that job.

## Architecture

The seam, the payload, and the downstream renderer are **all unchanged from v2.** v3 adds one
pure scorer and selects it behind a flag inside the single existing `_learned_hooks` helper.

> **Note (codebase state at design time):** the *cross-account transfer* follow-up has since
> merged (commits `8abc8fe`…`82f9c29`). `caption.py` now assembles **two** learned-hook keys:
> `learned_hooks` (own-surface, from `best_hooks` via `_learned_hooks`) and
> `learned_hooks_transferred` (borrowed cross-surface style, from `transferred_hooks` via
> `_transferred_hooks`). **v3 replaces ONLY the own-surface allocator** — `best_hooks` →
> `ucb_rank`, inside `_learned_hooks`/`learned_hooks`. The transfer path
> (`variant_transfer.transferred_hooks`, `_transferred_hooks`, `learned_hooks_transferred`) is
> **orthogonal and untouched** by v3. They must not be entangled: transfer is a cold-start prior
> for surfaces with no own-data; UCB is the allocator over a surface's *own* analyzed data. A
> surface can legitimately carry both keys (UCB pick of its own + a borrowed prior).

**Data flow (the only new/changed arrows in CAPS — everything else is v1/v2, untouched):**
```
... v1/v2 ... → publish → track → analyzed   (lift_score per Post, per variant — UNCHANGED)
  → variant_learning.ucb_rank(led, cfg, account, platform) -> list[str]:    # NEW (pure, read-only)
        by_hook = _collect_lifts(led, account, platform)     # {hook: [lift,...]} — analyzed only
        N = Σ len(lifts)   (total analyzed variant posts on this surface; every arm has nₕ ≥ 1)
        N == 0                          → return []           (no arms — nothing to allocate)
        score(h) = mean(lifts_h) + c·sqrt(ln N / nₕ)         (c = cfg.variant_ucb_c, default √2)
                                                              (N == 1 → ln 1 = 0 → bonus 0 → bare mean)
        return [argmax score]   (top-1: the single allocation hint — SAME shape as best_hooks)
                                 (ties on identical (nₕ, mean) broken by sorted hook string — deterministic)
  → caption._learned_hooks(led, cfg, surfaces):                             # MODIFIED (scorer select)
        if not cfg.variant_learning: return []          # (unchanged master gate)
        scorer = ucb_rank if cfg.variant_ucb else best_hooks   # THE STRATEGY SWITCH
        try: for each surface, collect scorer(led, cfg, acct, plat) → dedup → learned
        except: log once, return []                     # (unchanged fail-open)
  → request_captions injects `learned_hooks` into guidance  # UNCHANGED (caption.py:115)
  → caption_prompt renders the "lean toward this STYLE, don't copy verbatim" block  # UNCHANGED
  → caption agent returns hooks biased toward the bandit's allocation       # explores AND exploits
```

**The flag selects the allocation strategy.** `FANOPS_VARIANT_UCB` (default **OFF**):
- `FANOPS_VARIANT_LEARNING` off → no hint at all (v2's master gate, unchanged — v3 is inert).
- learning on, `FANOPS_VARIANT_UCB` off → **v2 gated-greedy** (`best_hooks`) — today's behavior,
  the safe rollback target, fully preserved.
- learning on, `FANOPS_VARIANT_UCB` on → **v3 UCB1** (`ucb_rank`) — the new allocation.

`best_hooks` is **not modified or removed** — it stays as the off-path strategy, so every v2
gate/determinism/isolation test stays valid and green, and rollback is a single env flip.

## Units / interfaces (what changes)

- **`variant_learning.py`** (MODIFY — add one function next to `best_hooks`) —
  `ucb_rank(led, cfg, account, platform) -> list[str]`: pure, read-only. Gathers `analyzed` posts
  with a `variant_hook` for the surface, computes `(nₕ, mean_lift)` per hook and `N`, applies the
  UCB1 formula with cold-start/degenerate rules above, returns `[argmax]` (or `[]` for `N==0`).
  No I/O, no mutation, no `random`/`hash`/wall-clock. Shares the same gather predicate as
  `best_hooks` (a private `_collect_lifts(led, account, platform) -> dict[hook, list[float]]`
  helper extracted so both scorers read the ledger identically — DRY, and guarantees v2/v3 see
  the same data).
- **`caption.py`** — `_learned_hooks` ([caption.py:79](src/fanops/caption.py)) selects the scorer:
  `scorer = ucb_rank if cfg.variant_ucb else best_hooks`. Everything else in that function
  (master-gate check, per-surface loop, dedup, fail-open try/except) is unchanged. The import line
  adds `ucb_rank` alongside `best_hooks`.
- **`config.py`** — two new properties mirroring the existing `variant_*` pattern
  ([config.py:136](src/fanops/config.py)):
  - `variant_ucb -> bool` (env `FANOPS_VARIANT_UCB`, default **False**) — select UCB over greedy.
  - `variant_ucb_c -> float` (env `FANOPS_VARIANT_UCB_C`, default **√2 ≈ 1.41421356**) — the
    exploration weight; `try/except ValueError → default`, and clamp `< 0 → default` (a negative
    `c` would invert exploration into anti-exploration — guard it).
- **`digest.py`** — the "Lift by variant" gate-state line ([digest.py:36](src/fanops/digest.py))
  becomes strategy-aware so the operator sees the *active* allocator's verdict: when
  `cfg.variant_ucb` is on, the per-surface line reports the UCB pick (e.g. `UCB → "<hook>"`)
  instead of the binary "learning ACTIVE / gathering data"; when off, today's v2 line is
  byte-identical. Reuses the scorer (one home for the logic), stays fail-open (any error →
  "gathering data").
- **`prompts.py`** — **UNCHANGED.** `caption_prompt` already renders `learned_hooks`; v3 emits the
  same field. No prompt-contract change.

## Testing strategy

The load-bearing tests are (1) UCB *picks the right arm* across explore/exploit regimes, and (2)
**determinism** — a re-run is byte-identical (the content-addressed invariant). Strict TDD,
RED→GREEN→VERIFY, full suite green per task.

- **`ucb_rank` unit** (`tests/test_variant_learning.py`, alongside the v2 tests):
  - **Exploit when settled:** one hook with many posts + clearly higher mean, others well-sampled
    → that hook (mean dominates, bonuses small).
  - **Explore the under-sampled:** a "leader" with a slightly higher mean but *many* posts vs. a
    challenger with a *slightly lower* mean but *few* posts → the **challenger** wins (its
    optimism bonus exceeds the small mean gap). This is the v2-lock-in fix, mechanized — pick `c`,
    means, and counts so the hand-computed UCB scores cross over, and assert the crossover.
  - **Tie determinism:** two arms with identical `(nₕ, mean)` (hence identical scores) → the
    sorted-string-lower hook, every call (no insertion-order dependence).
  - **Degenerate / empty:** `N == 0` (no analyzed variant posts) → `[]`; `N == 1` (one arm, one
    post) → that hook with bonus 0 (bare mean, no `ln 0`); single hook with many posts → that hook;
    old ledger (posts without `variant_hook`, or none `analyzed`) → `[]`, no crash.
  - **`c` does what it should:** `c = 0` → pure greedy (highest mean always wins, no exploration);
    large `c` → exploration dominates (fewest-sampled wins). Two assertions pin the knob's effect.
  - **Determinism (the invariant):** `ucb_rank(led,...) == ucb_rank(led,...)` byte-for-byte on the
    same ledger; AND no `random`/`hash`/`datetime`/`time` import reachable from the function
    (mirror v2's no-nondeterminism guard — a source/AST scan of `variant_learning.py`).
- **`caption._learned_hooks` / `request_captions`** (`tests/test_caption.py`):
  - UCB on + a surface where UCB picks hook X → request payload `guidance`/`learned_hooks` carries
    X. UCB on but `variant_learning` off → no hint (master gate). UCB **off** + learning on →
    **byte-identical to v2** (greedy path; reuse the existing v2 caption test as the oracle).
  - **Fail-open:** monkeypatch `fanops.caption.ucb_rank` to raise → request still written, no hint,
    clip advances (the v2 fail-open test, extended to the UCB scorer).
- **Config** (`tests/test_config.py`): `variant_ucb` defaults False; `variant_ucb_c` defaults √2;
  env overrides both; bad `FANOPS_VARIANT_UCB_C` (`"abc"`, `"-1"`) → default (parse + negative
  guard).
- **Amplify-isolation (C1)** — the v2 invariant **extended to `ucb_rank`**: the existing AST
  data-flow test proving the amplify path (`track.py`/`pipeline.py` → `amplify`) never reaches the
  learner must still pass; add an assertion that `ucb_rank` (like `best_hooks`) is called only from
  `caption.py`/`digest.py`. Mutation-proof it: inject a `ucb_rank(...)` call into `amplify()` →
  the isolation test goes RED.
- **Backward-compat:** with `FANOPS_VARIANT_UCB` unset, the **entire existing 387-test suite stays
  green unchanged** (v3 is fully behind the new flag; greedy is the default allocation).
- **Real integration** (`tests/integration/test_variant_ucb_real.py`, NEW): build a REAL on-disk
  ledger for a surface with two hooks engineered so UCB's pick *differs* from greedy's (greedy
  picks the high-mean well-sampled hook; UCB picks the under-sampled challenger). Set
  `FANOPS_VARIANT_LEARNING=1` + `FANOPS_VARIANT_UCB=1`, run `request_captions`, read the ACTUAL
  request file from `04_agent_io/requests/`, assert its `learned_hooks` carries the **UCB** pick
  (not greedy's). Then re-run identically and assert the request file is byte-identical (the
  determinism invariant, proven end-to-end on disk — the project's Integrate bar).

## Out of scope (v3)

- **Automated propagation into amplify** (`_delete_moment_cascade` / `classify_outcomes`) — still
  the C1-risk path, still deferred. v3 stays strictly on the caption-bias side of the line (this is
  the *separate* "auto-amplify v3" backlog item, NOT this one — naming collision noted: this spec
  is the **bandit** follow-up, file-named `v3-ucb-bandit`; the amplify follow-up is independent).
- **Cross-account / cross-surface transfer** (one surface's signal informing another) — already
  shipped as a *separate* follow-up (`variant_transfer`, commits `8abc8fe`…`82f9c29`) and is
  **out of scope here / untouched**. v3 runs an independent bandit per (account, platform) surface
  over that surface's OWN data; the transfer prior remains a distinct, orthogonal payload key
  (`learned_hooks_transferred`). v3 does not read, modify, or replace transfer.
- **Seeded Thompson sampling** — rejected for v3 (UCB1 gives structural determinism with no RNG to
  seed/audit). Documented here as a considered-and-declined alternative; could revisit only if UCB
  exploration proves materially too weak in practice.
- **Contextual / non-stationary bandits** (decaying old data, time-windowed counts) — v3 treats all
  analyzed posts on a surface as one stationary pool. Recency-weighting is a later refinement.
- **Weighted multi-hook allocation hints** (emit top-K with weights instead of top-1) — v3 keeps
  the single-hint payload shape v2 established (one allocation hint per request). A weighted-set
  payload would change the prompt contract; deferred.
- **Per-platform `c` tuning** — v3 uses one global `c`. Per-surface exploration weights are a later
  refinement if the data warrants it.

## Risks / guardrails

- **Over-exploration starving a real winner.** Too-large `c` keeps biasing toward weak,
  under-sampled hooks. Mitigation: default `c = √2` (the UCB1 literature standard, balanced);
  configurable down to `0` (pure greedy = v2-greedy behavior) for an operator who wants less
  exploration; the `c<0 → default` clamp prevents anti-exploration. The integration + unit tests
  pin the knob's effect so a mis-set `c` is visible.
- **Early-data thrash.** When a surface has few posts, the bias can swing between under-sampled
  arms as each accrues a post and its bonus shifts. Mitigation: arms only exist with `nₕ ≥ 1` (no
  +∞ ping-pong), the deterministic tie-break (sorted hook string) makes ties stable and
  reproducible (not random), and in practice a surface has few distinct hooks. This swinging IS the
  intended exploration while data is thin — it settles as the means separate and bonuses decay.
- **Determinism regression (the cardinal sin).** Any accidental `random`/`hash()`/wall-clock in the
  scorer breaks the content-addressed invariant. Mitigation: the determinism unit test + the
  AST/source no-nondeterminism scan + the end-to-end byte-identical re-run in integration. Three
  independent locks.
- **Amplify-cascade isolation (C1).** Same as v2 — `ucb_rank` touches none of the amplify path;
  the AST data-flow isolation test (extended to `ucb_rank`) mechanizes it; mutation-proven.
- **Fail-open everywhere.** Flag off, any exception, old ledger, degenerate surface → today's
  behavior. A bandit failure can never block a caption, hold a clip, or fail a post. Tested.
- **Reversibility.** The only written artifact is the caption *request* payload. Flip
  `FANOPS_VARIANT_UCB` off → next request reverts to v2-greedy; flip `FANOPS_VARIANT_LEARNING` off
  → no hint at all. Nothing persisted needs unwinding.

## Self-review checklist (pre-plan)

- **Does it kill both v2 weaknesses?** Yes — UCB's bonus lets under-sampled hooks overtake an early
  leader (kills lock-in #1); UCB is never silent once any variant data exists, with no `min_gap`
  floor (kills wasted-early-signal #2). Both are mechanized as the two load-bearing unit tests.
- **Is the determinism real, not asserted?** Yes — the formula uses only integer counts + float
  means already in the ledger; no RNG/hash/clock exists to make it vary; locked by a determinism
  unit test, an AST/source scan, and a byte-identical end-to-end re-run.
- **Could it delete/retire/amplify anything?** No — the only write is the caption *request* payload;
  amplify/C1 path untouched and isolation-tested (mutation-proven).
- **Is it reversible / default-safe?** Yes — default OFF behind `FANOPS_VARIANT_UCB`; off-path is
  v2-greedy (itself default-OFF behind `FANOPS_VARIANT_LEARNING`); rollback is one env flip.
- **Scope:** single implementation plan — one new pure function, one scorer-select line, two config
  props, one digest line, tests. No decomposition needed.
