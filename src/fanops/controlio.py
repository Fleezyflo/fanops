"""Shared atomic read/write primitives for the hand-editable JSON control files (accounts.json,
personas.json). Extracted from accounts.py/personas.py, which carried a byte-identical copy of the
atomic-write body — exactly the correctness-critical code (unique temp + os.replace + cleanup-on-failure)
where a future hardening of one copy would silently skip the other. ONE implementation now; both modules
route through it. NB the LEDGER has its own writer (fixed .json.tmp + 0600, single-writer-under-flock) —
deliberately NOT merged here; this pair is for the multi-writer, operator-hand-editable control files."""
from __future__ import annotations
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from fanops.errors import ControlFileError, reason as _reason

M = TypeVar("M", bound=BaseModel)


def write_json_atomic(p: Path, raw: object) -> None:
    """Persist any JSON-serializable value via temp file + os.replace, so a crash mid-write never leaves a
    torn file. A UNIQUE temp (mkstemp, same dir so os.replace stays atomic) — a fixed <name>.tmp lets two
    concurrent writers clobber each other's temp (one's os.replace then FileNotFoundErrors). On any failure
    the temp is best-effort unlinked and the ORIGINAL error re-raised (the suppress only guards the cleanup
    unlink, never the real write error). Indented for the operator who still hand-edits. NB ffmpeg/mpeg
    atomic writes (clip.render_reframed) use their own muxer-inferable .part suffix discipline (MOL-78) —
    these helpers are the JSON/text/bytes control-file boundary."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh: fh.write(json.dumps(raw, indent=2) + "\n")
        os.replace(tmp, p)                               # atomic: never a half-written file
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)   # best-effort cleanup; re-raise the real error
        raise


def write_text_atomic(p: Path, text: str, *, mode: int | None = None) -> None:
    """Persist text via mkstemp + os.replace in the target's directory (same-fs atomic swap). On failure the
    temp is best-effort unlinked and the original error re-raised. Optional `mode` is applied to the temp
    before replace. NB ffmpeg/mpeg atomic writes use their own .part suffix discipline (MOL-78) — not here."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh: fh.write(text)
        if mode is not None: os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise


def write_bytes_atomic(p: Path, data: bytes, *, mode: int | None = None) -> None:
    """Persist bytes via mkstemp + os.replace in the target's directory (same-fs atomic swap). On failure the
    temp is best-effort unlinked and the original error re-raised. Optional `mode` is applied to the temp
    before replace. NB ffmpeg/mpeg atomic writes use their own .part suffix discipline (MOL-78) — not here."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        os.write(fd, data); os.close(fd); fd = -1
        if mode is not None: os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        if fd >= 0: os.close(fd)
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise


def load_raw_list(p: Path, key: str) -> tuple[dict, list]:
    """A control file as the RAW dict (absent -> {key: []}) + its top-level `key` list. Mutating the raw
    dict (not a model_dump) preserves unknown/future fields and sibling records exactly. Raises
    ControlFileError when the top-level `key` is not a list (a corrupt/mis-shaped file)."""
    raw = json.loads(p.read_text()) if p.exists() else {key: []}
    lst = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(lst, list):
        raise ControlFileError(f"{p.name} invalid: expected a top-level '{key}' list")
    return raw, lst


def load_validated(p: Path, model: type[M]) -> M:
    """Read a JSON control file and validate it against `model`. Missing file -> ControlFileError.
    Malformed JSON or schema violation -> ControlFileError naming the field (fail-LOUD, uniform policy)."""
    if not p.exists():
        raise ControlFileError(f"{p.name} missing: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise ControlFileError(f"{p.name} invalid: JSON parse error at line {e.lineno}: {e.msg}") from e
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
