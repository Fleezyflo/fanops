#!/usr/bin/env python3
"""Correlate a completion claim against REAL tool executions in the current turn.

The Stop hook must not trust evidence-SHAPED text in my own final message — I can
type a fake `123 passed` line as easily as a bare claim. The only thing I cannot
forge in prose is the harness-written execution record in the transcript JSONL:
a `tool_use` block I emitted, its matching `tool_result` (a user-type entry with
`is_error` set by the harness), and for Bash the real `toolUseResult.stdout` /
`.stderr` / `.interrupted`. This module reads those records and answers: does a
GENUINE, non-errored tool run in the CURRENT TURN substantiate the claim?

Turn boundary: the current turn is everything after the last REAL user prompt
(a user entry whose content is a str, or a list containing a `text` block). A
`tool_result`-only user entry is NOT a prompt and does not open a new turn.

Claim tiers (strongest anchor required first):
  - test-pass claim  -> a real Bash result whose stdout shows a pytest PASS
                        summary, not interrupted, is_error not True.
  - lint-clean claim -> a real Bash result whose stdout shows the clean summary.
  - generic done/works/verified/✅ -> at minimum a real, non-errored tool run in
                        this turn (you ran something and it did not fail). A
                        "done" with ZERO tool execution this turn is unbacked.

Everything fails OPEN: a parse error, a missing transcript, an unreadable entry
never blocks — the hook degrades to allowing the turn rather than wedging it.
"""
import json
import re

# ── claim classification ────────────────────────────────────────────────────
_TEST_PASS_CLAIM = re.compile(
    r"\ball\s+tests?\s+(?:pass|passed|passing)\b"
    r"|\b\d+\s+tests?\s+pass\b"
    r"|\bsuite\s+(?:is\s+)?green\b"
    r"|\btests?\s+(?:are\s+)?(?:now\s+)?passing\b",
    re.IGNORECASE,
)
_LINT_CLEAN_CLAIM = re.compile(
    r"\blint(?:ing)?\s+(?:is\s+)?(?:clean|passes|passing)\b"
    r"|\bMD013\s+(?:clean|=?\s*0)\b"
    r"|\bruff\s+(?:clean|passes|passing)\b"
    r"|\bno\s+lint\s+errors?\b",
    re.IGNORECASE,
)
_GENERIC_CLAIM = re.compile(
    r"✅"
    r"|\bit\s+works\b"
    r"|\bfully\s+(?:working|fixed|implemented|functional)\b"
    r"|\bverified\s+working\b"
    r"|\bproduction[- ]ready\b"
    r"|\b100%\s+(?:complete|done|working)\b"
    r"|\bsuccessfully\s+(?:implemented|completed|fixed|verified)\b"
    r"|\b(?:done|complete|finished)\s+and\s+(?:verified|tested|working)\b"
    r"|\beverything\s+(?:works|passes|is\s+working)\b"
    r"|\bconfirmed\s+working\b",
    re.IGNORECASE,
)
# Structural guarantee: the strongest claim class — asserting a property is
# ENFORCED (the bad path can't be built). In this codebase that is only true
# when a TEST proves it, so it demands a real passing pytest run this turn.
# Absorbs the old prose-regex rule block-overclaim-without-proof, which fired
# on the vocabulary even when the proving test was cited beside it.
_STRUCTURAL_CLAIM = re.compile(
    r"\bstructurally\s+(?:0|zero)\b"
    r"|\bunrepresentable\b"
    r"|\bimpossible\s+by\s+construction\b"
    r"|\bcannot\s+be\s+constructed\b"
    r"|\bcan\s+no\s+longer\s+be\s+constructed\b"
    r"|\bguaranteed\s+(?:impossible|safe|correct|never)\b"
    r"|\bprovably\s+(?:impossible|correct|safe)\b",
    re.IGNORECASE,
)

# ── real-output signatures (matched against harness stdout, not my prose) ────
_PYTEST_PASS = re.compile(r"\b(\d+)\s+passed\b", re.IGNORECASE)
_PYTEST_FAIL = re.compile(r"\b(\d+)\s+(?:failed|error(?:s)?)\b", re.IGNORECASE)
_LINT_CLEAN_OUT = re.compile(
    r"Summary:\s*0\s+error|All\s+checks\s+passed|\b0\s+problems?\b", re.IGNORECASE
)


def classify_claim(text):
    """Return the strongest claim tier present in text, or None.

    Order matters: a test-pass or lint-clean claim demands a specific real
    result; a generic claim only demands *some* successful run.
    """
    if _STRUCTURAL_CLAIM.search(text):
        return "structural"
    if _TEST_PASS_CLAIM.search(text):
        return "test_pass"
    if _LINT_CLEAN_CLAIM.search(text):
        return "lint_clean"
    if _GENERIC_CLAIM.search(text):
        return "generic"
    return None


def _iter_entries(transcript_path):
    with open(transcript_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue  # skip a corrupt line, never abort the scan


def _is_real_user_prompt(entry):
    """True only for a genuine user turn-opener, not a tool_result entry."""
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return content.strip() != ""
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        # a prompt has a text block and no tool_result; a tool_result entry has
        # tool_result blocks — that is NOT a new turn.
        return "text" in kinds and "tool_result" not in kinds
    return False


def current_turn_entries(transcript_path):
    """Return the entries belonging to the current turn (after the last real
    user prompt), in chronological order. Empty list on any read failure."""
    try:
        entries = list(_iter_entries(transcript_path))
    except OSError:
        return []
    last_prompt_idx = -1
    for i, e in enumerate(entries):
        if _is_real_user_prompt(e):
            last_prompt_idx = i
    return entries[last_prompt_idx + 1:] if last_prompt_idx >= 0 else entries


def _tool_results_in(entries):
    """Yield (tool_use_id, is_error, stdout, stderr, interrupted) for every
    tool_result in entries. Pulls real output from toolUseResult when present."""
    for e in entries:
        if e.get("type") != "user":
            continue
        tur = e.get("toolUseResult")
        stdout = stderr = ""
        interrupted = False
        if isinstance(tur, dict):
            stdout = str(tur.get("stdout", "") or "")
            stderr = str(tur.get("stderr", "") or "")
            interrupted = bool(tur.get("interrupted", False))
        for b in (e.get("message", {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out = stdout
                if not out and isinstance(b.get("content"), str):
                    out = b["content"]  # non-Bash tools put output here
                yield (
                    b.get("tool_use_id"),
                    b.get("is_error"),
                    out,
                    stderr,
                    interrupted,
                )


def claim_is_backed(claim_tier, entries):
    """Does a GENUINE tool run in `entries` substantiate the claim tier?

    Returns (backed: bool, reason: str). `reason` explains a failure so the
    Stop hook can tell me exactly what real evidence is missing.
    """
    results = list(_tool_results_in(entries))
    if not results:
        return False, "no tool ran this turn — the claim rests on nothing executed"

    if claim_tier in ("test_pass", "structural"):
        for _id, is_error, stdout, stderr, interrupted in results:
            blob = f"{stdout}\n{stderr}"
            if interrupted or is_error is True:
                continue
            if _PYTEST_PASS.search(blob) and not _PYTEST_FAIL.search(blob):
                return True, ""
        if claim_tier == "structural":
            return False, (
                "a structural-guarantee claim (a property is ENFORCED / the bad "
                "path can't be built) is only true if a TEST proves it — and no "
                "real passing pytest run this turn backs it. Write the test that "
                "refutes the bad path, run it, and let its output land"
            )
        return False, (
            "no real pytest run this turn shows a clean pass (a passing summary "
            "with no failures/errors, not interrupted, not is_error)"
        )

    if claim_tier == "lint_clean":
        for _id, is_error, stdout, stderr, interrupted in results:
            blob = f"{stdout}\n{stderr}"
            if interrupted or is_error is True:
                continue
            if _LINT_CLEAN_OUT.search(blob):
                return True, ""
        return False, "no real lint/check run this turn shows a clean summary"

    # generic: require at least one real, non-errored, non-interrupted tool run
    for _id, is_error, stdout, stderr, interrupted in results:
        if not interrupted and is_error is not True:
            return True, ""
    return False, "every tool run this turn errored or was interrupted"


def unbacked_claim_reason(final_message_text, transcript_path):
    """Top-level check for the Stop hook. Returns a human reason string if the
    final message makes a completion claim that NO real current-turn tool run
    substantiates, else None. Fails OPEN (returns None) on any error."""
    try:
        tier = classify_claim(final_message_text)
        if tier is None:
            return None
        entries = current_turn_entries(transcript_path)
        backed, why = claim_is_backed(tier, entries)
        if backed:
            return None
        label = {
            "structural": "structural guarantee",
            "test_pass": "tests pass",
            "lint_clean": "lint clean",
            "generic": "done/works/verified",
        }[tier]
        return (
            f"unbacked completion claim ('{label}') — {why}. Run the real check "
            f"and let its ACTUAL output land as a tool result before you claim it; "
            f"pasting evidence-shaped text into your message does not count."
        )
    except Exception as exc:  # noqa: BLE001 — fail open, never wedge the turn
        import sys
        print(f"completion_evidence: failed open: {exc}", file=sys.stderr)
        return None
