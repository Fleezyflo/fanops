# tests/test_hashtag_hygiene.py
# R4 — STRUCTURAL quality gates for a tag ENTERING the curated corpus. Junk here is load-bearing junk
# everywhere: a corpus tag seeds the discovery store, joins the vetted membership, and leads the shipped
# line, so it is near-permanent once in. Every tag below was LIVE in a persona corpus on 2026-07-16, and
# `#fypppp…` (73 p's) was shipping to production.
#
# Semantic fit ("is #taylorswift right for THIS artist") is deliberately NOT tested here: it is not
# machine-decidable, an off-catalogue denylist is unbounded, and that judgement is the operator's — which
# is exactly why the curated corpus is human-governed. See hashtag_hygiene's module docstring.
import pytest
from fanops.config import Config
from fanops import personas as P
from fanops.hashtag_hygiene import screen_corpus, tag_defect

_FYP = "#fypppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"   # 73 p's — was live + shipping


@pytest.mark.parametrize("tag,why", [
    (_FYP, "keysmash"), ("#aaaa", "keysmash"), ("#love", "generic"), ("#instagood", "generic"),
    ("#explore", "generic"), ("#explorepage", "generic"), ("#highlights", "generic"), ("#post", "generic"),
    ("#trending", "generic"), ("#art", "generic"), ("#spotify", "generic"), ("#missviralchallenge", "generic"),
    ("#fyp", "discovery-owned"), ("#reels", "discovery-owned"), ("#viral", "discovery-owned"),
    ("#123", "digits"), ("#a", "too short"), ("#" + "x" * 40, "too long"),
])
def test_junk_is_refused_with_a_reason(tag, why):
    d = tag_defect(tag)
    assert d, f"{tag} ({why}) must be refused"
    assert isinstance(d, str) and d.strip(), "a refusal must say WHY — an unexplained refusal is unreviewable"


@pytest.mark.parametrize("tag", ["#bars", "#lyrics", "#undergroundhiphop", "#hiphopmusic", "#arabicmusic",
                                 "#rap", "#freestyle", "#rapper", "#podcast", "#storytime", "#rapmusic"])
def test_catalogue_valid_tags_are_curatable(tag):
    assert tag_defect(tag) is None, f"{tag} is real + catalogue-valid and must survive the gate"


def test_hygiene_normalizes_before_judging():
    # the gate must not be bypassable by casing/whitespace
    assert tag_defect(" #LOVE ") and tag_defect("FYP") and tag_defect("Instagood")


def test_discovery_tags_are_refused_because_the_selector_floors_them():
    # not "bad tags" — vet_hashtags already backfills one per platform (_DISCOVERY/disc_floor), so a
    # curated copy burns a brand slot to buy reach the selector grants for free: a duplicate lever.
    for t in ("#fyp", "#reels", "#viral"):
        assert "discovery" in (tag_defect(t) or "")


def test_corpus_write_boundary_refuses_junk(tmp_path):
    from fanops.persona_store import add_corpus_tag
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Curator", id="curator")
    with pytest.raises(ValueError, match="keysmash"):
        add_corpus_tag(cfg, "curator", _FYP)
    with pytest.raises(ValueError, match="generic engagement"):
        add_corpus_tag(cfg, "curator", "#instagood")
    with pytest.raises(ValueError, match="discovery"):
        add_corpus_tag(cfg, "curator", "#fyp")
    add_corpus_tag(cfg, "curator", "#bars")                       # a real one still lands
    assert "#bars" in P.Personas.load(cfg).get("curator").hashtag_corpus


def test_screen_corpus_reports_every_rejection():
    clean, rejected = screen_corpus(["#bars", _FYP, "#love", "#lyrics", "#fyp", "#bars"])
    assert clean == ["#bars", "#lyrics"]                          # normalized, deduped, order preserved
    assert set(rejected) == {_FYP, "#love", "#fyp"}
    assert all(r for r in rejected.values()), "every drop must carry a reason — the migration prints these"
