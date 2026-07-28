"""Drift guard: the fanops-hook-hashtag SKILL.md is DOCUMENTATION; the source of truth is the code
(the hashtags.py COMPOSITION floors + prompts._hook_spec). The doc duplicates those values, so without a
test it can silently drift from what actually runs. These tests parse the machine-readable DRIFT-GUARD
blocks in SKILL.md and assert they match the code — mutate either side and this goes red.

The hashtag block used to mirror `hashtags.VETTED`, a hand-ranked reach pool. That pool is DELETED: a
tag's worth is now its live platform measurement, so there is no canonical tag list to document. What
remains frozen — and therefore documentable — is the COMPOSITION floor: the AR region tags
(`_ARABIC`), which is format rather than a reach claim. The platform discovery floor is deleted."""
import re
from pathlib import Path
from fanops.hashtags import _ARABIC
from fanops.prompts import _hook_spec

_SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "fanops-hook-hashtag" / "SKILL.md"

# The proven hook MECHANISMS named in _hook_spec; the doc must not drop or rename one. The original 4
# psychological TRIGGERS (which replaced the old 6 inert labels) plus the 5 evidence-rewrite mechanisms
# (result-first / atmospheric pov / peer-challenge / social proof / fomo) — each carries its craft +
# fail-condition in _hook_spec. One canonical lowercased form (space vs hyphen is the only drift risk).
_PATTERNS = ["curiosity gap", "pattern interrupt", "self-relevance", "emotional arousal",
             "result-first", "atmospheric pov", "peer-challenge", "social proof", "fomo"]


def _guard_block(name: str) -> str:
    text = _SKILL.read_text()
    m = re.search(rf"DRIFT-GUARD:{name}.*?```[a-z]*\n(.*?)```", text, re.S)
    assert m, f"SKILL.md is missing the machine-readable DRIFT-GUARD:{name} block"
    return m.group(1)


def _composition_floors() -> list[str]:
    """The only frozen tag list left in hashtags.py: the AR region floor. Sorted so the doc has ONE
    canonical ordering to mirror."""
    return sorted(set(_ARABIC))


def test_skill_composition_floors_match_code():
    doc_tags = re.findall(r"#\S+", _guard_block("hashtags"))
    assert doc_tags == _composition_floors()       # doc list == the code's composition floors, in order


def test_skill_hook_patterns_match_code():
    spec = _hook_spec().lower()
    documented = {ln.strip().lower() for ln in _guard_block("patterns").splitlines() if ln.strip()}
    assert documented == set(_PATTERNS)            # doc lists exactly the canonical patterns
    for p in _PATTERNS:
        assert p in spec                           # ...and every one actually appears in the code spec
