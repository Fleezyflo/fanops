#!/usr/bin/env python3
"""Stop hook — blocks the turn from ENDING when the final message carries the
accountability-avoidance / hedging / half-measure tells the operator flagged.

The 21 PreToolUse rules + the per-turn contract cover tool calls and turn-START.
Nothing watched the turn-END prose, so the diversion lived there: option-deflection
("would you like me to..."), self-exculpating preamble ("I won't paper over..."),
half-measure framing ("good enough for now", "simpler approach"). Those are LEXICAL,
hence detectable, hence blockable — calling them "semantic and unblockable" was the dodge.

On a match it returns {"decision":"block","reason":...}, which feeds the reason back
and forces a rewrite. Guards on stop_hook_active so it blocks AT MOST once per turn
(no infinite loop). Fails OPEN with a visible stderr trace — never silently swallows.
"""
import sys
import re
import json

# High-signal tells only. Each is a genuine diversion signature, not a generic softener,
# so the rule forces a rewrite without firing on every message.
TELLS = [
    # option-deflection: offering instead of doing the grounded route
    (r"\b(would|do) you (want|like) me to\b", "option-deflection (offering instead of doing)"),
    (r"\bshould I (go ahead|proceed|continue)\b", "option-deflection (asking permission to do the obvious)"),
    (r"\b(if you'?d like|if you want)[, ].{0,40}\bI can\b", "option-deflection (conditional offer)"),
    (r"\blet me know (if|whether) you\b", "punt to the operator instead of deciding"),
    # self-exculpating preamble: armoring a weak answer instead of fixing it
    (r"\bI won'?t (paper over|sell you|pretend|sugar-?coat|hide)\b", "self-exculpating hedge preamble"),
    (r"\bthe (one )?honest (limit|truth|ceiling|caveat)\b", "self-exculpating hedge preamble"),
    (r"\bto be (fully |totally )?honest\b", "self-exculpating hedge preamble"),
    # half-measure framing: presenting a lesser route as acceptable
    (r"\bhalf[- ]ass", "half-measure framing"),
    (r"\bgood enough for now\b", "half-measure framing"),
    (r"\b(a |the )?simpler approach\b", "half-measure framing (lesser route)"),
    (r"\bquick and dirty\b", "half-measure framing"),
    (r"\bfor now,? (I'?ll|we'?ll|let'?s) (just )?", "deferral / partial-execution framing"),
    # operator handoff: narrating the user's job instead of executing (FanOps session tells)
    (r"\boperator[- ]gated\b", "operator handoff instead of executing"),
    (r"\b(you can|you could) (click|run|use)\b", "operator handoff (you click…)"),
    (r"\bwhen you'?re ready\b", "deferral to the operator"),
    (r"\b(say the word|let me know if you want)\b", "permission-asking / punt"),
    (r"\b(optional|if you want me to)\b.{0,30}\b(later|push|merge|publish)\b", "optional handoff instead of doing"),
    (r"\b(ship|publish).{0,20}\b(backlog|queue)\b", "shipping backlog unprompted"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in TELLS]


def last_assistant_text(transcript_path):
    """Return the concatenated text of the final assistant message, or ''."""
    with open(transcript_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        entry = json.loads(raw)
        if entry.get("type") != "assistant" and entry.get("message", {}).get("role") != "assistant":
            continue
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        return ""
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"block-hedge-on-stop: bad stdin: {exc}", file=sys.stderr)
        sys.exit(0)

    if data.get("stop_hook_active"):  # already continuing from a prior block — do not loop
        sys.exit(0)

    path = data.get("transcript_path")
    if not path:
        print("block-hedge-on-stop: no transcript_path", file=sys.stderr)
        sys.exit(0)

    try:
        text = last_assistant_text(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"block-hedge-on-stop: transcript read failed: {exc}", file=sys.stderr)
        sys.exit(0)

    hits = sorted({why for rx, why in _COMPILED if rx.search(text)})
    if hits:
        reason = (
            "BLOCKED ending: your message carries diversion tells — "
            + "; ".join(hits)
            + ". Rewrite it: lead with the verdict, do the full grounded route (brief > requirement > "
            "best practice > base-compliance > reliability/robustness/resilience > scalability), and "
            "DELIVER the action instead of offering it or armoring a weaker answer. No half-measures, "
            "no permission-asking, no self-exculpating preamble."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
