"""The user-level engineering contract and this repo's overlay are gone.

Pins: overlay and project hooks.json are absent (no second engine, no empty stub).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OVERRIDE = _ROOT / ".cursor" / "contract.json"
_HOOKS_JSON = _ROOT / ".cursor" / "hooks.json"
_RULE = _ROOT / ".cursor" / "rules" / "fanops-contract.mdc"


def test_overlay_is_absent():
    assert not _OVERRIDE.exists()


def test_project_hooks_json_is_absent():
    assert not _HOOKS_JSON.exists()


def test_project_rule_is_always_applied():
    if not _RULE.exists():
        return
    head = _RULE.read_text(encoding="utf-8").split("---", 2)[1]
    assert "alwaysApply: true" in head
