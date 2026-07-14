# Decision Record v4 — OpenCV / smart-framing: one construction, fail loud

**Status:** experimental commit on `fix/cv2-required-fail-loud` (PR #633). **Not merged.**
Supersedes v3 and the `22f3380` non-constructing "Fix B", which was correctly rejected: it let a broken
prerequisite (corrupt ONNX / OpenCV ABI mismatch / `create()` failure) fall through to CENTERED output.

**Governing acceptance criterion:** a missing or broken prerequisite must refuse loudly and must never be
normalized into centered output. Centered output is permitted only for a genuine detection miss *after* the
prerequisite has been successfully initialized.

---

## 1. Exact diff from `22f3380`

```
 .github/workflows/ci.yml      |  27 ++++--
 CLAUDE.md                     |   2 +-
 docs/GOLIVE.md                |   2 +-
 docs/RUNBOOK.md               |   2 +-
 scripts/base_install_smoke.py |  81 ++++++++++-------
 scripts/ci_env_probe.py       |  42 +++++++++   (new)
 scripts/lock-deps.sh          |   5 +-
 src/fanops/clip.py            |  25 +++--
 src/fanops/framing.py         | 117 ++++++++++++++++--------
 tests/test_smart_framing.py   | 206 +++++++++++++++++++++++++++++++++---------
 10 files changed, 374 insertions(+), 135 deletions(-)
```
Plus this record and two added tests (OFF-contract + runtime lifetime) in the follow-up commit.

The key reversal vs `22f3380`: `require_cv2`'s non-constructing attr+file check is REPLACED by a real
construction (`_framing_runtime_or_raise`), and `render_account_cut` no longer swallows the refusal.

## 2. The one-construction, fail-loud design

`framing._FramingRuntime{cv2, detector}` — a per-resolution object (NOT a process-global).

`framing._framing_runtime_or_raise(cfg)` — the sole constructor AND the prerequisite gate:
1. `_cv2()` is None → `ToolchainMissingError`
2. `cv2.FaceDetectorYN.create` attr missing (OpenCV too old) → `ToolchainMissingError`
3. vendored ONNX absent → `ToolchainMissingError`
4. the real `FaceDetectorYN.create` **raises** → caught and re-raised as `ToolchainMissingError` (`from e`)
5. the real `FaceDetectorYN.create` returns **None** → `ToolchainMissingError`
6. else → the runtime carrying the constructed detector

`clip._resolve_framing` calls it ONCE and threads `_rt=` into `detect_window` / `speaker_track` /
`subject_focus` / `motion_saliency`, which reuse `_rt.detector` (or `_rt.cv2` for saliency). A genuine
detection miss still returns `(None,None,None)` centered; a broken prerequisite never reaches that path.

**Production defect this exposed and fixed:** `clip.render_account_cut`'s outer
`except Exception: return False, None` was SWALLOWING the prerequisite refusal into a centered fail-open. It
now `except ToolchainMissingError: raise` before the broad catch (only ffmpeg/parse failures fail open).
`render_moment` and `_supercut_span_entries` already propagate.

## 3. Constructor-return-`None` and constructor-exception results

Direct-call harness (real cv2 5.0.0, py3.12.8) and the committed tests:
- `create()` → None ⇒ `_resolve_framing` RAISES `ToolchainMissingError` ✓
- `create()` raises ⇒ RAISES `ToolchainMissingError` ✓
- constructor failure does NOT reach detection (`detect_window` call count == 0 on refusal) ✓ — so it cannot
  become centered output
- detector built OK + no face found ⇒ `(None,None,None)` centered, NO raise ✓

## 4. Per-entry-point refusal results

`_resolve_framing` refuses on: cv2 None ✓ · `FaceDetectorYN`/`.create` missing ✓ · model absent ✓ ·
`create()`→None ✓ · `create()` raises ✓.
`render_moment` refuses ✓ · `render_account_cut` refuses ✓ (this test **caught the swallow defect** in §2) ·
`_supercut_span_entries` refuses ✓ · no autouse/suite-wide bypass exists ✓ (asserted against `conftest.py`).
The five router `require_cv2 = lambda` stubs are REMOVED — cv2 is genuinely installed in the unit lane, so the
real runtime builds and the stubbed DETECTION functions drive the routing.

### 4a. OFF contract — the runtime is NOT constructed when smart framing is OFF

`clip._resolve_framing` evaluates the toggle FIRST:
```python
if not cfg.smart_framing:
    return None, None, None          # centered; no runtime, no cv2
rt = framing._framing_runtime_or_raise(cfg)
```
`test_resolve_off_never_constructs_runtime` stubs BOTH `_framing_runtime_or_raise` and `_cv2` to raise
`AssertionError` if called, sets `FANOPS_SMART_FRAMING=0`, and asserts `(None,None,None)`. **Negative control
run:** with the ordering deliberately inverted (runtime built before the toggle check) the test FAILS with
"framing runtime CONSTRUCTED while smart_framing is OFF" — so it is a real guard, not a tautology. The retained
OFF path therefore does not require OpenCV.

### 4b. Object lifetime / concurrency — the runtime never crosses concurrent work

The runtime is created inside each `_resolve_framing` invocation and is never stored in module, config, source,
or any shared cache (`framing.py`: constructed at the `return _FramingRuntime(...)` and held only in the caller's
local `rt`; no `global`, no module cache).
`test_framing_runtime_is_per_invocation_never_shared` asserts, over 2 sequential + 2 concurrent
(`ThreadPoolExecutor`) resolutions: **4 distinct `_FramingRuntime` objects and 4 distinct detector objects**, and
that no `_FramingRuntime` is retained in `vars(framing)`, on the `Config`, or on the `Source`. This is why YuNet's
mutable `setInputSize` state is safe: no detector is ever shared across concurrent resolutions.

## 5. Actual CI ffmpeg probe (`scripts/ci_env_probe.py`, unit job — MEASURED, not inferred)

CI run 29328162939, unit job:
```
python:  3.12.13
machine: x86_64
ffmpeg:  None          <- shutil.which("ffmpeg"), genuinely absent
ffprobe: None
cv2:     5.0.0         <- present (the [framing] extra, from the hashed lock)
OK: ffmpeg is NOT on PATH in this lane — frame extraction cannot run; detection fails open to centered
```
The probe also logs a loud `NOTE:` if a future `ubuntu-latest` image ever ADDS ffmpeg, so the unit-lane
contract cannot silently rot.

## 6. CI constructor / extraction counts — what CI actually established

**CI passed tests asserting one constructor call per resolution and the expected extraction counts.**
CI did **not** report the numeric values: pytest ran with `-q`, which suppresses the tests' stdout on pass, so
the `[framing-counts]` numbers do not appear in any CI log or artifact. The evidence is the passing assertions
inside `test_one_resolve_constructs_detector_exactly_once` and
`test_framing_construction_and_extraction_counts_reported` (both integration-marked, real OpenCV 5.0.0, x86_64,
e2e job of run 29328162939: `23 passed`).

The numeric values below are from the LOCAL run of the same tests (macOS/arm64, real cv2 5.0.0) and are
reported as local measurements, not CI output:
```
COLD resolution: FaceDetectorYN.create=1  detect_window=1  grid_extract=2
WARM resolution: FaceDetectorYN.create=1  detect_window=1  grid_extract=1
```
Init scope = PER-RESOLUTION: exactly one construction every resolution, never two. The sidecar caches detection
RESULTS, not the detector object, so a warm resolution still constructs one but extracts less.

## 7. Clean-venv base-install results

The `base-install` job creates a LITERAL venv (`python -m venv .venv-base`; `pip install .`, no extras). The
smoke asserts: cv2 ABSENT · `fanops` + CLI import · a representative non-render operation
(`fanops.cli.main(["--help"])`) · the render prerequisite REFUSES. It asserts **nothing** about
smart-framing OFF — that off-switch policy is a separate decision (F3) and is not entrenched here.
Validated in `docker --platform linux/amd64 python:3.12-slim` (exit 0) and **passed in CI** (run 29328162939).

## 8. Linux x86-64 lock-generation evidence

`docker run --platform linux/amd64 python:3.12-slim`:
- container arch **x86_64** · python **3.12.13** · pip-compile **7.5.3**
- `pip-compile --generate-hashes --allow-unsafe --strip-extras --extra dev --extra studio --extra framing --output-file requirements/ci-unit.txt pyproject.toml`
- result: **byte-identical** to the prior ARM64-generated lock — `--generate-hashes` enumerates ALL platform
  wheels (28 opencv wheel hashes across manylinux x86_64/aarch64, macOS, Windows), so the resolution is
  arch-independent here. The `bdist.linux-aarch64` artifact was produced by the smoke's `pip install .` (a local
  wheel build), not by lock generation.
- lock drift guard: pyproject deps unchanged ⇒ not triggered. `--require-hashes` install: PROVEN on x86_64 CI
  (`opencv-python-headless==5.0.0.93`, `numpy==2.5.1` installed from the lock).

## 9. Timing — observed, not causally isolated, and inside the noise

**Observed unit duration improved from 78.29s to 72.04s across two different commits. The one-construction
change is the leading explanation, but causal attribution was not isolated.** `22f3380 → 273bef9` changed
production code, tests, CI diagnostics, docs, smoke behavior, and the test count (4820 → 4825), so the delta
cannot be attributed to the guard alone.

**A subsequent run makes even the "leading explanation" framing unsafe.** Run 29330823058 (`8071405` — the same
production code as `273bef9`, plus two added tests) measured **79.33s**, against `273bef9`'s **72.04s**. That is
**+7.3s of run-to-run variance on essentially identical code — the same magnitude as the entire observed
delta.** So the timing series does not establish that the one-construction change made the suite faster; it
establishes only that the candidate stays comfortably inside budget. Any real performance claim would require
repeated runs of controlled variants (same tests, same code, only the guard implementation differing), which was
not done.

| run | commit | tests | unit pytest |
|---|---|---|---|
| 29312364865 | `6061c16c` (main baseline) | 4814 | 72.17s |
| 29324715957 | `22f3380` (double-build guard) | 4820 | 78.29s |
| 29328162939 | `273bef9` (one-construction) | 4825 | 72.04s |
| 29330823058 | `8071405` (same code + 2 tests) | 4827 | 79.33s |

The v3 claim that "+6.12s was caused by 83 detector constructions" is **WITHDRAWN** — construction is one per
*resolution*, not per test, and the suite-wide resolution count was never measured.

SLO: every run passed (`79.33s <= 135s budget` on the latest). An SLO failure would reject the candidate pending
profiling; the budget is not an automatic fallback and was not raised.

## 10. Documentation and deployment findings

- No Dockerfiles, docker-compose files, systemd units, launchd plists, or standalone `requirements*.txt` for a
  production render host exist. FanOps runs as an editable `pip install -e` on the operator's machine driven by
  a launchd daemon — the render host IS the editable install, so there is no separate deploy manifest to audit.
- `[framing]` coverage BEFORE: `README.md` ✓ (already `.[dev,studio,transcribe,framing]`); `docs/RUNBOOK.md` ✗,
  `docs/GOLIVE.md` ✗, `CLAUDE.md` ✗. FIXED: added to all three with the note that smart framing defaults ON and
  the render refuses without it. `pip install 'fanops[framing]'` is now the documented remediation.
- CLI preflight, doctor enhancements, toggle policy, base-dependency policy, and detection-miss telemetry remain
  in their separate follow-up records (F1–F5).

## 11. Final disposition

**Accept for merge**, subject to the operator's judgment on the two open product decisions below (neither is a
defect in this candidate).

Every acceptance criterion is met and CI-verified (run 29328162939 + the follow-up run for the two added tests):
a broken prerequisite refuses loudly at every render entry point and can never become centered output; a genuine
detection miss still centers; the detector is constructed exactly once per resolution; the OFF path builds no
runtime and requires no OpenCV; the runtime never crosses concurrent work; no suite-wide bypass exists; the base
install refuses; the unit lane's ffmpeg-absent/cv2-present contract is measured, not assumed.

**Open product decisions (out of scope here):** the off-switch policy (retain / deprecate / remove — F3) and
cv2-as-base-dependency vs the optional extra (F4).

**Known, accepted cost:** construction is per-resolution and is not amortized across resolutions of the same
source (the sidecar caches results, not the detector), so a warm resolution builds one detector it may not use.
That is the price of re-proving the prerequisite every resolution. A source-scoped runtime cache would require
the thread-safety analysis that a shared YuNet detector demands, and is deliberately not attempted here.

---

## Retractions carried into v4
- "the rare corrupt-ONNX case still fails open to centered" (v3) — that WAS the defect; v4 constructs and refuses.
- "+6.12s caused by 83 detector constructions" — withdrawn; construction is per-resolution, count unmeasured.
- "only the guard changed between 22f3380 and 273bef9" — false; many things changed (see §9).
- "CI reported the constructor counts" — false; CI passed the assertions, the numbers were not emitted (see §6).
- "the ARM64-generated lock is CI-faithful" — replaced by an amd64 regeneration and the byte-identical finding.
