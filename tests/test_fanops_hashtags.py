# tests/test_fanops_hashtags.py
# Layer A — the ONLY code that touches the Graph for hashtags and the only writer of the measurement
# cache (00_control/hashtags.json). One pass per POSTING persona: description -> terms -> anchor tags ->
# ONE top_media fetch per tag -> {like_count, co-occurring tags}. NO ledger, NO doctor gate, NO local
# budget (Meta's own refusals are the sole governor — see test_hashtag_platform_truth.py).
# This file owns the DRIVER contract: the written file's shape + order, accrual, the corrupt-personas
# abort, the 12h throttle, and the two CLI verbs.
import inspect
import json
import pytest
from fanops.config import Config
from fanops.hashtags import METRIC_FIELD, load_measurements
from fanops.fanops_hashtags import refresh_store


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph_router(metric_by_tag, *, cooccur=""):
    """A fake Meta Graph `get`: ig_hashtag_search resolves '<q>'->'id-<q>'; {hid}/top_media returns the
    co-occurring caption (for the harvest) + the tag's verbatim like_count (for the measurement). A tag
    this router does not know returns empty top_media — Meta published nothing, so it stays UNMEASURED."""
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            if tag not in metric_by_tag:
                return _Resp(200, {"data": []})
            return _Resp(200, {"data": [{"caption": cooccur, "like_count": metric_by_tag[tag],
                                         "comments_count": 0}]})
        return _Resp(200, {"data": []})
    return get


def _dead_router(url, params=None, timeout=None):
    """Meta answers with a refusal body — proves the pass records it instead of inventing a silent miss."""
    return _Resp(404, {"error": {"code": 100, "message": "dead"}})


def _persona(cfg, *, pid="curator"):
    """A persona whose niche is ONE declared term, so persona_terms yields exactly one anchor
    (#hiphop) and the harvest attribution is unambiguous. Linked to an ACTIVE account, because
    _posting_personas narrows discovery to the personas that actually post."""
    from fanops import personas as P
    P.add_persona(cfg, name="Hiphop", voice="any register", niche=["hiphop"], id=pid)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    return pid


def test_refresh_store_atomic_write_preserves_prior_on_crash(tmp_path, monkeypatch):
    # L08: a crash mid-write must leave the PRIOR valid hashtags.json intact (write_json_atomic).
    from fanops import controlio
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    refresh_store(cfg)                                      # establish a valid cache file
    good = cfg.hashtags_path.read_text()
    real_replace = controlio.os.replace
    def boom(src, dst):
        raise OSError("simulated crash during replace")
    monkeypatch.setattr(controlio.os, "replace", boom)
    with pytest.raises(OSError):
        refresh_store(cfg)
    monkeypatch.setattr(controlio.os, "replace", real_replace)
    assert cfg.hashtags_path.read_text() == good


def test_refresh_store_takes_no_ledger_and_no_doctor_gate(tmp_path, monkeypatch):
    # The own-reach model is gone: refresh_store's signature carries NO `led`, and it writes WITHOUT any
    # learn-doctor verdict on disk (the cache does not depend on a published post).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    assert "led" not in inspect.signature(refresh_store).parameters
    cfg = Config(root=tmp_path)
    assert not (cfg.control / "learn_doctor.json").exists()     # no doctor verdict anywhere
    out = refresh_store(cfg)
    assert out["written"] is True and cfg.hashtags_path.exists()  # still writes — no gate


def test_written_file_is_the_flat_record_shape_ranked_by_the_metric(tmp_path, monkeypatch):
    # The cache is `{tag: {graph_id, like_count, measured_at, from}}` written in metric-DESC order, so a
    # reader that just iterates the file already has the menu. `last_complete_pass` is a sibling stamp,
    # not a tag record (MOL-525).
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, get=_graph_router({"#hiphop": 500, "#beta": 900, "#alpha": 100},
                                         cooccur="#alpha #beta"))
    blob = json.loads(cfg.hashtags_path.read_text())
    tags = {k: v for k, v in blob.items() if isinstance(v, dict)}
    assert list(tags) == sorted(tags, key=lambda t: (-tags[t][METRIC_FIELD], t))   # metric desc on disk
    assert blob["#beta"]["graph_id"] == "id-beta" and blob["#beta"]["measured_at"]
    assert blob["#beta"]["from"] == {"#hiphop": 1}          # the anchor whose top media surfaced it
    assert isinstance(blob.get("last_complete_pass"), str) and blob["last_complete_pass"]
    assert "reach" not in json.dumps(blob)                  # no invented metric key survives


def test_measurements_accrue_across_passes(tmp_path, monkeypatch):
    # A later pass that discovers a DIFFERENT slice must ADD to the evidence, not replace it: candidates
    # rotate as the anchor's top media change, and the cache must accumulate what each pass bought. Pass 2
    # cannot even re-measure #alpha (Meta refuses it) — its pass-1 record must still stand.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, get=_graph_router({"#hiphop": 500, "#alpha": 100}, cooccur="#alpha"))
    refresh_store(cfg, get=_graph_router({"#hiphop": 500, "#beta": 900}, cooccur="#beta"))
    m = load_measurements(cfg)
    assert "#alpha" in m and "#beta" in m                    # both slices survive
    assert m["#alpha"][METRIC_FIELD] == 100                  # an unreachable tag keeps its prior evidence
    assert m["#beta"][METRIC_FIELD] == 900


def test_refresh_store_without_creds_measures_nothing_and_never_calls_out(tmp_path, monkeypatch):
    # No Meta creds -> resolve_hashtag short-circuits BEFORE any request, so the pass measures nothing and
    # writes an EMPTY cache. Honest: there is no frozen floor left to pad it with.
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path); _persona(cfg)
    calls: list = []
    def spy(url, params=None, timeout=None):
        calls.append(url); return _Resp(404, None)
    out = refresh_store(cfg, get=spy)
    assert out["written"] is True and out["measured"] == 0
    assert calls == []                                       # never hits the network without creds
    assert load_measurements(cfg) == {}                      # no invented measurements
    assert isinstance(json.loads(cfg.hashtags_path.read_text()).get("last_complete_pass"), str)


# --- `hashtags discover` is the per-persona niche REPORT: READ-ONLY and ZERO NETWORK ------------------
def test_cmd_hashtags_discover_reports_and_writes_nothing(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); pid = _persona(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"#detroitrap": {
        "graph_id": "id-detroitrap", METRIC_FIELD: 4200.0,
        "measured_at": datetime.now(timezone.utc).isoformat(), "from": {"#hiphop": 3}}}))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("discover must spend no Graph call"))
    before = cfg.hashtags_path.read_text()
    rc = cmd_hashtags_discover(cfg)
    blob = cfg.log_path.read_text()
    assert rc == 0 and "#detroitrap" in blob and pid in blob
    assert cfg.hashtags_path.read_text() == before           # a report never mutates the cache


def test_cmd_hashtags_discover_no_personas(tmp_path):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    cfg = Config(root=tmp_path)
    rc = cmd_hashtags_discover(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 0 and any(r["outcome"] == "no_personas" for r in recs)


# --- the run loop refreshes the cache on a 12h throttle (constant update), fail-open ------------------
def test_refresh_store_if_due_throttles_and_fail_open(tmp_path, monkeypatch):
    # MOL-525: gate on last_complete_pass inside the cache, NOT file mtime (a throttled write still bumps mtime).
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    assert refresh_store_if_due(cfg)["refreshed"] is False        # no Meta creds -> clean no-op
    assert refresh_store_if_due(cfg)["reason"] == "no Meta creds"
    assert not cfg.hashtags_path.exists()
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    assert refresh_store_if_due(cfg, get=_dead_router)["refreshed"] is True   # no cache yet -> writes
    assert cfg.hashtags_path.exists()
    assert isinstance(json.loads(cfg.hashtags_path.read_text()).get("last_complete_pass"), str)
    assert refresh_store_if_due(cfg, max_age_s=43200, get=_dead_router)["refreshed"] is False  # fresh -> skip
    assert refresh_store_if_due(cfg, max_age_s=43200, get=_dead_router)["reason"] == "fresh"
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    assert refresh_store_if_due(cfg, max_age_s=10, get=_dead_router)["refreshed"] is True      # stamp stale -> refresh


def test_throttled_pass_does_not_advance_complete_stamp(tmp_path, monkeypatch):
    """D-2 / MOL-525 (1): throttled writes must not buy (or extend) the 12h silence window."""
    import fanops.meta_graph as mg
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    monkeypatch.setattr(mg, "_sleep", lambda *_: None)
    monkeypatch.setattr(mg, "_MAX_RL_RETRIES", 0)
    cfg = Config(root=tmp_path); _persona(cfg)

    def throttle_get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            return _Resp(400, {"error": {"code": 4, "message": "rate"}})
        return _Resp(404, {"error": {"code": 100, "message": "x"}})

    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out0 = refresh_store(cfg, get=throttle_get, now=t0)          # first pass ever — Meta cuts off immediately
    assert out0["throttled"] is True and "last_complete_pass" not in json.loads(cfg.hashtags_path.read_text())
    # Old mtime gate would skip for 12h; missing completion stamp must keep the tick due.
    assert refresh_store_if_due(cfg, max_age_s=43200, get=_dead_router, now=t0 + timedelta(minutes=5)
                                )["refreshed"] is True

    refresh_store(cfg, get=_graph_router({"#hiphop": 50}), now=t0 + timedelta(hours=1))  # complete
    stamp = json.loads(cfg.hashtags_path.read_text())["last_complete_pass"]
    t_th = t0 + timedelta(hours=2)
    out1 = refresh_store(cfg, get=throttle_get, now=t_th)         # later incomplete pass
    assert out1["throttled"] is True
    assert json.loads(cfg.hashtags_path.read_text())["last_complete_pass"] == stamp
    # Stamp must not slide forward with the throttled write (would extend silence past the real complete).
    skip = refresh_store_if_due(cfg, max_age_s=43200, get=_dead_router, now=t_th + timedelta(minutes=1))
    assert skip["refreshed"] is False and skip["reason"] == "fresh"


def test_stalest_remeasure_reaches_known_before_fresh_anchor(tmp_path, monkeypatch):
    """D-2 / MOL-525 (2): under throttle, a stale known tag must beat a freshly-measured anchor."""
    import fanops.meta_graph as mg
    from fanops import personas as P
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    monkeypatch.setattr(mg, "_sleep", lambda *_: None)
    monkeypatch.setattr(mg, "_MAX_RL_RETRIES", 0)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="A", voice="x", niche=["freshanchor"], id="a")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#freshanchor": {"graph_id": "id-freshanchor", METRIC_FIELD: 10.0,
                         "measured_at": "2026-07-01T12:00:00+00:00"},
        "#staletail": {"graph_id": "id-staletail", METRIC_FIELD: 20.0,
                       "measured_at": "2026-01-01T00:00:00+00:00", "from": {"#freshanchor": 1}},
        "last_complete_pass": "2026-07-01T12:00:00+00:00"}))
    order: list = []
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            order.append(tag)
            if len(order) >= 2:                                  # measure one, then Meta stops the pass
                return _Resp(400, {"error": {"code": 4, "message": "rate"}})
            return _Resp(200, {"data": [{"caption": "", "like_count": 21, "comments_count": 0}]})
        return _Resp(404, {"error": {"code": 100}})
    out = refresh_store(cfg, get=get, now=datetime_for_pass())
    assert out["throttled"] is True
    assert order[0] == "#staletail", "stalest known must be first — not the fresh anchor"


def datetime_for_pass():
    from datetime import datetime, timezone
    return datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)


def test_run_tick_logs_non_fresh_hashtag_skip(tmp_path, monkeypatch):
    """D-2 / MOL-525 (3): no-creds / error skips must log; `fresh` stays quiet (same class as corpora)."""
    from fanops.cli import _cmd_run_pass
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _cmd_run_pass(cfg, "2026-07-02T00:00:00Z")
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    skipped = [r for r in recs if r.get("outcome") == "store_refresh_skipped"]
    assert skipped and skipped[0].get("reason") == "no Meta creds"


# --- Corrupt personas.json MUST NOT clobber the accrued cache (MOL-12→15). Personas.load raises
# ControlFileError; refresh_store turns that into a loud abort and leaves the file byte-identical, while a
# genuinely-ABSENT personas.json is not an abort (it just measures nothing).
def _write_corrupt_personas(cfg):
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text('{"personas": [oops]}')    # bareword: not valid JSON -> ControlFileError


def test_refresh_store_aborts_and_preserves_cache_on_corrupt_personas(tmp_path, monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    accrued = json.dumps({"#measured": {"graph_id": "id-measured", METRIC_FIELD: 5000.0,
                                        "measured_at": "2026-07-20T00:00:00+00:00"}}, indent=2)
    cfg.hashtags_path.write_text(accrued)                   # evidence already bought
    _write_corrupt_personas(cfg)
    out = refresh_store(cfg, get=_graph_router({"#beta": 900}, cooccur="#beta"))
    assert out["written"] is False and out["aborted"] == "corrupt_personas"   # loud, non-success return
    assert "personas.json invalid:" in out["reason"]                          # the reason surfaces
    assert cfg.hashtags_path.read_text() == accrued          # byte-identical: the evidence is UNTOUCHED


def test_refresh_store_absent_personas_is_not_an_abort(tmp_path, monkeypatch):
    # The abort is ONLY for corrupt, never for absent: with no personas there are no anchors, so the pass
    # measures nothing and writes cleanly.
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    assert not cfg.personas_path.exists()
    out = refresh_store(cfg)
    assert out["written"] is True and "aborted" not in out and out["measured"] == 0


def test_refresh_store_if_due_corrupt_personas_reports_reason_never_raises(tmp_path, monkeypatch):
    # MOL-14: the unattended tick keeps its fail-open contract (never raises into the run) AND surfaces the
    # corrupt-abort as a REPORTED reason, with the accrued cache preserved byte-identical.
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    accrued = json.dumps({"#measured": {"graph_id": "id-measured", METRIC_FIELD: 1.0,
                                        "measured_at": "2026-07-20T00:00:00+00:00"}}, indent=2)
    cfg.hashtags_path.write_text(accrued)                   # no last_complete_pass -> due every tick
    _write_corrupt_personas(cfg)                            # the broken control file that must not clobber
    r = refresh_store_if_due(cfg, max_age_s=10, get=_graph_router({"#beta": 900}))   # must NOT raise
    assert r["refreshed"] is False and r["aborted"] == "corrupt_personas"
    assert "personas.json invalid:" in r["reason"]
    assert cfg.hashtags_path.read_text() == accrued          # accrued cache preserved


def test_cmd_hashtags_refresh_corrupt_personas_exits_2_and_no_keyerror(tmp_path, monkeypatch):
    # MOL-13 caller contract: `fanops hashtags refresh` must NOT KeyError on the abort shape — it logs the
    # reason loudly and exits 2. The healthy verb still logs its summary and exits 0.
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    _write_corrupt_personas(cfg)
    rc = cmd_hashtags_refresh(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 2                                          # loud non-zero exit, no KeyError
    aborted = next(r for r in recs if r["outcome"] == "refresh_aborted")
    assert "personas.json invalid:" in aborted.get("reason", "")


def test_refresh_store_routes_novel_searches_per_persona_ig_user_id(tmp_path, monkeypatch):
    """Two posting personas on two accounts with distinct ig_user_ids: novel searches must hit different user_id params."""
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "GLOBAL")
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="A", voice="a", niche=["alpha"], id="pa")
    P.add_persona(cfg, name="B", voice="b", niche=["beta"], id="pb")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "h_a", "platforms": ["instagram"], "status": "active", "persona_id": "pa", "ig_user_id": "IG-A"},
        {"handle": "h_b", "platforms": ["instagram"], "status": "active", "persona_id": "pb", "ig_user_id": "IG-B"},
    ]}))
    calls = []
    def get(url, params=None, timeout=None):
        p = params or {}
        calls.append((url, dict(p)))
        if "recently_searched_hashtags" in url:
            return _Resp(200, {"data": []})
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            return _Resp(200, {"data": [{"caption": "", "like_count": 10}]})
        return _Resp(200, {"data": []})
    out = refresh_store(cfg, get=get)
    assert out["written"] is True
    search_uids = sorted({p.get("user_id") for u, p in calls if "ig_hashtag_search" in u})
    assert search_uids == ["IG-A", "IG-B"], search_uids

