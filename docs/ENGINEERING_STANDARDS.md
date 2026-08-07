<!-- Engineering Standards — code-craft guidance, GUIDANCE-ONLY.
     Base: origin/main @ a79528d (#676), revalidated 2026-07-16; de-governanced 2026-07-25
     (the prose governance layer was deleted — docs/ENFORCEMENT.md indexes what actually enforces).
     This document claims NO gating. Where a real mechanism enforces a rule stated here, the
     mechanism is named; everything else is convention held by review. -->

# FanOps — Engineering Standards

> **What this document is.** The **code-craft layer**: how code is *written* here day-to-day — naming,
> layout, boundaries, typing, flags, test craft, observability channels, public surface. It is
> guidance, not a gate: the mechanisms that actually block a merge or refuse at runtime are indexed
> in [`docs/ENFORCEMENT.md`](ENFORCEMENT.md).

**Position.** Two rules of construction survive from the old governance layer, because they are
correct:
1. **No second registry.** CI controls are referenced by `id` against
   [`.github/ci-control-registry.yml`](../.github/ci-control-registry.yml), never restated.
2. **No competing authority.** When this document disagrees with executable source, tests, or live
   CI config, the executable plane wins and this document is corrected.

**Field key (every OWNED standard).** *Rule · Rationale · Evidence.* Sections marked
**[REFERENCE]** describe behavior owned by a real mechanism elsewhere and restate nothing.

> **Anchors are hints.** Cite the **symbol**; treat any `file:line` as a hint and re-`grep`.
> Line numbers in Evidence below are hints only.

---

## 1 · Naming — **[OWNED]**

### STD-NAME-01 — `snake_case` functions, `PascalCase` classes, no exceptions
- **Rule:** every `def`/method is `snake_case` (optionally `_`/`__`-prefixed); every class is `PascalCase`.
- **Rationale:** uniform call-site reading across 130 modules; the one convention with zero measured violations — cheap to keep, noisy to break.
- **Evidence:** exhaustive AST scan @ `a79528d`: 1889/1889 functions and 159/159 classes conform.

### STD-NAME-02 — a closed string set is a `class X(str, Enum)`; module-private tunables are `_UPPER`
- **Rule:** state machines / closed value sets subclass `(str, Enum)`; module-level tunables are `_UPPER_SNAKE` and private by default; a `UPPER` (public) name means it is deliberately cross-module.
- **Rationale:** `(str, Enum)` is JSON-serializable for the ledger by construction; private-by-default keeps the cross-module surface visible (`STD-API-01`).
- **Evidence:** 16/16 enums use `(str, Enum)` (`models.py` SourceState/MomentState/ClipState/PostState/…, `accounts.AccountStatus`, `reframe.ReframeClass`); 335 `_UPPER` vs 88 `UPPER` top-level constants.

### STD-NAME-03 — first-party env vars are `FANOPS_*`; persisted ids are `<kind>_<12-hex-sha1>`
- **Rule:** every first-party env var is `FANOPS_`-prefixed; every persisted id is content-addressed via `ids.py` — never Python's builtin `hash()` (salted per interpreter, PEP 456).
- **Rationale:** the env surface is a trust boundary; a salted id would break content-addressing across processes.
- **Evidence:** `ids.py` (`make_id`/`child_id`/`content_id`/`surface_key`, `sha1(...)[:12]`, with the anti-`hash()` rationale stated in-module); 62 `FANOPS_*` names.

---

## 2 · Repository layout — **[OWNED]**

### STD-LAYOUT-01 — src-layout; a subsystem becomes a **subpackage only when gated by an optional dependency**
- **Rule:** code lives under `src/fanops/`. A flat module is the default. A subpackage (`post/`, `studio/`) is justified **only** by an optional-extra boundary that must stay importable without the extra.
- **Rationale:** the two subpackages exist to keep `import fanops` free of Flask/provider deps; size alone never justifies a package (that is what `STD-LAYOUT-02` is for).
- **Evidence:** `studio/__init__.py` ("Import app.py LAZILY (it pulls Flask); keeping this package init Flask-free lets `import fanops.studio` work on a core, no-`[studio]` install"); `post/__init__.py` (Poster interface + factory); `pyproject.toml` extras.

### STD-LAYOUT-02 — a growing module splits into a **prefixed flat family**, byte-identical at extraction
- **Rule:** when a module outgrows one concern, split it into `<stem>_<concern>.py` siblings and keep the original as a **facade** (`STD-BOUND-01`). The extraction commit changes **no behavior** and says so.
- **Rationale:** preserves import paths and review legibility; the byte-identical clause makes the split reviewable as a pure move.
- **Evidence:** `persona_directives.py` / `persona_research.py` / `persona_store.py` each open "extracted from personas.py, audit #6 — behavior byte-identical"; same shape for `ledger*`, `variant_*`, `config*`, `cutover*`.

### STD-LAYOUT-03 — module size is budgeted, not capped
- **Rule:** a module over **~1,200 lines** is a split candidate; exceeding it is allowed but should be a noted decision, not drift.
- **Rationale:** honest about reality — `cli.py` and `studio/views.py` are both over budget and both are *deliberate* (a single argparse dispatcher; a re-export facade). A hard cap would force a worse structure; no budget at all lets them grow unwatched (`cli.py` grew 1448→1465 during this audit alone).
- **Evidence:** @ `a79528d`: `cli.py` 1465, `studio/views.py` 1435, `studio/actions.py` 1188, `config.py` 1151. None is a declared hot file (`.agents/lanes.json`).

---

## 3 · Module boundaries — **[OWNED]**

### STD-BOUND-01 — a facade re-exports; siblings never import each other
- **Rule:** `actions.py` / `views.py` are **facades** that re-export from their `actions_*` / `views_*` siblings. A sibling may import `*_common`, **never another sibling**. The facade is the only aggregator.
- **Rationale:** keeps the studio import graph a strict 2-level tree with no lateral edges — the cheapest possible acyclicity guarantee.
- **Evidence:** `actions_run.py` ("depends only on actions_common … never on a sibling action module, so the import graph stays acyclic"); `actions_common.py` ("Imports nothing from fanops.* — a leaf module"); grep @ `a79528d`: **0** sibling imports across `actions_{run,approve,casting,wipe,segments}.py`.

### STD-BOUND-02 — a Studio route is thin: parse → **one** action/view → render
- **Rule:** blueprint-per-tab (`app_routes_*.py`, one `register_*_routes(app, cfg)` each). A route parses the form, calls exactly **one** `actions_*` (mutate) or `views_*` (read), and renders. Business logic never lives in a route.
- **Rationale:** makes every mutation attributable to one function and keeps routes trivially reviewable.
- **Evidence:** `src/fanops/studio/CLAUDE.md` (the layer-discipline rulebook); `app.py` registers 7 route modules.

### STD-BOUND-03 — `views_*` are pure reads; every mutation goes through **one** `Ledger.transaction`
- **Rule:** `views_*` project `Ledger.load` and never write. `actions_*` mutate inside exactly one `Ledger.transaction`. Sanctioned exceptions are named in `studio/CLAUDE.md`, not invented ad hoc.
- **Rationale:** one lock-safe load→mutate→save per operation is what makes concurrent daemon/Studio/CLI access safe.
- **Evidence:** `ledger.transaction` (lock-before-load, closing the AUDIT-B4 lost-update window); `studio/CLAUDE.md` names the two sanctioned read-side exceptions.

---

## 4 · Imports — **[REFERENCE]**

Owned by `tools/arch` (unit-lane enforced — see `docs/ENFORCEMENT.md`):
- **`ARCH-004`** — no new compile-time import cycle (one baselined cycle).
- **`ARCH-007`** — a must-stay-lazy import may not be hoisted to module level. *Hoisting looks like a cleanup and bricks process start.*

**Craft note (not a new rule):** a cross-subsystem "upward" import (e.g. `health_model` → `studio`, `post/run` → `studio.views_common`) is legal **only** as an in-function import, and is pinned by `ARCH-007`. If you are writing one, you are on a governed edge — read the rule text in `tools/arch/policy.py` first.

---

## 5 · Versioning — **[OWNED]**

### STD-VER-01 — the package version has exactly ONE authority: `pyproject.toml`
- **Rule:** `pyproject.toml [project].version` is the sole version authority. `fanops.__version__` is **derived** from installed metadata (`importlib.metadata.version`), never a second literal. An un-installed checkout yields the deliberately-unreal sentinel `0.0.0+uninstalled` — never a plausible fake version.
- **Rationale:** a second literal drifts. It **did**: `__init__.py` said `0.3.0` while `pyproject.toml` said `0.4.0`, and both were live-read (the CLI heartbeat, the daemon self-adopt signal) — pip metadata reported a version the running tool denied. This is the `_CLI_PRINT_COUNT` failure class ("a number copied into prose is a defect") applied to a version string.
- **Evidence:** `src/fanops/__init__.py` (`__version__ = _package_version("fanops")` + the `PackageNotFoundError` sentinel), landed by **#662** `fix(version): single version authority — __init__ derives from pyproject via importlib.metadata`. Prior drift is **historical** (fixed).

### STD-VER-02 — release/versioning process is **not yet defined** (honest gap)
- **Rule:** *(none yet)* — there is no declared release process, changelog, or semver policy.
- **Rationale for recording it as a gap rather than inventing one:** a single-operator, continuously-deployed localhost tool may not want semver + release automation at all; picking one here would be inventing policy the evidence does not compel. `v0.1` in `docs/design/v0.1-ship-route.md` is a **milestone label**, unrelated to `[project].version`.
- **Evidence:** no `CHANGELOG*` in-tree; `git tag` = 6 snapshot/checkpoint tags, **zero** semver tags ever; no release step in any workflow.

---

## 6 · Error handling — **[REFERENCE]** + one owned craft rule

Owned by real mechanisms (see `docs/ENFORCEMENT.md`) — do not restate:
- **Fail direction follows consequence** — verdict-producer → more checking; degradable feature → safe default + **surfaced** breadcrumb; correctness prerequisite → closed and loud.
- **No new silent broad `except`** — the AST swallow ratchet (`tests/test_swallow_ratchet.py`). *Known residual: the ratchet accepts stdlib `logging`, so surfacing remains a review judgment.*
- **Internal modules route output through the logger, never `print()`** — exact-equality budget (`tests/test_internal_prints_routed.py`, `IMPL-007`).
- **A correctness prerequisite refuses loudly** — cv2 (`framing.require_cv2`; proven by the `base-install` job).
- **Schedule monotonicity asserted at import time.**

### STD-ERR-01 — an operator-facing exception lives in `errors.py` and is caught by name in `cli.py` — **[OWNED]**
- **Rule:** an exception the **operator** can act on lives in `errors.py`, subclasses `Exception`, and has a matching `except` arm in the `cli.py` dispatch with a deliberate exit code. An exception that is an **internal signal** between two collaborators may live in its module and subclass `RuntimeError` — but must then be caught by its collaborator, never leak to the CLI.
- **Rationale:** this is the real, observed two-tier taxonomy; naming it stops a third pattern (an operator-facing error with no CLI arm → a raw traceback instead of an exit code).
- **Evidence:** `errors.py` (11 classes: `ControlFileError`, `LockBusyError`, `AuthError`+`PostizAuthError`/`ZernioAuthError`, `ToolchainMissingError`, `CutoverError`, …), each with a `cli.py` catch arm and a fixed exit code (1 for lock/run-busy, 2 otherwise); internal tier: `llm.py` (5 `RuntimeError` subclasses caught in `responder.py`), `framing_outcomes.ResolverInvariantError`.

---

## 7 · State ownership — **[REFERENCE]**

Owned by real mechanisms (see `docs/ENFORCEMENT.md`):
- **State is explicit per unit; never inferred.** *If your feature needs to know "what happened to X," add a field to X.*
- **No auto-publish** — a `Post` is born `awaiting_approval`; publish iterates `queued` only (the runtime publish gate).
- **No new unguarded door to a terminal `Post` state** — `published` ⇒ `public_url` (`IMPL-009`).
- **A `Moment` is mutated by `setattr`, never `model_copy`.**
- **No ledger model sets `extra="forbid"`** (`IMPL-010`).
- **One invariant, one owner; one mechanism, one implementation.**

---

## 8 · Persistence — **[REFERENCE]** + one owned craft rule

Owned by the runtime + its regression locks (`tests/test_publish_lockfree.py`, `tests/test_reconcile_lockfree.py`, `tests/test_ledger_sqlite_store.py`):
- **The cardinal rule:** no network call or heavy subprocess inside the ledger lock.
- **The ledger is never wiped implicitly** — wipe is snapshot + typed-confirm; restore serializes on the lock (fixed #653–#655).
- **Migrations + forward-compat** — §16 below.

### STD-PERSIST-01 — every hand-editable control-file write routes through `controlio` — **[OWNED]**
- **Rule:** writes to a hand-editable control file (`accounts.json`, `personas.json`, `cutover.json`, sidecars) go through `controlio.write_json_atomic` / `write_text_atomic` / `write_bytes_atomic` (mkstemp **same-dir** + `os.replace` + cleanup-on-failure). Media/ffmpeg outputs use their own `<dst>.part.mp4` temp (the **extension picks the muxer** — a bare `.part` fails muxer init) swept on every exit path. The **ledger** is exempt: it has its own single-writer-under-flock SQLite writer, deliberately not merged into `controlio`.
- **Rationale:** a fixed `<name>.tmp` lets two concurrent writers clobber each other's temp — `controlio`'s own module docstring says so, and at least one caller still does exactly that.
- **Evidence:** `controlio.py` (the mkstemp/`os.replace` primitive + the "NB the LEDGER has its own writer … deliberately NOT merged here" note); `clip.py` (`.part.mp4`, MOL-78); **known deviation:** `autopilot.set_env_var` hand-rolls a fixed-name `.env.tmp` with no lock across multiple callers.

---

## 9 · Testing — **[REFERENCE]** + owned craft rules

Hard rules, mechanically held (see `docs/ENFORCEMENT.md` and `tests/CLAUDE.md`):
- **Tests run in CI only**; local execution is mechanically denied (`.claude/settings.json` permissions.deny). *(`FANOPS_LOCAL_TESTS=1` is an operator-only override from a human terminal.)*
- **A hanging test is the bug**; the 60 s timeout is a deadlock guardrail, **never raised to pass**.
- **Every policy rule has a negative control** that fires on an injected defect (`tools.arch selftest` / `tools.ci selftest`).
- **Test-first**; a lock must break to fix the defect, never pin it.

### STD-TEST-01 — one test file per source module, by convention or by declared override — **[OWNED]**
- **Rule:** a changed `src/` module must map to a test file — by naming convention (`studio/actions_*`→`test_studio_*`/`test_actions_*`, `post/*`→`test_post_*`, …) or by an entry in `check_scope.py::_OVERRIDES`. An unmapped changed module fails the scoped check unless `FANOPS_CHECK_ALLOW_NO_TESTS=1`.
- **Rationale:** the cheapest possible "did you test it" signal, run locally in seconds before CI.
- **Evidence:** `scripts/check_scope.py` (`_OVERRIDES` + `_convention_candidates` + `orphan_src_modules`); `scripts/check.sh` (fails closed on an orphan).

### STD-TEST-02 — the test environment is hermetic by construction — **[OWNED]**
- **Rule:** a test never reads the operator's live `.env`. The autouse `_hermetic_publish_env` fixture strips the `_LEAKY_ENV` allowlist before every test. **When you add a default-ON flag or a credential env var a repo `.env` might carry, add it to `_LEAKY_ENV`.** A test that *wants* a non-default value sets it via `monkeypatch` (`delenv(..., raising=False)` for a possibly-absent key).
- **Rationale:** `load_dotenv` does not override an already-set var, so a leaked value silently makes a test assert against the operator's config instead of the code default — a green test proving nothing.
- **Evidence:** `tests/conftest.py` (`_LEAKY_ENV`, `_hermetic_publish_env`, `_no_real_publish_sleep`); `tests/CLAUDE.md` (the gotcha, with the causal explanation).

### STD-TEST-03 — workspace isolation is `Config(root=tmp_path)`, never a shared or env-set root — **[OWNED]**
- **Rule:** every test builds its own `Config(root=tmp_path)`. A test must never mutate `FANOPS_ROOT` to point the suite at a root.
- **Rationale:** parallel `-n auto` execution makes shared state a flake generator; the env-var form leaks across tests.
- **Evidence:** 303 test files use `Config(root=tmp_path)`; `tests/test_reframe.py::test_the_runner_never_mutates_FANOPS_ROOT` is a direct regression lock on the antipattern.

### STD-TEST-04 — a marker is a lane contract, not a label — **[OWNED]**
- **Rule:** the four markers each map to exactly one CI lane and mean exactly one thing: `integration` (**must** run in the e2e lane — a skip becomes a **failure** under `FANOPS_REQUIRE_E2E=1`), `slow` (cross-face proofs, e2e lane), `ci_hook_regression` (proves the skip→fail hook; excluded from the normal e2e run), `asr` (nightly-only, needs the heavy extra). Every test under `tests/integration/` **must** carry the `integration` marker.
- **Rationale:** markers are the only thing routing a test to a lane; an unmarked integration test silently runs in the hermetic unit lane and its real-toolchain assertions never execute.
- **Evidence:** `pyproject.toml [tool.pytest] markers`; `tests/conftest.py` (the `pytest_runtest_makereport` skip→fail hookwrapper); **#666** `test(ci): ratchet — every tests/integration/ test must carry the integration marker` (this closed the previously-unmarked `test_variation_render.py`).

---

## 10 · Documentation — **[REFERENCE]** + owned craft rules

Owned by real mechanisms:
- **Generated artifacts are views** — hand-editing one is drift (`ARCH-006` byte-compare over `derived/`).
- **A reference document carries a provenance header** (this file's header is the example).
- **A number copied into prose is a defect** (`IMPL-007` scans docs/ for stale budget copies).

### STD-DOC-01 — cite the **symbol**; a `file:line` is a hint — **[OWNED]**
- **Rule:** documentation cites a **symbol name** (function/constant/test). A line number may accompany it as a *hint* and must never be the only identifier. Reading a doc: trust the symbol, re-`grep` the line.
- **Rationale:** anchors rot on the next edit — measured, not theoretical: INV-20 found **10 of 10** nested-`CLAUDE.md` anchors stale, and this audit measured drift up to **+129 lines**, several multiples of the ±30 tolerance `AGENTS.md` itself states. Symbols were **100% accurate** across the same sample; only the numbers rotted.
- **Evidence:** INV-20 measured 10/10 nested-`CLAUDE.md` anchors stale while symbols were 100% accurate; `AGENTS.md` ("Anchors may have drifted ±30 lines — **trust the symbol, re-find the line**").

### STD-DOC-02 — a doc's home is its authority level, and a stale doc is corrected or banner'd — **[OWNED]**
- **Rule:** `docs/` root = operator/reference (plus `docs/ENFORCEMENT.md`, the enforcement index); `docs/CODEMAPS/` = **frozen** structural snapshots; `docs/design/` = product plans; `docs/runbooks/` = operational runbooks; nested `CLAUDE.md` = edit-time rulebooks. A doc that is superseded gets a **superseded-by banner** or is deleted with its rationale recorded in the deleting PR — never silently.
- **Rationale:** placement is currently ad hoc (`RUNBOOK.md`/`GOLIVE.md` sit at `docs/` root while `docs/runbooks/` holds only two files) and three docs were found describing code that had changed underneath them.
- **Evidence:** `docs/CONTROL-FILES.md` still calls `00_control/ledger.json` "the only state store" while `ledger.py` declares SQLite the single source of truth and `ledger.json` read-only break-glass — a live, still-open drift @ `a79528d`. `anomalies.md`'s "all HOLD" headline is stale (R3/R8).

---

## 11 · Decision records — **[OWNED]**

### STD-ADR-01 — a decision's record is the PR that lands it
- **Rule:** decision rationale lives in the pull request (title, body, commit message) that lands the change — main history is the decision record. The ADR namespace (`docs/adr/`) is **deleted and tombstoned** (`tests/test_governance_tombstone.py`); do not recreate it. A hard-to-reverse or surprising-without-context decision gets its full reasoning in the PR body before merge.
- **Rationale:** the prose ADR layer claimed authority no mechanism enforced (theatre census, 2026-07); PR bodies are already mandatory, reviewed, and archived by the merge itself.
- **Evidence:** the cleanup route's own PRs carry census evidence, dispositions, and consequences in their bodies — the working example of the format.

---

## 12 · CI expectations — **[REFERENCE]**

**Owned by the registry and the validators. This document restates no control row.**
- [`.github/ci-control-registry.yml`](../.github/ci-control-registry.yml) — the intended executable inventory (the single owner of control rows), reconciled against the workflows by `tools/ci` (DC-1/2/4/6/7) in the required unit lane.
- [`docs/ENFORCEMENT.md`](ENFORCEMENT.md) — what actually blocks a merge, and what merely runs.

**What an author needs to know:** your PR is gated by the **live** required set — `unit (fast, no toolchain)` only. Everything else runs and reports (advisory), or runs on schedule/dispatch. Do not add, rename, or promote a check without updating the registry in the same PR — the reconciliation validators go red on drift.

---

## 13 · Governance — **[REFERENCE]**

The prose governance layer is deleted (2026-07). [`docs/ENFORCEMENT.md`](ENFORCEMENT.md) is the
whole of the law: a rule exists only with its enforcer. Two principles carried forward:
- **CI is the sole merge-quality authority**; the registry is the declared-intent plane.
- **No bot silently rewrites the governance-of-record** — reconciliation **reports** drift; a human lands the fix.

---

## 14 · Performance — **[OWNED]**

### STD-PERF-01 — CI runtime has a bounded, blocking budget; product runtime does not
- **Rule:** the unit lane has a hard duration budget (**blocking**), set as an upper **bound above the measured spread of an unchanged suite** — not a p95, not an SLO, and not a guessed number. One value for pull requests and pushes alike: a gate must never be stricter on the branch that has to pass to land than on the trunk it merges into. There is **no** product-runtime performance standard, deliberately: render/transcribe cost is dominated by ffmpeg/whisper, and no latency SLO has ever been the binding constraint.
- **Rationale:** budget what is both measurable and load-bearing. Shared-runner wall-clock carries real variance, so a threshold set *inside* that spread rules on scheduling noise and its verdict says nothing about the change under test; a bound set *above* it still catches the unbounded growth the budget exists to catch. The CI budget protects the developer loop; a fabricated product-latency SLO would be a standard nobody measures.
- **Evidence:** `scripts/ci_slo_gate.py` (`check_budget`, exits non-zero over budget); the budget is set in ci.yml's unit job (`CI_UNIT_PYTEST_BUDGET_S`; **blocking**, not `continue-on-error`), a step inside registry control `CI-UNIT`. The number lives in ci.yml and nowhere else — MOL-829 deleted the copy that had rotted here, the "derived from a measured p95" claim that eight measurements refuted, and a citation of `CI-UNIT-SLO`, a control id absent from `.github/ci-control-registry.yml`.

---

## 15 · Observability — **[OWNED]**

### STD-OBS-01 — operator-visible events go through `get_logger(cfg)`; stdlib `logging` is for library-internal detail only
- **Rule:** anything an operator may need to see uses `fanops.log.get_logger(cfg)` — the **surfaced** channel (sanitized single-line JSON → `07_reports/run.log` **and** stderr, `0600`). Module-level stdlib `logging` is acceptable only for detail nobody operates on. **A fail-open breadcrumb on a safety-critical path MUST use the surfaced channel.**
- **Rationale:** *logging ≠ surfacing.* `src/fanops/` configures **no** stdlib handler anywhere — no `basicConfig`, no `dictConfig`, no `addHandler` — so a stdlib log line's visibility depends entirely on the host. The wipe path's restorability guard (`ledger_wipe.snapshot_is_restorable`) logs its failure to that unconfigured channel: technically logged, practically invisible, on the most destructive path in the system. Logging is not surfacing — that is the exact trap.
- **Evidence:** `log.py` (the closure: sanitize → JSON → `O_APPEND` + stderr, `0600`); 164 `get_logger` call sites vs 26 stdlib `getLogger` bindings with 0 handler configuration; `ledger_wipe.snapshot_is_restorable` (stdlib `logger.warning`, bare `except Exception`); known residual: the swallow ratchet accepts stdlib `logging`, so surfacing is a review judgment.

### STD-OBS-02 — a log level matches blast radius
- **Rule:** `get_logger(cfg)`'s `level=` is set when an event is a real skip/failure. Do not let a failure ride the `"info"` default.
- **Rationale:** only **5 of 164** call sites pass `level=`; skips like `no_integration_id` and `cascade_unlink_failed` log at `info`, which makes level-based triage useless.
- **Evidence:** `log.py` (`level="info"` default); the 5 explicit sites (`fanops_hashtags`, `learn_doctor`).

---

## 16 · Migrations — **[REFERENCE]**

Owned by the runtime (`ledger.py` migrations + `tests/test_ledger_sqlite_store.py`):
- **A migration is justified only by a real on-disk shape change** — additive, idempotent, copy-on-write, **never wipes**; a hop-chain (`SCHEMA_VERSION` v0→v11).
- **Forward-compat via `extra="ignore"`** — a shape is dropped **only after every consumer is gone** (the migration is the on-disk half of a teardown).
- **A ledger newer than the running code is refused loudly**, never loaded-and-field-dropped.
- **A new feature is byte-identical when its flag is off** (§20).
- **The migrate-vs-shim decision is mechanical:** *does a migration mechanism exist?* The ledger has a hop-chain → it may drop. The accounts registry has none → it must stay lenient.

---

## 17 · Public APIs — **[OWNED]**

### STD-API-01 — the public surface is the facade; a subpackage declares nothing else stable
- **Rule:** import from the **facade** (`fanops.studio.actions`, `fanops.studio.views`, `fanops.post`), not from a private sibling (`actions_run`, `views_review`). The `_`-prefix marks module-private; a bare name in a facade's re-export block is the closest thing to a public API.
- **Rationale:** the facade is the only thing preserving `STD-BOUND-01`'s acyclic tree; a direct sibling import from outside bypasses the contract silently.
- **Evidence:** `actions.py` / `views.py` re-export blocks (`# noqa: F401`); no `__all__` on `post/__init__.py` or `studio/__init__.py`; nothing prevents a direct sibling import today.

### STD-API-02 — a provider is added by a registry entry, never by edits scattered across the publish path
- **Rule:** a new publish backend is **one `PROVIDERS` entry** (lazy factory callables). The dryrun/live safety gates live in `post/run.py` and `post/__init__.py` — **not** in `providers.py` — and are not to be moved there.
- **Rationale:** one seam for "who publishes a channel"; keeping the gates out of the registry keeps the plugin lookup free of safety logic.
- **Evidence:** `post/providers.py` ("adding a provider later … is a NEW ENTRY here — not edits scattered across the publish path"); `post/__init__.py::get_poster` (raises rather than build a `DryRunPoster` when live); `post/CLAUDE.md` ("These are NOT in providers.py") — the go-live gates live in `post/run.py` + `post/__init__.py`.

---

## 18 · Security — **[REFERENCE]**

Owned by real mechanisms (`tests/test_secret_provider.py`, `tests/test_secret_write_routing.py`, `scripts/scan-secrets.sh`):
- **Secrets are keyring-first; reads fail open, writes fail closed** (round-trip verified). *An unverified write would let a caller scrub the plaintext fallback believing it stored.*
- **No secret enters a PR diff** — the scan has **no bypass** (control `CI-UNIT-SECRETSCAN`, in the required lane).
- **API keys are write-only** — never rendered back to any surface.
- **The Studio is localhost, no-auth by design** (a recorded accepted decision, not an oversight). Do not file CSRF/rate-limit tickets against it.
- **Craft note:** error text echoed to a ledger/stdout is redacted (`errors.redact`) — see `STD-DEP`/§6.

---

## 19 · Dependency management — **[OWNED]**

### STD-DEP-01 — CI installs from hash-verified locks; a dependency change regenerates them
- **Rule:** CI installs `pip install --require-hashes -r requirements/ci-{unit,e2e}.txt` then `pip install -e . --no-deps`. Locks are generated by `scripts/lock-deps.sh` (`pip-compile --generate-hashes`), **never hand-edited**, and regenerated on **linux/py3.12** to match the runner. Changing a dependency in `pyproject.toml` **requires** regenerating the locks in the same PR. `[asr]` is nightly-only and **intentionally unlocked**.
- **Rationale:** hashed locks make the CI environment reproducible and the supply chain auditable; the drift guard makes forgetting mechanically impossible.
- **Evidence:** `scripts/lock-deps.sh` (the two profiles + the platform-faithfulness caveat); `scripts/check-locks.sh` (the PR drift guard); control `CI-UNIT-LOCKDRIFT` (a required sub-gate).

### STD-DEP-02 — an optional dependency is lazy, and its absence is a **decided** direction
- **Rule:** an optional extra (`[studio]`, `[compose]`, `[asr]`, `[framing]`, `[keyring]`, `[transcribe]`) is imported **lazily**, and the absent-extra behavior is an explicit decision recorded at the extra: **fail-open** (degrade + breadcrumb) or **fail-closed** (refuse loudly). `[framing]` is the standing example of fail-closed: with `smart_framing` on and cv2 absent the render **refuses** rather than silently centre-crop.
- **Rationale:** the direction follows consequence. A degradable feature degrades; a **correctness prerequisite** refuses.
- **Evidence:** `pyproject.toml` (each extra's comment states lazy + fail direction); `framing.require_cv2` → `ToolchainMissingError` (exit 2); `scripts/base_install_smoke.py` proves the refusal on a no-extras install (control `CI-BASEINSTALL`).

### STD-DEP-03 — workflow actions are SHA-pinned — **[REFERENCE]**
Owned by `DC-6` (`tools/ci/checks.py`, unit-lane enforced) and `dependabot.yml`. *Historical: the one violation (`lane-guard.yml` floating `@v7`/`@v6`) was fixed in **#663**.*

---

## 20 · Feature flags — **[OWNED]**

### STD-FLAG-01 — a flag is read one way: a `Config` property
- **Rule:** a runtime flag is read via a `Config` property. `Settings` (pydantic) is the **strict validation boundary** used by `doctor`/`config` to fail loud on typos — it is **not** the runtime path. Do not add a third way to read a flag.
- **Rationale:** the split is deliberate (a fail-open runtime + a fail-loud diagnostic), but it is invisible unless stated, and it has already produced a real duplication (below).
- **Evidence:** `config.py` (73 `@property`, ~74 direct `os.getenv`, **zero** delegating to `Settings`); `settings.py` ("typed env boundary"); `doctor._env_settings_check` ("FAIL LOUD on enum/bool typos the runtime path would fail-open on"); 306 test files import `Config`, 3 import `settings`.

### STD-FLAG-02 — default-ON reads off-words; default-OFF reads on-words; a new **learning** signal ships default-OFF
- **Rule:** default-ON flag ⇒ `v not in {"0","false","no","off"}`. Default-OFF flag ⇒ `v in {"1","true","yes","on"}`. Every new **learning/bias** signal ships **default-OFF**, validation-frozen, amplify-only, with its own kill switch and a firewall test proving the off-path is byte-identical.
- **Rationale:** capability without risk. Generation/casting features may be default-ON (they are the product); a *learning* signal that acts before it is proven is how you learn on an unproven shape.
- **Evidence:** ~30 boolean properties in `config.py` follow the word-sets exactly; `docs/FLAGS.md` (the firewall-test table); `validation_gate.learning_validated`; `src/fanops/CLAUDE.md` ("off by default, frozen until validated, generation/schedule only").

### STD-FLAG-03 — a value set has one definition
- **Rule:** a closed value set (e.g. the poster backends) is defined **once** and imported. Two hand-maintained copies are a defect, whether the copy is a number or a `frozenset`.
- **Rationale:** the copies are byte-identical **today**; a 4th backend added to one and not the other silently splits validation between the strict diagnostic path and the runtime gate.
- **Evidence:** **live @ `a79528d`** — `_VALID_BACKENDS = frozenset({"dryrun","postiz","zernio"})` is defined independently in **both** `config.py` and `settings.py`; `PosterBackend = Literal[...]` likewise; `accounts.py` imports the `settings.py` copy while `config.py`'s gate checks its own. **No test ties them together.**

---

## 21 · Technical debt — **[OWNED]** principles

- **A residual is acceptable only** when zero/low-reachability, contained (ideally regression-locked), and documented **with an owner**. *"A deferral is not a discharge."*
- **Accepted residual over disproportionate change** — measure reachability before forcing a broad fix.
- **Prefer the smallest correct solution** — over-engineering is rejected on the record.

### Standards-layer residuals (this document's own, registered here)

| ID | Residual | Why accepted | Containment | Owner |
|---|---|---|---|---|
| `STD-RESIDUAL-1` | `check_scope.py::_OVERRIDES` is never checked for exhaustiveness against the whole tree | it only needs to be right for **changed** files, which is what `check.sh` scopes; a whole-tree audit would gate on a hand-maintained table | the orphan check fails closed on a changed unmapped module | ci-lane |
| `STD-RESIDUAL-2` | the `slow` marker has no generative criterion (a closed set of named files) | a runtime threshold would be arbitrary; the set is small and reviewed | 4 named files; lane routing is explicit | ci-lane |
| `STD-RESIDUAL-3` | `.markdownlint.json` exists and is wired to **nothing** | zero blast radius (a config with no runner); deleting vs wiring is a real choice, not an obvious fix | no execution path exists | docs |

*Residuals of the **architecture/CI** planes are **not** listed here — they live in the validators' own baselines (`tools/arch` registries, the swallow ratchet, the registry).*

---

## 22 · Deletion policy — **[OWNED]** principles

- **"Dead / zero-caller" is a LEAD, never a verdict.** A deletion ships a whole-tree **AST census**, not a grep, and is **revalidated at execution**, not when the plan was written (see also the alias/lazy-import sweep rule in `src/fanops/CLAUDE.md`).
- **Evidence of why:** the name-based call graph cannot see aliased or lazily-bound backends and once mislabeled **5 live functions** as dead (`post/providers.py`'s lazy lambdas are all flagged "zero callers" and all live); a Cycle-8 plan had **4 of 4** deletion premises invalidated at execution and the deletions were cancelled cleanly.
- **A rotting copy is deleted, not re-explained.**

---

## Maintenance of this document

- **Drift detection:** the sync-docs discipline — when code, configs, or CI behavior change under a
  claim made here, the same PR corrects the claim. `IMPL-007` mechanically catches stale budget
  copies; everything else is review.
- **Amendment:** an `STD-*` change that alters a decision records its rationale in the PR that lands
  it (STD-ADR-01). A `[REFERENCE]` section is **never** amended here — amend the owning mechanism.
- **Provenance:** this document carries a base-SHA header. If it disagrees with executable source or
  live config, **the executable plane wins and this document is corrected.**
