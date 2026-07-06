"""P2: a variation earns a post only when it is a COHERENT, EXPLAINABLE difference — each declares the
cheap-text AXIS it moves (hook_string|caption_angle|hook_placement) + a one-line rationale.
T1 = the schema/prompt/round-trip; T2 = the coherence gate. No new module — folds into caption.py."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, ClipState, Platform, CaptionSet, CaptionItem)
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions
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

def test_caption_prompt_no_longer_asks_for_hook_variation():
    # ROOT FIX: the per-surface hook variation (hook/axis/rationale) was removed from the caption gate;
    # the frame-seeing moment gate owns hooks now, so the caption prompt is hashtags-only.
    p = caption_prompt({"surfaces": [{"surface": "a/instagram", "platform": "instagram"}]})
    assert "rationale" not in p and "hooks_by_persona" not in p

def test_ingest_ignores_legacy_axis_and_rationale(tmp_path):
    # ROOT FIX: the caption gate no longer carries per-surface hook variation, so even a legacy response
    # with hook/axis/rationale is ignored -> stored None (the dormant machinery is a /ecc:prp-plan deeper fix).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="no warning. just impact.", hashtags=["#mohflow"],
                    hook="wait for the drop", axis="Hook String", rationale="different opening words")
    ]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["a/instagram"]
    assert mc["hook"] is None and mc["axis"] is None and mc["rationale"] is None
