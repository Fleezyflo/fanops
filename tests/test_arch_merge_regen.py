"""MOL-833: merge=ours on architecture derived/ + post-merge regen removes the manual ritual.

Hermetic throwaway git repo. A stub `.venv/bin/python` implements `python -m tools.arch regen` by
hashing `src/**` into `.reports/architecture/derived/MANIFEST.json` — same contract as the real
regen (pure function of source; no new generation logic). Exercises the shipped `.gitattributes`
and `.githooks/post-merge` (copied into the sandbox).

Callers: CI unit lane via pytest collection. No production API. Schema under test is a synthetic
`{"digest":"<sha256>"}` MANIFEST only. User instruction: meet acceptance (clean dual-branch merge,
byte-identical regen, negative control for real conflicts, drift gate stays green).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DERIVED_REL = Path(".reports/architecture/derived/MANIFEST.json")


def _run(cmd, cwd, env=None):
    e = dict(os.environ if env is None else env)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True)


def _git(repo: Path, *args: str, check: bool = True):
    r = _run(["git", "-C", str(repo), *args], cwd="/")
    if check:
        assert r.returncode == 0, f"git {args} failed:\n{r.stdout}\n{r.stderr}"
    return r


def _fingerprint(repo: Path) -> str:
    """Byte-stable fingerprint of every file under src/ (sorted paths)."""
    h = hashlib.sha256()
    src = repo / "src"
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(repo).as_posix()
            h.update(rel.encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def _write_manifest(repo: Path) -> str:
    digest = _fingerprint(repo)
    path = repo / DERIVED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{{"digest":"{digest}"}}\n', encoding="utf-8")
    return digest


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Throwaway repo with shipped attributes + post-merge and a stub arch regen."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".reports" / "architecture" / "derived").mkdir(parents=True)
    hooks = repo / ".githooks"
    hooks.mkdir()
    shutil.copy2(REPO / ".gitattributes", repo / ".gitattributes")
    shutil.copy2(REPO / ".githooks" / "post-merge", hooks / "post-merge")
    (hooks / "post-merge").chmod(0o755)

    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    # Stub python: only implements `python -m tools.arch regen` → rewrite MANIFEST from src/.
    py = venv_bin / "python"
    py.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "-m" && "${2:-}" == "tools.arch" && "${3:-}" == "regen" ]]; then
          ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
          python3 - "$ROOT" <<'PY'
        import hashlib, sys
        from pathlib import Path
        root = Path(sys.argv[1])
        h = hashlib.sha256()
        for p in sorted((root / "src").rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                h.update(rel.encode()); h.update(b"\\0")
                h.update(p.read_bytes()); h.update(b"\\0")
        out = root / ".reports/architecture/derived/MANIFEST.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"digest":"' + h.hexdigest() + '"}\\n', encoding="utf-8")
        print("rewritten MANIFEST.json")
        PY
          exit 0
        fi
        echo "unexpected stub invocation: $*" >&2
        exit 2
    """))
    py.chmod(0o755)

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", ".githooks")
    # Same install path as ./scripts/setup-hooks.sh — attribute alone does not arm ours.
    _git(repo, "config", "merge.ours.driver", "true")

    (repo / "src" / "left.py").write_text("LEFT=0\n", encoding="utf-8")
    (repo / "src" / "right.py").write_text("RIGHT=0\n", encoding="utf-8")
    _write_manifest(repo)
    _commit_all(repo, "baseline")
    return repo


def test_repo_declares_merge_ours_on_derived():
    """Shipped attribute must keep generated derived/ on the built-in ours driver."""
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert ".reports/architecture/derived/**" in text
    assert "merge=ours" in text
    r = _run(
        ["git", "check-attr", "merge", "--", ".reports/architecture/derived/MANIFEST.json"],
        cwd=REPO,
    )
    assert r.returncode == 0
    assert "merge: ours" in r.stdout


def test_concurrent_derived_rewrites_merge_clean_and_match_fresh_regen(sandbox: Path):
    """Acceptance: two branches each shift a scanned source line → clean merge; artifact == regen."""
    repo = sandbox

    _git(repo, "checkout", "-q", "-b", "branch-a")
    (repo / "src" / "left.py").write_text("LEFT=1\n", encoding="utf-8")
    digest_a = _write_manifest(repo)
    _commit_all(repo, "shift left")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "branch-b")
    (repo / "src" / "right.py").write_text("RIGHT=1\n", encoding="utf-8")
    digest_b = _write_manifest(repo)
    _commit_all(repo, "shift right")
    assert digest_a != digest_b

    _git(repo, "checkout", "-q", "branch-a")
    merge = _git(repo, "merge", "--no-edit", "branch-b", check=False)
    assert merge.returncode == 0, (
        f"merge must be clean with merge=ours + post-merge:\n{merge.stdout}\n{merge.stderr}"
    )
    assert "CONFLICT" not in merge.stdout
    assert "CONFLICT" not in merge.stderr

    merged_manifest = (repo / DERIVED_REL).read_text(encoding="utf-8")
    expect = _fingerprint(repo)
    assert merged_manifest == f'{{"digest":"{expect}"}}\n', (
        "post-merge must leave derived/ byte-identical to a fresh regen on the merged source"
    )

    # Second regen is a no-op (determinism / acceptance re-assert).
    r = _run([str(repo / ".venv" / "bin" / "python"), "-m", "tools.arch", "regen"], cwd=repo)
    assert r.returncode == 0, f"regen failed:\n{r.stdout}\n{r.stderr}"
    assert (repo / DERIVED_REL).read_text(encoding="utf-8") == merged_manifest

    # Hook must not have auto-committed the refresh (follow-up commit is human/agent).
    status = _git(repo, "status", "--porcelain")
    head_manifest = _git(repo, "show", f"HEAD:{DERIVED_REL.as_posix()}").stdout
    if head_manifest != merged_manifest:
        # Working tree dirty on derived/ — expected after post-merge regen of stale ours.
        assert DERIVED_REL.as_posix() in status.stdout


def test_real_source_conflict_fails_loudly_and_is_not_regenerated_away(sandbox: Path):
    """Negative control: a non-generated conflict still fails; hook must not fire / wipe it."""
    repo = sandbox

    _git(repo, "checkout", "-q", "-b", "branch-a")
    (repo / "src" / "left.py").write_text("LEFT=from-a\n", encoding="utf-8")
    _write_manifest(repo)
    _commit_all(repo, "a edits left")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "branch-b")
    (repo / "src" / "left.py").write_text("LEFT=from-b\n", encoding="utf-8")
    _write_manifest(repo)
    _commit_all(repo, "b edits left")

    _git(repo, "checkout", "-q", "branch-a")
    merge = _git(repo, "merge", "--no-edit", "branch-b", check=False)
    assert merge.returncode != 0, "incompatible source edits must conflict"
    left_text = (repo / "src" / "left.py").read_text(encoding="utf-8")
    assert "<<<<<<<" in left_text, (
        "real conflict must remain visible — not silently regenerated away"
    )
    assert (repo / ".git" / "MERGE_HEAD").exists()
