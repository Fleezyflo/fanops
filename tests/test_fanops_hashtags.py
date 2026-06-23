# tests/test_fanops_hashtags.py
# M4 offline core: own-reach hashtag ranking -> 00_control/hashtags.json store, GATED on the F2
# learn-doctor PASS verdict (don't trust reach until the analytics label reconciles). The live Meta
# Graph trend fetch is a deferred, operator-gated follow-up (untestable without a Meta app).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.hashtags import load_store, vetted_menu, vet_hashtags
from fanops.fanops_hashtags import rank_tags_by_reach, refresh_store


def _analyzed_post(led, pid, tags, reach):
    led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, hashtags=tags, metrics={"reach": reach}))

def _pass_doctor(cfg):
    # write the F2 learn-doctor verdict file M4 reads (decoupled via the known 00_control path)
    p = cfg.control / "learn_doctor.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"verdict": "PASS"}))


def test_rank_tags_by_reach_orders_by_mean_reach_per_post(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#hiphop", "#bars"], 1000)      # #bars: 1000
    _analyzed_post(led, "p2", ["#hiphop"], 9000)               # #hiphop: mean (1000+9000)/2 = 5000
    ranked = rank_tags_by_reach(led)
    assert ranked[0] == "#hiphop"                              # higher mean reach ranks first
    assert "#bars" in ranked

def test_tag_reach_means_computes_mean_per_tag(tmp_path):
    # B4 (closed loop): the mean reach-per-post per tag, surfaced next to each curated corpus tag.
    from fanops.fanops_hashtags import tag_reach_means
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#bars"], 1000)
    _analyzed_post(led, "p2", ["#bars"], 3000)                 # #bars mean = 2000
    _analyzed_post(led, "p3", ["#rap"], 500)
    means = tag_reach_means(led)
    assert means["#bars"] == 2000.0 and means["#rap"] == 500.0


def test_rank_tags_by_reach_ignores_non_numeric_reach_and_unanalyzed(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#rap"], 500)
    led.add_post(Post(id="p2", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, hashtags=["#nope"], metrics={}))  # not analyzed
    ranked = rank_tags_by_reach(led)
    assert ranked == ["#rap"]                                  # only the analyzed post with numeric reach

def test_refresh_store_gates_on_doctor_pass(tmp_path):
    # No learn-doctor verdict (or not PASS) -> refresh writes NOTHING (reach is garbage-in).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#hiphop"], 1000)
    out = refresh_store(led, cfg)
    assert out["written"] is False and not cfg.hashtags_path.exists()

def test_refresh_store_writes_reach_ranked_store_when_pass(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#undergroundhiphop"], 8000)
    _pass_doctor(cfg)
    out = refresh_store(led, cfg)
    assert out["written"] is True and cfg.hashtags_path.exists()
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert store[0] == "#undergroundhiphop"                    # own-reach winner ranks first
    assert "#hiphop" in store                                  # frozen seed merged so never-posted tags survive

def test_load_store_reads_tags_or_none(tmp_path):
    cfg = Config(root=tmp_path)
    assert load_store(cfg) is None                             # absent -> None (fall back to frozen)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#x", "#y"]}))
    assert load_store(cfg) == ["#x", "#y"]
    cfg.hashtags_path.write_text("{ corrupt")
    assert load_store(cfg) is None                             # corrupt -> None, never raises


def test_vetted_menu_uses_store_when_given_else_frozen():
    assert vetted_menu(store=["#a", "#b"]) == ["#a", "#b"]     # dynamic store drives the menu
    assert "#hiphop" in vetted_menu()                          # no store -> frozen pools (today)

def test_vet_hashtags_store_aware_and_byte_identical_without_store():
    # store=None -> exactly today's frozen behavior.
    base = vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en")
    assert vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en", store=None) == base
    # with a store, the store IS the vetted set: an in-store tag survives, off-store is dropped + backfilled.
    out = vet_hashtags(["#mytrend", "#garbageword"], Platform.instagram, "en", store=["#mytrend", "#second"])
    assert "#mytrend" in out and "#garbageword" not in out
    assert len(out) <= 4


# --- M4 live trend half: refresh_store blends Meta Graph trend sampling on top of own-reach ----------
class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body

def _trend_router(score_by_id):
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if "top_media" in url:
            return _Resp(200, {"data": [{"like_count": score_by_id, "comments_count": 0}]})
        return _Resp(404, None)
    return get

def test_refresh_store_trends_on_by_default_failopen_without_creds(tmp_path, monkeypatch):
    # B2: FANOPS_HASHTAG_TRENDS now DEFAULTS ON (the Graph API is on by default). Without Meta creds,
    # sample_trends no-ops -> the store is own-reach-only, byte-identical to the old default-OFF output.
    # Default-ON is safe without a token (fail-open).
    monkeypatch.delenv("FANOPS_HASHTAG_TRENDS", raising=False)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#owned"], 5000); _pass_doctor(cfg)
    out = refresh_store(led, cfg)
    assert out["written"] is True and out.get("trend_sampled", 0) == 0   # no creds -> no trend sampling
    assert json.loads(cfg.hashtags_path.read_text())["tags"][0] == "#owned"   # own-reach only, byte-identical

def test_refresh_store_blends_trending_tag_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HASHTAG_TRENDS", "1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#owned"], 5000); _pass_doctor(cfg)
    out = refresh_store(led, cfg, get=_trend_router(900))
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert store[0] == "#owned"                                 # own reach stays PRIMARY
    assert out.get("trend_sampled", 0) >= 1                     # at least one tag trend-sampled

def test_refresh_store_trends_fail_open_without_token(tmp_path, monkeypatch):
    # flag on but NO token -> own-reach only, still written (never blocks on missing creds).
    monkeypatch.setenv("FANOPS_HASHTAG_TRENDS", "1")
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, "p1", ["#owned"], 5000); _pass_doctor(cfg)
    out = refresh_store(led, cfg, get=_trend_router(900))
    assert out["written"] is True and out.get("trend_sampled", 0) == 0
