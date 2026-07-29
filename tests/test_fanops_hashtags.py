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
from fanops.hashtags import METRIC_FIELD, _metric, load_measurements, ranked_tags
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
    # The prior file must be established by a REAL measure: a pass with nothing due writes nothing
    # at all (MOL-695 zero-progress), so an empty fake would leave no file to preserve.
    from fanops import controlio
    cfg = Config(root=tmp_path); _persona(cfg)
    client = _FakeClient({"#hiphop": 10})                   # no media_count -> volume-due next pass
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
    # Layer B also writes personas.json on flush — only the 2nd hashtags.json replace may boom.
    from pathlib import Path
    from fanops import controlio, personas as P
    cfg = Config(root=tmp_path)
    niches = [f"seed{i}" for i in range(6)]                 # 6 anchors → flush at measured=5, then final
    P.add_persona(cfg, name="Mid", voice="x", niche=niches, id="mid")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "mid"}]}))
    metrics = {f"#{n}": float(10 + i) for i, n in enumerate(niches)}
    n_hash = {"n": 0}
    real_replace = controlio.os.replace

    def boom_after_first_hashtags_flush(src, dst):
        if Path(dst).name != "hashtags.json":
            return real_replace(src, dst)                   # personas derive writes must land
        n_hash["n"] += 1
        if n_hash["n"] == 1:
            return real_replace(src, dst)                   # mid-pass flush lands
        raise OSError("crash on later hashtags write")

    monkeypatch.setattr(controlio.os, "replace", boom_after_first_hashtags_flush)
    with pytest.raises(OSError):
        refresh_store(cfg, scrape_client=_FakeClient(metrics))
    monkeypatch.setattr(controlio.os, "replace", real_replace)
    raw = json.loads(cfg.hashtags_path.read_text())
    assert "last_complete_pass" not in raw                  # partial flush must not buy 12h silence
    tags = [k for k in raw if k.startswith("#")]
    assert len(tags) == 5                                   # accrued through the mid-pass flush
    # Layer B already ran on the flush — corpus tracks the store without waiting for pass end
    from fanops.personas import Personas
    corp = list(Personas.load(cfg).get("mid").hashtag_corpus or [])
    assert len(corp) >= 1 and all(t in raw for t in corp)


def test_refresh_store_derives_corpora_on_its_own_writes(tmp_path, monkeypatch):
    """Layer B is on the Layer A write path — refresh alone updates corpora; no separate force."""
    cfg = Config(root=tmp_path); pid = _persona(cfg)
    from fanops.personas import Personas
    assert list(Personas.load(cfg).get(pid).hashtag_corpus or []) == []
    refresh_store(cfg, scrape_client=_FakeClient(
        {"#hiphop": 500, "#alpha": 100}, cooccur="#alpha"))
    corp = list(Personas.load(cfg).get(pid).hashtag_corpus or [])
    assert "#hiphop" in corp and "#alpha" in corp


def test_refresh_store_takes_no_ledger_and_no_doctor_gate(tmp_path, monkeypatch):
    # The own-reach model is gone: refresh_store's signature carries NO `led`, and it writes WITHOUT any
    # learn-doctor verdict on disk (the cache does not depend on a published post).
    assert "led" not in inspect.signature(refresh_store).parameters
    assert "get" not in inspect.signature(refresh_store).parameters
    assert "scrape_client" in inspect.signature(refresh_store).parameters
    cfg = Config(root=tmp_path); _persona(cfg)
    assert not (cfg.control / "learn_doctor.json").exists()     # no doctor verdict anywhere
    out = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 10}))
    assert out["written"] is True and cfg.hashtags_path.exists()  # still writes — no gate
    assert out.get("backend") == "scrape"


def test_written_file_is_the_flat_record_shape_ranked_by_the_metric(tmp_path, monkeypatch):
    # The cache is a flat record map written in the SAME order the selection menu uses (`ranked_tags` —
    # size-first since MOL-692), so a reader that just iterates the file already has the menu.
    # `last_complete_pass` is a sibling stamp, not a tag record (MOL-525).
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, scrape_client=_FakeClient(
        {"#hiphop": 500, "#beta": 900, "#alpha": 100}, cooccur="#alpha #beta",
        media_count_by_tag={"#hiphop": 5_000, "#beta": 900_000, "#alpha": 200}))
    blob = json.loads(cfg.hashtags_path.read_text())
    tags = {k: v for k, v in blob.items() if isinstance(v, dict)}
    assert list(tags) == ranked_tags(tags)                       # menu order on disk
    assert list(tags) == ["#beta", "#hiphop", "#alpha"]           # media_count desc, NOT median desc
    assert blob["#beta"]["graph_id"] == "id-beta" and blob["#beta"]["measured_at"]
    assert blob["#beta"]["from"] == {"#hiphop": 2}  # two Top medias in fake (MOL-665)          # inbound: niche on beta Top (not outbound)
    assert isinstance(blob.get("last_complete_pass"), str) and blob["last_complete_pass"]
    assert "reach" not in json.dumps(blob)                  # no invented metric key survives


def test_measurements_accrue_across_passes(tmp_path, monkeypatch):
    # A later pass that discovers a DIFFERENT slice must ADD to the evidence, not replace it.
    # Age past the corpus 24h due tier so the anchor remesures and harvests the new co-tag (MOL-695).
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#alpha": 100}, cooccur="#alpha"), now=t0)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#beta": 900}, cooccur="#beta"),
                  now=t0 + timedelta(hours=25))
    m = load_measurements(cfg)
    assert "#alpha" in m and "#beta" in m
    assert _metric(m["#alpha"]) == 100 and m["#alpha"]["like_count"] == 100
    assert _metric(m["#beta"]) == 900 and m["#beta"]["like_count"] == 900


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
    assert "play_count" in blob or "like_count" in blob
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
    _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    client = _FakeClient({"#hiphop": 10})
    assert refresh_store_if_due(cfg, scrape_client=client, now=t0)["refreshed"] is True
    assert cfg.hashtags_path.exists()
    assert isinstance(json.loads(cfg.hashtags_path.read_text()).get("last_complete_pass"), str)
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client, now=t0)["refreshed"] is False
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client, now=t0)["reason"] == "fresh"
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (t0 - timedelta(hours=13)).isoformat()
    blob["#hiphop"]["measured_at"] = (t0 - timedelta(hours=25)).isoformat()  # corpus-due remesure
    cfg.hashtags_path.write_text(json.dumps(blob))
    assert refresh_store_if_due(cfg, max_age_s=10, scrape_client=client, now=t0)["refreshed"] is True


def test_throttled_pass_does_not_advance_complete_stamp(tmp_path, monkeypatch):
    """D-2 / MOL-525 (1): throttled writes must not buy (or extend) the 12h silence window.

    MOL-695: Instagram ScrapeThrottled also arms a cooldown — ticks before `until` must not re-open scrape."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    # First: measure successfully so we have a complete stamp + cache to preserve across a later throttle.
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t0)
    stamp = json.loads(cfg.hashtags_path.read_text())["last_complete_pass"]
    t_th = t0 + timedelta(hours=25)                        # corpus-due so the pass actually opens medias_top
    out1 = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t_th)
    assert out1["throttled"] is True
    assert json.loads(cfg.hashtags_path.read_text())["last_complete_pass"] == stamp
    assert _cooldown_path(cfg).exists()
    skip_cool = refresh_store_if_due(cfg, max_age_s=1, scrape_client=_FakeClient({"#hiphop": 50}),
                                     now=t_th + timedelta(minutes=5))
    assert skip_cool["refreshed"] is False and skip_cool["reason"] == "cooldown"
    # After complete stamp is still fresh relative to a long max_age, reason stays cooldown (checked first).
    skip = refresh_store_if_due(cfg, max_age_s=43200, scrape_client=_FakeClient({}),
                                now=t_th + timedelta(minutes=1))
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"


def test_scrape_throttle_cooldown_backoff_and_success_reset(tmp_path, monkeypatch):
    """MOL-695: throttle writes cooldown; ticks before until skip scrape; delays 30m→1h→2h→6h; success clears."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path, _COOLDOWN_DELAYS_S)
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    # Empty cache + immediate throttle → measured=0 no-progress (no hashtags.json write) but cooldown lands.
    out0 = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t0)
    assert out0["throttled"] is True and out0.get("reason") == "no_progress"
    assert out0["written"] is False and not cfg.hashtags_path.exists()
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[0])).isoformat()
    client = _FakeClient({"#hiphop": 50}, throttle_after=0)
    blocked = refresh_store_if_due(cfg, max_age_s=1, scrape_client=client,
                                   now=t0 + timedelta(minutes=29))
    assert blocked["refreshed"] is False and blocked["reason"] == "cooldown" and client.media_calls == []
    # Expire streak-1; throttle again → streak 2 / 1h
    t1 = t0 + timedelta(minutes=31)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t1)
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["streak"] == 2
    assert cd["until"] == (t1 + timedelta(seconds=_COOLDOWN_DELAYS_S[1])).isoformat()
    t2 = t1 + timedelta(hours=1, minutes=1)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t2)
    assert json.loads(_cooldown_path(cfg).read_text())["streak"] == 3
    assert json.loads(_cooldown_path(cfg).read_text())["until"] == (
        t2 + timedelta(seconds=_COOLDOWN_DELAYS_S[2])).isoformat()
    t3 = t2 + timedelta(hours=2, minutes=1)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t3)
    assert json.loads(_cooldown_path(cfg).read_text())["streak"] == 4
    assert json.loads(_cooldown_path(cfg).read_text())["until"] == (
        t3 + timedelta(seconds=_COOLDOWN_DELAYS_S[3])).isoformat()
    t4 = t3 + timedelta(hours=6, minutes=1)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}, throttle_after=0), now=t4)
    assert json.loads(_cooldown_path(cfg).read_text())["streak"] == 5          # still capped at 6h delay
    assert json.loads(_cooldown_path(cfg).read_text())["until"] == (
        t4 + timedelta(seconds=_COOLDOWN_DELAYS_S[3])).isoformat()
    # Success with measured>0 clears the file
    t_ok = t4 + timedelta(hours=6, minutes=1)
    ok = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t_ok)
    assert ok["written"] is True and ok["measured"] >= 1
    assert not _cooldown_path(cfg).exists()


def test_corrupt_cooldown_fails_open(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _cooldown_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _cooldown_path(cfg).write_text("{not-json")
    client = _FakeClient({"#hiphop": 10})
    out = refresh_store_if_due(cfg, max_age_s=1, scrape_client=client, now=t0)
    assert out["refreshed"] is True and client.media_calls  # corrupt → no cooldown gate


def test_zero_progress_pass_preserves_hashtags_bytes_and_skips_rederive(tmp_path, monkeypatch):
    """MOL-695: measured==0 with no tag mutation → byte/mtime-identical hashtags.json; no rederive."""
    import fanops.fanops_hashtags as fh
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t0)
    before = cfg.hashtags_path.read_bytes()
    mtime = cfg.hashtags_path.stat().st_mtime_ns
    stamp = json.loads(before)["last_complete_pass"]
    calls = {"n": 0}
    real = fh._rederive_posting_corpora
    def boom(*a, **k):
        calls["n"] += 1; return real(*a, **k)
    monkeypatch.setattr(fh, "_rederive_posting_corpora", boom)
    # Age past complete gate but refuse the only due work → measured=0, cache unchanged.
    out = refresh_store(cfg, scrape_client=_FakeClient({}, refuse_tags={"#hiphop", "hiphop"}),
                        now=t0 + timedelta(hours=25))
    assert out["measured"] == 0 and out["written"] is False and out.get("reason") == "no_progress"
    assert cfg.hashtags_path.read_bytes() == before
    assert cfg.hashtags_path.stat().st_mtime_ns == mtime
    assert json.loads(cfg.hashtags_path.read_text())["last_complete_pass"] == stamp
    assert calls["n"] == 0


def test_zero_progress_still_writes_when_tag_records_mutate(tmp_path, monkeypatch):
    """measured==0 must NOT skip write when orphan eviction mutates the tag map (prove the predicate).

    The zero-progress skip compares the write projection against the map ON DISK — comparing it against
    another projection would hide eviction, since the projection is what evicts."""
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); _persona(cfg)
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    # Orphan non-anchor with dead `from` — _records_for_write evicts it even with no new measures.
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", "like_count": 10.0, "media_count": 100.0,
                    "media_count_at": now.isoformat(), "measured_at": now.isoformat()},
        "#orphan": {"graph_id": "id-orphan", "like_count": 9.0,
                    "measured_at": now.isoformat(), "from": {"#punchlines": 4}},
        "last_complete_pass": now.isoformat()}))
    before = cfg.hashtags_path.read_text()
    out = refresh_store(cfg, scrape_client=_FakeClient({}, refuse_tags={"#hiphop", "hiphop"}), now=now)
    assert out["measured"] == 0 and out["written"] is True
    assert cfg.hashtags_path.read_text() != before
    assert "#orphan" not in load_measurements(cfg)


def test_refresh_pass_priority_queue_due_tiers(tmp_path, monkeypatch):
    """MOL-695: unmeasured anchor → missing volume → stale corpus → weekly long-tail; fresh irrelevant skipped."""
    from datetime import datetime, timezone, timedelta
    from fanops import personas as P
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="A", voice="x", niche=["newroot"], id="a")
    # Seed corpus membership on the posting persona (already-loaded; derive not required for the queue).
    row = json.loads(cfg.personas_path.read_text())
    for p in row["personas"]:
        if p["id"] == "a":
            p["hashtag_corpus"] = ["#corpustag"]
    cfg.personas_path.write_text(json.dumps(row))
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#missingvol": {"graph_id": "id-missingvol", "like_count": 11.0,
                        "measured_at": (now - timedelta(hours=2)).isoformat(),
                        "from": {"#newroot": 2}},
        "#corpustag": {"graph_id": "id-corpustag", "like_count": 22.0, "media_count": 500.0,
                       "media_count_at": (now - timedelta(hours=2)).isoformat(),
                       "measured_at": (now - timedelta(hours=25)).isoformat(),
                       "from": {"#newroot": 2}},
        "#weeklytail": {"graph_id": "id-weeklytail", "like_count": 33.0, "media_count": 600.0,
                        "media_count_at": (now - timedelta(days=1)).isoformat(),
                        "measured_at": (now - timedelta(days=8)).isoformat(),
                        "from": {"#newroot": 2}},
        "#freshnoise": {"graph_id": "id-freshnoise", "like_count": 44.0, "media_count": 700.0,
                        "media_count_at": (now - timedelta(hours=1)).isoformat(),
                        "measured_at": (now - timedelta(hours=1)).isoformat(),
                        "from": {"#newroot": 2}},
        "last_complete_pass": (now - timedelta(days=2)).isoformat()}))
    metrics = {"#newroot": 1, "#missingvol": 11, "#corpustag": 22, "#weeklytail": 33, "#freshnoise": 44}
    client = _FakeClient(metrics, media_count_by_tag={"#missingvol": 1000, "#newroot": 50,
                                                      "#corpustag": 500, "#weeklytail": 600,
                                                      "#freshnoise": 700})
    out = refresh_store(cfg, scrape_client=client, now=now)
    assert out["written"] is True
    assert "freshnoise" not in client.media_calls, "fresh non-corpus/non-volume tag must stay off the queue"
    assert client.media_calls[:4] == ["newroot", "missingvol", "corpustag", "weeklytail"]


def test_stalest_remeasure_reaches_known_before_fresh_anchor(tmp_path, monkeypatch):
    """Superseded by MOL-695 due tiers: volume-due / weekly beats a freshly-measured anchor.

    Under throttle, a missing-volume known tag must run before remesuring a fresh anchor."""
    from fanops import personas as P
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="A", voice="x", niche=["freshanchor"], id="a")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#freshanchor": {"graph_id": "id-freshanchor", METRIC_FIELD: 10.0, "media_count": 100.0,
                         "media_count_at": (now - timedelta(hours=1)).isoformat(),
                         "measured_at": (now - timedelta(hours=1)).isoformat()},
        "#staletail": {"graph_id": "id-staletail", METRIC_FIELD: 20.0,
                       "measured_at": (now - timedelta(days=8)).isoformat(),
                       "from": {"#freshanchor": 1}},
        "last_complete_pass": (now - timedelta(days=2)).isoformat()}))
    client = _FakeClient({"#freshanchor": 11, "#staletail": 21}, throttle_after=0,
                         media_count_by_tag={"#staletail": 50})
    out = refresh_store(cfg, scrape_client=client, now=now)
    assert out["throttled"] is True
    assert client.media_calls[0] == "staletail", "weekly/volume-due known must beat a fresh measured anchor"


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


def test_refresh_store_refuses_a_second_concurrent_pass(tmp_path, monkeypatch):
    """MOL-686: a pass rewrites the whole cache from its own snapshot, so two in flight discard each
    other's tags. A pass already holding the lease makes the second a clean abort — no network, cache
    byte-identical — and the lease releases so the next pass runs normally."""
    from fanops.fanops_hashtags import _pass_lease
    cfg = Config(root=tmp_path); _persona(cfg)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 100}))
    before = cfg.hashtags_path.read_text()
    client = _FakeClient({"#hiphop": 999})
    with _pass_lease(cfg) as held:
        assert held is True
        out = refresh_store(cfg, scrape_client=client)
    assert out["written"] is False and out["aborted"] == "busy"
    assert client.media_calls == []                          # not one fetch spent on a doomed pass
    assert cfg.hashtags_path.read_text() == before
    assert refresh_store(cfg, scrape_client=client)["written"] is True


def test_scrape_try_cap_default_clears_a_full_cache_remeasure(tmp_path):
    """MOL-686/MOL-695: try_cap stays large enough for niches + co-tag headroom. Queue is due-tiered
    (not every cached tag every pass), but the default cap must still clear a heavy due set."""
    import fanops.fanops_hashtags as fh
    assert fh._SCRAPE_TRY_CAP >= 400
    assert fh._SCRAPE_TRY_CAP >= 300 + fh._SCRAPE_COTAG_ENQUEUE_CAP
    assert fh._SCRAPE_COTAG_ENQUEUE_CAP == 40
    assert fh._VOLUME_MAX_AGE_DAYS == 7
    assert fh._SCRAPE_PARALLEL == 4


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
    # No personas → empty queue → measured=0 no-progress (MOL-695); still not a corrupt abort.
    assert out.get("aborted") != "corrupt_personas"
    assert out["written"] is False and out.get("reason") == "no_progress" and out["measured"] == 0


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
