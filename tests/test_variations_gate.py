"""P2: a variation earns a post only when it is a COHERENT, EXPLAINABLE difference — each declares the
cheap-text AXIS it moves (hook_string|caption_angle|hook_placement) + a one-line rationale.
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


def test_caption_item_schema_drops_dead_hook_axis_rationale():
    # AGENT-7: the caption gate is hashtags-only, so the LLM --json-schema must NOT offer hook/axis/rationale
    # (dead fields the model could otherwise author into a hashtags-only gate). Pydantic ignores them on an
    # old on-disk response, but the SCHEMA we hand claude -p no longer lists them.
    props = CaptionItem.model_json_schema()["properties"]
    assert "hook" not in props and "axis" not in props and "rationale" not in props
    assert "caption" in props and "hashtags" in props and "surface" in props   # the live surface is intact

def test_normalize_variation_axis_known_and_unknown():
    for a in VARIATION_AXES:
        assert normalize_variation_axis(a) == a
    assert normalize_variation_axis("Hook String") == "hook_string"
    assert normalize_variation_axis("hook-placement") == "hook_placement"
    assert normalize_variation_axis("vibes") is None
    assert normalize_variation_axis(None) is None

def test_caption_prompt_no_longer_asks_for_hook_variation():
    # ROOT FIX: the per-surface hook variation (hook/axis/rationale) was removed from the caption gate;
    # the frame-seeing moment gate owns hooks now, so the caption prompt is hashtags-only.
    p = caption_prompt({"surfaces": [{"surface": "@a/instagram", "platform": "instagram"}]})
    assert "rationale" not in p and "hooks_by_persona" not in p

def test_ingest_ignores_legacy_axis_and_rationale(tmp_path):
    # ROOT FIX: the caption gate no longer carries per-surface hook variation, so even a legacy response
    # with hook/axis/rationale is ignored -> stored None (the dormant machinery is a /ecc:prp-plan deeper fix).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.", hashtags=["#mohflow"],
                    hook="wait for the drop", axis="Hook String", rationale="different opening words")
    ]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]
    assert mc["hook"] is None and mc["axis"] is None and mc["rationale"] is None


# --- P2 T2: the coherence gate (clean beats noise) ----------------------------------------------
from fanops.caption import coherent_variation

def test_gate_drops_rationale_less_variant():
    assert coherent_variation("the part nobody clipped", "", siblings=set()) is False
    assert coherent_variation("the part nobody clipped", None, siblings=set()) is False

def test_gate_drops_template_cluster_variant():
    # v2: the floor is MECHANICAL only — it still drops a variant that clusters on an opening template
    # already used by siblings (the 'reads like a bot' tell). v2.1 tune: the cluster threshold is 3
    # siblings sharing the first THREE words (was 2/2 — it over-fired on good distinct openers).
    sibs = {"wait for the beat drop", "wait for the last line", "wait for the hometown bar"}  # a 'wait for the' cluster
    assert coherent_variation("wait for the final verse", "open loop", siblings=sibs) is False

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
