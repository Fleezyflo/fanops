"""Contract for scripts/scan-secrets.sh — the shared gate for .githooks/pre-commit AND CI (MOL-193).

MOL-724: an EMPTY candidate-file list must scan clean. macOS ships /bin/bash 3.2, where expanding
"${files[@]}" on an empty array is an unbound-variable ABORT under `set -u`; bash >= 4.4 expands it
to nothing. So `staged` with a clean index and `diff-base HEAD` exited 1 for every operator on a
stock macOS bash while CI (ubuntu, bash 5) never saw it.

Because no bash >= 4.4 can be made to exhibit the old semantics (BASH_COMPAT / shopt compat3x do NOT
restore it — verified), the runtime tests below are a true RED/GREEN gate only where a bash < 4.4
exists (macOS /bin/bash). On CI they are a smoke test, and `test_scanner_contract_is_intact` is the
half that is provable everywhere. Both halves ship deliberately; neither alone is sufficient.

Fixture credentials are BUILT BY CONCATENATION so no line of this file is itself a scanner finding —
the pre-commit hook scans this file with these exact patterns.
"""
import os, shutil, subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCANNER = REPO / "scripts" / "scan-secrets.sh"

# One sample per pattern the scanner declares. Values are assembled at runtime; the source lines here
# are inert. "private key block" is the one that regressed silently — its regex starts with '-', so
# without `rg -e` ripgrep parsed it as a flag and the swallowed error read as "no match" (MOL-724).
_SAMPLES = {
    "OpenAI key": "sk" + "-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "GitHub classic token": "ghp" + "_" + "a" * 36,
    "GitHub fine-grained token": "github" + "_pat_" + "b" * 24,
    "AWS access key": "AKI" + "A" + "0123456789ABCDEF",
    "private key block": "-----BEGIN " + "PRIVATE KEY-----",
    "generic credential assignment": "api" + '_key = "' + "s3cr3t-value-here" + '"',
}

_NEEDS_RG = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep absent — scan_one's matcher cannot run")


def _bashes():
    """Every distinct bash on this host, OLDEST-first. /bin/bash is 3.2 on macOS — the only
    interpreter that can exhibit the empty-array abort — so it is exercised whenever present."""
    out, seen = [], set()
    for p in ("/bin/bash", shutil.which("bash")):
        if p and os.path.exists(p) and os.path.realpath(p) not in seen:
            seen.add(os.path.realpath(p)); out.append(p)
    return out or ["bash"]


def _run(cmd, cwd):
    e = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return subprocess.run(cmd, cwd=str(cwd), env=e, capture_output=True, text=True)


def _git(repo, *args):
    r = _run(["git", "-C", str(repo), *args], cwd="/")
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway repo holding a copy of the real scanner, with ONE baseline commit.

    core.hooksPath is pointed at a nonexistent dir: the host may carry a GLOBAL hooksPath whose own
    pre-commit would refuse the deliberate leak commits below.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCANNER, repo / "scripts" / "scan-secrets.sh")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", str(repo / ".nohooks"))
    (repo / "README.md").write_text("baseline\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _scan(repo, bash, *args):
    return _run([bash, "scripts/scan-secrets.sh", *args], cwd=repo)


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("mode", [("staged",), ("diff-base", "HEAD")], ids=["staged", "diff-base"])
def test_empty_scan_exits_clean(sandbox, bash, mode):
    """MOL-724 REGRESSION: zero candidate files is a CLEAN scan, not an abort."""
    r = _scan(sandbox, bash, *mode)
    assert "unbound variable" not in r.stderr, f"{bash} tripped `set -u` on the empty file list:\n{r.stderr}"
    assert r.returncode == 0, f"{bash} {mode} on an empty diff exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    assert r.stderr.strip() == "", f"a clean scan must be silent:\n{r.stderr}"


@_NEEDS_RG
@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_credential_pattern_still_blocks(sandbox, name):
    """Every declared pattern still exits 1 in `staged` mode — the fix relaxes nothing."""
    (sandbox / "leak.txt").write_text(_SAMPLES[name] + "\n")
    _git(sandbox, "add", "leak.txt")
    r = _scan(sandbox, _bashes()[0], "staged")
    assert r.returncode == 1, f"{name!r} was NOT blocked (exit {r.returncode}):\n{r.stderr}"
    assert name in r.stderr, f"{name!r} matched under some other rule:\n{r.stderr}"


@_NEEDS_RG
def test_every_pattern_blocks_in_diff_base_mode(sandbox):
    """The CI code path: all six patterns in one committed file are all reported, exit 1."""
    base = _git(sandbox, "rev-parse", "HEAD").stdout.strip()
    (sandbox / "leak.txt").write_text("\n".join(_SAMPLES.values()) + "\n")
    _git(sandbox, "add", "leak.txt"); _git(sandbox, "commit", "-q", "-m", "leak")
    r = _scan(sandbox, _bashes()[0], "diff-base", base)
    assert r.returncode == 1, f"committed leaks not blocked (exit {r.returncode}):\n{r.stderr}"
    missing = [n for n in _SAMPLES if n not in r.stderr]
    assert missing == [], f"patterns that did not fire: {missing}\n{r.stderr}"


@pytest.mark.parametrize("args", [(), ("bogus",), ("diff-base",)], ids=["no-args", "bad-mode", "diff-base-no-ref"])
def test_bad_invocation_exits_2(sandbox, args):
    """Usage errors stay exit 2 — distinct from 0 (clean) and 1 (finding)."""
    r = _scan(sandbox, _bashes()[0], *args)
    assert r.returncode == 2, f"expected usage exit 2, got {r.returncode}:\n{r.stdout}\n{r.stderr}"
    assert "usage:" in r.stderr


def test_scanner_contract_is_intact():
    """The half provable on ANY bash. Asserts on EXECUTABLE lines only — comments legitimately
    describe the patterns and the (nonexistent) bypass, and must stay describable."""
    src = SCANNER.read_text()
    code = "\n".join(ln for ln in src.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))

    assert "${#files[@]}" in code, "MOL-724: no length guard on the candidate-file array"
    assert 'for file in "${files[@]}"' in code, "the scanned-file loop is gone — re-anchor this test"
    assert code.index("${#files[@]}") < code.index('for file in "${files[@]}"'), \
        "the empty-array guard must come BEFORE the loop that expands it (bash 3.2 aborts otherwise)"

    assert 'rg -n --pcre2 -e "$regex"' in code, "patterns must be passed as -e operands (a '-'-leading regex is not a flag)"

    for name in _SAMPLES:                                    # no pattern quietly removed
        assert f'"{name}"' in code, f"credential pattern {name!r} is gone from the scanner"

    for bypass in ("ECC_SKIP", "SKIP_SCAN", "FANOPS_SKIP", "NO_VERIFY"):
        assert bypass not in code, f"{bypass} bypass introduced — CI must honor no local skip"

    for whole_tree in ("git log", "rev-list", "git grep", "--all"):
        assert whole_tree not in code, f"{whole_tree!r} — the scanner reads ADDED lines only, never the tree/history"
