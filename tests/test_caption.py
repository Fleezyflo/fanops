import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, Platform, CaptionSet, CaptionItem
from fanops.agentstep import response_path, request_path, latest_request_id
from fanops.caption import brand_risk_flag, request_captions, ingest_captions

def _clip(led, cfg):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))

def test_brand_risk_flags_offbrand_english():
    assert brand_risk_flag("sorry pls stream my song 🥺") is not None
    assert brand_risk_flag("link in bio, official drop from the label") is not None
    assert brand_risk_flag("no warning. just impact. 🔥") is None

def test_brand_risk_flags_offbrand_arabic():
    # FIX F33: Arabic begging/please-stream must be caught too.
    assert brand_risk_flag("اسمعوا الأغنية من فضلكم 🥺") is not None      # "please listen"
    assert brand_risk_flag("لينك في البايو") is not None                  # "link in bio"
    assert brand_risk_flag("ما في تحذير. بس تأثير.") is None              # clean bravado

def test_request_captions_writes_surfaces_and_language(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    surfaces = [("@a", Platform.instagram), ("@a", Platform.tiktok)]
    led = request_captions(led, cfg, "clip_1", surfaces)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert {s["surface"] for s in payload["surfaces"]} == {"@a/instagram", "@a/tiktok"}
    assert payload["transcript_excerpt"] == "they slept on me"
    assert payload["language"] == "en"
    assert led.clips["clip_1"].state is ClipState.captions_requested

def test_ingest_captions_clean_advances_and_stores(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    hashtags=["#mohflow"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    assert led.clips["clip_1"].state is ClipState.captioned
    assert led.clips["clip_1"].held is False
    assert led.clips["clip_1"].meta_captions["@a/instagram"]["caption"].startswith("no warning")

def test_ingest_captions_missing_surface_holds_not_default(tmp_path):
    # FIX F74: a response missing a requested surface must HOLD, not silently post a default.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="only IG was answered")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and "missing caption" in (c.held_reason or "")
    assert c.state is ClipState.held

def test_ingest_captions_offbrand_holds(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="pls stream 🥺 sorry")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and "bravado" in (c.held_reason or "")
    assert c.state is ClipState.held
