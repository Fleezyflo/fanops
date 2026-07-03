#!/usr/bin/env python3
"""PreToolUse guard for AskUserQuestion: block a DUMB question, allow a real fork.

A regex over the question text cannot judge "was this already settled" — a
polite, nuanced question is exactly the diversion that slips a pattern (proven
this session). So this guard correlates the question against THIS TURN's real
context in the transcript, the same un-gameable approach as completion_evidence.

The operator is not the agent's coding guidelines: they already know best
practice, what they want, and the best way. So the default is DECIDE, not ask.
A question is DUMB — and blocked — when any of these hold:

  1. It re-asks the task just given: the question's salient terms overlap
     heavily with this turn's user prompt (you told me X, I ask "should I X?").
  2. It offers to do the work worse / at reduced ambition: an option set where
     one branch is a lesser version of another (full-vs-simpler, all-vs-partial,
     robust-vs-quick) — doing good work is never a fork.
  3. It asks about a domain a HARD RULE already settles (wipe ledger, black/
     ruff-format, auto-publish, commit control files) — not the operator's to
     re-decide per-question.
  4. It was fired with NO decision-work this turn: straight to asking with no
     prior tool use / investigation — the agent did not exhaust the hierarchy.

A GENUINE fork survives: it introduces a NEW decision the turn's prompt did not
contain, its options are peer alternatives (neither is "less"), it is not rule-
governed, and it follows real investigation. That question is allowed through —
but the standing guidance is still to prefer a one-line prose fork + a grounded
pick over opening a question box at all.

Fails OPEN: any parse error allows the question rather than wedging the tool.
Emits a deny decision as PreToolUse hookSpecificOutput on a dumb question.
"""
import json
import re
import sys

# Option-set "reduced ambition" tells: one branch is a lesser version.
_LESSER_OPTION = re.compile(
    r"\b(?:simpler|simpler|quick(?:er)?|quick\s+and\s+dirty|partial(?:ly)?|"
    r"bare[- ]?minimum|barebones|minimal|lighter|lite|basic|stub|placeholder|"
    r"shortcut|cut\s+corners|half[- ]?\w+|good\s+enough|watered[- ]down|"
    r"band[- ]?aid|hack(?:y)?|less\s+(?:robust|complete|thorough)|"
    r"skip(?:ping)?|defer(?:red)?|for\s+now|later)\b",
    re.IGNORECASE,
)

# Hard-rule-governed domains the operator does not re-decide per question.
_RULE_GOVERNED = re.compile(
    r"\b(?:wipe|reset|clear|empty|truncate)\b.{0,30}\bledger\b"
    r"|\bledger\b.{0,30}\b(?:wipe|reset|clear|empty|truncate)\b"
    r"|\b(?:black|ruff\s+format|autopep8|yapf)\b"
    r"|\bauto[- ]?publish\b|\bpublish\s+(?:without|before)\s+approval\b"
    r"|\bcommit\b.{0,30}\b(?:accounts|personas)\.json\b"
    r"|\bgit\s+reset\s+--hard\b",
    re.IGNORECASE,
)

# Re-confirmation grammar: asking whether to do the thing at all.
_RECONFIRM = re.compile(
    r"\b(?:do you|are you)\b.{0,20}\b(?:really|sure|actually|certain)\b"
    r"|\breally\s+want\s+(?:me|us)\s+to\b"
    r"|\bshould\s+i\s+(?:really|actually|still|even|go ahead)\b"
    r"|\bwant\s+me\s+to\s+(?:go ahead|proceed|do the (?:whole|entire|full))\b",
    re.IGNORECASE,
)

_STOP = set(
    "the a an of to for and or in on at is it be do you me us this that with "
    "should would could can i we my our your how what which when where why "
    "please want need make build add fix use set get run".split()
)


def _turn_prompt_text(transcript_path):
    """Concatenated text of THIS TURN's opening user prompt(s), or ''."""
    with open(transcript_path, "r", encoding="utf-8") as fh:
        entries = []
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                continue

    def is_prompt(e):
        if e.get("type") != "user":
            return False
        c = e.get("message", {}).get("content")
        if isinstance(c, str):
            return c.strip() != ""
        if isinstance(c, list):
            kinds = {b.get("type") for b in c if isinstance(b, dict)}
            return "text" in kinds and "tool_result" not in kinds
        return False

    def ptext(e):
        c = e.get("message", {}).get("content")
        if isinstance(c, str):
            return c
        return " ".join(
            b.get("text", "")
            for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )

    last = -1
    for i, e in enumerate(entries):
        if is_prompt(e):
            last = i
    return ptext(entries[last]) if last >= 0 else ""


def _tools_ran_this_turn(transcript_path):
    """True if any assistant tool_use appears after the last real user prompt."""
    with open(transcript_path, "r", encoding="utf-8") as fh:
        entries = []
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                continue

    def is_prompt(e):
        if e.get("type") != "user":
            return False
        c = e.get("message", {}).get("content")
        if isinstance(c, str):
            return c.strip() != ""
        if isinstance(c, list):
            kinds = {b.get("type") for b in c if isinstance(b, dict)}
            return "text" in kinds and "tool_result" not in kinds
        return False

    last = -1
    for i, e in enumerate(entries):
        if is_prompt(e):
            last = i
    for e in entries[last + 1:]:
        for b in (e.get("message", {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return True
    return False


def _tokens(text):
    return {
        w
        for w in re.findall(r"[a-z0-9_]{3,}", text.lower())
        if w not in _STOP
    }


def _question_blob(questions):
    parts = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        parts.append(q.get("question", ""))
        parts.append(q.get("header", ""))
        for o in q.get("options", []) or []:
            if isinstance(o, dict):
                parts.append(o.get("label", ""))
                parts.append(o.get("description", ""))
    return "\n".join(parts)


def dumb_reason(questions, transcript_path):
    """Return a reason string if the question is dumb, else None. Fails OPEN."""
    try:
        blob = _question_blob(questions)
        if not blob.strip():
            return None  # nothing to judge; let it through

        # 2 + 3 + reconfirm: structural tells, decidable from the question alone.
        if _LESSER_OPTION.search(blob):
            return (
                "offering to do the work at reduced ambition (a 'lesser' option "
                "like simpler/partial/quick/good-enough). Doing good work is not "
                "a fork — build the full version."
            )
        if _RULE_GOVERNED.search(blob):
            return (
                "asking about something a HARD RULE already settles (no-wipe "
                "ledger / no black·ruff-format / no auto-publish / no committing "
                "control files). The operator does not re-decide these per "
                "question — follow the rule."
            )
        if _RECONFIRM.search(blob):
            return (
                "re-confirming a task you were already given ('really/sure/"
                "actually want me to…'). The answer is yes — you were told. "
                "Proceed."
            )

        # 1: echoes the just-given instruction (high token overlap with the turn).
        prompt = _turn_prompt_text(transcript_path)
        if prompt:
            qtok, ptok = _tokens(blob), _tokens(prompt)
            if qtok:
                overlap = len(qtok & ptok) / len(qtok)
                # A question whose salient terms are mostly re-stating this turn's
                # prompt is re-asking what was just required.
                if overlap >= 0.6 and len(qtok & ptok) >= 3:
                    return (
                        "re-asking what this turn's instruction already stated "
                        f"(the question restates the brief — {int(overlap * 100)}% "
                        "of its salient terms come straight from your prompt). "
                        "Decide from what you were told and proceed."
                    )

        # 4: fired with zero decision-work this turn — straight to asking.
        if not _tools_ran_this_turn(transcript_path):
            return (
                "asked before doing any decision-work this turn (no "
                "investigation, no tool use — straight to a question). Exhaust "
                "the hierarchy (brief > requirement > best practice > base > "
                "reliability > scalability) first; a real fork that survives goes "
                "in ONE prose line with your grounded pick, not a question box."
            )

        return None  # a genuine, investigated, peer-option fork — allowed
    except Exception as exc:  # noqa: BLE001 — fail open, never wedge the tool
        print(f"decide_dont_ask: failed open: {exc}", file=sys.stderr)
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if data.get("tool_name") != "AskUserQuestion":
        sys.exit(0)
    questions = data.get("tool_input", {}).get("questions")
    reason = dumb_reason(questions, data.get("transcript_path"))
    if reason:
        msg = (
            "BLOCKED: dumb question — " + reason + " The operator is not your "
            "coding guidelines; they already know best practice and what they "
            "want. Only the operator re-enables asking (via /hookify-configure); "
            "the agent does not ask its way out."
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                    },
                    "systemMessage": msg,
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
