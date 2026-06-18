"""P2: a variation earns a post only when it is a COHERENT, EXPLAINABLE difference — each declares the
cheap-text AXIS it moves (hook_pattern|hook_string|caption_angle|hook_placement) + a one-line rationale.
T1 = the schema/prompt/round-trip; T2 = the coherence gate. No new module — folds into caption.py."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, ClipState, Platform, CaptionSet, CaptionItem)
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions, normalize_variation_axis, VARIATION_AXES
from fanops.prompts import caption_prompt


def _clip(led, cfg):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))


def test_caption_item_carries_axis_and_rationale():
    it = CaptionItem(surface="@a/instagram", caption="x", axis="hook_pattern", rationale="contrarian angle")
    assert it.axis == "hook_pattern" and it.rationale == "contrarian angle"

def test_caption_item_axis_defaults_none():
    it = CaptionItem(surface="@a/instagram", caption="x")
    assert it.axis is None and it.rationale is None

def test_normalize_variation_axis_known_and_unknown():
    for a in VARIATION_AXES:
        assert normalize_variation_axis(a) == a
    assert normalize_variation_axis("Hook Pattern") == "hook_pattern"
    assert normalize_variation_axis("hook-placement") == "hook_placement"
    assert normalize_variation_axis("vibes") is None
    assert normalize_variation_axis(None) is None

def test_caption_prompt_asks_for_axis_and_rationale():
    p = caption_prompt({"surfaces": [{"surface": "@a/instagram", "platform": "instagram"}]})
    assert "axis" in p and "rationale" in p
    for a in VARIATION_AXES:
        assert a in p

def test_ingest_round_trips_axis_and_rationale(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.", hashtags=["#mohflow"],
                    hook="wait for the drop", axis="Hook Pattern", rationale="open-loop tease")
    ]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]
    assert mc["axis"] == "hook_pattern"                  # normalized
    assert mc["rationale"] == "open-loop tease"


# --- P2 T2: the coherence gate (clean beats noise) ----------------------------------------------
from fanops.caption import coherent_variation

def test_gate_drops_rationale_less_variant():
    assert coherent_variation("the part nobody clipped", "", siblings=set()) is False
    assert coherent_variation("the part nobody clipped", None, siblings=set()) is False

def test_gate_drops_template_cluster_variant():
    # v2: the floor is MECHANICAL only — it still drops a variant that clusters on an opening template
    # already used by siblings (the 'reads like a bot' tell).
    sibs = {"wait for the beat drop", "wait for the last line"}     # a 'wait for' cluster
    assert coherent_variation("wait for the hometown line", "open loop", siblings=sibs) is False

def test_gate_allows_formerly_semantic_slop_now_critic_owns_it():
    # v2 scope (accepted trade): the semantic backstop moved to the reasoning critic, which does NOT
    # run on per-surface caption siblings — so coherent_variation no longer drops 'his hardest bar'
    # (mechanical floor only). Documented in caption.coherent_variation; locked here so it's intentional.
    assert coherent_variation("his hardest bar", "contrarian take", siblings=set()) is True

def test_gate_drops_near_duplicate_of_a_sibling():
    assert coherent_variation("wait for the drop", "open loop", siblings={"wait for the drop"}) is False

def test_gate_keeps_distinct_onbrand_justified_variant():
    assert coherent_variation("the part nobody clipped", "curiosity angle",
                              siblings={"wait for the drop"}) is True

def test_gate_drops_empty_hook():
    assert coherent_variation("", "has a reason", siblings=set()) is False
    assert coherent_variation(None, "has a reason", siblings=set()) is False
