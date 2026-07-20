---
id: CC-2026-07-20-phase3-closeout
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T3: this change edits tools/contract/** and docs/governance/**"]
supersedes: []
---

# CC-2026-07-20-phase3-closeout

### objective

Close Phase 3 truthfully: fix the default-base defect that made every landed contract unreadable,
stop two prose counts from rotting further, disclose the phase's own incomplete lifecycle records
without repairing them, and move the roadmap to the status the evidence supports.

**One root cause runs through the first two.** *A claim with no reader.* A default value nobody
re-examined, and two sentences describing code that nothing compared against the code.

1. **`--base` defaulted to `origin/main`.** Correct while a change is in flight; wrong the instant
   it lands, because the ref then IS the head. The diff came back empty, no trait derived, and the
   verifier reported the declaration as differing from a derived set it never computed — `CL-2`,
   on every landed contract, about a default rather than about the contract. **An empty diff is not
   evidence that nothing changed.** It is the same vacuous zero this tool refuses everywhere else,
   arriving through a flag default instead of a failed read.

2. **Two prose counts said "two" where the code had four.** `NC-AC-10`'s own success message
   printed four reads while calling them two, and `tests/test_contract_compiler.py` described "the
   two closed endpoints". Neither was read by anything. The same defect shipped in
   `CC-2026-07-20-acceptance-rederivation` `success_condition` 8, which is frozen and stays
   disclosed in its own `accepted` row rather than edited.

3. **Three of Phase 3's four contracts carry incomplete lifecycles**, and defect 1 hid it: every one
   answered `CL-2` regardless of state. With the base fixed they derive `merged_unauthorized`,
   `acceptance_claimed` and `merged`. They are **disclosed, not backfilled** — and, by operator
   disposition recorded 2026-07-20, they are **disposed rather than left open**: all three stand
   unrepaired, permanently. G1 remains an unratified disclosed unauthorized merge, G2 an unratified
   `acceptance_claimed` historical violation, G3 a disclosed post-merge omission. *Disposed* means
   the question has been answered and the answer is *nothing* — not that anything was fixed.

### success_condition

Each is machine-checkable against the landed tree. None restates a count that code already owns.

1. `python -m tools.contract verify docs/contracts/CC-2026-07-20-acceptance-rederivation.md` with
   **no** `--base` returns `continue` / `OK` / state `accepted` / 0 diagnostics at every phase —
   the same verdict the explicit base returns. Before this change the same command returned `CL-2`.
2. An explicit `--base` is still honoured verbatim, unchanged in behaviour.
3. `NC-AC-35` is **DETECTED**, and goes **MISSED** when either half of the defect is reinstated —
   the CLI default reverted to a ref, or `run()` made to ignore `created.base_sha`. Both directions
   were run.
4. `python -m tools.contract selftest` reports every control DETECTED and exits 0.
5. No source file states a count of `MergeFactsPort`'s reads in prose. Where the size of that set
   matters it is derived from the port, which is what `NC-AC-10` already does.
6. `docs/governance/PHASE3_LIFECYCLE_DISCLOSURE.md` states, for each Phase 3 contract, the state
   derived by `python -m tools.contract state` — not a state transcribed from intent — and records
   a **disposition** for each of G1, G2 and G3.
7. The lifecycle rows of `CC-2026-07-18-change-contract-compiler`,
   `CC-2026-07-19-cli-lifecycle-integrity` and `CC-2026-07-19-single-operator-authorization` are
   **byte-identical** to their values at `ddbf696baf69189212e11a9004aa2cf05762b047`. Nothing is
   backfilled, ratified or rewritten — and under the disposition, nothing later will be.
8. The roadmap's Phase 3 row reads `ACCEPTED`, its outcome section records **R9 as DISPOSED** with
   a per-gap disposition and links the disclosure, and the next gate names Phase 4 while Phase 4's
   status row still reads `NOT STARTED`.
9. Neither document lets `ACCEPTED` be read as ratifying G1 or G2. Both state the independence
   explicitly, because a phase status and an authorization are different claims and the danger of
   this change is that a reader collapses them.
10. `python -m tools.arch ci` and `python -m tools.ci static` both exit 0.

### rollback

Revert this PR. The change touches the contract tooling, its selftest, one test comment and two
governance documents; no `src/fanops/` runtime path, no CI workflow, no registry entry and no
repository setting moves. Reverting reinstates the `CL-2` default-base defect and the two stale
counts, and removes the disclosure — it does not re-authorize anything, because this change
authorizes nothing.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | a4538c284a14536de4c00547bee8e49086b79fd0 |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

**ADR-0105 is cited and NOT amended.** Its blob is the value already on `main`, so no
`AUTH-BLOB-MOVED` arises and no renewed ADR approval is required. This change alters no normative
rule: it repairs an implementation defect, corrects two descriptions, and records facts.

### coupling

| what | must_move_with | why |
|---|---|---|
| the `--base` argparse default | `run()`'s S4a base resolution | either alone leaves the defect reachable — a ref default with correct resolution still ships the old behaviour to every CLI caller, and `None` with no resolution passes `None` into `diff_names`. `NC-AC-35` guards both halves for this reason |
| `run()`'s `base` parameter type | every `run(...)` call site and `_run` in the selftest | `base` became optional; a caller still typed to a required `str` would pass a ref and silently reacquire the default |
| the size of `MergeFactsPort`'s surface | `NC-AC-10` alone | the count must live in exactly one executing place. Any prose copy is a second source that cannot be kept in step, which is how it went stale the first time |
| the derived states in the disclosure | `python -m tools.contract state` output | the document's value is that it is re-derivable; a transcribed state would rot exactly like the counts it reports on |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| tools/contract/__main__.py | the base-anchor defect and its CLI default (ADR-0105 §1 `T3`) | declared |
| tools/contract/selftest.py | `NC-AC-35`, and `NC-AC-10`'s stale success message | declared |
| tests/test_contract_compiler.py | the stale prose count in the hermetic-platform comment (ADR-0105 §9) | declared |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | the Phase 3 status row, its outcome and the next gate | declared |
| docs/governance/PHASE3_LIFECYCLE_DISCLOSURE.md | the residual **R9** disclosure | declared |
| docs/contracts/CC-2026-07-20-phase3-closeout.md | this contract (ADR-0105 §3.6) | declared |

### prohibited_scope

| glob | why |
|---|---|
| docs/adr/** | no normative rule changes; ADR-0105 is cited, never edited |
| .github/workflows/** | no CI job added, edited or removed |
| .github/ci-control-registry.yml | `NC-AC-35` rides the existing selftest collection; no control is registered, reclassified or required |
| src/fanops/** | no runtime change |
| docs/REPOSITORY_CONSTITUTION.md | cited as authority, never edited |
| docs/ARCHITECTURAL_LAWS.md | cited as authority, never edited |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | a landed contract, and one this change reports on — editing it is `DECL-DIVERGED` / `LIFECYCLE-REWRITTEN`, and would be the backfill this change exists to refuse |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | as above |
| docs/contracts/CC-2026-07-19-single-operator-authorization.md | as above |
| docs/contracts/CC-2026-07-20-acceptance-rederivation.md | frozen under its own `D`; `success_condition` 8 stays as shipped, disclosed in its `accepted` row |
| .claude/** · .githooks/** | no hook change |

**Repository settings are out of scope by construction.** No collaborator, permission,
branch-protection rule, reviewer or App installation is added or altered; none of them live in the
tree, so no glob can name them.

### expected_surfaces

| path | kind | why |
|---|---|---|
| tools/contract/__main__.py | MODIFIED | S4a resolves the base from the contract's own `created.base_sha` when none is given; unresolvable is `unverifiable`, never a silent fallback; `--base` defaults to `None`; `run()`'s `base` becomes optional |
| tools/contract/selftest.py | MODIFIED | `NC-AC-35` added and registered; `NC-AC-10`'s success message derives its count instead of stating it |
| tests/test_contract_compiler.py | MODIFIED | the hermetic-platform comment stops naming a count and points at the control that derives one |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | MODIFIED | Phase 3 → `ACCEPTED`; a Phase 3 outcome section recording **R9 as DISPOSED** with a per-gap disposition; the next gate becomes Phase 4 while Phase 4 stays `NOT STARTED`; the disclosure added to evidence links |
| docs/governance/PHASE3_LIFECYCLE_DISCLOSURE.md | NEW | the **R9** disclosure — three incomplete lifecycles stated as derived, each with a final disposition leaving it unrepaired, and no repair performed |
| docs/contracts/CC-2026-07-20-phase3-closeout.md | NEW | this contract |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| the no-`--base` invocation returned `CL-2` on every landed contract | execution of python -m tools.contract verify at all three phases before the fix | ddbf696baf69189212e11a9004aa2cf05762b047 | tool:phase3-closeout |
| `NC-AC-10` printed four reads while calling them two | read of tools/contract/selftest.py:1501 before the fix | ddbf696baf69189212e11a9004aa2cf05762b047 | tool:phase3-closeout |
| three of four Phase 3 contracts derive an incomplete lifecycle state | execution of python -m tools.contract state on each | ddbf696baf69189212e11a9004aa2cf05762b047 | tool:phase3-closeout |
| `NC-AC-10` already guarded `MergeFactsPort`'s surface, so the erratum's cause was the unread prose and not a missing control | read of the surface assertion in tools/contract/selftest.py | ddbf696baf69189212e11a9004aa2cf05762b047 | tool:phase3-closeout |

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED, including `NC-AC-35` | proves each rule FIRES on an injected defect. `NC-AC-35` was additionally run with each half of its defect reinstated and observed MISSED both times — a control proven only in the green direction has not been proven |
| OB-BASE-BEHAVIOUR | python -m tools.contract verify on a LANDED contract with no `--base` | the only check that exercises the defect's real condition. The selftest drives fakes, so it can show the resolution happens but never that a landed contract now reads correctly |
| OB-UNIT-CI | the required context `unit (fast, no toolchain)` concluding success on the exact head | the ONLY evidence the pytest suite passes — a DISTINCT boundary from OB-NEG-CONTROL, sharing neither fixtures nor ports |
| OB-E2E-CI | the required context `real-tooling E2E (must run, not skip)` concluding success on the exact head | exercises the real toolchain rather than a fake |
| OB-NO-BACKFILL | byte-comparison of the three prior contracts' lifecycle sections against `ddbf696b` | proves the disclosure disclosed and did not repair — the one obligation whose failure would invert this change's purpose |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set — proves the ARTIFACTS match the source |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — proves the DECLARED controls match the wired ones |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-20T18:28:03Z | created | id=CC-2026-07-20-phase3-closeout; base_sha=ddbf696baf69189212e11a9004aa2cf05762b047; timestamp_source=GitHub API Date response header, observed during this operation |
