# FanOps — Cycle 6 corrections to Cycles 1–5

**Cycle 6 · 2026-07-14 · git HEAD `fcffa73` (unchanged)**

Authority order applied: **executable code > executed experiment > MEASURED LIVE STATE > AST census > prior JSON >
prior prose > comments.**

Cycle 5 closed with the method note:

> *"An architectural claim about the SHAPE of the codebase is not established by reading the code. It is
> established by a census over the WHOLE tree."*

**Cycle 6 adds the operational twin:**

> **A claim about what a fix will COST is not established by reading the code. It is established by MEASURING
> THE LIVE TREE.** Cycle 4 named **five** read-only checks as merge gates — the stranded-post count, the
> retired-moment count, the malformed-backend check, the render count, the shrink-dir count — and **ran none of
> them.** It then designed the sequence, the gates, and the blockers **around what those numbers might be.**
>
> **Cycle 6 ran all five. Three of them collapse a risk Cycle 4 rated as blocking.**
>
> And a fourth **sharpens the one that matters most** — from "CRITICAL by mechanism" to **"CRITICAL, and it is
> on the procedure this operator demonstrably runs."**

---

## `C6-COR-01` — 🔴 `RC-9` is an **ORPHANED ROOT CAUSE**. Ten roots, ten slices, and the mapping is not a bijection.

**Not previously recorded as a gap.**

Cycle 4 produced ten root causes (`RC-1`…`RC-10`) and ten slices (`S01`…`S10`). It **looks** like a bijection.
It is not:

| | |
|---|---|
| `RC-4` **+** `RC-5` | → **S01** *(two roots collapse into one slice)* |
| **`RC-9`** | 🔴 **→ NOTHING.** |

Cycle 4's own words: *"It does not fix `RC-9` (the mutation/validator boundary). Zero reachability. Filed,
sequenced last."*

**Deferring it was CORRECT and Cycle 6 upholds that judgement.** Fixing `RC-9` means touching 8 models and ~57
`model_copy` sites to close a bug with **zero current reachability** — while `RC-4` silently deletes media. Cycle
4 said so itself: *"fixing it first would spend the audit's credibility on a bug nobody can hit."*

**But a deferral is not a discharge.** And *"we checked, it's unreachable"* is a claim with a **shelf life**: it
was true in Cycle 2, Cycle 3, Cycle 4 and Cycle 5, and **nothing in the repository prevents it from becoming
false in the next PR.** The property survives via **four independent manual call-site guards**. A **fifth door**
added without one **saves cleanly and then bricks the next `Ledger.load`** — taking down the daemon and every
Studio page at once.

🔴 **And three of this program's own slices — `S03`, `S04`, `S06` — all mutate `Post`.**

**Resolution: `S11`, a GUARD slice.** It does **not** fix `RC-9`. It **pins** `RC-9`'s unreachability in CI: an
AST policy test whose baseline is the four known doors, and **a fifth makes CI red.** Test-only, zero blast
radius, self-merges on green.

> **That is the right-sized response to a CRITICAL-if-reached defect with ZERO reachability. It converts an
> inspection result with a shelf life into a mechanical invariant — and it is why the recommended landing order
> puts it SECOND, before the three slices that mutate `Post`.**

---

## `C6-COR-02` — 🔴 The **S02 ↔ S10 conflict** — Cycle 4's *"single most dangerous"* interaction — **is not reachable on this deployment.**

**Superseded claim** ([`change_interference.json`](change_interference.json) `S02 <-> S10`;
[`CHANGE_INTERFERENCE_MATRIX.md`](CHANGE_INTERFERENCE_MATRIX.md) §3;
[`prompts/C4-SLICE-02.md`](prompts/C4-SLICE-02.md) §4; [`prompts/C4-SLICE-10.md`](prompts/C4-SLICE-10.md) §4):

> *"**If S02 normalizes a typo'd backend, a previously-dark channel goes live → `is_live_backend` flips `True` →
> `_learn_pass` starts running — INCLUDING the IRREVERSIBLE `retire()` — on a deployment where it previously did
> not.** Fixing a typo could silently begin permanently retiring moment lineages."*
>
> *"**Mandatory mitigation while PD-3 is unanswered:** S02 **must** log the `is_live_backend` transition loudly…"*

**Cycle 4 asked for the check — *"check it read-only before you ship"* — and never ran it.**

**MEASURED (2026-07-14, live `00_control/accounts.json`):**

| Handle | Platform | Backend |
|---|---|---|
| `markmakmouly` | instagram | `postiz` |
| `perca.late` | instagram | `postiz` |
| `cisumwolfhom` | instagram | `postiz` |
| `backlikeineverleft` | tiktok | `zernio` |
| `hrmny-blog` | tiktok | `zernio` |

> 🔴 **MALFORMED BACKEND VALUES: 0.**
>
> **S02 cannot flip `is_live_backend` from `False` to `True` by normalizing a typo — BECAUSE THERE IS NO TYPO
> TO NORMALIZE.**

**What this does and does not change:**

| | |
|---|---|
| ✅ **`RC-3` is STILL A REAL DEFECT.** | The **load** boundary has no validation, `Accounts.validate()` checks the *pairing* and never the *value*, and hand-editing `accounts.json` is the **documented operator channel** (`AR-09`). It is reachable **by the next hand-edit.** |
| 🔴 **The S02↔S10 HAZARD is NOT reachable today.** | **S02 is DECOUPLED from PD-3 on the current tree.** Cycle 4's mandatory loud-log mitigation is **DISARMED**. |
| ⚠️ **AND IT IS RE-ARMED AS A MERGE GATE.** | This is a property of a **mutable file**. **S02's PR MUST re-run the check and state the result.** If a malformed value has appeared, the Cycle-4 mitigation **re-arms in full.** |

> **A collapsed risk that is not re-verified at merge is a risk that was merely not looked at.**

---

## `C6-COR-03` — 🔴 `S04`'s escalation burst — *"THE MOST IMPORTANT NOTE IN THIS AUDIT"* — has a blast radius of **ZERO**.

**Superseded claim** ([`prompts/C4-SLICE-04.md`](prompts/C4-SLICE-04.md) §6;
[`IMPLEMENTATION_SEQUENCE.md`](IMPLEMENTATION_SEQUENCE.md) §5;
[`implementation_sequence.json`](implementation_sequence.json) `S04.migration`):

> *"🔴 **THE MOST IMPORTANT NOTE IN THIS AUDIT.** On the first pass after deploy, **every currently-stranded post
> becomes eligible for escalation at once.** On the live ledger that is bounded (**347 posts**) — but the
> operator **must** be warned."*

**Cycle 4 counted the ledger's TOTAL posts (347) and inferred the blast radius from it.** That is the wrong
denominator. The ladder only touches posts in `{submitting, submitted, needs_reconcile}`.

**MEASURED (2026-07-14, live ledger, read-only):**

```
posts: 347   —   awaiting_approval: 347
                 submitting:        0
                 submitted:         0
                 needs_reconcile:   0
                 published:         0
                 failed:            0
```

> 🔴 **THE RECONCILABLE SET IS EMPTY. S04's migration is a NO-OP TODAY. Zero rows will be escalated, zero
> labelled.**

**What survives, and it is important:**

> **The report-only mode (`fanops reconcile --report-terminals`) is STILL REQUIRED — but as a PERMANENT GATE,
> not as a one-time warning about today's count.** The moment the operator approves any of those 347
> `awaiting_approval` posts and it publishes, the ladder **acquires a real blast radius**, and the operator must
> be able to **see** it before it is **written**.

**And it inverts the schedule** — see `C6-COR-06`.

---

## `C6-COR-04` — `PD-3` has cost **nothing**. The irreversible actuator has **never fired**.

**Requested and never answered** ([`prompts/C4-SLICE-10.md`](prompts/C4-SLICE-10.md) §5.5):

> *"**Report, read-only, how many moments are ALREADY `retired`** — and whether any were retired on **fewer than
> 8** attributed posts. **That number is the honest measure of what this gate has already cost.**"*

**MEASURED:**

```
moments: 347   —   clipped: 347
                   retired:   0     ← the answer
```

> 🔴 **ZERO. `adjust.retire` has never written `MomentState.retired` on this ledger.**

**`PD-3` remains UNANSWERED and `S10` remains BLOCKED.** The *policy* question is exactly as open as it was. But
it is now **answered on cost**: the gate has suppressed nothing, there is no cleanup burden, and — combined with
`C6-COR-02` — **`S02` cannot unfreeze it either.**

**`PD-3` is no less REQUIRED. It is no longer URGENT.**

---

## `C6-COR-05` — 🔴 `S01` is not merely first by severity. **It is on the procedure this operator actually runs.**

**Extends** [`root_causes.json`](root_causes.json) `RC-4` (*reachability: "an operator following the DOCUMENTED
wipe-rollback procedure while the daemon is running"*).

Cycle 4 rated `RC-4` **CRITICAL** on the strength of the **mechanism** — and it was right to. But *"an operator
**would** follow the documented procedure"* is a **hypothesis about behaviour**, and every prior cycle left it
there.

**MEASURED — `00_control/`:**

```
ledger.snapshot.20260705T103618Z.1d5a6ffe.json     ← a genuine Ledger.snapshot
ledger.json.prewipe-20260629-000912               ← a pre-WIPE backup
ledger.sqlite.pre-pull-20260710T141136Z
ledger.json.pre-mol126-20260707T141155Z
ledger.json.bak.premerge-20260623-002502
ledger.json.bak.reingest-20260628-135516
ledger.json.preframing-bak
ledger.json.preroot-bak
ledger.bak-asd.json  ·  ledger.bak-framing.json  ·  ledger.json.bak.refan
```

> 🔴 **ELEVEN snapshot/backup artifacts, spanning 2026-06-20 → 2026-07-10 — roughly monthly, with a launchd
> daemon installed.** `ledger_wipe.py:246` advertises `restore_snapshot` as **THE** wipe-rollback path.

> **`S01`'s race (`C4-F2a` — silent, unrecoverable deletion of real `.mp4` files) is not an exotic break-glass
> hazard. It sits on the machinery this operator reaches for every few weeks.**

**Nothing changes about the fix. Everything changes about how confidently it goes first.**

---

## `C6-COR-06` — the landing order **inverts**, and measurement is why

**Superseded** ([`IMPLEMENTATION_SEQUENCE.md`](IMPLEMENTATION_SEQUENCE.md) §2): Phase 2 = `S02` + `S07`; Phase 3
= the submission lifecycle (`S03`→`S04`→`S05`→`S06`).

**Cycle 6 reverses them**, on two measured grounds:

1. 🔴 **The window for the lifecycle chain is OPEN NOW and it CLOSES ON FIRST PUBLISH.** `LS-1`: the reconcilable
   set is **empty**. `S04` is a **zero-migration change today.** The moment one of the 347 `awaiting_approval`
   posts is approved and publishes, it stops being one. **Close the submission lifecycle before it is next
   exercised** — the system has already walked it **73 times** (`LS-6`).

2. **`S02` was early partly to de-risk the malformed-backend blast radius.** `LS-3`: **that blast radius does not
   exist.** The urgency that put it in Phase 2 is gone.

**And `S11` moves to SECOND** — free, test-only, and it makes `GB-4` mechanical **before** `S03`/`S04`/`S06`
land, not after.

---

## `C6-SC-1` — 🔴 Cycle 6's own first read of the ledger concluded *"nothing has ever published on this tree."* **FALSE.**

**Self-correction, recorded rather than quietly fixed.**

The ledger shows `published: 0` and `analyzed: 0`. I was one sentence from writing that the publish lifecycle was
**untested in production** — which would have downgraded `RC-1`/`RC-2` from *"defects on a road the system has
driven"* to *"latent hazards on a road it has not."* **That would have been a material mis-ranking.**

**The `06_published/` archive refutes it: 73 records across four day-buckets (2026-06-29, 06-30, 07-04, 07-05).**

**The truth:** the current ledger is **post-wipe** (`ledger.json.prewipe-20260629-000912` is right there, and
project memory records *"shipped history only in `ledger.json.bak`"*). The 347 `awaiting_approval` posts are a
**fresh batch**. **The publish path has been exercised 73 times.**

> **The lesson, and it is the Cycle-5 lesson wearing different clothes:** *the ledger is not the system's memory
> — it is the system's CURRENT memory.* **An entity count of zero can mean "never happened" or "was wiped," and
> those have opposite implications for severity.** Check the artifact that *cannot* be wiped.

---

## `C6-SC-2` — 🔴 The **`cli.py` print-count collision**: a mechanical, cross-slice merge hazard Cycle 4 did not name

**Not previously recorded.**

Cycle 4 told every slice to *"replay both AST ratchets"* — correct, and insufficient. It never **derived what
they cost each slice.**

**MEASURED, from the test files at `fcffa73`:**

| | |
|---|---|
| `tests/test_internal_prints_routed.py` | `_CLI_PRINT_COUNT = 147`, asserted with 🔴 **EXACT EQUALITY** (`assert len(...) == 147`). **Not a ceiling.** |
| `_INTERNAL_MODULES` | includes **`ledger.py`** (S01's file) and **`pipeline.py`** (S07's file) — both must contain **ZERO** `print()`. |
| `tests/test_swallow_ratchet.py` | a **per-file baseline dict** (49 files). A file **not in it** gaining *any* silent broad `except` fails **immediately**. |

**Consequences Cycle 4 could have derived and did not:**

- 🔴 **`S08`, `S09` and `S10` all touch `cli.py`.** If two land carrying a bumped `_CLI_PRINT_COUNT`, **the
  second's rebase yields a wrong constant and CI goes red for a reason unrelated to its change.**
- **`S01` may add ZERO `print()` to `ledger.py`.** **`S07` may add ZERO to `pipeline.py`.**
- **`ledger.py`, `ledger_sqlite.py`, `settings.py`, `post/__init__.py`, `post/metrics.py` and `adjust.py` are
  NOT in the swallow baseline** — so **any** new silent broad `except` in them is an **instant CI failure**, not
  a budget overrun.

**The rule that dissolves the coupling rather than serializing around it:**

> 🔴 **NO SLICE MAY CHANGE `cli.py`'s `print()` COUNT.** Route new operator output through `get_logger`, or reuse
> an existing print. **This is free — none of S08/S09/S10 needs a new print.**

---

## Claims from Cycles 1–5 that Cycle 6 **re-verified and upholds**

Recorded so no later cycle re-litigates them.

| Claim | Cycle-6 status |
|---|---|
| **Cycle 4's 16 load-bearing `file:line` citations** | 🔴 **ALL 16 RE-RESOLVED AT `fcffa73`. ALL CORRECT.** Unlike `CLAUDE.md`'s, which are **10 of 10 stale** (`INV-20`). **The contract may safely inherit them.** |
| `IF-1` / `C4-COR-02` — the `Render` entity is never minted | **UPHELD — and now confirmed against the LIVE TREE, not merely by AST: 0 renders, 0 of 347 posts carry a `render_id`.** |
| `RC-10` / `C3-F4` — the unbounded `fanops-shrink-*` leak | **UPHELD, and MEASURED: 41 dirs; `04_agent_io` = 924 MB.** |
| `AR-04` — 107 lazy edges to an equal-or-higher level, **56 strictly upward** | **UPHELD (Cycle 5). Promoted to `GB-1`, a mandatory boundary on every slice — which no Cycle-4 prompt carried, because the dependency model did not exist when they were written.** |
| `COUP-10` — `providers.py`'s six lazy factories are false-dead-code | **UPHELD. Promoted to `GB-2`.** |
| `SHIM-005` / `INV-19` — forward-compat holds by pydantic's *default* | **UPHELD. Promoted to `GB-3`.** |
| `RC-5` / `AR-03` — the green test that encodes the defect | **UPHELD.** Cycle 6 re-checked **all 18** of Cycle 4's proposed tests for the same inversion. **Only this one is inverted.** |
| `INV-08` — no-auto-publish | **UPHELD — and the live tree is the proof: 347 posts, ALL `awaiting_approval`, nothing published without approval.** |
| Cycle 4's root-cause set (`RC-1`…`RC-10`) and its remediation *designs* | **UPHELD — unchanged. Cycle 6 designed no fixes and discovered no defects.** |
| Schema version 11 | **UPHELD** (read from the live ledger). |

---

## Method notes carried forward to Cycle 7

1. 🔴 **A claim about what a fix will COST is not established by reading the code.** Cycle 4 named five read-only
   merge gates and ran none of them. **Three of the five collapse a risk it rated as blocking.** *Ask of every
   operational gate: has anyone actually counted?*
2. 🔴 **An entity count of zero is ambiguous.** *"Never happened"* and *"was wiped"* look identical in a ledger
   and have **opposite** implications for severity (`C6-SC-1`). **Check the artifact that cannot be wiped.**
3. 🔴 **A deferral is not a discharge.** `RC-9` was correctly deferred and then simply **stopped being tracked**.
   *"We checked, it's unreachable"* has a shelf life; **a CI policy test does not.**
4. 🔴 **The mechanical constraints already in CI are part of the contract, and they must be READ, not just
   NAMED.** Cycle 4 said *"replay both ratchets."* It never noticed one of them is a **shared, exact-equality
   budget across three slices** (`C6-SC-2`).
5. 🔴 **A boundary that FORBIDS is free; a guard that ENFORCES is code.** `GB-1` costs nothing and binds
   everyone. `S12` is code, traces to a **risk** rather than an approved **root cause**, and is therefore
   **surfaced as a decision (PD-5) rather than smuggled in as a fix.**
6. 🔴 **This contract is a document, and this codebase's signature defect is documents that name mechanisms that
   do not exist.** Every boundary here is expressed as a **machine-checkable predicate** — a path, a function
   name, a numeric baseline, an AST assertion — precisely so **Cycle 7 can enforce it and it cannot become the
   next `INV-20`.** *Until Cycle 7 lands, this contract is enforced by attention. That is better than nothing,
   and it is not a mechanism.*
