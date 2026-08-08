"""Negative controls — proof that the validators detect the drift they CLAIM to detect.

A validator nobody has tried to fool is a validator nobody should trust. Cycle 4 found a green CI
test that ASSERTED the data-loss outcome of the restore race and called it correct (`RC-5`). The
lesson generalizes: **for every invariant claimed to be protected by a check, read the check's
assertion, not its name.** These controls are how this system reads its own.

Method. Each control:
  1. builds an isolated fixture (a real copy of src/ + tests/ + the canonical artifacts),
  2. records the findings BEFORE injection,
  3. injects exactly one defect,
  4. asserts the expected rule fires with evidence that was NOT present before.

Step 2 is what makes this rigorous. The live tree already carries real findings (a stale contract
ratchet, two unassigned modules). A control that merely asserted "the rule fires" would pass on
that pre-existing noise and prove nothing. Demanding NEW evidence proves the validator
DISCRIMINATES — that it responds to the injected defect specifically.

Runnable locally: this uses no pytest. (Repo policy: the suite is CI-only — parallel local runs
take the machine down.) CI wraps these same functions.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import common, drift, generate, policy, registries, render
from .common import ARCH, REPO, SRC, dumps

_PATCHED = (common, generate, policy, registries, drift, render)


@dataclass
class Control:
    id: str
    defect: str
    expect_rule: str
    layer: str          # "architecture" | "implementation"


CONTROLS: list[Control] = [
    Control("NC-01", "undocumented dependency (a NEW compile-time import cycle)", "ARCH-004", "architecture"),
    Control("NC-02", "missing owner (a module in no subsystem)", "ARCH-001", "architecture"),
    Control("NC-03", "ghost module (declared in the KB, absent on disk)", "ARCH-002", "architecture"),
    Control("NC-04", "undocumented environment variable", "ARCH-003", "architecture"),
    Control("NC-05", "invalid invariant (a derived number restated in kb/, contradicting the code)", "ARCH-009", "architecture"),
    Control("NC-06", "missing fingerprint input (a lazy import HOISTED to module level)", "ARCH-007", "architecture"),
    Control("NC-07", "a NEW undeclared subprocess call site", "ARCH-008", "architecture"),
    Control("NC-08", "generated-file edit (derived/ hand-modified)", "ARCH-006", "architecture"),
    Control("NC-09", "unauthorized slice expansion (a slice owns a file that does not exist)", "IMPL-001", "implementation"),
    Control("NC-10", "illegal slice dependency / broken implementation DAG", "IMPL-003", "implementation"),
    Control("NC-11", "orphaned root cause (a root cause mapping to no slice)", "IMPL-004", "implementation"),
    Control("NC-12", "missing rollback plan", "IMPL-005", "implementation"),
    Control("NC-13", "missing verification (a slice absent from the verification matrix)", "IMPL-005", "implementation"),
    Control("NC-14", "invalid implementation contract (a slice with no root cause, not marked PROPOSED)", "IMPL-008", "implementation"),
    Control("NC-15", "missing merge gate (a stale ratchet budget the contract copied)", "IMPL-007", "implementation"),
    Control("NC-16", "a NEW unguarded door to a terminal Post state (GB-4)", "IMPL-009", "implementation"),
    Control("NC-17", "extra='forbid' on a ledger model (GB-3)", "IMPL-010", "implementation"),
    Control("NC-18", "UNKNOWN growth beyond the approved ceiling", "ARCH-005", "architecture"),
    Control("NC-19", "an unresolvable construct (a dynamic import) appears", "ARCH-010", "architecture"),
    Control("NC-20", "a slice boundary written as PROSE, not a predicate", "IMPL-002", "implementation"),
    Control("NC-21", "a required verification DISAPPEARS", "IMPL-006", "implementation"),
    Control("NC-22", "a canonical artifact is MISSING (the gate must FAIL, not pass vacuously)", "GOV-001", "architecture"),
    Control("NC-23", "a side-effect census restated in kb/side_effects.json", "ARCH-008", "architecture"),
    Control("NC-24", "a stale _CLI_PRINT_COUNT assignment in tools/arch/ — the engine's OWN rationale (G1 widened scope)", "IMPL-007", "implementation"),
    Control("NC-25", "a stale FANOPS_ var named in docs/CONFIG.md but read nowhere (G2 operator-doc rot)", "ARCH-003", "architecture"),
    Control("NC-26", "a phantom env var declared in kb/configuration.json but read nowhere", "ARCH-003", "architecture"),
]


@contextlib.contextmanager
def fixture():
    """A real, isolated copy of everything the checkers read."""
    root = Path(tempfile.mkdtemp(prefix="arch-nc-"))
    try:
        (root / "src").mkdir()
        shutil.copytree(SRC, root / "src" / "fanops")
        shutil.copytree(REPO / "tests", root / "tests",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures", "integration"))
        arch = root / ".reports" / "architecture"
        arch.mkdir(parents=True)
        for sub in ("kb", "contract", "governance"):
            if (ARCH / sub).exists():
                shutil.copytree(ARCH / sub, arch / sub,
                                ignore=shutil.ignore_patterns("prompts", ".DS_Store"))
        # tools/arch is copied so IMPL-007's WIDENED scan (which reads REPO/tools/arch, G1) has a real
        # target the patched REPO reaches — NC-24 injects a stale budget there. Without this the widened
        # scope would be inert in the fixture and its control could not fire.
        shutil.copytree(REPO / "tools" / "arch", root / "tools" / "arch",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # G2: docs/CONFIG.md is read by ARCH-003's operator-doc check; copy it so NC-25 can inject a stale
        # FANOPS_ mention and prove that half of ARCH-003 fires. Without it the doc-check would be inert in
        # the fixture (config_md.exists() False) and its control could not fire.
        if (REPO / "docs" / "CONFIG.md").exists():
            (root / "docs").mkdir(exist_ok=True)
            shutil.copy(REPO / "docs" / "CONFIG.md", root / "docs" / "CONFIG.md")
        saved = {m: {k: getattr(m, k) for k in
                     ("REPO", "SRC", "TESTS", "ARCH", "KB", "CONTRACT", "DERIVED", "GOVERNANCE")
                     if hasattr(m, k)} for m in _PATCHED}
        new = {"REPO": root, "SRC": root / "src" / "fanops", "TESTS": root / "tests",
               "ARCH": arch, "KB": arch / "kb", "CONTRACT": arch / "contract",
               "DERIVED": arch / "derived", "GOVERNANCE": arch / "governance"}
        for m in _PATCHED:
            for k, v in new.items():
                if hasattr(m, k):
                    setattr(m, k, v)
        try:
            yield root, new
        finally:
            for m, kv in saved.items():
                for k, v in kv.items():
                    setattr(m, k, v)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _sig(findings: list[policy.Finding]) -> set[tuple[str, str]]:
    return {(f.rule, e) for f in findings for e in f.evidence} | {(f.rule, f.detail) for f in findings}


def _run(paths: dict) -> list[policy.Finding]:
    generate.generate(src=paths["SRC"], out=paths["DERIVED"])
    return policy.check(paths["DERIVED"])


_BASELINE: set[tuple[str, str]] | None = None


def _clean_baseline() -> set[tuple[str, str]]:
    """Findings a PRISTINE fixture produces — the same value for every control, so computed ONCE.

    Measured: a control is 0.36s fixture + 1.89s pristine pass + 1.90s injected pass. The pristine
    pass was recomputed 24 times for an identical answer — 44% of the suite's 99s, spent deriving
    a constant. The injected pass is irreducible; it is the actual test.

    Sharing is sound because no finding names the temp tree it was built in — every evidence string
    is repo-relative (`fanops.config:1112 computed_env_key`). `_assert_fixture_agnostic` re-proves
    that on the real findings rather than trusting it, because the failure would be silent and
    total: a baseline carrying fixture paths matches nothing, every control's own evidence reads as
    NEW, and all 24 pass while detecting nothing.

    Computed OUTSIDE any `fixture()` context — fixture() patches module path constants and restores
    them on exit, so entering it from inside another restores to already-patched values."""
    global _BASELINE
    if _BASELINE is None:
        with fixture() as (root, p):
            findings = _run(p)
            _assert_fixture_agnostic(findings, root)
            _BASELINE = _sig(findings)
    return _BASELINE


def _assert_fixture_agnostic(findings: list[policy.Finding], root: Path) -> None:
    leaked = [e for f in findings for e in (*f.evidence, f.detail) if str(root) in e]
    if leaked:
        raise AssertionError(
            f"{len(leaked)} finding(s) name the fixture root, so one control's baseline cannot be "
            f"compared against another's tree: {leaked[0][:120]}")


# ── the injections ──────────────────────────────────────────────────────────────────────────
def _inject(cid: str, root: Path, p: dict) -> None:
    src, kb, con, gov = p["SRC"], p["KB"], p["CONTRACT"], p["GOVERNANCE"]

    def patch(path: Path, fn) -> None:
        d = json.loads(path.read_text())
        fn(d)
        path.write_text(dumps(d))

    if cid == "NC-01":   # a NEW compile-time import cycle: ids <-> timeutil
        (src / "ids.py").write_text("from fanops import timeutil  # INJECTED\n"
                                    + (src / "ids.py").read_text())
        (src / "timeutil.py").write_text("from fanops import ids  # INJECTED\n"
                                         + (src / "timeutil.py").read_text())

    elif cid == "NC-02":  # a module belonging to no subsystem
        (src / "nc_orphan_module.py").write_text('"""injected: belongs to no subsystem."""\n')

    elif cid == "NC-03":  # a ghost: declared in the KB, absent on disk
        patch(kb / "subsystems.json",
              lambda d: d["subsystems"]["S01_foundation"]["modules"].append("nc_ghost_module"))

    elif cid == "NC-04":  # an env var read but never declared
        # The realistic shape — a plain `import os` + `os.getenv("KEY")`. (The first version of
        # this control injected `__import__("os").getenv(...)`, which no real code writes, and it
        # "failed" for that reason alone. A control that tests an unrealistic shape proves nothing
        # about the rule; it only tests the control.)
        (src / "log.py").write_text((src / "log.py").read_text()
                                    + '\nimport os as _nc_os_real\n'
                                    + 'import os\n_NC = os.getenv("FANOPS_NC_UNDECLARED")\n')

    elif cid == "NC-05":  # a declared number that contradicts the code
        patch(kb / "dependencies.json", lambda d: d["totals"].update({"compile_edges_G1": 99999}))

    elif cid == "NC-06":  # hoist a pinned must-stay-lazy import to module level
        base = json.loads((gov / "baselines.json").read_text())
        s, t = base["must_stay_lazy"][0]
        f = src / (s.split("fanops.", 1)[1].replace(".", "/") + ".py")
        f.write_text(f"import {t}  # INJECTED HOIST\n" + f.read_text())

    elif cid == "NC-07":  # a NEW undeclared subprocess call site (load-bearing ARCH-008 subject)
        # Inject into a module with ZERO subprocess sites today so the census grows AND the module
        # is absent from the allowlist. ARCH-008 must name the module and the ceiling.
        # Shape matches real call sites (`import subprocess` + `subprocess.run`) — an alias would
        # be invisible to the extractor (head must be the literal name `subprocess`).
        # (NC-23 covers the kb restatement half — this must NOT duplicate NC-05/NC-23.)
        (src / "log.py").write_text(
            (src / "log.py").read_text()
            + "\nimport subprocess\n"
            + "def _nc_undeclared_side_effect():\n"
            + "    subprocess.run(['true'], check=False)\n")

    elif cid == "NC-23":  # a side-effect census restated in kb/ (presence, not agreement)
        # The #875 property kept under ARCH-008: an AST-computable number in
        # counts_AST_verified still fails the build. Distinct from NC-07 (new site).
        patch(kb / "side_effects.json",
              lambda d: d["counts_AST_verified"].update({"subprocess_call_sites": 99999}))

    elif cid == "NC-08":  # hand-edit a GENERATED artifact
        generate.generate(src=src, out=p["DERIVED"])
        f = p["DERIVED"] / "modules.json"
        d = json.loads(f.read_text())
        d["totals"]["modules"] = 1
        f.write_text(dumps(d))

    elif cid == "NC-09":  # a slice owns a file that does not exist
        patch(con / "file_ownership.json",
              lambda d: d["ownership"].update({"src/fanops/nc_does_not_exist.py": {"owner": "S01"}}))

    elif cid == "NC-10":  # a back edge -> the implementation DAG gains a cycle
        patch(con / "implementation_contract.json",
              lambda d: d["implementation_dag"]["ordering_edges"].append(
                  {"from": "S04", "to": "S03", "why": "INJECTED back edge"}))

    elif cid == "NC-11":  # a root cause mapping to no slice
        def orphan(d):
            for row in d["root_cause_to_completion"]:
                if row["root_cause"] == "RC-9":
                    row.pop("slice", None)
                    row.pop("slices", None)
        patch(con / "traceability.json", orphan)

    elif cid == "NC-12":  # a slice with no rollback class
        patch(con / "rollback_matrix.json", lambda d: d["slices"].pop("S03", None))

    elif cid == "NC-13":  # a slice absent from the verification matrix
        patch(con / "verification_matrix.json", lambda d: d["slices"].pop("S03", None))

    elif cid == "NC-14":  # a slice tracing to no approved root cause
        def untrace(d):
            for s in d["slices"]:
                if s["id"] == "S08":
                    s["root_causes"] = []
                    s.pop("status", None)
        patch(con / "implementation_contract.json", untrace)

    elif cid == "NC-15":  # the contract's copy of a ratchet budget goes stale
        patch(con / "implementation_contract.json",
              lambda d: d["GLOBAL_BOUNDARIES"]["GB-6_ast_ratchet_budgets"]["print_ratchet"].update(
                  {"mechanism_B": "`_CLI_PRINT_COUNT = 4242`, asserted with EXACT EQUALITY"}))

    elif cid == "NC-16":  # a FIFTH, unguarded door to a terminal Post state
        (src / "text.py").write_text((src / "text.py").read_text()
                                     + "\nfrom fanops.models import PostState\n"
                                       "def _nc_door(p):\n    p.state = PostState.published\n")

    elif cid == "NC-17":  # extra="forbid" on a ledger model
        (src / "models.py").write_text((src / "models.py").read_text()
                                       + '\nclass _NCModel(BaseModel):\n'
                                         '    model_config = ConfigDict(extra="forbid")\n')

    elif cid == "NC-18":  # UNKNOWNs grow past the approved ceiling
        def grow(d):
            d["unknowns"].append({
                "id": "UNK-NC-INJECTED", "question": "injected", "subsystem": "S01",
                "evidence": "injected", "owner": "nc", "risk": "LOW",
                "next_investigation": "n/a", "status": "open", "review_date": "2099-01-01"})
        patch(gov / "unknowns.json", grow)

    elif cid == "NC-19":  # a construct the extractor cannot statically resolve
        (src / "text.py").write_text((src / "text.py").read_text()
                                     + '\ndef _nc_dyn(name):\n'
                                       '    import importlib\n'
                                       '    return importlib.import_module(name)\n')

    elif cid == "NC-20":  # a slice boundary written as PROSE rather than a predicate
        def prose(d):
            part = d["ownership"]["src/fanops/studio/actions.py"]["partition"]["S06"]
            part["permitted_functions"] = ["whatever seems right around the revert area"]
        patch(con / "file_ownership.json", prose)

    elif cid == "NC-22":
        # Delete a canonical artifact. THE GATE MUST GO RED. This is the control that proves the
        # system cannot pass vacuously — the exact failure that `.reports/` being in .gitignore
        # would have produced in CI: no inputs, every check skipped, GREEN.
        (con / "file_ownership.json").unlink()

    elif cid == "NC-21":  # a required verification DISAPPEARS
        # Baseline a verification as present, then make it absent. (The real baseline is EMPTY
        # today — no slice has landed — so the control must arm the rule itself to test it. A rule
        # that cannot be tested because it protects nothing yet is still a rule that must WORK the
        # day it starts protecting something.)
        base = json.loads((gov / "baselines.json").read_text())
        base["required_verifications_present"] = ["test_nc_vanished_invariant"]
        (gov / "baselines.json").write_text(dumps(base))

    elif cid == "NC-24":
        # G1: a stale `_CLI_PRINT_COUNT = <n>` assignment in tools/arch/ — the engine's OWN rationale
        # surface. Before the widened scan, IMPL-007 read only .reports/architecture/, so it COULD NOT
        # SEE ITSELF: a wrong budget in the very files that do the checking went unwatched. Inject a
        # fresh tools/arch file carrying a wrong assignment; the widened scan must fire IMPL-007 on it.
        # (selftest.py is EXCLUDED from the scan — it injects wrong assignments by design — so a NEW
        # file is used, proving the widened .py scan works, not the exclusion.)
        (root / "tools" / "arch" / "nc_stale_budget.py").write_text(
            "# INJECTED (NC-24): a stale copy of the cli.py print budget.\n"
            "_CLI_PRINT_COUNT = 999  # wrong on purpose\n", encoding="utf-8")

    elif cid == "NC-25":
        # G2: the operator doc (docs/CONFIG.md) names a FANOPS_ var the code does not read — the exact rot
        # G2 catches (a switch whose reader was deleted, or a doc naming a var that never existed). APPEND a
        # stale mention (like NC-24, an append cannot fail to change the bytes) and the doc-half of
        # ARCH-003 must fire on it.
        md = root / "docs" / "CONFIG.md"
        md.write_text(md.read_text()
                      + "\n| `FANOPS_NC_STALE_DOC` | off | INJECTED (NC-25): named here, read nowhere | .env |\n")

    elif cid == "NC-26":
        # The REVERSE direction of the kb half: a name declared in kb/configuration.json that nothing reads.
        # This is the shape FANOPS_HASHTAG_TRENDS had for a year while ARCH-003 checked only derived-minus-
        # declared and reported green. NC-04 (the forward direction) cannot fail on this defect, so without
        # this control the new half would be decorative. `env_vars` is a sorted array of names.
        patch(kb / "configuration.json",
              lambda d: d["env_vars"].append("FANOPS_NC_PHANTOM_DECLARED"))

    else:
        raise AssertionError(f"no injection defined for {cid}")


# ── the harness ─────────────────────────────────────────────────────────────────────────────
def detect(c: Control) -> tuple[bool, str]:
    """Run ONE control end to end: fixture → baseline → inject → did the named rule fire?

    *** THIS IS THE ONLY IMPLEMENTATION. ***

    `run()` (the CLI) and `tests/test_arch_governance.py` (pytest) BOTH call it. They used to each
    have their own copy of this logic, with their own `if c.id == "NC-08"` special case — and the
    moment a new control was added (the generated-doc one, since removed 2026-07), only one copy
    learned about it: the CLI reported all-green while pytest failed that control, on the same
    commit. Two implementations of "does this control detect?" will always drift, and the one
    that drifts is the one nobody watches.
    """
    sig_before = _clean_baseline()      # once per process, not once per control
    with fixture() as (root, p):
        _inject(c.id, root, p)

        # NC-08 asserts on GENERATED-ARTIFACT INTEGRITY, which is a byte-comparison, not a
        # policy Finding — the check that makes every other one trustworthy. It covers derived/
        # (the only generated surface since the governance doc's deletion, 2026-07).
        if c.id == "NC-08":
            stale, want = drift.stale_artifacts(p["DERIVED"]), "modules.json"
            hit = [d for d in stale if d.artifact == want]
            if not hit:
                return False, f"NOT DETECTED — a hand-edited {want} went unnoticed"
            ev = (hit[0].evidence or ["byte-compare failed"])[0]
            return True, f"{len(stale)} stale artifact(s) detected: {ev[:60]}"

        new = [(f.rule, e) for f in _run(p) for e in f.evidence if (f.rule, e) not in sig_before]
        hit_ev = [e for r, e in new if r == c.expect_rule]
        if not hit_ev:
            return False, f"NOT DETECTED — {c.expect_rule} produced no new evidence"
        return True, f"{c.expect_rule} fired with NEW evidence: {hit_ev[0][:76]}"


def run(verbose: bool = True) -> int:
    results: list[tuple[Control, bool, str]] = []

    for c in CONTROLS:
        try:
            ok, detail = detect(c)
            results.append((c, ok, detail))
        except Exception as exc:  # a control that cannot run is a control that proves nothing
            results.append((c, False, f"CONTROL ERRORED: {type(exc).__name__}: {exc}"))

    passed = sum(1 for _, ok, _ in results if ok)
    if verbose:
        print("negative controls — does each validator detect the defect it claims to?\n")
        w = max(len(c.defect) for c in CONTROLS)
        for c, ok, detail in results:
            mark = "\x1b[32mDETECTED\x1b[0m" if ok else "\x1b[31mMISSED  \x1b[0m"
            print(f"  {c.id}  {mark}  {c.defect:<{w}}  -> {c.expect_rule}")
            print(f"          {detail}")
        print()
        print(f"  {passed}/{len(results)} injected defects detected.")
        if passed != len(results):
            print("\n  A MISSED control means the rule it names is DECORATIVE: it is claimed in the "
                  "\n  policy set but does not actually fire. That is worse than having no rule, "
                  "\n  because it manufactures confidence.")
    return 0 if passed == len(results) else 1
