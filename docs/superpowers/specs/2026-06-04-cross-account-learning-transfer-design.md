# Cross-Account / Cross-Surface Learning Transfer — Design Spec

**Date:** 2026-06-04 · **Backlog item:** (j) follow-up (the v2 "Out of scope" line:
*"Cross-surface / cross-account learning transfer … is a later optimization"*) · **Status:** design settled, ready for implementation plan
**Builds on:** `2026-06-04-creative-variation-v2-feedback-design.md` (v2, per-surface caption-bias loop, shipped PR #14)
**Depends on:** v2 merged to `main` (verified: commit `5f275fd`, `src/fanops/variant_learning.py` present).

## Problem

v2 (PR #14) closed the A/B loop **per surface, in isolation**: `request_captions`
(`caption.py:79` `_learned_hooks`) asks `variant_learning.best_hooks` only about the surfaces
in the *current* caption request, so a hook style proven on `(@A, instagram)` only ever biases
`(@A, instagram)`'s own future captions. Account B's `(@B, instagram)` surface starts cold and
must independently re-discover what A already learned. Learning does not compound across the
account network — each surface is an island.

This feature lets a hook **style** proven on one surface act as a **weak prior** for *other,
same-platform* surfaces, so a network of accounts compounds its learning instead of every
surface paying the full exploration cost alone — **without collapsing the per-account creative
diversity that variation v1 exists to create.**

It keeps v2's exact safety posture: **OBSERVE-then-BIAS, on the caption-request side only.**
It reads `Post` lift data (already there) and writes one extra string into the caption payload.
It touches **none** of `amplify`/`classify_outcomes`/`_delete_moment_cascade` (the C1 cascade-delete
path v1/v2 correctly avoided); the transfer scorer is imported by `caption.py`/`digest.py` ONLY,
never by `track.py`/`pipeline.py` — enforced by extending the existing isolation grep test.

## The design (settled)

**Transfer a proven hook STYLE as a demoted, same-platform, persona-aware WEAK PRIOR — gated at
least as strictly as v2, plus an additional multi-donor gate so one fluky surface cannot seed
the whole network. Keep each surface's OWN winner ranked above any borrowed signal, and keep
per-account exploration alive.**

The three design questions the task posed, resolved from the brief's stated principles
("transfer STYLE not the verbatim hook", "keep per-account exploration alive", "gate ≥ v2's"):

### Q1 — When is transfer valid?  → **Same platform (hard gate); persona as a soft tiebreaker. Never cross-platform; never a blind cross-account broadcast.**

- **Platform is the hard requirement.** `Platform` is the one *objective* similarity axis in the
  model: it dictates aspect (`PLATFORM_ASPECT`), length ceilings (`PLATFORM_MAX_SECONDS`), and
  caption/hook conventions. A 9:16 TikTok hook style is simply not evidence for a 16:9 YouTube
  caption. So a donor surface must share the recipient's `platform` to contribute. This also
  means the union returned for a multi-surface caption request stays per-platform-correct.
- **Persona is a soft signal, never a gate.** `Account.persona` is a free-text string
  (`@TBD-1`: "fast cinematic edits, hype energy"; `@TBD-2`: "raw studio + lyric-forward"). It is
  too fuzzy to *gate* on (any similarity metric would be arbitrary), but it is a reasonable
  *deterministic ranking nudge*: when more candidate styles pass the gate than we will emit,
  prefer donors whose persona token-overlaps the recipient's. Token-overlap (lowercased word-set
  Jaccard, ties broken by donor-surface count then lexicographically) is fully deterministic — no
  embeddings, no model call, no wall-clock. Persona missing on either side → overlap 0 (it just
  loses the tiebreak; it does not disqualify).
- **Rejected:** cross-platform transfer (weak validity, highest homogenization risk); persona as
  a hard gate (free-text → arbitrary threshold, brittle); same-account-only (too timid — it
  forbids exactly the cross-account compounding this feature is for, and v1's diversity intent is
  protected by the *weak-prior + own-winner-wins* mechanics below, not by walling accounts off).

### Q2 — How to avoid homogenizing all accounts into one voice?

Four mechanics, each load-bearing:

1. **Style, not verbatim.** Transfer emits the winning *hook string(s)* into the SAME
   "lean toward this STYLE (tone, length, angle), do NOT copy verbatim" prompt block v2 already
   renders (`prompts.caption_prompt`). The model is biased toward a style, never handed a script.
2. **Own winner always wins.** A surface that *already has its own* gated winner
   (`best_hooks` non-empty for it) gets **NO transferred prior** — it is learning from its own
   data and does not also get told what a neighbor did. Transfer only fills the *cold-start* gap:
   surfaces with no trustworthy winner of their own yet.
3. **Borrowed signal is demoted and clearly separated.** The transferred styles ride a **separate
   payload key** (`learned_hooks_transferred`), distinct from v2's own-surface key
   (`learned_hooks`). The prompt renders the borrowed block *below* the own block and labels it as
   a weaker, "what's working elsewhere on this platform" hint — so the model (and any reader of
   the request file) can tell a surface's own proven style from a borrowed one, and the own signal
   dominates when both are present. (In practice (2) means they are rarely both present, but the
   separation makes the precedence explicit and testable.)
4. **Exploration stays alive via v2's comparative gate, reused unchanged.** The donor side calls
   `best_hooks`, which already returns `[]` for a lone leader (no runner-up to beat) *precisely to
   avoid collapsing per-account exploration*. Transfer cannot launder a non-comparative winner —
   if a donor surface hasn't run a real A/B with a clear margin, it contributes nothing. We add a
   **cap** (`FANOPS_VARIANT_TRANSFER_MAX_HOOKS`, default 2) on how many borrowed styles a single
   request carries, so even a popular style-cluster cannot flood one caption.

### Q3 — Trust gate (must be ≥ v2's).  → **v2's gate on every donor, PLUS a multi-donor gate on the style.**

- **Per-donor:** every contributing surface must itself pass v2's full gate — `best_hooks` returns
  its winner only at `≥ FANOPS_VARIANT_MIN_POSTS` analyzed posts AND a comparative `≥
  FANOPS_VARIANT_MIN_GAP` margin. (Reused verbatim — transfer never re-implements or loosens it.)
- **Cross-donor (NEW, stricter):** a hook style transfers to a recipient only if it is the gated
  winner on **≥ `FANOPS_VARIANT_TRANSFER_MIN_DONORS` distinct *other* same-platform surfaces**
  (default 2). One surface winning with one style is "that surface's local win", not yet a
  platform-level signal worth pushing onto cold surfaces. This makes the transfer gate a strict
  superset of v2's: a style must clear v2's bar **on multiple surfaces** before it crosses over.
- **Default OFF**, behind its **own** flag `FANOPS_VARIANT_TRANSFER` (independent of both
  `FANOPS_CREATIVE_VARIATION` and `FANOPS_VARIANT_LEARNING`). **Fail-open**: any error building the
  transferred prior → no prior → v2/today behavior. Purely additive.

## Architecture

The read side already exists (lift_score per Post, per variant_hook, per surface — v1/v2). This
feature adds a second pure scorer that surveys *other* surfaces and feeds a clearly-subordinate
prior into the caption request, beside (never merged into) v2's own-surface hint.

**Data flow (new arrows in CAPS; v2's own-surface arrow unchanged):**
```
... publish → track → analyzed   (lift_score per Post, per variant_hook, per surface — UNCHANGED)
  v2 (unchanged):  best_hooks(led,cfg,acct,plat) → this surface's own gated winner → learned_hooks
  NEW (this feature):
  variant_transfer.transferred_hooks(led, cfg, accounts, account, platform):   # pure, read-only
        IF best_hooks(led,cfg,account,platform) non-empty: return []            # own winner wins → no borrow
        for every OTHER active surface S' with S'.platform == platform:         # SAME-PLATFORM only
            w = best_hooks(led, cfg, S'.account, platform)                      # v2 gate on each donor
            if w: tally each winning hook → set of donor surfaces that won it
        keep hooks won on >= TRANSFER_MIN_DONORS distinct donor surfaces        # cross-donor gate (NEW, stricter)
        drop any hook already in THIS surface's own winners (defensive)
        persona-rank the survivors (token-overlap, deterministic tiebreak), cap at MAX_HOOKS
        return that list  (else [])
  request_captions (MODIFIED — needs the Accounts registry to know sibling surfaces):
        learned            = _learned_hooks(led, cfg, surfaces)                 # v2, unchanged
        learned_transferred = _transferred_hooks(led, cfg, accounts, surfaces)  # NEW, fail-open
        payload["learned_hooks"]            = learned             (only if non-empty — unchanged)
        payload["learned_hooks_transferred"] = learned_transferred (only if non-empty — NEW)
  caption_prompt (MODIFIED): render learned_hooks_transferred as a SEPARATE, weaker block
        BELOW the own-surface block, labelled "working elsewhere on this platform"  # demoted prior
```

**Why this is safe (identical argument to v2, one axis wider):** the only state read is
`Post.metrics["lift_score"]` + `variant_*` + `Account.platform`/`persona` (all already present).
The only state written is the caption *request payload* — an agent-input file, not the ledger,
not a unit's state, not the amplify path. A wrong prior at worst nudges one caption generation
toward a style proven on a sibling; it cannot delete, retire, or amplify anything. Fully
reversible: flip `FANOPS_VARIANT_TRANSFER` off and the next request reverts.

## Units / interfaces (what changes)

- **`variant_transfer.py`** (NEW module) — `transferred_hooks(led, cfg, accounts, account,
  platform) -> list[str]`: pure, read-only. Returns `[]` if the recipient has its own gated winner
  (own-wins rule). Otherwise gathers OTHER active **same-platform** surfaces' gated winners (each
  via `variant_learning.best_hooks` — v2 gate reused), keeps hooks that won on `≥
  cfg.variant_transfer_min_donors` distinct donor surfaces, drops any already in the recipient's
  own winners, persona-ranks (deterministic), caps at `cfg.variant_transfer_max_hooks`. No I/O, no
  mutation, no `random`/`hash`/wall-clock. Imports `best_hooks` from `variant_learning` and reads
  the `Accounts` registry. **Mirrors `best_hooks`'s shape so it's trivially testable.** MUST NEVER
  be imported by `track.py`/`pipeline.py` (extends the C1 isolation grep to cover both modules).
- **`caption.py`** — `request_captions` gains an `accounts: Accounts` parameter (the sibling-surface
  source of truth) and a `_transferred_hooks(...)` fail-open helper mirroring `_learned_hooks`.
  Adds `learned_hooks_transferred` to the payload only when non-empty (OFF/empty → byte-identical
  to v2). Fail-open: any exception in the transfer call is swallowed (logged once) → no prior.
  *Caller update (verified):* the sole production caller is `pipeline.advance` at
  `pipeline.py:84`, which already has `accts = Accounts.load(cfg)` in scope (`pipeline.py:39`) and
  already builds its surfaces list from `accts.surfaces()` — so passing `accounts=accts` is a
  one-line change with no new load. All other call sites are tests (`test_caption.py`,
  `tests/integration/test_variant_learning_real.py`), updated as part of the TDD work. To keep
  those and any third-party callers working, `accounts` is an OPTIONAL keyword param defaulting to
  `None`; `None` → transfer simply yields `[]` (no sibling registry → nothing to borrow), so the
  signature change is backward-compatible and transfer is inert without an explicit registry.
- **`config.py`** — `FANOPS_VARIANT_TRANSFER` (default OFF; same on-words contract as the other
  toggles), `FANOPS_VARIANT_TRANSFER_MIN_DONORS` (default 2, non-int → default),
  `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` (default 2, non-int → default). No new MIN_POSTS/MIN_GAP —
  transfer reuses v2's `variant_min_posts`/`variant_min_gap` via `best_hooks`.
- **`prompts.py`** — `caption_prompt` renders a `learned_hooks_transferred` block **below** the v2
  `learned_hooks` block, labelled as a weaker, cross-surface "what's working elsewhere on this
  platform — a lighter nudge than your own proven style above; do NOT copy verbatim" hint. Absent
  → no block (prompt byte-identical to v2).
- **`digest.py`** — (cheap, optional) the "Lift by variant" section notes when a surface is
  *receiving* a transferred prior ("borrowing platform signal") vs has its own ("learning ACTIVE")
  vs neither ("gathering data"). Reuses both scorers so gate logic stays in one place. Fail-open to
  the safe label. To avoid threading `accounts` through `write_digest`'s 7 call sites (several CLI
  verbs have no registry in scope), `write_digest` *self-loads* `Accounts.load(cfg)` (only when
  transfer is on; fail-open to `None` → no label) and passes it to `render_digest`; the explicit
  `render_digest(..., accounts=...)` param stays for unit tests and registry-bearing callers.

## Testing strategy (strict TDD — each task ends full-suite green)

- **`variant_transfer.transferred_hooks` (the load-bearing unit):**
  (a) recipient has own gated winner → `[]` (own-wins rule);
  (b) one donor surface wins a style but `< TRANSFER_MIN_DONORS` distinct donors → `[]` (multi-donor gate);
  (c) a style is the gated winner on `≥ TRANSFER_MIN_DONORS` distinct same-platform donors → that style returned;
  (d) a donor that would win on a DIFFERENT platform does NOT contribute to this platform's recipient (same-platform hard gate);
  (e) a donor below v2's own gate (too few posts / no comparative margin) contributes nothing (gate reuse);
  (f) persona ranking: with more survivors than `MAX_HOOKS`, the persona-closer donors are kept, deterministically; missing persona → overlap 0, never a crash;
  (g) cap: never returns more than `MAX_HOOKS`;
  (h) determinism: same ledger+accounts → identical list (ordering stable);
  (i) empty/old ledger (no variant posts) → `[]`, no crash.
- **`request_captions`:** transfer ON + a recipient surface that qualifies → payload carries
  `learned_hooks_transferred` with the borrowed style AND (if the surface also had its own winner,
  which by the own-wins rule it must not) — assert own-wins precedence holds. Transfer OFF, OR no
  qualifying donor → payload byte-identical to v2 (no `learned_hooks_transferred` key). Fail-open:
  a raising `transferred_hooks` → request still written, no prior, clip still advances.
- **`caption_prompt`:** `learned_hooks_transferred` present → rendered as a separate block BELOW
  the own-surface block, with the "do NOT copy verbatim / lighter nudge" instruction; absent →
  prompt byte-identical to v2. Both keys present → own block appears above transferred block.
- **Adversarial / amplify-isolation (C1):** extend the existing grep-isolation test so it asserts
  `variant_transfer` (like `variant_learning`) is imported by `caption.py`/`digest.py` ONLY — never
  by `track.py`/`pipeline.py`. The amplify/delete-cascade path stays blind to the transfer learner.
- **Anti-homogenization regression:** an explicit test that a surface WITH its own gated winner
  receives NO transferred prior (mechanic 2), and that transfer never emits a style the recipient
  already won (defensive dedupe) — locking the "don't collapse into one voice" guarantee.
- **Backward-compat:** transfer OFF leaves the full suite green; v2 ON + transfer OFF behaves
  exactly as PR #14; an old ledger (posts without `variant_key`) → both scorers return `[]`.
- **Real integration:** seed a ledger where a hook style clearly out-lifts (comparative, ≥ MIN_POSTS)
  on TWO distinct same-platform donor surfaces, and a THIRD same-platform recipient surface has no
  own winner; run real `request_captions` with transfer ON; assert the on-disk request file carries
  that style in `learned_hooks_transferred` and NOT in `learned_hooks` — the cross-surface loop
  closing, proven end-to-end. A companion case with `TRANSFER_MIN_DONORS=3` and only 2 donors
  asserts NO transfer (the stricter gate, proven on real disk).

## Out of scope (this feature)

- **Automated propagation into amplify** (`_delete_moment_cascade` / `classify_outcomes`) — still
  the C1-risk path, still deferred (unchanged from v1/v2). This feature stays on the caption-bias side.
- **Cross-PLATFORM transfer** (a TikTok winner informing a YouTube caption) — deliberately excluded;
  platform is the hard validity gate. A future feature could add gated, weighted cross-platform
  transfer if same-platform proves valuable and safe first.
- **Embedding/semantic persona similarity** — persona ranking is deterministic token-overlap only
  (no model call, no embeddings) to preserve the no-random/no-wall-clock determinism invariant.
- **Multi-armed-bandit / decayed / recency-weighted transfer** — this is a simple gated weak prior;
  smarter allocation (exploration-exploitation scheduling, time-decay) is a later follow-up if it
  proves worth it (already noted in v2's out-of-scope as the bandit follow-up).
- **Learning the caption BODY style** (not just the hook) — scoped to the hook axis, as v1/v2 were.

## Risks / guardrails

- **Homogenization (the core risk):** mitigated by (1) style-not-verbatim, (2) own-winner-wins
  (cold surfaces only), (3) demoted/separated borrowed signal, (4) v2's comparative gate reused +
  the MAX_HOOKS cap. The anti-homogenization regression test locks (2) and the dedupe in.
- **Weak/fluky donor signal:** the multi-donor gate (`TRANSFER_MIN_DONORS`, default 2) makes the
  transfer gate a strict superset of v2's — a style must clear v2's bar on multiple surfaces before
  it crosses over. One lucky surface cannot seed the network.
- **Amplify-cascade isolation (C1):** this feature touches NONE of `amplify`/`classify_outcomes`/
  `_delete_moment_cascade`. Enforced by the extended grep-isolation test. The loop closes on the
  agent-request side only — one axis wider than v2, same wall against the dangerous path.
- **Fail-open everywhere:** transfer off, no qualifying donor, any exception, old ledger → v2/today
  behavior. A transfer failure can never block a caption, hold a clip, or fail a post.
- **Determinism:** `transferred_hooks` is a pure function of ledger + accounts state — no `random`,
  no `hash()`, no wall-clock (persona ranking is deterministic token-overlap with a stable
  tiebreak) — so a re-run yields the identical prior (consistent with the content-addressed invariant).
- **Reversibility:** the only written artifact is the caption *request* payload. Flip
  `FANOPS_VARIANT_TRANSFER` off and the very next request reverts. Nothing persisted needs unwinding.
