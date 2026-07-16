<!-- Standards Automation Plan — minimal slices to mechanize the unenforced STD-* standards.
     Base: origin/main @ a79528d (#676), 2026-07-16. NOTHING here is implemented.
     SCOPE FENCE: SLICE-STD-* only. Slices already owned by the CI governance program (ADR-0100/0101/0102)
     or by docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md are CROSS-REFERENCED, never re-planned.
     This plan proposes NO new required check and NO branch-protection change. -->

# Standards — Automation Plan (`SLICE-STD-*`)

Every `documented-only` / `violated` standard in [`ENGINEERING_STANDARDS.md`](../ENGINEERING_STANDARDS.md)
is a gap. This converts each into the **smallest effective** slice — or records, with a reason, that it
should **stay unautomated**. It implements none of them.

**Slice fields:** *Invariant · Owner · Validator · Blast radius · Dependencies · Rollback · Tests · CI integration.*
(Deliberately the same vocabulary as `CONSTITUTION_IMPLEMENTATION_ROADMAP.md` so the two roadmaps compose
rather than compete.)

**Three rules every slice obeys** (inherited from ADR-0100 / the arch engine — not re-derived):
1. **A validator ships a negative control** that proves it fires on an injected defect. *A rule nobody has tried to fool is a rule nobody should trust.*
2. **Advisory first, promotion later** — promotion to a required check follows **ADR-0101 §8**, is **not** proposed here, and is never bundled with the slice that builds the validator.
3. **Report, never auto-fix.** No slice edits source, docs, the registry, or branch protection to make itself pass.

> **Scope fence.** This plan owns **no** runtime remediation, **no** CI promotion, and **no** ADR. Where a
> gap is already owned, the row below points at its owner and stops.

---

## 1 · Cross-references — owned elsewhere, NOT re-planned here

| Gap | Already owned by | Do not duplicate |
|---|---|---|
| Stale `file:line` anchors (**STD-DOC-01**) | **`CM-7`** in [`CONSTITUTION_MAINTENANCE.md`](CONSTITUTION_MAINTENANCE.md), built by `SLICE-CONSTLINT` | this layer is CM-7's **consumer**; it defines no anchor checker |
| Dangling citations / schema / supersession in this layer | **`CM-1`/`CM-2`/`CM-5`/`CM-6`** (`SLICE-CONSTLINT`) | `STANDARDS_MAINTENANCE.md` extends CM-*, adds no engine |
| "enforced" claims that disagree with reality | **`CM-8`** (`SLICE-CM-CONTRADICTION`, gated on CI `DC-3`) | — |
| `CI-BASEINSTALL` → required (**STD-LAYOUT-01**, **STD-DEP-02**) | **ADR-0101 Phase E** / `SLICE-BASEINSTALL-REQUIRED` | no promotion proposed here |
| CI prose number-rot (**LAW-SOT-03**) | `tools/ci` **`DC-4`** | — |
| Generated-view byte-gating | `tools/ci` **`DC-5`**; arch `ARCH-006` | — |
| Workflow SHA-pin/timeout hygiene | `tools/ci` **`DC-6`** (**LAW-CI-07**) | — |
| `AGENTS.md` advertises the dormant land-gate | `SLICE-DOC-AGENTS` (constitution roadmap) | — |
| `anomalies.md` stale "all HOLD" banner | `SLICE-DOC-ANOMALIES` (constitution roadmap) | — |
| ADR numbering collision; Tier-1 formalization | `SLICE-ADR-NUMBERING` → `SLICE-ADR-FORMALIZE-T1` | this layer cuts **no** ADR |

---

## 2 · Slices

### SLICE-STD-BACKEND-PARITY — **the only live standards violation** *(highest value, smallest diff)*
- **Invariant:** a closed value set has exactly one definition (**STD-FLAG-03**, C2.3).
- **Evidence:** `_VALID_BACKENDS = frozenset({"dryrun","postiz","zernio"})` and `PosterBackend = Literal[...]` are each defined **independently twice** — in `config.py` **and** `settings.py` — byte-identical today, with **no test tying them**. `accounts.py` imports the `settings.py` copy while `config.py`'s runtime gate checks its own. A 4th backend added to one silently splits validation between the strict diagnostic path (`doctor`/`config`) and the runtime gate.
- **Owner:** `config`. · **Validator:** **none — deliberately.** The fix is **deletion of one copy** (one module imports from the other), per C15.3 *"the fix for a rotting copy is deletion"* and C15.2 *smallest correct solution*. A parity test would **institutionalize** the two copies it is meant to protect against — the exact anti-pattern the `_CLI_PRINT_COUNT` saga teaches.
- **Blast radius:** 2 modules, one import edge. `settings.py` is the natural home (it already declares the typed boundary and `accounts.py` already imports from it) — so `config.py` imports from `settings`. **Check first:** `config.py` must not import `settings` at module level if that creates a cycle; if it does, the shared set moves to a leaf (`ids.py`-style) instead. This is a real, cheap risk to resolve at implementation, not now.
- **Dependencies:** none. · **Rollback:** revert (a one-import change).
- **Tests:** the existing `test_config.py` / `test_config_verb.py` / `test_settings` suites must stay green; add **no** new parity test (that is the point).
- **CI integration:** none new — it rides the required unit lane.

### SLICE-STD-ATOMIC — an AST ratchet for hand-rolled atomic writes
- **Invariant:** every hand-editable control-file write routes through `controlio` (**STD-PERSIST-01**).
- **Evidence:** `controlio.py`'s own docstring warns a fixed `<name>.tmp` "lets two concurrent writers clobber each other's temp"; `autopilot.set_env_var` hand-rolls exactly that against the single global `.env` with no lock across multiple callers (`golive._dual_write`, `cli`, `daemon.install`). ≥6 files hand-roll a temp+replace; the others are lower-risk (per-key-unique paths or an external lock).
- **Owner:** `controlio` + `tools/arch`-style AST ratchet. · **Validator:** a new `tests/test_atomic_write_ratchet.py` modelled **exactly** on `test_swallow_ratchet.py`: walk `src/fanops/**` ASTs, flag a `.tmp`/`mkstemp`+`os.replace` pattern outside `controlio`/`ledger_sqlite`/the ffmpeg `.part` sites, **baseline the known deviations by file**, and fail only on a **new or increased** count.
- **Blast radius:** one test file. **No source change** — the ratchet baselines today's reality and stops growth. *(Fixing `autopilot.set_env_var` itself is a separate runtime slice, deliberately not bundled: a ratchet is docs-safe, a `.env`-writer change is not.)*
- **Dependencies:** none. · **Rollback:** delete the test (one-line collection revert).
- **Tests:** the ratchet **is** the test; ships with a negative control (inject a hand-rolled fixed-name temp writer into a fixture module → must fail).
- **CI integration:** collected by the existing required unit lane (`CI-UNIT-PYTEST`). **No new control row**, no promotion.

### SLICE-STD-SURFACED-BREADCRUMB — surfaced logging on named safety-critical paths **only**
- **Invariant:** a fail-open breadcrumb on a safety-critical path uses the **surfaced** channel (**STD-OBS-01**, C7.2).
- **Evidence:** `src/fanops/` configures **no** stdlib logging handler anywhere (0 `basicConfig`/`dictConfig`/`addHandler`), so a stdlib log line's visibility depends on the host. `ledger_wipe.snapshot_is_restorable` — the pre-wipe restorability gate, the most destructive path in the system — catches bare `Exception` and logs via the unconfigured stdlib channel. `anomalies.md` already rates this the weakest site on the most dangerous path.
- **Owner:** `errors`/`log` + reviewers. · **Validator:** extend the swallow ratchet with a **small explicit path allowlist** (`ledger_wipe.py`, and any future path an operator must not be blind on): within those files a broad-except handler must call `get_logger(cfg)` or `errors.fail_open`, not bare stdlib `logging`.
- **Blast radius:** the ratchet + (separately) one narrowing of `snapshot_is_restorable`'s handler. **Scoped deliberately narrow: tree-wide enforcement is `AR-6`, an accepted residual — this slice must not re-litigate it.** The whole justification is that *this specific path's* blast radius is data loss.
- **Dependencies:** none. · **Rollback:** shrink the allowlist to empty (the ratchet degrades to today's behavior).
- **Tests:** negative control — point the allowlist at a fixture that logs via stdlib in a broad except → must fail.
- **CI integration:** the existing unit lane. No new control row.

### SLICE-STD-DOC-CORRECTIONS — correct the three measured doc/code drifts
- **Invariant:** a doc does not describe code that has changed underneath it (**STD-DOC-02**, C16.4).
- **Evidence (all live @ `a79528d`):** (a) `docs/CONTROL-FILES.md` states `00_control/ledger.json` is "load-bearing state store (**the only state store**)" while `ledger.py` declares SQLite the single source of truth and `ledger.json` **read-only break-glass, never written after M1-F**; (b) `docs/CI_SLO.md` carries an illustrative `e2e_slow_s` figure inconsistent with the real ~170 s negative-control load; (c) `src/fanops/CLAUDE.md`'s sibling-parity note has **reversed direction** (it frames `Accounts.load` as the deficient sibling; `accounts.py` now has a per-row guard **and** a `load_accounts_safe`, while `Personas.load` has neither).
- **Owner:** docs. · **Validator:** none for (a)/(c) — a one-time correction. `CM-4`/`CM-8` **report** this class going forward.
- **Blast radius:** docs-only, 3 files. **Not this PR:** (a) and (b) touch tracked docs owned by other programs (persistence, CI SLO) and (c) touches a nested rulebook — each is a **follow-up doc PR**, matching how the constitution roadmap sequenced `SLICE-DOC-AGENTS`/`SLICE-DOC-ANOMALIES` out of its own PR.
- **Dependencies:** none. · **Rollback:** revert. · **Tests:** none (prose). · **CI integration:** none.

### SLICE-STD-SIZE — advisory module-size census
- **Invariant:** a module over ~1,200 lines is a **noted decision**, not drift (**STD-LAYOUT-03**).
- **Evidence:** `cli.py` 1465 (**grew 1448→1465 during this audit**), `studio/views.py` 1435, `studio/actions.py` 1188, `config.py` 1151. None is a declared hot file, so no guard watches them.
- **Owner:** authors. · **Validator:** a report-only census (a `tools/arch`-style derived table, or a line in the existing arch report). **Never blocking** — a line-count gate is precisely the "decoration that makes a dashboard green" `policy.py` warns against, and would push authors to split badly rather than deliberately.
- **Blast radius:** one report. · **Dependencies:** none. · **Rollback:** delete the report.
- **Tests:** n/a (report-only; no verdict to control). · **CI integration:** report only. **Explicitly never a required check.**

### SLICE-STD-API-ALL — declare the public surface
- **Invariant:** the facade is the public surface (**STD-API-01**).
- **Evidence:** neither `post/__init__.py` nor `studio/__init__.py` declares `__all__`; nothing flags a direct sibling import bypassing the `actions.py`/`views.py` facade.
- **Owner:** `post`, `studio`. · **Validator:** `__all__` + (optionally) a ruff `F401`-consistent re-export block. No new test.
- **Blast radius:** 2–4 `__init__`/facade files. **Low priority — recorded honestly:** this is an internal package with exactly one consumer (itself), so a wrong boundary costs a refactor, not a break.
- **Dependencies:** none. · **Rollback:** delete the `__all__` lines. · **Tests:** existing import tests. · **CI integration:** none new.

### SLICE-STD-ERRTIER — every `errors.py` class has a `cli.py` arm
- **Invariant:** an operator-facing exception exits with a deliberate code, never a raw traceback (**STD-ERR-01**).
- **Evidence:** 11/11 `errors.py` classes currently have a `cli.py` catch arm with a fixed exit code — the convention holds, but nothing keeps it. `framing_outcomes.ResolverInvariantError` (internal tier, 0 catch sites) shows what an unpaired error looks like if it ever escaped its module.
- **Owner:** `errors`/`cli`. · **Validator:** an AST check — every public class in `errors.py` appears in a `cli.py` `except` clause.
- **Blast radius:** one test. · **Dependencies:** none. · **Rollback:** delete the test.
- **Tests:** negative control — add an `errors.py` class with no arm → must fail. · **CI integration:** unit lane. **Low priority** (11/11 today).

### SLICE-STD-MARKDOWNLINT — wire it or delete it
- **Invariant:** a declared mechanism has an executor, or it is not declared (`CM-4`'s dormant-governance class; **STD-RESIDUAL-3**).
- **Evidence:** `.markdownlint.json` exists with a considered philosophy near-verbatim mirroring the ruff config's ("only CORRECTNESS rules stay on… every disable is a documented formatting-preference") — and is invoked by **nothing**: zero references in any workflow, hook, or script; no `package.json` in the repo.
- **Owner:** docs/operator. · **Validator:** n/a — this is a **decision**, not a check: wire it (adds a Node toolchain to a Python repo — a real cost for a single-operator tool) or delete the config.
- **Blast radius:** one config file, or one CI step + a toolchain. · **Dependencies:** none. · **Rollback:** trivial either way.
- **Tests:** n/a. · **CI integration:** none proposed. **Recommendation: delete.** A Node toolchain for markdown style in a Python repo fails C15.2 (smallest correct solution); the config's own philosophy ("correctness only") is already served by review.

---

## 3 · Blocked on an operator decision

| Slice | The decision | Why this document does not make it | Owner |
|---|---|---|---|
| `BLOCK-RELEASE-POLICY` | define (or explicitly decline) a release/versioning/CHANGELOG policy (**STD-VER-02**) | a single-operator, continuously-deployed localhost tool may legitimately want **no** semver/release machinery; inventing one here would be policy the evidence does not compel. `v0.1` in the ship-route doc is a *milestone label*, unrelated to `[project].version`. | operator |
| `BLOCK-STATIC-TYPING` | adopt mypy/pyright on a typed subset, or **explicitly accept "no static checker"** | ~80–82 % of functions are annotated and `pipeline.py`'s `TypedDict`-over-dataclass rationale **explicitly assumes "a checker"** that does not exist — so today the comment claims a safety property nothing provides. Adopting a checker on a 130-module tree with a deliberate compact one-liner style is a real cost; *either* answer is defensible, but the current silent middle is not. If declined, the `pipeline.py` comment must be corrected (it asserts a mechanism that does not exist — the repo's signature defect class, C2.2). | operator |

---

## 4 · Deliberately NOT automated (recorded so it is not re-proposed)

| Standard | Why no validator |
|---|---|
| **STD-NAME-01/02** (naming) | **1889/1889** functions and **159/159** classes conform; 16/16 enums conform. A validator for a convention with zero measured violations is decoration. |
| **STD-BOUND-01** (no sibling cross-imports) | 0 violations measured; **LAW-ARCH-02** (`ARCH-004`) is the cycle backstop if it were ever broken. |
| **STD-OBS-02** (level matches blast radius) | a machine cannot judge blast radius. A review rule by nature. |
| **STD-FLAG-02** (flag shape) | the "default-OFF, validation-frozen, amplify-only, firewall-tested" shape is a **checklist across four subsystems**, not a predicate; per-flag firewall tests already prove the load-bearing half. |
| **STD-VER-01** (one version authority) | **structurally enforced** — the derivation cannot disagree with its own source. A test would assert a tautology. |
| Tree-wide surfaced-logging | **`AR-6`** — an accepted residual (surfacing is a review judgment the AST cannot make). `SLICE-STD-SURFACED-BREADCRUMB` is scoped to a named safety-critical path set precisely to **not** re-litigate it. |

---

## 5 · Ordered execution

```
SLICE-STD-BACKEND-PARITY   ─ independent, highest value (the one live violation)
SLICE-STD-ATOMIC           ─ independent (ratchet only; no source change)
SLICE-STD-SURFACED-BREADCRUMB ─ independent (narrow allowlist)
SLICE-STD-DOC-CORRECTIONS  ─ independent  → follow-up doc PRs (3 files, 3 owners)
SLICE-STD-SIZE / -API-ALL / -ERRTIER / -MARKDOWNLINT ─ low priority, any order
CI-PROGRAM DC-*  ─► (CM-8) ─► standards contradiction reporting  [cross-ref only]
SLICE-CONSTLINT  ─► CM-7   ─► STD-DOC-01 anchor reporting        [cross-ref only]
```

**First wave (safe, highest value):** `SLICE-STD-BACKEND-PARITY` (deletes the one live violation),
then `SLICE-STD-ATOMIC` (ratchets the largest silent-growth surface without touching source).
**Second wave:** `SLICE-STD-SURFACED-BREADCRUMB`, `SLICE-STD-DOC-CORRECTIONS` (follow-up doc PRs).
**Third wave (low priority):** size census, `__all__`, err-tier, markdownlint decision.
**Operator track (parallel):** the two §3 product decisions.

**No slice in this plan proposes a new required check, a branch-protection change, or a workflow edit.**
Promotion of anything here follows ADR-0101 §8 and is the CI program's to sequence.
