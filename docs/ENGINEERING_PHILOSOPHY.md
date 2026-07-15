<!-- The Engineering Philosophy — the practical design instincts behind FanOps.
     Base: origin/main @ 04c4092 (#664), 2026-07-16. Companion to docs/REPOSITORY_CONSTITUTION.md.
     This is EXPLANATORY: it argues the reasoning behind the rules, so a new engineer (or agent) can
     make a novel decision the same way the repository already does. The rules and their enforcement
     status live in the Constitution and docs/ARCHITECTURAL_LAWS.md; this document is the "why". -->

# FanOps — Engineering Philosophy

The Constitution says *what* the rules are and whether they are enforced. This document explains *how
this repository thinks*, so that a decision nobody has written down yet can still be made the FanOps
way. It is deliberately argumentative, not a checklist.

The single instinct under everything: **trust what you can mechanically re-derive; distrust what you
can only read.** Every practice below is a corollary.

## 1. Explicit state over hidden inference

The system never infers where a unit is in its lifecycle; it records it. A `Source`, `Moment`, `Clip`,
`Post`, `Render`, `Stitch`, `Batch` each carry an explicit enum, and setters are immutable — you write a
new value, you do not mutate in place. The reason is legibility under concurrency: a launchd daemon, the
Studio, and a CLI all touch the same ledger, and a state you can *read* is a state three processes can
agree on, while a state you *infer* is three processes guessing independently.

The sharpest expression is the publish lifecycle. A post is *born* `awaiting_approval`; the publisher
iterates only `queued`. Nothing about "is this ready to go out" is inferred from timestamps or
heuristics — it is an explicit operator promotion. This is why the no-auto-publish guarantee can be a
one-line invariant instead of a web of conditions: the state *is* the decision.

The corollary for anyone adding a feature: if your feature needs to know "what happened to X," add a
field to X, do not reconstruct it. A reserved state with no writer (a `RenderState.queued` that no code
advances) is fine — an inert door is honest — but a state *inferred* from other fields is a second
source of truth that will drift.

## 2. One authority per invariant

Every invariant has exactly one owner, and every mechanism has exactly one implementation. This is not
tidiness; it is a scar. When the negative-control count lived in two places, the CLI reported "23/23
green" while pytest failed control 24 *on the same commit* — two copies of one truth, disagreeing. The
fix was not to sync them; it was to delete one and have both callers invoke `selftest.detect`.

So the instinct when you find yourself writing a second check for something already checked: stop and
ask which one is the owner. If a producer and a consumer both decide "is this backend live," they must
call the *same* predicate — RC-3b proved `producer_claim == is_live_backend` across all 96 states by
collapsing two conditionals into one shared capability, not by making two conditionals agree. Two
conditionals that "agree today" are a latent divergence; one capability cannot diverge from itself.

Where genuine redundancy is valuable (a local pre-commit scan *and* a CI scan), it is declared as such —
the CI control registry's `duplicate_groups` names the distinct boundary each member protects. Redundancy
is allowed; *unjustified* redundancy is a defect.

## 3. Fail-open vs fail-closed is a decision, not a default

There is no blanket "always fail open." The direction is chosen by consequence, and there are three
distinct cases:

- A **verdict producer** that cannot decide fails toward *more* checking. `deep_required(None)` returns
  `True`; `UNKNOWN_IMPACT` is never treated as safe. The most expensive way to be wrong about a proof is
  to skip it, so uncertainty escalates.
- A **degradable side-feature** fails to a safe default *and leaves a logged breadcrumb*. One bad frame
  or one flaky network read must never wedge a whole pass — but it must never vanish either. `fail_open`
  logs with `exc_info=True` and refuses to swallow `KeyboardInterrupt`/`SystemExit`.
- A **correctness prerequisite** fails closed and loud. When smart framing is on and cv2 is absent, the
  render *refuses* (exit 2) rather than ship a blind centre-crop. A missing prerequisite is not a
  detection miss; degrading silently would ship a worse product while reporting success.

The trap this guards against is the one the repo actually fell into: the destructive ledger-wipe path
once "failed open" by logging to an *unsurfaced* stdlib channel — technically logged, practically
invisible. So the deeper rule is **logging ≠ surfacing**. A breadcrumb that no operator will ever see is
a silent failure wearing a log statement. When you choose fail-open, choose the surfaced channel and a
severity that matches the blast radius.

## 4. Ambiguity is never resolved as success

An ambiguous outcome — a network POST that may or may not have landed — is never collapsed to "done." It
becomes `needs_reconcile`, and a later reconcile pass, working from live evidence, resolves it. Publishing
is a three-phase handshake precisely so the ambiguous middle has somewhere to live: the claim is committed
*before* the network call, the network call sits between two short transactions, and finalization or
reconciliation closes it. The alternative — guessing, and recording the guess as truth — is how you strand
a post forever or double-publish. When you cannot know, say "unknown" and route it to the machine whose job
is to find out, never to the field that means "success."

## 5. Fix the root, minimally

A fix names the root cause and corrects *it*, not the symptom it emitted. The commit history is a
catalogue of `RC-n` root corrections, and the tell of a bad fix is a verb like "make-visible," "harden,"
or "sweep" — those describe treating a symptom class, not removing its generator. RC-3b did not add a
guard where posts stranded; it unified the predicate that let them strand.

But root-first does not mean big. The instinct is the *smallest* correction that removes the root. A
proposed `PostState.ready` that would have threaded a new state through eight files and sixty-seven read
sites was rejected in favor of a one-chokepoint guard — partly *because* the small fix needed no
migration and nothing to un-stamp. Cheap-and-total beats impressive-and-broad. Over-engineering is named
and rejected on the record, so the next person does not re-propose it.

## 6. Deterministic evidence over prose; a number in prose is distrusted

The repository's signature defect, found in every audit cycle, is *"the document names a mechanism that
does not exist while the property survives via a different one."* The response was to stop trusting prose
for anything a machine can measure. Generated docs are byte-reproduced from source and byte-compared in
CI; a hand-edit is drift by definition. The architecture governance doc "contains no hand-written facts."

The most distrusted artifact of all is a **number copied into prose**. `_CLI_PRINT_COUNT` once lived in
nine places holding four different values, and rotted *again* while the PR fixing it was open. So the
rule became: a count lives in exactly one derived place, and IMPL-007 scans the tree for stale
assignments. "Byte-identity proves faithfulness, not truth" — a generated doc can be faithfully wrong, so
the *inputs* are what get verified.

**Line anchors are the same trap, smaller.** Function names and semantics are stable; the `file:line`
citations to them rot on the next edit (INV-20 found 10 of 10 nested-`CLAUDE.md` anchors stale). So the
convention is: cite the *symbol*, treat the line number as a hint, and re-`grep` before trusting it. This
document, and every document in this layer, follows that — which is why each carries a provenance header
naming its base SHA, and why a stale anchor is a documentation defect, not a lie to route around.

## 7. Live re-derivation overrides historical plans

A plan, a memory, a frozen codemap, a prior audit — all are *leads*, not proof, and all have a shelf
life. The discipline is to re-derive the current state before acting on a historical claim. This is not
abstract: while this very constitutional layer was being written, `origin/main` advanced from #652 to
#664, and four of the evidence dossier's headline findings went stale within hours — RC-4/RC-5 got
fixed, the ADR system went from "dormant" to active, the CI control registry appeared, lane-guard was
hardened. Had the layer been written from the snapshot, it would have shipped four false claims.

So the instinct is structural distrust of your own recent notes. Cycle 4 named five read-only merge gates
and *ran none of them*; Cycle 6 ran all five and three "blocking" risks collapsed against the live tree
(0 malformed backends, 0 stranded posts, 0 retired moments). Cost and reachability are *measured*, never
read. And a collapsed risk is re-armed as a merge gate, because "a collapsed risk not re-verified at
merge is a risk merely not looked at." Revalidation is not a one-time step; it is the default posture.

## 8. Compatibility: preserved by default, broken deliberately

Backward compatibility is the default and it is *load-bearing*: a ledger model uses pydantic
`extra="ignore"` so an older binary parses a newer ledger and drops unknown keys instead of crashing —
and `extra="forbid"` is a BLOCKING-banned change precisely because it "looks like tightening" and would
brick every reader. A new feature is byte-identical for legacy callers and when its flag is off, proven
by a firewall test. "Byte-identical when unchanged" appears hundreds of times in the tree because it is
how you add capability without risk.

Compatibility is broken only when the *thing* being broken is a feature, not a data shape, and only after
every consumer is gone. A schema migration may *drop* a map (v10→v11 dropped `account_selections`) — but
only as the on-disk half of a teardown that first removed every reader in code. The rule for deciding
migrate-vs-shim is mechanical: does a migration mechanism exist? The ledger has a hop-chain, so it may
drop; the accounts registry has none, so it must stay lenient. You do not break a shape you cannot
migrate.

## 9. Accepted residual over disproportionate change

Not every known gap is fixed. A residual is *accepted* — deliberately kept — when it is zero-or-low
reachability, contained, and documented with an owner. RC-9's mutation-time enforcement was deferred
because the risk has zero current reachability and the fix would cost a broad runtime change and a fixture
churn out of all proportion to a bug nobody can hit; instead a GUARD slice *pins* its unreachability in CI
so the moment it becomes reachable, CI goes red. "A deferral is not a discharge" — the acceptance is only
legitimate because it rests on two *measured* numbers (0 offending rows, ~6 affected fixtures), not an
assertion, and because containment is mechanical.

The instinct: before forcing a broad, risky runtime change to close a gap, ask what the gap's actual
reachability is, and whether a cheap guard can pin it. Forcing the big change to feel thorough is its own
kind of over-engineering.

## 10. Deletion requires a current reachability proof

"Zero callers" is a lead, never a verdict. The name-based call graph cannot see aliased or lazily-bound
backends, and it once mislabeled five *live* functions as dead. So a deletion must ship a whole-tree AST
census, not a grep — and, critically, it is revalidated *at execution*, not when the plan was written. A
plan that called four things dead had all four premises invalidated at execution time (one was a live
helper with ~25 test callers) and the deletions were cancelled cleanly rather than forced through. Deleting
on a stale "it's dead" is how you remove something that is quietly load-bearing. Prove reachability now, or
do not delete.

## 11. Reversals are recorded and learned from

The repository reverses itself openly. The per-frame reframe "chase" was built, shipped, and reverted in
favor of a static locked-off crop — and the reversal is *recorded*, so the next person does not re-propose
the chase. Decision records carry a Retractions section that withdraws the author's own earlier claims by
name. A silent reversal loses the lesson; a recorded one turns a mistake into a guardrail. When you undo a
decision, write down why the original was wrong — that sentence is worth more than the revert.

## 12. Parallel agents and worktrees operate without collision

This repository is built largely by autonomous agents, sometimes several at once, and that is only safe
under strict isolation rules — learned from real host crashes and real double-merges:

- **One isolated worktree per agent.** Two agents in one checkout share a HEAD and an index and will
  clobber each other; each substantial unit of work runs in its own `git worktree` off fresh
  `origin/main`. (This document was written that way.)
- **Never overwrite, delete, or silently absorb another agent's files.** When a parallel run had already
  produced an ADR catalogue and a `docs/constitution/` layer, the correct move was to cite them as
  evidence, build at the *specified* canonical paths, and leave theirs untouched — not to merge or
  supersede them by hand.
- **Re-fetch before you land.** Parallel orchestrators collide; `git fetch` + `gh pr view` the target
  before merging, and never force-push over another agent's commits. When a stacked child breaks after
  its parent squash-merges, open a *replacement* PR on fresh `main` rather than force-pushing the old one
  into shape.
- **Host safety is a hard limit.** Stacked hot sessions and parallel local test suites crash the machine;
  tests are CI-only, and heavy fan-out is bounded. Owning the output means also not taking the machine
  down to produce it.
- **Reconcile, do not compete.** When your work overlaps another agent's register, produce a
  reconciliation (which claim wins, and why, against the live tree) rather than a third unreconciled
  source of truth. Two constitutions that disagree are the exact defect this layer exists to end.

---

*Read next:* `docs/REPOSITORY_CONSTITUTION.md` for the rules and their enforcement status;
`docs/ARCHITECTURAL_LAWS.md` for the enforceable subset with mechanisms; `docs/governance/EVIDENCE_RECONCILIATION.md`
for how the current reading was derived.
