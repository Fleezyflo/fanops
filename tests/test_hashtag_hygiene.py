# tests/test_hashtag_hygiene.py
# STRUCTURAL quality gates for a tag entering a DERIVED corpus. Only defects a machine can actually
# decide live here — a keysmash (`#fypppp…`, 73 p's, which shipped live), a tag that is really a
# sentence, a digits-only tag, anything that does not survive normalization. These are properties of the
# STRING, so a refusal is always explainable and testable.
#
# The editorial denylists are GONE: "generic engagement" (#love/#instagood) and "discovery-owned"
# (#fyp/#reels) encoded a taste claim the platform now answers directly — a tag's worth is its measured
# like_count, and a broad high-reach tag co-occurring with the persona's niche is exactly the versatility
# the corpus is supposed to have. Semantic fit stays unattempted for the same reason it always was.
# Relevance is enforced upstream (discovery is anchored in the persona's description) and the operator's
# ban list remains the explicit veto.
import pytest
from fanops.hashtags import is_curatable, norm_tag, tag_defect

_FYP = "#fypppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"   # 73 p's — was live + shipping


@pytest.mark.parametrize("tag,why", [
    (_FYP, "keysmash"), ("#aaaa", "keysmash"), ("#123", "digits"), ("#a", "too short"),
    ("#" + "x" * 40, "too long"), ("", "empty"), ("#", "empty"), ("   ", "empty"),
    ("#has space", "charset"), ("#عربي", "charset"),
])
def test_structural_junk_is_refused_with_a_reason(tag, why):
    d = tag_defect(tag)
    assert d, f"{tag} ({why}) must be refused"
    assert isinstance(d, str) and d.strip(), "a refusal must say WHY — an unexplained refusal is unreviewable"
    assert is_curatable(tag) is False


@pytest.mark.parametrize("tag", ["#bars", "#lyrics", "#undergroundhiphop", "#hiphopmusic", "#arabicmusic",
                                 "#rap", "#freestyle", "#rapper", "#podcast", "#storytime", "#rapmusic"])
def test_catalogue_valid_tags_are_curatable(tag):
    assert tag_defect(tag) is None, f"{tag} is real + catalogue-valid and must survive the gate"


@pytest.mark.parametrize("tag", ["#love", "#instagood", "#explore", "#trending", "#art", "#spotify",
                                 "#fyp", "#reels", "#viral"])
def test_broad_tags_are_curatable_because_the_platform_judges_worth(tag):
    # REFUTES the old editorial denylists: these were refused as "generic engagement" / "discovery-owned",
    # i.e. on a taste claim. A tag's worth is now its measured like_count, so the structural gate lets
    # them through and the measurement decides.
    assert tag_defect(tag) is None, f"{tag} is structurally clean — worth is the platform's call, not ours"


def test_hygiene_normalizes_before_judging():
    # the gate must not be bypassable by casing/whitespace
    assert tag_defect(" #AAAA ") and tag_defect("123")
    assert tag_defect(" #Love ") is None                 # ...and normalization does not invent a defect


def test_keysmash_beats_length_in_the_reason_string():
    # `#fypppp…` is both over-long AND a keysmash; the reason is operator-facing, so the most precise
    # true statement must win.
    assert "keysmash" in tag_defect(_FYP)


def test_structurally_junk_tag_is_not_curatable():
    assert not is_curatable(_FYP)


@pytest.mark.parametrize("raw,want", [
    ("", ""), ("   ", ""), ("#", ""), ("#Love", "#love"), (" #bars ", "#bars"),
    ("bars", "#bars"), ("##bars", "#bars"),
])
def test_norm_tag_canonicalizes(raw, want):
    assert norm_tag(raw) == want
