"""Unit tests for scripts/codemap_drift.py."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import codemap_drift as cd  # noqa: E402


def test_parse_index_counts():
    text = "<!-- Files scanned: 109/109 | call_graph.json | 1,067 callables -->"
    got = cd._parse_index_counts(text)
    assert got["module_count_total"] == 109
    assert got["callable_count"] == 1067


def test_find_forbidden_claims_skips_historical(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_ROOT", tmp_path)
    codemaps = tmp_path / "docs" / "CODEMAPS"
    codemaps.mkdir(parents=True)
    (codemaps / "lifecycle-full-picture.md").write_text("crosspost.py:269 is old\n", encoding="utf-8")
    (codemaps / "system-lens-map.md").write_text("live at crosspost.py:269\n", encoding="utf-8")
    hits = cd.find_forbidden_claims(codemaps)
    files = {h["file"] for h in hits}
    assert "docs/CODEMAPS/system-lens-map.md" in files
    assert "docs/CODEMAPS/lifecycle-full-picture.md" not in files


def test_find_forbidden_claims_allows_removed_context(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_ROOT", tmp_path)
    codemaps = tmp_path / "docs" / "CODEMAPS"
    codemaps.mkdir(parents=True)
    (codemaps / "C4.md").write_text("AccountSelection was deleted in P11.\n", encoding="utf-8")
    assert cd.find_forbidden_claims(codemaps) == []


def test_detect_drift_live_repo():
    rep = cd.detect_drift(cache_dir=_ROOT / ".codemap-cache-test")
    assert rep["live"]["module_count"] >= 100
    assert rep["live"]["callable_count"] > rep["live"]["module_count"]
    # Current main docs are known-stale — drift must be detected
    assert rep["drift"] is True
    assert any("callable count" in r or "crosspost.py:269" in r for r in rep["reasons"])
