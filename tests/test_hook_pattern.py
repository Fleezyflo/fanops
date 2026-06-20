"""P1: capture the hook PATTERN (which of the 6 _hook_spec formulas the responder/editor chose). It is
the dim P4 ranks FIRST, so it must be REAL — declared by the LLM, normalized to a canonical key, and
persisted on the Moment (one writer = moments ingest). Unknown/absent -> None (never crashes)."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, MomentPick, MomentDecision
from fanops.hookcheck import normalize_hook_pattern, HOOK_PATTERNS
from fanops.moments import ingest_moments
from fanops.prompts import moment_prompt


def test_normalize_accepts_canonical_keys():
    for k in HOOK_PATTERNS:
        assert normalize_hook_pattern(k) == k

def test_normalize_maps_synonyms_and_formatting():
    assert normalize_hook_pattern("Open Loop") == "open_loop"
    assert normalize_hook_pattern("curiosity-gap") == "curiosity"
    assert normalize_hook_pattern("comment/opinion") == "comment_bait"
    assert normalize_hook_pattern("POV / relatable") == "pov"

def test_normalize_unknown_or_empty_is_none():
    assert normalize_hook_pattern("vibes") is None
    assert normalize_hook_pattern("") is None
    assert normalize_hook_pattern(None) is None
    assert normalize_hook_pattern(123) is None

def test_ingest_moments_persists_hook_pattern(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), duration=120.0))
    dec = MomentDecision(source_id="s1", request_id="r1", picks=[
        MomentPick(start=10, end=28, reason="the punchline lands", hook="wait for the drop",
                   hook_pattern="open_loop")])
    mocker.patch("fanops.moments.read_response", return_value=dec)
    led = ingest_moments(led, cfg, "s1")
    m = [m for m in led.moments.values() if m.parent_id == "s1"][0]
    assert m.hook == "wait for the drop"
    assert m.hook_pattern == "open_loop"

def test_ingest_moments_pattern_none_when_hook_rejected(tmp_path, mocker):
    # v2: a MECHANICALLY-rejected hook (exact cross-clip duplicate) is nulled by hookcheck -> its
    # pattern must be None too (quality slop is no longer rejected here; the critic owns that).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), duration=120.0))
    led.add_moment(Moment(id="m_other", parent_id="s_other", state=MomentState.decided,
                          start=0, end=5, reason="r", hook="wait for the drop"))   # a prior clip used this
    dec = MomentDecision(source_id="s1", request_id="r1", picks=[
        MomentPick(start=10, end=28, reason="r", hook="wait for the drop", hook_pattern="contrarian")])
    mocker.patch("fanops.moments.read_response", return_value=dec)
    led = ingest_moments(led, cfg, "s1")
    m = [m for m in led.moments.values() if m.parent_id == "s1"][0]
    assert m.hook is None and m.hook_pattern is None

def test_moment_prompt_asks_for_hook_pattern():
    p = moment_prompt({"duration": 120.0, "clip_profile": "talk"})
    assert "hook_pattern" in p
    for k in HOOK_PATTERNS:
        assert k in p
