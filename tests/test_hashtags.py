"""Measurement cache reader + ingest lock membership (ship_from_lock)."""
import json
from fanops.hashtags import METRIC_FIELD, load_measurements

# A measured menu, metric-ranked (what ranked_tags returns). Membership + rank in one list.
STORE = ["#hiphop", "#rap", "#hiphopmusic", "#rapper", "#bars", "#newmusic", "#undergroundhiphop"]


# --- the measurement cache reader ---------------------------------------------------------------

def _rec(value, *, gid="id-x", at="2026-07-20T00:00:00+00:00", frm=None):
    r = {"graph_id": gid, METRIC_FIELD: value, "measured_at": at}
    if frm: r["from"] = frm
    return r


def test_load_measurements_absent_corrupt_and_mis_shaped(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert load_measurements(cfg) == {}                    # absent -> {} (selection then ships short)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text("{ corrupt")
    assert load_measurements(cfg) == {}                     # corrupt -> {}, never raises
    cfg.hashtags_path.write_text(json.dumps(["#a", "#b"]))
    assert load_measurements(cfg) == {}                     # a list is not the cache shape


def test_load_measurements_drops_half_records_and_the_legacy_shape(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#Good": _rec(900.0, gid="id-good", frm={"#Anchor": "2"}),
        "#nogid": {METRIC_FIELD: 5.0, "measured_at": "2026-07-20T00:00:00+00:00"},
        "#nostamp": {"graph_id": "id-x", METRIC_FIELD: 5.0},
        "#legacy": {"graph_id": "id-l", "reach": 4200, "measured_at": "2026-07-20T00:00:00+00:00"},
        "#negative": _rec(-1.0),
    }))
    m = load_measurements(cfg)
    assert set(m) == {"#good"}                             # key normalized; every half-record dropped
    assert m["#good"][METRIC_FIELD] == 900.0
    assert m["#good"]["from"] == {"#anchor": 2}            # harvest attribution normalized + int-coerced


# --- per-account hashtag CORPUS (persona differentiation) ---------------------------------------

# ---- the AR floor fires on CORPUS too, so a corpus-led persona keeps region reach ----
# --- MOL-511 (C-1): ingest vets from per-surface hashtag_store (not the global cache) -----------

def test_ingest_scopes_vet_store_per_surface(tmp_path):
    """Request hashtag_store is not membership. Sidecar lock is. 141-tag request + 12-tag lock
    ships ⊆ lock. Per-surface request stores cannot sneak a tag onto another surface."""
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                               CaptionSet, CaptionItem)
    from fanops.agentstep import response_path, request_path, latest_request_id
    from fanops.caption import request_captions, ingest_captions
    from fanops.source_tags import source_tag_locks_path

    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    lock = [f"#lock{i:02d}" for i in range(12)]
    request_pile = [f"#req{i:03d}" for i in range(141)]
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_1": {"pile": request_pile, "lock": lock, "researched_at": "2026-08-18T00:00:00Z"},
    }))
    request_captions(led, cfg, "clip_1",
                     [("a", Platform.instagram), ("b", Platform.instagram), ("c", Platform.instagram)])
    req_path = request_path(cfg, "captions", "clip_1")
    req = json.loads(req_path.read_text())
    by = {s["surface"]: s for s in req["surfaces"]}
    by["a/instagram"]["hashtag_store"] = request_pile
    by["b/instagram"]["hashtag_store"] = ["#betaonly"]
    by["c/instagram"]["hashtag_store"] = []
    req_path.write_text(json.dumps(req))
    rid = latest_request_id(cfg, "captions", "clip_1")
    picks_a = lock[:3] + ["#req000", "#betaonly"]
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=picks_a),
        CaptionItem(surface="b/instagram", caption="x",
                    hashtags=["#betaonly", "#lock00", "#req140"]),
        CaptionItem(surface="c/instagram", caption="x",
                    hashtags=["#req000", "#globalwinner"]),
    ]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    a = led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"]
    b = led.clips["clip_1"].meta_captions["b/instagram"]["hashtags"]
    c = led.clips["clip_1"].meta_captions["c/instagram"]["hashtags"]
    assert a == lock[:3]
    assert set(a) <= set(lock) and len(a) <= 4
    assert b == ["#lock00"]
    assert "#betaonly" not in a and "#betaonly" not in b
    assert "#req000" not in a and "#req140" not in b
    assert c == []


def test_ingest_absent_hashtag_store_is_short_not_global(tmp_path):
    """No hashtag_store key on the surface (legacy request) must NOT fall back to the global cache."""
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                               CaptionSet, CaptionItem)
    from fanops.agentstep import response_path, request_path, latest_request_id
    from fanops.caption import request_captions, ingest_captions

    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    from fanops.source_tags import source_tag_locks_path
    lock_p = source_tag_locks_path(cfg)
    lock_p.parent.mkdir(parents=True, exist_ok=True)
    lock_p.write_text(json.dumps({
        "src_1": {"pile": [], "lock": [], "researched_at": "2026-08-17T00:00:00Z"},
    }))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "1", "like_count": 900, "measured_at": "2026-07-01T00:00:00+00:00"},
    }))
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    req_path = request_path(cfg, "captions", "clip_1")
    req = json.loads(req_path.read_text())
    assert "hashtag_store" not in req["surfaces"][0]        # no accounts -> key absent
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    # Pre-MOL-511 would keep #hiphop from the global cache; scoped ingest ships empty (no discovery pad).
    assert led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"] == []
