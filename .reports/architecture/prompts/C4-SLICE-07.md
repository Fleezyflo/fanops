# C4-SLICE-07 — Reconcile and publish are gated on the same predicate

**Root cause:** `RC-3b` · **Severity: HIGH** · **Prerequisite: S02 (preferred)**
**PR title must carry:** `(Unit: reconcile-publish-gate-parity)`

---

## 0. Before you edit anything
**Reverify the cited lines.** State the root cause in your own words. Then read §4 — **it contains the single
most dangerous scope expansion in the entire Cycle-4 sequence.**

---

## 1. What is broken

```python
# pipeline.py:311-338
def _reconcile_safe(cfg, log):
    if cfg.is_live_backend:          # :318   ← GATED
        reconcile_due(cfg)

def _publish_safe(cfg, log):
    publish_due(cfg, now=None)       # :334   ← NOT GATED
```

**`reconcile` is the SOLE READER of `submitting`** — `publish_due` iterates `queued` only
([run.py:442](src/fanops/post/run.py:442)).

> **So the sole reader of a state can be switched off while the writer keeps producing it.**

When every channel's backend is malformed (or credential-less, or provider-less), `live_ready_channels()` returns
`[]` → `is_live_backend` is `False` → **reconcile never runs while publish still CLAIMS posts into
`submitting`.** The post is stranded **permanently AND unlabeled** — no escalation, no `error_reason`, no
terminal state, no digest bucket.

**A producer and the sole consumer of its output state are gated on different predicates. Either both are gated,
or neither is.**

---

## 2. The fix — Option A (recommended): **remove the gate from `_reconcile_safe`**

**`reconcile_due` is already per-post safe.** Its **own docstring** says so
([pipeline.py:315-316](src/fanops/pipeline.py:315)):

> *"…resolves each post's provider via `effective_provider` and **skips dryrun/provider-less posts**."*

**The gate is redundant defence that became an active hazard.** Removing it restores the invariant with the least
code.

### 🔴 The pre-flight that decides the option

> **Confirm `reconcile_due` makes ZERO network calls on a dryrun-only deployment.**
> **This is an acceptance criterion, not a nicety.** If it does **not**, take **Option B** instead.

**Option B (fallback):** gate `_publish_safe` on `is_live_backend` too. **Strictly safer**, but it also stops
**dryrun previews** from being written on a not-live system ([run.py:458-467](src/fanops/post/run.py:458)) — a
**real feature regression.** Prefer A unless the pre-flight fails.

---

## 3. Acceptance criteria

1. With `is_live == True` and `live_ready_channels() == []`: **either both** `_reconcile_safe` and
   `_publish_safe` run, **or neither** does.
2. 🔴 **`reconcile_due` makes ZERO network calls on a dryrun-only deployment** *(the pre-flight, pinned by a
   test)*.
3. **Dryrun preview writing is not regressed.**

## 4. 🔴 THE FORBIDDEN SCOPE EXPANSION — read this twice

> ## ❌ DO NOT ALSO REMOVE THE `is_live_backend` GATE FROM `_learn_pass` ([cli.py:965](src/fanops/cli.py:965)).

That gate is the **only** thing standing between a not-live-backend deployment and
**`adjust.retire`** ([adjust.py:95](src/fanops/adjust.py:95)) — which writes **`MomentState.retired`**, a state
that **`reconcile_moments` REFUSES to undo** ([ledger.py:636-642](src/fanops/ledger.py:636)).
**It is structurally IRREVERSIBLE.**

**"Unifying the gating" — the most natural-looking cleanup in this diff — would run PERMANENT MOMENT RETIREMENT
on a deployment where learning was previously frozen.**

**This is the `S07` ↔ `S10` semantic conflict. It is the single most dangerous scope expansion in the entire
Cycle-4 sequence. `RC-7` / `PD-3` is UNRESOLVED. Do not touch the learn gate.**

### Also forbidden
- ❌ Do **not** touch `accounts.py` — that is **S02**.
- ❌ Do **not** add another doctor warning. The half-live banner **already exists**
  ([doctor.py:322](src/fanops/doctor.py:322), [views.py:723](src/fanops/studio/views.py:723)) and is
  **suppressed whenever one valid channel remains** (`C3-OBS-5`). **A second warning on the same broken predicate
  adds noise, not safety.**
- ❌ Do **not** touch `reconcile.py` (**S04**) or `post/run.py` (**S03**).

## 5. Tests

| Test | Must fail before? |
|---|---|
| `test_reconcile_and_publish_gate_parity` | ✅ |
| `test_reconcile_makes_no_network_calls_when_not_live` *(the pre-flight, pinned)* | ⚪ |
| `test_learn_pass_is_still_gated_on_is_live_backend` 🔴 **(guards the forbidden expansion)** | ⚪ |

*(That last test is the cheapest possible guard against the `S07`↔`S10` hazard. **Write it.**)*

## 6. Enumerate before you edit
**Every** reader of `cfg.is_live_backend` ([config.py:459](src/fanops/config.py:459)). Expect: `_reconcile_safe`,
`_learn_pass`, and the health/doctor surfaces. **State explicitly which ones this slice changes (exactly one) and
which it must not.**

## 7. Preserve
`AuthError` re-raise symmetry in both `_reconcile_safe` and `_publish_safe` · the swallow-with-log for any other
hiccup (a status-API hiccup must not wedge the pass) · dryrun preview writing.

## 8. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge: NO. Verifier: REQUIRED** — and **the verifier must specifically confirm `_learn_pass`'s gate is
untouched.**
**Rollback:** revert.
**State remaining unknowns honestly.**
