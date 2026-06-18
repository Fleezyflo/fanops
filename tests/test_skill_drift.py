"""Drift guard: the fanops-hook-hashtag SKILL.md is DOCUMENTATION; the source of truth is the code
(hashtags.VETTED + prompts._hook_spec). The doc duplicates those values, so without a test it can
silently drift from what actually runs. These tests parse the machine-readable DRIFT-GUARD blocks in
SKILL.md and assert they match the code — mutate either side and this goes red."""
import re
from pathlib import Path
from fanops.hashtags import VETTED
from fanops.prompts import _hook_spec

_SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "fanops-hook-hashtag" / "SKILL.md"

# v2 (craft): the 4 psychological TRIGGERS every hook fires, named in _hook_spec; the doc must not
# drop or rename one. These replace the old 6 inert self-declared labels.
_PATTERNS = ["curiosity gap", "pattern interrupt", "self-relevance", "emotional arousal"]


def _guard_block(name: str) -> str:
    text = _SKILL.read_text()
    m = re.search(rf"DRIFT-GUARD:{name}.*?```[a-z]*\n(.*?)```", text, re.S)
    assert m, f"SKILL.md is missing the machine-readable DRIFT-GUARD:{name} block"
    return m.group(1)


def test_skill_vetted_hashtags_match_code():
    doc_tags = set(re.findall(r"#\S+", _guard_block("hashtags")))
    assert doc_tags == VETTED                      # doc tag set == code VETTED set, exactly


def test_skill_hook_patterns_match_code():
    spec = _hook_spec().lower()
    documented = {ln.strip().lower() for ln in _guard_block("patterns").splitlines() if ln.strip()}
    assert documented == set(_PATTERNS)            # doc lists exactly the canonical patterns
    for p in _PATTERNS:
        assert p in spec                           # ...and every one actually appears in the code spec
