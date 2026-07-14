# C4-SLICE-10 — Irreversible retirement policy

**Root cause:** `RC-7` · **Severity: MEDIUM**

> # 🔴 BLOCKED ON `PD-3`. **DO NOT EXECUTE THIS PROMPT.**
>
> This slice is **written, not authorized.** It exists so that when the operator answers `PD-3`, the work is
> ready — **not so that an agent can pick it up and start.**
>
> **If you are an implementing agent and `PD-3` has not been answered in writing, STOP HERE.**

---

## 1. The finding (this part is settled)

`_learn_pass` ([cli.py:151-155](src/fanops/cli.py:151)) runs inside a ledger transaction, gated **only** on
`cfg.is_live_backend`:

```python
led = pull_metrics(led, cfg, …)
r   = classify_outcomes(led, per_surface=cfg.adjust_per_surface)
led = amplify(led, cfg, r["winners"])
led = retire(led, r["losers"])            # ← cli.py:155
```

`adjust.retire` ([adjust.py:82-96](src/fanops/adjust.py:82)) writes **`MomentState.retired`**
([adjust.py:95](src/fanops/adjust.py:95)) — and **`reconcile_moments` REFUSES to un-retire**
([ledger.py:636-642](src/fanops/ledger.py:636)). **It is structurally irreversible.**

It fires as soon as `round(n * 0.2) >= 1` — i.e. **n = 3 analyzed posts**
([adjust.py:47](src/fanops/adjust.py:47)).

### The asymmetry

| Actuator | Reversible? | Gate |
|---|---|---|
| `p4_dim_bias` | ✅ | DEFAULT-OFF + `learning_validated` + **`p4_unlocked`** (≥ 8 posts × ≥ 2 values) |
| `variant_amplify` | ✅ | DEFAULT-OFF + `learning_validated` |
| `timing_bias` | ✅ | DEFAULT-OFF + `learning_validated` |
| **`adjust.retire`** | ❌ **IRREVERSIBLE** | 🔴 **`cfg.is_live_backend` ONLY** |

> **Gate strength is inversely correlated with blast radius.** The one actuator that cannot be undone has the
> weakest gate; the three that can be undone have the strongest.

---

## 2. Why this is **blocked**, and not merely unfixed

**Whether this is an intentionally aggressive policy or an oversight CANNOT be recovered from the code**, and the
audit brief **explicitly forbids inferring product intent.**

**And there is real evidence *for* intent:** the guards that exist are considered, not accidental. A loser must be

- in the **bottom 20 %**, **AND**
- below `lift_floor = 20.0`, **AND**
- **not** a winner, **AND**
- **not** `lift_degraded`

([adjust.py:50-52](src/fanops/adjust.py:50)). **Somebody thought about this.** That is exactly why an auditor must
not overrule it. **This is the operator's call.**

---

## 3. `PD-3` — the question that must be answered

> **Is `adjust.retire`'s irreversible `MomentState.retired` at n = 3 analyzed posts an intentionally aggressive
> policy, or an oversight? Should it be gated on `learning_validated` + `p4_unlocked` like every reversible
> actuator?**

### Three legitimate answers. **No recommendation is offered.**

| | Option | What it addresses |
|---|---|---|
| **A** | Gate `retire` behind `p4_unlocked` (`cli.py:155`) | removes the **aggression** |
| **B** | Make retirement **reversible** — add an operator un-retire verb; relax [ledger.py:636-642](src/fanops/ledger.py:636) | removes the **ASYMMETRY**, keeps the policy — *arguably the better framing, since the asymmetry is the actual finding* |
| **C** | Leave the behaviour; **document it** | If intentional, the defect is that **`INV-14`** (*"bias actuators are amplify-only + validation-frozen"*) creates a **FALSE IMPRESSION** by being **true only as scoped** — it silently excludes the one destructive actuator |

---

## 4. 🔴 The interaction that makes `PD-3` **urgent**, even unanswered

> **`S02` ↔ `S10`.** `S02` (backend normalization) changes what feeds `live_ready_channels()` → which feeds
> `cfg.is_live_backend` → **which is `_learn_pass`'s only gate.**
>
> **If `S02` normalizes a typo'd backend, a previously-dark channel goes live → `is_live_backend` flips `True` →
> `_learn_pass` starts running — INCLUDING `retire()` — on a deployment where it previously did not.**
>
> **Fixing a typo could silently begin permanently retiring moment lineages.**

**Mandatory mitigation while `PD-3` is unanswered:** `S02` **must** log the `is_live_backend` transition loudly,
and the operator **must** be told that fixing a malformed backend **can unfreeze the learning pass.**
*(This is written into `C4-SLICE-02`.)*

**Equally:** `S07` **must not** remove the `is_live_backend` gate from `_learn_pass` while "unifying the gating."
*(This is written into `C4-SLICE-07` as a forbidden scope expansion — the single most dangerous one in the
sequence.)*

---

## 5. If and when `PD-3` is answered — what the implementing agent must do

1. **Reverify** every cited line against current source.
2. **Restate** the chosen option and **quote the operator's decision verbatim** in the PR.
3. **Enumerate** every writer and reader of `MomentState.retired`
   ([adjust.py:95](src/fanops/adjust.py:95); [ledger.py:687](src/fanops/ledger.py:687) cascade-survivor;
   [ledger.py:636-642](src/fanops/ledger.py:636) the refusal-to-un-retire; `clip.py`'s render guard, which
   **depends on** the retirement sticking).
4. 🔴 **If Option B (reversible):** trace what `clip.py`'s render guard does when a moment is **un-retired**.
   [adjust.py:90-95](src/fanops/adjust.py:90) exists *precisely because* an un-retired moment **would be
   re-rendered into a fresh live clip on a later pass, silently undoing the retirement.** **An un-retire verb
   must contend with that, or it will resurrect content the operator suppressed.**
5. **Report, read-only, how many moments are ALREADY `retired`** on the live ledger — and whether any were
   retired on **fewer than 8** attributed posts. **That number is the honest measure of what this gate has
   already cost.**
6. **Add adversarial tests.** Run CI (`unit`). **Never run the suite locally.**
7. **Do not merge** without explicit authorization.
8. **State remaining unknowns honestly.**

---

## 6. Forbidden scope expansion (whenever this does run)

- ❌ Do **not** touch the **reversible** actuators (`p4_dim_bias`, `variant_amplify`, `timing_bias`) or their
  gates. **They are correct.**
- ❌ Do **not** touch `classify_outcomes`' guards (bottom-20 % ∧ `lift < 20.0` ∧ not-a-winner ∧ not
  `lift_degraded`). **They are the real, considered guards** — and they are the evidence that this policy was
  deliberate.
- ❌ Do **not** touch `amplify` or `MAX_AMPLIFY_PER_SOURCE`.
- ❌ Do **not** remove `_learn_pass`'s `is_live_backend` gate.
