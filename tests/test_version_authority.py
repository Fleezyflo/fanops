# tests/test_version_authority.py — SLICE-VERSION-AUTHORITY.
#
# READ THE ASSERTION, NOT THE NAME. pyproject.toml is the SINGLE version authority. Before this slice
# src/fanops/__init__.py carried a second hand-maintained literal ("0.3.0") that had drifted from
# pyproject ("0.4.0"); `fanops --version` / the daemon heartbeat reported the stale one. __version__
# now derives from installed metadata, so the two can never disagree again. Hermetic unit test.
from __future__ import annotations

from importlib.metadata import version

import fanops


def test_version_derives_from_package_metadata():
    """The single-authority lock: __version__ MUST equal the installed pyproject version.

    Fails-before this slice (0.3.0 literal != 0.4.0 metadata); passes after (derived). If anyone
    reintroduces a hand-maintained literal that drifts, this goes red.
    """
    assert fanops.__version__ == version("fanops"), (
        f"fanops.__version__ ({fanops.__version__!r}) != pyproject metadata "
        f"({version('fanops')!r}) — derive __version__ from importlib.metadata, do not hand-maintain "
        f"a second literal.")
