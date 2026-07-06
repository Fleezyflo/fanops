"""Prove scripts/check.sh scopes ruff + pytest to changed modules vs the merge-base (spec: ci-hooks-cleanup).

The scoping logic is what the gate REMOVED from pre-push must now do correctly. We exercise check.sh in a
throwaway git repo (its own .venv with a stub python we control) so the assertions are hermetic and never
touch the real repo state or run the real suite. We assert on which files check.sh SELECTS, not on their
pass/fail, by making the "unchanged" module a poison pill: if scoping leaks, it gets linted/run and the
script fails loudly.

check_scope.py (the resolver) is unit-tested directly; sandbox tests copy it in so the stub python can
delegate scope resolution to the real interpreter.
"""
import os, shutil, subprocess, textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check.sh"
SCOPE = REPO / "scripts" / "check_scope.py"

# Body written into throwaway fixture test files. A real (never-trivial) assertion so it is not a
# hollow test; the stub python never executes pytest on it anyway (scope is asserted via the log).
_FIXTURE_TEST = "def test_ok():\n    assert (2 * 3) == 6\n"


def _run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git(cwd, *args):
    r = _run(["git", *args], cwd)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway git repo with a fake .venv whose python is a stub we control."""
    repo = tmp_path / "repo"
    (repo / "src" / "fanops").mkdir(parents=True)
    (repo / "tests").mkdir()
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    # Stub `python`: records the argv it was called with to $INVOKED_LOG and exits 0, UNLESS the poison
    # module appears in its args — then exits 1. This lets us assert SCOPE by inspecting the log,
    # deterministically, without a real ruff/pytest.
    py_stub = venv_bin / "python"
    py_stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # Delegate scope resolution to the real interpreter (stdlib-only script).
        for a in "$@"; do
          case "$a" in
            *check_scope.py) exec /usr/bin/env python3 "$@";;
          esac
        done
        echo "$@" >> "$INVOKED_LOG"
        for a in "$@"; do
          case "$a" in
            *poison*) echo "poison reached: $a" >&2; exit 1;;
          esac
        done
        exit 0
    """))
    py_stub.chmod(0o755)

    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(CHECK, scripts / "check.sh")
    shutil.copy2(SCOPE, scripts / "check_scope.py")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    return repo


def _commit_all(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _env(repo):
    e = dict(os.environ)
    e["INVOKED_LOG"] = str(repo / ".invoked")
    e["BASE"] = "HEAD~1"   # scope = last commit's changes, deterministic without origin/main
    return e


def _log(repo):
    p = repo / ".invoked"
    return p.read_text() if p.exists() else ""


def test_scopes_to_changed_module_and_its_test(sandbox):
    """A change to one module lints that module + runs its test; leaves the poison module untouched."""
    repo = sandbox
    (repo / "src" / "fanops" / "widget.py").write_text("x = 1\n")
    (repo / "tests" / "test_widget.py").write_text(_FIXTURE_TEST)
    (repo / "src" / "fanops" / "poison.py").write_text("y = 2\n")
    (repo / "tests" / "test_poison.py").write_text(_FIXTURE_TEST)
    _commit_all(repo, "baseline")

    (repo / "src" / "fanops" / "widget.py").write_text("x = 42\n")
    _commit_all(repo, "touch widget")

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    log = _log(repo)

    assert r.returncode == 0, f"check.sh failed unexpectedly:\n{r.stdout}\n{r.stderr}"
    assert "widget.py" in log
    assert "test_widget.py" in log
    assert "poison" not in log, f"scope leaked to poison module:\n{log}"


def test_changed_test_file_is_run_directly(sandbox):
    """Editing a test file (not a src module) runs THAT test file."""
    repo = sandbox
    (repo / "tests" / "test_alpha.py").write_text(_FIXTURE_TEST)
    (repo / "src" / "fanops" / "poison.py").write_text("y = 2\n")
    _commit_all(repo, "baseline")

    (repo / "tests" / "test_alpha.py").write_text(_FIXTURE_TEST.replace("== 6", "== 6  # touched"))
    _commit_all(repo, "touch test_alpha")

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    log = _log(repo)

    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "test_alpha.py" in log
    assert "poison" not in log


def test_no_python_change_runs_nothing(sandbox):
    """A commit that changes only a .md lints/tests nothing and exits 0 (fast path)."""
    repo = sandbox
    (repo / "src" / "fanops" / "poison.py").write_text("y = 2\n")
    (repo / "README.md").write_text("v1\n")
    _commit_all(repo, "baseline")

    (repo / "README.md").write_text("v2\n")
    _commit_all(repo, "docs only")

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "no changed .py" in r.stdout
    assert not (repo / ".invoked").exists(), "ran python despite no .py change"


def test_scoped_pytest_failure_propagates_nonzero(sandbox):
    """A changed module whose scoped test 'fails' (poison in the path) makes check.sh exit non-zero."""
    repo = sandbox
    (repo / "src" / "fanops" / "poison.py").write_text("y = 2\n")
    (repo / "tests" / "test_poison.py").write_text(_FIXTURE_TEST)
    _commit_all(repo, "baseline")

    (repo / "src" / "fanops" / "poison.py").write_text("y = 99\n")
    _commit_all(repo, "touch poison")

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    assert r.returncode != 0, "check.sh must exit non-zero when a scoped check fails"


def test_pre_push_hook_runs_no_pytest():
    """The real pre-push hook must INVOKE zero test tooling (spec: guards only).

    Asserts on executable INVOCATION, not word occurrence — the hook's own comments legitimately say
    'no pytest / no ruff' to document the policy, and blocking the word would forbid documenting it.
    """
    hook = (REPO / ".githooks" / "pre-push").read_text()
    # Strip comment + blank lines; only executable statements may not invoke test tooling.
    code = "\n".join(ln for ln in hook.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
    assert "-m pytest" not in code and "pytest " not in code, "pre-push must not invoke pytest"
    assert "-m ruff" not in code and "ruff check" not in code, "pre-push must not invoke ruff"
    # No skip-bypass BRANCH in executable code (the comment may explain that none exists — that's fine).
    assert "FANOPS_SKIP_PREPUSH" not in code, "no skip-bypass branch in a hook that runs no tests"
    assert "refs/heads/main" in code, "pre-push must still guard main"


def test_check_full_mirrors_ci():
    """check-full.sh must run the CI unit command: ruff check . + pytest -m 'not integration'."""
    full = (REPO / "scripts" / "check-full.sh").read_text()
    assert "ruff check ." in full
    assert 'pytest -q -m "not integration"' in full


def test_scopes_studio_module_to_studio_test(sandbox):
    """A studio/ subdir change maps to tests/test_studio_<name>.py (not skipped by top-level-only glob)."""
    repo = sandbox
    (repo / "src" / "fanops" / "studio").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "fanops" / "studio" / "widget.py").write_text("x = 1\n")
    (repo / "tests" / "test_studio_widget.py").write_text(_FIXTURE_TEST)
    (repo / "src" / "fanops" / "poison.py").write_text("y = 2\n")
    _commit_all(repo, "baseline")

    (repo / "src" / "fanops" / "studio" / "widget.py").write_text("x = 42\n")
    _commit_all(repo, "touch studio widget")

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    log = _log(repo)

    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "test_studio_widget.py" in log, f"studio scoped test not selected:\n{log}"
    assert "poison" not in log, f"scope leaked to poison module:\n{log}"


def test_check_scope_resolver_conventions():
    """Unit-test the resolver: studio/, post/, and override alternates."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_scope", SCOPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = mod.resolve_tests
    assert "tests/test_studio_actions.py" in r(["src/fanops/studio/actions.py"])
    assert "tests/test_post_run.py" in r(["src/fanops/post/run.py"])
    assert "tests/test_smart_framing.py" in r(["src/fanops/framing.py"])
    assert "tests/test_ledger.py" in r(["src/fanops/ledger.py"])
    assert r(["src/fanops/controlio.py"]) == ["tests/test_cutover.py"]


def test_check_scope_covers_all_src_modules():
    """Every src module must resolve to >=1 test via convention or override (no silent blind spots)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_scope", SCOPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src_root = REPO / "src" / "fanops"
    bare = []
    for py in sorted(src_root.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(REPO).as_posix()
        if not mod.resolve_tests([rel]):
            bare.append(rel)
    assert bare == [], f"modules with no scoped test mapping: {bare}"


def test_check_self_heals_hookspath(sandbox):
    """check.sh arms the policy hooks: an unwired repo gets core.hooksPath=.githooks set for it.

    This is the root fix for 'hooks are inert until someone remembers to wire them' — the main-push
    guard would otherwise be off by default in every fresh clone/worktree.
    """
    repo = sandbox
    # Baseline so BASE=HEAD~1 resolves; no .py change needed — the wiring runs before the diff logic.
    (repo / "README.md").write_text("v1\n")
    _commit_all(repo, "baseline")
    (repo / "README.md").write_text("v2\n")
    _commit_all(repo, "docs")

    # Precondition: the sandbox has NO hooksPath set.
    pre = _run(["git", "config", "--local", "core.hooksPath"], repo)
    assert pre.stdout.strip() == "", "sandbox should start unwired"

    r = _run(["bash", str(CHECK)], repo, env=_env(repo))
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

    post = _run(["git", "config", "--local", "core.hooksPath"], repo)
    assert post.stdout.strip() == ".githooks", "check.sh must wire the policy hooks"
    assert "armed" in r.stdout, "check.sh should announce it armed the hooks"
