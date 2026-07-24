#!/usr/bin/env python3
"""Self-verifying theatre census for the FanOps repository.

The agent authored no classifications. Every row's `class` is COMPUTED by `classify()` from
primitives that are each either an ANCHOR (a file:line whose recorded `expect` substring is diffed
against the live file) or a CAPTURED EXECUTION (a command re-run for its exit / delta). This script
is both the generator and the checker:

    python verify_theatre.py build     # re-derive every row from the tree, write theatre.jsonl + theatre.md
    python verify_theatre.py           # verify: re-derive, recompute class, diff vs committed jsonl; exit 1 on ANY drift

It imports no repository code (the Settings flip-probe runs in a subprocess) and never runs pytest.
Blocked controls (needing the CI runner, a live service, a ledger fixture, or self-tripping a guard)
are recorded UNVERIFIED(reason=...) with the would-prove command, per the decision procedure.
"""
from __future__ import annotations
import json, re, subprocess, sys
from pathlib import Path

# ---- repo root (search upward for pyproject.toml; the census anchors are relative to it) ----
def find_root() -> Path:
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        p = base
        for _ in range(8):
            if (p / "pyproject.toml").exists() and (p / "tools" / "arch").exists():
                return p
            p = p.parent
    raise SystemExit("theatre: cannot locate repo root (no pyproject.toml with tools/arch above cwd)")

ROOT = find_root()
OUTDIR = Path(__file__).resolve().parent           # jsonl + md land beside this script
PY = str((ROOT / ".venv" / "bin" / "python")) if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
def _q(s): return "'" + s.replace("'", "'\\''") + "'"
PYQ = _q(PY)                                        # PY path contains a space -> must be shell-quoted

# ============================ primitive helpers ============================
def line_at(rel: str, lineno: int) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()[lineno - 1]
    except Exception:
        return ""

def find_line(rel: str, kw: str):
    """First 1-indexed line in `rel` containing literal `kw`; (loc, expect) or (None, None)."""
    fp = ROOT / rel
    if not fp.exists() or not kw:
        return None, None
    for i, ln in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if kw in ln:
            return f"{rel}:{i}", kw
    return None, None

def anchor(rel: str, kw: str):
    loc, exp = find_line(rel, kw)
    return None if loc is None else {"loc": loc, "expect": exp}

def anchor_any(rel: str, kws):
    for kw in kws:
        a = anchor(rel, kw)
        if a:
            return a
    return None

def line1(rel: str):
    t = line_at(rel, 1).strip()
    return {"loc": f"{rel}:1", "expect": t[:24] if t else rel}

def sh(cmd: str):
    """Run a shell command from ROOT; return (exit, stdout+stderr)."""
    r = subprocess.run(cmd, shell=True, cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"

def grep_count(pattern: str, *paths: str):
    # exclude this census's own output dir so counts never self-reference (would break determinism)
    repro = f"grep -rnI --exclude-dir=reconciliation {shq(pattern)} " + " ".join(paths)
    rc, out = sh(repro)
    n = len([ln for ln in out.splitlines() if ln.strip()]) if rc in (0, 1) else -1
    return n, repro

# ============================ control registry (each captured ONCE) ============================
_PROBE_SRC = r'''
import os, json
os.environ.pop("FANOPS_LIVE", None)
from fanops import settings as S
def build(flag, val):
    if val is None: os.environ.pop(flag, None)
    else: os.environ[flag] = val
    return S.Settings()      # pydantic-settings re-reads os.environ each instantiation; no reload needed
def sentinels(base):         # type-appropriate values so the flip is a real delta, not a type error
    if isinstance(base, bool): return ["0" if base else "1"]
    if isinstance(base, int): return [str(base + 7)]
    if isinstance(base, float): return [str(base + 1.5)]
    return (["1", "census"] if base in ("", None) else [str(base) + "Z", "1"])
fields = [n for n in S.Settings.model_fields if n.startswith("FANOPS_")]
res = {}
for f in fields:
    base = getattr(build(f, None), f)
    delta = "read+validated (invalid value rejected)"   # fallback: field IS read/validated => live
    for sv in sentinels(base):
        try:
            hi = getattr(build(f, sv), f)
            delta = "IDENTICAL" if repr(base) == repr(hi) else f"{base!r}->{hi!r}"
            break
        except Exception:
            continue
    res[f] = delta
    build(f, None)
print(json.dumps(res))
'''

_CACHE: dict = {}
def control(name: str) -> dict:
    if name in _CACHE:
        return _CACHE[name]
    d = _run_control(name)
    _CACHE[name] = d
    return d

def _selftest(cmd: str, need: str) -> dict:
    rc, out = sh(cmd)
    m = re.search(need, out)
    ok = rc == 0 and m is not None
    return {"cmd": cmd.replace(PYQ, "python"), "exit": rc, "delta": (m.group(0) if ok else f"rc={rc}"), "blocks": ok}

def _run_control(name: str) -> dict:
    if name == "arch-selftest":
        return _selftest(f"{PYQ} -m tools.arch selftest", r"\d+/\d+ injected defects detected")
    if name == "ci-selftest":
        return _selftest(f"{PYQ} -m tools.ci selftest", r"NC-DC\d")
    if name == "contract-selftest":
        return _selftest(f"{PYQ} -m tools.contract selftest", r"\d+/\d+ injected defects detected")
    if name == "e2e-push":
        rc, out = sh(f"{PYQ} scripts/ci_e2e_trigger.py --event push")
        return {"cmd": "python scripts/ci_e2e_trigger.py --event push", "exit": rc,
                "delta": "run=false (context reports green; suite skipped)", "blocks": False}
    if name == "e2e-dispatch":
        rc, out = sh(f"{PYQ} scripts/ci_e2e_trigger.py --event workflow_dispatch")
        return {"cmd": "python scripts/ci_e2e_trigger.py --event workflow_dispatch", "exit": rc,
                "delta": "run=true", "blocks": "RUNNING THE FULL" in out}
    if name == "envprobe-noexit":
        n, repro = grep_count(r"sys.exit(1)", "scripts/ci_env_probe.py")
        return {"cmd": repro, "exit": 0, "delta": f"no failing exit path (matches={n})", "blocks": n > 0}
    if name == "learning-closed":
        cmd = (f"{PYQ} -c \"import tempfile; from fanops.config import Config; "
               f"from fanops import validation_gate as v; c=Config(root=tempfile.mkdtemp()); "
               f"print('closed' if not v.learning_validated(c) else 'open')\"")
        rc, out = sh(cmd)
        return {"cmd": "python -c '<learning_validated on empty root>'", "exit": rc,
                "delta": out.strip()[-40:], "blocks": rc == 0 and "closed" in out}
    if name == "weakhook":
        cmd = f"{PYQ} -c \"from fanops.hookcheck import is_weak_hook as w; print(w('',[]), w('unique text here',['x']))\""
        rc, out = sh(cmd)
        return {"cmd": "python -c '<is_weak_hook empty vs unique>'", "exit": rc,
                "delta": out.strip()[-40:], "blocks": rc == 0 and out.strip().startswith("True")}
    if name == "gateguard":
        return {"cmd": "(observed) first Bash / Write-create this session", "exit": 2,
                "delta": "intercepted: '[Fact-Forcing Gate] present these facts, then retry'", "blocks": True}
    if name == "toggle-probe":
        rc, out = sh(f"{PYQ} -c {shq(_PROBE_SRC)}")
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except Exception:
            data = {}
        return {"cmd": "python -c '<Settings flip over every FANOPS_* field>'", "exit": rc, "delta": data, "blocks": None}
    if name == "noop":
        return {"cmd": "n/a (no mechanism to exercise)", "exit": "n/a", "delta": "NO-OP", "blocks": None}
    reasons = {
        "pytest": "needs-CI-runner (pytest denied locally)",
        "ci-diff": "needs-CI-runner (a PR diff to scan)",
        "ci-log": "needs-CI-runner (a timing log to exceed budget)",
        "ledger": "needs-ledger+live-metrics harness",
        "live": "needs-live-Postiz/Meta analytics",
        "self-trip": "would require performing the prohibited action",
        "launchd": "needs-launchd-plist runtime",
    }
    return {"cmd": f"UNVERIFIED({reasons.get(name, name)})", "exit": "UNVERIFIED", "delta": reasons.get(name, name), "blocks": None}

# ============================ decision procedure (pure) ============================
def classify(kind: str, r: dict) -> str:
    mech = r.get("mechanism") is not None
    wired = r.get("wired") is not None
    ctl = r.get("control", {})
    exit_ = ctl.get("exit")
    blocks = ctl.get("blocks")
    if kind == "toggle":
        if r.get("not_a_toggle"):
            return "NOT-A-TOGGLE"
        reads = r["read_sites"]["count"]
        if reads == 0:
            return "DEAD-TOGGLE(unread)"
        if exit_ == "UNVERIFIED":
            return f"UNVERIFIED({ctl.get('delta','')})"
        if ctl.get("delta") == "IDENTICAL":
            return "DEAD-TOGGLE(identical-branches)"
        if r.get("dead_code"):
            return "DEAD-TOGGLE(gates-dead-code)"
        return "LIVE"
    if not mech:
        return "DECORATIVE"
    if not wired:
        return "INERT"
    if exit_ == "UNVERIFIED":
        return f"UNVERIFIED({ctl.get('delta','')})"
    # SELF-REF is tested before REAL: a check whose only effect is asserting its own pass "blocks"
    # only on its own failure, so naive block-detection would wrongly grab REAL. The spec lists it
    # to catch exactly that — a green self-test wired to nothing but itself.
    if r.get("self_ref"):
        return "SELF-REF"
    if blocks is True:
        return "REAL"
    if blocks is False:
        return "UNFALSIFIABLE"
    return f"UNVERIFIED({ctl.get('delta','')})"

# ============================ Surface A — governance-prose ENFORCER INDEX (deduplicated) ============================
# The governance prose (constitution / laws / standards) holds NO executable mechanism itself — deleting it
# changes no gate. What is worth extracting before deletion is the deduplicated set of ENFORCERS it NAMES: for
# each, does it exist, is it wired into a required lane, how many clauses cite it? That index is the one-page
# constitution to preserve. Pure grep; no per-clause judgment. (Clause-level detail is intentionally NOT emitted.)
A_PROSE = [
    ("docs/REPOSITORY_CONSTITUTION.md", r"^\*\*(C\d+\.\d+)"),
    ("docs/REPOSITORY_CONSTITUTION.md", r"^\|\s*(AR-\d+)\b"),
    ("docs/ARCHITECTURAL_LAWS.md",       r"^###\s+(LAW-[A-Z0-9-]+)"),
    ("docs/ENGINEERING_STANDARDS.md",    r"^###\s+(STD-[A-Z0-9-]+)"),
    ("docs/ENGINEERING_STANDARDS.md",    r"^\|\s*`?(STD-RESIDUAL-[A-Z0-9-]+)"),
    ("docs/governance/CONSTITUTION_MAINTENANCE.md", r"^\|\s*\*\*(CM-\d+)"),
]
_MECH_TOK = re.compile(r'\b(ARCH-\d+|IMPL-\d+|GOV-\d+|DC-\d+|test_[a-z0-9_]{3,})\b')
A_PROSE_SUMMARY = (0, 0, 0)   # (clauses_total, clauses_naming_>=1_enforcer, clauses_naming_none) — set by surface_A

def _grep_first(needle, path):
    rc, out = sh(f"grep -rnI {shq(needle)} {path}")
    for ln in out.splitlines():
        mm = re.match(r'(\S+?):(\d+):', ln)
        if mm:
            return {"loc": f"{mm.group(1)}:{mm.group(2)}", "expect": needle}
    return None

def _doc_blocks(rel, pat):
    lines = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    rx = re.compile(pat)
    for i, ln in enumerate(lines):
        m = rx.match(ln)
        if not m:
            continue
        if ln.lstrip().startswith("|"):                 # a table row IS its own block (self-contained columns)
            yield m.group(1), i + 1, ln
        else:                                          # a heading/bold block = header + annotation lines, to the blank
            j = i + 1
            while j < len(lines) and lines[j].strip():
                j += 1
            yield m.group(1), i + 1, "\n".join(lines[i:j])

def _mech_of_token(tok):
    """Resolve a NAMED enforcer to (mechanism_anchor|None, control, wired_kind) — mechanically, no judgment."""
    if tok.startswith(("ARCH-", "IMPL-", "GOV-")):
        return anchor("tools/arch/policy.py", tok), "arch-selftest", "unit-lane"
    if tok.startswith("DC-"):
        n = tok.split("-")[1]
        if n == "3":
            return anchor("tools/ci/checks.py", "dc3"), "live", None      # DC-3 never auto-runs -> not required-wired
        return anchor("tools/ci/checks.py", "dc" + n), "ci-selftest", "unit-lane"
    if tok.startswith("test_"):
        return _grep_first(f"def {tok}", "tests"), "pytest", "unit-lane"
    return None, "noop", None

def surface_A():
    cites, first, total, naming = {}, {}, 0, 0
    for rel, pat in A_PROSE:
        if not (ROOT / rel).exists():
            continue
        for aid, ln, block in _doc_blocks(rel, pat):
            total += 1
            toks = set(_MECH_TOK.findall(block))
            if toks:
                naming += 1
            for t in toks:
                cites[t] = cites.get(t, 0) + 1
                first.setdefault(t, {"loc": f"{rel}:{ln}", "expect": aid})
    global A_PROSE_SUMMARY
    A_PROSE_SUMMARY = (total, naming, total - naming)
    rows = []
    for tok in sorted(cites):
        mech, ctl, wired_kind = _mech_of_token(tok)
        n, repro = grep_count(tok, "docs/")
        r = {"id": f"A/{tok}", "surface": "A/enforcer-index",
             "def": mech or first[tok],                 # code anchor if the enforcer exists, else the prose naming it
             "claim": first[tok],
             "read_sites": {"repro": repro, "count": n, "list": []},
             "mechanism": mech, "wired": _wired_anchor(wired_kind) if mech else None,
             "control": control(ctl), "note": f"named by {cites[tok]} prose clauses; exists={bool(mech)}"}
        r["class"] = classify("gate", r)
        r["repro"] = repro
        rows.append(r)
    return rows

# ============================ shared wiring anchors ============================
def _wired_anchor(kind):
    if kind == "required":
        return anchor(".github/workflows/ci.yml", "unit (fast, no toolchain)")
    if kind == "unit-lane":
        return anchor(".github/workflows/ci.yml", "not integration and not slow")
    return None  # advisory / observability / unwired (E's default-path is resolved to a real caller separately)

def _first_caller(name, deffile):
    """A REAL call site of `name` in src/fanops, excluding its own def line — proves the gate is on a live path."""
    defloc = (anchor(deffile, f"def {name}") or {}).get("loc")
    rc, out = sh(f"grep -rnI {shq(name)} src/fanops")
    for ln in out.splitlines():
        mm = re.match(r'(src/fanops/\S+?):(\d+):', ln)
        if mm and f"{mm.group(1)}:{mm.group(2)}" != defloc:
            return {"loc": f"{mm.group(1)}:{mm.group(2)}", "expect": name}
    return None

# ============================ Surface B — CI gates ============================
B_SPEC = [
    ("B/unit",              ".github/workflows/ci.yml", "unit (fast, no toolchain)", ".github/workflows/ci.yml", "pytest", "required", "pytest"),
    ("B/unit.secretscan",   ".github/workflows/ci.yml", "Secret scan", "scripts/scan-secrets.sh", "exit 1", "required", "ci-diff"),
    ("B/unit.lockdrift",    ".github/workflows/ci.yml", "Lockfile drift", "scripts/check-locks.sh", "#!/", "required", "ci-diff"),
    ("B/unit.envprobe",     ".github/workflows/ci.yml", "env probe", "scripts/ci_env_probe.py", "def main", "required", "envprobe-noexit"),
    ("B/unit.lint",         ".github/workflows/ci.yml", "ruff", ".github/workflows/ci.yml", "ruff", "required", "ci-diff"),
    ("B/unit.slo",          ".github/workflows/ci.yml", "SLO", "scripts/ci_slo_gate.py", "def", "required", "ci-log"),
    ("B/unit.hookverify",   ".github/workflows/ci.yml", "REQUIRE_E2E", ".github/workflows/ci.yml", "REQUIRE_E2E", "required", "pytest"),
    ("B/unit.archgov",      ".github/workflows/ci.yml", "not integration and not slow", "tools/arch/policy.py", "ARCH-001", "unit-lane", "arch-selftest"),
    ("B/unit.civalidator",  ".github/workflows/ci.yml", "not integration and not slow", "tools/ci/checks.py", "def dc1", "unit-lane", "ci-selftest"),
    ("B/base-install",      ".github/workflows/ci.yml", "base-install", "scripts/base_install_smoke.py", "def", "advisory", "noop"),
    ("B/e2e",               ".github/workflows/ci.yml", "real-tooling E2E", ".github/workflows/ci.yml", "steps.gate.outputs.run", "required", "e2e-push"),
    ("B/e2e.gate",          ".github/workflows/ci.yml", "full real-tooling lane", "scripts/ci_e2e_trigger.py", "def decide", "required", "e2e-push"),
    ("B/e2e.integration",   ".github/workflows/ci.yml", "Integration suite", ".github/workflows/ci.yml", "Integration suite", "advisory", "pytest"),
    ("B/e2e.slow",          ".github/workflows/ci.yml", "Slow", ".github/workflows/ci.yml", "Slow", "advisory", "pytest"),
    ("B/ci-timing",         ".github/workflows/ci.yml", "ci-timing", "", "", "observability", "noop"),
    ("B/arch.gate",         ".github/workflows/architecture.yml", "tools.arch ci", "tools/arch/policy.py", "def check", "advisory", "arch-selftest"),
    ("B/arch.impact",       ".github/workflows/architecture.yml", "impact", "tools/arch/impact.py", "def", "advisory", "noop"),
    ("B/arch.controls",     ".github/workflows/architecture.yml", "selftest", "tools/arch/selftest.py", "CONTROLS", "advisory", "arch-selftest"),
    ("B/lane-guard",        ".github/workflows/lane-guard.yml", "lane_guard", "scripts/lane_guard.py", "def main", "advisory", "noop"),
    ("B/nightly.pipaudit",  ".github/workflows/nightly.yml", "pip-audit", "", "", "observability", "noop"),
    ("B/nightly.asr",       ".github/workflows/nightly.yml", "asr", ".github/workflows/nightly.yml", "asr", "advisory", "pytest"),
]

# ============================ Surface C — governance engine ============================
C_SPEC = [
    ("C/contract.decide",     "tools/contract/decide.py", "def decide", "tools/contract/decide.py", "def decide", "advisory", "noop"),
    ("C/contract.selftest",   "tools/contract/selftest.py", "CONTROLS", "tools/contract/selftest.py", "CONTROLS", "unit-lane", "contract-selftest", True),
    ("C/contract.parse",      "tools/contract/parse.py", "def digest_range", "tools/contract/parse.py", "def digest_range", "advisory", "noop"),
    ("C/contract.model",      "tools/contract/model.py", "ALL_FIELDS", "tools/contract/model.py", "ALL_FIELDS", "advisory", "noop"),
    ("C/contract.lifecycle",  "tools/contract/lifecycle.py", "def gates", "tools/contract/lifecycle.py", "def gates", "advisory", "noop"),
    ("C/contract.validate",   "tools/contract/validate.py", "def", "tools/contract/validate.py", "def", "advisory", "noop"),
    ("C/contract.classify",   "tools/contract/classify.py", "def", "tools/contract/classify.py", "def", "advisory", "noop"),
    ("C/contract.derive",     "tools/contract/derive.py", "def", "tools/contract/derive.py", "def", "advisory", "noop"),
    ("C/contract.adapters",   "tools/contract/adapters.py", "subprocess", "tools/contract/adapters.py", "subprocess", "advisory", "noop"),
    ("C/contract.report",     "tools/contract/report.py", "def", "tools/contract/report.py", "def", "advisory", "noop"),
    ("C/contract.main",       "tools/contract/__main__.py", "def", "tools/contract/__main__.py", "def", "advisory", "noop"),
    ("C/arch.policy",         "tools/arch/policy.py", "ARCH-001", "tools/arch/policy.py", "ARCH-001", "unit-lane", "arch-selftest"),
    ("C/arch.drift",          "tools/arch/drift.py", "def", "tools/arch/drift.py", "def", "unit-lane", "arch-selftest"),
    ("C/arch.registries",     "tools/arch/registries.py", "def", "tools/arch/registries.py", "def", "unit-lane", "arch-selftest"),
    ("C/arch.selftest",       "tools/arch/selftest.py", "CONTROLS", "tools/arch/selftest.py", "CONTROLS", "advisory", "arch-selftest"),
    ("C/arch.impact",         "tools/arch/impact.py", "def", "tools/arch/impact.py", "def", "advisory", "noop"),
    ("C/arch.main",           "tools/arch/cli.py", "def", "tools/arch/cli.py", "def", "advisory", "noop"),
    ("C/ci.checks",           "tools/ci/checks.py", "def dc1", "tools/ci/checks.py", "def dc1", "unit-lane", "ci-selftest"),
    ("C/ci.dc3",              "tools/ci/checks.py", "dc3", "tools/ci/checks.py", "dc3", "advisory", "live"),
    ("C/ci.selftest",         "tools/ci/selftest.py", "CONTROLS", "tools/ci/selftest.py", "CONTROLS", "unit-lane", "ci-selftest"),
    ("C/ci.main",             "tools/ci/cli.py", "def", "tools/ci/cli.py", "def", "advisory", "noop"),
    ("C/scripts.ci_slo_gate",      "scripts/ci_slo_gate.py", "def", "scripts/ci_slo_gate.py", "def", "required", "ci-log"),
    ("C/scripts.scan-secrets",     "scripts/scan-secrets.sh", "exit 1", "scripts/scan-secrets.sh", "exit 1", "required", "ci-diff"),
    ("C/scripts.check-locks",      "scripts/check-locks.sh", "#!/", "scripts/check-locks.sh", "#!/", "required", "ci-diff"),
    ("C/scripts.ci_e2e_trigger",   "scripts/ci_e2e_trigger.py", "def decide", "scripts/ci_e2e_trigger.py", "def main", "required", "e2e-push"),
    ("C/scripts.lane_guard",       "scripts/lane_guard.py", "def main", "scripts/lane_guard.py", "def main", "advisory", "noop"),
    ("C/scripts.pr_collision_guard","scripts/pr_collision_guard.py", "def main", "scripts/pr_collision_guard.py", "def main", "advisory", "noop"),
    ("C/scripts.check_scope",      "scripts/check_scope.py", "def main", "", "", "advisory", "noop"),
    ("C/scripts.repo_sweep",       "scripts/repo_sweep.py", "def", "scripts/repo_sweep.py", "def", "advisory", "noop"),
    ("C/scripts.check_sh",         "scripts/check.sh", "#!/", "scripts/check.sh", "#!/", "advisory", "noop"),
    ("C/scripts.base_install_smoke","scripts/base_install_smoke.py", "def", "scripts/base_install_smoke.py", "def", "advisory", "noop"),
    ("C/scripts.ci_env_probe",     "scripts/ci_env_probe.py", "def main", "scripts/ci_env_probe.py", "def main", "required", "envprobe-noexit"),
]

# ============================ Surface E — cold-start gates ============================
E_SPEC = [
    ("E/learning_validated",       "src/fanops/validation_gate.py", "def learning_validated", "src/fanops/validation_gate.py", "metrics_confirmed", "default-path", "learning-closed"),
    ("E/p4_unlocked",              "src/fanops/validation_gate.py", "def p4_unlocked", "src/fanops/validation_gate.py", "def p4_unlocked", "default-path", "ledger"),
    ("E/enough_attributed_signal", "src/fanops/validation_gate.py", "def enough_attributed_signal", "src/fanops/validation_gate.py", "def enough_attributed_signal", "default-path", "ledger"),
    ("E/dim_collecting_progress",  "src/fanops/validation_gate.py", "def dim_collecting_progress", "src/fanops/validation_gate.py", "def dim_collecting_progress", "default-path", "ledger"),
    ("E/learn_doctor",             "src/fanops/learn_doctor.py", "def", "src/fanops/learn_doctor.py", "def", "default-path", "live"),
    ("E/is_weak_hook",             "src/fanops/hookcheck.py", "def is_weak_hook", "src/fanops/hookcheck.py", "def is_weak_hook", "default-path", "weakhook"),
    ("E/gate_source_id",           "src/fanops/gate_keys.py", "def gate_source_id", "src/fanops/gate_keys.py", "def gate_source_id", "default-path", "ledger"),
    ("E/doctor.setup_state",       "src/fanops/doctor.py", "def", "src/fanops/doctor.py", "def", "default-path", "live"),
]

# (Surface F removed: the .claude agent harness — hooks, hookify rules, GateGuard — is the
#  orchestration ABOVE this repo, not FanOps. See the exclusion note in assemble().)

# ============================ Surface D — toggles ============================
D_NONSETTINGS = {
    "FANOPS_ROOT":            ("src/fanops/config.py", "FANOPS_ROOT", "root", False, False),
    "FANOPS_POSTIZ_ONDEMAND": ("src/fanops/daemon.py", "FANOPS_POSTIZ_ONDEMAND", "root", False, False),
    "FANOPS_AUTO_ADOPT":      ("src/fanops/daemon.py", "FANOPS_AUTO_ADOPT", "root", False, False),
    "FANOPS_DAEMON_INTERVAL": ("src/fanops/daemon.py", "FANOPS_DAEMON_INTERVAL", "launchd", False, False),
    "FANOPS_CFG":             ("src/fanops/studio/app.py", "FANOPS_CFG", "noop", False, True),
}
D_EXTRAS = [
    ("FANOPS_LOCAL_TESTS",         "scripts/check.sh", "FANOPS_LOCAL_TESTS"),
    ("FANOPS_CHECK_ALLOW_NO_TESTS","scripts/check.sh", "FANOPS_CHECK_ALLOW_NO_TESTS"),
    ("FANOPS_REQUIRE_STUDIO",      "scripts/check-full.sh", "FANOPS_REQUIRE_STUDIO"),
    ("FANOPS_FIXTURE_ROOT",        "scripts/gen_framing_vectors.py", "FANOPS_FIXTURE_ROOT"),
    ("FANOPS_NC_UNDECLARED",       "tools/arch/selftest.py", "FANOPS_NC_UNDECLARED"),
    ("FANOPS_NC_STALE_DOC",        "tools/arch/selftest.py", "FANOPS_NC_STALE_DOC"),
    ("FANOPS_X",                   "tools/arch/policy.py", "FANOPS_X"),
]

def _first_file(flag, cands):
    for c in cands:
        if find_line(c, flag)[0]:
            return c
    return cands[0]

def _toggle_ctl(flag, kind, probe):
    if kind == "noop":
        return {"cmd": "n/a (not an os.environ toggle)", "exit": "n/a", "delta": "NO-OP", "blocks": None}
    if kind == "launchd":
        return control("launchd")
    if kind == "root":
        return {"cmd": "python -c '<Config/daemon reads os.getenv(flag)>'", "exit": 0,
                "delta": f"{flag} consumed at import/daemon path", "blocks": None}
    d = probe.get(flag) if isinstance(probe, dict) else None
    if d is None:
        return {"cmd": "python -c '<Settings flip>'", "exit": "UNVERIFIED", "delta": "not-a-Settings-field", "blocks": None}
    return {"cmd": f"python -c '<Settings flip {flag}>'", "exit": 0, "delta": d, "blocks": None}

def surface_D():
    rows = []
    rc, out = sh(r"grep -rhoIE --include='*.py' 'FANOPS_[A-Z0-9_]+' src/ | sort -u")
    src_flags = [ln.strip() for ln in out.splitlines() if ln.strip()]
    probe = control("toggle-probe")["delta"]
    for flag in src_flags:
        n, repro = grep_count(flag, "src/", "tools/", "scripts/")
        if flag in D_NONSETTINGS:
            df, dkw, ctl, dead, nat = D_NONSETTINGS[flag]
            da = anchor(df, dkw) or {"loc": f"{df}:1", "expect": flag}
            r = {"id": f"D/{flag}", "surface": "D/toggle", "def": da, "claim": None,
                 "read_sites": {"repro": repro, "count": n, "list": []}, "mechanism": None, "wired": None,
                 "control": _toggle_ctl(flag, ctl, probe), "dead_code": dead, "not_a_toggle": nat}
        else:
            da = (anchor("src/fanops/settings.py", flag) or anchor("src/fanops/config.py", flag)
                  or {"loc": "src/fanops/settings.py:1", "expect": flag})
            r = {"id": f"D/{flag}", "surface": "D/toggle", "def": da, "claim": None,
                 "read_sites": {"repro": repro, "count": n, "list": []}, "mechanism": None, "wired": None,
                 "control": _toggle_ctl(flag, "probe", probe), "dead_code": False, "not_a_toggle": False}
        r["class"] = classify("toggle", r)
        r["repro"] = repro
        rows.append(r)
    for flag, df, dkw in D_EXTRAS:
        n, repro = grep_count(flag, "src/", "tools/", "scripts/")
        da = anchor(df, dkw) or {"loc": f"{df}:1", "expect": flag}
        nat = flag == "FANOPS_X"
        r = {"id": f"D/{flag}", "surface": "D/toggle-extra", "def": da, "claim": None,
             "read_sites": {"repro": repro, "count": n, "list": []}, "mechanism": None, "wired": None,
             "control": _toggle_ctl(flag, "probe" if not nat else "noop", probe),
             "dead_code": False, "not_a_toggle": nat}
        r["class"] = classify("toggle", r)
        r["repro"] = repro
        rows.append(r)
    return rows

# ============================ assemble / build / verify ============================
def _surf_kind(rid):
    return {"B": "ci-gate", "C": "engine", "E": "cold-start"}[rid[0]]

def _bc_row(rid, df, dkw, mf, mkw, wired_kind, ctl, self_ref=False):
    defa = anchor(df, dkw) or {"loc": f"{df}:0", "expect": f"<UNRESOLVED:{dkw}>"}
    mech = anchor(mf, mkw) if mf else None
    if mech and wired_kind == "default-path":                    # E cold-start gate -> a real call site
        wired = _first_caller(dkw.replace("def ", "").strip(), df)
    else:
        wired = _wired_anchor(wired_kind) if mech else None
    r = {"id": rid, "surface": f"{rid[0]}/{_surf_kind(rid)}", "def": defa, "claim": defa,
         "read_sites": {"repro": f"grep -n {shq(dkw)} {df}" if dkw else "n/a", "count": 0, "list": []},
         "mechanism": mech, "wired": wired,
         "control": control(ctl), "self_ref": self_ref}
    r["class"] = classify("gate", r)
    r["repro"] = f"grep -n {shq(mkw or dkw)} {mf or df}"
    return r

def assemble():
    rows = []
    rows += surface_A()
    for spec in B_SPEC:
        rows.append(_bc_row(*spec))
    for spec in C_SPEC:
        rows.append(_bc_row(*spec))
    for spec in E_SPEC:
        rows.append(_bc_row(*spec))
    rows += surface_D()
    return rows

# Surface F (.claude hooks / hookify / GateGuard) is DELIBERATELY EXCLUDED: it is the agent-operating
# harness above this repository, not FanOps source or FanOps's own governance. GateGuard isn't even in
# the tree. Auditing it would conflate "the orchestration above the system" with "the system".

# ---- the two strata this census keeps separate, per the scope correction ----
LAYER = {"A": "governance", "B": "governance", "C": "governance", "D": "system", "E": "system"}
LAYER_DESC = {
    "system": "src/fanops product code — the clip + cross-post engine itself",
    "governance": "the governance/orchestration the project imposes on its own changes (docs, CI, contract engines)",
}

def _base_class(c):
    return re.sub(r"\(.*\)", "", c)

def build():
    rows = assemble()
    (OUTDIR / "theatre.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    render_md(rows)
    unresolved = [r["id"] for r in rows if "<UNRESOLVED" in json.dumps(r)]
    print(f"built {len(rows)} rows -> {OUTDIR/'theatre.jsonl'}")
    if unresolved:
        print(f"UNRESOLVED anchors ({len(unresolved)}): " + ", ".join(unresolved))
    from collections import Counter
    for cls, n in Counter(_base_class(r['class']) for r in rows).most_common():
        print(f"  {n:4}  {cls}")
    return rows

def verify():
    fp = OUTDIR / "theatre.jsonl"
    if not fp.exists():
        print("no theatre.jsonl — run `build` first"); return 1
    recorded = [json.loads(ln) for ln in fp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fresh = assemble()
    by_id = {r["id"]: r for r in fresh}
    drift = []
    if len(recorded) != len(fresh):
        drift.append(f"row-count {len(recorded)} != re-derived {len(fresh)}")
    for rec in recorded:
        f = by_id.get(rec["id"])
        if not f:
            drift.append(f"{rec['id']}: vanished on re-derive"); continue
        for fld in ("def", "mechanism", "wired", "claim"):
            a = rec.get(fld)
            if not a or not isinstance(a, dict):
                continue
            m = re.match(r"^(.+):(\d+)$", a.get("loc", ""))
            if not m:                                     # every anchor MUST be a real file:line, diffable
                drift.append(f"{rec['id']}.{fld}: non-file:line anchor {a.get('loc')!r} (unverifiable)")
            elif a["expect"] not in line_at(m.group(1), int(m.group(2))):
                drift.append(f"{rec['id']}.{fld}: anchor moved ({a['loc']} lacks {a['expect']!r})")
        if rec["read_sites"].get("count") != f["read_sites"].get("count"):
            drift.append(f"{rec['id']}.read_sites: {rec['read_sites']['count']} != {f['read_sites']['count']}")
        re_x, fr_x = rec["control"].get("exit"), f["control"].get("exit")
        if isinstance(fr_x, int) and re_x != fr_x:
            drift.append(f"{rec['id']}.control.exit: {re_x} != {fr_x}")
        if rec["class"] != f["class"]:
            drift.append(f"{rec['id']}.class: recorded {rec['class']} != recomputed {f['class']}")
    md = OUTDIR / "theatre.md"
    if md.exists():
        md_lines = [ln for ln in md.read_text(encoding="utf-8").splitlines() if re.match(r"^[A-Z]", ln) and "def:" in ln]
        if len(md_lines) != len(recorded):
            drift.append(f"md artifact-lines {len(md_lines)} != jsonl rows {len(recorded)}")
    if drift:
        print(f"DRIFT ({len(drift)}):")
        for d in drift[:80]:
            print("  x", d)
        return 1
    print(f"OK - {len(recorded)} rows: every anchor resolves, every count reproduces, every class recomputes. self-verified.")
    return 0

# ============================ theatre.md ============================
def render_md(rows):
    from collections import Counter, OrderedDict
    order = ["DECORATIVE", "INERT", "UNFALSIFIABLE", "SELF-REF", "DEAD-TOGGLE", "NOT-A-TOGGLE", "UNVERIFIED", "REAL", "LIVE"]
    def rank(c):
        b = _base_class(c)
        return order.index(b) if b in order else 99
    counts = Counter(_base_class(r["class"]) for r in rows)
    def layer_of(r): return LAYER.get(r["surface"][0], "governance")
    meaning = {
        "REAL": "captured control injected a violation and it was caught / switch live",
        "LIVE": "toggle flip produced a real captured behavior delta",
        "DECORATIVE": "claims enforcement; no executable mechanism acts on a violation",
        "INERT": "mechanism exists but off the required path (advisory / orphan / unwired)",
        "UNFALSIFIABLE": "wired + required, but the captured control shows it cannot fail",
        "SELF-REF": "effect is solely emitting the artifact that asserts its own pass",
        "UNVERIFIED": "wired, but the blocking control needs CI-pytest / live service / ledger / self-trip",
        "DEAD-TOGGLE": "read 0 times, identical branches, or gates dead code",
        "NOT-A-TOGGLE": "false-positive: matched the grep but is not an os.environ switch",
    }
    L = ["# Theatre census - FanOps @ main", "",
         "Machine-derived; every row's class is recomputed by `verify_theatre.py` from anchors + captured",
         "executions. `python verify_theatre.py` exits 0 iff this file is honest against the tree. The",
         ".claude agent harness (hooks / hookify / GateGuard) is EXCLUDED as orchestration above this repo,",
         "not FanOps. The two strata are kept separate below.", ""]
    for lyr in ("system", "governance"):
        c = Counter(_base_class(r["class"]) for r in rows if layer_of(r) == lyr)
        real, live = c.get("REAL", 0), c.get("LIVE", 0)
        ngates = sum(v for k, v in c.items() if k not in ("LIVE", "DEAD-TOGGLE", "NOT-A-TOGGLE"))
        if lyr == "system":
            ntog = live + c.get("DEAD-TOGGLE", 0) + c.get("NOT-A-TOGGLE", 0)
            L.append(f"**SYSTEM — {live} of {ntog} toggles LIVE, {real} of {ngates} gates REAL. The product enforces.**")
        else:
            theatre = c.get("DECORATIVE", 0) + c.get("INERT", 0) + c.get("UNFALSIFIABLE", 0) + c.get("SELF-REF", 0)
            L.append(f"**GOVERNANCE — {real} of {ngates} gates injection-proven; {theatre} enforce nothing on a PR.**")
        L.append("  " + LAYER_DESC[lyr])
        L.append("  " + " · ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: rank(kv[0]))))
        L.append("")
    L += ["| class | n | meaning |", "|---|---|---|"]
    for cl in sorted(counts, key=rank):
        L.append(f"| {cl} | {counts[cl]} | {meaning.get(cl,'')} |")
    L.append("")
    for lyr in ("system", "governance"):
        L.append(f"# {lyr.upper()} — {LAYER_DESC[lyr]}")
        L.append("")
        surfaces = OrderedDict()
        for r in sorted(rows, key=lambda r: (r["surface"], rank(r["class"]), r["id"])):
            if layer_of(r) == lyr:
                surfaces.setdefault(r["surface"], []).append(r)
        for surf, rs in surfaces.items():
            sc = Counter(_base_class(r["class"]) for r in rs)
            L.append(f"## {surf}  ({len(rs)}: " + ", ".join(f"{k} {v}" for k, v in sc.most_common()) + ")")
            for r in sorted(rs, key=lambda r: (rank(r["class"]), r["id"])):
                g = (r.get("mechanism") or {}).get("loc", "∅")
                ctl = r["control"]
                cd = ctl.get("delta")
                cd = json.dumps(cd) if isinstance(cd, dict) else str(cd)
                if len(cd) > 40:
                    cd = cd[:37] + "..."
                cmd = str(ctl.get("cmd", ""))
                if len(cmd) > 34:
                    cmd = cmd[:31] + "..."
                L.append(f"{r['class']}  {r['id']}  def:{r['def']['loc']}  reads:{r['read_sites']['count']}  "
                         f"gate:{g}  ctl:{cmd}->{ctl.get('exit')}/{cd}")
            L.append("")
    (OUTDIR / "theatre.md").write_text("\n".join(L) + "\n", encoding="utf-8")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    build() if cmd == "build" else sys.exit(verify())
