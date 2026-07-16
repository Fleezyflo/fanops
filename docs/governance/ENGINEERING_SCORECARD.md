<!-- Engineering Scorecard — the durable maturity baseline for future reviews.
     Base: origin/main @ a79528d (#676), 2026-07-16. Replaces the point-in-time findings of the
     Engineering Standards Audit with a re-measurable per-category baseline.
     This is a BASELINE, not an audit: each row states what to re-measure, not a one-off verdict. -->

# FanOps — Engineering Scorecard

**Purpose.** A durable, re-measurable maturity baseline. The audit that produced it is **historical**;
this scorecard is what future reviews re-score. Each category carries the *evidence to re-derive*, not a
frozen verdict — per Philosophy §7, *"a claim not revalidated against the current tree is presumed stale."*

**Maturity scale.**
- **Exemplary** — mechanically enforced, proven to fire (a negative control or a red-on-violation test), and above typical practice for a project of this kind.
- **Strong** — enforced, but with a known limit or an accepted residual.
- **Adequate** — the convention holds in practice; enforcement is convention/review only, and no violation is measured.
- **Weak** — the convention is stated but drifts, or a live violation exists.
- **Absent** — no standard exists.

**Trend** is measured against the audit base (`0a3b503`/`9ea4bc6`, 2026-07-15) → this base (`a79528d`, 2026-07-16).

**Re-scoring rule.** Do not re-score from this document. Re-derive each row's evidence against the
then-current `origin/main`, then update. A row whose evidence cannot be re-derived is **stale, not passing**.

---

## Scorecard

### 1 · Architecture governance
- **Maturity:** **Exemplary** · **Trend:** ↑ improving · **Owner:** `tools/arch`
- **Evidence (re-derive):** `tools/arch/policy.py::RULES` (rule count + severities); `tools/arch/selftest.py::CONTROLS` (negative controls); `test_every_rule_is_reachable`; `ARCH-GATE` / `CI-UNIT-ARCHGOV` control rows.
- **Strengths:** the DERIVED/DECLARED/GENERATED split; a byte-compare drift gate; **every rule has a negative control proving it fires** — a discipline most mature projects lack entirely; a time-boxed exceptions registry; an unknowns ceiling that blocks growth.
- **Weaknesses:** `ARCH-008` (side-effect census) is WARNING-only; two arch harnesses execute per PR (a declared `duplicate_group`).
- **Risks:** the unknowns ceiling sits at **8/8 — zero headroom**; the next UNKNOWN discovered anywhere blocks CI until deliberately raised. That is by design, but it is a live tripwire.
- **Since audit:** `IMPL-007` widened to scan the engine itself (#641); the rotting "21" comment deleted (#665).

### 2 · CI governance & merge control
- **Maturity:** **Strong** (mechanism) / **partially deployed** · **Trend:** ↑↑ strongly improving · **Owner:** `tools/ci` + ADR-0100/0101/0102
- **Evidence (re-derive):** `.github/ci-control-registry.yml` (`intended_required_contexts` vs `current_required_contexts`, `rollout.phase`); `tools/ci` DC-1..DC-6; live `gh api …/branches/main/protection`.
- **Strengths:** the control plane the audit said did not exist **now exists** — a registry of intent + a validator + a declared precedence order; ADR-0100/0101/0102 give merge policy a written, versioned owner.
- **Weaknesses:** **2 of 5** intended required contexts are live; the validator is built but **not yet blocking**; `enforce_admins=false`.
- **Risks:** CI is the *sole* merge-quality gate (0 required reviews), which raises the stakes on the required set being correct — the reason ADR-0101 sequences Phase E one control at a time.
- **Since audit:** registry + validator landed (#661); validator wired into the unit lane (#670); lane-guard hardened (#663); `ci-timing` bounded (#668). **The audit's thesis — "merge policy is not machine-verifiable" — is superseded** (R4).

### 3 · Failure semantics & error handling
- **Maturity:** **Strong** · **Trend:** → stable · **Owner:** `errors` + reviewers
- **Evidence (re-derive):** `tests/test_swallow_ratchet.py` (the per-file baseline); `test_internal_prints_routed.py` (`_CLI_PRINT_COUNT`); `errors.fail_open`; `framing.require_cv2`.
- **Strengths:** a **three-branch** fail-direction rule (verdict → more checking; feature → safe default + breadcrumb; prerequisite → closed and loud) that is genuinely reasoned, not "always fail open"; two AST ratchets bounding silent growth.
- **Weaknesses:** the swallow ratchet accepts stdlib `logging`, so *surfacing* is a review judgment (**AR-6**); the print ratchet covers only 9 named modules + `cli.py`.
- **Risks:** **`logging ≠ surfacing`** — `src/fanops/` configures **zero** stdlib handlers, so 26 stdlib log sites are host-dependent. The wipe path's restorability guard logs there: technically logged, practically invisible, on the most destructive path (`SLICE-STD-SURFACED-BREADCRUMB`).

### 4 · Persistence & data integrity
- **Maturity:** **Exemplary** · **Trend:** ↑ improving · **Owner:** `ledger`
- **Evidence (re-derive):** `ledger_sqlite.py` (WAL, `synchronous=FULL`, `0600`); `ledger.py::_MIGRATIONS` + `SCHEMA_VERSION`; `_NewerSchema`; `test_ledger_migration.py`, `test_ledger_sqlite_store.py`.
- **Strengths:** the cardinal rule (no I/O inside the ledger lock) with tests **and** a 60 s deadlock guardrail; a forward-only tested hop-chain (v0→v11) where a newer-than-code ledger is **refused, never field-dropped**; wipe is snapshot + typed-confirm + verified-restorable.
- **Weaknesses:** the `controlio` atomic-write rule is stated in a rulebook and checked by nothing; ≥6 files hand-roll a temp+replace, one (`autopilot.set_env_var`) reproducing the exact fixed-name hazard `controlio` warns against.
- **Risks:** low — the deviations are mostly per-key-unique paths or externally locked; the `.env` writer is the real one.
- **Since audit:** **RC-4/RC-5 (CRITICAL wipe/restore data-loss) fixed** (#653/#654/#655).

### 5 · Testing
- **Maturity:** **Strong** · **Trend:** ↑ improving · **Owner:** ci-lane
- **Evidence (re-derive):** `pyproject.toml [tool.pytest] markers`; `tests/conftest.py` (`_LEAKY_ENV`, skip→fail hookwrapper); `tests/integration/` marker ratchet; `scripts/check_scope.py`; control rows `CI-UNIT-PYTEST` / `CI-E2E-INTEGRATION` / `CI-UNIT-HOOKVERIFY`.
- **Strengths:** hermetic-by-construction env (autouse, no opt-out) with a *causal* rationale; markers as lane contracts with a **guard on the guard** (the skip→fail hook has its own regression proof); golden fixtures with checksummed provenance; negative controls.
- **Weaknesses:** no shared `cfg`/`ledger` fixture (303 files re-declare `Config(root=tmp_path)`); the `slow` marker has no generative criterion (**STD-RESIDUAL-2**); the `check_scope` override table is not exhaustiveness-checked (**STD-RESIDUAL-1**).
- **Risks:** low. The CI-only rule means the suite's health is only ever observed on a PR — deliberate (host safety) and accepted.
- **Since audit:** the unmarked-integration-test gap **closed by a ratchet** (#666) — every `tests/integration/` test must now carry the marker.

### 6 · Versioning & release
- **Maturity:** **Adequate** (versioning: **Strong**; release: **Absent**) · **Trend:** ↑ improving · **Owner:** packaging / operator
- **Evidence (re-derive):** `src/fanops/__init__.py` (`_package_version`); `pyproject.toml [project].version`; `git tag`; any `CHANGELOG*`.
- **Strengths:** the version now has **one structural authority** — `__version__` is derived from installed metadata, so the two-literal drift class is *impossible*, not merely tested against.
- **Weaknesses:** **no release process at all** — no CHANGELOG, zero semver tags ever, no release automation, no declared policy (**STD-VER-02**, `BLOCK-RELEASE-POLICY`).
- **Risks:** low today (single-operator, continuously deployed). Rises the moment a second consumer or a rollback-to-a-version need appears.
- **Since audit:** **the audit's #1 High finding is discharged** — `pyproject 0.4.0` vs `__init__ 0.3.0` **fixed** by #662 with the correct structural fix (derivation, not a parity test).

### 7 · Code craft (naming, layout, boundaries)
- **Maturity:** **Adequate** · **Trend:** → stable (size: ↓ declining) · **Owner:** authors + review
- **Evidence (re-derive):** AST scan of `def`/`class` conformance; `wc -l src/fanops/**`; grep for sibling imports across `actions_*`.
- **Strengths:** naming is **100 % conformant** across 1889 functions / 159 classes / 16 enums — no validator needed or wanted; the studio facade tree has **zero** lateral edges; the split-into-a-prefixed-family convention ships byte-identical extractions.
- **Weaknesses:** no public-API declaration (`__all__` absent); god-modules unwatched — `cli.py` **grew 1448→1465 during the audit itself**, `studio/views.py` 1435.
- **Risks:** low individually; the size trend is the only *measurably worsening* metric in this scorecard.
- **Since audit:** no change except growth.

### 8 · Configuration & feature flags
- **Maturity:** **Weak** · **Trend:** → stable · **Owner:** `config`
- **Evidence (re-derive):** `grep -rn '_VALID_BACKENDS = ' src/fanops/`; `config.py` property/`os.getenv` counts; `test_config_doc_drift.py`; `ARCH-003`.
- **Strengths:** the default-ON/OFF word-sets are followed by ~30 booleans without exception; every env read is **mechanically declared** (`ARCH-003`, extended to `docs/CONFIG.md`); doc parity is tested.
- **Weaknesses:** **the one live standards violation in the repo** — `_VALID_BACKENDS` and `PosterBackend` are each hand-defined **twice** (`config.py`, `settings.py`) with no test tying them; `accounts.py` imports one copy while the runtime gate checks the other.
- **Risks:** a 4th backend added to one copy silently splits validation between the diagnostic path and the runtime gate. Low likelihood, quiet failure.
- **Action:** `SLICE-STD-BACKEND-PARITY` — **delete a copy** (C15.3), do not add a parity test.

### 9 · Observability
- **Maturity:** **Weak** · **Trend:** → stable · **Owner:** `log` / `errors`
- **Evidence (re-derive):** `log.py`; count `get_logger` vs `logging.getLogger` sites; grep `basicConfig|dictConfig|addHandler` in `src/fanops/` (expect **0**); `health_model.render_prometheus_metrics`; `digest.py`.
- **Strengths:** the surfaced channel is well-built (sanitized single-line JSON, `O_APPEND`, `0600`, dual-sink); one typed health owner + a Prometheus `/metrics` view; an append-only never-raising audit trail; two orthogonal daemon liveness verdicts (RC-6).
- **Weaknesses:** **two logging systems with no bridge** — 26 stdlib sites, **0 handlers configured**; only 5 of 164 surfaced calls set a level, so failures ride the `info` default.
- **Risks:** the wipe-guard blindness (§3). Level-based triage is currently not possible.

### 10 · Documentation & ADRs
- **Maturity:** **Strong** (governance docs) / **Adequate** (operational docs) · **Trend:** ↑↑ strongly improving · **Owner:** constitution maintainer + docs
- **Evidence (re-derive):** `docs/adr/` (ADR count + catalogue); `docs/ARCHITECTURE_GOVERNANCE.md` header + `ARCH-006`; provenance headers across the governance layer; `git ls-files docs/`.
- **Strengths:** one **truly generated** doc with a byte-compare gate; a `path:line`-citation norm; an explicit "when prose and code disagree, the code is right" precedence; a live ADR system (0100–0103 + a 99-decision catalogue); provenance headers on the governance layer.
- **Weaknesses:** anchor rot is systemic (INV-20: **10/10** nested-`CLAUDE.md` anchors stale; measured drift to **+129 lines**) — mitigated in *policy* (cite the symbol) but not yet mechanized (`CM-7`, designed); doc placement is ad hoc (`docs/runbooks/` holds 2 of ~10 runbooks); three docs still describe changed code; `.markdownlint.json` is wired to nothing.
- **Risks:** an editing agent trusting a rotted anchor. Mitigated by "trust the symbol."
- **Since audit:** the Constitution, Philosophy, Laws, ADR roadmap, and governance roadmaps **landed** (#675); the ADR system went from "dormant" (a stale audit claim) to demonstrably active.

### 11 · Security & secrets
- **Maturity:** **Strong** · **Trend:** → stable · **Owner:** secrets / ci-lane
- **Evidence (re-derive):** `secret_provider.set_secret` (read-back-or-raise); `scripts/scan-secrets.sh` (no bypass); control `CI-UNIT-SECRETSCAN`.
- **Strengths:** the **asymmetric** posture is genuinely well-reasoned — reads fail open, **writes fail closed with a verified round-trip** (so a caller never scrubs the plaintext fallback believing it stored); keys are write-only; the secret scan deliberately has **no** skip env, and CI honors no local bypass.
- **Weaknesses:** none material to the threat model.
- **Risks:** Studio localhost no-auth (**AR-2**) — a recorded, bounded decision; re-evaluate **only** if ever exposed beyond localhost.

### 12 · Dependency management
- **Maturity:** **Strong** · **Trend:** ↑ improving · **Owner:** ci-lane
- **Evidence (re-derive):** `requirements/ci-*.txt` headers + `--hash=` lines; `scripts/lock-deps.sh`; `scripts/check-locks.sh`; `.github/dependabot.yml`; workflow action pins.
- **Strengths:** hash-verified locks, `--require-hashes` installs, a **blocking** drift guard, narrowly-scoped Dependabot with in-file rationale, SHA-pinned actions.
- **Weaknesses:** `check-locks.sh` is a regex heuristic over the diff, not a resolver run; lock platform-faithfulness is trust-based (documented caveat, not re-verified per regen).
- **Risks:** low. `[asr]` unlocked is deliberate and nightly-only.
- **Since audit:** the last SHA-pin violation (`lane-guard.yml`) **fixed** (#663).

### 13 · Git & history
- **Maturity:** **Adequate** · **Trend:** ↑ improving · **Owner:** operator + ADR-0102
- **Evidence (re-derive):** `git log --merges | wc -l` vs total; conventional-shape ratio over the last N subjects; `required_linear_history` in live BP.
- **Strengths:** merge strategy now has a **written owner** (ADR-0102: squash-only + linear history); the derived-file half is already enforced (regenerate, never hand-merge); ~74 % of recent subjects are conventional-shaped by pure discipline.
- **Weaknesses:** `required_linear_history` is **Phase E, not yet live** — merge commits remain legal today; commit-message grammar is unenforced (**AR-4**, accepted: reviving a message gate = reviving the dormant land-gate).
- **Risks:** history noise until Phase E lands; low.
- **Since audit:** **the audit's "no declared merge strategy" finding is superseded by ADR-0102** — the decision now exists; only deployment remains.

---

## Summary

| Category | Maturity | Trend |
|---|---|---|
| Architecture governance | **Exemplary** | ↑ |
| Persistence & data integrity | **Exemplary** | ↑ |
| CI governance & merge control | **Strong** (partially deployed) | ↑↑ |
| Failure semantics | **Strong** | → |
| Testing | **Strong** | ↑ |
| Security & secrets | **Strong** | → |
| Dependency management | **Strong** | ↑ |
| Documentation & ADRs | **Strong** / Adequate | ↑↑ |
| Versioning & release | **Adequate** (release **Absent**) | ↑ |
| Code craft | **Adequate** | → (size ↓) |
| Git & history | **Adequate** | ↑ |
| Configuration & flags | **Weak** | → |
| Observability | **Weak** | → |

**Baseline reading.** The repository is **strongest exactly where it is mechanized** and weakest where a
convention lives only in prose — which is the Engineering Standards thesis, now measured per category.
**Two Weak rows** (configuration/flags, observability) hold the only live, unowned defects:
the duplicated backend value-set and the unsurfaced wipe-guard breadcrumb. Both have slices.

**Direction is strongly positive.** Between the audit base and this base — roughly one day — the version
drift was structurally fixed, the marker gap was ratcheted, the count rot was deleted, lane-guard was
hardened, the CI control plane was built and wired, and the constitutional layer landed. The one
**measurably worsening** metric is module size (`cli.py` 1448→1465).
