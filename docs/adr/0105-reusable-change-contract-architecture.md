---
status: accepted
date: 2026-07-18
accepted_in_principle: 2026-07-18
approved_digest: sha256:6db101a956dc3a8479cad2281dc0c43fe0e52a234e2c29705da63774c3826e5d
supersedes: []
references: [0100, 0101, 0102]
deciders: [operator]
---

# ADR-0105 — Reusable Change-Contract Architecture

> **Scope.** This ADR defines the **model** for a per-change authorization and verification artifact.
> It ships no executable, no schema file, no check and no workflow. The compiler and verifier are
> Phase 3 (`docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md`).

## Status

**Accepted** (in principle, 2026-07-18). Phase 2 of the Agent Change System program. Entry criterion
met: Phase 1 `ACCEPTED` (PR #701, `937777d`).

**Amendment, 2026-07-19 — §4.1a and operator decision 3.** Phase 3B implementation proved that the
exact-head gate as originally specified is **unsatisfiable in this repository**, and that the same
self-reference defeats `head_proposed` in every repository. §4.1a replaces relocation-of-the-record
with **parent-binding**, and adds an explicitly-bounded second evidence route for repositories with a
single write principal. Multi-principal behaviour is unchanged. **This amendment moved the body, so it
requires renewed approval; the `approved_digest` above names the amended body, which is
`REQUESTED / NOT YET APPROVED` until the operator answers the Phase 3B governance-amendment gate.**

**What this acceptance binds to.** `approved_digest` in the front matter is `sha256` over this file's
**body** — every byte after the front matter's closing `---` line, including the newline that follows
it. The digest excludes the front matter so that it can live inside the file it describes without
self-reference. Reference implementation:

```python
import hashlib, pathlib
raw = pathlib.Path("docs/adr/0105-reusable-change-contract-architecture.md").read_bytes()
print("sha256:" + hashlib.sha256(raw.split(b"\n---\n", 1)[1]).hexdigest())
```

**Any byte change to the body** — during implementation, review, or later — changes the digest and
**invalidates this architecture approval, requiring renewed approval.** There is no typographical
exception: a whitespace, spelling or broken-link fix changes the digest and requires re-approval like
any other edit. The **only** permitted unreviewed operation is mechanically inserting the digest of the
already-approved body into the front matter, which by construction leaves the body untouched. The body
is never altered to accommodate the digest. Correcting a digest to match an edited body without
re-approval is a governance violation, not a fix.

## Context

An agent starting work here cannot answer, from any single artifact, *"may I make this change, and how
will I know I made it correctly?"* The authorities exist and are now truthful (Phase 1), but they are
**general**: `LAW-*` states what is always true, `STD-*` how code is written, the control registry what
CI always runs. None resolves to **this** change.

The gap has a recorded cost: silent scope expansion; re-auditing settled facts every session;
implementing when only design was authorized; and reading a completed historical program contract as
the universal contract for all future work.

**What already exists and must not be duplicated:**

- **Precedence** — `docs/REPOSITORY_CONSTITUTION.md` C2.1 (line 37), inherited from ADR-0100.
- **Non-duplication discipline** — `docs/ENGINEERING_STANDARDS.md:32`: *"No second registry. CI
  controls are referenced by `id`, never restated."*
- **Declared-vs-live two-plane device** — `.github/ci-control-registry.yml`
  `current_required_contexts` vs `intended_required_contexts` (lines 33–48).
- **Risk-priced verification** — `.orchestration/SPEC.md:47`: an independent record is bought only
  where wrongness is expensive (a `lanes.json` hot file, >5 files, or an unverifiable file list).
- **Head-binding of evidence** — the same SPEC: a record's `head_sha` must equal the PR's current
  `headRefOid`; stale → refused.
- **Legitimate-redundancy test** — registry `duplicate_groups` + `distinct_boundaries`, enforced by
  `DC-5` (`tools/ci/checks.py:115`).
- **Impact classification** — `tools/arch/impact.py`; **verification selection** —
  `tools/arch/verifymap.py::required_for`.
- **Commit-SHA-bound human approval** — GitHub pull-request reviews already bind to a `commit_id`.

A Change Contract is therefore **not a new governance system**. It is a per-change *resolution* of
existing authorities, plus two things none of them provide: a **declared scope the diff can be checked
against**, and a **lifecycle whose gates bind to immutable digests and SHAs**.

## Decision

### 1 · What a Change Contract is, and when one is required

A **Change Contract** governs the authorization and verification envelope of **one** change, from
design through acceptance. It is task-specific, binding, and written **before** implementation.

**A contract is REQUIRED when any of `T1`–`T6` holds.**

---

**T1 — Multi-subsystem span.**

- **Predicate:** the set `{ subsystem_of(module_of(p)) : p ∈ changed_paths, module_of(p) ≠ ⊥ }` has
  cardinality > 1.
- **Evidence source:** `.reports/architecture/derived/modules.json` `subsystem_of` (total, derived,
  byte-verified), via `git diff --name-only <base>...<head>`.
- **Derivability:** **mechanically derivable**, once the path→module transform exists (gap **G2**).
- **Why it requires a contract:** a change inside one boundary is already governed by that boundary's
  laws and by required CI. The moment it spans boundaries, no single owner's rules cover it, and the
  question *"who else breaks"* has no default answer.
- **False positive:** a mechanical rename or comment sweep touching many subsystems with zero semantic
  coupling. It will demand a contract it does not need.
- **False negative:** a single-file change inside one subsystem that alters a contract every other
  subsystem depends on (a shared enum, a ledger field). Span is a proxy for coupling and this is where
  the proxy fails. `T2` is the intended backstop; where impact also reads it as compatible, **neither
  fires** and the change is uncontracted. This is the model's weakest trigger, stated plainly, and it
  is an **accepted Phase 4 calibration risk** — the trigger is deliberately not broadened to cover it.

---

**T2 — Architectural impact above compatible.**

- **Predicate:** `classification ∈ {MIGRATION_REQUIRED, BREAKING_CHANGE, UNKNOWN_IMPACT}`.
- **Evidence source:** `python -m tools.arch impact --base <sha>` (`tools/arch/impact.py:26-32`).
- **Derivability:** **mechanically derivable**; today the CLI emits Markdown only (gap **G1**).
- **Why:** these three classes are the tool's own statement that the change is not locally containable.
  `UNKNOWN_IMPACT` is included deliberately — *"the analyzer could not tell"* is a reason to slow down,
  not to proceed.
- **False positive:** `UNKNOWN_IMPACT` raised by an extractor limitation (`derived/unsupported.json`)
  rather than by real risk.
- **False negative:** impact reads only the dimensions it populates; **five of thirteen are initialized
  and never written** (gap **G4**). A change whose only architectural effect lands in a dead dimension
  classifies as `COMPATIBLE_CHANGE` and `T2` stays silent.

---

**T3 — Governance surface.**

- **Predicate:** any changed path matches `docs/REPOSITORY_CONSTITUTION.md`,
  `docs/ARCHITECTURAL_LAWS.md`, `docs/ENGINEERING_STANDARDS.md`, `docs/adr/**`,
  `docs/governance/**`, `.github/ci-control-registry.yml`, `.github/workflows/**`, `tools/arch/**`,
  `tools/ci/**`, `tools/contract/**`, `.agents/lanes.json`, `.orchestration/**`.
- **Evidence source:** the diff path list against that literal set, which lives **only here**.
- **Derivability:** **mechanically derivable** (pure path match).
- **Why:** these files are what every other change is judged against. Changing the ruler is not the
  same kind of act as changing what it measures, and it is the one class where a wrong change is
  invisible afterwards — because the thing that would have caught it is what changed.
- **False positive:** a typographical fix in a governance document. Real, accepted: see the worked
  example in §5, where the resulting obligation set is genuinely small.
- **False negative:** a governance-equivalent file outside the list — a new validator added under a
  path not enumerated. The list is hand-maintained and has no mechanical completeness proof. **Adding
  a governance surface must add it here in the same change.** Hand-maintenance is accepted for Phase 2;
  mechanical completeness verification is Phase 3 work.
- **`docs/contracts/**` is CONDITIONALLY excluded, not blanket-excluded** — see §3.6.

---

**T4 — Live or destructive action.**

- **Predicate:** the change executes, or is a prerequisite for executing, any of: a ledger mutation; a
  publish; an external-service call (Postiz, Meta Graph, Zernio, TikTok); an account or Persona
  mutation; a branch-protection or repository-settings mutation; a secret rotation; or a **deletion
  that removes or mutates externally durable or live persisted state** — a ledger, an account,
  published material, an external-service record, a secret, or a repository setting.
- **Deletion boundary:** deleting a **git-tracked repository file** is **not** `live` merely because it
  is a deletion. Git retains the content and the change is revertable, which is exactly the property
  `live` exists to flag the absence of. Such a deletion may still set other traits or fire other
  triggers (`T1`, `T2`, `T3`, `T5`), and it is judged by those.
- **Evidence source:** **human-declared**, corroborated by `derived/side_effects.json` and by the
  change's own runbook.
- **Derivability:** **human-declared.** `side_effects.json` censuses where side-effect *code* lives; it
  cannot know that *this task* intends to run it. No tool distinguishes "edits the publish path" from
  "publishes".
- **Why:** it is the only class where being wrong is not recoverable by reverting a commit.
- **False positive:** an agent marks a read-only probe as live out of caution. Cheap and preferred.
- **False negative:** the load-bearing risk of the whole model — an agent that does not realize its
  action is live. Mitigated only by the rule that any doubt resolves to `T4`, and by §10's requirement
  that a live action carry a separate execution gate an operator must give.

---

**T5 — Breadth or hot-file contact.**

- **Predicate:** the changed-file set touches a `lanes.json` hot file, **or** contains more than five
  files, **or** cannot be enumerated (fail closed).
- **Evidence source:** `.agents/lanes.json`; `git diff --name-only`. **Reused verbatim from
  `.orchestration/SPEC.md:47`** — not a new threshold.
- **Derivability:** **mechanically derivable**.
- **Why:** the repository has already calibrated this exact threshold as the point where independent
  verification is worth buying, with a recorded rationale (CI cannot catch an implementer grading their
  own homework). Reusing a calibrated number beats inventing one.
- **False positive:** a six-file mechanical rename.
- **False negative:** a five-file change of deep consequence. `T1`/`T2` are the intended backstops.
- **Note:** the >5 count includes `generated-consequence` files, which can push a two-file semantic
  change over the line. Accepted: over-triggering is the cheap direction.

---

**T6 — Operator requirement.**

- **Predicate:** the operator requires a contract for this task.
- **Evidence source:** the operator's instruction.
- **Derivability:** **human-declared**.
- **Why:** the trigger set is a heuristic over a space no predicate covers completely. `T6` is the
  escape hatch that keeps `T1`–`T5` honest instead of forcing them to be over-broad.
- **False positive / negative:** none — it is definitional.

---

**No contract is required when none of `T1`–`T6` holds.** A contained change inside one subsystem is
governed by the existing laws and required CI, which already work. **This is the default path and it
must stay free.** A system demanding a contract for every change gets routed around, and a routed-around
control is worse than none, because it also lies.

**A Change Contract does not govern:** how code is written (`STD-*`), what the architecture is
(`LAW-*`), what CI runs (the registry), why a standing decision was made (an ADR), where the program
stands (the roadmap), or any runtime behaviour.

| Artifact | Answers | Lifetime | Binds |
|---|---|---|---|
| **ADR** | *why* this decision — forever | permanent | future decisions |
| **Change Contract** | *whether and how* this one change may proceed | one change | this change only |
| Implementation plan | *what steps*, in what order | one change | nothing |
| Issue | *what is wrong* | until fixed | nothing |
| PR description | *what changed*, for a reviewer | one PR | nothing |
| CI configuration | *what is checked* — always | permanent | every change |
| Architecture report | *what is true* now | a snapshot | nothing |

The contract is the only one that is simultaneously **binding, task-specific, and written in advance**.
An ADR binds but is not task-specific; a plan is task-specific but binds nothing.

**Four structural commitments keep this from becoming another governance system:**

1. **Reference-only.** A contract cites `C*`, `LAW-*`, `STD-*`, control ids and `ADR-NNNN` **by id**,
   never restating them (`ENGINEERING_STANDARDS.md:32`).
2. **Conditional.** Required only on `T1`–`T6`.
3. **No index, no registry.** Identity is the contract `id`; uniqueness is filename uniqueness;
   discovery is `git log` and the file tree.
4. **It expires.** After `accepted`, a contract is a historical record. **A Change Contract never
   becomes precedent.** Only laws, standards and ADRs stand.

### 2 · Authority model

A contract **resolves** authority for one change. It never creates authority.

**Resolution order is inherited, not invented** — Constitution C2.1: *executable source & tests → live
GitHub configuration → accepted ADRs & registries → generated docs → historical prose.*

**One rule C2.1 does not cover — task-specific human authorization is narrowing-only.** An operator
instruction may **remove** authorization. It may **not grant** what a law forbids. Exceeding a law is
an amendment (Constitution C18.1 → an ADR) or the rule's existing `exception_process` — never a
contract field. Without this, *"the operator approved it"* becomes a universal law-bypass.

| Conflict | Resolution |
|---|---|
| Authorities at **different** precedence levels disagree | Higher wins (C2.1). The contract **records** the conflict — evidence of documentation rot (`LAW-SOT-01`). |
| Authorities at the **same** level disagree | The agent **must not choose**. **Escalate.** |
| An authority is **silent** | Silence is **not** authorization for anything in `T1`–`T6`. For a change outside the trigger set, silence plus green required CI is sufficient. |

### 3 · Contract structure

A contract is **one file** with **two parts**, separated by a single byte-exact boundary.

```text
docs/contracts/<id>.md
├── DECLARATION  — file start … up to (excluding) the line "## Lifecycle"
│                  Frozen at approval. Editing it voids approval.
└── LIFECYCLE    — the line "## Lifecycle" … end of file
                   Append-only. Appending never voids approval.
```

**Declaration digest** `D` = `sha256` over the declaration byte range — every byte from the start of
the file up to, and excluding, the first line that is exactly `## Lifecycle`:

```python
import hashlib, pathlib
raw = pathlib.Path("docs/contracts/<id>.md").read_bytes()
print("sha256:" + hashlib.sha256(raw.split(b"\n## Lifecycle\n", 1)[0]).hexdigest())
```

This split is the mechanism that removes the circularity of storing lifecycle state inside an
approval-bound artifact: **approval binds to `D`, and no lifecycle event changes `D`.**

#### 3.1 Declaration fields — 18

Sources: **H** human-declared · **R** repository-derived · **T** tool-derived · **C** conditional.
*Today* is the honest derivability state at `937777d`, and is the input to Phase 3.

| # | Field | Source | Required | Today |
|---|---|---|---|---|
| 1 | `id` — §6 | H | mandatory | ✅ |
| 2 | `objective` | H | mandatory | ✅ |
| 3 | `success_condition` — **falsifiable** | H | mandatory | ✅ |
| 4 | `traits` — §5 axis A, a set | C (`cross-system` T; `governance` T; `live` H) | mandatory | ⚠️ G1, G2 |
| 5 | `authority` — governing `C*` / `LAW-*` / `STD-*` / control / `ADR-NNNN` ids, each with the blob SHA of its file at approval | R | mandatory | ✅ |
| 6 | `owners` — affected subsystem ids, **full form** | T (`subsystem_of`) | mandatory | ⚠️ G2 |
| 7 | `allowed_scope` — path globs, each with why it is in scope | H | mandatory | ✅ |
| 8 | `prohibited_scope` — explicitly out, including near-misses | H | mandatory | ✅ |
| 9 | `authorized_actions` ⊆ {design, implement, merge, apply-live, accept} | H | mandatory | ✅ |
| 10 | `expected_surfaces` — files this change intends to touch | H, checked vs diff | mandatory | ✅ |
| 11 | `incidental_allowlist` — paths permitted but not intended | H | optional | ✅ |
| 12 | `blast_radius` — modules reachable from the change | C | if `cross-system` | ❌ G3 |
| 13 | `invariants` — `LAW-*` / `C*` ids the change bears on | C | mandatory | ⚠️ prose-only |
| 14 | `coupling` — what must move together | H + R | conditional | ✅ |
| 15 | `reusable_evidence` — `{claim, proven_by, proven_at, binding}` | H | optional | ✅ |
| 16 | `verification` — obligations, by control `id` and requirement | T (`verifymap`) + §9 | mandatory | ⚠️ G4 |
| 17 | `rollback` — how this is undone, and at what cost | H | mandatory | ✅ |
| 18 | `stop_conditions` — **task-specific additions only**; the universal ladder is §10 | H | optional | ✅ |

Plus, in front matter and **inside** the declaration: `supersedes` (§6).

#### 3.2 What is deliberately *not* a declaration field

- **`status`.** Lifecycle state is **derived** (§4.3), never declared. A declared status is a claim
  that rots; a derived one cannot.
- **`branch`, `pr`, `base_sha`, `head_sha`.** These change during the lifecycle (§6). They are
  lifecycle bindings.
- **`out_of_scope_findings`.** Append-only by nature ⇒ lifecycle (§3.3).
- **Approval records.** Lifecycle events (§4.2).

#### 3.3 Lifecycle section

Append-only. Never rewritten. Never reordered. Contains, in order of occurrence: **bindings**
(`branch`, `pr`, `base_sha`, `head_sha` — appended, superseded by later appends, never edited in
place), **events** (§4.2), and **`out_of_scope_findings`** — discoveries recorded, *not fixed* (§5).

#### 3.4 Fields merged away from the requested set, and why

- **`implementation boundary` → renamed and redefined as `authorized_actions` (#9).** As "the edge of
  allowed scope" it was a third name for `allowed_scope` + `prohibited_scope`. As "which lifecycle
  actions are authorized" it is distinct and load-bearing — the field whose absence produces *"the
  agent implemented when only design was approved."*
- **`refusal conditions` + `escalation conditions` → folded into `stop_conditions` (#18).** The ladder
  is universal (§10); copying it into every contract is the duplication this repository already banned.
- **`evidence invalidation conditions` → hoisted to §8 as a rule.** The rule is universal; the
  `binding` is per-evidence.
- **`approval state` + `acceptance state` → the lifecycle record (§4).** Two declared fields describing
  one position in a state machine is what created the circularity this ADR fixes.

#### 3.5 Fields added beyond the requested set, and why

- **`id` (#1)** — nothing can reference a contract without a stable identity.
- **`traits` (#4)** — the dispatch key; every other obligation is selected by it.
- **`incidental_allowlist` (#11)** — makes §5 axis B computable. Without it, `incidental` is a
  post-hoc excuse rather than an advance declaration.
- **`out_of_scope_findings`** (lifecycle) — the mechanism that makes *"record findings, do not fix
  them"* survive a session boundary.

#### 3.6 Contract-file governance rule

`docs/contracts/**` is **conditionally** outside `T3`. The distinction prevents recursive contracts
without letting authorization records be silently rewritten.

**Does NOT trigger `T3` by itself:**

- creating a new contract in the prescribed format;
- appending a valid lifecycle event after the `## Lifecycle` boundary.

**Governance-sensitive — triggers `T3`, and the applicable governance obligations must be satisfied:**

- editing an existing declaration;
- moving or altering the declaration/lifecycle boundary;
- changing declaration-digest semantics;
- rewriting, deleting or reordering lifecycle history;
- changing the contract storage or lifecycle convention.

**The asymmetry is deliberate: writing the record is routine; rewriting the record is governance.**
An append is monotone and auditable — history only grows, and nothing already written changes meaning.
Every listed governance-sensitive operation is non-monotone: each can make an earlier authorization say
something it did not say at the time it was given. A contract whose declaration can be quietly edited,
or whose lifecycle can be reordered, is evidence of nothing.

Editing a declaration therefore has two independent consequences, and both apply: it changes `D` and
voids approval (§4.4), **and** it is a `T3` governance change in its own right.

### 4 · Lifecycle

#### 4.1 The three gates, and what each binds to

| Gate | Binds to | Recorded where | Moves the head? |
|---|---|---|---|
| **Content approval** (`approved`) | the **declaration digest `D`** | in-file, `## Lifecycle`, in the same commit that freezes the declaration | Yes — and it does not matter: it precedes implementation, and it is not what merge approval binds to. |
| **Exact-head approval** (`merge_approved`) | the triple **(`D`, `parent_sha`, blob SHA of the contract at `parent_sha`)** | **a GitHub pull-request review** where a second principal exists; otherwise **in-file, parent-bound** (§4.1a) | **No, in either form** — a review is not a commit, and a parent-bound record names the commit it is appended *onto*. |
| **Acceptance** (`accepted`) | the **resulting `main` SHA** plus a demonstration of `success_condition` | in-file, appended **after** the merge, in a separate commit | Yes — post-merge, so nothing downstream depends on the head it moves. |

**This is the answer to the circularity.** Exact-head approval is the only gate that must not move the
head. Where a second principal exists it is recorded outside the tree — in a mechanism (a PR review)
that already binds to a commit SHA and that GitHub makes immutable and auditable.

#### 4.1a Parent-binding, and the two evidence routes (amends the original §4.1)

**The defect this amends.** The original §4.1 solved a *self-reference* problem by *relocating* the
record: a record written into the tree cannot name the commit that contains it, because the commit
hash is computed over the record. Relocating it to a GitHub PR review dodged that — and silently
imported an unstated precondition, that **a second account with write access exists and will act**.
Where it does not, no evidence of any kind is admissible and the gate is unsatisfiable. The same
self-reference defeats `head_proposed` (§4.2), which was never relocated and therefore made the
`implemented` state (§4.3) **unreachable by construction**. Two instances, one root cause.

**The correction.** A record binds to its **parent** — the commit it is appended onto — and the
verifier proves the rest from git:

1. `parent_sha` is an ancestor of the current head;
2. no path other than this contract file moved between them;
3. the declaration bytes are identical at both;
4. the head's lifecycle byte-prefix-extends the parent's.

Together these establish that head and parent differ by lifecycle appends to this contract and by
nothing else — a delta that the §3 byte split already proves cannot change `D`, the code, or any
cited authority. **Approving the parent and merging the head therefore approve the same change.** This
is the §3 argument applied to the head instead of the digest; it is not a new principle, and it is
checked, never assumed.

**This document is itself the precedent.** §Status escapes exactly this self-reference by excluding
the front matter from `approved_digest`, "so that it can live inside the file it describes without
self-reference". §3 escapes it again by excluding the lifecycle from `D`. Both define *what the record
covers* so the record can sit inside its own subject. §4.1a is the third application: define what the
approval covers — the parent, plus a delta proven inert — instead of moving the record somewhere it
cannot cover itself. **Relocation was the one escape of the three that depended on a fact outside the
repository, and it is the one that broke.**

**Two evidence routes, disjoint input sets, and which applies is not a choice.**

| Route | Evidence | Ceiling |
|---|---|---|
| **W — witnessed** | a PR review whose `state` is `APPROVED` and whose `commit_id` equals the FINAL pre-merge head | `verified`; **tried first, and preferred whenever it succeeds** |
| **U — unwitnessed** | an in-file `merge_approved` event satisfying the four parent-binding checks, plus an ATTESTED writer census | `claimed_unknown` — **dormant; see below** |

**Route W inputs, and only these:** governed PR identity; the final pre-merge PR head; the review
list; each review's `state`; each review's `commit_id`; and, post-merge only, the tree-fidelity
inputs. A qualifying witnessed review reaches `verified` **without reading** an in-file
`merge_approved` event, without any writer-census evidence, and without any Route U admissibility
evidence.

**Route U inputs, and only these:** the final pre-merge PR head; the parent-bound `merge_approved`
event; the `census_observed` event of §4.2b; the external attestation §4.2b requires; the
parent-binding proof; and, post-merge, tree fidelity.

**The ordered algorithm.** `merge_authorization` (§4.2a) is computed by evaluating **Route W first, to
completion, before any Route U input is read**.

1. Read the Route W inputs. **If any required Route W read did not complete → `unavailable`.** Stop.
2. If some review has `state == APPROVED` and `commit_id == final_pre_merge_head`: post-merge, test
   tree fidelity — fidelity fails → `fidelity_failed`; otherwise → `verified`. **Stop; no Route U
   input is read.**
3. Otherwise Route W is readable and did not qualify. Record whether any review with
   `state == APPROVED` existed at ANY head. **`COMMENTED`, `CHANGES_REQUESTED` and `DISMISSED`
   reviews are not authorization claims and are recorded as none.** Then continue to Route U.
4. Read the Route U inputs, the attestation dependency of §4.2b included. If any required Route U
   read did not complete → `unavailable`. Stop. An unretrievable attestation IS an unavailable read;
   it never falls back to the unattested payload.
5. No `merge_approved` event present: if step 3 recorded an APPROVED review → `claimed_stale`; if it
   recorded none → `absent`. With no APPROVED review at any head and no `merge_approved` event,
   neither route holds a claim, and the readable finding is `absent`, NOT `claimed_stale`.
6. `merge_approved` present but `parent_binds` fails at the final pre-merge head → `claimed_stale`.
7. The `census_observed` event is absent, or present and fails its §4.2b schema — any key missing,
   any grammar violation, `principal_count` unequal to the token count, or `digest` unequal to the
   recomputation → `claimed_unknown`.
8. `census_observed` schema-valid but `head_sha` is not the final pre-merge head → `claimed_stale`.
9. `census_observed` schema-valid and bound to the final pre-merge head → `claimed_unknown`, whatever
   `principal_count` asserts. **This is the terminal step of Route U: the route has NO step that
   returns `verified`.**

**The witnessed predicate is `state` and `commit_id` ALONE.** The verifier tests
`state == APPROVED` and `commit_id == final_pre_merge_head`, and nothing else. **Reviewer identity is
not an input available to it** — the review port returns `(commit_id, state)` pairs and never reads a
login. This amendment adds NO identity predicate and NO permission predicate. The non-author property
of a qualifying review is produced by GitHub refusing an author's self-approval; it is
platform-produced evidence, recorded as such, never a rule the verifier evaluates. The earlier
wording "a non-author `APPROVED` PR review" described a property of the evidence as though it were a
check, and is corrected here.

**Route U is prospectively DORMANT and cannot reach `verified`.** A `census_observed` row is written
into the contract file by the same principal whose authority it asserts. Its `digest` is recomputed
from the `principals` string beside it, so it proves internal consistency and nothing more: it cannot
show that every writer class was enumerated, and a census omitting a whole class is byte-perfect and
self-consistent. `principal_count == 1` is an assertion, not a finding, and a transcribed `Date`
header is not the header. The former rule — the platform "reports exactly one principal with push
access", in practice satisfied by a self-written row — let a record authorize its own merge, and it
is **DELETED rather than tightened**.

**What Route U would require.** A census payload may support `verified` only when it is bound to an
attestation that is (a) separately retrievable from the contract file, (b) produced by the platform
rather than by any principal who can write this repository, (c) read by the verifier itself at
verification time, and (d) validated without reference to the payload's own self-descriptive fields.
**No source satisfying all four is defined, and this ADR names none.** An operator attestation, a
self-written digest, check-run prose and any artifact the same principal can author fail (b) or (d)
and are excluded BY NAME. Route U stays dormant until a LATER amendment defines a real external
attestation source; until then `claimed_unknown` is its ceiling.

**Decided: an unavailable Route W read does NOT permit Route U fallback.** If the review list cannot
be read, whether a qualifying witnessed approval exists is unknown, and falling back would accept the
weaker route precisely when the stronger one cannot be checked — an outage would silently lower the
evidence standard. This holds independently of dormancy: a fallback would also report a readable
negative where the truth is an unread input. Step 1 therefore terminates at `unavailable`, and the
decision stops at `ST-7`.

**Decided: a Route U input that is never read cannot downgrade a witnessed authorization.** Step 2
stops before step 4, so Route U availability is irrelevant once Route W qualifies.

**What the unwitnessed route does not have, stated plainly.** It carries no second principal's
judgement, and no amount of verification can manufacture one. The original text made it admissible
wherever such judgement was *structurally unavailable*; the correction above is that **the repository
was allowed to establish that unavailability about itself**, which is not evidence. Until an external
attestation exists the route is admissible NOWHERE, and the verifier says so by name —
`claimed_unknown`, disclosed above the fold. **A governance system that degrades silently has been
bypassed; one that degrades loudly and durably has been honest about what it can prove.**

**Why leaving Route U dormant is not "inoperative".** In a single-principal repository the operator's
own word is *already* the sole evidence behind content approval (§4.1 row 1) and acceptance (row 3),
both of which are in-file records an agent writes, and the original text rejected leaving the merge
gate unsatisfiable on the grounds that a governance system which can never authorize a merge in the
repository it governs fails **inoperative** rather than safe. That reasoning stands, and **Route W
discharges it**: an `APPROVED` review at the final pre-merge head is reachable here today and needs no
permission grant. The gate is operative through the route carrying real evidence, while the route
carrying only self-assertion is held dormant rather than deleted, so a future attestation source can
activate it without re-litigating the model.

#### 4.2 Lifecycle events

Append-only. Each carries a UTC timestamp and its binding.

| Event | Appended by | Carries | Moves head |
|---|---|---|---|
| `created` | agent | `id`, `base_sha` | yes |
| `approved` | operator, recorded by agent | `D`, the approval token | yes |
| `binding` | agent | `branch`, `pr` — re-appended whenever either changes | yes |
| `implementation_started` | agent | — | yes |
| `head_proposed` | agent | **`parent_sha`**, required-CI result at that head, verifier result | yes |
| `merge_approved` | **operator** | a GitHub PR review at `commit_id`, **or** in-file **`parent_sha`** (§4.1a) | **no** |
| `census_observed` | agent | `repo_id`, `observed_at`, `head_sha`, `sources`, `principals`, `principal_count`, `digest` (§4.2b) | yes |
| `merged` | derived | the squash SHA on `main` | n/a |
| `accepted` | operator, recorded by agent | `merge_sha`, `decision`, `date`, `operator`, `runs`, `pr` (§4.2c) | yes (post-merge) |
| `refused` | agent **or** operator | reason | yes |
| `superseded` | operator | successor `id` | yes |
| `abandoned` | operator | reason | yes |

`merged` and `accepted` are appended together in the single post-merge commit. The file is written in
three logical stages: creation-and-approval, any pre-merge appends, and the post-merge record.

##### 4.2a `merge_authorization` — the closed value set

The §4.1a algorithm returns `merge_authorization`. It has **exactly six values** and no others.

| value | exact predicate | required reads | all reads completed | pre-merge state | post-merge state | rule | outcome |
|---|---|---|---|---|---|---|---|
| `verified` | Route W step 2 ONLY — Route U has no step returning this value | Route W inputs only | yes | `approved_for_merge` | `merged` | OK | continue |
| `claimed_stale` | an APPROVED review exists but binds no final head; or `parent_binds` fails at the final head; or the census binds a head other than the final head | that route's inputs | yes | lower ladder | `merged_unverified` | ST-10 | stop |
| `claimed_unknown` | the `census_observed` event is absent, fails its schema, or is schema-valid but carries no external attestation | Route U inputs | yes | lower ladder | `merged_unverified` | ST-10 | stop |
| `fidelity_failed` | ROUTE W qualified pre-merge but the PR-head tree does not equal the merged-commit tree | trees at both commits | yes | n/a | `merged_unverified` | ST-10 | stop |
| `absent` | reads completed and neither route holds a claim: no APPROVED review at any head AND no `merge_approved` event | that route's inputs | yes | lower ladder | `merged_unauthorized` | ST-9 | stop |
| `unavailable` | a required read of the route under evaluation did NOT complete, the attestation dependency included | whichever read failed | **no** | lower ladder | `merged_unverified` | ST-7 | stop |

**Only Route W reaches `verified`.** Both values that require a route to QUALIFY — `verified` and
`fidelity_failed`, since fidelity is tested only after a route qualifies pre-merge — are Route W's
alone. `claimed_stale`, `claimed_unknown`, `absent` and `unavailable` are reachable as shown. The set
is six because every value remains reachable; what §4.1a shrank is Route U's ceiling.

**Exhaustiveness.** The six values partition into three disjoint groups: `{verified}` → rank 6;
`{claimed_stale, claimed_unknown, fidelity_failed, unavailable}` → rank 7; `{absent}` → rank 8. The
groups are disjoint and their union is the whole set, so once the governed merge commit is on `main`,
exactly one of ranks 6, 7 and 8 matches. **No value falls through to `approved_for_merge`,
`implemented` or lower after the merge is known to exist.**

**`claimed_inadmissible` is deliberately absent.** Such a value would require positive proof that two
or more effective writers existed at the authorization instant, and no platform surface returns
historical permission state for a user-owned repository — the organization audit log does not exist
for one, and installation endpoints are unreadable. **A state whose predicate cannot be evaluated must
not exist.** Its readable case is covered: while no attestation source exists, EVERY schema-valid
census yields `claimed_unknown` (step 9) whatever count it asserts, and an absent or malformed one
yields `claimed_unknown` too (step 7).

**Readable negatives and unavailable evidence stay distinct.** `absent` means reads succeeded and
proved nothing admissible exists — reported through `ST-9`, with `ST-7` NOT firing. `unavailable`
means a read did not complete — reported through `ST-7`, and never recorded as `absent`, which would
convert an outage into a proven governance failure. Under `unavailable`, derivation still runs and
yields `merged_unverified`; it can never yield `merged` or `accepted`. The decision independently
stops at `ST-7`, and the report carries both.

##### 4.2b `census_observed` — schema, and why it is not evidence

Appended immediately before the `merge_approved` it supports. It is a **GRAMMAR for a future attested
payload, not evidence** — nothing below authorizes anything on its own.

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
self-consistent. **No combination of these keys authorizes anything.** The attestation requirement is
stated in §4.1a and is not restated as satisfiable here.

**Classification, while dormant.** *Malformed* — any key missing, any grammar violation,
`principal_count` unequal to the token count, or `digest` unequal to the recomputation →
`claimed_unknown`. *Stale* — `head_sha` is not the final pre-merge head → `claimed_stale`.
*Unattested* — every remaining case, a schema-valid row asserting `principal_count` 1 included →
`claimed_unknown`. *Unavailable* — a read needed to verify the record, or the attestation dependency,
did not complete → `unavailable`, hence `ST-7`. **There is no verified outcome.**

**Two separate blockers, and readability is the lesser one.** The census cannot be CAPTURED today,
because the App-installation surface returns 401 or 403 to every available credential. Making that
surface readable would still not make Route U verify: the captured result would be transcribed into
the contract file by its author, which is the defect this section removes. Readability is necessary;
external attestation is what is missing. The predicate stays fully evaluable throughout — the event is
absent, malformed, stale or unattested — so no unevaluable state is introduced.

##### 4.2c `accepted` — schema

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
`acceptance_claimed`. **Grammar failures are parse-time; semantic mismatches are state-time.**

#### 4.3 Derived lifecycle state

State is **computed**, never declared. The ladder is **first match wins**, evaluated top to bottom.
Rank is precedence: a lower rank is reached only when every rank above it fails. There are
**fourteen** states.

| # | state | complete predicate |
|---|---|---|
| 1 | `refused` | a `refused` event is present |
| 2 | `superseded` | a `superseded` event is present |
| 3 | `abandoned` | an `abandoned` event is present |
| 4 | `accepted` | an `accepted` event is present AND the governed merge commit exists on `main` AND `accepted.merge_sha` equals that actual governed merge commit AND `merge_authorization == verified` AND `acceptance_verified` |
| 5 | `acceptance_claimed` | an `accepted` event is present AND rank 4 does not hold — the merge is not on `main`, or `accepted.merge_sha` names a commit that is not the governed merge, or `merge_authorization != verified`, or NOT `acceptance_verified` |
| 6 | `merged` | the governed merge commit is on `main` AND `merge_authorization == verified` |
| 7 | `merged_unverified` | the governed merge commit is on `main` AND `merge_authorization` is one of `claimed_stale`, `claimed_unknown`, `fidelity_failed`, `unavailable` |
| 8 | `merged_unauthorized` | the governed merge commit is on `main` AND `merge_authorization == absent` |
| 9 | `approved_for_merge` | `merge_authorization == verified` at the current head |
| 10 | `implemented` | a `head_proposed` event binds the current head (§4.1a) AND CI is green |
| 11 | `in_implementation` | an `approved` event names the current `D` AND an `implementation_started` event is present |
| 12 | `approved` | an `approved` event names the current `D` |
| 13 | `in_review` | a PR is open AND all mandatory fields for the declared traits are present |
| 14 | `draft` | otherwise |

**The count is fourteen, and no earlier state was removed.** The previous ladder had eleven.
`acceptance_claimed`, `merged_unverified` and `merged_unauthorized` are added, so 11 + 3 = 14.
`merged` and `accepted` are **NARROWED by predicate, not split into replacements**: each keeps its
name and rank and gains a stricter predicate, with the weaker cases falling to the new states beneath
it.

**Three precedence rules, stated explicitly.** Ranks 1–3 are unconditional and evaluated before any
gate is consulted; a terminal event outranks even a verified acceptance, because terminal events are
self-limiting and honouring one can never grant anything. Ranks 4–5 precede 6–8 because an `accepted`
event is a claim about the whole change and must be reported as such even when the merge beneath it
is unverified. Ranks 6–8 partition the single condition "the governed merge commit is on `main`" by
authorization status, and that condition is derived from the platform and `main` ancestry, **never
from a lifecycle row**.

**Derivation and decision are separate.** An unavailable read still yields a merged-family state —
`merged_unverified`, never `merged` and never `accepted` — while `ST-7` fires independently on the
decision side. The report carries both; neither substitutes for the other.

Three human gates — `approved`, `merge_approved`, `accepted` — and these are the three the program
already runs on. **Merge is an event, not a state that authorizes anything.** `merged` never implies
`accepted`, and **no lifecycle row can promote a record to a state that asserts an irreversible act
was authorized.**

##### 4.3a `acceptance_verified`, defined mechanically

TRUE if and only if **all** of A through F hold. Nothing here consults free-form prose.

**A** — the `accepted` event satisfies the §4.2c schema, and a `merged` event carries `merge_sha`.
**B** — the actual governed merge SHA is `pullRequest.mergeCommit.oid` for the governed PR, confirmed
an ancestor of `main`. The immutable identifier is the commit SHA. It is **never taken from a row**.
**C** — `accepted.merge_sha` and `merged.merge_sha` both equal B.
**D** — the `merged` row timestamp equals `pullRequest.mergedAt` exactly.
**E** — every verification obligation resolves to evidence by the §4.3b transform, and every resolved
evidence item is satisfied.
**F** — `accepted.runs` equals exactly the set of qualifying check-run ids that satisfied E. After
acceptance only those ids are verified, so a later rerun cannot revise an earlier verdict.

**The `evidence=` free-text field is descriptive only and carries no verification weight.** A through
F are the whole gate, and writing an `accepted` row cannot satisfy any of them.

Readable failures of C, D, E or F derive `acceptance_claimed`. Unavailable reads of the PR record,
the check-runs list, or the registry blob at `created.base_sha` additionally fire `ST-7`.

##### 4.3b Obligation-to-evidence transform

Every obligation must resolve to exactly one of: **(i)** a registered CI control id together with its
exact workflow path and job name; **(ii)** an exact check context name; or **(iii)** an independently
executed repository command with a machine-verifiable result artifact. **An obligation resolving to
none of the three is UNMAPPED, and an unmapped obligation fails condition E** — it is never silently
dropped because its prose contains no parseable context.

**A control mapping resolves to the STEP, not the job, wherever the job can conclude `success` while
the step is skipped.** A job-level mapping would accept a control that never executed, which is
exactly the class of defect this amendment removes.

##### 4.3c Negative-control inventory for this model

Implementing this section requires the controls **NC-SM-01 through NC-SM-29**, enumerated in
`docs/contracts/CC-2026-07-19-adr-0105-state-machine.md`, and the three new decision rules **`ST-9`**
(a readable negative — no admissible claim), **`ST-10`** (a readable stale, unknown or fidelity
failure) and **`ST-11`** (an acceptance claim that does not verify). **No other new rule identifier is
authorized**; `ST-8` is the current maximum. `A5`, `OK`, `RF-3` and `ST-7` already exist and are
reused unchanged.

Two of those controls are **structural** — they assert a property outside the decision table and name
the exact failure text they must produce: `NC-SM-11` (`AssertionError: state set size {n} != 14`) and
`NC-SM-17` (`AssertionError: derived {state} is rank {n} > 8 with the merge on main`). The declared
count of fourteen and the implemented state set cannot drift apart while `NC-SM-11` runs.

#### 4.4 Invalidation

| Event | Content approval (`D`) | Exact-head approval |
|---|---|---|
| **Declaration edited** (any of §3.1, or `supersedes`) | **VOID.** `D` changes. Re-approve. | **VOID** |
| **Lifecycle appended** | **survives** — `D` is unchanged by construction | **survives if the append is lifecycle-only** (§4.1a checks 2–4 prove it); **VOID** if the same range carries anything else |
| **Head SHA moves** (any commit touching anything but this contract's lifecycle) | survives | **VOID.** §4.1a check 2 or 3 fails; stale → refused. |
| **Base moves** (`main` advances) | survives unless a cited authority changed | **VOID if** the rebase changes the diff; otherwise survives with required CI re-run at the new head |
| **A cited authority's file blob changed** | **FLAG — re-confirm** | **FLAG — re-confirm** |
| **Contract `id` reused or reassigned** | **VOID** | **VOID** |

**Appending a lifecycle event voids neither approval, and a commit carrying anything else voids the
exact-head one.** The line is drawn at *what moved*, not at *whether the head moved* — the head always
moves. Content approval survives an append by the §3 byte split; exact-head approval survives it by the
§4.1a delta proof. Both rest on the same fact: a lifecycle append cannot change the declaration, the
code, or a cited authority. **Binding to head equality instead would have voided the record in the act
of writing it** — which is what made the original rule unsatisfiable rather than merely strict.

**Authority-change detection** binds to the **git blob SHA of each cited authority file** at approval
(field #5). A mismatch **flags for re-confirmation; it does not auto-void.** File granularity would
void every open contract on any edit to `docs/ARCHITECTURAL_LAWS.md`. Per-rule granularity needs an
extractor that does not exist; Phase 3 may build it. Flag-not-void has no false-negative.

### 5 · Classification — additive, never subtractive

#### 5.1 Axis A — traits (change level)

**Traits are orthogonal and independently determined. A change carries a *set*.** Obligations are the
**union** over the set. **No trait ever removes an obligation another trait imposes.**

| Trait | Predicate | Adds to authority | Adds to evidence rule | Adds to verification | On scope breach |
|---|---|---|---|---|---|
| `cross-system` | `T1` ∨ `T2` | `tools.arch impact`; the affected `LAW-*` | — | `verifymap` requirements; `blast_radius` | stop |
| `governance` | `T3` | Constitution C18.1; the ADR process | re-verify what the change bears on | `python -m tools.arch ci`; `tools.ci static`; **a firing negative control for every new rule** | stop |
| `live` | `T4` | **operator authorization, always**; a **separate execution gate** | **no reuse — re-prove immediately before execution** | pre-image capture; rollback rehearsal; post-mutation re-probe | **refuse** |

**`contained` is not a trait.** It is the derived label for the empty trait set — a change that needs
no contract at all unless `T5` or `T6` fired. A contract may therefore exist with an empty trait set;
its obligations are then exactly the required CI.

**`risk_tier`** = `live` > `governance` > `cross-system` > none. It selects **only** the breach
response in §10 (stop versus refuse). **It never selects obligations**, because that is precisely the
subtraction this model forbids.

#### 5.2 Worked examples

**(a) Contained governance documentation change** — correcting the stale enforcement tally in
`docs/ARCHITECTURAL_LAWS.md`.
Traits `{governance}`. Contract required by `T3`. Obligations: the governance set only — C18.1 does not
apply (no rule changes), `tools.arch ci` + `tools.ci static` green, no negative control (no new rule).
No impact analysis, no `blast_radius`, no live obligations. **Small, and correctly small.**

**(b) Cross-system runtime change** — a ledger field consumed by publish and reconcile.
Traits `{cross-system}`. Obligations: impact analysis, `verifymap` requirements, `blast_radius`, the
affected `LAW-PERSIST-*` / `LAW-RECON-*`, per-additional-owner justification. No governance
obligations, no live obligations.

**(c) Live governance change** — an OGD branch-protection mutation (M1–M6).
Traits `{governance, live}`. Obligations are the **union**: C18.1 and the ADR process **and**
`tools.arch ci` **and** `tools.ci static` **and** operator authorization **and** a separate execution
gate **and** pre-image capture **and** rollback rehearsal **and** post-mutation re-probe. Evidence:
**no reuse** (the `live` rule dominates on evidence, which is a *tightening*, not a removal). Breach
response: **refuse** (`risk_tier` = `live`).
**Under a mutually-exclusive worst-wins model this change would have been classified `live` and
silently lost every governance obligation.** That is the defect this section exists to remove, and it
is not hypothetical — it is Phase 5.

**(d) Generated consequence of any of the above** — `docs/ARCHITECTURE_GOVERNANCE.md` regenerating
because a source line moved.
**Not a trait and not a change-level classification.** It is a per-file label (§5.3). The generated
file **inherits the change's trait set** and is never classified independently. It adds one obligation:
proof it was produced by regeneration and not hand-edited.

#### 5.3 Axis B — per-file labels (file level, kept strictly separate from §5.1)

| Label | Definition | Handling |
|---|---|---|
| `declared` | matches `expected_surfaces` | expected |
| `generated-consequence` | a derived artifact that is a pure function of a `declared` file (`LAW-SOT-02`, `LAW-DOC-01`) | **allowed without re-approval** — but only when produced by regeneration, never hand-edited (ADR-0102 §4) |
| `incidental` | matches `incidental_allowlist`, declared **in advance** | allowed |
| `unauthorized` | none of the above | **STOP** — amend the declaration and re-approve, or revert the file |

Labels never alter the trait set, and traits never relabel a file. A `governance` change may contain
`incidental` files; a `contained` change may contain `unauthorized` ones.

**The anti-silent-scope-expansion check, computable today with no new tooling:**

```text
unauthorized = files(git diff --name-only <base>...<head>)
             − expected_surfaces
             − generated_consequences(expected_surfaces)
             − incidental_allowlist
```

Non-empty ⇒ stop. **Phase 3 should implement this first**: highest value, zero prerequisites.

**Incidental discovery is not incidental change.** Finding an adjacent defect is expected and welcome;
*fixing* it is unauthorized scope expansion unless it is a required dependency of the declared work.
Record it in `out_of_scope_findings` and leave the code alone.

### 6 · Identity

**`id` format:** `CC-YYYY-MM-DD-<slug>` — `CC`, the UTC creation date, and a lowercase kebab-case slug
of 1–6 words. Example: `CC-2026-07-18-change-contract-architecture`.

- **Uniqueness:** the id is the filename stem in `docs/contracts/`. **The filesystem is the uniqueness
  check** — the same allocation discipline `docs/adr/` already uses (scan the directory). **No
  registry is introduced for identity.**
- **Reuse:** **never.** An id is permanent, including for `refused`, `superseded` and `abandoned`
  contracts, whose files are retained. A retired id is never reassigned.
- **Immutability:** the id is a declaration field. Changing it changes `D` and voids approval — which
  is correct, because a different id is a different contract.
- **The date is creation, not approval or merge.** It never moves. It exists to make the directory sort
  chronologically, matching the existing `YYYY-MM-DD-slug.md` convention under
  `docs/superpowers/plans/`.

**Separate bindings, all in the lifecycle section, all mutable by append:**

| Binding | Why it is not the identity |
|---|---|
| `branch` | Branches are disposable (ADR-0102 §3). The replacement-PR rule (§6 of that ADR) deliberately creates a **new branch** for the same work after a stacked-parent squash. |
| `pr` | A replacement PR gets a new number for the same contract. |
| `base_sha` | Moves on every rebase. |
| `head_sha` | Moves on every commit. |

**Relationship:** one contract has exactly one `id`; over its life it may bind to **several** branches
and **several** PRs, each appended as a `binding` event. If the *declaration* must change after
approval, that is a **new contract** with a new `id` and `supersedes: <old id>`; the old contract is
appended `superseded` and retained. This is the same discipline ADRs use, and it is why identity does
not need a registry: continuity is expressed by `supersedes`, not by a mutable central record.

### 7 · Scope and ownership

**"Owner" means architectural owner, not a person. This repository has no human ownership** — there is
no `CODEOWNERS` file and no file→person map. A contract must not pretend otherwise.

| Source | Keyed by | Coverage | Status for a contract |
|---|---|---|---|
| `derived/modules.json` `subsystem_of` (from `kb/subsystems.json`) | dotted module | **total**, derived, byte-verified | **AUTHORITATIVE owner of source files** |
| `contract/file_ownership.json` (`owner: "S01"` = a slice) | path | 19 files | **CLOSED** — Cycle-6 historical, roadmap **D3**. Not an owner for new work. |
| `kb/ownership.json` | asset | 8 entities, 10 control files | **advisory** — hand-written, never regenerated |
| `.github/ci-control-registry.yml` `owner:` | control id | 27 controls | owns **CI controls**, not files |

**Namespace collision, resolved here:** slice ids (`S01`…`S12`) and subsystem ids
(`S01_foundation`…`S19_*`) are unrelated taxonomies sharing a prefix. **`owners` carries subsystem ids
only, always in full form (`S04_registry`), never a bare `S01`.**

**Filename convention is not scope proof.** A path glob declares *intent*; the *check* resolves
`path → module → subsystem` through the derived map. A file belongs to a subsystem because
`subsystem_of` says so. Where a path has **no** module — documentation, configuration, workflows —
ownership is **declared and reviewed, never inferred**, and the contract marks which scope entries are
inferred versus declared.

**Cross-owner dependencies.** More than one subsystem in `owners` ⇒ the `cross-system` trait, and the
contract records, per additional owner, *why* it is touched. This is what stops the drive-by edit.

**Generated files are never in `allowed_scope`.** They are `generated-consequence`, produced by
regeneration only. Naming one in `allowed_scope` invites the hand-edit that `LAW-DOC-01` and the drift
gate already forbid.

**Prohibited additions default to all.** A new dependency, environment variable, required check,
registry, top-level directory or public surface is prohibited unless explicitly allowed.

### 8 · Evidence reuse

**The default is reuse.** Re-deriving settled facts every session is the failure this ends.

Each record carries `{claim, proven_by, proven_at, binding}`, where `binding` is *what the proof was
taken against*: a git SHA, a file blob SHA, a tool version, or a live-state probe.

| | Invalidator | Effect | Mechanizable |
|---|---|---|---|
| I1 | The bound source changed | invalid **for the changed part only** | yes — blob SHA compare |
| I2 | Conflicting evidence appeared | invalid; both recorded; **escalate** if same precedence | partially |
| I3 | The prior proof was incomplete **for this use** | invalid **for the new use only** — the original claim stands | no — judgement |
| I4 | The `live` trait is set | invalid **regardless of age** | yes |
| I5 | Older than the control it supports permits | invalid | yes, once a max age is declared |

**Two freshness regimes, no magic number:**

- **Source-bound** evidence is fresh **while its blob SHA is unchanged, with no expiry.** A fact proven
  about a file that has not changed does not become false with time.
- **Live-bound** evidence **expires**, because live state changes without a commit. It is re-proven
  **immediately before the mutation it authorizes** — which `I4` already compels, so no wall-clock
  constant is needed.

A constant such as *"evidence expires after 7 days"* is deliberately refused: it would be arbitrary,
would rot, and would itself become the stale prose number `LAW-SOT-03` governs.

### 9 · Verification selection

**Inputs:** the trait set (§5.1), the per-file labels (§5.3), the affected invariants, live-state
involvement, and generated artifacts.

**Base mechanism: reuse `tools/arch/verifymap.py::required_for`.** Do not build a second selector.
Obligations from traits are **added** to its output, never substituted for it.

- **V1 — smallest sufficient proof.** One check per fact.
- **V2 — redundancy requires a materially different failure mode**, justified exactly as the registry
  already requires: a `distinct_boundaries` statement, the device `DC-5` enforces
  (`tools/ci/checks.py:115`). The repository has already answered *"when is redundancy legitimate"*;
  this reuses that answer rather than restating it.
- **V3 — reference controls by `id`.** Never restate what a control does.
- **V4 — a check that cannot fail on this change is not verification.** An obligation must be shown
  capable of firing (red-before, or a negative control).

**Known defects Phase 3 must resolve rather than rediscover:** `verifymap` carries **two permanently
dead requirements** — `changed_state_machines` (`verifymap.py:32`) and `changed_rollback` (`:72`).
`impact.py` initializes both keys and **never writes them** (each appears exactly once, at the
initializer), and `required_for` dispatches on truthiness. Phase 3 must populate the dimensions or
retire the requirements. `changed_slices`, `changed_verification` and `changed_merge_gates` are
likewise never written but carry no requirement — dead and harmless.

`tools.arch verify` **always exits 0** and says so honestly: *a requirement on the author and the
reviewer, not a CI gate.* This ADR does not change that and **adds no CI job.** What it adds is that
the requirement list becomes **contractual** — declared in advance, checkable at the merge gate —
instead of advisory output nobody is bound by.

### 10 · Stop, refusal and escalation

| Trigger | Action | Next actor |
|---|---|---|
| Within declared scope, authority clear | **continue** | agent |
| A mandatory field cannot be filled | **request clarification** | operator |
| The right change lies outside `allowed_scope` | **request expanded authorization** — never widen unilaterally | operator |
| The diff contains an `unauthorized` file | **stop** (`risk_tier` < `live`) / **refuse** (`live`) | agent → operator |
| A cited authority changed after approval | **stop** | operator |
| Two same-precedence authorities conflict | **escalate** | operator |
| The task requires exceeding a `LAW-*` | **escalate** — the path is amendment (C18.1), not a contract field | operator |
| A `live` action has no separate execution gate | **refuse** | — |
| The task cannot be made safe under any authorization the operator can grant | **refuse** | — |
| `success_condition` is not falsifiable and cannot be made so | **refuse** | — |

**Refusal is a first-class successful outcome.** A contract terminating in `refused` with a recorded
reason **has done its job**. It is not a failed contract and not a failed agent. Phase 4 case 3 and
Phase 7 case 2 exist to prove precisely this; a system exercised only on its happy paths has not been
tested.

**Anti-rationalization rules — binding:**

- **Silence is not permission.** "No law forbids it" does not authorize it.
- **Inferred intent is not authorization.** "The operator would obviously want this" is not a grant.
- **Discovery is not a licence to fix.** Finding a defect authorizes recording it, nothing more.
- **A blocked path is a signal, not an obstacle to route around.** A denied tool call, a failing gate
  or a refused permission is information about the boundary.
- **Partial authorization is not full authorization.** design ≠ implement ≠ merge ≠ apply-live ≠ accept.
- **Being nearly done does not authorize finishing.** Sunk effort is not a grant.

### 11 · Storage and source of truth

**A Change Contract is a committed Markdown file with a YAML front-matter head, one file per contract,
at `docs/contracts/<id>.md`, landing in the same pull request as the change it governs.**

| Decision | Why |
|---|---|
| **Committed**, not ephemeral | A cold-start agent has no chat history; `tools/` cannot read PR bodies |
| **Committed**, not gitignored | The counter-example is in this repository: the per-unit verification-record convention has **16 records on disk, 0 tracked**, its gate dormant — designed, documented, invisible |
| **Same PR as the change** | ADR-0102 §1 makes one PR one squash commit, so `git show <sha>` yields the change **and** its authorization atomically. This is what removes the need for an index. |
| **No registry, no index** | Identity is `id`; uniqueness is the filename; discovery is `git log` and the file tree. An index would be the second registry `ENGINEERING_STANDARDS.md:32` forbids. |
| **Markdown + front matter** | Identical in shape to the existing ADR files. Machine-readable head for the Phase 3 compiler, prose body for rationale. No new format. |
| **`docs/contracts/`** | Verified free at `937777d`: untracked and not gitignored. |

#### 11.1 Authoritative representation

| When | Authoritative artifact | Identified by |
|---|---|---|
| **Before merge** | the contract blob at the **currently approved PR head** | `git rev-parse <head_sha>:docs/contracts/<id>.md` — an immutable blob id |
| **After merge** | the contract blob at the **resulting `main` SHA** | `git rev-parse <main_sha>:docs/contracts/<id>.md` |

The merge base is explicitly **not** authoritative: a contract introduced in its own PR does not exist
at the merge base, so a merge-base authority is undefined exactly when it is needed.

**Approval binds to immutable digests, not to a path:** content approval binds to `D` (§3); exact-head
approval binds to `(D, head_sha, blob id at head_sha)`. A path can be repointed; a digest cannot.

**PR descriptions, chat messages, scratchpad copies and an agent's recollection are never
authoritative.** Disagreement between the authoritative blob and any of them is an **unrecorded
authorization change — stop.**

#### 11.2 Retention

`superseded` and `abandoned` contracts flip nothing in the declaration — they receive a lifecycle
event and are **retained forever**. Deletion would destroy the audit trail the artifact exists to
create.

#### 11.3 One mechanical constraint on the location

`IMPL-007` scans `docs/` for a `_CLI_PRINT_COUNT = <n>` assignment and **treats the assignment form as
a live claim** (`tools/arch/policy.py:644`, `:674-676`). A contract file must never carry that form.
Not hypothetical: it is why the untracked reconstruction documents are not committable today
(roadmap **B3**).

### 12 · Relationship to later phases

| Phase | Owns | Must not |
|---|---|---|
| **2** (this ADR) | the model — fields, the declaration/lifecycle split, gates, traits, authority resolution, evidence rule, identity, storage | ship any executable, schema file, check or workflow |
| **3** | the compiler (derives derivable fields) and the verifier (checks a contract against a diff at a head), **each rule carrying a firing negative control** | change the model without amending this ADR |
| **4** | cold-start acceptance: a contained change, a cross-system change, and a request correctly stopped | ship new mechanism |
| **5** | OGD M1–M6 — the first `{governance, live}` change, and the model's real test | touch the contract model |
| **6** | the orchestration-enforcement decision — **may adopt the contract as the gate's input** | re-enable anything before deciding |
| **7** | production acceptance: one real change and one correct refusal | — |

**Phase 2 → Phase 3 interface.** Phase 3 must be implementable from this ADR alone, without another
repository-wide authority investigation. The testable surface: the declaration field list and
derivation classes (§3.1); the declaration/lifecycle byte boundary and `D` (§3); the three gates and
their bindings (§4.1); derived state (§4.3); invalidation (§4.4); the trait predicates and the union
rule (§5.1); the per-file label predicates (§5.3); identity (§6); the evidence rule (§8); and the four
gaps below.

| | Gap Phase 3 must close | Evidence |
|---|---|---|
| **G1** | `tools.arch impact` emits **Markdown only**; no machine-readable CLI output — flags are `--base`, `--strict` only | `cli.py:181-184`, `:233-236`; `impact.py:299-327` |
| **G2** | **No path→module transform.** `subsystem_of` is module-keyed; diffs are path-keyed | `generate.py:225-227` (already special-cases `__init__`) |
| **G3** | **No reverse-dependency closure.** `fan_in_compile` stores **counts only**, never dependent identities — `blast_radius` is not computable today | `graph.py:147-150`, `:184` |
| **G4** | `verifymap` has **two permanently dead triggers**; their impact dimensions are initialized and never written | `verifymap.py:32`, `:72`; `impact.py:87-101` |

Phase 3 additionally owns **mechanical completeness verification of the `T3` governance-surface list**,
which remains hand-maintained in Phase 2 by accepted operator decision.

## Alternatives considered

- **No contract; strengthen the laws instead.** Rejected — laws are general by construction. No general
  rule states what *this* change may touch, which is the entire gap.
- **A single global contract for all future work.** Rejected — the precise defect Phase 1 closed. A
  contract governing everything constrains nothing.
- **Contract as an ephemeral artifact (PR body or chat).** Rejected — unreadable by a cold-start agent
  and by tooling; not auditable afterwards.
- **Contract as gitignored runtime state.** Rejected — this repository already demonstrates the failure
  mode: 16 records, 0 tracked, gate dormant.
- **A contract registry or index file.** Rejected — a second registry, forbidden by
  `ENGINEERING_STANDARDS.md:32`, and unnecessary once the contract lands in the same squash commit.
- **JSON contracts with a schema file.** Rejected for Phase 2 — that is the executable schema this
  phase excludes.

## Rejected alternatives (non-obvious)

- **Mutually-exclusive change classes with worst-wins.** Rejected — it **subtracts** obligations. A
  branch-protection mutation is both `governance` and `live`; under worst-wins its governance
  obligations vanish. Traits union, and `risk_tier` selects only the breach response.
- **Storing lifecycle state as a declaration field.** Rejected — it is self-invalidating: recording
  `merged` edits the artifact whose approval authorized the merge. §3's byte boundary is the fix.
- **Recording exact-head approval in the contract file.** Rejected — writing it moves the head it
  approves. A GitHub review binds to `commit_id` natively and moves nothing.
- **A blanket `T3` exclusion for `docs/contracts/**`.** Rejected — it would let a declaration be edited
  or lifecycle history be reordered with no governance obligation at all, which makes the authorization
  record worthless. §3.6 distinguishes appending from rewriting instead.
- **Treating every deletion as `live`.** Rejected — deleting a git-tracked file is revertable by
  construction, and classifying it `live` would ban evidence reuse and demand an execution gate for
  ordinary code removal, driving agents to avoid deletion. §T4's deletion boundary scopes `live` to
  externally durable state.
- **`id` = branch slug.** Rejected — ADR-0102 §6's replacement-PR rule deliberately creates a **new
  branch** for the same work, so a branch-derived identity does not survive the recovery path the
  repository prescribes.
- **Requiring a contract for every change.** Rejected — the cost falls on the common case, agents route
  around it, and a routed-around control is worse than none because it also lies.
- **A wall-clock evidence-freshness constant.** Rejected — arbitrary, rots, and becomes the stale prose
  number `LAW-SOT-03` governs.
- **Auto-voiding every open contract when a cited authority file changes.** Rejected as
  disproportionate at file granularity.
- **Allowing an operator instruction to grant an exception to a `LAW-*`.** Rejected — it converts every
  approval into a law-bypass.
- **Treating path globs as ownership proof.** Rejected — ownership resolves through `subsystem_of`.

## Consequences

- A cold-start agent can answer "may I change this, and how will I know I did it right?" from one file
  plus the ids it cites.
- Silent scope expansion becomes **mechanically detectable** with no new tooling (§5.3).
- Merge and acceptance are structurally distinct; landing code never implies the outcome was proven.
- Obligations **accumulate** — a higher-risk trait can never erase a lower-risk one's requirements.
- Approval binds to immutable digests, so *"what exactly was approved"* is always answerable.
- Evidence stops being re-derived every session.
- Refusal becomes a recordable, gradeable outcome.
- **Cost:** every triggered change gains one file, one design gate, and one exact-head gate. That is the
  intended price, and `T1`–`T6` is where it is contained.

## Risks

- **The trigger set is miscalibrated.** `T1`'s single-file-deep-coupling false negative is stated
  explicitly in §1. **Accepted as a Phase 4 calibration risk by operator decision; the trigger is
  deliberately not broadened.** *Mitigation:* `T5` reuses an already-calibrated threshold; Phase 4
  exercises both a contained and a cross-system case. *(estimate.)*
- **`T4` depends on an agent recognizing its own action is live.** *Mitigation:* any doubt resolves to
  `T4`; a live action additionally requires a separate operator execution gate. **Accepted residual** —
  no mechanism detects an intention.
- **Exact-head approval depends on GitHub review semantics.** If reviews are dismissed on push
  (`dismiss_stale_reviews`), that *reinforces* the model; if not, the verifier must compare the
  review's `commit_id` to the current head itself. *Mitigation:* Phase 3 compares explicitly rather
  than trusting the badge. *(estimate.)*
- **The `T3` surface list has no completeness proof.** Hand-maintained in Phase 2 by accepted operator
  decision; mechanical verification is Phase 3 work. *(accepted, time-boxed.)*
- **An unwitnessed merge approval (§4.1a) is forgeable by the agent that writes it.** So is `approved`,
  and so is `accepted` — all three are in-file records of an operator act, and this route makes the
  set consistent rather than adding a new exposure. The threat model is **agent drift and overreach,
  not a compromised agent**: an adversary with commit access already owns every artifact this system
  is made of. **Accepted residual.** *Mitigation, and the honest ceiling on it:* the route is
  unreachable wherever a second principal exists, and the report discloses it every time it is used.
  If the threat model ever widens to a compromised agent, the correct answer is a **signature over
  `parent_sha` by a key the agent cannot reach** — deliberately not built now, because key custody is
  an operator fact this repository cannot verify, and an unverifiable check is decoration.
- **The witnessed route proves account-distinctness, not independence.** A single operator can hold a
  second account and approve their own work; the gate would go green having proven nothing. This is
  unchanged by the amendment and is called out because it bounds what the *stronger* route is worth:
  neither route can prove that a human actually read the diff. **Accepted residual** — the same limit
  as "contracts become box-ticking", below.
- **Contracts become box-ticking.** *Mitigation:* derive what is derivable; require a falsifiable
  success condition. **Accepted residual** — no mechanism forces a human to think.
- **Phase 3 slips and the model stays prose.** *Mitigation:* §5.3 ships immediately with zero
  prerequisites and carries most of the safety value alone.
- **G3 is larger than estimated.** *Mitigation:* `blast_radius` is required only for `cross-system`.
- **`docs/contracts/` accumulates.** **Accepted residual** — the same as `docs/adr/`.

## Migration plan

No migration. No existing artifact is converted, moved or retired.
`.reports/architecture/contract/implementation_contract.json` remains a closed historical record
(roadmap **D3**) and is **not** the ancestor of this model.

## Rollback plan

This ADR ships no executable and no live change. Rollback is `git revert` of the single squash commit,
removing the ADR, the roadmap edits and the two navigation rows. Nothing observes them. Reversing the
decision after acceptance is a new superseding ADR.

## Enforcement mechanism

**None in Phase 2, by design.** This ADR is the model; Phase 3 builds the compiler and verifier and is
where enforcement is specified. No CI job, workflow, hook, branch-protection setting or policy rule is
added, modified or enabled.

## Verification contract

- `python -m tools.arch ci` green — no source line moves, so no drift and no regeneration.
- `.venv/bin/python -m tools.ci static` green — no control, workflow or `branch_protection_context`
  touched.
- **DC-4:** the added `AGENTS.md` row places no control's `branch_protection_context` string on a line
  with the word "advisory". It contains neither.
- **IMPL-007:** no file added carries a `_CLI_PRINT_COUNT = <n>` assignment.
- **`approved_digest`** matches the committed body (§Status). A mismatch is a governance failure, not a
  formatting nit.
- No test is added, because no behaviour is added. Phase 3 ships a firing negative control per rule.

## Superseded decisions or documents

None. This ADR fills an absent decision: no per-change authorization model was ever recorded. It does
**not** supersede `.reports/architecture/contract/implementation_contract.json`, which remains closed
historical evidence of the Cycle-6 program (roadmap **D3**).

## Affected workflows and controls

**None.** No workflow, control, registry entry or branch-protection setting is modified. The ADR
*references* controls by id; it changes none.

## Operator decisions — accepted 2026-07-18

1. **ADR-0105 accepted** — the model as specified in this text.
2. **The documented `T1` false-negative boundary is accepted as a Phase 4 calibration risk.** The
   trigger is not broadened to cover it.
3. **Exact-head merge approval is a GitHub pull-request review bound to the current `commit_id`**, not
   a chat token. — **AMENDED 2026-07-19 (§4.1a).** Phase 3B proved the mechanism unavailable in this
   repository: `Fleezyflo` is the only account with push access and is the author of every PR, and
   GitHub refuses self-approval, so no review of any kind can ever be obtained. A decision between two
   options, one of which does not exist, is not a decision. **What the original decision was
   protecting is retained in full: the approval still binds to a specific commit, and the binding is
   still checked against git rather than trusted.** What changes is only the medium of record where —
   and *only* where — the platform proves no second principal exists. The rejected alternative was a
   bare chat token carrying no commit at all; that remains rejected.
4. **The `T3` governance-surface list may remain hand-maintained in Phase 2.** Its completeness
   verification is Phase 3 work.
5. **Contracts live at `docs/contracts/<id>.md`, in the same PR as the change they govern.**
6. **Operator authorization is narrowing-only and cannot override a `LAW-*`.**
