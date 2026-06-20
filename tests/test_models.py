# tests/test_models.py
import pytest
from pydantic import ValidationError
from fanops.models import (
    Source, Moment, Clip, Post, Platform, SourceState, MomentState, ClipState, PostState,
    MomentRequest, MomentDecision, MomentPick,
    CaptionSet, CaptionItem,
)

def test_source_defaults_catalogued():
    s = Source(id="src_1", source_path="/s/x.mp4")
    assert s.state is SourceState.catalogued and s.transcript is None

def test_moment_hook_strategy_defaults_none():
    m = Moment(id="m1", parent_id="src_1", start=0.0, end=10.0, reason="x")
    assert m.hook_strategy is None                # M2: router annotation; old ledgers load (no migration)

def test_stitch_plan_defaults_suggested():
    from fanops.models import StitchPlan, StitchState
    sp = StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut")
    assert sp.state is StitchState.suggested      # born suggested (operator-approval gated)
    assert sp.asset_ids == [] and sp.plan_params == {}
    assert sp.base_fingerprint is None and sp.error_reason is None

def test_stitch_plan_id_is_deterministic_and_content_addressed():
    from fanops.models import stitch_plan_id
    a = stitch_plan_id("clip_1", ["asset_b", "asset_a"], "impact_cut", {"cut": 1.0})
    b = stitch_plan_id("clip_1", ["asset_a", "asset_b"], "impact_cut", {"cut": 1.0})   # asset order swapped
    c = stitch_plan_id("clip_1", ["asset_a"], "impact_cut", {"cut": 1.0})              # different asset set
    d = stitch_plan_id("clip_2", ["asset_a", "asset_b"], "impact_cut", {"cut": 1.0})   # different clip
    assert a == b                                 # deterministic + asset-order-independent (dedup key)
    assert a != c and a != d                      # different pairing / clip -> different id
    assert a.startswith("stitch_")

def test_unit_parent_chain():
    s = Source(id="src_1", source_path="/s/x.mp4")
    m = Moment(id="mom_1", parent_id=s.id, start=1.0, end=8.0,
               reason="punchline + beat drop", transcript_excerpt="they slept on me")
    c = Clip(id="clip_1", parent_id=m.id, path="/c/clip_1.mp4")
    p = Post(id="post_1", parent_id=c.id, account="@a", account_id="98432",
             platform=Platform.instagram, caption="x")
    assert m.parent_id == s.id and c.parent_id == m.id and p.parent_id == c.id

def test_moment_requires_reason():
    with pytest.raises(ValidationError):
        Moment(id="m", parent_id="src", start=0.0, end=5.0)  # no reason

def test_clip_hold_and_retire_are_first_class():
    c = Clip(id="c", parent_id="m", path="/c.mp4", held=True, held_reason="begging")
    assert c.held is True and c.held_reason == "begging"
    assert ClipState.held.value == "held" and ClipState.retired.value == "retired"

def test_post_failed_is_distinct_from_analyzed():
    assert PostState.failed.value == "failed"
    assert PostState.analyzed.value == "analyzed"
    assert PostState.failed is not PostState.analyzed

def test_post_carries_account_id_and_media():
    p = Post(id="p", parent_id="c", account="@a", account_id="98432",
             platform=Platform.tiktok, caption="x", media_urls=["https://h/v.mp4"])
    assert p.account_id == "98432" and p.media_urls == ["https://h/v.mp4"]

def test_every_unit_has_error_state():
    assert SourceState.error and MomentState.error and ClipState.error and PostState.error

def test_moment_request_carries_request_id():
    req = MomentRequest(source_id="src_1", request_id="r1", duration=42.0,
                        transcript=[{"start": 0.0, "end": 3.0, "text": "intro"}],
                        signal_peaks=[{"t": 16.0, "kind": "loudness"}])
    assert req.request_id == "r1"
    dec = MomentDecision(source_id="src_1", request_id="r1", picks=[
        MomentPick(start=14.0, end=21.0, reason="bar lands, beat drops",
                   transcript_excerpt="they slept on me")])
    assert dec.request_id == "r1" and dec.picks[0].end == 21.0

def test_caption_set_roundtrip():
    cs = CaptionSet(request_id="rc1", items=[CaptionItem(surface="@a/instagram",
                    caption="no warning. just impact.", hashtags=["#mohflow"])])
    assert cs.items[0].surface == "@a/instagram" and cs.request_id == "rc1"

def test_moment_pick_rejects_non_finite_timestamps():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            MomentPick(start=bad, end=5.0, reason="r")
        with pytest.raises(ValidationError):
            MomentPick(start=0.0, end=bad, reason="r")

def test_caption_item_has_optional_hook():
    from fanops.models import CaptionItem
    item = CaptionItem(surface="@a/instagram", caption="x", hashtags=[], language="en", hook="WATCH THIS")
    assert item.hook == "WATCH THIS"
    # optional: old payloads without hook still validate
    assert CaptionItem(surface="@a/instagram", caption="x").hook is None

def test_post_has_optional_variant_fields():
    from fanops.models import Post, Platform, PostState
    p = Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
             caption="x", state=PostState.queued, variant_key="vk1", variant_hook="WATCH THIS")
    assert p.variant_key == "vk1" and p.variant_hook == "WATCH THIS"
    # old ledgers (no variant fields) still load
    p2 = Post(id="p2", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
              caption="x", state=PostState.queued)
    assert p2.variant_key is None and p2.variant_hook is None

# ---- M1 (structural-hooks): asset origin (native vs third-party) ----
# origin_kind is a THIRD axis, distinct from source_origin (drop|url|scan, HOW it arrived) and the
# P1 creative-provenance (first_frame_kind, attribution). WRITE-ONCE at catalogue.
def test_source_origin_kind_defaults_native():
    s = Source(id="src_1", source_path="/s/x.mp4")          # old ledgers (no field) load as native
    assert s.origin_kind == "native"

def test_source_origin_kind_accepts_third_party():
    s = Source(id="src_1", source_path="/s/x.mp4", origin_kind="third_party")
    assert s.origin_kind == "third_party"

def test_source_origin_kind_rejects_unknown_value():
    with pytest.raises(ValidationError):                    # a Literal -> bad value rejected at construct/load
        Source(id="src_1", source_path="/s/x.mp4", origin_kind="borrowed")

def test_source_state_has_retired_and_discovered():
    # retired = removed (never deleted); discovered = a rebuild orphan, inert until an operator confirms
    assert SourceState.retired.value == "retired" and SourceState.discovered.value == "discovered"

# ---- content-lifecycle Phase 2: created_at (Source+Post) + published_at (Post) day-anchor ----
def test_source_post_created_at_default_none():
    # Optional[str]=None so an OLD-shape ledger row (no created_at/published_at) loads unchanged.
    s = Source(id="src_1", source_path="/s/x.mp4")
    assert s.created_at is None
    p = Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x")
    assert p.created_at is None and p.published_at is None
    # old-shape dicts (no new keys) validate
    s2 = Source.model_validate({"id": "src_2", "source_path": "/s/y.mp4"})
    p2 = Post.model_validate({"id": "p2", "parent_id": "c", "account": "@a", "account_id": "1",
                              "platform": "tiktok", "caption": "x"})
    assert s2.created_at is None and p2.created_at is None and p2.published_at is None

def test_source_post_created_at_round_trip():
    s = Source(id="src_1", source_path="/s/x.mp4", created_at="2026-06-19T10:00:00Z")
    p = Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
             caption="x", created_at="2026-06-19T11:00:00Z", published_at="2026-06-19T12:00:00Z")
    assert s.created_at == "2026-06-19T10:00:00Z"
    assert p.created_at == "2026-06-19T11:00:00Z" and p.published_at == "2026-06-19T12:00:00Z"
