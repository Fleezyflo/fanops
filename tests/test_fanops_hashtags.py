# tests/test_fanops_hashtags.py
# Layer A — instagrapi is the network source; the only writer of the measurement cache
# (00_control/hashtags.json). One pass per POSTING persona: description -> terms -> anchor tags ->
# ONE medias_top fetch per tag -> {like_count, co-occurring tags}. NO ledger, NO doctor gate, NO local
# budget (Instagram throttle is the sole governor — see test_hashtag_platform_truth.py).
# This file owns the DRIVER contract: the written file's shape + order, accrual, the corrupt-personas
# abort, the 12h throttle, and the CLI verbs.
import inspect
import json
import pytest
from fanops.config import Config
from fanops.hashtags import METRIC_FIELD, load_measurements
from fanops.fanops_hashtags import refresh_store
from hashtag_scrape_fakes import _FakeClient


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
    cfg = Config(root=tmp_path)
    client = _FakeClient({})
    refresh_store(cfg, scrape_client=client)                # establish a valid cache file
    good = cfg.hashtags_path.read_text()
    real_replace = controlio.os.replace
    def boom(src, dst):
        raise OSError("simulated crash during replace")
    monkeypatch.setattr(controlio.os, "replace", boom)
    with pytest.raises(OSError):
        refresh_store(cfg, scrape_client=client)
    monkeypatch.setattr(controlio.os, "replace", real_replace)
    assert cfg.hashtags_path.read_text() == good


def test_refresh_store_midpass_flush_survives_later_crash(tmp_path, monkeypatch):
    # Every 5 successful measures flushes the cache WITHOUT stamping last_complete_pass.
    # A crash after that flush must keep the accrued tags (not roll back to empty/prior-only).
    from fanops import controlio, personas as P
    cfg = Config(root=tmp_path)
    niches = [f"seed{i}" for i in range(6)]                 # 6 anchors → flush at measured=5, then final
    P.add_persona(cfg, name="Mid", voice="x", niche=niches, id="mid")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "mid"}]}))
    metrics = {f"#{n}": float(10 + i) for i, n in enumerate(niches)}
    n_writes = {"n": 0}
    real_replace = controlio.os.replace

    def boom_after_first_flush(src, dst):
        n_writes["n"] += 1
        if n_writes["n"] == 1:
            return real_replace(src, dst)                   # mid-pass flush lands
        raise OSError("crash on later write")

    monkeypatch.setattr(controlio.os, "replace", boom_after_first_flush)
    with pytest.raises(OSError):
        refresh_store(cfg, scrape_client=_FakeClient(metrics))
    monkeypatch.setattr(controlio.os, "replace", real_replace)
    raw = json.loads(cfg.hashtags_path.read_text())
    assert "last_complete_pass" not in raw                  # partial flush must not buy 12h silence
    tags = [k for k in raw if k.startswith("#")]
    assert len(tags) == 5                                   # accrued through the mid-pass flush


def test_refresh_store_takes_no_ledger_and_no_doctor_gate(tmp_path, monkeypatch):
    # The own-reach model is gone: refresh_store's signature carries NO `led`, and it writes WITHOUT any
    # learn-doctor verdict on disk (the cache does not depend on a published post).
    assert "led" not in inspect.signature(refresh_store).parameters
    assert "get" not in inspect.signature(refresh_store).parameters
    assert "scrape_client" in inspect.signature(refresh_store).parameters
    cfg = Config(root=tmp_path)
    assert not (cfg.control / "learn_doctor.json").exists()     # no doctor verdict anywhere
    out = refresh_store(cfg, scrape_client=_FakeClient({}))
    assert out["written"] is True and cfg.hashtags_path.exists()  # still writes — no gate
    assert out.get("backend") == "scrape"


def test_written_file_is_the_flat_record_shape_ranked_by_the_metric(tmp_path, monkeypatch):
    # The cache is `{tag: {graph_id, like_count, measured_at, from}}` written in metric-DESC order, so a
    # reader that just iterates the file already has the menu. `last_complete_pass` is a sibling stamp,
    # not a tag record (MOL-525).
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, scrape_client=_FakeClient(
        {"#hiphop": 500, "#beta": 900, "#alpha": 100}, cooccur="#alpha #beta"))
    blob = json.loads(cfg.hashtags_path.read_text())
    tags = {k: v for k, v in blob.items() if isinstance(v, dict)}
    assert list(tags) == sorted(tags, key=lambda t: (-tags[t][METRIC_FIELD], t))   # metric desc on disk
    assert blob["#beta"]["graph_id"] == "id-beta" and blob["#beta"]["measured_at"]
    assert blob["#beta"]["from"] == {"#hiphop": 1}          # the anchor whose top media surfaced it
    assert isinstance(blob.get("last_complete_pass"), str) and blob["last_complete_pass"]
    assert "reach" not in json.dumps(blob)                  # no invented metric key survives


def test_measurements_accrue_across_passes(tmp_path, monkeypatch):
    # A later pass that discovers a DIFFERENT slice must ADD to the evidence, not replace it.
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#alpha": 100}, cooccur="#alpha"))
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#beta": 900}, cooccur="#beta"))
    m = load_measurements(cfg)
    assert "#alpha" in m and "#beta" in m
    assert m["#alpha"][METRIC_FIELD] == 100
    assert m["#beta"][METRIC_FIELD] == 900


def test_refresh_store_no_scrape_aborts_loudly(tmp_path, monkeypatch):
    # Missing scrape -> written:False / aborted:no_scrape. No silent Graph fallback; cache untouched.
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path); _persona(cfg)
    out = refresh_store(cfg)
    assert out["written"] is False and out["aborted"] == "no_scrape"
    assert not cfg.hashtags_path.exists()


def test_cmd_hashtags_discover_reports_and_writes_nothing(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); pid = _persona(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"#detroitrap": {
        "graph_id": "id-detroitrap", METRIC_FIELD: 4200.0,
        "measured_at": datetime.now(timezone.utc).isoformat(), "from": {"#hiphop": 3}}}))
    before = cfg.hashtags_path.read_text()
    rc = cmd_hashtags_discover(cfg)
    blob = cfg.log_path.read_text()
    assert rc == 0 and "#detroitrap" in blob and pid in blob
    assert "instagrapi" in blob
    assert cfg.hashtags_path.read_text() == before


def test_cmd_hashtags_discover_no_personas(tmp_path):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    cfg = Config(root=tmp_path)
    rc = cmd_hashtags_discover(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 0 and any(r["outcome"] == "no_personas" for r in recs)


def test_refresh_store_if_due_throttles_and_fail_open(tmp_path, monkeypatch):
    # MOL-525: gate on last_complete_pass inside the cache, NOT file mtime.
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path)
    assert refresh_store_if_due(cfg)["refreshed"] is False
    assert refresh_store_if_due(cfg)["reason"] == "no scrape session"
    assert not cfg.hashtags_path.exists()
    client = _FakeClient({})
    assert refresh_store_if_due(cfg, scrape_client=client)["refreshed"] is True
    assert cfg.hashtags_path.exists()
    assert isinstance(json.loads(cfg.hashtags_path.read_text()).get("last_complete_pass"), str)
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client)["refreshed"] is False
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client)["reason"] == "fresh"
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    assert refresh_store_if_due(cfg, max_age_s=10, scrape_client=client)["refreshed"] is True


def test_throttled_pass_does_not_advance_complete_stamp(tmp_path, monkeypatch):
    """D-2 / MOL-525 (1): throttled writes must not buy (or extend) the 12h silence window."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out0 = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t0)
    assert out0["throttled"] is True and "last_complete_pass" not in json.loads(cfg.hashtags_path.read_text())
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=_FakeClient({}),
                                now=t0 + timedelta(minutes=5))["refreshed"] is True
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t0 + timedelta(hours=1))
    stamp = json.loads(cfg.hashtags_path.read_text())["last_complete_pass"]
    t_th = t0 + timedelta(hours=2)
    out1 = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t_th)
    assert out1["throttled"] is True
    assert json.loads(cfg.hashtags_path.read_text())["last_complete_pass"] == stamp
    skip = refresh_store_if_due(cfg, max_age_s=43200, scrape_client=_FakeClient({}),
                                now=t_th + timedelta(minutes=1))
    assert skip["refreshed"] is False and skip["reason"] == "fresh"


def test_stalest_remeasure_reaches_known_before_fresh_anchor(tmp_path, monkeypatch):
    """D-2 / MOL-525 (2): under throttle, a stale known tag must beat a freshly-measured anchor."""
    from fanops import personas as P
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
    client = _FakeClient({"#freshanchor": 11, "#staletail": 21}, throttle_after=1)
    out = refresh_store(cfg, scrape_client=client, now=datetime_for_pass())
    assert out["throttled"] is True
    assert client.media_calls[0] == "staletail", "stalest known must be first — not the fresh anchor"


def test_refresh_store_try_cap_ends_pass_without_complete_stamp(tmp_path, monkeypatch):
    """Scrape pass budget: stop after _SCRAPE_TRY_CAP tries, write evidence, do NOT stamp complete."""
    import fanops.fanops_hashtags as fh
    monkeypatch.setattr(fh, "_SCRAPE_TRY_CAP", 2)
    monkeypatch.setattr(fh, "_SCRAPE_COTAG_ENQUEUE_CAP", 0)   # no co-tag expansion in this proof
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="A", voice="x", niche=["alpha", "beta", "gamma"], id="a")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    client = _FakeClient({"#alpha": 10, "#beta": 20, "#gamma": 30})
    out = refresh_store(cfg, scrape_client=client)
    assert out["throttled"] is True and out["tried"] == 2 and out["measured"] == 2
    assert "last_complete_pass" not in json.loads(cfg.hashtags_path.read_text())
    assert len(client.media_calls) == 2


def test_refresh_store_cotag_enqueue_cap(tmp_path, monkeypatch):
    """One anchor can harvest dozens of co-tags; only _SCRAPE_COTAG_ENQUEUE_CAP are measured this pass."""
    import fanops.fanops_hashtags as fh
    monkeypatch.setattr(fh, "_SCRAPE_TRY_CAP", 50)
    monkeypatch.setattr(fh, "_SCRAPE_COTAG_ENQUEUE_CAP", 2)
    cfg = Config(root=tmp_path); _persona(cfg)
    # 5 co-tags in caption — only 2 may join the queue
    co = "#c1 #c2 #c3 #c4 #c5"
    metrics = {"#hiphop": 100, "#c1": 1, "#c2": 2, "#c3": 3, "#c4": 4, "#c5": 5}
    client = _FakeClient(metrics, cooccur=co)
    out = refresh_store(cfg, scrape_client=client)
    assert out["discovered"] == 2
    m = load_measurements(cfg)
    assert "#hiphop" in m
    measured_cos = [t for t in ("#c1", "#c2", "#c3", "#c4", "#c5") if t in m]
    assert len(measured_cos) == 2


def test_refresh_store_cotags_measure_before_remeasure(tmp_path, monkeypatch):
    """Harvested co-tags must run BEFORE stale remesure — append put them behind the whole cache and
    starved craft/burner expansion under try_cap."""
    import fanops.fanops_hashtags as fh
    monkeypatch.setattr(fh, "_SCRAPE_TRY_CAP", 2)            # hiphop + cotag only; remesure must not steal
    monkeypatch.setattr(fh, "_SCRAPE_COTAG_ENQUEUE_CAP", 5)
    cfg = Config(root=tmp_path); _persona(cfg)
    # Pre-seed a STALE non-anchor so remesure would eat the try_cap if cotags append at the end.
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#oldnoise": {"graph_id": "id-oldnoise", METRIC_FIELD: 9.0,
                      "measured_at": "2020-01-01T00:00:00+00:00"},
        "last_complete_pass": "2020-01-01T00:00:00+00:00"}))
    client = _FakeClient(
        {"#hiphop": 100, "#freshco": 50, "#oldnoise": 9},
        cooccur="#freshco",
    )
    out = refresh_store(cfg, scrape_client=client)
    assert out["throttled"] is True and out["tried"] == 2
    # Under try_cap=2 with insert-priority: hiphop then freshco. Append-priority would be hiphop, oldnoise.
    assert client.media_calls == ["hiphop", "freshco"]
    m = load_measurements(cfg)
    assert "#freshco" in m and m["#freshco"].get("from", {}).get("#hiphop")


def datetime_for_pass():
    from datetime import datetime, timezone
    return datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)


def test_run_tick_logs_non_fresh_hashtag_skip(tmp_path, monkeypatch):
    """D-2 / MOL-525 (3): no-scrape / error skips must log; `fresh` stays quiet."""
    from fanops.cli import _cmd_run_pass
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _cmd_run_pass(cfg, "2026-07-02T00:00:00Z")
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    skipped = [r for r in recs if r.get("outcome") == "store_refresh_skipped"]
    assert skipped and skipped[0].get("reason") == "no scrape session"


def _write_corrupt_personas(cfg):
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text('{"personas": [oops]}')


def test_refresh_store_aborts_and_preserves_cache_on_corrupt_personas(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    accrued = json.dumps({"#measured": {"graph_id": "id-measured", METRIC_FIELD: 5000.0,
                                        "measured_at": "2026-07-20T00:00:00+00:00"}}, indent=2)
    cfg.hashtags_path.write_text(accrued)
    _write_corrupt_personas(cfg)
    out = refresh_store(cfg, scrape_client=_FakeClient({"#beta": 900}, cooccur="#beta"))
    assert out["written"] is False and out["aborted"] == "corrupt_personas"
    assert "personas.json invalid:" in out["reason"]
    assert cfg.hashtags_path.read_text() == accrued


def test_refresh_store_absent_personas_is_not_an_abort(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    assert not cfg.personas_path.exists()
    out = refresh_store(cfg, scrape_client=_FakeClient({}))
    assert out["written"] is True and "aborted" not in out and out["measured"] == 0


def test_refresh_store_if_due_corrupt_personas_reports_reason_never_raises(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    accrued = json.dumps({"#measured": {"graph_id": "id-measured", METRIC_FIELD: 1.0,
                                        "measured_at": "2026-07-20T00:00:00+00:00"}}, indent=2)
    cfg.hashtags_path.write_text(accrued)
    _write_corrupt_personas(cfg)
    r = refresh_store_if_due(cfg, max_age_s=10, scrape_client=_FakeClient({"#beta": 900}))
    assert r["refreshed"] is False and r["aborted"] == "corrupt_personas"
    assert "personas.json invalid:" in r["reason"]
    assert cfg.hashtags_path.read_text() == accrued


def test_cmd_hashtags_refresh_corrupt_personas_exits_2_and_no_keyerror(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path)
    _write_corrupt_personas(cfg)
    rc = cmd_hashtags_refresh(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 2
    aborted = next(r for r in recs if r["outcome"] == "refresh_aborted")
    assert "personas.json invalid:" in aborted.get("reason", "")


def test_refresh_store_reports_parallel_one_when_client_injected(tmp_path, monkeypatch):
    """Injected scrape_client (unit fakes) forces parallel=1 so FakeClient stays single-threaded."""
    import fanops.fanops_hashtags as fh
    monkeypatch.setattr(fh, "_SCRAPE_PARALLEL", 8)
    cfg = Config(root=tmp_path); _persona(cfg)
    out = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 10}))
    assert out.get("parallel") == 1 and out["written"] is True
