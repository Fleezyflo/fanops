# Lever thresholds — selection-layer mechanics

> generated; ENGINEER-owned mechanics — to change one, edit the named line + regenerate. Listed for operator review + approval.

| NAME | current value | what it controls | raising | lowering | edit |
|------|---------------|------------------|---------|----------|------|
| `bands.short` | 8-15s | render floor/ceiling + moment-pick window for `short` profile | wider `short` clips everywhere this band applies | tighter `short` clips | `fanops/bands.py:23` |
| `bands.medium` | 16-26s | render floor/ceiling + moment-pick window for `medium` profile | wider `medium` clips everywhere this band applies | tighter `medium` clips | `fanops/bands.py:24` |
| `bands.long` | 28-45s | render floor/ceiling + moment-pick window for `long` profile | wider `long` clips everywhere this band applies | tighter `long` clips | `fanops/bands.py:25` |
| `bands.talk` | 12-22s | render floor/ceiling + moment-pick window for `talk` profile | wider `talk` clips everywhere this band applies | tighter `talk` clips | `fanops/bands.py:16` |
| `bands.song` | 18-35s | render floor/ceiling + moment-pick window for `song` profile | wider `song` clips everywhere this band applies | tighter `song` clips | `fanops/bands.py:17` |
| `_MAX_OVERLAP_FRAC` | 0.5 | two picks overlapping more than this fraction of the shorter span are deduped | fewer near-duplicate picks survive | more overlap allowed | `fanops/moments.py:128` |
| `_MAX_TARGET_PICKS` | 30 | per-persona pick ceiling in the moment prompt | model may aim for more picks on long sources | fewer picks requested | `fanops/prompts.py:57` |
| `_target_pick_count` | `round(duration / band.span)` capped 1..30 | how many clips to aim for by source length | more picks on long sources | fewer picks | `fanops/prompts.py:61` |
| `filter_peaks_by_intensity` terciles | `lo_thr = scores[n//3]`, `hi_thr = scores[(2*n)//3]` | what score counts as high/low energy for P4b peak filtering | stricter slice (fewer peaks kept) | looser slice | `src/fanops/signals.py` → `filter_peaks_by_intensity` |
| `_MIN_MOMENT_S` | 0.5s | minimum pick duration (noise floor) | reject shorter spans | allow shorter picks | `fanops/moments.py:125` |
| `_EOF_TOLERANCE_S` | 0.5s | pick may extend past probed EOF by this much | more EOF overrun tolerated | stricter EOF bound | `fanops/moments.py:123` |
