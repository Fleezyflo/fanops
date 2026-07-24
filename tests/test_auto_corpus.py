# S12 — automated persona corpus refresh (backend-only): throttle, fill-to-target, pin protection,
# content screen, self-prune, live-vs-offline fill, and the inertness of the DELETED corpus_auto env var.
# Local hashtag_budget.json never refuses Graph calls (observational meter only).
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


def test_local_meter_full_still_attempts_graph(tmp_path, monkeypatch):
    """Even with 30 local meter entries, live discovery still hits the Graph when creds exist."""
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    for i in range(30):
        record_query(cfg, f"#t{i}")
    called = {"n": 0}
    def _counting_get(url, params=None, timeout=None):
        called["n"] += 1
        return _router("#fresh")(url, params=params, timeout=timeout)
    r = refresh_persona_corpus(cfg, pid, get=_counting_get)
    assert called["n"] > 0, "local meter full must not refuse Graph"
    assert r.get("reason") != "budget_exhausted"


def test_offline_fill_when_no_creds(tmp_path, monkeypatch):
    """Measured-evidence fill runs when Meta creds are absent (live cannot run)."""
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "4")
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#seed")
    _seed_store(cfg, {"#alpha": 100, "#beta": 90, "#gamma": 80, "#delta": 70})
    called = {"n": 0}
    def _no_graph(url, params=None, timeout=None):
        called["n"] += 1; return _Resp(404, None)
    r = refresh_persona_corpus(cfg, pid, get=_no_graph)
    assert called["n"] == 0, "no-creds path must not touch the Graph"
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


# --- apply_auto_corpus meta-merge gate + orphaned-auto repair (hashtag-graph-loop unit) ---

def test_apply_auto_corpus_writes_meta_for_brand_new_auto_tag(tmp_path, monkeypatch):
    """Live bug: brand-new auto tag must land with source=auto sidecar (absent-as-pinned must NOT gate the write)."""
    from fanops.persona_store import apply_auto_corpus
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    apply_auto_corpus(cfg, pid, tags=["#fresh"], meta={
        "#fresh": {"source": "auto", "reach": 42.0, "added": "2026-07-25T00:00:00+00:00"},
    })
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta["#fresh"]["source"] == "auto"
    assert meta["#fresh"]["reach"] == 42.0


def test_apply_auto_corpus_updates_reach_on_existing_auto(tmp_path, monkeypatch):
    from fanops.persona_store import apply_auto_corpus
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _write_meta(cfg, pid, ["#auto1"], {
        "#auto1": {"source": "auto", "reach": 1.0, "added": "2026-01-01T00:00:00+00:00"},
    })
    apply_auto_corpus(cfg, pid, tags=["#auto1"], meta={
        "#auto1": {"source": "auto", "reach": 999.0, "added": "2026-07-25T00:00:00+00:00"},
    })
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta["#auto1"]["source"] == "auto" and meta["#auto1"]["reach"] == 999.0


def test_apply_auto_corpus_refuses_overwrite_explicit_pin(tmp_path, monkeypatch):
    from fanops.persona_store import apply_auto_corpus
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#pinned")
    before = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]["#pinned"]
    apply_auto_corpus(cfg, pid, tags=["#pinned", "#other"], meta={
        "#pinned": {"source": "auto", "reach": 999.0, "added": "2026-07-25T00:00:00+00:00"},
        "#other": {"source": "auto", "reach": 10.0, "added": "2026-07-25T00:00:00+00:00"},
    })
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta["#pinned"]["source"] == "pinned"
    assert meta["#pinned"] == before
    assert meta["#other"]["source"] == "auto"


def test_legacy_meta_absent_not_in_store_stays_partitioned_pinned(tmp_path, monkeypatch):
    """PARTITION path: legacy corpus tag with NO meta and NOT in store evidence remains pinned."""
    from fanops.persona_research import _partition_corpus
    from fanops.persona_store import repair_orphaned_auto_meta
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _write_meta(cfg, pid, ["#legacy"], {})          # no meta entry at all
    _seed_store(cfg, {"#unrelated": 50})            # store evidence for a DIFFERENT tag
    n = repair_orphaned_auto_meta(cfg, pid)
    assert n == 0
    row = json.loads(cfg.personas_path.read_text())["personas"][0]
    assert "#legacy" not in (row.get("hashtag_corpus_meta") or {})
    pinned, auto = _partition_corpus(row["hashtag_corpus"], row.get("hashtag_corpus_meta") or {})
    assert pinned == ["#legacy"] and auto == []


def test_repair_stamps_auto_onto_meta_absent_with_store_evidence(tmp_path, monkeypatch):
    """Broken-fill repair: meta-absent + store graph-reach → source=auto; explicit pins untouched."""
    from fanops.persona_store import repair_orphaned_auto_meta
    from fanops.persona_research import _partition_corpus
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _write_meta(cfg, pid, ["#orphan", "#pinned", "#truelegacy"], {
        "#pinned": {"source": "pinned", "reach": None, "added": "2026-01-01T00:00:00+00:00"},
        # #orphan and #truelegacy deliberately have NO meta (broken fill / true legacy)
    })
    _seed_store(cfg, {"#orphan": 777})              # only #orphan has store evidence
    n = repair_orphaned_auto_meta(cfg, pid)
    assert n == 1
    row = json.loads(cfg.personas_path.read_text())["personas"][0]
    meta = row["hashtag_corpus_meta"]
    assert meta["#orphan"]["source"] == "auto" and meta["#orphan"]["reach"] == 777.0
    assert meta["#pinned"]["source"] == "pinned"
    assert "#truelegacy" not in meta                # no store evidence → stay legacy-absent (=pinned)
    n2 = repair_orphaned_auto_meta(cfg, pid)
    assert n2 == 0                                  # idempotent
    pinned, auto = _partition_corpus(row["hashtag_corpus"], meta)
    assert "#orphan" in auto and "#pinned" in pinned and "#truelegacy" in pinned
