# Lever thresholds — selection-layer mechanics

> generated; ENGINEER-owned mechanics — to change one, edit the named line + regenerate. Listed for operator review + approval.

| NAME | current value | what it controls | raising | lowering | edit |
|------|---------------|------------------|---------|----------|------|
| `_MAX_OVERLAP_FRAC` | 0.5 | two picks overlapping more than this fraction of the shorter span are deduped | fewer near-duplicate picks survive | more overlap allowed | `fanops/moments.py:129` |
| `_MAX_TARGET_PICKS` | 30 | per-persona pick ceiling in the moment prompt | model may aim for more picks on long sources | fewer picks requested | `fanops/prompts.py:51` |
| `_target_pick_count` | ceiling `30` on a probed source (0 if unprobed) | how many clips to aim for | more picks allowed | fewer picks allowed | `fanops/prompts.py:55` |
| `filter_peaks_by_intensity` terciles | `lo_thr = scores[n//3]`, `hi_thr = scores[(2*n)//3]` | what score counts as high/low energy for P4b peak filtering | stricter slice (fewer peaks kept) | looser slice | `src/fanops/signals.py` → `filter_peaks_by_intensity` |
| `_EOF_TOLERANCE_S` | 0.5s | pick may extend past probed EOF by this much | more EOF overrun tolerated | stricter EOF bound | `fanops/moments.py:126` |
