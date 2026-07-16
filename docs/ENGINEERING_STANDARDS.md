<!-- Engineering Standards — the CODE-CRAFT layer of the governance system.
     Base: origin/main @ a79528d (#676), revalidated 2026-07-16.
     POSITION: subordinate to docs/REPOSITORY_CONSTITUTION.md (§2 precedence). This document owns
     day-to-day code craft ONLY. It never restates an ARCHITECTURAL_LAWS.md law, a
     .github/ci-control-registry.yml control row, or an ADR decision — it REFERENCES them.
     Enforcement status per standard is HONEST: `documented-only` means exactly that. -->

# FanOps — Engineering Standards

> **What this document is.** The **code-craft layer**: how code is *written* here day-to-day — naming,
> layout, boundaries, typing, flags, test craft, observability channels, public surface. It sits
> **below** the Architectural Laws and **above** nothing. It is the standards authority; it is not a
> second source of truth for anything already owned elsewhere.

## Position in the governance system (read this first)

| Layer | Document | Owns | ID space |
|---|---|---|---|
| Intent / philosophy | [`docs/REPOSITORY_CONSTITUTION.md`](REPOSITORY_CONSTITUTION.md) | the rules + their honest enforcement status | `C*` |
| Reasoning / instincts | [`docs/ENGINEERING_PHILOSOPHY.md`](ENGINEERING_PHILOSOPHY.md) | *why* the repo decides as it does | — |
| Enforceable architecture | [`docs/ARCHITECTURAL_LAWS.md`](ARCHITECTURAL_LAWS.md) | architectural invariants + mechanisms | `LAW-*` |
| CI control plane | [`.github/ci-control-registry.yml`](../.github/ci-control-registry.yml) | **every CI control row** (single owner) | control `id` |
| Decisions | [`docs/adr/`](adr/) | per-decision record + rationale | `ADR-NNNN` |
| **Code craft (this doc)** | `docs/ENGINEERING_STANDARDS.md` | naming · layout · boundaries · typing · versioning · flags · test craft · observability · public API · deps | **`STD-*`** |

**Precedence (inherited, not redefined).** Constitution §2 / ADR-0100: *executable source & tests →
live GitHub config → accepted ADRs & registries → generated docs → historical prose.* This document is
plane 4/5. **When it disagrees with a higher plane, the higher plane wins and this document is
corrected** (§Maintenance).

**Two hard constraints this document obeys.**
1. **No second registry.** CI controls are referenced by `id`, never restated.
2. **No competing law.** Where an `LAW-*` already governs a topic, the section here is marked
   **[REFERENCE]** and points at it. Only **[OWNED]** sections carry `STD-*` rules.

**Field key (every OWNED standard).** *Rule · Rationale · Evidence · Enforcement owner · Current
enforcement · Planned enforcement.*

**Status vocabulary** (shared with `docs/governance/EVIDENCE_RECONCILIATION.md`): `enforced` ·
`partially-enforced` · `documented-only` · `dormant` · `proposed` · `accepted-residual` · `historical`.

> **Anchors are hints.** Per `ENGINEERING_PHILOSOPHY.md` §6 / INV-20: cite the **symbol**; treat any
> `file:line` as a hint and re-`grep`. Line numbers in Evidence below are hints only.

---

## 1 · Naming — **[OWNED]**

### STD-NAME-01 — `snake_case` functions, `PascalCase` classes, no exceptions
- **Rule:** every `def`/method is `snake_case` (optionally `_`/`__`-prefixed); every class is `PascalCase`.
- **Rationale:** uniform call-site reading across 130 modules; the one convention with zero measured violations — cheap to keep, noisy to break.
- **Evidence:** exhaustive AST scan @ `a79528d`: 1889/1889 functions and 159/159 classes conform.
- **Enforcement owner:** authors + review. · **Current:** `documented-only` (ruff `select = ["E","F"]` has no naming rules — `pyproject.toml`). · **Planned:** none — a 100%-adherent convention does not justify a validator (`SLICE-STD-NAME` explicitly **not** proposed).

### STD-NAME-02 — a closed string set is a `class X(str, Enum)`; module-private tunables are `_UPPER`
- **Rule:** state machines / closed value sets subclass `(str, Enum)`; module-level tunables are `_UPPER_SNAKE` and private by default; a `UPPER` (public) name means it is deliberately cross-module.
- **Rationale:** `(str, Enum)` is JSON-serializable for the ledger by construction; private-by-default keeps the cross-module surface visible (`STD-API-01`).
- **Evidence:** 16/16 enums use `(str, Enum)` (`models.py` SourceState/MomentState/ClipState/PostState/…, `accounts.AccountStatus`, `reframe.ReframeClass`); 335 `_UPPER` vs 88 `UPPER` top-level constants.
- **Enforcement owner:** models + authors. · **Current:** `documented-only`. · **Planned:** none.

### STD-NAME-03 — first-party env vars are `FANOPS_*`; persisted ids are `<kind>_<12-hex-sha1>`
- **Rule:** every first-party env var is `FANOPS_`-prefixed; every persisted id is content-addressed via `ids.py` — never Python's builtin `hash()` (salted per interpreter, PEP 456).
- **Rationale:** the env surface is a trust boundary (C3.3); a salted id would break content-addressing across processes.
- **Evidence:** `ids.py` (`make_id`/`child_id`/`content_id`/`surface_key`, `sha1(...)[:12]`, with the anti-`hash()` rationale stated in-module); 62 `FANOPS_*` names.
- **Enforcement owner:** `ids.py` (structural — one module) + `tools/arch`. · **Current:** `enforced` for the env-declaration half (**LAW-ARCH-04** / `ARCH-003` — every env read is declared); `documented-only` for the prefix + id-format conventions. · **Planned:** none (bypassing `ids.py` is the only way to break the id rule, and is review-visible).

---

## 2 · Repository layout — **[OWNED]**

### STD-LAYOUT-01 — src-layout; a subsystem becomes a **subpackage only when gated by an optional dependency**
- **Rule:** code lives under `src/fanops/`. A flat module is the default. A subpackage (`post/`, `studio/`) is justified **only** by an optional-extra boundary that must stay importable without the extra.
- **Rationale:** the two subpackages exist to keep `import fanops` free of Flask/provider deps; size alone never justifies a package (that is what `STD-LAYOUT-02` is for).
- **Evidence:** `studio/__init__.py` ("Import app.py LAZILY (it pulls Flask); keeping this package init Flask-free lets `import fanops.studio` work on a core, no-`[studio]` install"); `post/__init__.py` (Poster interface + factory); `pyproject.toml` extras.
- **Enforcement owner:** authors + review; the no-extras contract is proven by CI. · **Current:** `partially-enforced` — the *contract* (a base install imports and refuses loudly) is proven by `scripts/base_install_smoke.py` via control **`CI-BASEINSTALL`** (advisory today; Phase-E promotion is **LAW-FAIL-03**'s remediation). The *layout rule itself* is `documented-only`. · **Planned:** none beyond `CI-BASEINSTALL` promotion (owned by ADR-0101 Phase E — not re-planned here).

### STD-LAYOUT-02 — a growing module splits into a **prefixed flat family**, byte-identical at extraction
- **Rule:** when a module outgrows one concern, split it into `<stem>_<concern>.py` siblings and keep the original as a **facade** (`STD-BOUND-01`). The extraction commit changes **no behavior** and says so.
- **Rationale:** preserves import paths and review legibility; the byte-identical clause makes the split reviewable as a pure move.
- **Evidence:** `persona_directives.py` / `persona_research.py` / `persona_store.py` each open "extracted from personas.py, audit #6 — behavior byte-identical"; same shape for `ledger*`, `variant_*`, `config*`, `cutover*`.
- **Enforcement owner:** authors + review. · **Current:** `documented-only`. · **Planned:** none.

### STD-LAYOUT-03 — module size is budgeted, not capped
- **Rule:** a module over **~1,200 lines** is a split candidate; exceeding it is allowed but should be a noted decision, not drift.
- **Rationale:** honest about reality — `cli.py` and `studio/views.py` are both over budget and both are *deliberate* (a single argparse dispatcher; a re-export facade). A hard cap would force a worse structure; no budget at all lets them grow unwatched (`cli.py` grew 1448→1465 during this audit alone).
- **Evidence:** @ `a79528d`: `cli.py` 1465, `studio/views.py` 1435, `studio/actions.py` 1188, `config.py` 1151. None is a declared hot file (`.agents/lanes.json`).
- **Enforcement owner:** review. · **Current:** `documented-only`. · **Planned:** `proposed` — an **advisory** report-only size census (`SLICE-STD-SIZE`, `STANDARDS_AUTOMATION_PLAN.md`). Deliberately **never blocking**: a line-count gate would be exactly the "decoration that makes a dashboard green" `policy.py` warns against.

---

## 3 · Module boundaries — **[OWNED]**

### STD-BOUND-01 — a facade re-exports; siblings never import each other
- **Rule:** `actions.py` / `views.py` are **facades** that re-export from their `actions_*` / `views_*` siblings. A sibling may import `*_common`, **never another sibling**. The facade is the only aggregator.
- **Rationale:** keeps the studio import graph a strict 2-level tree with no lateral edges — the cheapest possible acyclicity guarantee.
- **Evidence:** `actions_run.py` ("depends only on actions_common … never on a sibling action module, so the import graph stays acyclic"); `actions_common.py` ("Imports nothing from fanops.* — a leaf module"); grep @ `a79528d`: **0** sibling imports across `actions_{run,approve,casting,wipe,segments}.py`.
- **Enforcement owner:** studio + review. · **Current:** `documented-only` — held with 0 violations, but by discipline (**LAW-ARCH-02** covers *cycles*, not this stricter tree shape). · **Planned:** none (0 violations; the general cycle law is the backstop).

### STD-BOUND-02 — a Studio route is thin: parse → **one** action/view → render
- **Rule:** blueprint-per-tab (`app_routes_*.py`, one `register_*_routes(app, cfg)` each). A route parses the form, calls exactly **one** `actions_*` (mutate) or `views_*` (read), and renders. Business logic never lives in a route.
- **Rationale:** makes every mutation attributable to one function and keeps routes trivially reviewable.
- **Evidence:** `src/fanops/studio/CLAUDE.md` (the layer-discipline rulebook); `app.py` registers 7 route modules.
- **Enforcement owner:** studio + review (`studio/CLAUDE.md` is the edit-time rulebook). · **Current:** `documented-only`. · **Planned:** none.

### STD-BOUND-03 — `views_*` are pure reads; every mutation goes through **one** `Ledger.transaction`
- **Rule:** `views_*` project `Ledger.load` and never write. `actions_*` mutate inside exactly one `Ledger.transaction`. Sanctioned exceptions are named in `studio/CLAUDE.md`, not invented ad hoc.
- **Rationale:** one lock-safe load→mutate→save per operation is what makes concurrent daemon/Studio/CLI access safe (C6.1).
- **Evidence:** `ledger.transaction` (lock-before-load, closing the AUDIT-B4 lost-update window); `studio/CLAUDE.md` names the two sanctioned read-side exceptions.
- **Enforcement owner:** ledger + studio. · **Current:** `partially-enforced` — the *transaction mechanism* is enforced by tests and **LAW-PERSIST-01** (no I/O under the lock); the *"views never write"* rule is `documented-only` with two recorded exceptions. · **Planned:** none.

---

## 4 · Imports — **[REFERENCE]**

Owned entirely by the Architectural Laws. Do not restate:
- **LAW-ARCH-02** — no new compile-time import cycle (`ARCH-004`; one baselined cycle).
- **LAW-ARCH-03** — a must-stay-lazy import may not be hoisted to module level (`ARCH-007`/GB-1). *Hoisting looks like a cleanup and bricks process start.*
- Constitution **C3.2** for the rationale.

**Craft note (not a new rule):** a cross-subsystem "upward" import (e.g. `health_model` → `studio`, `post/run` → `studio.views_common`) is legal **only** as an in-function import, and is pinned by `ARCH-007`. If you are writing one, you are on a governed edge — read LAW-ARCH-03 first.

---

## 5 · Versioning — **[OWNED]**

### STD-VER-01 — the package version has exactly ONE authority: `pyproject.toml`
- **Rule:** `pyproject.toml [project].version` is the sole version authority. `fanops.__version__` is **derived** from installed metadata (`importlib.metadata.version`), never a second literal. An un-installed checkout yields the deliberately-unreal sentinel `0.0.0+uninstalled` — never a plausible fake version.
- **Rationale:** a second literal drifts. It **did**: `__init__.py` said `0.3.0` while `pyproject.toml` said `0.4.0`, and both were live-read (the CLI heartbeat, the daemon self-adopt signal) — pip metadata reported a version the running tool denied. This is the `_CLI_PRINT_COUNT` failure class (C2.3, "a number copied into prose is a defect") applied to a version string.
- **Evidence:** `src/fanops/__init__.py` (`__version__ = _package_version("fanops")` + the `PackageNotFoundError` sentinel), landed by **#662** `fix(version): single version authority — __init__ derives from pyproject via importlib.metadata`. Prior drift is **historical** (fixed).
- **Enforcement owner:** packaging + `tools/arch` (C2.3 class). · **Current:** **`enforced` — structurally.** There is no second literal to drift; the derivation cannot disagree with its own source. · **Planned:** none needed. A `test_version_consistency` would now assert a tautology; the *structural* fix is strictly better than a test guarding two copies.

### STD-VER-02 — release/versioning process is **not yet defined** (honest gap)
- **Rule:** *(none yet)* — there is no declared release process, changelog, or semver policy.
- **Rationale for recording it as a gap rather than inventing one:** a single-operator, continuously-deployed localhost tool may not want semver + release automation at all; picking one here would be inventing policy the evidence does not compel. `v0.1` in `docs/design/v0.1-ship-route.md` is a **milestone label**, unrelated to `[project].version`.
- **Evidence:** no `CHANGELOG*` in-tree; `git tag` = 6 snapshot/checkpoint tags, **zero** semver tags ever; no release step in any workflow.
- **Enforcement owner:** operator. · **Current:** `proposed` (undecided). · **Planned:** **`BLOCK-RELEASE-POLICY`** — an operator product decision (`STANDARDS_AUTOMATION_PLAN.md` §Blocked). Deliberately **not** decided by this document.

---

## 6 · Error handling — **[REFERENCE]** + one owned craft rule

Owned by the Constitution and Laws — do not restate:
- **C7.1 / Philosophy §3** — fail direction follows consequence (verdict-producer → more checking; degradable feature → safe default + **surfaced** breadcrumb; correctness prerequisite → closed and loud).
- **LAW-FAIL-01** — no new silent broad `except` (the AST swallow ratchet). *Known residual **AR-6**: the ratchet accepts stdlib `logging`, so surfacing remains a review judgment.*
- **LAW-FAIL-02** — internal modules route output through the logger, never `print()` (exact-equality budget).
- **LAW-FAIL-03** — a correctness prerequisite refuses loudly (cv2; `CI-BASEINSTALL`).
- **LAW-FAIL-04** — schedule monotonicity asserted at import time.

### STD-ERR-01 — an operator-facing exception lives in `errors.py` and is caught by name in `cli.py` — **[OWNED]**
- **Rule:** an exception the **operator** can act on lives in `errors.py`, subclasses `Exception`, and has a matching `except` arm in the `cli.py` dispatch with a deliberate exit code. An exception that is an **internal signal** between two collaborators may live in its module and subclass `RuntimeError` — but must then be caught by its collaborator, never leak to the CLI.
- **Rationale:** this is the real, observed two-tier taxonomy; naming it stops a third pattern (an operator-facing error with no CLI arm → a raw traceback instead of an exit code).
- **Evidence:** `errors.py` (11 classes: `ControlFileError`, `LockBusyError`, `AuthError`+`PostizAuthError`/`ZernioAuthError`, `ToolchainMissingError`, `CutoverError`, …), each with a `cli.py` catch arm and a fixed exit code (1 for lock/run-busy, 2 otherwise); internal tier: `llm.py` (5 `RuntimeError` subclasses caught in `responder.py`), `framing_outcomes.ResolverInvariantError`.
- **Enforcement owner:** `errors` + review. · **Current:** `documented-only`. · **Planned:** `proposed` — `SLICE-STD-ERRTIER` (an advisory check that every `errors.py` class has a `cli.py` arm). Low priority; the pairing is currently 11/11.

---

## 7 · State ownership — **[REFERENCE]**

Owned by the Constitution and Laws:
- **C5.1 / Philosophy §1** — state is explicit per unit; never inferred. *If your feature needs to know "what happened to X," add a field to X.*
- **LAW-STATE-01** — no auto-publish; a `Post` is born `awaiting_approval`; publish iterates `queued` only.
- **LAW-STATE-02** — no new unguarded door to a terminal `Post` state; `published` ⇒ `public_url` (GB-4/R1). *Residual AR-1 (RC-9 mutation-time deferral, pinned by S11.)*
- **LAW-STATE-03** — a `Moment` is mutated by `setattr`, never `model_copy` (GB-5).
- **LAW-STATE-04** — no ledger model sets `extra="forbid"` (GB-3).
- **C4.1 / Philosophy §2** — one invariant, one owner; one mechanism, one implementation.

---

## 8 · Persistence — **[REFERENCE]** + one owned craft rule

Owned by the Constitution and Laws:
- **LAW-PERSIST-01** — **the cardinal rule**: no network call or heavy subprocess inside the ledger lock.
- **LAW-PERSIST-02** — the ledger is never wiped implicitly; wipe is snapshot + typed-confirm; restore serializes on the lock (RC-4/RC-5 **fixed** #653–#655).
- **LAW-PERSIST-03 / LAW-PERSIST-04** — migrations + forward-compat (§16 below).

### STD-PERSIST-01 — every hand-editable control-file write routes through `controlio` — **[OWNED]**
- **Rule:** writes to a hand-editable control file (`accounts.json`, `personas.json`, `cutover.json`, sidecars) go through `controlio.write_json_atomic` / `write_text_atomic` / `write_bytes_atomic` (mkstemp **same-dir** + `os.replace` + cleanup-on-failure). Media/ffmpeg outputs use their own `<dst>.part.mp4` temp (the **extension picks the muxer** — a bare `.part` fails muxer init) swept on every exit path. The **ledger** is exempt: it has its own single-writer-under-flock SQLite writer, deliberately not merged into `controlio`.
- **Rationale:** a fixed `<name>.tmp` lets two concurrent writers clobber each other's temp — `controlio`'s own module docstring says so, and at least one caller still does exactly that.
- **Evidence:** `controlio.py` (the mkstemp/`os.replace` primitive + the "NB the LEDGER has its own writer … deliberately NOT merged here" note); `clip.py` (`.part.mp4`, MOL-78); **known deviation:** `autopilot.set_env_var` hand-rolls a fixed-name `.env.tmp` with no lock across multiple callers.
- **Enforcement owner:** `controlio` + review. · **Current:** `documented-only` (`src/fanops/CLAUDE.md` states the rule; nothing checks it; ≥6 files hand-roll a temp+replace). · **Planned:** `proposed` — **`SLICE-STD-ATOMIC`**: an AST ratchet in the shape of `test_swallow_ratchet.py` (baseline the known deviations, block *new* hand-rolled writers). See `STANDARDS_AUTOMATION_PLAN.md`.

---

## 9 · Testing — **[REFERENCE]** + owned craft rules

Owned by the Constitution and Laws — do not restate:
- **LAW-CI-01** — tests run in **CI only**; local execution is mechanically denied. *(`FANOPS_LOCAL_TESTS=1` is an operator-only override from a human terminal.)*
- **LAW-CI-02** — a hanging test is the bug; the 60 s timeout is a deadlock guardrail, **never raised to pass**.
- **LAW-CI-03** — every policy rule has a negative control that fires on an injected defect.
- **C11.3** — test-first; a lock must break to fix the defect, never pin it.

### STD-TEST-01 — one test file per source module, by convention or by declared override — **[OWNED]**
- **Rule:** a changed `src/` module must map to a test file — by naming convention (`studio/actions_*`→`test_studio_*`/`test_actions_*`, `post/*`→`test_post_*`, …) or by an entry in `check_scope.py::_OVERRIDES`. An unmapped changed module fails the scoped check unless `FANOPS_CHECK_ALLOW_NO_TESTS=1`.
- **Rationale:** the cheapest possible "did you test it" signal, run locally in seconds before CI.
- **Evidence:** `scripts/check_scope.py` (`_OVERRIDES` + `_convention_candidates` + `orphan_src_modules`); `scripts/check.sh` (fails closed on an orphan).
- **Enforcement owner:** ci-lane / `check.sh`. · **Current:** `enforced` for **changed** files (pre-commit + manual `check.sh`). · **Planned:** none. *Known limit: the `_OVERRIDES` table is hand-maintained and is never checked for exhaustiveness against the whole tree — recorded as an accepted residual (`STD-RESIDUAL-1`), not a slice.*

### STD-TEST-02 — the test environment is hermetic by construction — **[OWNED]**
- **Rule:** a test never reads the operator's live `.env`. The autouse `_hermetic_publish_env` fixture strips the `_LEAKY_ENV` allowlist before every test. **When you add a default-ON flag or a credential env var a repo `.env` might carry, add it to `_LEAKY_ENV`.** A test that *wants* a non-default value sets it via `monkeypatch` (`delenv(..., raising=False)` for a possibly-absent key).
- **Rationale:** `load_dotenv` does not override an already-set var, so a leaked value silently makes a test assert against the operator's config instead of the code default — a green test proving nothing.
- **Evidence:** `tests/conftest.py` (`_LEAKY_ENV`, `_hermetic_publish_env`, `_no_real_publish_sleep`); `tests/CLAUDE.md` (the gotcha, with the causal explanation).
- **Enforcement owner:** ci-lane + `tests/CLAUDE.md`. · **Current:** `enforced` (autouse, no opt-out) for the listed keys; `documented-only` for the "add your new flag to the list" step. · **Planned:** none.

### STD-TEST-03 — workspace isolation is `Config(root=tmp_path)`, never a shared or env-set root — **[OWNED]**
- **Rule:** every test builds its own `Config(root=tmp_path)`. A test must never mutate `FANOPS_ROOT` to point the suite at a root.
- **Rationale:** parallel `-n auto` execution makes shared state a flake generator; the env-var form leaks across tests.
- **Evidence:** 303 test files use `Config(root=tmp_path)`; `tests/test_reframe.py::test_the_runner_never_mutates_FANOPS_ROOT` is a direct regression lock on the antipattern.
- **Enforcement owner:** ci-lane. · **Current:** `partially-enforced` (one targeted regression test; the general rule is convention). · **Planned:** none.

### STD-TEST-04 — a marker is a lane contract, not a label — **[OWNED]**
- **Rule:** the four markers each map to exactly one CI lane and mean exactly one thing: `integration` (**must** run in the e2e lane — a skip becomes a **failure** under `FANOPS_REQUIRE_E2E=1`), `slow` (cross-face proofs, e2e lane), `ci_hook_regression` (proves the skip→fail hook; excluded from the normal e2e run), `asr` (nightly-only, needs the heavy extra). Every test under `tests/integration/` **must** carry the `integration` marker.
- **Rationale:** markers are the only thing routing a test to a lane; an unmarked integration test silently runs in the hermetic unit lane and its real-toolchain assertions never execute.
- **Evidence:** `pyproject.toml [tool.pytest] markers`; `tests/conftest.py` (the `pytest_runtest_makereport` skip→fail hookwrapper); **#666** `test(ci): ratchet — every tests/integration/ test must carry the integration marker` (this closed the previously-unmarked `test_variation_render.py`).
- **Enforcement owner:** ci-lane. · **Current:** **`enforced`** — the directory↔marker ratchet (#666) + the skip→fail hook + its guard-on-the-guard (`test_ci_require_e2e.py`, control `CI-UNIT-HOOKVERIFY`). · **Planned:** none. *(The `slow` criterion remains a judgment call — a closed set of named files, not a generative rule. Recorded as `STD-RESIDUAL-2`.)*

---

## 10 · Documentation — **[REFERENCE]** + owned craft rules

Owned by the Constitution and Laws:
- **LAW-DOC-01** — generated docs are views; hand-editing one is drift (`ARCH-006` byte-compare).
- **LAW-DOC-02 / C16.4** — a governing document carries a provenance header.
- **C2.3 / LAW-SOT-03** — a number copied into prose is a defect (`IMPL-007`).

### STD-DOC-01 — cite the **symbol**; a `file:line` is a hint — **[OWNED]**
- **Rule:** documentation cites a **symbol name** (function/constant/test). A line number may accompany it as a *hint* and must never be the only identifier. Reading a doc: trust the symbol, re-`grep` the line.
- **Rationale:** anchors rot on the next edit — measured, not theoretical: INV-20 found **10 of 10** nested-`CLAUDE.md` anchors stale, and this audit measured drift up to **+129 lines**, several multiples of the ±30 tolerance `AGENTS.md` itself states. Symbols were **100% accurate** across the same sample; only the numbers rotted.
- **Evidence:** `ENGINEERING_PHILOSOPHY.md` §6 ("Line anchors are the same trap, smaller"); INV-20; `AGENTS.md` ("Anchors may have drifted ±30 lines — **trust the symbol, re-find the line**").
- **Enforcement owner:** constitution maintainer + authors. · **Current:** `documented-only`. · **Planned:** **`CM-7`** (stale symbol/line-anchor detection) — **already designed** in `docs/governance/CONSTITUTION_MAINTENANCE.md`, report-only, gated on `SLICE-CONSTLINT`. **Not re-planned here** — this standard is a *consumer* of CM-7.

### STD-DOC-02 — a doc's home is its authority level, and a stale doc is corrected or banner'd — **[OWNED]**
- **Rule:** `docs/` root = operator/reference; `docs/governance/` = governance machinery; `docs/adr/` = decisions; `docs/ci/` = CI program; `docs/CODEMAPS/` = **frozen** structural snapshots; nested `CLAUDE.md` = edit-time rulebooks. A doc that is superseded gets a **superseded-by banner**, not a silent deletion (C18.3).
- **Rationale:** placement is currently ad hoc (`RUNBOOK.md`/`ARCH_RUNBOOK.md`/`GOLIVE.md` sit at `docs/` root while `docs/runbooks/` holds only two files) and three docs were found describing code that had changed underneath them.
- **Evidence:** `docs/CONTROL-FILES.md` still calls `00_control/ledger.json` "the only state store" while `ledger.py` declares SQLite the single source of truth and `ledger.json` read-only break-glass — a live, still-open drift @ `a79528d`. `anomalies.md`'s "all HOLD" headline is stale (R3/R8).
- **Enforcement owner:** docs + constitution maintainer. · **Current:** `documented-only`. · **Planned:** `SLICE-STD-DOC-CORRECTIONS` (a doc-correction wave; `STANDARDS_AUTOMATION_PLAN.md`), and **CM-4/CM-8** report the class going forward.

---

## 11 · ADR usage — **[REFERENCE]**

Owned by the Constitution + the ADR layer. Do not restate:
- **C16.1** — an ADR lives at `docs/adr/NNNN-slug.md`; write one when a decision is **hard-to-reverse ∧ surprising-without-context ∧ the result of a real trade-off**.
- **C16.2** — `docs/adr/README.md` is the historical catalogue (99 back-filled decisions); formalization into standalone ADRs is **prioritized, not bulk-generated**.
- [`docs/adr/FORMALIZATION_ROADMAP.md`](adr/FORMALIZATION_ROADMAP.md) — the Tier-1 order.
- **C18.1** — a constitutional change is proposed as an ADR.

**Standards-specific rule of use (not a new ADR policy):** a change to an **[OWNED]** `STD-*` rule that alters a *decision* (not a typo) records an ADR and is referenced here. A change to a **[REFERENCE]** section is never made here — it is made in the owning document.

> **Numbering caution.** ADR numbering has a known unresolved collision (`ADR-FORMAT` says "increment from highest" → 0104; the catalogue reserves 0001–0099 for back-fill). `SLICE-ADR-NUMBERING` resolves it. **This layer cuts no ADR** and therefore takes no number.

---

## 12 · CI expectations — **[REFERENCE]**

**Owned entirely by the CI governance program. This document restates no control row.**
- [`.github/ci-control-registry.yml`](../.github/ci-control-registry.yml) — the intended executable inventory (the single owner of control rows).
- **ADR-0100** — CI governance authority + control registry + the precedence order + `DC-1..DC-6`.
- **ADR-0101** — required-checks + merge-gate policy (5 intended contexts; 2 live; the §8 promotion criteria).
- **ADR-0102** — merge strategy + repository history policy.
- **LAW-CI-01..08** — the enforceable CI subset.
- [`docs/ci/CI_GOVERNANCE_INDEX.md`](ci/CI_GOVERNANCE_INDEX.md) — the CI program entry point.
- [`docs/CI_ARCHITECTURE_REVIEW.md`](CI_ARCHITECTURE_REVIEW.md) — **historical**: the audit that produced ADR-0100/0101/0102.

**What an author needs to know:** your PR is gated by the **live** required set (`unit`, `e2e` today), not by the intended set. Everything else runs and reports. Do not add, rename, or promote a check outside the registry + ADR-0101 §8 path.

---

## 13 · Governance — **[REFERENCE]**

- **Constitution §12** — CI is the sole merge-quality authority; the registry is the declared-intent plane.
- **Constitution §17** — accepted residuals (AR-1..AR-8). *A residual is legitimate only if zero/low-reachability, contained, and owned.*
- **Constitution §18** — the amendment process.
- **LAW-CI-08** — no bot silently rewrites the governance-of-record; reconciliation **reports** drift, a human lands the fix.
- [`docs/governance/CONSTITUTION_MAINTENANCE.md`](governance/CONSTITUTION_MAINTENANCE.md) — the CM-1..CM-8 check catalogue that keeps this layer honest.

**Standards-layer maintenance** is defined in [`docs/governance/STANDARDS_MAINTENANCE.md`](governance/STANDARDS_MAINTENANCE.md) — it **extends** CM-*, it does not fork it.

---

## 14 · Performance — **[OWNED]**

### STD-PERF-01 — CI runtime has a measured, blocking budget; product runtime does not
- **Rule:** the unit lane has a hard duration budget (**blocking**), derived from a measured p95 plus margin — not a guessed number. There is **no** product-runtime performance standard, deliberately: render/transcribe cost is dominated by ffmpeg/whisper, and no latency SLO has ever been the binding constraint.
- **Rationale:** budget what is both measurable and load-bearing. The CI budget protects the developer loop; a fabricated product-latency SLO would be a standard nobody measures.
- **Evidence:** `scripts/ci_slo_gate.py` (`check_budget`, exits non-zero over budget); `docs/CI_SLO.md` (135 s PR / 140 s main = p95 115 s + ~17–20 % margin; "**blocking**", not `continue-on-error`); control `CI-UNIT-SLO`.
- **Enforcement owner:** ci-lane (control `CI-UNIT-SLO`). · **Current:** `enforced` (unit lane); e2e timing is explicitly **advisory**. · **Planned:** none. *Known limit: `docs/CI_SLO.md` carries an illustrative `e2e_slow_s` figure inconsistent with the real ~170 s negative-control load — a doc-correction, not a budget change (`SLICE-STD-DOC-CORRECTIONS`).*

---

## 15 · Observability — **[OWNED]**

### STD-OBS-01 — operator-visible events go through `get_logger(cfg)`; stdlib `logging` is for library-internal detail only
- **Rule:** anything an operator may need to see uses `fanops.log.get_logger(cfg)` — the **surfaced** channel (sanitized single-line JSON → `07_reports/run.log` **and** stderr, `0600`). Module-level stdlib `logging` is acceptable only for detail nobody operates on. **A fail-open breadcrumb on a safety-critical path MUST use the surfaced channel.**
- **Rationale:** *logging ≠ surfacing.* `src/fanops/` configures **no** stdlib handler anywhere — no `basicConfig`, no `dictConfig`, no `addHandler` — so a stdlib log line's visibility depends entirely on the host. The wipe path's restorability guard (`ledger_wipe.snapshot_is_restorable`) logs its failure to that unconfigured channel: technically logged, practically invisible, on the most destructive path in the system. This is the exact trap Philosophy §3 names.
- **Evidence:** `log.py` (the closure: sanitize → JSON → `O_APPEND` + stderr, `0600`); 164 `get_logger` call sites vs 26 stdlib `getLogger` bindings with 0 handler configuration; `ledger_wipe.snapshot_is_restorable` (stdlib `logger.warning`, bare `except Exception`); Constitution **C7.2** + residual **AR-6** (the swallow ratchet accepts stdlib `logging`, so surfacing is a review judgment).
- **Enforcement owner:** `errors`/`log` + review. · **Current:** `documented-only` — **AR-6 is exactly this gap**, accepted at the *ratchet* level. · **Planned:** `proposed` — **`SLICE-STD-SURFACED-BREADCRUMB`**: narrow the swallow ratchet to require the *surfaced* channel **on a named safety-critical path set only** (starting with `ledger_wipe`), not tree-wide. Tree-wide would re-litigate AR-6, which is accepted.

### STD-OBS-02 — a log level matches blast radius
- **Rule:** `get_logger(cfg)`'s `level=` is set when an event is a real skip/failure. Do not let a failure ride the `"info"` default.
- **Rationale:** only **5 of 164** call sites pass `level=`; skips like `no_integration_id` and `cascade_unlink_failed` log at `info`, which makes level-based triage useless.
- **Evidence:** `log.py` (`level="info"` default); the 5 explicit sites (`fanops_hashtags`, `learn_doctor`).
- **Enforcement owner:** authors + review. · **Current:** `documented-only`. · **Planned:** none (a mechanical check cannot judge blast radius — a review rule by nature).

---

## 16 · Migrations — **[REFERENCE]**

Owned by the Constitution and Laws:
- **LAW-PERSIST-03 / C10.1** — a migration is justified only by a real on-disk shape change; additive, idempotent, copy-on-write, **never wipes**; a hop-chain (`SCHEMA_VERSION` v0→v11).
- **LAW-PERSIST-04 / C10.2** — forward-compat via `extra="ignore"`; a shape is dropped **only after every consumer is gone** (the migration is the on-disk half of a teardown).
- **C10.3** — a ledger newer than the running code is **refused loudly**, never loaded-and-field-dropped.
- **C10.4** — a new feature is byte-identical when its flag is off (§20).
- **Philosophy §8** — the migrate-vs-shim decision is mechanical: *does a migration mechanism exist?* The ledger has a hop-chain → it may drop. The accounts registry has none → it must stay lenient.

---

## 17 · Public APIs — **[OWNED]**

### STD-API-01 — the public surface is the facade; a subpackage declares nothing else stable
- **Rule:** import from the **facade** (`fanops.studio.actions`, `fanops.studio.views`, `fanops.post`), not from a private sibling (`actions_run`, `views_review`). The `_`-prefix marks module-private; a bare name in a facade's re-export block is the closest thing to a public API.
- **Rationale:** the facade is the only thing preserving `STD-BOUND-01`'s acyclic tree; a direct sibling import from outside bypasses the contract silently.
- **Evidence:** `actions.py` / `views.py` re-export blocks (`# noqa: F401`); no `__all__` on `post/__init__.py` or `studio/__init__.py`; nothing prevents a direct sibling import today.
- **Enforcement owner:** authors + review. · **Current:** `documented-only`. · **Planned:** `proposed` — `SLICE-STD-API-ALL` (declare `__all__` on the two subpackage inits + the two facades). Low priority: this is an **internal** package with a single consumer (itself); the cost of a wrong boundary is a refactor, not a break.

### STD-API-02 — a provider is added by a registry entry, never by edits scattered across the publish path
- **Rule:** a new publish backend is **one `PROVIDERS` entry** (lazy factory callables). The dryrun/live safety gates live in `post/run.py` and `post/__init__.py` — **not** in `providers.py` — and are not to be moved there.
- **Rationale:** one seam for "who publishes a channel" (C8.2); keeping the gates out of the registry keeps the plugin lookup free of safety logic.
- **Evidence:** `post/providers.py` ("adding a provider later … is a NEW ENTRY here — not edits scattered across the publish path"); `post/__init__.py::get_poster` (raises rather than build a `DryRunPoster` when live); `post/CLAUDE.md` ("These are NOT in providers.py"); **LAW-PROV-01/02** own the go-live gates.
- **Enforcement owner:** providers + `post/CLAUDE.md`. · **Current:** `enforced` for the gates (**LAW-PROV-02**, routing tests); `documented-only` for the registry-entry shape. · **Planned:** none (3/3 backends conform).

---

## 18 · Security — **[REFERENCE]**

Owned by the Constitution and Laws:
- **LAW-SEC-01 / C14.1** — secrets are keyring-first; **reads fail open, writes fail closed** (round-trip verified). *An unverified write would let a caller scrub the plaintext fallback believing it stored.*
- **LAW-SEC-02 / C14.3** — no secret enters a PR diff; the scan has **no bypass** (control `CI-UNIT-SECRETSCAN`, required).
- **C14.2** — API keys are write-only; never rendered back to any surface.
- **C14.4 / AR-2** — the Studio is localhost, no-auth **by design** (a recorded accepted decision, not an oversight). Do not file CSRF/rate-limit tickets against it.
- **Craft note:** error text echoed to a ledger/stdout is redacted (`errors.redact`) — see `STD-DEP`/§6.

---

## 19 · Dependency management — **[OWNED]**

### STD-DEP-01 — CI installs from hash-verified locks; a dependency change regenerates them
- **Rule:** CI installs `pip install --require-hashes -r requirements/ci-{unit,e2e}.txt` then `pip install -e . --no-deps`. Locks are generated by `scripts/lock-deps.sh` (`pip-compile --generate-hashes`), **never hand-edited**, and regenerated on **linux/py3.12** to match the runner. Changing a dependency in `pyproject.toml` **requires** regenerating the locks in the same PR. `[asr]` is nightly-only and **intentionally unlocked**.
- **Rationale:** hashed locks make the CI environment reproducible and the supply chain auditable; the drift guard makes forgetting mechanically impossible.
- **Evidence:** `scripts/lock-deps.sh` (the two profiles + the platform-faithfulness caveat); `scripts/check-locks.sh` (the PR drift guard); control `CI-UNIT-LOCKDRIFT` (**LAW-CI-05**, a required sub-gate).
- **Enforcement owner:** ci-lane (control `CI-UNIT-LOCKDRIFT`). · **Current:** **`enforced`** (blocking sub-gate of the required unit lane). *Known limit: `check-locks.sh` is a regex heuristic over the diff, not a resolver run — an unusual edit shape could slip past.* · **Planned:** none.

### STD-DEP-02 — an optional dependency is lazy, and its absence is a **decided** direction
- **Rule:** an optional extra (`[studio]`, `[compose]`, `[asr]`, `[framing]`, `[keyring]`, `[transcribe]`) is imported **lazily**, and the absent-extra behavior is an explicit decision recorded at the extra: **fail-open** (degrade + breadcrumb) or **fail-closed** (refuse loudly). `[framing]` is the standing example of fail-closed: with `smart_framing` on and cv2 absent the render **refuses** rather than silently centre-crop.
- **Rationale:** C7.1 — the direction follows consequence. A degradable feature degrades; a **correctness prerequisite** refuses (**LAW-FAIL-03**).
- **Evidence:** `pyproject.toml` (each extra's comment states lazy + fail direction); `framing.require_cv2` → `ToolchainMissingError` (exit 2); `scripts/base_install_smoke.py` proves the refusal on a no-extras install (control `CI-BASEINSTALL`).
- **Enforcement owner:** each extra's owner + ci-lane. · **Current:** `partially-enforced` — the `[framing]` contract is proven by `CI-BASEINSTALL` (**advisory today**; Phase-E promotion is LAW-FAIL-03's remediation, owned by ADR-0101). Other extras' directions are `documented-only`. · **Planned:** none new (promotion is the CI program's).

### STD-DEP-03 — workflow actions are SHA-pinned — **[REFERENCE]**
Owned by **LAW-CI-07** (+ `DC-6`, proposed) and `dependabot.yml`. *Historical: the one violation (`lane-guard.yml` floating `@v7`/`@v6`) was fixed in **#663**.*

---

## 20 · Feature flags — **[OWNED]**

### STD-FLAG-01 — a flag is read one way: a `Config` property
- **Rule:** a runtime flag is read via a `Config` property. `Settings` (pydantic) is the **strict validation boundary** used by `doctor`/`config` to fail loud on typos — it is **not** the runtime path. Do not add a third way to read a flag.
- **Rationale:** the split is deliberate (a fail-open runtime + a fail-loud diagnostic), but it is invisible unless stated, and it has already produced a real duplication (below).
- **Evidence:** `config.py` (73 `@property`, ~74 direct `os.getenv`, **zero** delegating to `Settings`); `settings.py` ("typed env boundary"); `doctor._env_settings_check` ("FAIL LOUD on enum/bool typos the runtime path would fail-open on"); 306 test files import `Config`, 3 import `settings`.
- **Enforcement owner:** config. · **Current:** `documented-only` for the rule; **`enforced`** for the env-declaration half (**LAW-ARCH-04**/`ARCH-003`: every env read is declared, incl. the `docs/CONFIG.md` name-set) and for doc parity (`test_config_doc_drift.py`, MOL-296). · **Planned:** `proposed` — `SLICE-STD-BACKEND-PARITY` (below).

### STD-FLAG-02 — default-ON reads off-words; default-OFF reads on-words; a new **learning** signal ships default-OFF
- **Rule:** default-ON flag ⇒ `v not in {"0","false","no","off"}`. Default-OFF flag ⇒ `v in {"1","true","yes","on"}`. Every new **learning/bias** signal ships **default-OFF**, validation-frozen, amplify-only, with its own kill switch and a firewall test proving the off-path is byte-identical.
- **Rationale:** C10.4 — capability without risk. Generation/casting features may be default-ON (they are the product); a *learning* signal that acts before it is proven is how you learn on an unproven shape.
- **Evidence:** ~30 boolean properties in `config.py` follow the word-sets exactly; `docs/FLAGS.md` (the firewall-test table); `validation_gate.learning_validated`; **C10.4** + `src/fanops/CLAUDE.md` ("off by default, frozen until validated, generation/schedule only").
- **Enforcement owner:** each feature owner + `tools/arch`. · **Current:** `partially-enforced` — per-flag firewall tests exist for the headline flags; the *shape* is a review rule. · **Planned:** none (a checklist, not a validator — see `STANDARDS_AUTOMATION_PLAN.md` §Rejected).

### STD-FLAG-03 — a value set has one definition
- **Rule:** a closed value set (e.g. the poster backends) is defined **once** and imported. Two hand-maintained copies are a defect by C2.3, whether the copy is a number or a `frozenset`.
- **Rationale:** the copies are byte-identical **today**; a 4th backend added to one and not the other silently splits validation between the strict diagnostic path and the runtime gate.
- **Evidence:** **live @ `a79528d`** — `_VALID_BACKENDS = frozenset({"dryrun","postiz","zernio"})` is defined independently in **both** `config.py` and `settings.py`; `PosterBackend = Literal[...]` likewise; `accounts.py` imports the `settings.py` copy while `config.py`'s gate checks its own. **No test ties them together.**
- **Enforcement owner:** config. · **Current:** **violated** (`documented-only`, one known live violation). · **Planned:** **`SLICE-STD-BACKEND-PARITY`** — the smallest correct fix is *deletion of one copy* (import from the other), not a parity test guarding two copies (C15.3: "the fix for a rotting copy is deletion"). See `STANDARDS_AUTOMATION_PLAN.md`.

---

## 21 · Technical debt — **[REFERENCE]**

Owned by the Constitution:
- **Constitution §17** — the accepted-residual register (AR-1..AR-8). A residual is acceptable **only** when zero/low-reachability, contained (ideally regression-locked), and documented **with an owner**. *"A deferral is not a discharge."*
- **Philosophy §9** — accepted residual over disproportionate change; measure reachability before forcing a broad fix.
- **C15.2 / Philosophy §5** — prefer the smallest correct solution; over-engineering is rejected **on the record**.

### Standards-layer residuals (this document's own, registered here)

| ID | Residual | Why accepted | Containment | Owner |
|---|---|---|---|---|
| `STD-RESIDUAL-1` | `check_scope.py::_OVERRIDES` is never checked for exhaustiveness against the whole tree | it only needs to be right for **changed** files, which is what `check.sh` scopes; a whole-tree audit would gate on a hand-maintained table | the orphan check fails closed on a changed unmapped module | ci-lane |
| `STD-RESIDUAL-2` | the `slow` marker has no generative criterion (a closed set of named files) | a runtime threshold would be arbitrary; the set is small and reviewed | 4 named files; lane routing is explicit | ci-lane |
| `STD-RESIDUAL-3` | `.markdownlint.json` exists and is wired to **nothing** | zero blast radius (a config with no runner); deleting vs wiring is a real choice, not an obvious fix | no execution path exists | docs |

*Residuals of the **architecture/CI** planes are **not** listed here — they live in Constitution §17.*

---

## 22 · Deletion policy — **[REFERENCE]**

Owned by the Constitution and Laws:
- **LAW-EVO-01 / C15.1 / Philosophy §10** — **"dead / zero-caller" is a LEAD, never a verdict.** A deletion ships a whole-tree **AST census**, not a grep, and is **revalidated at execution**, not when the plan was written.
- **Evidence of why:** the name-based call graph cannot see aliased or lazily-bound backends and once mislabeled **5 live functions** as dead (`post/providers.py`'s lazy lambdas are all flagged "zero callers" and all live); a Cycle-8 plan had **4 of 4** deletion premises invalidated at execution and the deletions were cancelled cleanly.
- **C15.3** — a rotting copy is **deleted**, not re-explained (the basis for `SLICE-STD-BACKEND-PARITY` preferring deletion over a parity test).

---

## Maintenance of this document

- **Drift detection:** [`docs/governance/STANDARDS_MAINTENANCE.md`](governance/STANDARDS_MAINTENANCE.md) — which **extends** the CM-1..CM-8 catalogue in `CONSTITUTION_MAINTENANCE.md`; it defines no second engine.
- **Enforcement crosswalk:** [`docs/governance/STANDARDS_ENFORCEMENT_MATRIX.md`](governance/STANDARDS_ENFORCEMENT_MATRIX.md).
- **Automation:** [`docs/governance/STANDARDS_AUTOMATION_PLAN.md`](governance/STANDARDS_AUTOMATION_PLAN.md) — `SLICE-STD-*` only; runtime/CI slices are cross-referenced to their owning program, never re-planned.
- **Maturity baseline:** [`docs/governance/ENGINEERING_SCORECARD.md`](governance/ENGINEERING_SCORECARD.md).
- **Amendment:** an `STD-*` change that alters a decision records an ADR (C18.1). A `[REFERENCE]` section is **never** amended here — amend the owning document.
- **Provenance:** this document carries a base-SHA header (C16.4). If it disagrees with executable source, live config, or an accepted ADR, **the higher plane wins and this document is corrected.**
