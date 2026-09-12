# tests/test_production_audit_gates.py
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

ROOT_OPERATOR_DOCS = (
    "docs/CONFIG.md",
    "docs/CONTROL-FILES.md",
    "docs/ENFORCEMENT.md",
    "docs/ENGINEERING_STANDARDS.md",
    "docs/FLAGS.md",
    "docs/GOLIVE.md",
    "docs/INSTAGRAM_CONNECT.md",
    "docs/LEVER-THRESHOLDS.md",
    "docs/LEVERS.md",
    "docs/MACHINE_HEALTH.md",
    "docs/META_CREDS_OPS.md",
    "docs/POSTIZ_OPS.md",
    "docs/POSTIZ_SETUP.md",
    "docs/RUNBOOK.md",
    "docs/YOUTUBE_CONNECT.md",
)


def _live_py_paths() -> set[str]:
    out = subprocess.check_output(["git", "ls-files", "src/fanops/"], text=True, cwd=REPO)
    return {ln for ln in out.splitlines() if ln.endswith(".py")}


def test_live_partition_covers_every_module() -> None:
    part = json.loads((REPO / "docs/CODEMAPS/partition.json").read_text(encoding="utf-8"))
    live = _live_py_paths()
    assert set(part["modules"].keys()) == live
    assert part["live_py_count"] == len(live)
    assert all(v in {f"C{i}" for i in range(1, 11)} for v in part["modules"].values())


def test_config_install_extras_section() -> None:
    text = (REPO / "docs/CONFIG.md").read_text(encoding="utf-8")
    assert "## Install extras and failure posture" in text
    assert "| [asr] |" in text and "| [framing] |" in text and "| [studio] |" in text


def test_register_covers_pack_findings() -> None:
    packs = sorted((REPO / "docs/audit/packs").glob("*.md"))
    assert len(packs) == 14
    reg = (REPO / "docs/audit/WEAKNESS_REGISTER.md").read_text(encoding="utf-8")
    for pack in packs:
        body = pack.read_text(encoding="utf-8")
        ids = re.findall(r"^\| ([A-Z0-9][A-Z0-9-]*-F\d+) \|", body, re.M)
        assert ids, pack.name
        for fid in ids:
            assert fid in reg, f"{fid} from {pack.name} missing in register"


def test_pack_file_count_is_14() -> None:
    packs = list((REPO / "docs/audit/packs").glob("*.md"))
    assert len(packs) == 14


def test_anomalies_reverification_has_substance() -> None:
    text = (REPO / "docs/CODEMAPS/anomalies.md").read_text(encoding="utf-8")
    assert "## Re-verification 2026-09-12" in text
    start = text.index("## Re-verification 2026-09-12")
    chunk = text[start + 1 :]
    nxt = chunk.find("\n## ")
    if nxt != -1:
        chunk = chunk[:nxt]
    lines = [ln for ln in chunk.splitlines()[1:] if ln.strip() and not ln.strip().startswith(">")]
    assert len(lines) >= 10


def test_readme_links_partition_and_score() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/audit/MASTER_MAP.md" in readme
    assert "docs/CODEMAPS/partition.json" in readme
    assert "docs/audit/PRODUCTION_SCORE.md" in readme


def test_audit_artifacts_exist() -> None:
    for rel in (
        "docs/CODEMAPS/partition.json",
        "docs/audit/MASTER_MAP.md",
        "docs/audit/PRODUCTION_SCORE.md",
        "docs/audit/WEAKNESS_REGISTER.md",
    ):
        assert (REPO / rel).is_file(), rel


def test_master_map_lists_root_operator_docs() -> None:
    body = (REPO / "docs/audit/MASTER_MAP.md").read_text(encoding="utf-8")
    for doc in ROOT_OPERATOR_DOCS:
        assert doc in body, doc
