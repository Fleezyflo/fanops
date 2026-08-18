"""Drift guard: the fanops-hook-hashtag SKILL.md is DOCUMENTATION; the source of truth is the code
(the hashtags.py COMPOSITION floors + F3 band/slot constants + prompts._hook_spec). The doc
duplicates those values, so without a test it can silently drift from what actually runs. These
tests parse the machine-readable DRIFT-GUARD blocks in SKILL.md and assert they match the code —
mutate either side and this goes red.

The hashtag block used to mirror `hashtags.VETTED`, a hand-ranked reach pool. That pool is DELETED:
a tag's worth is now its live platform measurement, so there is no canonical tag list to document.
What remains frozen — and therefore documentable — is the AR region floor (`_ARABIC`) and the F3
band + slot constants. The platform discovery floor is deleted."""
import re
from pathlib import Path
from fanops.hashtags import (
    INT32_MEDIA_COUNT,
    MEGA_MEDIA_FLOOR,
    MEGA_SLOT_MAX,
    MID_MEDIA_FLOOR,
    _ARABIC,
)
from fanops.prompts import _hook_spec

_SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "fanops-hook-hashtag" / "SKILL.md"

# The proven hook MECHANISMS named in _hook_spec; the doc must not drop or rename one. The original 4
# psychological TRIGGERS (which replaced the old 6 inert labels) plus the 5 evidence-rewrite mechanisms
# (result-first / atmospheric pov / peer-challenge / social proof / fomo) — each carries its craft +
# fail-condition in _hook_spec. One canonical lowercased form (space vs hyphen is the only drift risk).
_PATTERNS = ["curiosity gap", "pattern interrupt", "self-relevance", "emotional arousal",
             "result-first", "atmospheric pov", "peer-challenge", "social proof", "fomo"]

_COMPOSITION = {
    "MEGA_MEDIA_FLOOR": int(MEGA_MEDIA_FLOOR),
    "MID_MEDIA_FLOOR": int(MID_MEDIA_FLOOR),
    "INT32_MEDIA_COUNT": int(INT32_MEDIA_COUNT),
    "MEGA_SLOT_MAX": int(MEGA_SLOT_MAX),
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


def _composition_floors() -> list[str]:
    """The only frozen tag list left in hashtags.py: the AR region floor. Sorted so the doc has ONE
    canonical ordering to mirror."""
    return sorted(set(_ARABIC))


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


def test_skill_composition_floors_match_code():
    doc_tags = re.findall(r"#\S+", _guard_block("hashtags"))
    assert doc_tags == _composition_floors()       # doc list == the code's composition floors, in order


def test_skill_hook_patterns_match_code():
    spec = _hook_spec().lower()
    documented = {ln.strip().lower() for ln in _guard_block("patterns").splitlines() if ln.strip()}
    assert documented == set(_PATTERNS)            # doc lists exactly the canonical patterns
    for p in _PATTERNS:
        assert p in spec                           # ...and every one actually appears in the code spec


def test_skill_composition_constants_match_code():
    assert _composition_constants() == _COMPOSITION


def test_skill_part2_is_not_mega_relevance_discovery_recipe():
    part2 = _part("Part 2").lower()
    for stale in _STALE_PART2:
        assert stale not in part2, f"Part 2 still teaches stale recipe {stale!r}"
    assert "source lock" in part2
    assert "play_count" in part2
    assert "7-day" in part2 or "current_top_reel_play_max_7d" in part2
    assert "mega_slot_max" in part2
    assert "80-pile" in part2 or "_per_account_hashtag_stores" in part2
    assert "store ∪ corpus" in part2 or "store u corpus" in part2  # named as the dead caption menu


def test_skill_caption_is_sentence_plus_tags_not_tag_line():
    text = _skill_text()
    assert "hashtag caption" not in text.lower()
    assert re.search(r"one\s+(?:non-hashtag\s+)?(?:hook\s+)?sentence", text, re.I)
    assert re.search(r"3\s*[–-]\s*4\s+tags", text, re.I)


def test_skill_operator_rule3_is_lock_membership():
    rule3 = _operator_rule(3)
    assert "source lock" in rule3.lower()
    assert "play_count" in rule3
    assert "7-day" in rule3 or "current_top_reel_play_max_7d" in rule3
    assert "80-pile" in rule3 or "store ∪ corpus" in rule3 or "store u corpus" in rule3
    assert "empty lock" in rule3.lower()
    assert re.search(r"at most 1|≤\s*1|<=\s*1", rule3)
    assert "VETTED" in rule3 and re.search(r"no `?VETTED", rule3)
    assert re.search(r"no semantic ban", rule3, re.I)
    assert "size_rank_key" not in rule3          # caption path; Layer B stays in Part 3
