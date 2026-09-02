"""Drift guard: the fanops-hook-hashtag SKILL.md is DOCUMENTATION; the source of truth is the code
(ship_from_lock for caption ship; prompts._hook_spec engagement brief for hooks)."""
import re
from pathlib import Path
from fanops.hashtags import (
    INT32_MEDIA_COUNT,
    MEGA_MEDIA_FLOOR,
    MID_MEDIA_FLOOR,
)
from fanops.prompts import _hook_spec

_SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "fanops-hook-hashtag" / "SKILL.md"

# Load-bearing phrases of prompts._hook_spec. The skill DRIFT-GUARD:patterns
# block must list exactly these, and each must appear lowercased in _hook_spec.
_PATTERNS = ["keep the viewer watching"]

_COMPOSITION = {
    "MEGA_MEDIA_FLOOR": int(MEGA_MEDIA_FLOOR),
    "MID_MEDIA_FLOOR": int(MID_MEDIA_FLOOR),
    "INT32_MEDIA_COUNT": int(INT32_MEDIA_COUNT),
}

# Old Part 2 4-slot recipe — any of these in Part 2 means the skill is teaching deleted composition.
_STALE_PART2 = (
    "#fyp", "#foryou", "#viral",
    "one mega genre", "one relevance tag", "platform-discovery",
)


def _skill_text() -> str:
    return _SKILL.read_text()


def _guard_block(name: str) -> str:
    text = _skill_text()
    m = re.search(rf"<!-- DRIFT-GUARD:{name}\b.*?-->\s*```[a-z]*\n(.*?)```", text, re.S)
    assert m, f"SKILL.md is missing the machine-readable DRIFT-GUARD:{name} block"
    return m.group(1)


def _part(heading: str) -> str:
    text = _skill_text()
    m = re.search(rf"^## {re.escape(heading)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    assert m, f"SKILL.md is missing section {heading!r}"
    return m.group(0)


def _operator_rule(n: int) -> str:
    text = _skill_text()
    m = re.search(rf"(?ms)^{n}\.\s+.*?(?=^\d+\.\s|\n---|\n## )", text)
    assert m, f"SKILL.md is missing operator rule {n}"
    return m.group(0)


def _composition_constants() -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in _guard_block("composition").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        key, sep, raw = ln.partition("=")
        assert sep, f"unparseable DRIFT-GUARD:composition line: {ln!r}"
        out[key.strip()] = int(raw.replace("_", "").strip())
    return out


def test_skill_hook_patterns_match_code():
    spec = _hook_spec().lower()
    documented = {ln.strip().lower() for ln in _guard_block("patterns").splitlines() if ln.strip()}
    assert documented == set(_PATTERNS)            # doc lists exactly the canonical patterns
    for p in _PATTERNS:
        assert p in spec                           # ...and every one actually appears in the code spec


def test_skill_composition_constants_match_code():
    assert _composition_constants() == _COMPOSITION


def test_skill_part2_is_ship_from_lock_not_banded_composition():
    part2 = _part("Part 2").lower()
    for stale in _STALE_PART2:
        assert stale not in part2, f"Part 2 still teaches stale recipe {stale!r}"
    assert "ship_from_lock" in part2
    assert "source lock" in part2
    assert "clip fit" in part2
    # play_count may remain as a meter; must not be the caption choose-key story
    assert "play_count" in part2
    assert "not the choose-key" in part2
    assert "caption choose-key" not in part2
    assert not re.search(r"choose(?:s|n)? by[` ]*play_count|choose-key among.{0,80}play_count", part2)
    assert "7-day" in part2 or "current_top_reel_play_max_7d" in part2
    assert "80-pile" in part2 or "_per_account_hashtag_stores" in part2
    assert "store ∪ corpus" in part2 or "store u corpus" in part2  # named as NOT the caption menu
    # Ship path must NOT claim AR floor / mega slot as consume rules.
    assert "ar region floor" not in part2 or "no** ar region" in part2 or "no ar region" in part2
    assert re.search(r"\bno\b.*\bar region floor\b|\bno\b.*\b_arabic\b", part2)
    assert re.search(r"\bno\b.*\bmega", part2)
    assert "vet_hashtags" in part2 and "deleted" in part2


def test_skill_caption_is_sentence_plus_tags_not_tag_line():
    text = _skill_text()
    assert "posted_text_for" in text
    assert "compose_posted_caption" in text
    assert re.search(r"3\s*[–-]\s*4\s+tags", text, re.I)
    assert re.search(r"moment\.hook", text, re.I)
    assert "empty lock" in text.lower()


def test_skill_operator_rule2_cites_ship_from_lock():
    rule2 = _operator_rule(2)
    assert "ship_from_lock" in rule2
    assert "vet_hashtags" not in rule2


def test_skill_operator_rule3_is_lock_membership():
    rule3 = _operator_rule(3)
    assert "ship_from_lock" in rule3
    assert "source lock" in rule3.lower()
    assert "shortlist_source_tags" in rule3
    assert "clip fit" in rule3.lower()
    assert "80-pile" in rule3 or "store ∪ corpus" in rule3 or "store u corpus" in rule3
    assert "empty lock" in rule3.lower()
    assert "VETTED" in rule3 and re.search(r"no `?VETTED", rule3)
    flat = re.sub(r"\s+", " ", rule3)
    assert re.search(r"no semantic ban", flat, re.I)
    assert "size_rank_key" not in rule3          # caption path; Layer B stays in Part 3
    # No AR / mega consume claims on the ship rule.
    assert re.search(r"no AR floor|no ar floor", rule3, re.I)
    assert re.search(r"no mega", rule3, re.I)
    # play_count must not be taught as the caption choose-key
    assert "Choose by `play_count`" not in rule3
    assert not re.search(r"picks? up to 4.{0,40}by `?play_count", rule3, re.I)
