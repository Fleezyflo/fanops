---
id: CC-2026-07-19-adr-0105-state-machine
traits: [governance]
authorized_actions: [design]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T6: the operator required a contract for this phase"]
supersedes: []
---

# CC-2026-07-19-adr-0105-state-machine

Amends ADR-0105 §4.1a, §4.2 and §4.3 so a lifecycle row can no longer promote a record to a state
that asserts an irreversible act was authorized. It amends the MODEL and implements NOTHING.

**This contract is self-contained.** Every state, predicate, precedence rule, schema and negative
control the amendment must produce is written out below. A reviewer holding only this repository and
this file can determine the exact required ADR text without any other source.

**This contract cannot be text-only, and that is a finding rather than a convenience.** `NC-C27`
recomputes the ADR body digest and compares it to the pin in `tools/contract/classify.py`. Editing
the body moves the digest, so the pin must move in the same change or CI reddens. The pin is ONE
line; any other edit to that file is a scope violation, not a judgement call.

**`authorized_actions` is `[design]` and stops there.** Implementation, merge and acceptance are
separate grants obtained at their own gates.

### objective

Amend ADR-0105 §4.3 from the eleven states `tools/contract/lifecycle.py::state()` returns today to
the fourteen enumerated below; amend §4.1a to give each authorization route its own input set and an
ordered evaluation algorithm; amend §4.2 to carry the census and acceptance schemas those states
consume. Add no rule to any tool. Change no behaviour. Implement no control.

**The count is fourteen, and no current state is removed.** Today's ladder returns eleven:
`refused`, `superseded`, `abandoned`, `accepted`, `merged`, `approved_for_merge`, `implemented`,
`in_implementation`, `approved`, `in_review`, `draft`. This amendment adds exactly three —
`acceptance_claimed`, `merged_unverified`, `merged_unauthorized` — so 11 + 3 = 14. `merged` and
`accepted` are NARROWED by predicate, not split into replacements: each keeps its name and rank and
gains a stricter predicate, with the weaker cases falling to the new states beneath it.

### the target state ladder

Ordered, **first match wins**, evaluated top to bottom. Rank is precedence: a lower rank is reached
only when every rank above it fails. Fourteen states, matching the count stated above.

| # | state | complete predicate |
|---|---|---|
| 1 | refused | a `refused` event is present |
| 2 | superseded | a `superseded` event is present |
| 3 | abandoned | an `abandoned` event is present |
| 4 | accepted | an `accepted` event is present AND the governed merge commit exists on `main` AND `accepted.merge_sha` equals that actual governed merge commit AND `merge_authorization == verified` AND `acceptance_verified` |
| 5 | acceptance_claimed | an `accepted` event is present AND rank 4 does not hold — the merge is not on `main`, or `accepted.merge_sha` names a commit that is not the governed merge, or `merge_authorization != verified`, or NOT `acceptance_verified` |
| 6 | merged | the governed merge commit is on `main` AND `merge_authorization == verified` |
| 7 | merged_unverified | the governed merge commit is on `main` AND `merge_authorization` is one of `claimed_stale`, `claimed_unknown`, `fidelity_failed`, `unavailable` |
| 8 | merged_unauthorized | the governed merge commit is on `main` AND `merge_authorization == absent` |
| 9 | approved_for_merge | `merge_authorization == verified` at the current head |
| 10 | implemented | a `head_proposed` event binds the current head AND CI is green |
| 11 | in_implementation | an `approved` event names the current `D` AND an `implementation_started` event is present |
| 12 | approved | an `approved` event names the current `D` |
| 13 | in_review | a PR is open AND all mandatory fields for the declared traits are present |
| 14 | draft | otherwise |

**Precedence rules the amendment must state explicitly.** Ranks 1–3 are unconditional and evaluated
before any gate is consulted; a terminal event outranks even a verified acceptance, because terminal
events are self-limiting and honouring one can never grant anything. Ranks 4–5 precede 6–8 because an
`accepted` event is a claim about the whole change and must be reported as such even when the merge
beneath it is unverified. Ranks 6–8 partition the single condition "the governed merge commit is on
`main`" by authorization status, and that condition is derived from the platform and `main` ancestry,
never from a lifecycle row.

### the ordered authorization algorithm

`merge_authorization` is computed by evaluating **Route W first, to completion, before any Route U
input is read**. The routes have disjoint input sets, and a Route U input can never affect a
qualifying witnessed authorization because it is never fetched.

**Route W inputs, and only these:** governed PR identity; the final pre-merge PR head; the review
list; each review's `state`; each review's `commit_id`; and, post-merge only, the tree-fidelity
inputs. A qualifying witnessed review must reach `verified` **without reading** an in-file
`merge_approved` event, without any writer-census evidence, and without any Route U admissibility
evidence.

**Route U inputs, and only these:** the final pre-merge PR head; the parent-bound `merge_approved`
event; the immutable census event of the next section; the parent-binding proof; and, post-merge,
tree fidelity.

**The algorithm, in order:**

1. Read the Route W inputs. **If any required Route W read did not complete → `unavailable`.** Stop.
2. If some review has `state == APPROVED` and `commit_id == final_pre_merge_head`: post-merge, test
   tree fidelity — fidelity fails → `fidelity_failed`; otherwise → `verified`. **Stop; no Route U
   input is read.**
3. Otherwise Route W is readable and did not qualify. Record whether any review existed at all, then
   continue to Route U.
4. Read the Route U inputs. If any required Route U read did not complete → `unavailable`. Stop.
5. No `merge_approved` event present: if step 3 saw at least one review, → `claimed_stale`; if it saw
   none, → `absent`.
6. `merge_approved` present but `parent_binds` fails at the final pre-merge head → `claimed_stale`.
7. The `census_observed` event is absent, or present and fails its schema → `claimed_unknown`.
8. Census valid and `principal_count == 1`: post-merge, test tree fidelity — fidelity fails →
   `fidelity_failed`; otherwise → `verified`.
9. Census valid and `principal_count != 1` → `absent`. This is a readable proof that no admissible
   claim of either route exists: the unwitnessed route is available only under a single effective
   writer, and a valid census showing more than one settles that question rather than leaving it open.

**Decided: an unavailable Route W read does NOT permit Route U fallback.** If the review list cannot
be read, whether a qualifying witnessed approval exists is unknown, and falling back would accept the
weaker route precisely when the stronger one cannot be checked — an outage would silently lower the
evidence standard. Step 1 therefore terminates at `unavailable` → `ST-7`. `NC-SM-21` holds this.

**Decided: a Route U input that is never read cannot downgrade a witnessed authorization.** Step 2
stops before step 4, so Route U availability is irrelevant once Route W qualifies. `NC-SM-20` holds
this by making every Route U-only input raise while a valid Route W review is present.

### the closed authorization-value set

**Six values.** `claimed_inadmissible` has been **removed**: it required positive proof that two or
more effective writers existed at the authorization instant, and no platform surface returns
historical permission state for a user-owned repository — the organization audit log does not exist
for one, and installation endpoints are unreadable. A state whose predicate cannot be evaluated must
not exist, so it does not. Its readable case is covered: a valid census proving more than one
principal yields `absent` (step 9), and an unobtainable or malformed census yields `claimed_unknown`
(step 7).

| value | exact predicate | required reads | all reads completed | pre-merge state | post-merge state | rule | outcome |
|---|---|---|---|---|---|---|---|
| verified | Route W step 2, or Route U step 8 | that route's inputs only | yes | approved_for_merge | merged | OK | continue |
| claimed_stale | a review exists but binds no final head, or `parent_binds` fails at the final head | that route's inputs | yes | lower ladder | merged_unverified | ST-10 | stop |
| claimed_unknown | the `census_observed` event is absent, or present and fails its schema | Route U inputs | yes | lower ladder | merged_unverified | ST-10 | stop |
| fidelity_failed | the route qualified pre-merge but PR-head tree does not equal the merged-commit tree | trees at both commits | yes | n/a | merged_unverified | ST-10 | stop |
| absent | reads completed and no admissible claim of either route exists, including a valid census proving more than one principal | that route's inputs | yes | lower ladder | merged_unauthorized | ST-9 | stop |
| unavailable | a required read of the route under evaluation did NOT complete | whichever read failed | **no** | lower ladder | merged_unverified | ST-7 | stop |

**Exhaustiveness proof the amendment must carry.** The six values partition into three disjoint
groups: `{verified}` → rank 6; `{claimed_stale, claimed_unknown, fidelity_failed, unavailable}` →
rank 7; `{absent}` → rank 8. The groups are disjoint and their union is the whole set, so once the
governed merge commit is on `main`, exactly one of ranks 6, 7 and 8 matches. **No value falls through
to `approved_for_merge`, `implemented` or lower after the merge is known to exist.** `NC-SM-14` and
`NC-SM-17` hold this at implementation.

**Readable negatives and unavailable evidence stay distinct.** `absent` means reads succeeded and
proved nothing admissible exists — reported through `ST-9`, with `ST-7` NOT firing. `unavailable`
means a read did not complete — reported through `ST-7`, and never recorded as `absent`, which would
convert an outage into a proven governance failure. Under `unavailable`, derivation still runs and
yields `merged_unverified`; it can never yield `merged` or `accepted`. The decision independently
stops at `ST-7`, and the report carries both.

### the Route U census event, schema

Event kind **`census_observed`**, appended immediately before the `merge_approved` it supports.

| key | grammar | binds to |
|---|---|---|
| `repo_id` | decimal integer, the GitHub numeric repository id | the repository identity, which survives renaming |
| `observed_at` | `YYYY-MM-DDTHH:MM:SSZ`, taken from the GitHub `Date` response header of the census read | the server-attested observation instant |
| `head_sha` | 40 lowercase hex | the final pre-merge PR head this census accompanies |
| `sources` | comma-separated fixed tokens drawn from `collaborators`, `installations`, `keys`, `workflow_permissions` | the platform surfaces actually read |
| `principals` | comma-separated `type:id` tokens, `type` one of `user`, `app`, `key`, `actions`, `team`, sorted ascending by whole token | the canonical effective-writer set |
| `principal_count` | decimal integer, equal to the token count of `principals` | the size assertion |
| `digest` | `sha256:` plus 64 hex over the exact `principals` string in UTF-8 | tamper-evidence for the set |

**Effective writer** means any principal able to write a ref at that instant: collaborators with
`push` (administrators included, since admin implies push); App installations with `contents: write`;
deploy keys with `read_only` false; and the Actions token when `default_workflow_permissions` is
`write` or when any workflow at `head_sha` declares `contents: write`. Teams are expanded to their
member users and additionally recorded as `team:<id>`; a user-owned repository has none.

**Post-merge proof that the record refers to the authorization instant is structural, not temporal.**
`census_observed.head_sha` must equal the final pre-merge PR head that `merge_approved` binds, and
`observed_at` must not exceed the server-observed instant of the first commit containing the census
row. Both are checkable after the fact from platform records, and neither trusts a local clock.

**Classification.** *Malformed* — any key missing, any grammar violation, `principal_count` unequal to
the token count, or `digest` unequal to the recomputation → `claimed_unknown`. *Stale* — `head_sha`
is not the final pre-merge head → `claimed_stale`. *Unavailable* — a read needed to verify the record
did not complete → `unavailable`, hence `ST-7`. *Verified* — schema valid, binding holds, and
`principal_count` is 1.

**Stated limitation, not a gap.** The census cannot be CAPTURED today, because the App-installation
surface returns 401 or 403 to every available credential. The predicate above remains fully
evaluable — the event is either absent, malformed or valid — so no unevaluable state is introduced.
Route U simply cannot reach `verified` until that surface is readable, and `claimed_unknown` is the
correct classification in the meantime.

### the `accepted` event, schema

| key | grammar |
|---|---|
| `merge_sha` | 40 lowercase hex |
| `decision` | exactly the literal `accepted`; no other value is permitted |
| `date` | `YYYY-MM-DD`, UTC |
| `operator` | non-empty token containing no semicolon |
| `runs` | ascending decimal check-run ids, comma-separated, no duplicates, non-empty |
| `pr` | decimal integer, the governed PR |

**Governed PR selection.** The governed PR is the `pr` of the LAST `binding` event. If `accepted.pr`
is present it must equal that value; disagreement is malformed. This removes the ambiguity when
several `binding` events exist.

**Multiple acceptance claims.** The LAST `accepted` event is evaluated. Earlier ones are historical,
retained, and never re-evaluated. **After a successfully verified acceptance no further lifecycle
event may be appended**, except `superseded`, or a later `accepted` naming a superseding `runs` set,
which together are the explicitly authorized correction mechanism. Anything else is
`EVENT-AFTER-TERMINAL`, reaching rule `A5`.

**Malformed handling.** Grammar violations are rejected during lifecycle validation as
`ACCEPT-INCOMPLETE`, reaching rule `A5`, and never reach state derivation. Rows that parse but whose
values disagree with the platform are not validation errors; they are unverified claims and derive
`acceptance_claimed`. Grammar failures are parse-time; semantic mismatches are state-time.

### `acceptance_verified`, defined mechanically

TRUE if and only if all of A through F hold. Nothing here consults free-form prose.

**A** — the `accepted` event satisfies the schema above, and a `merged` event carries `merge_sha`.
**B** — the actual governed merge SHA is `pullRequest.mergeCommit.oid` for the governed PR, confirmed
an ancestor of `main`. Immutable identifier: the commit SHA. It is never taken from a row.
**C** — `accepted.merge_sha` and `merged.merge_sha` both equal B.
**D** — the `merged` row timestamp equals `pullRequest.mergedAt` exactly.
**E** — every verification obligation resolves to evidence by the transform below, and every resolved
evidence item is satisfied.
**F** — `accepted.runs` equals exactly the set of qualifying check-run ids that satisfied E. After
acceptance only those ids are verified, so a later rerun cannot revise an earlier verdict.

**The `evidence=` free-text field is descriptive only and carries no verification weight.** The
amendment must state that in those words. A through F are the whole gate, and writing an `accepted`
row cannot satisfy any of them.

Readable failures of C, D, E or F derive `acceptance_claimed`. Unavailable reads of the PR record,
the check-runs list, or the registry blob at `created.base_sha` additionally fire `ST-7`.

### obligation-to-evidence transform

Every obligation must resolve to exactly one of: **(i)** a registered CI control id together with its
exact workflow path and job name; **(ii)** an exact check context name; or **(iii)** an independently
executed repository command with a machine-verifiable result artifact. **An obligation resolving to
none of the three is UNMAPPED, and an unmapped obligation fails condition E** — it is never silently
dropped because its prose contains no parseable context. `NC-SM-25` holds this.

| obligation | resolution | form |
|---|---|---|
| OB-NC-C27 | the `Negative controls` STEP of job `negative controls (validator effectiveness)` in `.github/workflows/architecture.yml`, whose own step conclusion must be `success` | (i) |
| OB-ARCH-CI | check context `gate (drift + policy + registries)` | (ii) |
| OB-CI-STATIC | check context `gate (drift + policy + registries)` | (ii) |
| OB-C18 | recompute the ADR body digest and compare it to the `adr_0105_renewed` value of the last `approved` row; the artifact is the two digests | (iii) |
| OB-LADDER | count the state rows of the amended §4.3 and compare to the number stated there; the artifact is the two counts | (iii) |
| OB-ROUTE-W | the review record, `state == APPROVED` and `commit_id == final_pre_merge_head`; the artifact is the review id and commit id | (iii) |

**OB-NC-C27 resolves to a STEP, not a job, and that distinction is load-bearing.** On this branch the
job concludes `success` while its `Negative controls` step is `skipped` by a fail-open selector. A
job-level mapping would accept a control that never executed, which is exactly the class of defect
this amendment exists to remove.

### success_condition

Each is independently checkable using only this repository and this contract:

1. `python -m tools.contract selftest` exits 0 with every control DETECTED, `NC-C27` included.
2. The amended §4.3 contains a first-match-wins ladder of **exactly fourteen states**, whose names,
   order and predicates match "the target state ladder" above, and states the number fourteen.
3. The amended §4.3 states the three precedence rules given above.
4. The amended §4.3 states that state derivation and the decision are separate, that an unavailable
   read still yields a merged-family state, and that `ST-7` fires independently.
5. The amended §4.1a contains the ordered algorithm above, including the disjoint input sets and both
   decisions: no Route U fallback on an unavailable Route W read, and no Route U input read once
   Route W qualifies.
6. The amended §4.1a states that the witnessed predicate is `state == APPROVED` and
   `commit_id == final_pre_merge_head` alone; that reviewer identity is not an input available to the
   verifier; that this amendment adds no identity or permission predicate; and that any non-author
   property of the review is platform-produced evidence rather than a verifier rule.
7. The amended §4.2 defines the `census_observed` and `accepted` schemas exactly as above.
8. The amended §4.2 defines `merge_authorization` as the CLOSED six-value set above and carries the
   exhaustiveness proof.
9. The amended §4.3 defines `acceptance_verified` as A through F and states that `evidence=` carries
   no verification weight.
10. The amended text carries the obligation-to-evidence transform, including that an unmapped
    obligation fails acceptance.
11. The amended text names NC-SM-01 through NC-SM-27 and the three new rules `ST-9`, `ST-10` and
    `ST-11`, and authorizes no other new rule identifier.
12. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
13. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.

### implementation controls

The later implementation contract must build every control below. **This contract builds none.**
`A5`, `OK`, `RF-3` and `ST-7` exist today; `ST-9`, `ST-10` and `ST-11` are the three the
implementation must INTRODUCE, and no other new rule id is authorized — `ST-8` is the current
maximum. Rows marked **structural** assert a property outside the decision table and name the exact
test failure they must produce.

**Twenty-seven controls.** The previous inventory of nineteen predated route separation, the census
schema, the accepted schema and the obligation transform; eight controls cover those. The count is
twenty-seven because the table below has twenty-seven rows.

| id | injected defect or missing evidence | expected derived state | expected rule | outcome | distinct boundary proven |
|---|---|---|---|---|---|
| NC-SM-01 | an `accepted` event with `merge_authorization` not `verified` | acceptance_claimed | ST-11 | stop | a written row cannot promote to `accepted` |
| NC-SM-02 | merge on `main`, a verified route, no `accepted` event | merged | OK | continue | verification alone does not manufacture acceptance |
| NC-SM-03 | reads complete and prove no admissible claim exists | merged_unauthorized | ST-9 | stop | a readable negative reaches ST-9 and `ST-7` must NOT fire |
| NC-SM-04 | a required Route U read raises | merged_unverified | ST-7 | stop | `unavailable` is its own value and reaches the fail-closed rule |
| NC-SM-05 | a Route U claim binding a head other than the final pre-merge head | merged_unverified | ST-10 | stop | binding is to the FINAL head, not any head |
| NC-SM-06 | a post-merge `merge_approved` append onto a `merged_unauthorized` record | merged_unauthorized, unchanged | ST-9 | stop | an append verifies nothing and originates nothing |
| NC-SM-07 | a witnessed review whose `commit_id` is not the final pre-merge head | merged_unverified | ST-10 | stop | the witnessed route binds the final head |
| NC-SM-08 | PR-head tree not equal to the merged-commit tree | merged_unverified | ST-10 | stop | `fidelity_failed` is a readable negative, never `ST-7` |
| NC-SM-09 | a `refused` event alongside a fully verified acceptance | refused | RF-3 | refuse | terminal precedence outranks rank 4 |
| NC-SM-10 | an `accepted` event that would satisfy rank 4, with a `refused` event also present | refused | RF-3 | refuse | first-match-wins is ordering: rank 1 is taken and rank 4 is never evaluated |
| NC-SM-11 | **structural** — enumerate the implemented state set and compare it to fourteen | not applicable | not applicable | `AssertionError: state set size {n} != 14` | the declared number and the implemented set cannot drift |
| NC-SM-12 | a required check-run at the merge SHA is `failure`, `skipped`, `cancelled` or absent | acceptance_claimed | ST-11 | stop | acceptance verification is separate from merge authorization |
| NC-SM-13 | a required read does not complete while the governed merge is on `main` | merged_unverified | ST-7 | stop | derivation and decision are separate and both are reported |
| NC-SM-14 | each of the six authorization values driven in turn with the merge on `main` | merged, merged_unverified, merged_unverified, merged_unverified, merged_unauthorized, merged_unverified | OK, ST-10, ST-10, ST-10, ST-9, ST-7 respectively | continue for the first, stop for the rest | the value set is total and the post-merge partition is exhaustive and mutually exclusive |
| NC-SM-15 | an `accepted` event while the governed merge is NOT on `main` | acceptance_claimed | ST-11 | stop | acceptance cannot precede the merge it accepts |
| NC-SM-16 | `accepted.merge_sha` naming a commit that is not the governed merge | acceptance_claimed | ST-11 | stop | the row is checked against the platform, never trusted |
| NC-SM-17 | **structural** — the governed merge on `main` under every authorization value | not applicable | not applicable | `AssertionError: derived {state} is rank {n} > 8 with the merge on main` | no fall-through below the merged family |
| NC-SM-18 | the `census_observed` event absent | merged_unverified | ST-10 | stop | `claimed_unknown` is a readable finding, NOT `unavailable` |
| NC-SM-19 | an `accepted` row whose `evidence=` text asserts success while `runs` does not | acceptance_claimed | ST-11 | stop | free-form evidence text cannot verify itself |
| NC-SM-20 | a qualifying Route W review present while EVERY Route U-only input raises | merged | OK | continue | Route U inputs are never read once Route W qualifies |
| NC-SM-21 | the Route W review read raises, with a Route U claim present that would otherwise qualify | merged_unverified | ST-7 | stop | an unavailable Route W read does NOT permit Route U fallback |
| NC-SM-22 | `census_observed` with `principal_count` unequal to the token count, or a mismatched `digest` | merged_unverified | ST-10 | stop | a malformed census is `claimed_unknown`, never silently trusted |
| NC-SM-23 | two `binding` events, with `accepted.pr` naming the earlier one | acceptance_claimed | ST-11 | stop | the governed PR is the last binding, and disagreement is malformed |
| NC-SM-24 | `accepted.runs` non-ascending, containing a duplicate, or empty | acceptance_claimed | ST-11 | stop | the run set has a canonical encoding that arbitrary values cannot satisfy |
| NC-SM-25 | a verification obligation resolving to none of the three evidence forms | acceptance_claimed | ST-11 | stop | an unmapped obligation fails acceptance and is never dropped |
| NC-SM-26 | three `accepted` events, only the last of which verifies | accepted | OK | continue | the last claim is evaluated and earlier ones are historical |
| NC-SM-27 | an `accepted` event with `decision` other than the literal `accepted` | rejected at validation | A5 | stop | `decision` has exactly one permitted value |

### rollback

`git revert` of the single squash commit restores the previous body, the previous pin and the
previous front-matter digest together, leaving no dangling approval. No tool behaviour changes in
either direction, because this contract changes no logic.

This contract is RETAINED after any terminal event, per ADR-0105 §11.2.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | f9fa602b501f80418d8a66eb9c6389a99ae64c8a |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

The ADR blob named here is the PRE-amendment body, the authority this change acts upon. The
POST-amendment digest is recorded in the lifecycle `approved` row as `adr_0105_renewed`.

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the documentation and pin paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | the amendment and its own front-matter `approved_digest` | declared |
| tools/contract/classify.py | the `ADR_0105_DIGEST` pin ONLY, which `NC-C27` compares against | declared |
| docs/contracts/CC-2026-07-19-adr-0105-state-machine.md | this contract | declared |

### prohibited_scope

| glob | why |
|---|---|
| tools/contract/lifecycle.py | the state model is AMENDED here and IMPLEMENTED under a later contract |
| tools/contract/decide.py | ST-9, ST-10 and ST-11 are NAMED here and introduced under a later contract |
| tools/contract/model.py | no type change; the schemas are text until implemented |
| tools/contract/adapters.py | no port added; the census and check-run joins are later contracts |
| tools/contract/__main__.py | the dependency fail-closed repair belongs to the implementation contract |
| tools/contract/selftest.py | no control added; this contract NAMES the inventory and does not build it |
| tests/** | NC-SM-01 through NC-SM-27 are implemented under a later contract, never here |
| .github/ci-control-registry.yml | the registry amendment is a SEPARATE contract |
| .github/workflows/** | no workflow change; enforcement remains Phase 6 |
| src/fanops/** | no runtime change |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | an accepted contract is not a mutable work log |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | a landed contract is not a mutable work log |
| docs/governance/** | the roadmap is not advanced, reordered or amended by this contract |

### expected_surfaces

| path | kind | why |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | §4.1a, §4.2 and §4.3 amended; front-matter `approved_digest` recomputed |
| tools/contract/classify.py | MODIFIED | one line, the `ADR_0105_DIGEST` pin |
| docs/contracts/CC-2026-07-19-adr-0105-state-machine.md | NEW | this contract |

### coupling

| what | must_move_with | why |
|---|---|---|
| the ADR-0105 body | the `ADR_0105_DIGEST` pin AND the ADR front-matter `approved_digest` | `NC-C27` recomputes the body digest and compares it to both. A body edit without both is red CI; a pin edit without the body is a lie about what was approved |
| the stated state count of fourteen | the enumerated ladder in the amended §4.3 | a number in prose that no reader can recount against a list is a defect. `NC-SM-11` keeps them equal |
| the control identifiers NC-SM-01..NC-SM-27 | the amended §4.3 text | an inventory living only in a contract body is one the implementation can silently shorten |
| the `census_observed` schema | the `verified` predicate of Route U | a route whose admissibility evidence has no schema cannot be verified after the fact, which is how the present defect arose |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| the witnessed route qualifies on `state == APPROVED` and `commit_id == head_sha` alone, with no permission or identity predicate | read of the exact-head branch in `gates` | f558f08d817b66bfc5afe046ff133edd0d5b9dc7 | tool:route-w-feasibility |
| `ReviewPort.approvals` returns `(commit_id, state)` pairs only and never reads a login, so the verifier cannot evaluate reviewer identity | read of `ReviewPort.approvals` | 69e1630ac4dccd0a9e2e8d4b594ed599c5582fa5 | tool:route-w-feasibility |
| `Derived` is constructed before the review and principal reads, so their failures cannot reach the field the fail-closed rule consults | read of the `run` pipeline stage order | edc5da272dda94a3b84025e5a6bdf566df5ce53e | tool:route-w-feasibility |
| `ST-7` predicates on `derived.unverifiable` alone, so a dependency diagnostic appended later decides nothing | read of the `ST-7` rule | 0a9574b0b6792d9103485f96b564642fa7bcd483 | tool:route-w-feasibility |
| `state()` returns exactly eleven states today, so adding three yields fourteen | read of the ladder and of `TERMINAL_EVENTS` | f558f08d817b66bfc5afe046ff133edd0d5b9dc7 | tool:route-w-feasibility |
| `ST-8` is the highest decision rule defined today, so ST-9, ST-10 and ST-11 extend the sequence without collision | read of the rule table | 0a9574b0b6792d9103485f96b564642fa7bcd483 | tool:route-w-feasibility |

Each row binds to the blob of the file its claim was read from, at the base commit of this change.
Citing the ADR blob for a claim about code would bind evidence to a document that cannot prove it.

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NC-C27 | python -m tools.contract selftest, NC-C27 DETECTED | proves the pin, the front matter and the live body agree, which is the ONE control this contract moves |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation |
| OB-C18 | the amendment disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent rather than silently |
| OB-LADDER | the amended §4.3 enumerates fourteen states and states the number fourteen | proves the declared count and the enumerated list agree at approval time, which NC-SM-11 then holds at implementation time |
| OB-ROUTE-W | an APPROVED review whose commit_id equals the FINAL pre-merge head, on a PR the reviewer did not author | the only merge evidence available while Route U cannot reach verified. The verifier checks state and commit_id ONLY; the non-author property is produced by GitHub refusing author self-approval, and is recorded here as platform evidence rather than as a verifier predicate |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-19T14:34:05Z | created | id=CC-2026-07-19-adr-0105-state-machine; base_sha=35cbf7fcebdd9e2b5f657a971af6c31140879123 |
