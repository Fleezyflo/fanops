# tests/test_graph_tag_metrics.py
# B2 — the Graph API as the on-demand EVIDENCE provider for corpus curation. tag_metrics(cfg, tag) looks
# up ONE hashtag's live Instagram metrics (resolve node + sum top_media engagement) so the operator can
# see a tag's reach BEFORE adding it to a persona's corpus, spending one ig_hashtag_search budget slot.
# Plus: FANOPS_HASHTAG_TRENDS now defaults ON (the Graph API is on by default, fail-open without creds).
from fanops.config import Config
from fanops.meta_graph import tag_metrics, budget_remaining, record_query
from fanops import personas as core
from fanops.studio import personas as sp


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _router(score):
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if "top_media" in url:
            return _Resp(200, {"data": [{"like_count": score, "comments_count": 5}]})
        return _Resp(404, None)
    return get


def _creds(monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")


# --- meta_graph.tag_metrics ---------------------------------------------------------------------

def test_tag_metrics_returns_engagement_on_resolve(tmp_path, monkeypatch):
    _creds(monkeypatch)
    m = tag_metrics(Config(root=tmp_path), "#detroitrap", get=_router(900))
    assert m["resolved"] is True and m["engagement"] == 905     # 900 likes + 5 comments
    assert m["tag"] == "#detroitrap"


def test_tag_metrics_failopen_without_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    m = tag_metrics(Config(root=tmp_path), "#x", get=_router(900))
    assert m["resolved"] is False and "META_GRAPH_TOKEN" in (m.get("error") or "")


def test_tag_metrics_unresolved_tag(tmp_path, monkeypatch):
    _creds(monkeypatch)
    def get(url, params=None, timeout=None):
        return _Resp(200, {"data": []})                        # ig_hashtag_search resolves to nothing
    m = tag_metrics(Config(root=tmp_path), "#nope", get=get)
    assert m["resolved"] is False and m.get("error")


def test_tag_metrics_budget_unreadable_failclosed(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("{ not json")
    m = tag_metrics(cfg, "#x", get=_router(900))
    assert m["resolved"] is False and "budget" in (m.get("error") or "").lower()


def test_tag_metrics_spends_one_budget_slot(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    before = budget_remaining(cfg)
    tag_metrics(cfg, "#detroitrap", get=_router(900))
    assert budget_remaining(cfg) == before - 1


def test_tag_metrics_budget_exhausted(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    for i in range(30):                                        # fill the 30/7-day window with distinct tags
        record_query(cfg, f"#t{i}")
    m = tag_metrics(cfg, "#newone", get=_router(900))
    assert m["resolved"] is False and "budget" in (m.get("error") or "").lower()


def test_tag_metrics_degenerate_tag_spends_no_budget(tmp_path, monkeypatch):
    # a bare "#" must be rejected BEFORE the Graph call so it never wastes one of the 30/7-day slots.
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    before = budget_remaining(cfg)
    m = tag_metrics(cfg, "#", get=_router(900))
    assert m["resolved"] is False and budget_remaining(cfg) == before


# --- config: Graph trends default ON ------------------------------------------------------------

def test_hashtag_trends_default_on(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_HASHTAG_TRENDS", raising=False)
    assert Config(root=tmp_path).hashtag_trends is True         # B2: the Graph API is ON by default


def test_hashtag_trends_explicit_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HASHTAG_TRENDS", "0")
    assert Config(root=tmp_path).hashtag_trends is False        # explicit off-word still disables


# --- Studio recommend action --------------------------------------------------------------------

def test_recommend_tag_returns_metrics(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    monkeypatch.setattr("fanops.meta_graph.tag_metrics",
                        lambda c, t, **k: {"tag": "#detroitrap", "resolved": True, "engagement": 905})
    r = sp.recommend_tag(cfg, pid, "#detroitrap")
    assert r.ok and r.detail["engagement"] == 905 and r.detail["tag"] == "#detroitrap" and r.detail["persona"] == pid


def test_recommend_unknown_persona_clean_error(tmp_path):
    r = sp.recommend_tag(Config(root=tmp_path), "ghost", "#x")
    assert r.ok is False and r.error


def test_recommend_unresolved_tag_clean_error(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    monkeypatch.setattr("fanops.meta_graph.tag_metrics",
                        lambda c, t, **k: {"tag": "#x", "resolved": False, "error": "did not resolve on Instagram"})
    r = sp.recommend_tag(cfg, pid, "#x")
    assert r.ok is False and "resolve" in r.error.lower()


def test_recommend_route_smoke(tmp_path):
    # No creds -> tag_metrics fails open -> ok=False -> the panel still renders at HTTP 200 (htmx contract).
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="P1", id="p1")
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/personas/recommend", data={"id": "p1", "tag": "#x"})
    assert r.status_code == 200
