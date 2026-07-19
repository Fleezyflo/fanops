---
id: CC-2026-07-19-single-operator-authorization
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T6: the operator required a contract for this correction"]
supersedes: []
---

# CC-2026-07-19-single-operator-authorization

### objective

Remove the second-person-centered merge-authorization system from the active repository and replace
it with a single-operator model.

This repository has exactly **one** human operator. The shipped model required a non-author GitHub
`APPROVED` review to authorize a merge (`ST-4`). That reviewer does not exist and must never be
required, so the gate could be waited on indefinitely and never cleared. ADR-0105 §4.1a already named
this outcome in its own text — a governance system that can never authorize a merge in the repository
it governs *"does not fail safe, it fails **inoperative**, and inoperative controls are removed
wholesale rather than satisfied."* This contract applies that sentence to the rule itself.

**The problem.**

Three distinct defects, one root cause — **an authorization predicate that names a principal the
repository does not have**.

1. **`ST-4` is unsatisfiable here.** Its predicate reads `gates.exact_head_approval`, which was
   computed primarily from PR reviews. The sole account is also the author of every PR and GitHub
   refuses self-approval, so no evidence admissible to that rule can ever exist.
2. **The in-file fallback was gated on a census the platform will not return.** The "unwitnessed"
   route required proof that exactly one principal could push. App-installation endpoints answer 401
   or 403 to every available credential, so the fallback was frequently `unknown`, which is not
   satisfied — the fallback inherited the same unreachability it existed to cure.
3. **A review outage could change a governance verdict.** `read_reviews` returning `None` set the
   gate to `unknown`. Availability of an irrelevant third-party API decided whether a merge was
   authorized.

**The solution.**

**One route.** Merge authorization is an operator-issued `merge_approved` lifecycle event carrying
`parent_sha`, `digest`, `pr`, `operator` and `token`, satisfying the four `parent_binds` checks.

**`ST-4` is deleted** — not renamed, aliased or renumbered. `ST-9` is a *different question*: has the
sole authority authorized THIS parent, for THIS contract, on THIS PR. The operator can answer it.

**No review or principal read survives in the authorization path.** `gates()` has no parameter to
pass them through; `read_reviews`, `read_principals` and `ReviewPort` are deleted rather than left
unused. The absence is structural — a dormant adapter is an invitation to wire it back in.

**What does not relax.** Every binding check still bites and each is proven from git or the
declaration rather than taken at its word: wrong digest, wrong PR, non-ancestor parent, a
non-contract path moved after the parent, a declaration edit, a rewritten lifecycle. A
lifecycle-only append still binds, because the §3 byte split proves that delta inert. **The agent may
transcribe an operator token; it may never author one.**

### success_condition

1. `python -m tools.contract selftest` exits 0 with every control DETECTED, including `NC-C27`.
2. `NC-SO-01` through `NC-SO-11` are registered and DETECTED, covering all fourteen required
   controls.
3. `ST-4` appears in no rule table, predicate, model constant, verifier path, report string or
   fixture; `decide.RULES` contains no `ST-4` and no duplicate ids.
4. `lifecycle.gates` has no `reviews` or `principals` parameter; `read_reviews`, `read_principals`,
   `WITNESSED`, `UNWITNESSED` and `ReviewPort` do not exist.
5. No executable line in `lifecycle.py`, `decide.py`, `adapters.py`, `__main__.py`, `report.py` or
   `model.py` contains a review, reviewer-identity or principal-census read.
6. A contract with **zero** PR reviews and a valid operator authorization reaches
   `merge_authorization == satisfied`.
7. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
8. The ADR body digest, its front-matter `approved_digest` and `classify.py::ADR_0105_DIGEST` agree.

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED including NC-SO-01..NC-SO-11 | proves each rule FIRES on an injected defect — the only check that can show a deleted route is really gone |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set — proves the ARTIFACTS match the source |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — proves the DECLARED controls match the wired ones |
| OB-C18 | the ADR-0105 correction disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent, not silently |
| OB-REVERIFY | the structural absence tests in tests/test_contract_compiler.py | proves no review or principal read survives ANYWHERE in the authorization path, which a behavioural probe cannot show |

### rollback

Revert this PR. The change is confined to the contract compiler, its ADR and its tests; no runtime
`src/fanops/` path, no CI workflow and no repository setting is touched, so a revert restores the
prior model exactly.

### allowed_scope

| glob | why | basis |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | the normative model being corrected (ADR-0105 §4.1a) | declared |
| tools/contract/** | the implementation of that model (ADR-0105 §1 `T3`) | declared |
| tests/test_contract_compiler.py | the tests pinning the deleted model (ADR-0105 §9) | declared |
| docs/contracts/CC-2026-07-19-single-operator-authorization.md | this contract (ADR-0105 §3.6) | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | no CI job added, edited or removed |
| .github/ci-control-registry.yml | no control added, edited or reclassified |
| src/fanops/** | no runtime change; nothing in the application moves |
| .claude/** | no hook change |
| .cursor/** | no hook change |
| .githooks/** | no hook change |
| .orchestration/** | not this change's decision |
| docs/governance/** | no roadmap or governance-record edit |
| requirements/** | no dependency added — stdlib only |

**Repository settings are out of scope by construction, not by declaration.** No collaborator,
permission, branch-protection rule, reviewer or App installation is added or altered by this change;
none of those live in the tree, so no glob can name them and none is touched.

### expected_surfaces

| path | kind | why |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | §4.1a rewritten to one route; §4.2 and §4.3 rows and §Risks corrected; body digest recomputed |
| tools/contract/lifecycle.py | MODIFIED | single-route `gates()`; `_merge_authorization`; `read_reviews`/`read_principals`/`WITNESSED`/`UNWITNESSED` deleted |
| tools/contract/decide.py | MODIFIED | `ST-4` deleted; `ST-9` added |
| tools/contract/model.py | MODIFIED | `Gates.exact_head_approval` → `merge_authorization`; `exact_head_evidence` deleted |
| tools/contract/adapters.py | MODIFIED | `ReviewPort` and its slug helper deleted |
| tools/contract/__main__.py | MODIFIED | review and principal reads removed from the execution path; `Ports.reviews` deleted |
| tools/contract/report.py | MODIFIED | gate payload and disclosure line follow the single route |
| tools/contract/selftest.py | MODIFIED | `FakeReviews` deleted; `NC-SO-01`..`NC-SO-11` added |
| tools/contract/classify.py | MODIFIED | `ADR_0105_DIGEST` re-pinned to the corrected body |
| tests/test_contract_compiler.py | MODIFIED | two-route tests replaced with single-operator and structural-absence tests |
| docs/contracts/CC-2026-07-19-single-operator-authorization.md | NEW | this contract |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | d971a881f4c7e58ab31f268b3a8d352b884ddec3 |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

**The ADR row names the CORRECTED body, `d971a881f4c7e58ab31f268b3a8d352b884ddec3`.** The contract
originally cited `f9fa602b501f80418d8a66eb9c6389a99ae64c8a`, the **pre-correction** body this change
acts upon — that is the historical fact, and it is not erased: it is the value in every commit up to
and including `2722dc0a7e7d7af5d794b544b808c1f70c180263`, `git log -p` on this file shows the
transition, and each `reusable_evidence` row below is still bound to the pre-correction tree at
`35cbf7fcebdd9e2b5f657a971af6c31140879123`.

**Why the row is rebound before any approval, not after.** This change amends the very ADR it cites,
so the blob moves the moment the correction lands and `AUTH-BLOB-MOVED` / `ST-2` fires. §4.4 makes
that a FLAG for re-confirmation rather than an auto-void, and the flag is discharged by recording
what was re-confirmed — a lifecycle append cannot do it, because `AUTH-BLOB-MOVED` compares this
table against the live blob and reads no lifecycle event. Rebinding first means the operator approves
one final `D` instead of approving a digest that the rebind would immediately invalidate. **This
terminates:** editing this contract moves `D` but does not move the ADR blob, so the row it now names
stays correct and no further round is generated.

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| `ST-4`'s predicate read `gates.exact_head_approval`, which was computed from PR reviews | read of tools/contract/decide.py:136 and lifecycle.py:177 before the correction | 35cbf7fcebdd9e2b5f657a971af6c31140879123 | tool:single-operator-correction |
| the in-file route required `len(principals) == 1` and treated an unreadable census as `unknown` | read of tools/contract/lifecycle.py:190-197 before the correction | 35cbf7fcebdd9e2b5f657a971af6c31140879123 | tool:single-operator-correction |
| `write_principals()` read only `/collaborators`, so App installations were never enumerated | read of tools/contract/adapters.py:225-236 before the correction | 35cbf7fcebdd9e2b5f657a971af6c31140879123 | tool:single-operator-correction |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-19T21:52:58Z | created | id=CC-2026-07-19-single-operator-authorization; base_sha=35cbf7fcebdd9e2b5f657a971af6c31140879123; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-19T22:36:33Z | approved | digest=sha256:1ef052a530a9dd73bd4bb2e20fbe1c6577efcd0e252d05ce90b5425abc67e5ac; token=APPROVE SINGLE-OPERATOR AUTHORIZATION CORRECTION; scope=single-operator-authorization-correction; adr_0105_renewed=sha256:37db3e0ca3c7557555a1b5885bc66138949dc320699bd5c3f4e9ab03cac87eea; adr_0105_authority_blob=d971a881f4c7e58ab31f268b3a8d352b884ddec3; operator=operator; pr=707; timestamp_source=GitHub API Date response header |
| 2026-07-19T22:38:18Z | implementation_started | note=implementation was performed under the operator corrective directive SINGLE-OPERATOR GOVERNANCE RECOVERY, which required corrective implementation rather than a further design exercise, and PRECEDED this record — the event is written where the lifecycle requires it; bound_to=sha256:1ef052a530a9dd73bd4bb2e20fbe1c6577efcd0e252d05ce90b5425abc67e5ac; implemented_in=2722dc0a7e7d7af5d794b544b808c1f70c180263, 0f785fc3b2d718ef65c27d60348b8642df76cf14, f1301e8bd2f5f9d2261939b04bbe258277525c5b; pr=707; timestamp_source=GitHub API Date response header |
| 2026-07-19T22:58:41Z | head_proposed | parent_sha=30144a33246b8ddf2f50db8f249187d074037a04; ci=green; ci_head=30144a33246b8ddf2f50db8f249187d074037a04; ci_required=unit (fast, no toolchain) pass 2m11s, real-tooling E2E (must run, not skip) pass 5m34s — both live-required contexts, observed at ci_head; verifier_pre=OK continue with 0 diagnostics; verifier_head=OK continue with 0 diagnostics; verifier_merge=ST-9 stop — no operator merge_approved event authorizes the current head, which is the expected remaining gate; scope=11 files, all declared, 0 unauthorized; selftest=96 of 96 injected defects detected; pr=707; timestamp_source=GitHub API Date response header |
| 2026-07-19T23:12:02Z | merge_approved | parent_sha=50e1aaa799650b2441e5f14246ddfc4e3056c8f8; digest=sha256:1ef052a530a9dd73bd4bb2e20fbe1c6577efcd0e252d05ce90b5425abc67e5ac; pr=707; operator=operator; token=APPROVE PR 707 SINGLE-OPERATOR MERGE |
