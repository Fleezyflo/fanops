# CI test performance investigation

**Date:** 2026-07-10  
**Branch:** `cursor/ci-test-perf-investigation-e517`  
**HEAD:** `5af3b857` (current `main` at investigation time)

## Executive summary

The **unit job** is dominated by the **Unit tests** step (~91% of job wall on current main). Unit pytest time **roughly doubled** after MOL-292 PR-1 (`84d9526`): CI-reported **218.67s → 390–403s** (+171–184s) with only **+6 tests** (+0.15%). The step-change aligns with commit `84d9526`, which added conftest hooks that call `Config.refresh_env()` → `Settings.runtime_load()` on every `monkeypatch.setenv`/`delenv` and on every test's hermetic env fixture setup/teardown. Instrumentation on current main counted **62,161** `refresh_env` calls in one full unit run; at **~1.36 ms/call** (local micro-benchmark) that alone accounts for **~85 s** locally and plausibly **~120–185 s** on slower CI runners — strongly supporting fixture/env-refresh overhead as the primary regression mechanism. The **e2e job** wall is dominated by **pip ci-e2e install** and **apt ffmpeg** when caches are cold; pytest integration (**17 tests**, ~22–35s reported) is the largest pytest slice but a minority of total e2e wall.

---

## 1. Unit job — step timing budget

Sources: GitHub Actions `gh run view <id> --json jobs` (step `startedAt`/`completedAt`). Push runs (no PR-only steps).

| Step | Pre-MOL-292 `4b37980` run 29060145209 | MOL-292 PR-1 `84d9526` run 29088497078 | Current main `5af3b85` run 29090531793 |
|------|---------------------------------------|----------------------------------------|----------------------------------------|
| **Job wall** | **260 s** | **432 s** | **442 s** |
| Checkout | 2 | 1 | 2 |
| setup-python | 21 | 19 | 18 |
| Install ci-unit lock | 11 | 13 | 12 |
| Lint (ruff) | &lt;1 | &lt;1 | &lt;1 |
| **Unit tests (pytest)** | **220** | **392** | **404** |
| pip-audit | 3 | 3 | 3 |
| Hook regression test | 1 | (in step 10, ~1) | 1 |

**Notes:**
- PR-only steps (secret scan, lockfile guard) were **skipped** on these push runs.
- Post-MOL-292, **pytest ≈ 91–93%** of unit job wall (404/442 on current main).
- Non-pytest steps are **stable** (~35 s combined); the **+182 s** job wall increase is almost entirely the **Unit tests** step (+184 s).

---

## 2. Unit job — pytest breakdown

### Command (exact, from [`.github/workflows/ci.yml`](../.github/workflows/ci.yml))

```bash
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m "not integration and not slow"
```

### Collection (current main, local CI-parity install)

| Metric | Value |
|--------|-------|
| Collected | 4095 |
| Deselected | 25 |
| Total in tree | 4120 |

Deselected = integration + slow + asr + ci_hook_regression markers (25 items).

### Total pytest time

| Source | Passed | Deselected | pytest reported |
|--------|--------|------------|-----------------|
| CI pre-MOL-292 (29060145209) | 4089 | 25 | **218.67 s** |
| CI MOL-292 PR-1 (29088497078) | 4092 | 25 | **390.34 s** |
| CI current main (29090531793) | 4095 | 25 | **402.73 s** |
| Local main (same install, 4 vCPU VM) | 4095 | 25 | **316.23 s** (`--durations=50`) |

Local VM runs **~21–27% faster** than CI for the same suite; regression **direction and step timing** are consistent across environments.

### Top 50 slow tests (local main, call time)

| Time (s) | Test |
|----------|------|
| 11.39 | `tests/test_zernio.py::test_publish_network_error_parks_needs_reconcile` |
| 9.53 | `tests/test_postiz.py::test_publish_network_error_parks_needs_reconcile_no_repost` |
| 5.90 | `tests/test_fanops_hashtags.py::test_refresh_store_if_due_throttles_and_fail_open` |
| 4.22 | `tests/test_doctor.py::test_doctor_warns_on_expiring_meta_token` |
| 4.08 | `tests/test_studio_nav.py::test_full_pages_carry_rail` |
| 4.07 | `tests/test_studio_golive.py::test_promote_account_planned_to_active_and_demoted_in_status` |
| 3.70 | `tests/test_doctor.py::test_doctor_requires_distinct_ig_user_id_per_active_account` |
| 3.42 | `tests/test_doctor.py::test_doctor_fails_on_dead_daemon_or_past_due_backlog` |
| 2.95 | `tests/test_studio_views.py::test_golive_status_reports_learning_validated` |
| 2.87 | `tests/test_studio_golive.py::test_golive_connect_step_collapses_once_connected` |

(Full top-50 list: `/tmp/ci-perf/unit-durations-main.txt` on investigation VM.)

### Top modules (aggregated call time, durations ≥ 0.05 s)

From local run with `--durations=0 --durations-min=0.05` (651 tests printed, **219.9 s** summed call time; pytest total **323.24 s**):

| Module | Summed call (s) | % of printed call sum |
|--------|-----------------|----------------------|
| `tests/test_studio_golive.py` | 54.89 | 25.0% |
| `tests/test_doctor.py` | 33.32 | 15.2% |
| `tests/test_postiz.py` | 13.98 | 6.4% |
| `tests/test_zernio.py` | 10.19 | 4.6% |
| `tests/test_fanops_hashtags.py` | 5.96 | 2.7% |

**~50% of printed call time (≥0.05 s)** is reached by the **top 4 modules** above (112.4 s of 219.9 s printed).  
**11,631** tests had call &lt; 0.05 s (hidden from duration list); much of the MOL-292 regression lands in this long tail via per-test `refresh_env` overhead (see §3).

---

## 3. Unit job — regression analysis

### Commits / SHAs compared

| SHA | Label | CI unit pytest | Local unit pytest |
|-----|-------|----------------|-------------------|
| `4b37980` | Pre-MOL-292 (last main before EnvSnapshot) | 218.67 s | **211.33 s** |
| `84d9526` | MOL-292 PR-1 (EnvSnapshot + conftest refresh hooks) | 390.34 s | **318.97 s** |
| `9fb409e` | MOL-292 PR-2 (doctor `strict_validate`) | (same job structure) | **337.54 s** |
| `5af3b85` | Current main (MOL-292 complete) | 402.73 s | **315.89–316.23 s** |

**Step change:** The large jump occurs at **`84d9526`** (+172 s CI, +108 s local). PR-2/3 add smaller variance, not a second step change.

### What changed

| Factor | Evidence | Impact |
|--------|----------|--------|
| Test count | 4089 → 4095 (+6) | **Negligible** |
| Slower individual tests | Top tests (zernio/postiz/doctor) similar ranks pre/post; `test_config.py` alone: 0.29 s → 0.34 s | **Minor** |
| Fixture/session overhead | See mechanism below | **Primary** |
| CI install/cache | Install step stable ~11–13 s | **Not a factor** |
| `scripts/check.sh` MOL-292 grep gates | Not run in CI unit job | **Excluded** |

### Regression mechanism (proven / strongly supported)

**Symptom:** +171–184 s CI pytest on unit marker with +6 tests.

**Mechanism:** Commit `84d9526` ([`tests/conftest.py`](../tests/conftest.py)):

1. Registers every `Config()` in a `WeakSet`.
2. Autouse `_config_refresh_on_monkeypatch_env` wraps `monkeypatch.setenv` / `delenv` → `_refresh_all_config_env()`.
3. Autouse `_hermetic_publish_env` calls `_refresh_all_config_env()` at **start and end** of every test.
4. Each refresh → `Config.refresh_env()` → `load_env_snapshot()` → `Settings.runtime_load()` ([`src/fanops/settings.py`](../src/fanops/settings.py): `load_dotenv` + `pydantic` `model_validate` on full env dict).

**Evidence:**

| Measurement | Value | Source |
|-------------|-------|--------|
| `refresh_env` invocations (full unit suite, main) | **62,161** | `scripts/_ci_perf_count_plugin.py` on investigation branch |
| `Settings.runtime_load` latency (median, 20 runs) | **1.36 ms** | Local micro-benchmark |
| Implied overhead (62,161 × 1.36 ms) | **~84.5 s** | Arithmetic |
| Observed local regression (211 → 316 s) | **~105 s** | Bisect runs |
| Pre-MOL-292 conftest | **No** `refresh_env` / `_config_refresh` | `_wt/pre-mol-292/tests/conftest.py` grep |
| CI step timing step at `84d9526` | Unit tests 220 s → 392 s | Run 29088497078 |

**Confidence:** **Strongly supported** that MOL-292 conftest env-refresh hooks explain **most** of the regression; **proven** that the step-change commit is `84d9526`. Remaining gap to full CI +184 s is plausibly slower `runtime_load` on CI runners and/or higher per-test refresh multiplicity when more `Config` instances are live (62,161 / 4095 ≈ **15.2 refresh_env calls per test** on average).

---

## 4. E2E job — step timing budget

| Step | Pre-MOL-292 29060145209 | MOL-292 PR-1 29088497078 | Current main 29090531793 |
|------|---------------------------|--------------------------|--------------------------|
| **Job wall** | **192 s** | **190 s** | **146 s** |
| Checkout | 1 | 2 | 1 |
| setup-python | 45 | 36 | 20 |
| apt cache restore | (in setup) | (in setup) | &lt;1 |
| Install ffmpeg + espeak | 32 | 63 | 20 |
| Install ci-e2e lock | 66 | 53 | 57 |
| Cache whisper tiny | 3 | 3 | 1 |
| Pre-fetch whisper tiny | 3 | 1 | 3 |
| Verify toolchain | &lt;1 | &lt;1 | &lt;1 |
| **Integration pytest** | **34** (wall) | **24** | **36** |
| **Slow pytest** | **3** | **2** | **3** |

E2E job wall **varies with cache** (apt/pip/whisper). On a warm cache (current main 146 s), **pip e2e + integration pytest** dominate; on cold cache (pre-MOL-292 192 s), **pip e2e (66 s) + apt (32 s)** dominate.

---

## 5. E2E job — pytest breakdown

### Integration suite

**Command:**

```bash
FANOPS_REQUIRE_E2E=1 FANOPS_REQUIRE_STUDIO=1 FANOPS_WHISPER_MODEL=tiny \
  python -m pytest -q -m "integration and not ci_hook_regression and not asr" -rs
```

| Source | Passed | Deselected | pytest reported |
|--------|--------|------------|-----------------|
| CI pre-MOL-292 | 17 | 4097 | 32.65 s |
| CI MOL-292 PR-1 | 17 | 4100 | 22.27 s |
| CI current main | 17 | 4103 | 34.49 s |
| Local main (PATH includes `.venv/bin` for `whisper` CLI) | 17 | 4103 | **17.28 s** |

**Slowest integration tests (local, call time):**

| Time (s) | Test | Dominant cost type |
|----------|------|-------------------|
| 10.50 | `tests/integration/test_e2e_real.py::test_real_transcript_drives_moment_and_real_clip_renders` | **ffmpeg + whisper CLI + pipeline** |
| 1.56 | `tests/test_compose.py::test_real_moviepy_prepend_intro_continuous_audio` | **moviepy/ffmpeg** |
| 0.91 | `tests/integration/test_variation_render.py::test_two_accounts_get_distinct_burned_hooks` | **ffmpeg render** |
| 0.85 | `tests/test_render_atomicity.py::test_render_reframed_real_ffmpeg_lands_nonempty` | **ffmpeg** |
| 0.61 | `tests/integration/test_discover_real.py::test_discover_real_thumbnails_then_intake_to_inbox` | **ffmpeg + network-ish** |

Remaining integration tests are **&lt;0.04 s** call (mostly hermetic disk/ledger paths).

### Slow suite

**Command:**

```bash
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m slow
```

| Source | Passed | Deselected | pytest reported |
|--------|--------|------------|-----------------|
| CI (all three runs) | 5 | 4112–4115 | **1.46–2.09 s** |
| Local main | 5 | 4115 | **2.70 s** |

**Slowest:** `test_per_persona_e2e.py::test_closed_loop_single_owner_lift_round_trip` (0.36 s local) — **Python/hermetic**, no real toolchain.

---

## 6. Mode matrix

| Invocation | Env flags | Selected | Deselected | pytest time (CI main) | Dominant cost |
|------------|-----------|----------|------------|----------------------|---------------|
| `-m "not integration and not slow"` | `FANOPS_REQUIRE_STUDIO=1` | 4095 | 25 | **402.73 s** | Python + SQLite + studio HTTP; post-MOL-292: **env refresh** |
| `-m "integration and not ci_hook_regression and not asr" -rs` | `FANOPS_REQUIRE_E2E=1`, `FANOPS_REQUIRE_STUDIO=1`, `FANOPS_WHISPER_MODEL=tiny` | 17 | 4103 | **34.49 s** | **ffmpeg/whisper/moviepy** subprocess |
| `-m slow` | `FANOPS_REQUIRE_STUDIO=1` | 5 | 4115 | **2.09 s** | Hermetic Python E2E proofs |
| `tests/test_ci_require_e2e.py` | `FANOPS_REQUIRE_E2E=1` | 1 | — | **&lt;1 s** (must **exit 1**) | Hook only |
| `-m integration` (not in CI) | — | 20 | 4100 | — | Includes `ci_hook_regression` + `asr` |

### Flag behavior (measured / code-proven)

| Flag | Where set | Effect |
|------|-----------|--------|
| `FANOPS_REQUIRE_STUDIO=1` | Unit + e2e jobs | [`pytest_configure`](../tests/conftest.py): **session abort** if `flask` missing (prevents silent studio skips). Flask present in ci-unit/ci-e2e locks. |
| `FANOPS_REQUIRE_E2E=1` | E2e integration + hook step | [`pytest_runtest_makereport`](../tests/conftest.py): integration **skip → fail**. Hook test: **exit 1** verified locally. |
| `FANOPS_WHISPER_MODEL=tiny` | E2e integration | Pins whisper model for `test_e2e_real` (avoids mid-test download when cache warm). Prefetch step loads tiny before pytest. |

---

## 7. Bottleneck ranking (both jobs)

Ranked by **seconds contributed to perceived CI wait** ≈ `max(unit_wall, e2e_wall)` per run (jobs parallel).

| Rank | Location | Seconds (current main CI) | Root cause | Evidence | Confidence |
|------|----------|----------------------------|------------|----------|------------|
| 1 | Unit: pytest step | 404 | Full unit suite 4095 tests | Step wall run 29090531793; log `402.73s` | **Proven** |
| 2 | Unit: setup-python | 18 | Actions pip cache restore | Step timing JSON | **Proven** |
| 3 | E2E: pip ci-e2e install | 57 | torch/whisper/opencv/moviepy hash install | Step timing; `ci-e2e.txt` 1373 lines | **Proven** |
| 4 | E2E: integration pytest | 34 (wall) / 34.5 (reported) | 17 real-tooling tests; `test_e2e_real` ~10 s local | CI log + local `--durations` | **Proven** |
| 5 | E2E: apt ffmpeg+espeak | 20 | `apt-get install` | Step timing (varies 20–63 s) | **Proven** |
| 6 | Unit: pip ci-unit install | 12 | Hash-verified dev+studio lock | Step timing | **Proven** |
| 7 | Unit pytest regression (delta vs pre-MOL-292) | **+184** | MOL-292 conftest `refresh_env` on every env mutation + hermetic fixture | Bisect at `84d9526`; 62,161 calls; `runtime_load` ~1.36 ms | **Strongly supported** |
| 8 | E2E: whisper prefetch | 3 | `whisper.load_model('tiny')` | Step timing | **Proven** |
| 9 | E2E: slow pytest | 2 | 5 hermetic cross-face proofs | CI log | **Proven** |
| 10 | Unit: ruff + pip-audit + hook | ~4 | Lint + advisory audit + single-file hook | Step timing | **Proven** |

---

## 8. Reproducibility

### CI runs

| Run ID | SHA | Event |
|--------|-----|-------|
| 29060145209 | `4b37980a` | Pre-MOL-292 push |
| 29088497078 | `84d95260` | MOL-292 PR-1 push |
| 29090531793 | `5af3b857` | Current main push |

Extract steps: `python3 scripts/ci_perf_extract_runs.py <run_ids>`

### Local investigation VM

- **OS:** Linux 6.12, Ubuntu (cursor cloud VM)
- **CPU:** 4 vCPU
- **RAM:** 15 GiB
- **Python:** 3.12.3 (venv)

### Install (CI parity)

```bash
pip install --require-hashes -r requirements/ci-unit.txt
pip install -e . --no-deps
# E2E additionally:
pip install --require-hashes -r requirements/ci-e2e.txt
sudo apt-get install -y ffmpeg espeak
python -c "import whisper; whisper.load_model('tiny')"
```

### Pytest commands

```bash
# Unit (CI)
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m "not integration and not slow"

# Unit profiling
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m "not integration and not slow" --durations=50

# refresh_env count (investigation plugin)
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m "not integration and not slow" -p scripts._ci_perf_count_plugin

# E2E integration (whisper CLI must be on PATH)
PATH="$PWD/.venv/bin:$PATH" FANOPS_REQUIRE_E2E=1 FANOPS_REQUIRE_STUDIO=1 FANOPS_WHISPER_MODEL=tiny \
  python -m pytest -q -m "integration and not ci_hook_regression and not asr" -rs --durations=20

# E2E slow
FANOPS_REQUIRE_STUDIO=1 python -m pytest -q -m slow --durations=10
```

### Artifacts on investigation VM

- `/tmp/ci-perf/unit-durations-main.txt`
- `/tmp/ci-perf/unit-durations-all.txt`
- `/tmp/ci-perf/bisect-{4b37980,84d9526,9fb409e}.txt`
- `/tmp/ci-perf/refresh-count-main.txt`
- `/tmp/ci-perf/e2e-integration.txt`, `e2e-slow.txt`
- `/tmp/ci-runs-extract.txt`

### Lockfiles

- [requirements/ci-unit.txt](../requirements/ci-unit.txt)
- [requirements/ci-e2e.txt](../requirements/ci-e2e.txt)

---

## 9. Open questions

1. **Local vs CI absolute times:** Local unit suite ~316 s vs CI ~403 s (~27% faster locally). Regression **ratios** are consistent; absolute CI attribution uses CI logs + call-count arithmetic.
2. **Per-test setup/teardown vs call:** Not decomposed; `--setup-show` on top-10 tests was not run (durations implicate distributed overhead more than single slow fixtures).
3. **PR-only step cost:** Secret scan + lockfile guard not measured (skipped on push runs used here).
4. **`REFRESH_ALL_COUNT=0` in plugin:** Plugin wrapped `cf._refresh_all_config_env` after import order; leaf `refresh_env` count (62,161) is the reliable metric.
5. **E2E apt variance:** 20–63 s across runs — cache/state dependent; use range in planning, not single number.

---

*Investigation only — no fix recommendations.*
