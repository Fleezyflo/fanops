"""Generated-doc rendering — EMPTY since the governance-prose deletion (cleanup route, 2026-07).

`docs/ARCHITECTURE_GOVERNANCE.md`, the one generated doc, died with the prose governance layer
(docs/ENFORCEMENT.md is the index of real enforcers; the machine artifacts under
`.reports/architecture/derived/` remain the canonical, drift-checked surface). The module and its
`expected()`/`render_all()` seam survive so a future generated view can be added without re-plumbing
the drift gate — the contract stands: the output of this module is a VIEW, never a SOURCE, and a
generated file that can silently drift is a defect.
"""
from __future__ import annotations

from pathlib import Path

from .common import REPO


def expected(repo: Path | None = None) -> dict[Path, str]:
    """The generated docs and their EXACT expected bytes — computed, never written.

    Empty since 2026-07: no generated docs remain. Kept as the single seam a future generated
    view plugs into (the drift gate and `render_all` both consume this dict).

    `repo=None`, NOT `repo=REPO`. A default argument is evaluated ONCE, at import — so
    `repo: Path = REPO` freezes the module-level REPO forever, and a negative-control fixture,
    which isolates the checkers by reassigning `render.REPO` to a temp tree, could never move it.
    Resolve the global at CALL time.
    """
    repo = repo or REPO
    return {}


def render_all() -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    for p, text in expected().items():
        out.append((p.relative_to(REPO).as_posix(), _write_text(p, text)))
    return out


def _write_text(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True
