---
id: CC-2026-07-20-acceptance-rederivation
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T6: the operator required a contract for this correction"]
supersedes: []
---

# CC-2026-07-20-acceptance-rederivation

### objective

Make acceptance a **verified finding** rather than a self-assertion, and rederive merge
authorization across a squash merge instead of reporting it stale.

**The problem — one root cause, four defects.** *A gate whose only input is the claim it is gating.*

1. **`lifecycle.gates()` set `acceptance = satisfied` on row presence alone.** The `accepted` event
   was the whole of its own evidence.
2. **`lifecycle.state()` returned `accepted` on row presence alone**, first-match-wins, ahead of
   `merged` and ahead of any consultation of merge authorization.
3. **A squash merge made a valid authorization report `stale`.** §4.1a binds authorization to a
   `parent_sha` that must be an ancestor of the head; a squash creates a NEW commit off the old
   `main`, so the authorized parent is not an ancestor of it. Verified live: the same event returns
   `False` against the squash `8311bc94` and `True` against the pre-merge PR head `68f5c2f`.
4. **`gates.acceptance` was read by no rule at all.** It was computed and rendered but consumed by no
   decision, so its wrong value could not have been observed. Correcting the predicate without adding
   a reader would have left it exactly as unobserved.

**A live crash, found while inventorying and fixed here.** `cmd_state` still read
`g.exact_head_approval` after `CC-2026-07-19` renamed that field, so `python -m tools.contract state`
exited 2 on every invocation. It survived because **the verb had no test and no control** — the rename
guard scanned only `decide.py`, and the selftest calls `run()` directly, never the verb wrappers.
`state` is the verb this change alters most, so it is repaired here rather than left broken.

**The solution.**

**Post-merge, ask the right commit.** Rederivation evaluates the existing `merge_approved` event and
the four §4.1a checks against the **final pre-merge PR head**, then requires the platform merge SHA to
be on `main`, any recorded merge SHA to equal it, and the PR-head **tree** to equal the landed tree.
Trees, not commits — a squash is *supposed* to be a different commit and the same content. Every input
existed before the merge, so this **verifies an authorization; it cannot create one**.

**Acceptance is checked against the platform.** Actual merge SHA, `merged` timestamp equal to
`mergedAt`, required check runs bound to that SHA, every required run `success`, and the recorded
check-run ids exactly equal to the verified set. The required set is read from **branch protection** —
never from the row, because a row that names its own bar sets its own bar. `evidence=` stays as human
rationale and is never read as proof.

**Three outcomes, and only one is acceptance.** A completed read that disagrees is a known negative
(`acceptance_claimed` / `merged_unverified`). A read that cannot complete is unavailable: recorded in
`Derived.unverifiable` **before** the derived facts are frozen, so it stops at `ST-7`. `ST-10` sits
*after* `ST-7` in the table so first-match-wins does that disambiguation — its position carries a
distinction its predicate cannot.

**The single-operator guarantee is preserved by shape.** `MergeFactsPort` has no `get(path)`, no
`api()`, no base-URL parameter; three private methods build fixed paths from validated components, and
`MergeFacts` has no field that could carry a review, an approval count or a person. `/reviews` is not
one argument away because no argument reaches path construction. `ST-4` stays deleted.

### success_condition

1. `python -m tools.contract selftest` exits 0 with every control DETECTED, including `NC-C27`.
2. `NC-AC-01` through `NC-AC-11` are registered and DETECTED, covering all eleven required controls;
   `NC-AC-12` covers the new `MERGED-INCOMPLETE` code.
3. An `accepted` row alone never derives state `accepted`, for every non-`satisfied` gate value.
4. A valid authorization rederives across a squash: state `merged`, `merge_authorization` satisfied.
5. A completed read that disagrees yields `acceptance_claimed` or `merged_unverified`, never `ST-7`;
   a read that cannot complete yields `ST-7`, never a negative finding.
6. `gates.acceptance` is read by at least one decision rule (`ST-10`).
7. No `cmd_*` verb reads a `Gates` attribute that is not a field of `Gates`, proven by AST.
8. `lifecycle.gates` has no `reviews`/`principals` parameter; `ST-4` is absent; `MergeFactsPort`
   exposes exactly `pull`, `check_runs`, `required_contexts` and no escape hatch.
9. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
10. The ADR body digest, its front-matter `approved_digest` and `classify.py::ADR_0105_DIGEST` agree.

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED including NC-AC-01..NC-AC-12 | proves each rule FIRES on an injected defect — the only check that can show a self-asserting gate is really gone |
| OB-ORDERING | NC-AC-04 and NC-AC-05 together | proves a completed-but-disagreeing read and an incomplete read reach DIFFERENT outcomes, which no single control can show |
| OB-STRUCTURAL | NC-AC-10 plus the structural absence tests in tests/test_contract_compiler.py | proves the new platform port cannot express a review question, which a behavioural probe cannot show |
| OB-VERB-COVERAGE | the AST guard over every `cmd_*` verb in tests/test_contract_compiler.py | proves the verb wrappers read only fields that exist — the gap that let a crash ship |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set — proves the ARTIFACTS match the source |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — proves the DECLARED controls match the wired ones |
| OB-C18 | the ADR-0105 amendment disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent, not silently |

### rollback

Revert this PR. The change is confined to the contract compiler, its ADR, its tests and one roadmap
row; no runtime `src/fanops/` path, no CI workflow and no repository setting is touched, so a revert
restores the prior model exactly. Reverting reinstates the four defects above, including the
`cmd_state` crash.

### allowed_scope

| glob | why | basis |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | the normative model being amended (§4.1, §4.2, §4.3, §4.3a, §4.4) | declared |
| tools/contract/** | the implementation of that model (ADR-0105 §1 `T3`) | declared |
| tests/test_contract_compiler.py | the tests pinning the corrected model (ADR-0105 §9) | declared |
| tests/fixtures/contracts/valid_full.md | the fixture whose lifecycle carries the changed event schema | declared |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | the Phase 3 status row this change qualifies | declared |
| docs/contracts/CC-2026-07-20-acceptance-rederivation.md | this contract (ADR-0105 §3.6) | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | no CI job added, edited or removed |
| .github/ci-control-registry.yml | no control added, edited or reclassified — these controls ride the `unit` job's collection |
| src/fanops/** | no runtime change; nothing in the application moves |
| .reports/architecture/** | nothing there references the contract compiler; declaring it would be an unauthorized surface in the opposite direction |
| docs/CODEMAPS/** | no codemap describes the contract compiler |
| docs/REPOSITORY_CONSTITUTION.md | cited as authority, never edited |
| docs/ARCHITECTURAL_LAWS.md | cited as authority, never edited |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | a landed contract; editing one is `DECL-DIVERGED` / `LIFECYCLE-REWRITTEN` |
| docs/contracts/CC-2026-07-19-single-operator-authorization.md | a landed contract; its record is append-only and no row of it is touched |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | a landed contract; not this change's record |
| .claude/** | no hook change |
| .githooks/** | no hook change |
| requirements/** | no dependency added — stdlib and `gh` only |

**Repository settings are out of scope by construction, not by declaration.** No collaborator,
permission, branch-protection rule, reviewer or App installation is added or altered; none of those
live in the tree, so no glob can name them. Branch protection is **read** to learn the required
contexts and is never written.

### expected_surfaces

| path | kind | why |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | §4.3a added; §4.1, §4.2, §4.3, §4.4 amended; a retired risk and two stale gate names corrected; body digest recomputed |
| tools/contract/model.py | MODIFIED | `MergeFacts`; `MERGED_VALUES`; `check_runs` added to `ACCEPTANCE_VALUES`; the state names; `MAIN_REF` relocated here |
| tools/contract/lifecycle.py | MODIFIED | `_acceptance`; `_rederive_post_merge`; `MERGED-INCOMPLETE`; the corrected `state()` ladder; `CLAIMED` |
| tools/contract/decide.py | MODIFIED | `ST-10` added after `ST-7`; `MERGED-INCOMPLETE` added to `_LIFECYCLE_FAIL` |
| tools/contract/adapters.py | MODIFIED | `MergeFactsPort` with three closed reads; `RepoPort.tree_of`; path-segment and slug validation |
| tools/contract/__main__.py | MODIFIED | the S5 platform read before `Derived` is frozen; `Ports.merge_facts`; the `cmd_state` crash repaired |
| tools/contract/report.py | MODIFIED | claimed and unknown acceptance disclosed in the rendered report |
| tools/contract/classify.py | MODIFIED | `ADR_0105_DIGEST` re-pinned to the amended body |
| tools/contract/selftest.py | MODIFIED | `FakeMergeFacts`; `FakeRepo.tree_of`; `NC-AC-01`..`NC-AC-12`; `NC-C25` strengthened across the three merged states |
| tests/test_contract_compiler.py | MODIFIED | acceptance and rederivation tests; the rename guard widened to six modules; the AST guard over every `cmd_*` verb |
| tests/fixtures/contracts/valid_full.md | MODIFIED | `merged` and `accepted` rows carry the added values |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | MODIFIED | the Phase 3 row records that acceptance is now verified |
| docs/contracts/CC-2026-07-20-acceptance-rederivation.md | NEW | this contract |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | 65b3e7267117fe41d4b09b3ac9ad421e74673797 |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

**The ADR row names the AMENDED body, `65b3e7267117fe41d4b09b3ac9ad421e74673797`.** The
pre-amendment body was `d971a881f4c7e58ab31f268b3a8d352b884ddec3` — that is the historical fact and
it is not erased: it is the value on `main` at `8311bc94b83fc0ba1b2ec0f1e1e163caee75e362`, and
`git log -p` on this file shows the transition.

**Why the row is bound to the amended blob before any approval.** This change amends the very ADR it
cites, so the blob moves the moment the amendment lands and `AUTH-BLOB-MOVED` / `ST-2` fires. §4.4
makes that a FLAG for re-confirmation rather than an auto-void, and the flag is discharged by
recording what was re-confirmed — a lifecycle append cannot do it, because `AUTH-BLOB-MOVED` compares
this table against the live blob and reads no lifecycle event. Binding first means the operator
approves one final `D` instead of a digest the rebind would immediately invalidate. **This
terminates:** editing this contract moves `D` but does not move the ADR blob.

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| `gates()` set `acceptance = satisfied` on the presence of an `accepted` row alone | read of tools/contract/lifecycle.py:189 before the correction | 8311bc94b83fc0ba1b2ec0f1e1e163caee75e362 | tool:acceptance-rederivation |
| `state()` returned `accepted` on row presence, ahead of `merged` and of merge authorization | read of tools/contract/lifecycle.py:240 before the correction | 8311bc94b83fc0ba1b2ec0f1e1e163caee75e362 | tool:acceptance-rederivation |
| no decision rule read `gates.acceptance`, so a wrong value was unobservable | scan of all 25 rules in tools/contract/decide.py before the correction | 8311bc94b83fc0ba1b2ec0f1e1e163caee75e362 | tool:acceptance-rederivation |
| the same valid authorization binds at PR head 68f5c2f and not at squash 8311bc94 | execution of lifecycle.parent_binds against both commits | 8311bc94b83fc0ba1b2ec0f1e1e163caee75e362 | tool:acceptance-rederivation |
| `cmd_state` read the deleted field `exact_head_approval` and exited 2 on every invocation | execution of python -m tools.contract state before the repair | 8311bc94b83fc0ba1b2ec0f1e1e163caee75e362 | tool:acceptance-rederivation |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-20T00:06:08Z | created | id=CC-2026-07-20-acceptance-rederivation; base_sha=8311bc94b83fc0ba1b2ec0f1e1e163caee75e362; timestamp_source=GitHub API Date response header, observed during this operation |
