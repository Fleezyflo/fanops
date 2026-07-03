#!/usr/bin/env python3
"""UserPromptSubmit — SITUATIONAL execute-first contract.

The old version injected a fixed ~400-char creed before EVERY prompt. A constant
nag is the first thing a model tunes out, and it fired whether or not the turn
had any divert risk. This version is SILENT by default and dispatches only when
this specific turn shows a real signal — and then emits only the ONE line that
fits, not the whole contract.

Signals, strongest first (each is evidence from the turn, not a blanket rule):
  1. I DIVERTED last turn — my previous final message carries a handoff / hedge /
     offer-instead-of-do / operator-gated tell. Highest-value case: I already did
     the bad thing, so the nudge names THAT specific diversion.
  2. The operator is REJECTING / frustrated this turn — push-back language.
     Fire the "you rejected the handoff — ship the code path now" line.
  3. The prompt is about PUBLISHING / going live — fire the "don't bulk-publish
     unless explicitly asked" safety line, and ONLY then.
  4. Nothing detected -> inject nothing.

Honest limit: this is heuristic Python, not an LLM — it dispatches on lexical
tells, not true intent. But precise dispatch + a single targeted line beats a
constant creed, and the Stop hook (block-hedge-on-stop.py) holds the real
enforcement with teeth. Fails open (emits nothing) on any error.
"""
from __future__ import annotations

import json
import re
import sys

# ── 1: divert tells in MY last message (mirrors block-hedge-on-stop) ──────────
_MY_DIVERT = [
    (re.compile(r"\byou (?:can|could|should|might want to) (?:run|click|use|approve|"
                r"execute|invoke)\b", re.I),
     "Last turn you told the operator to run/click something. Do it yourself in "
     "code/CLI unless it needs a secret only they hold."),
    (re.compile(r"\b(?:would|do) you (?:want|like) me to\b|\bshould I (?:go ahead|"
                r"proceed|continue)\b|\blet me know (?:if|whether)\b", re.I),
     "Last turn you OFFERED instead of doing. This turn: decide from the brief "
     "and execute — no permission-asking."),
    (re.compile(r"\boperator[- ]gated\b|\bwhen you'?re ready\b|\bif you want me to\b"
                r".{0,30}\b(?:later|push|merge|publish)\b", re.I),
     "Last turn you deferred to the operator / punted to 'later'. Ship the code "
     "path now."),
    (re.compile(r"\bgood enough for now\b|\bsimpler approach\b|\bquick and dirty\b|"
                r"\bfor now,? (?:I'?ll|we'?ll|let'?s)\b", re.I),
     "Last turn you framed a half-measure as acceptable. Build the full-ambition "
     "version."),
]

# ── 2: operator rejection / frustration THIS turn ────────────────────────────
_REJECTION = re.compile(
    r"\b(?:do your job|not asking me|stop (?:telling|listing|asking)|you skipped|"
    r"lazy|half[- ]?ass|reject(?:ed)?|discard|that'?s not (?:how|what)|"
    r"you (?:always|keep|never)|do it properly|not able|dumber|"
    r"beyond me|do this properly)\b",
    re.I,
)

# ── 3: publishing / go-live risk THIS turn ───────────────────────────────────
_PUBLISH_RISK = re.compile(
    r"\b(?:publish|go[- ]?live|ship (?:the )?(?:queue|backlog|posts?)|"
    r"post (?:to|it|them)|cross-?post|reconcile live|bulk[- ]?publish)\b",
    re.I,
)
# ...but stay silent when the prompt is clearly discussion, not a command.
_PUBLISH_DISCUSS = re.compile(
    r"\b(?:don'?t|do not|never|without|explain|what|why|how does|is the|are the)\b",
    re.I,
)


def _last_assistant_text(transcript_path):
    """My previous final assistant message text, or '' on any failure."""
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text.strip():
                return text
    return ""


def build_context(user_prompt, transcript_path):
    """Return the targeted contract lines for THIS turn, or '' to stay silent."""
    hits = []

    # 1: did I divert last turn? (highest value — evidence I already erred)
    last = _last_assistant_text(transcript_path)
    for rx, line in _MY_DIVERT:
        if rx.search(last):
            hits.append(line)
            break  # one divert line is enough; don't pile on

    # 2: is the operator rejecting / frustrated this turn?
    if _REJECTION.search(user_prompt or ""):
        hits.append(
            "The operator is pushing back — no runbooks, no 'optional later', no "
            "asking. Do the full grounded route and DELIVER it this turn."
        )

    # 3: is this a publish / go-live command? (fire the safety line only here)
    if _PUBLISH_RISK.search(user_prompt or "") and not _PUBLISH_DISCUSS.search(
        user_prompt or ""
    ):
        hits.append(
            "This touches publishing/going-live: do NOT bulk-publish, ship a "
            "queue, or 'prove it works' on live posts unless explicitly told to "
            "in this same message."
        )

    if not hits:
        return ""  # silent by default — no signal, no injection

    return "<execute-first>\n" + "\n".join(f"- {h}" for h in hits) + "\n</execute-first>"


def main():
    user_prompt = ""
    event = "UserPromptSubmit"
    transcript_path = None
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            user_prompt = str(
                data.get("prompt") or data.get("message") or data.get("content") or ""
            )
            event = str(data.get("hook_event_name") or event)
            transcript_path = data.get("transcript_path")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"anti-divert-contract: stdin: {exc}", file=sys.stderr)

    ctx = build_context(user_prompt, transcript_path)
    if not ctx:
        sys.exit(0)  # inject nothing
    out = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": ctx,
        }
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
