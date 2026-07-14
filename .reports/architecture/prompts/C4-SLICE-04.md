# C4-SLICE-04 — The reconcile terminal ladder is a pure function of `(state, age)`

**Root cause:** `RC-2` (+ `RC-8`'s reconcile half) · **Severity: HIGH** · **Prerequisite: S03** · **Sequential**
**PR title must carry:** `(Unit: terminal-ladder-state-age-only)`

---

## 0. Before you edit anything

**Reverify every cited line.** Then **state the root cause in your own words.** If your statement mentions the
*status clients*, **you have inherited Cycle 3's mis-attribution — read `C4-COR-04` first.** The two clients are
**symmetric**; both raise on `>= 300`.

---

## 1. What is broken — **three independent exclusions, each fatal alone**

```python
# reconcile.py:620-777
try:
    info = poll(post.submission_id) or {}
except AuthError:  raise
except Exception as exc:
    …stamp "reconcile poll error: …"
    continue                                            # :635  ← ❶ BAILS OUT OF THE LADDER
# ── everything below is reachable ONLY on a SUCCESSFUL poll ──
else:                                                   # :739
    if _is_fake_token(post) and submitting      and age > 24h: → needs_reconcile   # :746  ← ❷
    if _is_fake_token(post) and needs_reconcile and age > 72h: → "GAVE UP:"        # :757  ← ❷
    if post.error_reason: continue                                                  # :767  ← ❸
```

| | Exclusion | Who it strands |
|---|---|---|
| ❶ | **A raising poll `continue`s at `:635`** and never reaches the ladder | **every Zernio 404**, any 5xx, any timeout |
| ❷ | **Both terminals gate on `_is_fake_token`** ([:77](src/fanops/reconcile.py:77)) | **any post with a real backend id — on either backend** |
| ❸ | **Any non-empty `error_reason` suppresses the post forever** | a post carrying a **stale** reason is silent from **pass one** |

**Executed (EXP-C4-1):** a real-token post held `submitting` at **+6 h, +25 h, +73 h, +1 000 h and +100 000 h.**

> ### The load-bearing assumption
> [reconcile.py:76](src/fanops/reconcile.py:76): *"A post carrying a real id is left to its normal poll (**its
> status WILL resolve**), never escalated."*
>
> **That is an assumption stated as a guarantee.** It is false whenever the platform deleted the post, the
> integration was removed, or the backend reports a non-terminal state forever (Postiz's own `QUEUE` maps to
> `"scheduled"`).

**Why it matters:** `reconcile` is the **sole reader** of `submitting` — `publish_due` iterates `queued` **only**
([run.py:442](src/fanops/post/run.py:442)). **If reconcile cannot terminate a post, nothing can.**

---

## 2. 🔴 Two fixes you will be tempted to ship. Both are **forbidden.**

- ❌ **Drop `_is_fake_token` from `:746`/`:757`.** Closes `C3-F1` **on Postiz only.** A raising poll still bails
  at `:635` (❶ survives) and the latch still fires (❸ survives). This is the brief's *"adding another special
  case for a real submission token without defining submission lifecycle ownership."*
- ❌ **Raise `_SUBMITTING_ESCALATE_AFTER` / `_RECONCILE_GIVEUP_AFTER`.** The brief names this exactly:
  *"merely changing a timeout for a structurally unreachable escalation."* **The escalation is not late. It is
  unreachable.**

---

## 3. The root fix — hoist the terminal **out of the poll-outcome branch**

> **The correct predicate for "give up on this post" is `(state, age)`.** Everything else the current code
> conditions on — *this pass's poll outcome*, *the token's provenance*, *whether any string was ever written to a
> free-text field* — is **incidental.**

```python
def _apply_age_terminal(post, now) -> Post | None:
    """PURE function of (state, age). Consults NOT the poll, NOT the token, NOT error_reason."""
    # XC-1: submitting      + age > _SUBMITTING_ESCALATE_AFTER (24h) → needs_reconcile
    # XC-2: needs_reconcile + age > _RECONCILE_GIVEUP_AFTER    (72h) → stamp "GAVE UP:"
```

- **Call it on EVERY reconcilable post, on EVERY pass, BEFORE the `try`/poll** — so a raising poll can no longer
  bypass it.
- The poll then only **ADVANCES** a post (`published` / `failed`). A poll error still stamps a reason and
  continues — **but it can no longer prevent the terminal.**
- 🔴 **Change `:767`'s suppression key** from *"any non-empty `error_reason`"* to the **explicit** terminal marker
  `_is_giveup(post)`. **A post with a *transient* reason must still be VISITED.** *(That latch is what makes the
  strand silent — `RC-8`. It lives inside the branch you are rewriting; **do not split it into another PR.**)*

### 🔴 The safety property that makes this whole slice safe — **pin it with a test**

> **`GAVE UP:` is a LABEL written to `error_reason`. It changes NO state.**
> A given-up post is **not re-queueable**, therefore it **cannot double-post.**

That is why `PD-2`'s recommendation (**the same ladder for a real token**) is safe. **If you find yourself making
`GAVE UP:` a state change, stop — you have broken the property the slice depends on.**

---

## 4. Acceptance criteria

1. 🔴 **THE 32-CELL MATRIX.** `backend {postiz, zernio}` × `poll {published, failed, unknown, RAISES}` ×
   `token {fake, real}` × `error_reason {empty, stale}`. **Every cell must reach a terminal or retryable state by
   +72 h.** *(Today **three of the four axes** can veto the terminal.)*
2. The terminal decision is a **pure function of `(state, age)`.**
3. A **raising** poll (Zernio 404, any 5xx, any timeout) **no longer prevents** the terminal.
4. **`GAVE UP:` remains a label** — no state change, not re-queueable, cannot double-post. **Pinned by a test.**
5. `:767`'s suppression key is the **explicit** `_is_giveup` marker. A post with a **transient** reason is still
   **visited**.
6. The **`gave_up` digest bucket fires on Zernio** for the first time.

## 5. Tests

| Test | Must fail before? |
|---|---|
| **`test_terminal_ladder_matrix`** *(parametrized, 32 cells)* | ✅ *today 3 of 4 axes veto the terminal* |
| `test_reconcile_visits_a_post_carrying_a_transient_reason` | ✅ |
| `test_giveup_is_a_label_not_a_state_change` *(double-post safety)* | ⚪ **pins the safety property** |

---

## 6. 🔴 The operational gate — **the most important note in this audit**

> **On the FIRST pass after deploy, EVERY currently-stranded post becomes eligible for escalation AT ONCE.**
> On the live ledger that is bounded (**347 posts**) — but the operator **must** be warned.

**Ship a report-only mode FIRST** — `fanops reconcile --report-terminals` — so the operator **sees** the blast
radius **before** it is **written**.

**Do not let the first run of the new ladder be the first time anyone learns how many posts it will touch.**

Report the **current count** of posts in `submitting` / `needs_reconcile` on the live ledger (**read-only**) in
the PR.

---

## 7. Enumerate before you edit
Every writer of `PostState.needs_reconcile` · every reader of `error_reason` (**three parsers**:
`transient_daemon_retry_count`, `_is_giveup`, the `unverified:` quarantine gate) · every consumer of the
`gave_up` digest bucket.

## 8. Preserve
- 🔴 **The prime directive: reconcile NEVER guesses a post's fate.** `GAVE UP:` is a **label**, not a transition
  to `failed`.
- `AuthError` → **re-raise** (the halt path, [:622](src/fanops/reconcile.py:622)).
- The `polled_as` stale-poll guard ([:610-612](src/fanops/reconcile.py:610)).
- Per-post containment: one post's poll error must never abort the pass.
- `_STUCK_AFTER` (6 h), `_SUBMITTING_ESCALATE_AFTER` (24 h), `_RECONCILE_GIVEUP_AFTER` (72 h) — **all measured
  from `scheduled_time`, NOT chained.** *(Cycle-3 pinned these; do not "fix" them.)*

## 9. 🔴 Forbidden scope expansion
- ❌ Do **not** raise any timeout.
- ❌ Do **not** make the Studio requeue family accept `submitting` — that hands the operator a button to re-drive
  a post whose **remote fate is unknown.** **A double-post vector.** Terminate (label) first, *then* recover.
- ❌ Do **not** change `post/run.py` (**S03**) or `studio/actions.py` (**S06**).
- ❌ Do **not** split `error_reason` into typed fields — larger change, needs a migration. **Deferred and filed.**

## 10. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge: NO. Verifier: REQUIRED.**
**Product gate:** `PD-2` — the *shape* is decided; the recommendation is the **same age ladder** for a real
token.
**Rollback:** revert — **but note that reverting does NOT un-escalate posts the new ladder already labeled.**
That is acceptable (the labels are **true**) but **must be stated in the PR.**
**State remaining unknowns honestly.**
