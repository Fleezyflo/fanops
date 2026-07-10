# tests/test_fanops_hashtags.py
# Hashtag store builder — the ONLY judge of a hashtag is its LIVE Meta Graph reach (operator 2026-06-27).
# refresh_store harvests co-occurring candidates from the niche seeds, measures their Graph reach within the
# 30/7-day budget, ranks by reach, writes 00_control/hashtags.json. NO ledger, NO learn-doctor gate (the store
# is independent of any published post). Own-post-reach attribution (tag_reach_means/rank_tags_by_reach) and
# the doctor gate were DELETED — a post's outcome attributes to the hook/clip/account, never to the hashtag.
import inspect
import json
import pytest
from fanops.config import Config
from fanops.models import Platform
from fanops.hashtags import load_store, vetted_menu, vet_hashtags
from fanops.fanops_hashtags import refresh_store


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph_router(reach_by_tag, *, cooccur=""):
    """A fake Meta Graph `get`: ig_hashtag_search resolves '#tag'->'id-tag'; {hid}/top_media returns the
    co-occurring caption (for the harvest) + like/comments = the tag's reach (for the measurement)."""
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            return _Resp(200, {"data": [{"caption": cooccur, "like_count": reach_by_tag.get(tag, 0),
                                         "comments_count": 0}]})
        return _Resp(404, None)
    return get


def test_refresh_store_takes_no_ledger_and_no_doctor_gate(tmp_path, monkeypatch):
    # The own-reach model is gone: refresh_store's signature carries NO `led`, and it writes WITHOUT any
    # learn-doctor verdict on disk (the store does not depend on a published post).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    assert "led" not in inspect.signature(refresh_store).parameters
    cfg = Config(root=tmp_path)
    assert not (cfg.control / "learn_doctor.json").exists()     # no doctor verdict anywhere
    out = refresh_store(cfg)
    assert out["written"] is True and cfg.hashtags_path.exists()  # still writes — no gate


def test_refresh_store_ranks_by_live_graph_reach(tmp_path, monkeypatch):
    # The store is ranked by LIVE Graph reach: a higher-reach co-occurring tag leads, regardless of any post.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="Curator", id="curator")
    P.add_corpus_tag(cfg, "curator", "#seed")               # the niche seed the harvest reads from
    get = _graph_router({"#beta": 900, "#alpha": 100}, cooccur="#alpha #beta")
    out = refresh_store(cfg, get=get)
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert store[0] == "#beta"                              # highest LIVE Graph reach leads
    assert store.index("#beta") < store.index("#alpha")     # reach order, not insertion order
    assert out["measured"] >= 2


def test_refresh_store_fail_open_without_creds_writes_frozen_floor(tmp_path, monkeypatch):
    # No Meta creds -> harvest/measure no-op -> the frozen reach-ranked seed stands (never empty, never raises).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    out = refresh_store(cfg)
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["written"] is True and out["measured"] == 0
    assert store[0] == vetted_menu()[0]                     # frozen floor order, byte-stable
    assert "#hiphop" in store


def test_load_store_reads_tags_or_none(tmp_path):
    cfg = Config(root=tmp_path)
    assert load_store(cfg) is None                          # absent -> None (fall back to frozen)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#x", "#y"]}))
    assert load_store(cfg) == ["#x", "#y"]
    cfg.hashtags_path.write_text("{ corrupt")
    assert load_store(cfg) is None                          # corrupt -> None, never raises


def test_refresh_store_flag_off_writes_frozen_floor_even_with_creds(tmp_path, monkeypatch):
    # FANOPS_HASHTAG_TRENDS=0 is the operator escape hatch: the store is the frozen reach floor only — no Graph
    # harvest/measure — even when Meta creds are present. Keeps the flag meaningful (not a dead switch).
    monkeypatch.setenv("FANOPS_HASHTAG_TRENDS", "0")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    out = refresh_store(cfg, get=_graph_router({"#beta": 900}, cooccur="#beta"))   # router present but must NOT be used
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["measured"] == 0 and out["harvested"] == 0       # Graph sampling skipped
    assert store == vetted_menu()                               # frozen floor verbatim


def test_load_store_reach_reads_graph_reach_map_or_empty(tmp_path):
    # WS5: refresh_store persists {"reach": {tag: graph reach}} for the Studio surface; load_store_reach reads
    # it normalized, fail-open to {} when absent / no reach key / corrupt.
    from fanops.hashtags import load_store_reach
    cfg = Config(root=tmp_path)
    assert load_store_reach(cfg) == {}                      # absent -> {}
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"]}))     # no reach key -> {}
    assert load_store_reach(cfg) == {}
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"], "reach": {"#A": 1200, "#b": "x"}}))
    assert load_store_reach(cfg) == {"#a": 1200.0}          # normalized key; non-numeric dropped


def test_vetted_menu_uses_store_when_given_else_frozen():
    assert vetted_menu(store=["#a", "#b"]) == ["#a", "#b"]  # dynamic store drives the menu
    assert "#hiphop" in vetted_menu()                       # no store -> frozen pools


def test_vet_hashtags_store_aware_and_byte_identical_without_store():
    base = vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en")
    assert vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en", store=None) == base
    out = vet_hashtags(["#mytrend", "#garbageword"], Platform.instagram, "en", store=["#mytrend", "#second"])
    assert "#mytrend" in out and "#garbageword" not in out
    assert len(out) <= 4


# --- `hashtags discover` REPORTS fresh per-persona tags, NEVER writes the caption menu. Auto-absorbing
# unvetted discoveries into the menu was DROPPED (an engagement floor admits spam + bypasses the operator
# curation gate). Curation stays operator-gated in the Studio: discover -> operator ACCEPTS into a corpus.
def test_cmd_hashtags_discover_reports_and_writes_nothing(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    from fanops import personas as P
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Curator", id="curator")
    monkeypatch.setattr("fanops.personas.discover_corpus",
                        lambda c, pid, **k: [{"tag": "#detroitrap", "count": 9}])
    rc = cmd_hashtags_discover(cfg)
    blob = cfg.log_path.read_text()
    assert rc == 0 and "#detroitrap" in blob and "curator" in blob
    assert not cfg.hashtags_path.exists()                   # discovery NEVER writes the caption menu


def test_cmd_hashtags_discover_no_personas(tmp_path):
    import json
    from fanops.fanops_hashtags import cmd_hashtags_discover
    cfg = Config(root=tmp_path)
    rc = cmd_hashtags_discover(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 0 and any(r["outcome"] == "no_personas" for r in recs)


# --- WS2: the run loop refreshes the Graph-reach store on a throttle (constant update), fail-open.
def test_refresh_store_if_due_throttles_and_fail_open(tmp_path, monkeypatch):
    import os
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    assert refresh_store_if_due(cfg)["refreshed"] is False       # no Meta creds -> clean no-op
    assert not cfg.hashtags_path.exists()
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    assert refresh_store_if_due(cfg)["refreshed"] is True        # no store yet -> writes (fail-open frozen floor)
    assert cfg.hashtags_path.exists()
    assert refresh_store_if_due(cfg, max_age_s=43200)["refreshed"] is False   # just written -> fresh -> throttled
    old = cfg.hashtags_path.stat().st_mtime - 100000
    os.utime(cfg.hashtags_path, (old, old))
    assert refresh_store_if_due(cfg, max_age_s=10)["refreshed"] is True       # stale -> refresh again


# --- Corrupt personas.json MUST NOT clobber the curated store (MOL-12→15). _seed_tags used to swallow the
# ControlFileError Personas.load raises, coercing corrupt->[]; the refresh then overwrote the operator's
# curated store with a generic one, silently. The guard: corrupt personas -> refresh ABORTS, store untouched;
# genuinely-absent personas still rebuild from the frozen floor exactly as before.
def _write_corrupt_personas(cfg):
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text('{"personas": [oops]}')    # bareword: not valid JSON -> ControlFileError

def test_seed_tags_propagates_control_file_error_on_corrupt_personas(tmp_path):
    # MOL-12: _seed_tags no longer swallows to [] — the ControlFileError propagates so refresh_store can tell
    # "corrupt" (abort) apart from "no personas" (rebuild). Absent/empty personas still yield [] (no raise).
    from fanops.fanops_hashtags import _seed_tags
    from fanops.errors import ControlFileError
    cfg = Config(root=tmp_path)
    assert _seed_tags(cfg) == []                            # absent personas.json -> [] (legitimately empty)
    _write_corrupt_personas(cfg)
    with pytest.raises(ControlFileError):
        _seed_tags(cfg)                                     # corrupt -> propagates, NOT coerced to []

def test_refresh_store_aborts_and_preserves_store_on_corrupt_personas(tmp_path, monkeypatch):
    # MOL-13: with a curated store already on disk, a corrupt personas.json makes refresh_store ABORT — the
    # store is byte-identical afterward (the destroy-the-good-store defect is structurally impossible) and the
    # return is a non-`written` abort result carrying the reason. Meta creds present so seeding is reached.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    curated = json.dumps({"tags": ["#curatedwinner", "#second"], "reach": {"#curatedwinner": 5000}}, indent=2)
    cfg.hashtags_path.write_text(curated)                   # the operator's reach-ranked store
    _write_corrupt_personas(cfg)
    out = refresh_store(cfg, get=_graph_router({"#beta": 900}, cooccur="#beta"))
    assert out["written"] is False and out["aborted"] == "corrupt_personas"   # loud, non-success return
    assert "personas.json invalid:" in out["reason"]                          # the reason surfaces
    assert cfg.hashtags_path.read_text() == curated         # byte-identical: the curated store is UNTOUCHED

def test_refresh_store_absent_personas_rebuilds_from_floor(tmp_path, monkeypatch):
    # MOL-13: the genuinely-empty case (no personas configured) still rebuilds from the frozen floor — the
    # abort is ONLY for corrupt, never for absent. Byte-identical to the no-creds frozen-floor behavior today.
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    assert not cfg.personas_path.exists()                   # no personas at all
    out = refresh_store(cfg)
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["written"] is True and "aborted" not in out
    assert store[0] == vetted_menu()[0] and "#hiphop" in store   # frozen floor, exactly as before

def test_refresh_store_healthy_personas_output_unchanged(tmp_path, monkeypatch):
    # MOL-13: the healthy path is byte-identical to today — a valid personas.json produces the same store the
    # pre-guard code did. (The guard only ADDS a corrupt-abort branch; the clean run is untouched.)
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="Curator", id="curator"); P.add_corpus_tag(cfg, "curator", "#seed")
    out = refresh_store(cfg, get=_graph_router({"#beta": 900, "#alpha": 100}, cooccur="#alpha #beta"))
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["written"] is True and "aborted" not in out
    assert store[0] == "#beta" and store.index("#beta") < store.index("#alpha")   # reach order preserved

def test_refresh_store_if_due_corrupt_personas_reports_reason_never_raises(tmp_path, monkeypatch):
    # MOL-14: the unattended tick keeps its fail-open contract (never raises into the run) AND surfaces the
    # corrupt-abort as a REPORTED reason — `refreshed` is False, the reason names the abort, and a curated
    # store on disk is preserved byte-identical. Meta creds present so the tick reaches the refresh.
    import os
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    curated = json.dumps({"tags": ["#curatedwinner"], "reach": {}}, indent=2)
    cfg.hashtags_path.write_text(curated)
    _write_corrupt_personas(cfg)                            # the broken control file that must not clobber the store
    old = cfg.hashtags_path.stat().st_mtime - 100000
    os.utime(cfg.hashtags_path, (old, old))                 # make it stale so the throttle doesn't short-circuit
    r = refresh_store_if_due(cfg, max_age_s=10, get=_graph_router({"#beta": 900}, cooccur="#beta"))  # must NOT raise
    assert r["refreshed"] is False and r["aborted"] == "corrupt_personas"
    assert "personas.json invalid:" in r["reason"]
    assert cfg.hashtags_path.read_text() == curated         # curated store preserved

def test_refresh_store_writes_reach_map_with_per_tag_live_scores(tmp_path, monkeypatch):
    # mol-hashtag-reach-graph-2c21: refresh_store persists {"reach": {tag: score}} where `score` is the
    # LIVE Meta Graph engagement (sum of top_media like+comment counts via trend_score).  The "reach" key
    # is what load_store_reach + the Studio Personas tab consume.  Operator invariant (2026-06-27): a tag's
    # worth is its LIVE platform reach — never a post's outcome.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="Curator", id="curator"); P.add_corpus_tag(cfg, "curator", "#seed")
    get = _graph_router({"#beta": 900, "#alpha": 100}, cooccur="#alpha #beta")
    out = refresh_store(cfg, get=get)
    blob = json.loads(cfg.hashtags_path.read_text())
    assert out["written"] is True
    assert "reach" in blob                                  # reach key present in written file
    assert blob["reach"].get("#beta") == 900                # LIVE Graph engagement (mocked like_count 900)
    assert blob["reach"].get("#alpha") == 100               # LIVE Graph engagement (mocked like_count 100)
    assert out["measured"] == len(blob["reach"])            # summary count matches the persisted map


def test_cmd_hashtags_refresh_corrupt_personas_exits_2_and_no_keyerror(tmp_path, monkeypatch):
    # MOL-13 caller contract: `fanops hashtags refresh` used to index r['measured']/['harvested']/['total']
    # unconditionally and always exit 0. On a corrupt-abort it must NOT KeyError on the abort shape — it logs
    # the reason loudly and exits 2. The healthy verb still logs its summary and exits 0.
    import json
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    _write_corrupt_personas(cfg)
    rc = cmd_hashtags_refresh(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 2                                          # loud non-zero exit, no KeyError
    aborted = next(r for r in recs if r["outcome"] == "refresh_aborted")
    assert "personas.json invalid:" in aborted.get("reason", "")
