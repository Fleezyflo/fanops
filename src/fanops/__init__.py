"""FanOps — intelligent clip + cross-post engine.

The package version has ONE authority: pyproject.toml. `__version__` is DERIVED from the installed
package metadata rather than hand-maintained here, so it can never drift from pyproject the way a
second literal did (this file said 0.3.0 while pyproject said 0.4.0). Consumers (`cli.py`,
`daemon.py`) are unchanged — they still read `fanops.__version__`.
"""
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("fanops")
except PackageNotFoundError:
    # Raw source checkout without an install (no metadata). pyproject remains the authority; this
    # sentinel is deliberately non-real so an un-installed context is obvious, never a fake version.
    __version__ = "0.0.0+uninstalled"
