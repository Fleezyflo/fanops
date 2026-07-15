# FanOps — Architecture Governance Runbook

**Cycle 7.** The operator-facing manual for `tools/arch`, plus the migration plan that retires the
last hand-maintained architectural facts.

This file is **hand-written** and that is deliberate: it is *instructions for a human*, not a claim
about the code. Every file it points at is generated. If you want to know what the architecture
**is**, read [`ARCHITECTURE_GOVERNANCE.md`](ARCHITECTURE_GOVERNANCE.md) — which is generated, and
therefore cannot lie.

---

## 1. The commands

`tools/arch` is **stdlib-only**. No install, no lockfile, no extras.

| Command | What it does | Cost |
|---|---|---|
| `python -m tools.arch regen` | Regenerate every DERIVED artifact into `.reports/architecture/derived/` | seconds |
| `python -m tools.arch check` | Evaluate every executable policy rule | seconds |
| `python -m tools.arch drift` | Regenerate into a temp dir, **byte-compare**, and explain the semantic difference | seconds |
| `python -m tools.arch ci` | The composite gate: drift + policy + registries. **Exit 1 on any BLOCKING finding.** | seconds |
| `python -m tools.arch impact --base <ref>` | The architectural blast radius of a diff | seconds |
| `python -m tools.arch verify --base <ref>` | Which verification classes that diff *requires* | seconds |
| `python -m tools.arch selftest` | The negative controls — proof the validators are not decorative | ~2 min |
| `python -m tools.arch registries` | Validate exceptions + unknowns; report expiries | seconds |
| `python -m tools.arch baseline --accept` | **Deliberately** re-pin the ratchet baselines | seconds |
| `python -m tools.arch docs` | Regenerate `ARCHITECTURE_GOVERNANCE.md` from the artifacts | seconds |

> **No rule count or control count is written in this table on purpose.** Both numbers rotted
> *within the session that wrote them* (the table claimed 20 rules and 21 controls; there were 21
> and 23). A number in prose is a number that drifts, and a drifted number in a governance document
> is this repo's signature defect. The authoritative count of rules is `tools/arch/policy.py::RULES`;
> of controls, `tools/arch/selftest.py::CONTROLS`. `python -m tools.arch check` prints both, and
> `docs/ARCHITECTURE_GOVERNANCE.md` is *generated* from them, so it cannot be stale.

> **Never run `pytest` locally.** Repo policy: the suite is CI-only, because parallel local runs take
> the machine down. Every command above is a plain script and is safe to run locally — that is why
> the negative controls are runnable without pytest at all.

---

## 2. The five situations you will actually hit

### “CI says `derived/` is stale.”

You changed code. The generated architecture no longer matches it.

```bash
python -m tools.arch regen && git add .reports/architecture/derived && git commit
```

Nothing to think about. **If you find yourself hand-editing a file under `derived/`, stop** — the
next regen destroys it, which is the point (`ARCH-006`).

---

### “CI says a module belongs to no subsystem.” (`ARCH-001`)

You added a module. Give it an owner in [`kb/subsystems.json`](../.reports/architecture/kb/subsystems.json),
then `regen`.

This is a **judgement**, and the system deliberately refuses to guess. A module with no subsystem has
no owner, no risk profile, and no reviewer — it is invisible to every claim the KB makes.

---

### 🔴 “CI says I hoisted a lazy import.” (`ARCH-007` — this is the important one)

**Do not ‘fix’ it by re-accepting the baseline.** Read this first.

The compile-time graph is a layered DAG **only because the upward imports are deferred to call time**
(exact counts: `derived/dependencies.json`, never restated here). `config` — a low, most-depended-on
module — reaches **up** to `accounts` and `meta_graph`. Moving one of those imports to module level

- looks like a cleanup,
- would be waved through by any reviewer,
- and **can break the process at start.**

Before Cycle 7, *nothing in this repository enforced that.* No test, no lint rule, no layer
declaration. `ARCH-007` is the first thing that does.

**So: put the import back inside the function body.** If the hoist is genuinely correct, prove it in
a clean interpreter (`python -c 'import fanops.<module>'`), then re-accept the baseline **and say why
in the PR**.

---

### “An exception expired.”

**An expired exception suppresses nothing.** That is not a bug. A suppression with no end date is not
an exception — it is a repeal, and repeals go through review, not through a JSON file.

Either fix the finding, or renew the exception in
[`governance/exceptions.json`](../.reports/architecture/governance/exceptions.json) with a *new*
justification. Renewing is allowed. Renewing silently is not.

---

### “The UNKNOWN ceiling blocks my PR.” (`ARCH-005`)

You added an UNKNOWN. Raising the ceiling in
[`governance/unknowns.json`](../.reports/architecture/governance/unknowns.json) is permitted — but
it is **a statement that the system is less understood than it was**, and it should be hard to do
quietly. Write the rationale.

---

## 3. Scheduled reconciliation

Runs Mondays 05:17 UTC (`.github/workflows/architecture.yml` → `reconcile`).

It regenerates everything, re-runs the policy set, re-validates the registries, and re-runs all 21
negative controls.

> 🔴 **It never commits.** If it finds drift, it **fails with a reviewable diff** in the job summary.
> A bot quietly editing the architecture of record is the opposite of governance.

---

## 4. 🔴 What this system CANNOT do

Stated plainly, so no future agent mistakes silence for coverage.

| Not covered | Why |
|---|---|
| **The live tree** | The Cycle-6 contract's `LS-1`…`LS-7` (0 stranded posts, 0 retired moments, 0 malformed backends, 41 shrink dirs, 73 published records, 11 snapshot artifacts) are properties of the **operator's live ledger**, which is **not in git**. This system cannot re-derive them, cannot detect their decay, and does not pretend to. They are re-armed as **merge gates** (`IR-11`) and **a human must re-run them**. |
| **The runtime call graph (G3)** | Never derived by any cycle. G2 is a strict **superset**. Every SCC claim **bounds** blast radius; it does not **establish** it. |
| **Product decisions** | `PD-1`…`PD-5`. Not recoverable from code. Not guessed. `PD-3` still blocks `S10`; `PD-5` still blocks `S12`. |
| **Intent** | `UNK-C5-1` (is the persona import cycle deliberate?) and `UNK-C5-2` (how many of the 56 upward lazy imports are deliberate?). Cycle 7 **pins** them so they cannot get worse. It does not resolve them. |
| **The dynamic doors** | `IMPL-009` baselines only the **literal** writes to `PostState.published`/`analyzed`. It is **blind** to `PostState(<runtime>)`, `model_copy(update=…)` and `setattr(…)`. That blind spot is **printed on every run** rather than hidden. Closing it needs a `Post` lifecycle state machine, which the contract deliberately defers (§10). |

---

## 5. 🔴 Migration plan — retiring the last hand-maintained architectural facts

The governance system works, but the canonical artifacts still **carry copies of derived numbers**.
A copy is a thing that rots. **Every item below rotted at least once already** — that is not a
prediction, it is the record.

`ARCH-009` and `IMPL-007` now make each of them CI-red instead of silent. That is containment.
**Deletion is the fix.**

### Phase 1 — delete the copies (each is a small, safe PR)

| Artifact | Field(s) to delete | Replace with | Evidence it rots |
|---|---|---|---|
| `kb/dependencies.json` | `totals.*`, `graph.compile_edges`, `graph.runtime_lazy_edges`, `layering.levels` | a pointer to `derived/dependencies.json` | Said 127 modules / 528 edges. **Wrong one commit later.** |
| `kb/subsystems.json` | `totality.*`, `subsystems.*.module_count`, `subsystems.*.compile_depends_on` | a pointer to `derived/modules.json` | Asserted a *total* partition of 127/127 while 2 modules had no owner. |
| `kb/configuration.json` | `env_vars.*.read_at`, `env_vars.*.reader_count` | a pointer to `derived/configuration.json` | Read sites are pure AST facts. |
| `kb/side_effects.json` | `counts_AST_verified.*` | a pointer to `derived/side_effects.json` | Said `mkdtemp_sites: 1`. **It is 2** — see below. |
| `contract/implementation_contract.json` | `GLOBAL_BOUNDARIES.GB-6_ast_ratchet_budgets.*` | a pointer to `derived/ratchets.json` | Pinned the `_CLI_PRINT_COUNT` budget as a *load-bearing, exact-equality* boundary at a value the enforcing test had already moved past. |

**What stays.** All the prose, and it is the most valuable content in the KB: the three-graph
distinction (G1/G1c/G2/G3), the network-ambiguity decision table, the publish side-effect ordering,
the invariants-that-are-FALSE-as-written section, and every UNKNOWN. **None of it is derivable, and
none of it rotted.** *The judgments survived; the facts did not.*

### Phase 2 — make the slice boundaries machine-checkable (`IMPL-002`)

Two boundaries in `contract/file_ownership.json` are **prose**:

- `S08` · `cli.py` — `"the daemon tick loop (:1300-1313)"`
- `S09` · `cli.py` — `"a NEW `cmd_clean` + its argparse registration"`

Neither names a function that exists. **They cannot be enforced by CI as written.** Rewrite each as
a bare function identifier. Until then, `cli.py`'s three-way partition (`S08`/`S09`/`S10`) is
enforced *by attention*.

### Phase 3 — store the ratchet budgets as structured fields, not sentences

This is the root cause of the worst bug Cycle 7 found **in its own code**. `IMPL-007` parsed the
budget out of an English sentence:

```
"`_CLI_PRINT_COUNT = <N>`, asserted with *** EXACT EQUALITY *** …"
```

The number is glued to a backtick, so the first parser extracted **nothing** and the rule **silently
did not fire**. It would never have caught the 147-vs-158 drift *that motivated this entire cycle* —
that was found by hand. **Only a negative control (`NC-15`) revealed it.**

A number that lives in prose will be parsed out of prose, and that parse will one day be wrong.
**Store it as a field.**

---

## 6. 🔴 Open findings this cycle produced (not fixed — governance touches no production code)

### The defect the governance system had in *itself* (fixed — read this before adding a check)

Every derived artifact carried `repository_commit: <git rev-parse --short HEAD>`, defended in a
code comment as *"provenance is the COMMIT, which is deterministic; the clock is not."*

That reasoning is wrong, and it made the entire gate **unsatisfiable**. The byte-compare asks one
question: *does regeneration from this source tree reproduce the committed bytes?* A commit stamp
is **self-invalidating** — committing the artifact moves `HEAD`, so CI regenerates a different SHA,
the compare goes red, and regenerating to fix it produces yet another commit and yet another SHA.
It never converges. **It would have failed every PR, forever, starting with the one that introduced
it** — and no other check caught it, because every other check ran at the same `HEAD` the artifacts
were built at.

Two rules fall out of it, and they are the ones to hold on to:

1. **A generated artifact is a pure function of the SOURCE TREE.** Not the clock, not the machine,
   not the user, not the path — and not git. The tree's identity is the **source fingerprint**, a
   content hash of the inputs the artifacts are actually derived from. That is real provenance.
   Pinned by `tests/test_arch_governance.py::test_generated_artifacts_are_a_pure_function_of_the_source_tree`,
   which regenerates from a copy of `src/` placed **outside any git repository** and demands
   byte-identical output. Anything reaching outside the source tree fails it.
2. **A control can pass for the wrong reason.** `NC-23` reported DETECTED while proving nothing:
   `render.expected(repo: Path = REPO)` bound `REPO` at *import*, so the fixture's path-patching
   never reached it and the control compared the real repo's doc against a fixture-derived
   expectation. Removing the commit stamp made the spurious difference vanish and the control
   flipped to MISSED — which is how it was found. **Default arguments bind once; resolve globals at
   call time** (`= None`, then `x = x or GLOBAL`). Every path default in `tools/arch` now does.

### `AR-11` is now **false as written**

`kb/risks.json` / `RC-10` state that `compress.maybe_shrink_for_cap` is *"the ONLY mkdtemp in
src/fanops (AST-confirmed)"*. **There are two.**

`src/fanops/cli.py:1116` — `tempfile.mkdtemp(prefix="fanops_reframe_")`, added by #634. **No
`rmtree`, no `finally`, no `TemporaryDirectory`.** The scratch dir is created, written into, and
never removed.

- **Severity: LOW**, and materially lower than the original — it is *operator-invoked*
  (`fanops reframe --dry-run`), leaks into the OS temp dir rather than the project's own
  `04_agent_io`, and `--scratch` overrides the location. The original leaks **once per oversize
  upload, forever**, into a persistent project directory (41 dirs / 924 MB, `LS-5`).
- **Why it matters anyway:** `S09`'s scope is *"the **shrink** temp dir gets an owner."* It does
  **not** cover this one. **An engineer who ships `S09` believing "the mkdtemp leak is fixed" would
  be wrong.** Widen `S09`, or file it separately — but do not let the audit's own claim of *"the
  ONLY mkdtemp"* carry that belief.
- Found by `ARCH-008` on the **first run** of this system. Nobody read `cli.py`.

### The swallow ratchet has **unclaimed slack**

`pipeline.py` and `studio/views.py` each declare a baseline of **13** and currently use **12**. A
future change can re-add a silent broad `except` to either **without CI noticing**. Tighten the
baselines to the actual counts.
