# Future Milestone (OUT OF SCOPE for the Account-First Studio program): Per-batch / per-account learning

This program ingests + publishes **per-account** (named, account-targeted batches; per-account hooks/captions/
casting) but does **not** *learn* per-account. Per-account / per-batch learning is a separate, larger track —
correctly sequenced LAST, and deliberately **not built** here. This file is the handoff a future session picks
up; it is a plan, not code.

## The seam (no new write path)

- `track.record_metrics` (`src/fanops/track.py:53`) is the **sole** writer of `Post.metrics[LIFT_SCORE]`
  (`track.py:68`; `LIFT_SCORE = "lift_score"`, `models.py`). The `variant_*` / `adjust` actuators only **read**
  `Post.metrics`.
- The future per-batch rollup **reader** groups those EXISTING per-post metrics by `Post.batch_id`
  (`models.py`) — **no schema change beyond the `batch_id` Face 1 already added, no new writer**. A
  `batch_id is None` lineage (un-batched ingest, `crosspost_to_account`, broken lineage) → tolerate `None` as
  **"Ungrouped"**, mirroring Face 4's Review grouping.
- Face 5 already ships a **display-only** sibling: `views.posted_batch_rollup(rows)` ("N posted · mean lift")
  over `PostedRow.lift_score`. It is a pure read, computes no learning, and writes nothing — it is the shape the
  future reader generalizes, NOT a learner.

## The unfreeze prerequisite (a CORRECTNESS gate — auto-opens on real data, NOT an operator step)

Learning stays **validation-frozen** behind `learning_validated(cfg)` (`src/fanops/validation_gate.py`, which
reads `cutover.json metrics_confirmed`) until the live metric field-shape is PROVEN against `track._W`.
`metrics_confirmed` is now stamped **automatically** — `track.pull_metrics` (`_auto_validate_metrics_shape`)
sets it the first time a real, **non-degraded** analyzed metric lands from a LIVE backend (dryrun never proves
it; a degraded row never stamps). There is **no operator step**: the gate opens on real data, not a manual
`fanops cutover metrics` ritual (that probe still works as an optional early shortcut). It is a correctness
gate (don't learn on an unproven / mis-keyed shape), not an operator gate. **No batch / casting flag unfreezes
learning** — `FANOPS_ACCOUNT_CASTING` gates organization/casting only, never the learning actuators.

## Explicit ordering (prerequisites, then build — none of it in this program)

1. **(prereq, automatic)** Land a LIVE Postiz metrics capture on a real instance → the first real, non-degraded
   poll auto-confirms the `lift_score` field shape → `metrics_confirmed` (no manual step; the cutover probe is
   an optional early shortcut).
2. **(prereq)** The learning actuators unfreeze (existing machinery; nothing new built).
3. **(future build)** The per-batch rollup READER: group `Post.metrics[LIFT_SCORE]` by `Post.batch_id`; `None` →
   Ungrouped. Pure read, additive — generalizes `posted_batch_rollup`.
4. **(future build, optional)** A per-account feedback view in Studio that closes the loop from results → the
   next batch's casting/hook choices.

Until step 1 lands on a real instance, this is correctly OUT OF SCOPE.
