---
id: CC-2026-07-19-cli-lifecycle-integrity
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1]
stop_conditions: []
supersedes: []
---

# CC-2026-07-19-cli-lifecycle-integrity

`LIFECYCLE-REWRITTEN` is implemented, correct, and covered by a negative control — and the shipped
command could not produce it. This contract governs the correction, and nothing else.

**It does not reopen `CC-2026-07-18-change-contract-compiler`.** That contract is `accepted`; its
lifecycle is closed and is not a work log. The defect was found after acceptance, so it is handled
the way ADR-0105 §6 says a post-acceptance change is handled: a new contract, with its own gates.

**The rule was never the defect.** `NC-C10b` injects a rewritten lifecycle and the rule fires. What
no control asserted is that the CLI ever hands the rule its input. That gap is the whole subject
here, and it is the reason a green control is not the same as a working check.

### objective

Make `LIFECYCLE-REWRITTEN` and `DECL-DIVERGED` reachable through the real `python -m tools.contract
verify` path, including the default invocation named in contract success conditions, by reading the
landed contract on `origin/main` INDEPENDENTLY of whether `--head` was supplied. Add the shipped-CLI
reachability proofs that would have caught this. Change no rule, no phase, no decision outcome and
no digest.

### success_condition

Each is executable and independently checkable by someone who did not write this:

1. `python -m tools.contract verify <contract>` with NO `--head`, against a working tree whose
   lifecycle rewrites a landed row, exits NON-ZERO and reports `LIFECYCLE-REWRITTEN`.
2. The same command against an untracked-clean, unmodified landed contract still reaches `continue`
   at rule `OK`, exit 0.
3. `python -m tools.contract verify --head HEAD` still evaluates the blob at `HEAD` — the artifact
   under evaluation is unchanged by this contract.
4. With `origin/main` unresolvable the run is FAIL-CLOSED — a named `unverifiable` entry reaching
   `ST-7` — never a silently-skipped comparison.
5. `python -m tools.contract selftest` exits 0 with every control DETECTED.
6. `python -m tools.arch ci` and `python -m tools.ci static` both exit 0.
7. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.

### rollback

`git revert` of the single squash commit restores the previous `run()` and removes the added tests.
The boundary is exactly three files and no state: the verifier is read-only, no workflow references
it, no runtime imports it, and `src/fanops/` does not move.

Cost of NOT rolling back a bad version is the greater risk, and it is bounded the same way: the
change can only make the verifier report MORE, never less, so a defect here surfaces as a false
`stop` — loud, and never a false `continue`.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | f9fa602b501f80418d8a66eb9c6389a99ae64c8a |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the tooling path (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| tools/contract/__main__.py | the composition root where the landed-copy read is wired | declared |
| tests/test_contract_compiler.py | the shipped-CLI proof matrix | declared |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | this contract | declared |

### prohibited_scope

| glob | why |
|---|---|
| tools/contract/lifecycle.py | the RULE is correct; touching it would be redesigning lifecycle integrity |
| tools/contract/decide.py | no rule added, reordered or reworded — a second overlapping rule is forbidden |
| tools/contract/parse.py | the digest model is untouched; the lifecycle stays outside `D` |
| tools/contract/adapters.py | `RepoPort` already exposes every read this needs |
| docs/adr/** | no ADR amendment — the model is frozen and this is an implementation defect |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | an accepted contract is not a mutable work log |
| docs/reconciliation/** | roadmap B3 is explicitly NOT combined into this contract |
| .github/workflows/** | no CI job added — enforcement is still Phase 6 |
| src/fanops/** | no runtime change |

### expected_surfaces

| path | kind | why |
|---|---|---|
| tools/contract/__main__.py | MODIFIED | read `origin/main` once, independently of `head`, fail closed |
| tests/test_contract_compiler.py | MODIFIED | the eight-case proof matrix through the production entry point |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | NEW | this contract |

### coupling

| what | must_move_with | why |
|---|---|---|
| the landed-copy read for `main_blob` | the `merged` derivation at S7 | both are the same fact about `origin/main`. Two reads could disagree between them, and a tool that answers "has this landed" differently in two places is worse than one that answers slowly |
| making the diagnostic reachable | a shipped-CLI test that fails without the fix | a reachability defect is invisible to a rule-level control by construction, so the proof has to run the entry point |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| `LIFECYCLE-REWRITTEN` fires when `main_blob` is supplied, so the rule itself is not the defect | `NC-C10b` DETECTED in the accepted implementation | f9fa602b501f80418d8a66eb9c6389a99ae64c8a | tool:contract-selftest |

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-CLI-REACH | the eight-case matrix driven through `__main__.main(argv)` | proves the SHIPPED path produces the diagnostic — the one thing a rule-level control cannot show |
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED | proves each rule still fires on an injected defect after the wiring change |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-19T15:00:00Z | created | id=CC-2026-07-19-cli-lifecycle-integrity; base_sha=7e7f5115ef071530a39d8025664fda98aeda847d |
