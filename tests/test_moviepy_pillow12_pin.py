# tests/test_moviepy_pillow12_pin.py — MOL-723. The e2e lock's Pillow advisory surface existed for one
# reason: MoviePy's newest RELEASE (2.2.1) declares `pillow<12.0`, so pip-compile could never resolve the
# e2e lane past Pillow 11.3.0. [compose] therefore pins the IMMUTABLE source archive of upstream commit
# 97316f37 (the one that dropped the cap and shimmed `ImageDraw._multiline_spacing`) — a URL + sha256,
# never a branch/tag and never `git+`, because CI installs with `pip install --require-hashes` and pip
# cannot hash a VCS ref. What is pinned here:
#
#   * the pin stays IMMUTABLE and carries the `#sha256=` fragment (unit lane — the ONLY lane a PR runs);
#   * the lock hashes THAT archive, not a same-named PyPI release (unit lane — see the docstring; this
#     is the one that would have caught the bug hit while building MOL-723);
#   * the e2e lock really did move Pillow past the cap (unit lane);
#   * MoviePy's wrapping TextClip — the sole consumer of the shimmed Pillow internal — really renders
#     against the installed Pillow 12 (integration lane, where .[compose] IS installed).
#
# Delete this file when MoviePy publishes a release > 2.2.1 containing 97316f37; the pyproject comment
# next to the pin carries the same exit condition.
from __future__ import annotations

import re, tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_E2E_LOCK = _ROOT / "requirements" / "ci-e2e.txt"
_MUTABLE = re.compile(r"(?i)\bgit\+|@(main|master|develop|v?\d+(\.\d+)*)\s*$|/(archive|tarball)/(refs/)?(heads|tags)/")
_ARCHIVE = re.compile(r"/archive/(?P<commit>[0-9a-f]{40})\.(tar\.gz|zip)(#sha256=(?P<sha>[0-9a-f]{64}))?$")


def _compose_reqs() -> list[str]:
    with open(_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]["compose"]


def test_compose_pin_is_immutable_never_a_branch_or_tag():
    """AC1: no mutable MoviePy branch/tag dependency — a moving ref would silently change what CI built."""
    reqs = _compose_reqs()
    assert reqs, "the [compose] extra vanished — MoviePy is the compose renderer"
    for req in reqs:
        assert not _MUTABLE.search(req), (
            f"[compose] pins a MUTABLE ref ({req!r}). Use a released `moviepy>=X.Y` or an immutable "
            f"commit archive URL — a branch/tag/git+ ref cannot be hash-verified and can move under CI.")
        if "://" in req:
            m = _ARCHIVE.search(req)
            assert m, f"[compose] URL pin {req!r} does not name a full 40-hex commit archive"
            assert m.group("sha"), (
                f"[compose] URL pin {req!r} has no `#sha256=` fragment. Without it pip-compile resolves "
                f"the archive to the version inside it, matches THAT against PyPI, and emits the released "
                f"artifact's hashes — which the archive can never satisfy, so the lock stops being "
                f"--require-hashes installable. The fragment is what selects pip-tools' URL-hash path.")


def test_e2e_lock_hashes_the_archive_the_pyproject_names():
    """AC2: the lock must carry the hash of the ARTIFACT pyproject pins, not of some same-named release.

    This is the guard for the failure that actually happened while building MOL-723: pip-compile emitted
    the PyPI moviepy-2.2.1 sdist+wheel hashes for a requirement whose source is the GitHub archive, and
    `pip install --require-hashes` refused the whole lock ("Expected 6b56803f... Got 6bef8575..."). A
    `--hash=` is present in both the broken and the correct output, so only comparing it to the
    pyproject fragment tells them apart."""
    want = {m.group("sha") for m in (_ARCHIVE.search(r) for r in _compose_reqs()) if m and m.group("sha")}
    if not want: pytest.skip("[compose] is on a released MoviePy — no archive hash to cross-check")
    lock = _E2E_LOCK.read_text(encoding="utf-8")
    entry = re.search(r"(?m)^moviepy\b.*(?:\n[ \t]+.*)*", lock)
    assert entry, "requirements/ci-e2e.txt no longer pins moviepy — the e2e compose tests need it"
    block = entry.group(0)
    assert "git+" not in block, "the e2e lock carries a VCS moviepy ref; --require-hashes refuses those"
    got = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
    assert got == want, (
        f"requirements/ci-e2e.txt hashes moviepy as {sorted(got)} but pyproject pins the archive "
        f"{sorted(want)}. pip would download the archive, hash it, and refuse the mismatch. Regenerate: "
        f"./scripts/lock-deps.sh --upgrade-package moviepy (on linux/py3.12) — never hand-edit a hash.")


def test_e2e_lock_resolves_pillow_past_the_moviepy_cap():
    """AC3: Pillow 11.3.0 carried the e2e lane's entire Pillow advisory set; the pin exists to escape it."""
    lock = _E2E_LOCK.read_text(encoding="utf-8")
    ver = re.search(r"(?m)^pillow==(\d+)\.(\d+)\.(\d+)", lock)
    assert ver, "requirements/ci-e2e.txt no longer pins pillow — check what dropped it before relaxing this"
    assert int(ver.group(1)) >= 12, (
        f"e2e Pillow is {ver.group(0).split('==')[1]} — the MoviePy `pillow<12.0` cap is back in force. "
        f"The [compose] pin must name a MoviePy revision without that cap.")


@pytest.mark.integration
def test_real_moviepy_wrapping_textclip_renders_under_pillow_12():
    """The pin's whole point, proven on real installs. MoviePy 2.2.1-release sizes captions with
    Pillow's private `ImageDraw._multiline_spacing`, dropped after Pillow 11.2 (upstream CHANGELOG:
    "Fix TextClip broken with Pillow > 11.2"); commit 97316f37 ships its own. A CAPTION TextClip long
    enough to WRAP is the only path through that code, so a one-liner would pass even on a bad pin.
    Skipped where .[compose] is absent (unit lane); FANOPS_REQUIRE_E2E=1 turns that skip into a
    failure on the e2e lane, which is the lane where the pin actually has to hold."""
    pytest.importorskip("moviepy")
    import PIL
    from fanops.compose import TemplateSpec, _text_layer
    assert int(PIL.__version__.split(".")[0]) >= 12, (
        f"Pillow is {PIL.__version__} — this smoke only proves the pin against Pillow 12+")

    def raster(text):
        clip = _text_layer(text, TemplateSpec(), 720, 1280, top=True)
        try: return clip.get_frame(0)                            # forces the Pillow text raster
        finally: clip.close()

    one = raster("DROP")
    many = raster("WAIT FOR THE BEAT TO DROP BECAUSE THIS IS THE PART EVERYONE REWINDS TWICE")
    # Taller than the single-line raster == the caption really wrapped, so line spacing was computed
    # — no magic pixel constant, and a one-liner (which never reaches the shim) cannot satisfy it.
    assert many.shape[0] > one.shape[0], f"caption did not wrap: {many.shape} vs {one.shape}"
    assert many.shape[1] <= int(720 * 0.9) + 8                   # honoured the caption wrap width
