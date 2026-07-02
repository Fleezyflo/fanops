# tests/test_graph_insights.py
# Leg 2 Task 2 (Read) + Task 3 (Land): Meta Graph media insights as the SOLE IG performance source.
# Task 2 here: meta_graph.media_insights branches on media_product_type (reels vs feed video), normalizes
# the returned metric names to lift keys, and DISCRIMINATES a permission/scope refusal (loud typed
# MetaInsightsScopeError) from a transient transport failure (None). Pure-fixture (injected `get=`).
import pytest
from fanops.config import Config
from fanops import meta_graph
from fanops.errors import MetaInsightsScopeError

_TOKEN = "SECRET-meta-token-xyz"


def _cfg(tmp_path, monkeypatch, *, token=_TOKEN, ig="ig-123"):
    monkeypatch.setenv("META_GRAPH_TOKEN", token) if token else monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.setenv("META_IG_USER_ID", ig) if ig else monkeypatch.delenv("META_IG_USER_ID", raising=False)
    return Config(root=tmp_path)


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _get(resp_or_fn):
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        return resp_or_fn(params) if callable(resp_or_fn) else resp_or_fn
    get.calls = calls
    return get


def _insights_body(pairs):
    # Graph /{media}/insights shape: {"data": [{"name": ..., "values": [{"value": N}]}, ...]}
    return {"data": [{"name": n, "values": [{"value": v}]} for n, v in pairs]}


# ---- reels branch: full set incl. avg watch time ------------------------------------------------

# ---- the one Meta-derived table: insights_metrics_for(product_type) is the SOLE request source ---------
# _MEDIA_METRICS maps each FanOps-consumed metric to the product types Meta declares valid (transcribed
# from the official ig-media/insights reference). A metric invalid for a type is NOT in the derived set ->
# it is unconstructable in the request. Deprecated names (plays/impressions) are absent by design.

_DEPRECATED = ("plays", "impressions", "clips_replays_count",
               "ig_reels_aggregated_all_plays_count", "video_views")


def test_media_metrics_table_is_v21_valid(tmp_path, monkeypatch):
    # The single sync point with Meta, DEFENDED: no deprecated metric may live in the table (that is what
    # let `plays` rot), and the Graph URL stays pinned to the version the table is valid for. This FAILS at
    # CI the day someone re-adds a deprecated metric — surfacing the class at CI, not in production.
    assert not (set(meta_graph._MEDIA_METRICS) & set(_DEPRECATED)), meta_graph._MEDIA_METRICS
    assert _cfg(tmp_path, monkeypatch).meta_graph_url.endswith("v21.0")


def test_insights_metrics_for_reels_includes_avg_watch():
    m = meta_graph.insights_metrics_for("REELS")
    assert "ig_reels_avg_watch_time" in m                        # REELS-only metric IS in the reels set
    for k in ("reach", "views", "likes", "comments", "saved", "shares"):
        assert k in m                                            # the shared metrics land too


def test_insights_metrics_for_feed_excludes_reels_only_metric():
    m = meta_graph.insights_metrics_for("FEED")
    assert "ig_reels_avg_watch_time" not in m                    # REELS-only -> UN-addable for FEED (not "dropped")
    for k in ("reach", "views", "likes", "comments", "saved"):
        assert k in m                                            # feed's valid metrics per Meta


def test_insights_metrics_for_never_contains_a_deprecated_name():
    # The whole class killer: no derived set for ANY product_type can contain a deprecated metric,
    # because the table doesn't hold one. This is what made `plays` unrequestable.
    for pt in ("REELS", "FEED", "STORY", "AD", None, "unexpected"):
        derived = meta_graph.insights_metrics_for(pt)
        assert not (set(derived) & set(_DEPRECATED)), (pt, derived)


def test_insights_metrics_for_case_insensitive():
    assert meta_graph.insights_metrics_for("reels") == meta_graph.insights_metrics_for("REELS")


def test_media_insights_reels_normalizes_full_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Meta's REAL v21 reels response uses `views` (not the deprecated `plays`).
    body = _insights_body([("reach", 1000), ("views", 1200), ("saved", 40),
                           ("shares", 12), ("likes", 300), ("comments", 25),
                           ("ig_reels_avg_watch_time", 8000)])
    got = _get(_Resp(200, body))
    out = meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    assert out["reach"] == 1000 and out["saves"] == 40 and out["shares"] == 12
    assert out["likes"] == 300 and out["comments"] == 25
    assert out["views"] == 1200                                  # views is the v21 metric
    assert out["avg_watch_ms"] == 8000                           # raw avg-watch ms, retention derived downstream


def test_media_insights_reels_request_omits_deprecated_plays(tmp_path, monkeypatch):
    # The request is derived from the Meta table: a REELS pull must send `views` and the reels-only
    # avg-watch, and must NEVER send the deprecated `plays` (the whole-request 400 cause).
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))
    meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    _url, params = got.calls[0]
    metric = params["metric"]
    assert "views" in metric and "ig_reels_avg_watch_time" in metric
    assert "plays" not in metric and "impressions" not in metric


def test_media_insights_feed_omits_reels_only_metric(tmp_path, monkeypatch):
    # A FEED media derives the feed set: NO ig_reels_avg_watch_time (Meta: REELS-only) -> no avg_watch_ms in
    # the result; reach/saves/etc still land. No `plays` either.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 500), ("saved", 9)])))
    out = meta_graph.media_insights(cfg, "M2", "FEED", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" not in params["metric"]
    assert "plays" not in params["metric"]
    assert out["reach"] == 500 and out["saves"] == 9
    assert "avg_watch_ms" not in out


# ---- scope refusal is LOUD (typed) vs transient is None -----------------------------------------

def test_media_insights_permission_error_raises_scope_error(tmp_path, monkeypatch):
    # A Meta permission/OAuth refusal (missing instagram_manage_insights) must raise the typed, LOUD
    # MetaInsightsScopeError — fail CLOSED, never silently None (which reads as 'no data', wrong).
    cfg = _cfg(tmp_path, monkeypatch)
    perm = {"error": {"code": 10, "type": "OAuthException",
                      "message": "(#10) Application does not have permission for this action"}}
    with pytest.raises(MetaInsightsScopeError):
        meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(400, perm)))


def test_media_insights_scope_error_withholds_token(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    perm = {"error": {"code": 200, "type": "OAuthException", "message": "Permissions error"}}
    try:
        meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(403, perm)))
        assert False, "expected MetaInsightsScopeError"
    except MetaInsightsScopeError as e:
        assert _TOKEN not in str(e)                              # the access_token never leaks into the message


def test_media_insights_transient_5xx_returns_none(tmp_path, monkeypatch):
    # A 5xx / transport blip is TRANSIENT -> None (re-poll next pass), NOT a scope error (don't cry wolf).
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(500, None))) is None


def test_media_insights_network_exception_returns_none(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    import requests
    def boom(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("down")
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=boom) is None


def test_media_insights_no_creds_returns_none(tmp_path, monkeypatch):
    # No token/ig id -> can't read -> None (transient-shaped; the daemon simply keeps prior snapshots).
    cfg = _cfg(tmp_path, monkeypatch, token=None)
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(200, _insights_body([("reach", 1)])))) is None


# ---- M2 residual: an unresolved product_type must NOT build an empty-metric request --------------

def test_media_insights_none_product_type_builds_no_request(tmp_path, monkeypatch):
    # LIVE RESIDUAL (post_4eb7c0802e79): media_id resolved but product_type=None -> insights_metrics_for(None)
    # is [] -> today media_insights sends an EMPTY `metric=` -> Meta 400 OAuthException -> _is_scope_error
    # (untouched) writes a FALSE scope-block. Honor the docstring ("the client skips an unresolved one"):
    # an empty derived set must be refused PRE-FLIGHT -> ZERO HTTP calls (None, transient-shaped, re-resolve
    # next pass), never a malformed request.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))       # would answer 200 IF called
    out = meta_graph.media_insights(cfg, "M1", None, get=got)
    assert out is None                                           # transient-shaped skip, keep prior snapshot
    assert got.calls == []                                       # no request built -> no empty `metric=` sent


# ---- Task 3: GraphInsightsClient emits the row contract with retention as a [0,1] fraction ----------

from fanops.models import Post, PostState, Platform
from fanops.post.metrics import GraphInsightsClient


def _ig_post(pid, media_id, *, cut_seconds=None, sub=None, product_type="REELS"):
    return Post(id=pid, parent_id="c", account="@a", account_id="acc1", platform=Platform.instagram,
                caption="x", state=PostState.published, media_id=media_id, cut_seconds=cut_seconds,
                product_type=product_type, submission_id=sub or f"real_{pid}",
                public_url=f"https://www.instagram.com/reel/{pid}/")   # R1: a published post has a permalink


def test_graph_client_emits_row_with_retention_fraction(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # avg_watch 8000ms over a 20s clip -> retention 0.40; other metrics pass through.
    post = _ig_post("p1", "M1", cut_seconds=20.0)
    insights = {"M1": {"reach": 1000, "views": 1200, "saves": 40, "shares": 12,
                       "likes": 300, "comments": 25, "avg_watch_ms": 8000}}
    client = GraphInsightsClient(cfg, posts=[post], insights_fn=lambda mid, pt: insights.get(mid))
    rows = client.list_posts()
    assert len(rows) == 1
    r = rows[0]
    assert r["postSubmissionId"] == "real_p1"
    m = r["metrics"]
    assert m["reach"] == 1000 and m["saves"] == 40 and m["shares"] == 12
    assert abs(m["retention"] - 0.40) < 1e-9                     # 8000 / (20 * 1000)
    assert "avg_watch_ms" not in m                              # raw ms is consumed into retention, not shipped


def test_graph_client_retention_clamped_to_one(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # avg_watch exceeding the clip length (loops / measurement noise) clamps to 1.0, never > 1.
    post = _ig_post("p1", "M1", cut_seconds=5.0)
    insights = {"M1": {"reach": 10, "avg_watch_ms": 9000}}       # 9s watched on a 5s clip
    client = GraphInsightsClient(cfg, posts=[post], insights_fn=lambda mid, pt: insights.get(mid))
    assert client.list_posts()[0]["metrics"]["retention"] == 1.0


def test_graph_client_omits_retention_without_duration(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # No cut_seconds -> retention honestly ABSENT (degraded), never fabricated; reach/saves still land.
    post = _ig_post("p1", "M1", cut_seconds=None)
    insights = {"M1": {"reach": 500, "saves": 9, "avg_watch_ms": 8000}}
    client = GraphInsightsClient(cfg, posts=[post], insights_fn=lambda mid, pt: insights.get(mid))
    m = client.list_posts()[0]["metrics"]
    assert m["reach"] == 500 and m["saves"] == 9
    assert "retention" not in m and "avg_watch_ms" not in m


def test_graph_client_skips_post_without_media_id(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # A post not yet resolved (media_id=None) is SKIPPED (no row) -> keeps its prior snapshot, re-polled later.
    post = _ig_post("p1", None, cut_seconds=20.0)
    client = GraphInsightsClient(cfg, posts=[post], insights_fn=lambda mid, pt: {"reach": 1})
    assert client.list_posts() == []


def test_graph_client_requests_the_posts_real_product_type(tmp_path, monkeypatch):
    # The client must send the media's REAL product_type (stamped at resolve), NOT a hard-coded REELS —
    # so media_insights derives the matching metric set (a FEED post gets the feed set, no reels-only
    # avg-watch -> no 400). It is guaranteed present past the media_id guard (single stamp site), so there
    # is no skip / fallback: the client simply forwards p.product_type.
    cfg = _cfg(tmp_path, monkeypatch)
    seen = []
    def spy(mid, pt):
        seen.append((mid, pt))
        return {"reach": 5}
    feed = _ig_post("p1", "M1", cut_seconds=20.0, product_type="FEED")
    reel = _ig_post("p2", "M2", cut_seconds=20.0, product_type="REELS")
    GraphInsightsClient(cfg, posts=[feed, reel], insights_fn=spy).list_posts()
    assert ("M1", "FEED") in seen                                # feed post -> feed type, not REELS
    assert ("M2", "REELS") in seen


def test_graph_client_transient_none_skips_that_post(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # A per-post transient (insights_fn -> None) SKIPS that id (no wholesale-zero of the prior snapshot),
    # while OTHER posts still land — mirrors PostizMetricsClient per-post isolation.
    p1 = _ig_post("p1", "M1", cut_seconds=20.0)
    p2 = _ig_post("p2", "M2", cut_seconds=20.0)
    insights = {"M2": {"reach": 77}}                            # M1 absent -> None -> skip p1
    client = GraphInsightsClient(cfg, posts=[p1, p2], insights_fn=lambda mid, pt: insights.get(mid))
    rows = client.list_posts()
    assert {r["postSubmissionId"] for r in rows} == {"real_p2"}


def test_graph_client_scope_error_sets_insights_blocked_and_stops(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # A scope refusal on the FIRST call fails the pass CLOSED + LOUD: no rows written (prior snapshots
    # intact), and the client exposes insights_blocked so doctor/Home can surface the one external gate.
    p1 = _ig_post("p1", "M1", cut_seconds=20.0)
    def boom(mid, pt):
        raise MetaInsightsScopeError("scope missing")
    client = GraphInsightsClient(cfg, posts=[p1], insights_fn=boom)
    rows = client.list_posts()
    assert rows == []                                           # nothing written -> no wrong numbers
    assert client.insights_blocked is True


def test_scope_block_persists_a_breadcrumb_that_doctor_reads(tmp_path, monkeypatch):
    # The scope block must persist so a SEPARATE doctor/Home read surfaces it (the block happens during a
    # daemon pull; doctor runs later). A scope error writes the breadcrumb; a clean insights read clears it.
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.insights_blocked_signal(cfg) is False     # clean by default
    p1 = _ig_post("p1", "M1", cut_seconds=20.0)
    GraphInsightsClient(cfg, posts=[p1],
                        insights_fn=lambda mid, pt: (_ for _ in ()).throw(MetaInsightsScopeError("x"))).list_posts()
    assert meta_graph.insights_blocked_signal(cfg) is True      # persisted -> doctor/Home can read it
    # a subsequent CLEAN read (scope granted) self-heals the signal
    GraphInsightsClient(cfg, posts=[p1],
                        insights_fn=lambda mid, pt: {"reach": 10, "avg_watch_ms": 8000}).list_posts()
    assert meta_graph.insights_blocked_signal(cfg) is False     # cleared once insights flow again


def test_none_product_type_post_writes_no_scope_block_end_to_end(tmp_path, monkeypatch):
    # M2 end-to-end (through the REAL media_insights, not a stub): a resolved post whose product_type is
    # None (the live post_4eb7c0802e79 shape) must NOT write a false scope-block. Meta would 400 the empty
    # request, but the pre-flight refusal means Meta is never called -> the classifier is never reached ->
    # no block. The injected `get` proves it: it is never invoked.
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.insights_blocked_signal(cfg) is False      # clean by default
    p1 = _ig_post("p1", "M1", cut_seconds=20.0, product_type=None)   # media_id resolved, type NOT yet
    got = _get(_Resp(400, {"error": {"code": 100, "type": "OAuthException",
                                     "message": "(#100) metric[0] must be one of the following values: ..."}}))
    rows = GraphInsightsClient(cfg, posts=[p1],
                              insights_fn=lambda mid, pt: meta_graph.media_insights(cfg, mid, pt, get=got)).list_posts()
    assert rows == []                                            # transient skip (no data) -> keep prior snapshot
    assert got.calls == []                                       # the malformed request was never built
    assert meta_graph.insights_blocked_signal(cfg) is False      # NO false scope-block over a malformed request
