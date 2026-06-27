# tests/test_hashtag_attribution_severance.py
"""INVARIANT (operator 2026-06-27): a hashtag is NEVER an attributed or learned dimension. A post's success
or failure attributes to the HOOK, the CLIP, and the ACCOUNT developed in the stitch — never to its hashtags.
Hashtags are judged ONLY by live Meta Graph reach (fanops_hashtags.refresh_store), a path that reads no ledger
and no post. These tests RED-fail if anyone reintroduces a hashtag term into the lift/learning weights, makes
lift_score depend on hashtags, or wires a learning module to read a post's hashtags to score/rank/weight it."""
import re
from pathlib import Path
from fanops.track import _W, lift_score

# The learning/scoring surface that must stay blind to hashtags.
_LEARNING_MODULES = ["track.py", "variant_learning.py", "variant_amplify.py", "variant_transfer.py",
                     "moment_hook_learning.py", "p4_dim_bias.py"]
_TAG_KEY = re.compile(r"hashtag|(^|_)tags?($|_)", re.IGNORECASE)


def test_lift_weights_carry_no_hashtag_dimension():
    # The objective weights are engagement signals only; no key attributes a post's outcome to a hashtag/tag.
    assert not any(_TAG_KEY.search(k) for k in _W), f"a hashtag/tag dimension leaked into _W: {_W}"
    assert {"saves", "shares", "retention", "reach", "likes"} <= set(_W)   # the engagement signals are present


def test_lift_score_is_independent_of_hashtags():
    # lift_score is a pure function of the metrics dict — hashtags are not an input, so a hashtags field
    # smuggled into a metrics row must NOT change the score (the whitelist drops unknown keys). Severance at
    # the scoring primitive: the same post earns the same lift no matter what tags it carried.
    metrics = {"saves": 10, "shares": 5, "retention": 0.4, "reach": 9000, "likes": 100}
    assert lift_score({**metrics, "hashtags": ["#viral", "#hiphop"]}) == lift_score(metrics)


def test_no_learning_module_attributes_a_post_outcome_to_hashtags():
    # SOURCE GUARD: the deleted own-reach attribution must never return, and no learning/scoring module may
    # read a post's `.hashtags` to score/rank/weight it. (The Graph-reach judge lives in fanops_hashtags, not
    # here, and reads no post.)
    src = Path(__file__).resolve().parents[1] / "src" / "fanops"
    for name in _LEARNING_MODULES:
        text = (src / name).read_text()
        assert "tag_reach_means" not in text, f"{name} references the deleted own-reach attribution"
        assert "rank_tags_by_reach" not in text, f"{name} references the deleted own-reach attribution"
        assert ".hashtags" not in text, f"{name} reads a post's hashtags — attribution leak"
