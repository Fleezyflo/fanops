"""Content-aware hashtags: a clip's tags must derive from THAT clip's information (its transcript),
survive vetting, and carry a provenance `source` for every shipped tag. Captions stay hashtags-only.

These pin the pure hashtags.py seams (the extractor + the `content=` admit/slot + the traced provenance).
The `content=None` cases are the FIREWALL — they must be byte-identical to today's vet_hashtags.
`STORE` stands in for the measurement cache as an ordered menu: membership is the cache UNION the corpus
UNION the content candidates, so a test that needs four slots filled must supply one."""
import pytest
from fanops.models import Platform
from fanops import hashtags as H
from fanops.hashtags import vet_hashtags, content_tag_candidates, vet_hashtags_traced

STORE = ["#hiphop", "#rap", "#bars", "#newmusic", "#rapmusic", "#viral"]   # the measured menu, metric-ranked


# ---- Task 1: the pure content extractor --------------------------------------------------------------
def test_content_candidates_extract_from_transcript():
    cands = content_tag_candidates("a fiery diss track about loyalty and loyalty forever")
    assert "#loyalty" in cands and "#diss" in cands and "#fiery" in cands
    assert "#loyalty" == cands[0]                      # frequency-first (appears twice)
    assert "#a" not in cands and "#and" not in cands   # stopwords / too-short dropped


@pytest.mark.parametrize("text", ["", None, "   ", "أغنية عربية فقط", "###", "12 34 5"])
def test_content_candidates_empty_for_blank_or_nonlatin(text):
    assert content_tag_candidates(text) == []          # nothing latin/usable -> [] -> byte-identity path


def test_content_candidates_drop_url_tech_junk():
    # a transcript's most-frequent token must not be a URL/tech word forced into the caption (review MEDIUM).
    cands = content_tag_candidates("http http www mp3 beats fire fire")
    assert "#http" not in cands and "#www" not in cands and "#mp3" not in cands
    assert "#fire" in cands and "#beats" in cands


def test_content_candidates_are_bounded_and_normalized():
    cands = content_tag_candidates(" ".join(f"word{i}" for i in range(50)), max_n=6)
    assert len(cands) <= 6
    assert all(c.startswith("#") and c == c.lower() for c in cands)


# ---- Task 2: vet_hashtags(content=) joins membership + reserves a slot --------------------------------
def test_content_tag_labels_measured_pick():
    # MOL-635 residual: content ∩ measured — unmeasured ASR tokens do NOT expand membership.
    # A measured tag that is ALSO a content candidate ships with content provenance (fit signal).
    assert "#diss" not in STORE
    assert "#diss" not in vet_hashtags(["#diss"], Platform.instagram, None, store=STORE, content=["#diss"])
    out = vet_hashtags(["#newmusic"], Platform.instagram, None, store=STORE, content=["#newmusic"])
    assert "#newmusic" in out
    _, sources = vet_hashtags_traced(["#newmusic"], Platform.instagram, None, store=STORE, content=["#newmusic"])
    assert sources.get("#newmusic") == "content"


@pytest.mark.parametrize("corpus", [None, ["#lyrics", "#bars", "#newmusic"], ["#freestyle", "#undergroundhiphop", "#trap"], ["#viral", "#rapmusic", "#hiphop"], ["#customtag"]])
def test_content_none_is_byte_identical(corpus):
    # FIREWALL: content=None must reproduce today's output exactly, across corpus combos.
    tags = ["#rap", "#bars", "#nonsense"]
    base = vet_hashtags(tags, Platform.tiktok, "en", store=STORE, corpus=corpus)
    withc = vet_hashtags(tags, Platform.tiktok, "en", store=STORE, corpus=corpus, content=None)
    assert base == withc


def test_content_does_not_reserve_when_the_measured_menu_fills_four():
    # content reservation deleted: model fills all 4 with measured tags; content does not displace.
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "en",
                       store=STORE, content=["#loyalty"])
    assert "#loyalty" not in out and len(out) == 4


def test_arabic_region_floor_still_wins_over_content():
    # an Arabic clip under a corpus keeps its region tag; content no longer reserves a competing floor.
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "ar",
                       store=STORE, corpus=["#viral", "#rapmusic", "#hiphop"], content=["#loyalty"])
    assert any(t in set(H._ARABIC) for t in out)       # region reach preserved
    assert "#loyalty" not in out                       # content reservation deleted


# ---- Task 3: provenance -- every shipped tag traces to a real signal ----------------------------------
def test_every_kept_tag_has_a_source():
    # content=#newmusic is measured (in STORE); unmeasured #diss cannot join.
    tags, sources = vet_hashtags_traced(["#newmusic", "#rap"], Platform.tiktok, "en", store=STORE,
                                        corpus=["#viral", "#rapmusic", "#hiphop", "#customtag"],
                                        content=["#newmusic", "#diss"])
    assert set(sources) == set(tags)                   # one source per shipped tag
    assert all(sources[t] for t in tags)               # none empty/sourceless
    assert set(sources.values()) <= {"content", "corpus", "region", "graph-reach"}
    assert "#diss" not in tags


def test_source_priority_content_over_the_measured_menu():
    # a tag that is BOTH a content candidate AND a measured cache tag is credited to content.
    tags, sources = vet_hashtags_traced(["#newmusic"], Platform.instagram, "en", store=STORE,
                                        content=["#newmusic"])
    assert sources.get("#newmusic") == "content"


def test_traced_list_matches_plain_vet():
    # DRY contract: the traced list == the plain list for identical inputs.
    kw = dict(store=STORE, corpus=["#freestyle", "#undergroundhiphop", "#trap", "#customtag"],
              content=["#bars"])  # measured
    plain = vet_hashtags(["#bars"], Platform.tiktok, "en", **kw)
    traced, _ = vet_hashtags_traced(["#bars"], Platform.tiktok, "en", **kw)
    assert plain == traced


# ---- MOL-76: the content FLOOR is brand-risk screened before it ships --------------------------------
# The content floor force-inserts the top transcript token as a hashtag. Raw ASR from a rap/hip-hop
# catalogue can surface an off-brand word; brand_risk_flag (caption.py's one content guard) gates the
# caption but NEVER the hashtag candidates. These pin the wiring fix: an off-brand content candidate is
# dropped BEFORE the vetted/preferred sets + the reserved-floor promotion, and backfill still fills the line.
def test_offbrand_content_candidate_never_ships_even_as_floor():
    # "begging" trips brand_risk_flag's default \bbeg(ging)?\b; it is the top content token and would win
    # the content-floor slot. It must NOT appear in the output, and the line still fills to 4 via backfill.
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "en",
                       store=STORE, content=["#begging", "#loyalty"])
    assert "#begging" not in out                       # off-brand content tag screened out of the floor
    assert len(out) == 4                               # backfill guarantees a non-empty, full line


def test_offbrand_content_candidate_not_admitted_to_membership():
    # even when the model itself "picks" the off-brand content word, it must not survive vetting via the
    # content= membership join (the floor screen is at the single choke point, not just the reserved slot).
    out = vet_hashtags(["#begging"], Platform.tiktok, "en", store=STORE, content=["#begging"])
    assert "#begging" not in out


def test_clean_content_still_ships_when_model_picks_it():
    # content ∩ measured: a measured content tag the model picks ships (provenance=content); unmeasured dies.
    out = vet_hashtags(["#bars", "#hiphop", "#rap", "#newmusic"], Platform.instagram, "en",
                       store=STORE, content=["#bars", "#loyalty"])
    assert "#bars" in out and "#loyalty" not in out and len(out) == 4


def test_offbrand_screen_drops_content_from_membership():
    # off-brand content screened; unmeasured #loyalty cannot join even if model-picked.
    out = vet_hashtags(["#loyalty", "#hiphop", "#rap", "#bars"], Platform.instagram, "en",
                       store=STORE, content=["#begging", "#loyalty", "#bars"])
    assert "#begging" not in out
    assert "#loyalty" not in out                       # unmeasured — content ∩ measured
    assert "#bars" in out                              # measured + content + model pick


# ---- Task 4: content reaches the POSTED line through request/ingest (the crux) ------------------------
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, CaptionSet
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions
from fanops.config import Config


# ---- MOL-642: content_tags wired through request / prompt / ingest ---------------------------------
# content_tags ride the payload + prompt as a fit signal; ingest passes content= so model-picked
# content tags survive membership. Seed fallback (items:[]) still has no content floor.

def test_request_payload_embeds_content_tags(tmp_path):
    """request_captions embeds content_tags derived from the transcript (MOL-642)."""
    from fanops.agentstep import request_path
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_x", parent_id="src_1", content_token="mom_x", start=0, end=7,
                          reason="r", transcript_excerpt="fiery diss track about loyalty"))
    led.add_clip(Clip(id="clip_x", parent_id="mom_x", path="/c.mp4", state=ClipState.rendered))
    import json
    request_captions(led, cfg, "clip_x", [("a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_x").read_text())
    assert "#loyalty" in payload.get("content_tags", [])
    assert "#diss" in payload.get("content_tags", [])


def test_pipeline_model_pick_content_survives_ingest(tmp_path):
    """Model-picked MEASURED content tag ships with source=content (MOL-642 + residual tighten)."""
    from fanops.models import CaptionItem
    from fanops.controlio import write_json_atomic
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_json_atomic(cfg.hashtags_path, {
        "#loyalty": {"play_count": 100, "like_count": 10, "measured_at": "2026-07-28T00:00:00+00:00",
                     "from": {"#loyalty": 2}},
        "#hiphop": {"play_count": 50, "like_count": 5, "measured_at": "2026-07-28T00:00:00+00:00",
                    "from": {"#hiphop": 2}}})
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_x", parent_id="src_1", content_token="mom_x", start=0, end=7,
                          reason="r", transcript_excerpt="fiery diss track about loyalty"))
    led.add_clip(Clip(id="clip_x", parent_id="mom_x", path="/c.mp4", state=ClipState.rendered))
    # Seed a surface store so membership includes #loyalty (persona-aligned menus normally do).
    led = request_captions(led, cfg, "clip_x", [("a", Platform.instagram)])
    # Patch the request surface store to the measured menu (no persona → store omitted).
    from fanops.agentstep import request_path
    req = json.loads(request_path(cfg, "captions", "clip_x").read_text())
    for s in req["surfaces"]:
        s["hashtag_store"] = ["#loyalty", "#hiphop"]
    request_path(cfg, "captions", "clip_x").write_text(json.dumps(req))
    rid = latest_request_id(cfg, "captions", "clip_x")
    response_path(cfg, "captions", "clip_x").write_text(
        CaptionSet(request_id=rid, items=[
            CaptionItem(surface="a/instagram", caption="#loyalty #hiphop", language="en",
                        hashtags=["#loyalty", "#hiphop"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_x")
    entry = led.clips["clip_x"].meta_captions["a/instagram"]
    assert "#loyalty" in entry["hashtags"]
    assert entry["tag_sources"].get("#loyalty") == "content"


def test_caption_prompt_renders_content_block(tmp_path):
    """caption_prompt renders clip-specific content tags when present (MOL-642)."""
    from fanops.prompts import caption_prompt
    base = {"surfaces": [{"surface": "a/instagram", "platform": "instagram"}], "language": "en"}
    out_with = caption_prompt({**base, "content_tags": ["#diss", "#loyalty"]})
    out_without = caption_prompt(base)
    assert "clip-specific" in out_with.lower()
    assert "#diss" in out_with and "#loyalty" in out_with
    assert "clip-specific" not in out_without.lower()
    assert out_with != out_without


def _seed(led, *, clip_id, mom_id, transcript):
    led.add_moment(Moment(id=mom_id, parent_id="src_1", content_token=mom_id, start=0, end=7,
                          reason="r", transcript_excerpt=transcript))
    led.add_clip(Clip(id=clip_id, parent_id=mom_id, path="/c.mp4", state=ClipState.rendered))


def _ingest_empty(led, cfg, clip_id):
    # the 83% case: the model soft-refuses (items:[]) -> seed fallback. Content must STILL reach the line.
    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid, items=[]).model_dump_json())
    return ingest_captions(led, cfg, clip_id)


def test_two_clips_seed_fallback_empty_without_corpus(tmp_path):
    # No content floor: seed fallback (items:[]) with cold cache still ships empty — content
    # only admits model picks. Different transcripts still diverge in the REQUEST payload.
    from fanops.agentstep import request_path
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="a fiery diss track about betrayal")
    _seed(led, clip_id="clip_b", mom_id="mom_b", transcript="a tender lullaby about devotion")
    led = _ingest_empty(led, cfg, "clip_a")
    led = _ingest_empty(led, cfg, "clip_b")
    a = led.clips["clip_a"].meta_captions["a/instagram"]["hashtags"]
    b = led.clips["clip_b"].meta_captions["a/instagram"]["hashtags"]
    assert a == b == []                                        # honest cold-cache floor: empty, not padded
    pa = json.loads(request_path(cfg, "captions", "clip_a").read_text())
    pb = json.loads(request_path(cfg, "captions", "clip_b").read_text())
    assert pa.get("content_tags") != pb.get("content_tags")


def test_seed_fallback_entry_carries_tag_sources(tmp_path):
    # Seed fallback with empty picks: no content floor → no content source (membership-only channel).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="a fiery diss track about betrayal")
    led = _ingest_empty(led, cfg, "clip_a")
    entry = led.clips["clip_a"].meta_captions["a/instagram"]
    assert set(entry["tag_sources"]) == set(entry["hashtags"])  # one source per shipped tag
    assert all(entry["tag_sources"].values())                  # none empty/sourceless
    assert "content" not in entry["tag_sources"].values()      # no model pick → no content source


# ---- Task 5: the prompt offers the clip's content tags (byte-identical without) ----------------------
from fanops.prompts import caption_prompt

_BASE_PAYLOAD = {"surfaces": [{"surface": "a/instagram", "platform": "instagram"}], "language": "en"}


def test_prompt_renders_content_tags_key_in_payload():
    # MOL-642: content_tags in the payload appear as a clip-specific fit signal.
    out_with = caption_prompt({**_BASE_PAYLOAD, "content_tags": ["#diss", "#loyalty"]})
    out_without = caption_prompt(_BASE_PAYLOAD)
    assert out_with != out_without
    assert "#diss" in out_with and "#loyalty" in out_with
    assert "clip-specific" in out_with.lower()


def test_prompt_byte_identical_without_content():
    out = caption_prompt(_BASE_PAYLOAD)
    assert "do not invent" in out.lower()                       # menu-only rule (MOL-174: reach × clip relevance)
    assert "clip-specific" not in out.lower()                   # no content block when absent


def test_contentless_clip_is_byte_identical(tmp_path):
    # an empty-transcript clip ships the same seed line as before this feature (firewall).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="")
    led = _ingest_empty(led, cfg, "clip_a")
    tags = led.clips["clip_a"].meta_captions["a/instagram"]["hashtags"]
    assert tags == vet_hashtags(None, Platform.instagram, "en")



def test_prompt_annotates_menu_with_hashtag_metrics():
    """MOL-636 + MOL-692 + MOL-976: menu entries carry the platform numbers. Tie-break is earlier
    menu entry when fit is equal — never a volume-order claim, never unlike-unit arithmetic."""
    out = caption_prompt({**_BASE_PAYLOAD,
                          "surfaces": [{"surface": "a/instagram", "platform": "instagram",
                                        "hashtag_store": ["#hiphop"]}],
                          "hashtag_metrics": {"#hiphop": {"media_count": 9000.0, "play_count": 120.0,
                                                          "current_top_reel_play_max_7d": 77.0}}})
    assert "media_count" in out and "9000" in out and "77" in out
    assert "BIGGEST FIRST" not in out
    assert "prefer earlier menu entries when fit is equal" in out
    assert "keep the earlier entry" in out
    assert "do not add or average them" in out.lower()


def test_request_payload_hashtag_metrics_sidecar(tmp_path):
    """MOL-636: request keeps hashtag_store as list[str] and adds hashtag_metrics sidecar."""
    from fanops.agentstep import request_path
    from fanops.controlio import write_json_atomic
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_json_atomic(cfg.hashtags_path, {
        "#hiphop": {"play_count": 5000, "like_count": 80, "media_count": 12,
                    "measured_at": "2026-07-28T00:00:00+00:00", "from": {"#hiphop": 2}}})
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_x", parent_id="src_1", content_token="mom_x", start=0, end=7,
                          reason="r", transcript_excerpt="bars"))
    led.add_clip(Clip(id="clip_x", parent_id="mom_x", path="/c.mp4", state=ClipState.rendered))
    # No persona aligned store → metrics may be empty; still store stays list type when present.
    request_captions(led, cfg, "clip_x", [("a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_x").read_text())
    for s in payload["surfaces"]:
        if "hashtag_store" in s:
            assert all(isinstance(t, str) for t in s["hashtag_store"])


def test_clip_make_tag_sources_from_source_not_persona_corpus(tmp_path):
    """Seed fallback tag_sources trace source high-count related tags (corpus/content), not persona monopoly."""
    from fanops.agentstep import request_path
    from fanops.controlio import write_json_atomic
    from fanops.accounts import Accounts, Account
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_json_atomic(cfg.hashtags_path, {
        "#diss": {"graph_id": "g1", "play_count": 100, "like_count": 10, "media_count": 9_000_000.0,
                  "measured_at": "2026-07-28T00:00:00+00:00"},
        "#loyalty": {"graph_id": "g2", "play_count": 80, "like_count": 8, "media_count": 50_000.0,
                     "measured_at": "2026-07-28T00:00:00+00:00"},
        "#personaone": {"graph_id": "g3", "play_count": 10, "like_count": 1, "media_count": 100.0,
                        "measured_at": "2026-07-28T00:00:00+00:00"},
        "#personatwo": {"graph_id": "g4", "play_count": 10, "like_count": 1, "media_count": 50.0,
                        "measured_at": "2026-07-28T00:00:00+00:00"},
        "#personathree": {"graph_id": "g5", "play_count": 10, "like_count": 1, "media_count": 10.0,
                          "measured_at": "2026-07-28T00:00:00+00:00"},
    })
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_x", parent_id="src_1", content_token="mom_x", start=0, end=7,
                          reason="r", transcript_excerpt="fiery diss track about loyalty"))
    led.add_clip(Clip(id="clip_x", parent_id="mom_x", path="/c.mp4", state=ClipState.rendered))
    accts = Accounts(cfg)
    accts.accounts = [Account(handle="a", platforms=[Platform.instagram],
                              hashtag_corpus=["#personaone", "#personatwo", "#personathree"])]
    led = request_captions(led, cfg, "clip_x", [("a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_x").read_text())
    assert "#diss" in payload["surfaces"][0]["corpus"]
    assert "#personaone" not in payload["surfaces"][0]["corpus"]
    rid = latest_request_id(cfg, "captions", "clip_x")
    response_path(cfg, "captions", "clip_x").write_text(CaptionSet(request_id=rid, items=[]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_x")
    entry = led.clips["clip_x"].meta_captions["a/instagram"]
    sources = entry["tag_sources"]
    assert "#diss" in entry["hashtags"] and sources.get("#diss") in ("corpus", "content")
    assert all(t not in entry["hashtags"] for t in ("#personaone", "#personatwo", "#personathree"))
    assert "corpus" in sources.values() or "content" in sources.values()
    assert len([t for t in entry["hashtags"] if sources.get(t) == "corpus"]) <= 3  # _CORPUS_LEAD_MAX


# ---- Task 6: Review surfaces the per-tag provenance (read-only) ---------------------------------------
def _surface_post(**kw):
    from fanops.studio.views_review import SurfacePost
    base = dict(post_id="p1", account="a", platform="instagram", persona=None, caption="#diss #fyp",
                hashtags=["#diss", "#fyp"], scheduled_time=None, media_url="/m", state="awaiting_approval",
                imminent=False, editable=True)
    return SurfacePost(**{**base, **kw})


def test_surface_edit_renders_tag_source_chips(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path))
    sp = _surface_post(tag_sources={"#diss": "content", "#hiphop": "graph-reach"})
    with app.test_request_context():
        html = app.jinja_env.get_template("_surface_edit.html").render(s=sp, backend="dryrun")
    assert "#diss" in html and "content" in html and "tag-src" in html   # the provenance chip renders


def test_surface_edit_no_chip_row_without_sources(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path))
    sp = _surface_post(tag_sources={})
    with app.test_request_context():
        html = app.jinja_env.get_template("_surface_edit.html").render(s=sp, backend="dryrun")
    assert "tag-prov" not in html                                       # legacy/absent -> no row, no clutter
