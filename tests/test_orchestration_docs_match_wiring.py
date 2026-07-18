# tests/test_orchestration_docs_match_wiring.py — the agent-facing docs must agree with the ACTUAL
# hook wiring about whether the orchestration gate enforces anything.
#
# WHY THIS EXISTS. The gate was disabled on 2026-07-15 by unwiring it from `.cursor/hooks.json` and
# `.claude/settings.json`. Two documents were updated to say so; eight more kept asserting live
# enforcement — including a machine-read manifest and the banner `orchestrate.py start` prints. An
# agent that believes a dormant gate will stop it skips a step it is actually responsible for. Nothing
# derived a doc's claim from the config, so the drift was invisible and had already recurred once.
#
# WHAT IT IS NOT. This is not a documentation linter and it does not police prose. It reads ONE narrow
# status marker per file and asserts it equals the state DERIVED FROM THE CONFIG. The config is the
# single source of truth; this test stores no active/dormant value of its own. Re-wire the gate and the
# required marker flips automatically — the test is bidirectional, which the negative control proves.
#
# ACCEPTED RESIDUAL: it pins the DECLARED STATUS, not every sentence. A file could carry a correct
# marker beside stale prose. Broader coverage would need prose parsing, which was deliberately excluded.
from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# The two configs that decide whether the gate runs at all.
_CURSOR_HOOKS = _ROOT / ".cursor" / "hooks.json"
_CLAUDE_SETTINGS = _ROOT / ".claude" / "settings.json"

# The gate's two entry points. A config that names neither cannot invoke it.
_GATE_TOKENS = ("orchestration_gate", "orchestration_gate_claude")

_MARKER = "ORCHESTRATION-GATE-STATUS:"
_DORMANT, _WIRED = "DORMANT", "WIRED"

# Every agent-facing document that tells a reader what the gate does. `.orchestration/SPEC.md` is the
# declared status owner; the other two carry the marker because an agent may start at either.
_DOCS = ("AGENTS.md", "ORCHESTRATION.md", ".orchestration/SPEC.md")


def _gate_is_wired(cursor_blob: str, claude_blob: str) -> bool:
    """True iff either loaded hook config actually invokes the gate.

    Deliberately a substring test over the raw config text: it cannot be fooled by a hook entry that
    is nested differently than we expect, and over-detection is the safe direction — claiming WIRED
    when it is not would demand the docs promise enforcement they do not have, and this test would
    then fail loudly rather than pass quietly.
    """
    return any(tok in cursor_blob or tok in claude_blob for tok in _GATE_TOKENS)


def _declared(doc: str) -> str:
    """The single status token a document declares. Raises if absent or ambiguous."""
    text = (_ROOT / doc).read_text(encoding="utf-8")
    found = [v for v in (_DORMANT, _WIRED) if f"{_MARKER} {v}" in text]
    assert found, (
        f"{doc} carries no `{_MARKER} <{_DORMANT}|{_WIRED}>` marker. Every document that describes the "
        f"orchestration gate must declare, in one place, whether it enforces anything — otherwise a "
        f"reader cannot tell a live guarantee from a retained design."
    )
    assert len(found) == 1, f"{doc} declares both {_DORMANT} and {_WIRED}; the status must be unambiguous."
    return found[0]


def test_configs_are_readable():
    """A missing or unparseable config would make every other assertion here vacuous."""
    for p in (_CURSOR_HOOKS, _CLAUDE_SETTINGS):
        assert p.exists(), f"{p.relative_to(_ROOT)} is missing — hook state cannot be derived."
        json.loads(p.read_text(encoding="utf-8"))  # raises on malformed JSON


@pytest.mark.parametrize("doc", _DOCS)
def test_doc_status_matches_actual_hook_wiring(doc: str):
    """The declared status must equal the state derived from the configs — in BOTH directions."""
    expected = _WIRED if _gate_is_wired(
        _CURSOR_HOOKS.read_text(encoding="utf-8"),
        _CLAUDE_SETTINGS.read_text(encoding="utf-8"),
    ) else _DORMANT
    actual = _declared(doc)
    assert actual == expected, (
        f"{doc} declares `{_MARKER} {actual}` but the configs say {expected}. "
        f"Either the wiring changed and the docs were not swept, or the docs were edited without the "
        f"wiring. Fix whichever is wrong — do not weaken this assertion."
    )


def test_negative_control_wired_config_demands_wired_docs():
    """DISCRIMINATING CONTROL: prove the guard is bidirectional, not a one-way string check.

    Feed the deriver a synthetic config in which the gate IS wired, while the real docs (correctly)
    declare DORMANT. The expected status must flip to WIRED and disagree with every document. If this
    control ever passes-by-agreeing, the test above is asserting nothing and the guard is decorative.

    Touches no real config: the synthetic blobs are local strings.
    """
    synthetic_cursor = json.dumps({"version": 1, "hooks": {
        "beforeShellExecution": [{"command": "python3 .cursor/hooks/orchestration_gate.py"}]}})
    synthetic_claude = json.dumps({"hooks": {}})

    assert _gate_is_wired(synthetic_cursor, synthetic_claude), \
        "the deriver failed to see a gate that IS wired — it would never demand a WIRED marker"

    for doc in _DOCS:
        assert _declared(doc) != _WIRED, (
            f"{doc} declares {_WIRED}. Either the gate was genuinely re-wired (then this control needs "
            f"the opposite synthetic config) or a document is promising enforcement that does not exist."
        )
