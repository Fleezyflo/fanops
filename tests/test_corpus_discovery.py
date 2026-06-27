# tests/test_corpus_discovery.py
# M3 — research becomes LIVE. core.discover_corpus harvests live co-occurring tags for a persona's
# category (corpus + lean pool + intake genre as seeds), drops what we already know (VETTED ∪ store ∪
# corpus), and returns evidence-carrying proposals — FAIL-OPEN to today's offline research_corpus re-rank
# when there are no creds / nothing fresh. The Studio "Research corpus" action now returns those dicts.
# research_corpus itself (the offline re-rank) is UNCHANGED — its tests in test_corpus_research.py stay green.
import pytest
from fanops.config import Config
from fanops import personas as core
from fanops.studio import personas as sp


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _creds(monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")


def _router(media):
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if "top_media" in url:
            return _Resp(200, {"data": media})
        return _Resp(404, None)
    return get


# --- core.discover_corpus ----------------------------------------------------------------------

def test_discover_corpus_live_returns_evidence(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#freestyle")                      # the corpus seeds the harvest
    media = [{"caption": "#detroitrap #bars", "like_count": 80, "comments_count": 20}]
    out = core.discover_corpus(cfg, pid, get=_router(media))
    tags = [c["tag"] for c in out]
    assert "#detroitrap" in tags and "#bars" not in tags            # novel kept; #bars (in VETTED) dropped as known
    assert all("count" in c for c in out)                           # free co-occurrence evidence present


def test_discover_corpus_excludes_existing_corpus(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#freestyle")                     # the corpus seeds the harvest
    core.add_corpus_tag(cfg, pid, "#detroitrap")                    # already curated -> must not be re-proposed
    media = [{"caption": "#detroitrap #flintrap", "like_count": 10, "comments_count": 0}]
    out = core.discover_corpus(cfg, pid, get=_router(media))
    tags = [c["tag"] for c in out]
    assert "#detroitrap" not in tags and "#flintrap" in tags


def test_discover_corpus_threads_measure_k(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#freestyle")                     # the corpus seeds the harvest
    media = [{"caption": "#detroitrap", "like_count": 50, "comments_count": 0}]
    out = core.discover_corpus(cfg, pid, measure_k=1, get=_router(media))
    assert out[0].get("measured_engagement") == 50.0               # measurement threads through when requested


def test_discover_corpus_offline_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    out = core.discover_corpus(cfg, pid)
    assert out and all(isinstance(c, dict) and c["tag"].startswith("#") for c in out)
    assert all("count" not in c for c in out)                      # offline = research_corpus re-rank wrapped as dicts


def test_discover_corpus_unknown_persona_raises(tmp_path):
    with pytest.raises(KeyError):
        core.discover_corpus(Config(root=tmp_path), "ghost")


# --- Studio action now returns dicts -----------------------------------------------------------

def test_studio_research_returns_dict_proposals(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    monkeypatch.setattr("fanops.personas.discover_corpus",
                        lambda c, p, **k: [{"tag": "#detroitrap", "count": 3}])
    r = sp.research_corpus(cfg, pid)
    assert r.ok and r.detail["proposals"] == [{"tag": "#detroitrap", "count": 3}]


def test_studio_research_unknown_persona_clean_error(tmp_path):
    r = sp.research_corpus(Config(root=tmp_path), "ghost")
    assert r.ok is False and r.error


def test_research_route_renders_evidence(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="P1", id="p1")
    monkeypatch.setattr("fanops.personas.discover_corpus",
                        lambda c, p, **k: [{"tag": "#detroitrap", "count": 7}])
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/personas/research", data={"id": "p1"})
    assert r.status_code == 200 and b"#detroitrap" in r.data and b"7 posts" in r.data
