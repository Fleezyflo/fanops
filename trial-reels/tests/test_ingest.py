"""Tests for ingest source collection shapes."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from lib import ingest as ingest_mod
from lib.ingest import collect_sources


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"in_dir": kwargs.pop("in_dir", "in")}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_ingest_single_file(tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")

    sources = collect_sources(_args(file=media))
    assert sources == [media.resolve()]


def test_ingest_folder(tmp_path: Path):
    folder = tmp_path / "batch"
    folder.mkdir()
    a = folder / "a.mov"
    b = folder / "b.mkv"
    skip = folder / "notes.txt"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    skip.write_text("nope")

    sources = collect_sources(_args(folder=folder))
    assert sources == [a.resolve(), b.resolve()]


def test_ingest_drain_in_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    one = in_dir / "one.webm"
    two = in_dir / "two.mp3"
    one.write_bytes(b"1")
    two.write_bytes(b"2")

    sources = collect_sources(_args(in_dir=in_dir))
    assert sources == [one.resolve(), two.resolve()]


def test_ingest_from_fanops_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clips_root = tmp_path / "03_clips"
    nested = clips_root / "acct"
    nested.mkdir(parents=True)
    clip = nested / "src-abc123-final.mp4"
    clip.write_bytes(b"fanops")

    monkeypatch.setattr(ingest_mod, "FANOPS_CLIPS_ROOT", clips_root)

    sources = collect_sources(_args(from_fanops="abc123"))
    assert sources == [clip.resolve()]
