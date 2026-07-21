---
id: CC-2026-07-21-preflight-classification
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T3: this change edits docs/adr/** and tools/contract/**"]
supersedes: []
---

# CC-2026-07-21-preflight-classification

### objective

**Make the pre-implementation phase reachable by the rule it enforces.**

ADR-0105 §3 requires a Change Contract to be written and content-approved **before** implementation.
Every classification input was derived from the actual git diff (`__main__.py` → `repo.diff_names` →
`derive.owners_for` → `classify.triggers`). At the moment the contract is written that diff contains
the contract and nothing else, so a change the contract itself declares spans two subsystems
classified as spanning none. All three branches were closed:

| branch | `verify --phase pre` | why it failed |
|---|---|---|
| declare the true trait | `CL-2` | declared ≠ derived, because the diff held only the contract |
| declare `contained` | `continue` | **false** — under-declared, took none of the cross-system obligations |
| implement first | `continue` | violated contract-before-implementation, the rule under test |

The phase returned `continue` exactly when the phase was already over. Reproduced mechanically
before this change: contract-only diff → `T1` *"0 subsystem(s) spanned: none"* → `CL-2`; the same
contract with the implementation added → `T1 FIRED — 2 subsystem(s)` → `continue`.

**The root is not `T1` and not the lifecycle.** The classifier had exactly one source of paths — the
diff — and no way to be asked to classify an *intended* path set, though the contract already states
one in `expected_surfaces`, and no contractless entry point existed to ask the question before a
contract was written at all.

`T1`'s predicate is **unchanged**. Only the path set it is evaluated over is selected by phase.

### success_condition

Each is machine-checkable against the landed tree. None restates a count that code already owns.

1. A contract whose `expected_surfaces` span two subsystems, with a diff containing **only the
   contract**, reports `T1` FIRED and `verify --phase pre` returns `continue`/`OK`. Before this
   change the same inputs returned `T1` not fired and `CL-2`.
2. At `at-head` and `merge-gate` the classification path set is still `git diff --name-only`, and a
   contract whose intent claims two subsystems while its diff touches one answers `CL-2` there.
3. An under-declared contract — intent spans two subsystems, declaration omits `cross-system` —
   answers `CL-2` at `pre`.
4. A **conservative** declaration (declaring more than intent proves, for a `T2` not yet evaluable)
   does **not** answer `CL-2` at `pre`, and the same contract **does** answer `CL-2` at `at-head`.
5. `python -m tools.contract preflight <path>...` runs with no contract and no diff, writes nothing,
   emits structured JSON, and returns `REQUIRED` or `UNDETERMINED` — **never** `NOT REQUIRED`.
6. An intended path that is wildcarded, malformed, unknown, under `src/` but not a module, or a
   module no subsystem owns, **fails closed** — never `contained`, never `not required`.
7. `AGENTS.md` names `python -m tools.contract preflight` directly.
8. `python -m tools.contract selftest` reports every control DETECTED, including `NC-P1`–`NC-P16`,
   and each of those was additionally observed **MISSED** with its named defect reinstated.
9. `python -m tools.arch ci`, `python -m tools.ci static` and `./scripts/check.sh` pass.
10. The five landed contracts in `docs/contracts/` are **byte-identical** to `25d2b96`, and their
    verdicts at their own recorded historical heads are unchanged.
11. At `pre` the impact analysis is **not run**: `T2`'s reason reads *not evaluated* rather than
    *unknown*, derived evidence records it, and an impact port that raises does **not** produce
    `ST-7` — proven through the production entry point, not only through fakes.
12. At `at-head` the **same** raising impact port still reaches `ST-7`, and a head diff classified
    `MIGRATION_REQUIRED` still fires `T2` and derives `cross-system`.

### rollback

`git revert` the single squash commit. The verb disappears, the phase selection reverts to the diff,
and `ADR_0105_DIGEST` reverts with the ADR body — nothing persists state, so there is nothing to
migrate back.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | bce8525d462e9df8e070191972cc7a757c6da377 |

### coupling

| what | must_move_with | why |
|---|---|---|
| the `pre` phase reading intent | the `preflight` verb | a preflight alone leaves `verify --phase pre` answering `CL-2` on every cross-system contract forever, degrading `CL-2` from a divergence finding into an expected pre-implementation state — the same "a verdict about a default, dressed as a finding about the contract" defect `CC-2026-07-20-phase3-closeout` removed from the base anchor. A phase switch alone leaves the front door with no way to ask before a contract exists. |
| the ADR §1a amendment | `ADR_0105_DIGEST` and the front-matter `approved_digest` | the pin exists so a rule cannot silently stop meaning what its authority says |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | §1a defines the phase-selected path set, the preflight limits, and the `pre`-versus-final trait comparison; §1 `T1`'s **Evidence source** line now points at §1a. The `T1` PREDICATE is untouched. | declared |
| tools/contract/__main__.py | S5a selects the classification path set by phase; the `preflight` verb | declared |
| tools/contract/derive.py | `intended_paths` — the fail-closed intent resolver | declared |
| tools/contract/classify.py | `intent_path_kind`, `NON_SOURCE_CLASSES`, the `path_source` message parameter, the renewed digest pin | declared |
| tools/contract/decide.py | `CL-2` becomes phase-aware — the only rule whose predicate changes | declared |
| tools/contract/selftest.py | `NC-P1`–`NC-P16` | declared |
| tests/test_contract_compiler.py | the production-entrypoint proof — the selftest drives fakes and cannot show the verb is reachable | declared |
| AGENTS.md | the front door names the preflight command | declared |
| docs/contracts/CC-2026-07-21-preflight-classification.md | this contract (ADR-0105 §3.6) | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | no workflow change |
| .github/ci-control-registry.yml | no registry change; this adds no CI job |
| src/fanops/** | no runtime change — this is governance tooling only |
| .orchestration/** | no orchestration change |
| .agents/lanes.json | no lane change |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | Phase 4 stays `NOT STARTED`; this is entry-readiness, not progress |
| docs/contracts/CC-2026-07-18-*.md | the landed contracts are byte-identical, never appended |
| docs/contracts/CC-2026-07-19-*.md | the landed contracts are byte-identical, never appended |
| docs/contracts/CC-2026-07-20-*.md | the landed contracts are byte-identical, never appended |

### expected_surfaces

| path | kind | why |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | §1a, and §1 `T1`'s evidence-source line |
| tools/contract/__main__.py | MODIFIED | phase-selected path set; the `preflight` verb |
| tools/contract/derive.py | MODIFIED | `intended_paths` |
| tools/contract/classify.py | MODIFIED | `intent_path_kind`; the renewed pin |
| tools/contract/decide.py | MODIFIED | phase-aware `CL-2` |
| tools/contract/selftest.py | MODIFIED | `NC-P1`–`NC-P16` |
| tests/test_contract_compiler.py | MODIFIED | the production-entrypoint proof |
| AGENTS.md | MODIFIED | the front-door route |
| docs/contracts/CC-2026-07-21-preflight-classification.md | NEW | this contract |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED, including `NC-P1`–`NC-P16`. Each of the sixteen was additionally run with its named defect reinstated and observed MISSED | a control proven only in the green direction has not been proven — `CC-2026-07-20-phase3-closeout` records the same requirement |
| OB-ENTRYPOINT | tests/test_contract_compiler.py drives `python -m tools.contract preflight` through `subprocess` | the selftest drives FAKES; only a real process proves argparse wires the verb and the real ports resolve the real artifacts |
| OB-ARM-REPLAY | the three recorded probe heads re-verified against this head | the only evidence the defect is actually closed on the inputs that exhibited it, rather than on fixtures written after the fact |
| OB-NO-BACKFILL | byte-comparison of the five landed contracts against `25d2b96`, and re-verification of each at its own recorded historical head | proves the correction disclosed and did not repair — the one obligation whose failure would invert this change's purpose |
| OB-AUTH-DRIFT | the current-head `AUTH-BLOB-MOVED` / `ST-2` state of the landed contracts, reported rather than suppressed | the ADR blob MOVED; claiming their current-head verdicts are unchanged would be false |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — proves the DECLARED controls match the wired ones |
| OB-UNIT-CI | the required context `unit (fast, no toolchain)` concluding success on the exact head | the ONLY evidence the pytest suite passes |
| OB-E2E-CI | the required context `real-tooling E2E (must run, not skip)` concluding success on the exact head | exercises the real toolchain rather than a fake |
| OB-C18 | this contract, the ADR amendment, and the renewed digest | Constitution C18.1 / the ADR process |
| OB-REVERIFY | every claim above re-run on the final head, not reused from an earlier run | no evidence reuse |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-21T00:00:00Z | created | id=CC-2026-07-21-preflight-classification; base_sha=25d2b965c104521c46513d7ce6d32ac7ee26b2ab; timestamp_source=operator-approved directive of this session |
| 2026-07-21T22:19:40Z | approved | digest=sha256:7e556e6ec70b0db928e22689a52ca226041cacf7414fbd95a1ed79f7bf2ff719; token=APPROVE PREFLIGHT CLASSIFICATION DECLARATION; scope=preflight-classification; adr_0105_authority_blob=bce8525d462e9df8e070191972cc7a757c6da377; adr_0105_body_digest=sha256:6b065acb4b0736b7035b46fb62fce4258f6e33b32b4d19f9799d037689bd93c1; operator=operator; pr=712; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-21T22:19:40Z | implementation_started | bound_to=sha256:7e556e6ec70b0db928e22689a52ca226041cacf7414fbd95a1ed79f7bf2ff719; implemented_head=f2e9daed42e5287903259ecee9604fb257b0bab5; note=implementation preceded this contract and its lifecycle, performed under the operator directive APPROVE PRE-IMPLEMENTATION CLASSIFICATION CORRECTION; the sequence is disclosed here rather than backdated; pr=712; timestamp_source=GitHub API Date response header, observed during this operation |
