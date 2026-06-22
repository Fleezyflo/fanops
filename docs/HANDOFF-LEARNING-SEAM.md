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

## The unfreeze prerequisite (the gate — do NOT skip)

Learning stays **validation-frozen** behind `learning_validated(cfg)` (`src/fanops/validation_gate.py`, which
reads `cutover.json metrics_confirmed`). `variant_amplify` / `variant_ucb` / `variant_transfer` are INERT until
`fanops cutover metrics` writes `metrics_confirmed=True` (`cutover.py` / `cutover_postiz.py`) against the LIVE
Postiz analytics label shape. **No batch / casting flag unfreezes learning** — `FANOPS_ACCOUNT_CASTING` gates
organization/casting only, never the learning actuators.

## Explicit ordering (prerequisites, then build — none of it in this program)

1. **(prereq, operator-gated)** Land a LIVE Postiz metrics capture on a real instance → `fanops cutover metrics`
   confirms the `lift_score` field shape → `metrics_confirmed`.
2. **(prereq)** The learning actuators unfreeze (existing machinery; nothing new built).
3. **(future build)** The per-batch rollup READER: group `Post.metrics[LIFT_SCORE]` by `Post.batch_id`; `None` →
   Ungrouped. Pure read, additive — generalizes `posted_batch_rollup`.
4. **(future build, optional)** A per-account feedback view in Studio that closes the loop from results → the
   next batch's casting/hook choices.

Until step 1 lands on a real instance, this is correctly OUT OF SCOPE.
