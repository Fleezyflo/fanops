<!-- Standards Enforcement Matrix — the crosswalk for the STD-* layer ONLY.
     Base: origin/main @ a79528d (#676), 2026-07-16.
     THIS IS A JOIN TABLE, NOT A REGISTRY. It references .github/ci-control-registry.yml control ids and
     docs/ARCHITECTURAL_LAWS.md law ids; it never restates a control row or a law. Where a topic is owned
     by a LAW-*, the row is a POINTER (marked [REF]) and carries no independent enforcement claim. -->

# Standards Enforcement Matrix (`STD-*`)

**Scope.** Only the **[OWNED]** standards of [`docs/ENGINEERING_STANDARDS.md`](../ENGINEERING_STANDARDS.md).
Topics owned by a law or an ADR appear as `[REF]` pointer rows so the crosswalk is complete, but their
enforcement truth lives in the owning document.

**Integration, not duplication (ADR-0100's two hard constraints).**
- **No second registry.** The `Validator` column cites a `.github/ci-control-registry.yml` control **`id`** or a test name. This file owns **no** control row.
- **No competing law.** The `Authority` column names the owner. Where it is a `LAW-*` or an ADR, this matrix is *derived* from that document and loses any disagreement with it (Constitution §2 precedence).

**Column meanings.** *Authority* = who owns the rule · *Current* = enforcement **today** (honest) · *Future* = planned/proposed · *CI owner* = the lane/control that would gate it · *Runtime owner* = the module that owns the behavior · *Doc owner* = who maintains the prose · *Validator* = the mechanism (control id / test / ratchet) · *Residual* = accepted gap · *ADR* = governing decision.

**Status vocabulary:** `enforced` · `partially-enforced` · `documented-only` · `proposed` · `violated` · `accepted-residual` · `n/a`.

---

## A · Owned standards (this layer's enforcement truth)

| Standard | Authority | Current enforcement | Future enforcement | CI owner | Runtime owner | Doc owner | Validator | Residual | ADR |
|---|---|---|---|---|---|---|---|---|---|
| **STD-NAME-01** snake_case fn / PascalCase class | `STD` | `documented-only` (1889/1889, 159/159 conform) | **none — deliberately** | — | authors | standards | none (ruff `select=["E","F"]` has no naming rules) | — | — |
| **STD-NAME-02** `(str, Enum)` sets; `_UPPER` tunables | `STD` | `documented-only` (16/16 enums) | none | — | `models` | standards | none | — | — |
| **STD-NAME-03** `FANOPS_*` env; `ids.py` content ids | `STD` + **LAW-ARCH-04** | env-declaration half **`enforced`**; prefix/id-format `documented-only` | none | `ARCH-GATE`, `CI-UNIT-ARCHGOV` | `ids`, `config` | standards | `ARCH-003` (declared env reads); `ids.py` is structurally the only id maker | — | — |
| **STD-LAYOUT-01** subpackage iff optional-dep | `STD` | `partially-enforced` — the no-extras **contract** is proven; the layout rule is `documented-only` | `CI-BASEINSTALL` promotion (**owned by ADR-0101 Phase E**) | `CI-BASEINSTALL` (**advisory today**) | `post`, `studio` | standards | `scripts/base_install_smoke.py` | AR-3 (2 live vs 5 intended required) | 0101 |
| **STD-LAYOUT-02** prefixed-flat split, byte-identical | `STD` | `documented-only` | none | — | authors | standards | none | — | — |
| **STD-LAYOUT-03** ~1,200-line size **budget** (not a cap) | `STD` | `documented-only` (`cli.py` 1465, `views.py` 1435 over budget, both deliberate) | `proposed` — advisory report-only census | none (**never blocking**) | authors | standards | `SLICE-STD-SIZE` | — | — |
| **STD-BOUND-01** facade re-exports; siblings never cross-import | `STD` | `documented-only` (0 violations measured) | none | — | `studio` | `studio/CLAUDE.md` | none (**LAW-ARCH-02** covers cycles, not this stricter tree) | — | — |
| **STD-BOUND-02** thin route: parse→one fn→render | `STD` | `documented-only` | none | — | `studio` | `studio/CLAUDE.md` | none | — | — |
| **STD-BOUND-03** `views_*` pure reads; one `Ledger.transaction` | `STD` + **LAW-PERSIST-01** | `partially-enforced` — the txn mechanism is `enforced`; "views never write" is `documented-only` w/ 2 recorded exceptions | none | `CI-UNIT-PYTEST` | `ledger`, `studio` | `studio/CLAUDE.md` | lock-free tests; 60 s deadlock guardrail | 2 sanctioned view exceptions | — |
| **STD-VER-01** one version authority (`pyproject`) | `STD` | **`enforced` — structurally** (derived via `importlib.metadata`; no second literal exists to drift) | none needed | — | packaging / `__init__` | standards | *structural* — a test would assert a tautology | — | — |
| **STD-VER-02** release/versioning process | `STD` | `proposed` — **undecided, no rule exists** | operator decision | — | — | standards | none | — | `BLOCK-RELEASE-POLICY` |
| **STD-ERR-01** operator errors in `errors.py` + a `cli.py` arm | `STD` | `documented-only` (11/11 paired) | `proposed` advisory check | — | `errors`, `cli` | standards | `SLICE-STD-ERRTIER` | — | — |
| **STD-PERSIST-01** control-file writes route through `controlio` | `STD` | `documented-only` — **≥6 files hand-roll a temp+replace**; `autopilot.set_env_var` reproduces the exact hazard `controlio` warns against | `proposed` — AST ratchet (baseline + block new) | `CI-UNIT-PYTEST` | `controlio` | `src/fanops/CLAUDE.md` | `SLICE-STD-ATOMIC` | known deviations baselined | — |
| **STD-TEST-01** changed src maps to a test | `STD` | **`enforced`** for changed files | none | `LOCAL-CHECKSH` / pre-commit | `check_scope.py` | standards | `scripts/check_scope.py::orphan_src_modules` | `STD-RESIDUAL-1` (override table not exhaustiveness-checked) | — |
| **STD-TEST-02** hermetic env (`_LEAKY_ENV`) | `STD` | **`enforced`** (autouse, no opt-out) for listed keys; "add your new flag" is `documented-only` | none | `CI-UNIT-PYTEST` | `tests/conftest.py` | `tests/CLAUDE.md` | `_hermetic_publish_env` fixture | — | — |
| **STD-TEST-03** `Config(root=tmp_path)` isolation | `STD` | `partially-enforced` (one targeted regression lock) | none | `CI-UNIT-PYTEST` | `tests/conftest.py` | `tests/CLAUDE.md` | `test_the_runner_never_mutates_FANOPS_ROOT` | — | — |
| **STD-TEST-04** a marker is a lane contract | `STD` + **LAW-CI-01** | **`enforced`** — directory↔marker ratchet (#666) + skip→fail hook + guard-on-the-guard | none | `CI-UNIT-HOOKVERIFY`, `CI-E2E-INTEGRATION` | `tests/conftest.py` | `tests/CLAUDE.md` | marker ratchet; `test_ci_require_e2e.py` | `STD-RESIDUAL-2` (`slow` has no generative criterion) | 0101 |
| **STD-DOC-01** cite the symbol; `file:line` is a hint | `STD` | `documented-only` | **`CM-7`** (report-only) — **already designed** in `CONSTITUTION_MAINTENANCE.md`; this standard is its *consumer* | — | — | constitution maintainer | `CM-7` via `SLICE-CONSTLINT` | anchors are hints by design | — |
| **STD-DOC-02** doc home = authority level; supersede with a banner | `STD` | `documented-only` — **live drift**: `CONTROL-FILES.md` still calls `ledger.json` "the only state store" | `SLICE-STD-DOC-CORRECTIONS`; then **CM-4/CM-8** report the class | — | — | docs | `CM-4`/`CM-8` (designed, not built) | `STD-RESIDUAL-3` (markdownlint unwired) | — |
| **STD-PERF-01** measured, blocking CI budget; no product SLO | `STD` | **`enforced`** (unit lane, blocking); e2e timing advisory **by decision** | none | `CI-UNIT-SLO` | `scripts/ci_slo_gate.py` | `docs/CI_SLO.md` | `ci_slo_gate.check_budget` | stale illustrative `e2e_slow_s` figure → doc-correction | 0101 |
| **STD-OBS-01** surfaced channel for operator-visible events | `STD` + **C7.2** | `documented-only` — **AR-6 accepts this gap at the ratchet level**; 26 stdlib sites, **0 handlers configured**; the wipe guard logs to the unconfigured channel | `proposed` — narrow the ratchet to a **named safety-critical path set only** | `CI-UNIT-PYTEST` | `log`, `errors` | standards | `SLICE-STD-SURFACED-BREADCRUMB` | **AR-6** (tree-wide is accepted; do not re-litigate) | — |
| **STD-OBS-02** level matches blast radius | `STD` | `documented-only` (5/164 sites set `level=`) | **none** — a machine cannot judge blast radius | — | `log` | standards | none (review rule by nature) | — | — |
| **STD-API-01** import the facade, not a sibling | `STD` | `documented-only` (no `__all__`; nothing prevents it) | `proposed` — declare `__all__` | — | `post`, `studio` | standards | `SLICE-STD-API-ALL` | internal package, single consumer | — |
| **STD-API-02** a provider is one registry entry; gates stay out of `providers.py` | `STD` + **LAW-PROV-02** | gates **`enforced`**; registry shape `documented-only` (3/3 conform) | none | `CI-UNIT-PYTEST` | `post/providers`, `post/__init__` | `post/CLAUDE.md` | routing tests; `get_poster` raises when live | — | — |
| **STD-DEP-01** hash-verified locks; regen on dep change | `STD` + **LAW-CI-05** | **`enforced`** (required sub-gate) | none | `CI-UNIT-LOCKDRIFT` | `scripts/lock-deps.sh` | `AGENTS.md` | `scripts/check-locks.sh` | regex heuristic, not a resolver run | 0101 |
| **STD-DEP-02** optional extra is lazy + a **decided** fail direction | `STD` + **LAW-FAIL-03** | `partially-enforced` — `[framing]` proven by `CI-BASEINSTALL` (advisory); others `documented-only` | promotion owned by **ADR-0101 Phase E** | `CI-BASEINSTALL` | each extra's owner | `pyproject.toml` | `base_install_smoke.py` | AR-3 | 0101 |
| **STD-FLAG-01** one read path (`Config` property) | `STD` | `documented-only` for the rule; **`enforced`** for env-declaration + doc parity | `SLICE-STD-BACKEND-PARITY` | `ARCH-GATE`, `CI-UNIT-ARCHGOV` | `config` | `docs/CONFIG.md` | `ARCH-003`; `test_config_doc_drift.py` | — | — |
| **STD-FLAG-02** default-ON/OFF word-sets; learning ships OFF | `STD` + **C10.4** | `partially-enforced` (per-flag firewall tests for headline flags) | **none** — a checklist, not a validator | `CI-UNIT-PYTEST` | `config`, feature owners | `docs/FLAGS.md` | firewall tests | — | — |
| **STD-FLAG-03** a value set has one definition | `STD` + **C2.3** | **`violated`** — `_VALID_BACKENDS` + `PosterBackend` each defined twice (`config.py`, `settings.py`), **no test ties them** | `SLICE-STD-BACKEND-PARITY` (**delete a copy**, per C15.3) | `CI-UNIT-PYTEST` | `config`, `settings` | standards | none today | — | — |

## B · Pointer rows (`[REF]` — enforcement truth lives in the owning document)

| Topic | Authority (owner) | Where enforcement is stated |
|---|---|---|
| Imports (cycles, lazy-hoist) | **LAW-ARCH-02**, **LAW-ARCH-03** | `ARCHITECTURAL_LAWS.md` · `ARCH-GATE` |
| Error direction / swallow / print ratchets | **C7.1**, **LAW-FAIL-01/02/03/04** | `ARCHITECTURAL_LAWS.md` · `CI-UNIT-PYTEST` |
| State ownership / no-auto-publish / terminal doors | **LAW-STATE-01..04**, **C5** | `ARCHITECTURAL_LAWS.md` |
| Persistence cardinal rule / wipe / restore | **LAW-PERSIST-01/02** | `ARCHITECTURAL_LAWS.md` |
| Migrations / forward-compat | **LAW-PERSIST-03/04**, **C10** | `ARCHITECTURAL_LAWS.md` |
| Testing authority (CI-only, timeout, neg-controls) | **LAW-CI-01/02/03** | `ARCHITECTURAL_LAWS.md` |
| Generated docs are views | **LAW-DOC-01**, **C16.3** | `ARCHITECTURAL_LAWS.md` · `ARCH-GATE` |
| ADR practice + formalization order | **C16.1/16.2** | `docs/adr/FORMALIZATION_ROADMAP.md` |
| **All CI controls, required set, merge gate** | **ADR-0100/0101**, `.github/ci-control-registry.yml` | the registry — **never restated here** |
| Merge strategy / history | **ADR-0102**, **LAW-CI-06** | `docs/adr/0102-*.md` |
| Workflow hygiene (SHA pins, timeouts) | **LAW-CI-07** (+ `DC-6`, proposed) | `ARCHITECTURAL_LAWS.md` |
| Security / secrets | **LAW-SEC-01/02**, **C14** | `ARCHITECTURAL_LAWS.md` |
| Deletion policy | **LAW-EVO-01**, **C15.1** | `ARCHITECTURAL_LAWS.md` |
| Accepted residuals (arch/CI planes) | **Constitution §17** (AR-1..AR-8) | `REPOSITORY_CONSTITUTION.md` |

---

## Enforcement tally (`STD-*` owned rows only)

| Status | Count | Notes |
|---|---|---|
| `enforced` | **8** | STD-VER-01 (structural), STD-TEST-01/02/04, STD-PERF-01, STD-DEP-01, + the enforced halves of STD-NAME-03 / STD-API-02 |
| `partially-enforced` | **6** | STD-LAYOUT-01, STD-BOUND-03, STD-TEST-03, STD-DEP-02, STD-FLAG-01, STD-FLAG-02 |
| `documented-only` | **10** | the craft conventions with 0 measured violations (naming, boundaries) + the real gaps (STD-OBS-01/02, STD-PERSIST-01, STD-DOC-01/02, STD-API-01, STD-ERR-01) |
| `violated` | **1** | **STD-FLAG-03** — the one live, unowned standards defect at `a79528d` |
| `proposed` (no rule yet) | **1** | STD-VER-02 (release policy — an operator decision) |

**Honesty note.** `documented-only` is not a failure by itself. Three of those rows (STD-NAME-01/02, STD-BOUND-01) have **zero measured violations** across the whole tree — the correct planned enforcement is **none**, because a validator for a convention nobody breaks is exactly the "decoration that makes a dashboard green" the policy engine warns against. The rows that *do* need mechanism are enumerated as slices in [`STANDARDS_AUTOMATION_PLAN.md`](STANDARDS_AUTOMATION_PLAN.md).
