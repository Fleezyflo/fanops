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

def test_media_insights_reels_normalizes_full_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    body = _insights_body([("reach", 1000), ("plays", 1200), ("saved", 40),
                           ("shares", 12), ("likes", 300), ("comments", 25),
                           ("ig_reels_avg_watch_time", 8000)])
    got = _get(_Resp(200, body))
    out = meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    assert out["reach"] == 1000 and out["saves"] == 40 and out["shares"] == 12
    assert out["likes"] == 300 and out["comments"] == 25
    assert out["views"] == 1200                                  # plays -> views (Meta rename tolerated)
    assert out["avg_watch_ms"] == 8000                           # raw avg-watch ms, retention derived downstream


def test_media_insights_reels_requests_reels_metric_names(tmp_path, monkeypatch):
    # LOCKED #3: a REELS media must request the reels metric list (must include avg-watch), never a blind
    # list that 400s on a non-applicable metric.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))
    meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" in params["metric"]


def test_media_insights_feed_omits_reels_only_metric(tmp_path, monkeypatch):
    # A non-reel (FEED/VIDEO) media must NOT request ig_reels_avg_watch_time (it 400s for feed) -> no
    # avg_watch_ms in the result; reach/saves/etc still land.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 500), ("saved", 9)])))
    out = meta_graph.media_insights(cfg, "M2", "FEED", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" not in params["metric"]
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


# ---- Task 3: GraphInsightsClient emits the row contract with retention as a [0,1] fraction ----------

from fanops.models import Post, PostState, Platform
from fanops.post.metrics import GraphInsightsClient


def _ig_post(pid, media_id, *, cut_seconds=None, sub=None):
    return Post(id=pid, parent_id="c", account="@a", account_id="acc1", platform=Platform.instagram,
                caption="x", state=PostState.published, media_id=media_id, cut_seconds=cut_seconds,
                submission_id=sub or f"real_{pid}",
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
