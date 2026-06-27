"""Content-aware hashtags: a clip's tags must derive from THAT clip's information (its transcript),
survive vetting, and carry a provenance `source` for every shipped tag. Captions stay hashtags-only.

These pin the pure hashtags.py seams (the extractor + the `content=` admit/slot + the traced provenance).
The `content=None` cases are the FIREWALL — they must be byte-identical to today's vet_hashtags."""
import pytest
from fanops.models import Platform
from fanops import hashtags as H
from fanops.hashtags import vet_hashtags, content_tag_candidates, vet_hashtags_traced


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
def test_content_tag_survives_vetting():
    # a content tag the model picked is NOT in VETTED; today it is dropped. With content= it survives.
    assert "#diss" not in H.VETTED
    out = vet_hashtags(["#diss"], Platform.instagram, None, content=["#diss"])
    assert "#diss" in out


@pytest.mark.parametrize("corpus", [None, ["#lyrics", "#bars", "#newmusic"], ["#freestyle", "#undergroundhiphop", "#trap"], ["#viral", "#rapmusic", "#hiphop"], ["#customtag"]])
def test_content_none_is_byte_identical(corpus):
    # FIREWALL: content=None must reproduce today's output exactly, across corpus combos.
    tags = ["#rap", "#bars", "#nonsense"]
    base = vet_hashtags(tags, Platform.tiktok, "en", corpus=corpus)
    withc = vet_hashtags(tags, Platform.tiktok, "en", corpus=corpus, content=None)
    assert base == withc


def test_content_floor_reserves_one_slot_when_reach_fills_four():
    # model fills all 4 with reach tags; a content tag still claims exactly one slot.
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "en",
                       content=["#loyalty"])
    assert "#loyalty" in out and len(out) == 4


def test_arabic_region_floor_still_wins_over_content():
    # an Arabic clip under a lean keeps its region tag AND gets a content tag (both floors satisfied).
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "ar",
                       corpus=["#viral", "#rapmusic", "#hiphop"], content=["#loyalty"])
    assert any(t in set(H._ARABIC) for t in out)       # region reach preserved
    assert "#loyalty" in out


# ---- Task 3: provenance -- every shipped tag traces to a real signal ----------------------------------
def test_every_kept_tag_has_a_source():
    tags, sources = vet_hashtags_traced(["#diss", "#rap"], Platform.tiktok, "en",
                                        corpus=["#viral", "#rapmusic", "#hiphop", "#customtag"], content=["#diss"])
    assert set(sources) == set(tags)                   # one source per shipped tag
    assert all(sources[t] for t in tags)               # none empty/sourceless


def test_source_priority_content_over_reach():
    # a tag that is BOTH a content candidate AND a reach/genre tag is credited to content.
    tags, sources = vet_hashtags_traced(["#newmusic"], Platform.instagram, "en",
                                        content=["#newmusic"])
    assert sources.get("#newmusic") == "content"


def test_traced_list_matches_plain_vet():
    # DRY contract: the traced list == the plain list for identical inputs.
    kw = dict(corpus=["#freestyle", "#undergroundhiphop", "#trap", "#customtag"], content=["#loyalty"])
    plain = vet_hashtags(["#diss"], Platform.tiktok, "en", **kw)
    traced, _ = vet_hashtags_traced(["#diss"], Platform.tiktok, "en", **kw)
    assert plain == traced


# ---- Task 4: content reaches the POSTED line through request/ingest (the crux) ------------------------
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, CaptionSet
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions
from fanops.config import Config


def _seed(led, *, clip_id, mom_id, transcript):
    led.add_moment(Moment(id=mom_id, parent_id="src_1", content_token=mom_id, start=0, end=7,
                          reason="r", transcript_excerpt=transcript))
    led.add_clip(Clip(id=clip_id, parent_id=mom_id, path="/c.mp4", state=ClipState.rendered))


def _ingest_empty(led, cfg, clip_id):
    # the 83% case: the model soft-refuses (items:[]) -> seed fallback. Content must STILL reach the line.
    led = request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid, items=[]).model_dump_json())
    return ingest_captions(led, cfg, clip_id)


def test_two_clips_one_persona_diverge_on_content(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="a fiery diss track about betrayal")
    _seed(led, clip_id="clip_b", mom_id="mom_b", transcript="a tender lullaby about devotion")
    led = _ingest_empty(led, cfg, "clip_a")
    led = _ingest_empty(led, cfg, "clip_b")
    a = led.clips["clip_a"].meta_captions["@a/instagram"]["hashtags"]
    b = led.clips["clip_b"].meta_captions["@a/instagram"]["hashtags"]
    assert a != b                                              # THE CRUX: different content -> different tags
    assert any(t in ("#diss", "#fiery", "#track", "#betrayal") for t in a)
    assert any(t in ("#tender", "#lullaby", "#devotion") for t in b)


def test_seed_fallback_entry_carries_tag_sources(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="a fiery diss track about betrayal")
    led = _ingest_empty(led, cfg, "clip_a")
    entry = led.clips["clip_a"].meta_captions["@a/instagram"]
    assert set(entry["tag_sources"]) == set(entry["hashtags"])  # one source per shipped tag
    assert all(entry["tag_sources"].values())                  # none empty/sourceless
    assert "content" in entry["tag_sources"].values()          # the clip's content reached the line


# ---- Task 5: the prompt offers the clip's content tags (byte-identical without) ----------------------
from fanops.prompts import caption_prompt

_BASE_PAYLOAD = {"surfaces": [{"surface": "@a/instagram", "platform": "instagram"}], "language": "en"}


def test_prompt_includes_content_block_when_present():
    out = caption_prompt({**_BASE_PAYLOAD, "content_tags": ["#diss", "#loyalty"]})
    assert "#diss" in out and "#loyalty" in out
    assert "clip-specific" in out.lower()                       # the model is told these are clip-derived


def test_prompt_byte_identical_without_content():
    out = caption_prompt(_BASE_PAYLOAD)
    assert "do NOT invent tags:" in out                         # original menu-only wording stands
    assert "clip-specific" not in out.lower()                   # no content block when absent


def test_contentless_clip_is_byte_identical(tmp_path):
    # an empty-transcript clip ships the same seed line as before this feature (firewall).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    _seed(led, clip_id="clip_a", mom_id="mom_a", transcript="")
    led = _ingest_empty(led, cfg, "clip_a")
    tags = led.clips["clip_a"].meta_captions["@a/instagram"]["hashtags"]
    assert tags == vet_hashtags(None, Platform.instagram, "en")


# ---- Task 6: Review surfaces the per-tag provenance (read-only) ---------------------------------------
def _surface_post(**kw):
    from fanops.studio.views_review import SurfacePost
    base = dict(post_id="p1", account="@a", platform="instagram", persona=None, caption="#diss #fyp",
                hashtags=["#diss", "#fyp"], scheduled_time=None, media_url="/m", state="awaiting_approval",
                imminent=False, editable=True)
    return SurfacePost(**{**base, **kw})


def test_surface_edit_renders_tag_source_chips(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path))
    sp = _surface_post(tag_sources={"#diss": "content", "#fyp": "discovery"})
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
