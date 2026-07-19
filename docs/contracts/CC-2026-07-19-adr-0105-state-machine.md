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
event; the census event of the next section; the external attestation that section requires; the
parent-binding proof; and, post-merge, tree fidelity.

**The algorithm, in order:**

1. Read the Route W inputs. **If any required Route W read did not complete → `unavailable`.** Stop.
2. If some review has `state == APPROVED` and `commit_id == final_pre_merge_head`: post-merge, test
   tree fidelity — fidelity fails → `fidelity_failed`; otherwise → `verified`. **Stop; no Route U
   input is read.**
3. Otherwise Route W is readable and did not qualify. Record whether any review with
   `state == APPROVED` existed at ANY head. **`COMMENTED`, `CHANGES_REQUESTED` and `DISMISSED`
   reviews are not authorization claims and are recorded as none.** Then continue to Route U.
4. Read the Route U inputs, the attestation dependency of the next section included. If any required
   Route U read did not complete → `unavailable`. Stop. An unretrievable attestation IS an
   unavailable read; it never falls back to the unattested payload.
5. No `merge_approved` event present: if step 3 recorded an APPROVED review → `claimed_stale`; if it
   recorded none → `absent`. With no APPROVED review at any head and no `merge_approved` event,
   neither route holds a claim, and the readable finding is `absent`, NOT `claimed_stale`.
6. `merge_approved` present but `parent_binds` fails at the final pre-merge head → `claimed_stale`.
7. The `census_observed` event is absent, or present and fails its schema — any key missing, any
   grammar violation, `principal_count` unequal to the token count, or `digest` unequal to the
   recomputation → `claimed_unknown`.
8. `census_observed` schema-valid but `head_sha` is not the final pre-merge head → `claimed_stale`.
9. `census_observed` schema-valid and bound to the final pre-merge head → `claimed_unknown`, whatever
   `principal_count` asserts. **This is the terminal step of Route U: the route has NO step that
   returns `verified`.**

**Decided: Route U is prospectively DORMANT and cannot reach `verified`.** A `census_observed` row is
written into the contract file by the same principal whose authority it asserts. Its `digest` is
recomputed from the `principals` string beside it, so it proves internal consistency and nothing
more: it cannot show that every writer class was enumerated, and a census omitting a whole class is
byte-perfect and self-consistent. `principal_count == 1` is an assertion, not a finding, and a
transcribed `Date` header is not the header. The former step — schema-valid plus
`principal_count == 1` yielding `verified` — let a record authorize its own merge, which is the exact
defect class this amendment exists to remove, so it is DELETED rather than tightened. `NC-SM-28`
holds this.

**What Route U would require.** A census payload may support `verified` only when it is bound to an
attestation that is (a) separately retrievable from the contract file, (b) produced by the platform
rather than by any principal who can write this repository, (c) read by the verifier itself at
verification time, and (d) validated without reference to the payload's own self-descriptive fields.
**No source satisfying all four is defined, and this contract names none.** An operator attestation,
a self-written digest, check-run prose and any artifact the same principal can author fail (b) or (d)
and are excluded BY NAME. Route U stays dormant until a LATER amendment defines a real external
attestation source; until then `claimed_unknown` is its ceiling.

**Decided: an unavailable Route W read does NOT permit Route U fallback.** If the review list cannot
be read, whether a qualifying witnessed approval exists is unknown, and falling back would accept the
weaker route precisely when the stronger one cannot be checked — an outage would silently lower the
evidence standard. This holds independently of dormancy: a fallback would also report a readable
negative where the truth is an unread input. Step 1 therefore terminates at `unavailable` → `ST-7`.
`NC-SM-21` holds this.

**Decided: a Route U input that is never read cannot downgrade a witnessed authorization.** Step 2
stops before step 4, so Route U availability is irrelevant once Route W qualifies. `NC-SM-20` holds
this by making every Route U-only input raise while a valid Route W review is present.

### the closed authorization-value set

**Six values.** `claimed_inadmissible` has been **removed**: it required positive proof that two or
more effective writers existed at the authorization instant, and no platform surface returns
historical permission state for a user-owned repository — the organization audit log does not exist
for one, and installation endpoints are unreadable. A state whose predicate cannot be evaluated must
not exist, so it does not. Its readable case is covered: while no attestation source exists EVERY
schema-valid census yields `claimed_unknown` (step 9) whatever count it asserts, and an absent or
malformed one yields `claimed_unknown` too (step 7).

**Only Route W reaches `verified`.** Both values that require a route to QUALIFY — `verified` and
`fidelity_failed`, since fidelity is tested only after a route qualifies pre-merge — are now Route
W's alone. `claimed_stale`, `claimed_unknown`, `absent` and `unavailable` are unchanged. The set
stays six because every value remains reachable; what shrinks is Route U's ceiling.

| value | exact predicate | required reads | all reads completed | pre-merge state | post-merge state | rule | outcome |
|---|---|---|---|---|---|---|---|
| verified | Route W step 2 ONLY — Route U has no step returning this value | Route W inputs only | yes | approved_for_merge | merged | OK | continue |
| claimed_stale | an APPROVED review exists but binds no final head; or `parent_binds` fails at the final head; or the census binds a head other than the final head | that route's inputs | yes | lower ladder | merged_unverified | ST-10 | stop |
| claimed_unknown | the `census_observed` event is absent, fails its schema, or is schema-valid but carries no external attestation | Route U inputs | yes | lower ladder | merged_unverified | ST-10 | stop |
| fidelity_failed | ROUTE W qualified pre-merge but PR-head tree does not equal the merged-commit tree | trees at both commits | yes | n/a | merged_unverified | ST-10 | stop |
| absent | reads completed and neither route holds a claim: no APPROVED review at any head AND no `merge_approved` event | that route's inputs | yes | lower ladder | merged_unauthorized | ST-9 | stop |
| unavailable | a required read of the route under evaluation did NOT complete, the attestation dependency included | whichever read failed | **no** | lower ladder | merged_unverified | ST-7 | stop |

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

Event kind **`census_observed`**, appended immediately before the `merge_approved` it supports. It is
a GRAMMAR for a future attested payload, not evidence — nothing below authorizes anything on its own.

| key | grammar | binds to |
|---|---|---|
| `repo_id` | decimal integer, the GitHub numeric repository id | the repository identity, which survives renaming |
| `observed_at` | `YYYY-MM-DDTHH:MM:SSZ`, transcribed from the GitHub `Date` response header of the census read | the CLAIMED observation instant — a transcription, never the header itself |
| `head_sha` | 40 lowercase hex | the final pre-merge PR head this census accompanies |
| `sources` | comma-separated fixed tokens drawn from `collaborators`, `installations`, `keys`, `workflow_permissions` | the platform surfaces actually read |
| `principals` | comma-separated `type:id` tokens, `type` one of `user`, `app`, `key`, `actions`, `team`, sorted ascending by whole token | the canonical effective-writer set |
| `principal_count` | decimal integer, equal to the token count of `principals` | the size assertion |
| `digest` | `sha256:` plus 64 hex over the exact `principals` string in UTF-8 | INTERNAL consistency of this row only — recomputed from the string beside it, and silent about completeness |

**Effective writer** means any principal able to write a ref at that instant: collaborators with
`push` (administrators included, since admin implies push); App installations with `contents: write`;
deploy keys with `read_only` false; and the Actions token when `default_workflow_permissions` is
`write` or when any workflow at `head_sha` declares `contents: write`. Teams are expanded to their
member users and additionally recorded as `team:<id>`; a user-owned repository has none.

**Post-merge binding is structural, not temporal.** `census_observed.head_sha` must equal the final
pre-merge PR head that `merge_approved` binds, and `observed_at` must not exceed the server-observed
instant of the first commit containing the census row. Both are checkable after the fact from
platform records, and neither trusts a local clock. Binding is a NECESSARY condition; it is not
evidence that the enumeration is complete.

**This schema is a grammar, not evidence.** Every key above is written into the contract file by the
principal whose authority the row asserts, so a syntactically perfect row is a well-formed CLAIM. The
`digest` detects only later editing of the `principals` string beside it; it cannot detect a writer
class that was never enumerated, and a census omitting one entirely is byte-perfect and
self-consistent. No combination of these keys authorizes anything.

**The attestation requirement.** A census payload supports `verified` only when it is bound to an
attestation that is (a) separately retrievable from the contract file, (b) produced by the platform
rather than by any principal who can write this repository, (c) read by the verifier itself at
verification time, and (d) validated without reference to the payload's own self-descriptive fields.
**This contract defines no such source and names none.** A later amendment must define a real
external attestation source before `verified` can exist on this route.

**Classification, while dormant.** *Malformed* — any key missing, any grammar violation,
`principal_count` unequal to the token count, or `digest` unequal to the recomputation →
`claimed_unknown`. *Stale* — `head_sha` is not the final pre-merge head → `claimed_stale`.
*Unattested* — every remaining case, a schema-valid row asserting `principal_count` 1 included →
`claimed_unknown`. *Unavailable* — a read needed to verify the record, or the attestation dependency,
did not complete → `unavailable`, hence `ST-7`. **There is no verified outcome.**

**Two separate blockers, and readability is the lesser one.** The census cannot be CAPTURED today,
because the App-installation surface returns 401 or 403 to every available credential. Making that
surface readable would still not make Route U verify: the captured result would be transcribed into
this file by its author, which is the defect this section removes. Readability is necessary; external
attestation is what is missing. The predicate stays fully evaluable throughout — the event is absent,
malformed, stale or unattested — so no unevaluable state is introduced.

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
5. The amended §4.1a contains the ordered algorithm above, including the disjoint input sets, both
   decisions — no Route U fallback on an unavailable Route W read, and no Route U input read once
   Route W qualifies — the rule that only a `state == APPROVED` review is a Route W authorization
   claim, and the statement that Route U has NO step returning `verified`.
6. The amended §4.1a states that the witnessed predicate is `state == APPROVED` and
   `commit_id == final_pre_merge_head` alone; that reviewer identity is not an input available to the
   verifier; that this amendment adds no identity or permission predicate; and that any non-author
   property of the review is platform-produced evidence rather than a verifier rule.
7. The amended §4.2 defines the `census_observed` and `accepted` schemas exactly as above, and states
   that the census schema is a grammar rather than evidence.
8. The amended §4.2 defines `merge_authorization` as the CLOSED six-value set above and carries the
   exhaustiveness proof.
9. The amended §4.3 defines `acceptance_verified` as A through F and states that `evidence=` carries
   no verification weight.
10. The amended text carries the obligation-to-evidence transform, including that an unmapped
    obligation fails acceptance.
11. The amended text names NC-SM-01 through NC-SM-29 and the three new rules `ST-9`, `ST-10` and
    `ST-11`, and authorizes no other new rule identifier.
12. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
13. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.
14. The amended §4.1a and §4.2 both carry the four attestation requirements, state that no source
    satisfying them is defined, and state that Route U cannot reach `verified` until a later
    amendment defines one.

### implementation controls

The later implementation contract must build every control below. **This contract builds none.**
`A5`, `OK`, `RF-3` and `ST-7` exist today; `ST-9`, `ST-10` and `ST-11` are the three the
implementation must INTRODUCE, and no other new rule id is authorized — `ST-8` is the current
maximum. Rows marked **structural** assert a property outside the decision table and name the exact
test failure they must produce.

**Twenty-nine controls.** The inventory of nineteen predated route separation, the census schema, the
accepted schema and the obligation transform; eight controls cover those. Two more cover Route U's
dormancy and the review-claim classification. The count is twenty-nine because the table below has
twenty-nine rows.

| id | injected defect or missing evidence | expected derived state | expected rule | outcome | distinct boundary proven |
|---|---|---|---|---|---|
| NC-SM-01 | an `accepted` event with `merge_authorization` not `verified` | acceptance_claimed | ST-11 | stop | a written row cannot promote to `accepted` |
| NC-SM-02 | merge on `main`, a verified route, no `accepted` event | merged | OK | continue | verification alone does not manufacture acceptance |
| NC-SM-03 | no reviews at all and no `merge_approved` event — reads complete and prove no claim exists | merged_unauthorized | ST-9 | stop | a readable negative reaches ST-9 and `ST-7` must NOT fire |
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
| NC-SM-21 | the Route W review read raises, with a complete `merge_approved` and census present | merged_unverified | ST-7 | stop | an unavailable Route W read does NOT permit Route U fallback |
| NC-SM-22 | `census_observed` with `principal_count` unequal to the token count, or a mismatched `digest` | merged_unverified | ST-10 | stop | a malformed census is `claimed_unknown`, never silently trusted |
| NC-SM-23 | two `binding` events, with `accepted.pr` naming the earlier one | acceptance_claimed | ST-11 | stop | the governed PR is the last binding, and disagreement is malformed |
| NC-SM-24 | `accepted.runs` non-ascending, containing a duplicate, or empty | acceptance_claimed | ST-11 | stop | the run set has a canonical encoding that arbitrary values cannot satisfy |
| NC-SM-25 | a verification obligation resolving to none of the three evidence forms | acceptance_claimed | ST-11 | stop | an unmapped obligation fails acceptance and is never dropped |
| NC-SM-26 | three `accepted` events, only the last of which verifies | accepted | OK | continue | the last claim is evaluated and earlier ones are historical |
| NC-SM-27 | an `accepted` event with `decision` other than the literal `accepted` | rejected at validation | A5 | stop | `decision` has exactly one permitted value |
| NC-SM-28 | a `census_observed` row passing EVERY internal check — schema valid, `digest` recomputing, `principal_count` 1, bound to the final head — with no independently retrievable server attestation | merged_unverified | ST-10 | stop | a census perfect on its own terms is `claimed_unknown`; Route U has NO path to `verified` |
| NC-SM-29 | reviews present but every one `COMMENTED`, with no `merge_approved` event | merged_unauthorized | ST-9 | stop | a non-APPROVED review is not an authorization claim, so the finding is `absent`, NOT `claimed_stale` |

### rollback

`git revert` of the single squash commit restores the previous body, the previous pin and the
previous front-matter digest together, leaving no dangling approval. No tool behaviour changes in
either direction, because this contract changes no logic.

This contract is RETAINED after any terminal event, per ADR-0105 §11.2.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | f0ebd9f0322a58b794c5275b0c8482831a5ca04a |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

**The ADR row records the operator-reconfirmed POST-amendment blob.** The original declaration cited
`f9fa602b501f80418d8a66eb9c6389a99ae64c8a`, the pre-amendment body this change acts upon; that
original binding is not erased, because **git history preserves it** — it is the value in every commit
up to and including `3f147edc6c008c1bebcb656b0649d9bdf112f356`, and `git log -p` on this file shows the
transition. The live row now names `f0ebd9f0322a58b794c5275b0c8482831a5ca04a`, the blob the operator
re-confirmed under token `APPROVE ADR-0105 RENEWED BODY DIGEST`, whose body digest
`sha256:6db101a956dc3a8479cad2281dc0c43fe0e52a234e2c29705da63774c3826e5d` is recorded in the lifecycle
`approved` row of 2026-07-19T16:55:23Z as `adr_0105_renewed`.

**Why the binding is updated rather than left stale.** ADR-0105 §4.4 makes a moved authority blob FLAG
for re-confirmation; re-confirmation happened, so the flag must be discharged by recording what was
re-confirmed. A lifecycle append cannot discharge it — `AUTH-BLOB-MOVED` compares this table against
the live blob and reads no lifecycle event — so the only truthful discharge is here. **This
terminates:** editing this contract changes `D` and requires a fresh approval of that `D`, but it does
not move the ADR blob, so the authority row it now names stays correct and no further round is
generated. The cost is exactly one re-approval, which is the point of the gate, not a bypass of it.

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
| tests/** | NC-SM-01 through NC-SM-29 are implemented under a later contract, never here |
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
| the control identifiers NC-SM-01..NC-SM-29 | the amended §4.3 text | an inventory living only in a contract body is one the implementation can silently shorten |
| the `census_observed` schema | the statement that it is a grammar rather than evidence, AND the four attestation requirements | a schema published without both reads as an authorization mechanism, which is precisely the defect corrected here |

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
| 2026-07-19T16:31:00Z | approved | digest=sha256:e5140c30cf91478c6b9ea1cea0778ece4bd441327c16c8ea6949e68ace743dcc; token=APPROVE ADR-0105 STATE-MACHINE AMENDMENT DESIGN; scope=declaration; adr_0105_authority=sha256:e757fb6e01d3e6f143f6d6af9f45bce780331562adb07149b55857baefc5875a; adr_0105_renewed=NOT_YET_RENEWED; renewal_required=the amended body does not exist at this instant so no renewed digest can be named here — a SECOND approved row carrying the recomputed amended ADR body digest is REQUIRED before head proposal, merge approval or acceptance; operator=operator; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-19T16:31:43Z | implementation_started | surfaces=docs/adr/0105-reusable-change-contract-architecture.md, tools/contract/classify.py, docs/contracts/CC-2026-07-19-adr-0105-state-machine.md; authorized_actions=design; note=recorded BEFORE any governed surface is edited; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-19T16:55:23Z | approved | digest=sha256:e5140c30cf91478c6b9ea1cea0778ece4bd441327c16c8ea6949e68ace743dcc; token=APPROVE ADR-0105 RENEWED BODY DIGEST; scope=amended-body; adr_0105_authority=sha256:e757fb6e01d3e6f143f6d6af9f45bce780331562adb07149b55857baefc5875a; adr_0105_renewed=sha256:6db101a956dc3a8479cad2281dc0c43fe0e52a234e2c29705da63774c3826e5d; operator=operator; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-19T18:23:37Z | approved | digest=sha256:c25154f036208db5c05cbfb9c36ffc3cf761b391108d546bb4f524c0c0540b9b; token=APPROVE ADR-0105 AUTHORITY-REBOUND DECLARATION; scope=authority-rebound-declaration; adr_0105_authority_blob=f0ebd9f0322a58b794c5275b0c8482831a5ca04a; adr_0105_renewed=sha256:6db101a956dc3a8479cad2281dc0c43fe0e52a234e2c29705da63774c3826e5d; operator=operator; timestamp_source=GitHub API Date response header, observed during this operation |
| 2026-07-19T18:38:54Z | head_proposed | parent_sha=e01d7ec88f7193ffab15cf1e95f27fd916a6fbf8; ci=green; ci_head=e01d7ec88f7193ffab15cf1e95f27fd916a6fbf8; verifier_pre=continue/OK; verifier_head=continue/OK; verifier_merge=stop/ST-4; pr=706 |
