import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, SourceState, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem)
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

def test_ingest_captions_brandrisk_wins_over_missing(tmp_path):
    # When a caption is off-brand AND another surface is missing, the brand-risk reason wins.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="pls stream 🥺")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True
    assert "bravado" in (c.held_reason or "") and "missing caption" not in (c.held_reason or "")

def test_ingest_captions_multi_surface_clean_advances(tmp_path):
    # All requested surfaces answered, none off-brand -> captioned (completeness satisfied).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="@a/tiktok", caption="they slept. not anymore.")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.state is ClipState.captioned and c.held is False
    assert set(c.meta_captions) == {"@a/instagram", "@a/tiktok"}

def test_ingest_captions_stores_hashtags(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning.", hashtags=["#mohflow", "#fyp"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    assert led.clips["clip_1"].meta_captions["@a/instagram"]["hashtags"] == ["#mohflow", "#fyp"]

def test_ingest_captions_noop_without_response(tmp_path):
    # No response on disk -> ledger untouched, not held (stale/pending guard).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.state is ClipState.captions_requested and c.held is False

def _seed_clip_awaiting_captions(tmp_path, src_lang="en"):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided,
                          language=src_lang, transcript=[{"start":0,"end":1,"text":"x"}]))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-4", start=0, end=4,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.rendered))
    led = request_captions(led, cfg, "c1", [("@a", Platform.instagram)])
    return cfg, led

def test_caption_in_wrong_language_is_held(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="bonjour le monde", language="fr")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    assert "language" in (led.clips["c1"].held_reason or "").lower()

def test_caption_with_unknown_surface_key_is_held_with_specific_reason(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    # typo: '@accounts/instagram' instead of the requested '@a/instagram'
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@accounts/instagram", caption="hi", language="en")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    reason = (led.clips["c1"].held_reason or "")
    assert "@accounts/instagram" in reason     # names the BAD surface, not a generic "missing"

# --- C2 hardening (Phase C adversarial finding): the language match must normalize IETF tags ---
# A skeptic proved the naive exact-string `!=` HELD legitimate same-language captions whose tag
# carried a region subtag or different casing (en-US / EN / "en " vs en). That is a harmful
# false-positive: it blocks correct work and, for an autonomous run, silently wedges the clip.
import pytest

@pytest.mark.parametrize("item_lang", ["en-US", "EN", "en-GB", "en ", " en", "En"])
def test_caption_same_base_language_with_region_or_case_is_not_held(tmp_path, item_lang):
    # en-US / EN / en-GB / "en " are all ENGLISH — they must NOT be held against an `en` source.
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    language=item_lang)]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.captioned   # not a false-positive hold
    assert led.clips["c1"].held is False

def test_caption_genuine_mismatch_still_held_after_normalization(tmp_path):
    # Normalization must NOT weaken the real control: fr vs en still holds (regression guard).
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="bonjour le monde",
                    language="fr-FR")]).model_dump_json())   # region tag on a TRUE mismatch
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    assert "language" in (led.clips["c1"].held_reason or "").lower()
