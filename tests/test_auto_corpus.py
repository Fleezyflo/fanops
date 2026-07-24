# S12 — automated persona corpus refresh (backend-only): throttle, fill-to-target, pin protection,
# content screen, self-prune, budget gate, offline fill, and the inertness of the DELETED corpus_auto env var.
import json
import time
from fanops.config import Config
from fanops import personas as core
from fanops.meta_graph import record_query
from fanops.persona_research import refresh_persona_corpus, refresh_corpora_if_due


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _creds(monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")


def _router(media, *, reach=None):
    reach = reach or {}
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if "top_media" in url:
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            score = reach.get(tag, 50)
            return _Resp(200, {"data": [{"caption": media, "like_count": score, "comments_count": 0}]})
        return _Resp(404, None)
    return get


def _seed_store(cfg, reach: dict[str, float]):
    """A store whose tags carry MEASURED Graph reach. R4: this helper means "we measured these", so it writes
    evidence records — a bare number now reads back `source: "unknown"` and is (correctly) refused for
    curation, because we would not know where it came from. See ADR-0104."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": list(reach.keys()), "reach": {
        t: {"reach": v, "measured_at": now, "source": "graph-reach", "confidence": 1.0}
        for t, v in reach.items()}}))


def _write_meta(cfg, pid, corpus, meta):
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d.get("id") == pid:
            d["hashtag_corpus"] = corpus
            d["hashtag_corpus_meta"] = meta
    cfg.personas_path.write_text(json.dumps(raw))


def test_throttle_12h(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    marker = cfg.control / ".corpora_refresh.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}")
    old = marker.stat().st_mtime
    time.sleep(0.05)
    r = refresh_corpora_if_due(cfg, max_age_s=43200, get=_router("#fresh"))
    assert r.get("refreshed") is False and r.get("reason") == "fresh"
    assert marker.stat().st_mtime >= old


def test_fill_to_target(tmp_path, monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "6")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    _seed_store(cfg, {"#other": 1})
    media = "#alpha #beta #gamma #delta #epsilon #zeta"
    r = refresh_persona_corpus(cfg, pid, get=_router(media, reach={"#alpha": 100, "#beta": 90, "#gamma": 80,
                                                                    "#delta": 70, "#epsilon": 60, "#zeta": 50}))
    assert r.get("changed") is True
    per = core.Personas.load(cfg).get(pid)
    assert len(per.hashtag_corpus) == 6


def test_pin_protection(tmp_path, monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#pinned")
    core.add_corpus_tag(cfg, pid, "#auto1")
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d.get("id") == pid:
            d["hashtag_corpus_meta"]["#auto1"] = {"source": "auto", "reach": 1, "added": "2026-01-01T00:00:00+00:00"}
    cfg.personas_path.write_text(json.dumps(raw))
    _seed_store(cfg, {"#high": 999, "#low": 1})
    refresh_persona_corpus(cfg, pid, get=_router("#high #low", reach={"#high": 999, "#low": 1}))
    per = core.Personas.load(cfg).get(pid)
    assert "#pinned" in per.hashtag_corpus
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta["#pinned"]["source"] == "pinned"


def test_screen_rejects(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    def _discover(c, p, **k):
        return [{"tag": "#pls", "count": 9, "measured_engagement": 900.0}]
    monkeypatch.setattr("fanops.persona_research.discover_corpus", _discover)
    r = refresh_persona_corpus(cfg, pid)
    after = core.Personas.load(cfg).get(pid).hashtag_corpus
    assert "#pls" not in after
    assert r.get("changed") is False or "#pls" not in (r.get("added") or [])


def test_self_prune(tmp_path, monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "3")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _write_meta(cfg, pid, ["#a", "#b", "#c"], {
        "#a": {"source": "auto", "reach": 10, "added": "2026-01-01T00:00:00+00:00"},
        "#b": {"source": "auto", "reach": 20, "added": "2026-01-01T00:00:00+00:00"},
        "#c": {"source": "auto", "reach": 30, "added": "2026-01-01T00:00:00+00:00"},
    })
    _seed_store(cfg, {"#dummy": 1})
    r = refresh_persona_corpus(cfg, pid, get=_router("#winner", reach={"#winner": 500}))
    assert r.get("changed") is True
    corpus = core.Personas.load(cfg).get(pid).hashtag_corpus
    assert "#winner" in corpus and len(corpus) == 3
    assert "#a" not in corpus


def test_budget_exhausted_no_evidence_unchanged(tmp_path, monkeypatch):
    """Budget==0 gates ONLY live Graph; with no measured evidence the offline fill has nothing to add."""
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    for i in range(30):
        record_query(cfg, f"#t{i}")
    before = cfg.personas_path.read_text()
    r = refresh_persona_corpus(cfg, pid, get=_router("#fresh"))
    assert r.get("changed") is False
    assert r.get("reason") != "budget_exhausted"   # early return deleted — budget no longer aborts the function
    assert cfg.personas_path.read_text() == before


def test_offline_fill_runs_when_budget_exhausted(tmp_path, monkeypatch):
    """Pin 1: measured-evidence fill runs at ZERO Graph quota (offline path spends none)."""
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    for i in range(30):
        record_query(cfg, f"#t{i}")
    assert __import__("fanops.meta_graph", fromlist=["budget_remaining"]).budget_remaining(cfg) == 0
    _seed_store(cfg, {"#alpha": 100, "#beta": 90, "#gamma": 80, "#delta": 70})
    called = {"n": 0}
    def _no_graph(url, params=None, timeout=None):
        called["n"] += 1; return _Resp(404, None)
    r = refresh_persona_corpus(cfg, pid, get=_no_graph)
    assert called["n"] == 0, "offline fill must not touch the Graph"
    assert r.get("changed") is True
    corpus = core.Personas.load(cfg).get(pid).hashtag_corpus
    assert len(corpus) == 4 and "#seed" in corpus
    assert {"#alpha", "#beta", "#gamma"} <= set(corpus)


def test_offline_fill_when_creds_present_but_live_empty(tmp_path, monkeypatch):
    """Pin 2: credentialed box degrades to measured evidence when live discovery returns empty."""
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    _seed_store(cfg, {"#alpha": 100, "#beta": 90, "#gamma": 80})
    monkeypatch.setattr("fanops.persona_research.discover_corpus",
                        lambda *a, **k: [])                  # live path yields nothing
    r = refresh_persona_corpus(cfg, pid, get=_router("#ignored"))
    assert r.get("changed") is True
    assert len(core.Personas.load(cfg).get(pid).hashtag_corpus) == 4


def test_no_creds_offline_fill(tmp_path, monkeypatch):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _seed_store(cfg, {"#alpha": 100, "#beta": 90, "#gamma": 80, "#delta": 70})
    r = refresh_persona_corpus(cfg, pid)
    assert r.get("changed") is True
    assert len(core.Personas.load(cfg).get(pid).hashtag_corpus) == 4


def test_pins_preserved_and_ban_beats_pin_on_offline_fill(tmp_path, monkeypatch):
    """Pin 3: pinned tags stay; U11 ban still beats pin on the evidence-fill path."""
    from fanops.hashtags import add_ban
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#pinned")
    core.add_corpus_tag(cfg, pid, "#banned")
    add_ban(cfg, "#banned")
    _seed_store(cfg, {"#alpha": 100, "#beta": 90, "#gamma": 80})
    r = refresh_persona_corpus(cfg, pid)
    assert r.get("changed") is True
    corpus = core.Personas.load(cfg).get(pid).hashtag_corpus
    assert "#pinned" in corpus and "#banned" not in corpus
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta["#pinned"]["source"] == "pinned"


def test_corpus_auto_env_var_is_inert(tmp_path, monkeypatch):
    """REPLACES test_flag_off_byte_identical (2026-07-25). The `corpus_auto` kill switch is DELETED, so a
    stale FANOPS_CORPUS_AUTO=0 left in an operator .env must be INERT — it froze every live persona at its
    3 seed tags for nine days while each tick logged `corpora_refresh_skipped reason=disabled`. The refresh
    now runs; the 12h marker is the only brake, and it is a rate limit, not a toggle."""
    _creds(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_AUTO", "0")
    cfg = Config(root=tmp_path)
    assert not hasattr(cfg, "corpus_auto")          # the property is gone, not merely defaulted ON
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    _seed_store(cfg, {"#alpha": 100})
    r = refresh_corpora_if_due(cfg, max_age_s=0, get=_router("#alpha #beta", reach={"#alpha": 100, "#beta": 90}))
    assert r.get("refreshed") is True and r.get("reason") != "disabled"
    marker = cfg.control / ".corpora_refresh.json"
    assert marker.exists()
    r2 = refresh_corpora_if_due(cfg, max_age_s=43200, get=_router("#alpha"))   # throttle PRESERVED
    assert r2.get("refreshed") is False and r2.get("reason") == "fresh"
