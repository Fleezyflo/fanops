> Frozen 2026-07-11 — invariants map, not auto-synced. When prose and code disagree, the code is right.

# Codemap — insights culmination (stamp → aggregate → actuator, per varied dimension)

The closed loop that lets **analyzed reach bias the creative dimensions it doesn't yet reach**. Every clip
varies along several axes (length, opening frame, framing, casting, timing); this map traces, per axis, where
the axis is **stamped** onto a `Post`, how it is **aggregated** by reach, and which **actuator** feeds the
winner back into generation/scheduling. Built by Legs 2–3 of the insights rebuild
(`.claude/plans/insights-culmination-MASTER.plan.md`); Leg 2 makes the reach authoritative, Leg 3 closes the
three uncovered dims (framing/timing/casting).

## The signal it ranks on (Leg 2)

Every actuator ranks by **raw Meta Graph reach**, not the engagement-skewed `lift_score` (lift weights reach
0.001 — reach-first is the operator's objective, one philosophy for all structural dims).

- IG performance is read from the **Meta Graph** ([track.py](../../src/fanops/track.py) `_default_list_posts`
  → `GraphInsightsClient`, track.py:181-185), the **sole IG metric reader**. `PostizMetricsClient`
  ([post/metrics.py](../../src/fanops/post/metrics.py)) is retained for any **non-IG** use but **no longer
  reads IG metrics** — Leg 2 made `PostizMetricsClient`-for-IG dead.
- `learning_validated` ([validation_gate.py](../../src/fanops/validation_gate.py)) is the freeze gate:
  nothing propagates until it unfreezes, which happens automatically on the first **live, non-degraded**
  Postiz-shaped row (`reach` + a primary engagement key `saves|shares`; retention NOT required —
  `_shape_proves_learning`, track.py:38). Frozen in **dryrun**; opens on go-live + real data. Not an
  operator ritual.

## Per-dimension lattice

| Dimension | Post field (STAMP) | AGGREGATE | ACTUATOR | Wired |
|---|---|---|---|---|
| length | `clip_profile` | `aggregate_by_dim` (in `_P4_DIMS`) | `apply_p4_dim_bias` | `cmd_run` post-loop |
| opening frame | `first_frame_kind` | `aggregate_by_dim` (in `_P4_DIMS`) | `apply_p4_dim_bias` | `cmd_run` post-loop |
| **framing** (top/center) | `top_bias` | `aggregate_by_dim` (in `_P4_DIMS`) | `apply_p4_dim_bias` | `cmd_run` post-loop |
| **timing** (hour/day) | `publish_hour`, `publish_dow` | `timing_bias_winner` | `apply_timing_bias` | `cmd_run` post-loop |
| ~~**casting** (account × content)~~ | — | — | — | **REMOVED P11** (`casting_bias` / `casting_reach_prior` deleted with LLM casting teardown) |
| hook / cheap-text axis | `variation_axis` | `best_hooks` / `ucb_rank` | `apply_variant_amplify` | `cmd_run` post-loop |

The hook axis predates this work (the caption/hook loop keeps `lift`); Legs 2–3 added framing and
timing reach feedback. The casting reach prior (`casting_bias`) was removed with the P11 casting teardown
(MOL-152) — owner attribution is now stamped at pick time, not learned via a casting brief.

### STAMP — where each dim lands on the Post

- `clip_profile`, `first_frame_kind`, `variation_axis`, `top_bias` are stamped at the crosspost mint
  ([crosspost.py](../../src/fanops/crosspost.py): `first_frame_kind` at :286, `top_bias` at :294 — **per-account**
  `cfg.resolve_top_bias(surf.account)`, NOT the global `aware_reframe`; framing is a per-account choice).
- `publish_hour` / `publish_dow` are stamped at the **true publish transition**, in the **operator-local**
  timezone (`cfg.operator_tz` via `timeutil.publish_buckets`; fails CLOSED to UTC on an unknown zone — a
  UTC-bucketed hour is noise for a single-region audience):
  - primary publish path: [post/run.py](../../src/fanops/post/run.py):267
  - late reconcile path: [reconcile.py](../../src/fanops/reconcile.py):374-375 (mirrors run.py after `published_at`)

### AGGREGATE — reach per value

- `aggregate_by_dim(led, dim)` ([digest.py](../../src/fanops/digest.py):37) groups **analyzed** posts by one
  stamped attribute (`getattr(p, dim, None)`, None-skipped) → per value `{n, reach_sum, reach_mean, …}`.
  Serves length, opening frame, and framing.
- `timing_bias_winner(led, cfg)` ([timing_bias.py](../../src/fanops/timing_bias.py):31) ranks
  `aggregate_by_dim(led, "publish_hour")` reach-desc / hour-asc, returns the leading hour when it clears the
  gap.

### The gate (shared by every structural actuator)

`p4_unlocked(led, cfg, dim)` = `learning_validated(cfg)` **AND** `enough_attributed_signal(led, dim)`
([validation_gate.py](../../src/fanops/validation_gate.py):42). Signal floor: ≥ `_MIN_ATTRIBUTED_N` (8)
attributed posts across ≥ `_MIN_VALUES` (2) distinct values — otherwise a UCB-style ranker explores forever on
n≈1 cells. `dim_bias_candidates` and `timing_bias_winner` also require the reach leader to beat the runner-up
by `cfg.p4_min_reach_gap` (a comparative winner).

### ACTUATOR — feeding the winner back

- **`apply_p4_dim_bias(led, cfg)`** ([p4_dim_bias.py](../../src/fanops/p4_dim_bias.py):56) — for each dim in
  `_P4_DIMS = ("first_frame_kind", "clip_profile", "top_bias")` (p4_dim_bias.py:27) whose reach winner clears
  the gate, amplifies a representative source via the existing `adjust.amplify` path, injecting the winning
  value as moment-request guidance (framing renders a natural bool hint: "top-anchored" / "centered"). Kill
  switch `FANOPS_P4_DIM_BIAS`, default OFF. Wired at [cli.py](../../src/fanops/cli.py):983.
- **`apply_timing_bias(led, cfg)`** ([timing_bias.py](../../src/fanops/timing_bias.py):79) — writes the
  reach-winning operator-local hour to a control-file prior (`cfg.timing_bias_path`) that `crosspost.surface_time`
  consumes (`hour_hint=`, `tz=`), window-clamped to `cfg.account_window(handle)`. A schedule-slot bias, never a
  publish. Kill switch `FANOPS_TIMING_BIAS`, default OFF. Wired at cli.py:994.

## Invariants

- **C1 firewall — every actuator is amplify/bias-only.** None retire, `_delete_moment_cascade`, or
  `set_*_state`. Grounded in `p4_dim_bias.py:8-14` and mirrored by `timing_bias`.
- **Validation-frozen.** Even with its kill switch ON, each actuator is INERT until `learning_validated` — a
  correctness gate against learning on an unproven / mis-keyed metric shape, not an operator gate.
- **Fail-safe.** Any actuator exception is logged once and leaves the ledger / brief byte-identical (the loop
  never crashes on a learning hiccup; `cmd_run` exit stays 0).
- **No new auto-publish path.** These actuators bias GENERATION and the SCHEDULE slot only. The
  no-auto-publish rule (a post is BORN `awaiting_approval`, only the operator promotes it) is untouched.

## The autonomous driver

`fanops run` (dispatched at [cli.py](../../src/fanops/cli.py):919) runs, after `advance`, `_learn_pass` →
`apply_variant_amplify` → `apply_p4_dim_bias` → `apply_timing_bias`, each in its own transaction, gated on
`is_live_backend` + its kill switch (cli.py:966-994). `cmd_daemon` (cli.py:505) is the launchd packaging of
`fanops run`.

## Tests

- Framing / timing / legibility: [tests/test_culmination_coverage.py](../../tests/test_culmination_coverage.py)
- The gate: `tests/test_validation_gate.py`; existing P4 dims: `tests/test_p4_dim_bias.py`
