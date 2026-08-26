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
from datetime import datetime, timezone
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


def _write_sidecar(cfg, names, *, sid="src_1", lock=None):
    """Persist a source_tag_locks.json row. `names` is the pile; lock defaults to names."""
    from fanops.source_tags import source_tag_locks_path
    pile = list(names)
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    table = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text())
            if isinstance(raw, dict):
                table = raw
        except (OSError, ValueError):
            table = {}
    table[sid] = {"pile": pile, "lock": list(lock if lock is not None else pile),
                  "researched_at": "2026-08-17T00:00:00Z"}
    p.write_text(json.dumps(table))


def test_refresh_store_atomic_write_preserves_prior_on_crash(tmp_path, monkeypatch):
    # L08: a crash mid-write must leave the PRIOR valid hashtags.json intact (write_json_atomic).
    # The prior file must be established by a REAL measure: empty-due alone only advances the
    # complete stamp; the atomic-crash proof needs a measured tag record on disk to preserve.
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
    # Only the 2nd hashtags.json replace may boom (the flush itself must land).
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
            return real_replace(src, dst)                   # any sibling control write must land
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
    # MOL-694: the flush is MEASUREMENT crash-safety only — Layer B no longer rides it, so a pass that
    # dies before its end-of-pass derive leaves the corpus alone. The evidence is durable and the
    # fingerprinted safety net (refresh_corpora_if_due) re-derives from it on the next tick.
    from fanops.personas import Personas
    assert list(Personas.load(cfg).get("mid").hashtag_corpus or []) == []


def test_refresh_store_derives_corpora_on_its_own_writes(tmp_path, monkeypatch):
    """Layer B is on the Layer A write path — refresh alone updates corpora; no separate force."""
    cfg = Config(root=tmp_path); pid = _persona(cfg)
    from fanops.personas import Personas
    assert list(Personas.load(cfg).get(pid).hashtag_corpus or []) == []
    refresh_store(cfg, scrape_client=_FakeClient(
        {"#hiphop": 500, "#alpha": 100}, cooccur="#alpha",
        media_count_by_tag={"#hiphop": 4_000_000, "#alpha": 50_000}))
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
    # Age past the 30d remesure floor so the anchor remesures and harvests the new co-tag (MOL-855).
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#alpha": 100}, cooccur="#alpha"), now=t0)
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 500, "#beta": 900}, cooccur="#beta"),
                  now=t0 + timedelta(days=31))
    m = load_measurements(cfg)
    assert "#alpha" in m and "#beta" in m
    assert _metric(m["#alpha"]) == 100 and m["#alpha"]["like_count"] == 100
    assert _metric(m["#beta"]) == 900 and m["#beta"]["like_count"] == 900


def test_refresh_store_no_scrape_aborts_loudly(tmp_path, monkeypatch):
    # Default harvest without injected client refuses instagrapi (Safari-only runtime).
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path); _persona(cfg)
    out = refresh_store(cfg)
    assert out["written"] is False and out["aborted"] == "safari_only"
    assert "Safari" in out["reason"]
    assert not cfg.hashtags_path.exists()


def test_refresh_store_checkpoint_is_its_own_abort(tmp_path, monkeypatch):
    """HT3 auth_death on Challenge* — injected harvest pass, Safari runtime in production."""
    from instagrapi.exceptions import ChallengeRequired
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path); _persona(cfg)

    class _Dead:
        _fanops_scrape_user = "u"
        def hashtag_info(self, _n):
            raise ChallengeRequired("challenge_required")

    out = refresh_store(cfg, scrape_client=_Dead())
    assert out["written"] is False and out["aborted"] == "auth_death"
    assert out["reason"] == "auth_death"
    assert not cfg.hashtags_path.exists()


def test_open_client_unattended_skips_account_info_probe(tmp_path, monkeypatch):
    """Unattended never calls account_info() or login(); search-probes; never dumps."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "secret-must-not-leak")
    cfg = Config(root=tmp_path)
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    original = "{}"
    _sess.write_text(original)
    calls: list[str] = []
    class _Rec:
        def load_settings(self, _p): calls.append("load_settings")
        def search_hashtags(self, _q):
            calls.append("search_hashtags")
            return []
        def account_info(self):
            calls.append("account_info")
            raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k):
            calls.append("login")
            raise AssertionError("default path must not call login()")
        def dump_settings(self, _p): calls.append("dump_settings")
    c = open_client(cfg, client_factory=_Rec)
    assert c is not None
    assert calls == ["load_settings", "search_hashtags"]
    assert _sess.read_text() == original


def test_cmd_hashtags_discover_reports_and_writes_nothing(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); pid = _persona(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"#detroitrap": {
        "graph_id": "id-detroitrap", METRIC_FIELD: 4200.0, "media_count": 50_000.0,
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
    _write_sidecar(cfg, ["#hiphop"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    client = _FakeClient({"#hiphop": 10})
    assert refresh_store_if_due(cfg, scrape_client=client, now=t0)["refreshed"] is True
    assert cfg.hashtags_path.exists()
    assert isinstance(json.loads(cfg.hashtags_path.read_text()).get("last_complete_pass"), str)
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client, now=t0)["refreshed"] is False
    assert refresh_store_if_due(cfg, max_age_s=43200, scrape_client=client, now=t0)["reason"] == "fresh"
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (t0 - timedelta(hours=13)).isoformat()
    blob["#hiphop"]["measured_at"] = (t0 - timedelta(days=31)).isoformat()  # remesure-due (≥30d)
    cfg.hashtags_path.write_text(json.dumps(blob))
    assert refresh_store_if_due(cfg, max_age_s=10, scrape_client=client, now=t0)["refreshed"] is True


def test_throttled_pass_does_not_advance_complete_stamp(tmp_path, monkeypatch):
    """D-2 / MOL-525 (1): throttled writes must not buy (or extend) the 12h silence window.

    MOL-695: Instagram platform throttle also arms a cooldown — ticks before `until` must not re-open scrape."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    # First: measure successfully so we have a complete stamp + cache to preserve across a later throttle.
    refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t0)
    stamp = json.loads(cfg.hashtags_path.read_text())["last_complete_pass"]
    t_th = t0 + timedelta(days=31)                         # remesure-due so the pass actually opens medias_top
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
    assert out0["throttled"] is True and out0.get("aborted") == "RateLimitError"
    assert out0["written"] is False and not cfg.hashtags_path.exists()
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["streak"] == 1 and cd["reason"] == "RateLimitError"
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
    # Streak/until/reason cleared; day/used/accounts may remain (MOL-854 day budget).
    if _cooldown_path(cfg).exists():
        left = json.loads(_cooldown_path(cfg).read_text())
        assert "until" not in left and "streak" not in left and "reason" not in left
    else:
        left = {}


def test_checkpoint_freezes_layer_a_and_stops_reopening_scrape(tmp_path, monkeypatch):
    """HT3: Challenge* arms indefinite auth_death — never the 30m→6h ladder; ticks skip forever
    until scrape-login clears (not a 12h auto-resume)."""
    from datetime import datetime, timezone, timedelta
    import fanops.ig_hashtag_scrape as igs
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path, _AUTH_DEATH_DELAY_S)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path); _persona(cfg)
    _write_sidecar(cfg, ["#hiphop"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    opens = {"n": 0}
    from instagrapi.exceptions import ChallengeRequired
    def locked(_cfg, user=None, **_k):
        opens["n"] += 1
        raise ChallengeRequired("challenge_required")
    def boom_client(*_a, **_k):
        raise AssertionError("tick remesure must open Safari, not open_client")
    monkeypatch.setattr(iws, "open_web_session", locked)
    monkeypatch.setattr(igs, "open_client", boom_client)
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is False and out["aborted"] == "auth_death"
    assert opens["n"] == 1
    cd = json.loads(_cooldown_path(cfg).read_text())
    rec = (cd.get("accounts") or {}).get("u") or cd
    assert rec["reason"] == "auth_death"
    assert rec["until"] == (t0 + timedelta(seconds=_AUTH_DEATH_DELAY_S)).isoformat()
    # Far past the old ladder / 12h checkpoint — still held; scrape-login is the only clear.
    for delta in (timedelta(minutes=1), timedelta(hours=13), timedelta(days=8)):
        skip = refresh_store_if_due(cfg, max_age_s=1, now=t0 + delta)
        assert skip["refreshed"] is False and skip["reason"] == "cooldown"
        assert skip["cooldown_reason"] == "auth_death"
    assert opens["n"] == 1, f"re-opened a locked account {opens['n']} times"
    assert not cfg.hashtags_path.exists()


def test_login_required_arms_the_laddered_cooldown(tmp_path, monkeypatch):
    """HT3: LoginRequired is auth death — indefinite hold, never the 30m→6h ladder."""
    from datetime import datetime, timezone, timedelta
    from instagrapi.exceptions import LoginRequired
    from fanops.fanops_hashtags import (_cooldown_path, _AUTH_DEATH_DELAY_S, _AUTH_DEATH_REASON,
                                       refresh_store_if_due)
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = refresh_store(cfg, scrape_client=_FakeClient({}, refuse=LoginRequired("login_required")), now=t0)
    assert out["aborted"] == _AUTH_DEATH_REASON and out["written"] is False
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == _AUTH_DEATH_REASON and cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_AUTH_DEATH_DELAY_S)).isoformat()
    # Past every ladder rung — still held; must NOT re-hit and deepen like the old ladder.
    t1 = t0 + timedelta(hours=7)
    skip = refresh_store_if_due(cfg, max_age_s=1,
                                scrape_client=_FakeClient({}, refuse=LoginRequired("login_required")),
                                now=t1)
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"
    assert skip["cooldown_reason"] == _AUTH_DEATH_REASON
    cd2 = json.loads(_cooldown_path(cfg).read_text())
    assert cd2["streak"] == 1  # no auto-retry / ladder deepen
    assert not cfg.hashtags_path.exists()


def test_expired_loginrequired_is_stripped_from_cooldown(tmp_path, monkeypatch):
    """HT3: auth-hold reasons never auto-scrub when until passes — scrape-login only."""
    from datetime import datetime, timezone
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import (_cooldown_path, _healthy_scrape_users, scrape_user_blocked)

    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "perca.late")
    now = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
    write_json_atomic(_cooldown_path(cfg), {"accounts": {
        "perca.late": {"until": "2026-08-18T22:45:10+00:00", "reason": "LoginRequired",
                       "streak": 2, "day": "2026-08-18", "used": 0},
    }})
    peers = _healthy_scrape_users(cfg, now, require_budget_room=False, require_session=False)
    assert peers == []
    assert scrape_user_blocked(cfg, "perca.late", now) is True
    rec = json.loads(_cooldown_path(cfg).read_text())["accounts"]["perca.late"]
    assert rec.get("reason") == "LoginRequired" and rec.get("until") == "2026-08-18T22:45:10+00:00"


def test_expired_session_at_fetch_arms_the_login_cooldown(tmp_path, monkeypatch):
    """HT3: dead session at first hashtag_info arms auth_death; ticks never re-hit / ladder."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path,
                                       _AUTH_DEATH_DELAY_S, _AUTH_DEATH_REASON)
    from instagrapi.exceptions import LoginRequired
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path); _persona(cfg)
    _write_sidecar(cfg, ["#hiphop"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    clients: list[_FakeClient] = []
    def make_client():
        c = _FakeClient({}, refuse=LoginRequired("login_required"))
        c._fanops_scrape_user = "u"
        clients.append(c)
        return c
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0, scrape_client=make_client())
    assert out["refreshed"] is False and out["aborted"] == _AUTH_DEATH_REASON
    cd = json.loads(_cooldown_path(cfg).read_text())
    rec = (cd.get("accounts") or {}).get("u") or cd
    assert rec["reason"] == _AUTH_DEATH_REASON and rec["streak"] == 1
    assert rec["until"] == (t0 + timedelta(seconds=_AUTH_DEATH_DELAY_S)).isoformat()
    assert out["cooldown_until"] == rec["until"] and out["cooldown_streak"] == 1
    assert len(clients[0].info_calls) == 1
    for delta in (timedelta(minutes=29), timedelta(hours=7), timedelta(days=8)):
        skip = refresh_store_if_due(cfg, max_age_s=1, now=t0 + delta,
                                    scrape_client=make_client())
        assert skip["refreshed"] is False and skip["reason"] == "cooldown"
        assert skip["cooldown_reason"] == _AUTH_DEATH_REASON
    assert sum(len(c.info_calls) for c in clients) == 1, "must not re-hit after auth_death"
    assert not cfg.hashtags_path.exists()
    cd2 = json.loads(_cooldown_path(cfg).read_text())
    rec2 = (cd2.get("accounts") or {}).get("u") or cd2
    assert rec2["streak"] == 1


def _log_recs(cfg, outcome=None):
    """run.log records, optionally narrowed to one outcome. Every field value is a STRING (log._san)."""
    if not cfg.log_path.exists():
        return []
    recs = [json.loads(ln) for ln in cfg.log_path.read_text().splitlines() if ln.strip()]
    return [r for r in recs if outcome is None or r.get("outcome") == outcome]


def test_outage_level_rises_with_the_streak_and_the_stall():
    """MOL-794: how loud a freeze is must be a FUNCTION of how long it has held, never a constant."""
    from fanops.fanops_hashtags import _outage_level, _COOLDOWN_DELAYS_S, _REFRESH_CADENCE_S
    c = _REFRESH_CADENCE_S
    assert _outage_level(1, 0.0, c) == "info"                       # one skipped tick is routine backoff
    assert _outage_level(1, c - 1, c) == "info"
    assert _outage_level(2, 0.0, c) == "warning"                    # it RE-armed: no longer one bad tick
    assert _outage_level(1, c, c) == "warning"                      # outlived its own refresh cadence
    assert _outage_level(len(_COOLDOWN_DELAYS_S), 0.0, c) == "error"  # ladder capped: retries stopped decaying
    assert _outage_level(1, 2 * c, c) == "error"                    # two whole cadences with no complete pass
    assert _outage_level(20, 132.7 * 3600, c) == "error"            # the live 2026-08-07 shape
    assert _outage_level(None, None, c) == "info"                   # nothing known -> no false alarm


def test_sustained_cooldown_skip_escalates_instead_of_fading(tmp_path):
    """MOL-794 — the inversion. Arming logged `level:"error"`, but the DAILY CONSEQUENCE of the same
    outage (`store_refresh_skipped reason=cooldown`) logged at info, so a five-day dead scrape session
    got quieter the longer it lasted. A skip under a freeze that has outlived the cadence now says so at
    its own severity, keyed only on state already on disk: the streak, and the age of the last pass."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 8, 7, 10, 23, tzinfo=timezone.utc)
    _cooldown_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _cooldown_path(cfg).write_text(json.dumps({                     # the live blob, verbatim in shape
        "streak": 20, "until": (t0 + timedelta(hours=4)).isoformat(),
        "updated_at": (t0 - timedelta(hours=2)).isoformat(), "reason": "login_required"}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "last_complete_pass": (t0 - timedelta(hours=132.7)).isoformat()}))
    client = _FakeClient({"#hiphop": 50})
    out = refresh_store_if_due(cfg, scrape_client=client, now=t0)
    assert out["refreshed"] is False and out["reason"] == "cooldown" and out["level"] == "error"
    assert client.media_calls == []                                 # still no request against the account
    rec = _log_recs(cfg, "scrape_outage")
    assert len(rec) == 1 and rec[0]["level"] == "error"
    assert rec[0]["reason"] == "login_required" and rec[0]["streak"] == "20"
    assert float(rec[0]["stalled_h"]) == pytest.approx(132.7, abs=0.1)
    assert "scrape-login" in rec[0]["detail"]                       # the stored remedy, not a new one


def test_a_first_cooldown_skip_stays_quiet(tmp_path):
    """The negative control for MOL-794: escalation that fires on the FIRST skip is noise with a new
    name. A 30m backoff behind a pass that completed an hour ago is routine and must log nothing."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 8, 7, 10, 23, tzinfo=timezone.utc)
    _cooldown_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _cooldown_path(cfg).write_text(json.dumps({
        "streak": 1, "until": (t0 + timedelta(minutes=20)).isoformat(),
        "updated_at": (t0 - timedelta(minutes=10)).isoformat(), "reason": "throttle"}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "last_complete_pass": (t0 - timedelta(hours=1)).isoformat()}))
    out = refresh_store_if_due(cfg, scrape_client=_FakeClient({"#hiphop": 50}), now=t0)
    assert out["refreshed"] is False and out["reason"] == "cooldown" and out["level"] == "info"
    assert _log_recs(cfg, "scrape_outage") == []


def test_rearming_during_a_long_stall_logs_louder_than_info(tmp_path):
    """The other half of the inversion: the PASS-END arm sites logged at the default `info` while the
    open-client arm sites hard-coded `error` — one event class, two severities. The first arm behind a
    fresh pass stays `info`; the same arm behind a five-day stall is an error."""
    from datetime import datetime, timezone, timedelta
    from instagrapi.exceptions import LoginRequired
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 8, 7, 10, 23, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", METRIC_FIELD: 50.0,
                    "measured_at": (t0 - timedelta(hours=1)).isoformat()},
        "last_complete_pass": (t0 - timedelta(hours=1)).isoformat()}))
    refresh_store(cfg, scrape_client=_FakeClient({}, refuse=LoginRequired("login_required")), now=t0)
    arms = _log_recs(cfg, "scrape_cooldown")
    assert arms and arms[-1]["streak"] == "1" and arms[-1]["level"] == "info"   # routine first arm
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (t0 - timedelta(days=5)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    refresh_store(cfg, scrape_client=_FakeClient({}, refuse=LoginRequired("login_required")),
                  now=t0 + timedelta(hours=1))
    arms = _log_recs(cfg, "scrape_cooldown")
    assert arms[-1]["streak"] == "2" and arms[-1]["level"] == "error"           # same event, now an outage


def test_partial_progress_then_throttle_still_arms_a_cooldown(tmp_path, monkeypatch):
    """MOL-854 (was MOL-727): never `_clear_cooldown` when `ig_throttled` — clear-then-rearm reset the
    streak to 1 (cooldown sawtooth) and wiped day-budget keys. Partial progress still writes evidence;
    the stop signal arms AND the prior streak keeps climbing."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path, _persist_cooldown,
                                        _COOLDOWN_DELAYS_S)
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _persist_cooldown(cfg, t0 - timedelta(hours=3), reason="throttle")
    _persist_cooldown(cfg, t0 - timedelta(hours=2), reason="throttle")      # prior streak = 2
    # #hiphop measures, harvests #alpha, and the co-tag's medias_top throttles: measured=1 + throttle.
    out = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 50, "#alpha": 100}, cooccur="#alpha",
                                                       throttle_after=1), now=t0)
    assert out["measured"] == 1 and out["throttled"] is True and out["written"] is True
    raw = json.loads(cfg.hashtags_path.read_text())
    assert "#hiphop" in raw                                # partial evidence is still durable
    assert "last_complete_pass" not in raw                 # an early stop never buys the 12h silence
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == "RateLimitError"
    assert cd["streak"] == 3                               # prior 2 NOT cleared — bump to 3 (no sawtooth)
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[2])).isoformat()   # 2h rung
    assert cd["day"] == "2026-07-01" and cd["used"] >= 1 and isinstance(cd.get("accounts"), dict)
    assert out["cooldown_until"] == cd["until"]
    nxt = _FakeClient({"#hiphop": 50})
    skip = refresh_store_if_due(cfg, max_age_s=1, scrape_client=nxt, now=t0 + timedelta(minutes=29))
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"
    assert nxt.info_calls == [] and nxt.media_calls == []   # not one request against a throttling account


def test_partial_progress_then_login_required_still_arms_a_cooldown(tmp_path, monkeypatch):
    """HT3: partial progress then LoginRequired arms auth_death (not the ladder) and leaves incomplete."""
    from datetime import datetime, timezone, timedelta
    from instagrapi.exceptions import LoginRequired
    from fanops.fanops_hashtags import _cooldown_path, _AUTH_DEATH_DELAY_S, _AUTH_DEATH_REASON

    class _DeadAfterFirst(_FakeClient):
        """First tag lands; the session dies on the next medias_top."""
        def hashtag_medias_top(self, name, amount=9):
            if self.media_calls:
                raise LoginRequired("login_required")
            return super().hashtag_medias_top(name, amount=amount)

    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = refresh_store(cfg, scrape_client=_DeadAfterFirst({"#hiphop": 50, "#alpha": 100},
                                                           cooccur="#alpha"), now=t0)
    assert out["measured"] == 1 and out["written"] is True
    assert out["throttled"] is True
    raw = json.loads(cfg.hashtags_path.read_text())
    assert "#hiphop" in raw and "last_complete_pass" not in raw
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == _AUTH_DEATH_REASON and cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_AUTH_DEATH_DELAY_S)).isoformat()
    assert cd["day"] == "2026-07-01" and cd["used"] >= 1



def test_scrape_login_loops_comma_users(tmp_path, monkeypatch):
    """MOL-857: scrape-login walks every FANOPS_IG_SCRAPE_USER with allow_reauth=True."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import cmd_hashtags_scrape_login
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    seen = []
    def fake_open(_cfg, *, allow_reauth=False, user=None, **_k):
        assert allow_reauth is True
        seen.append(user)
        return object()
    monkeypatch.setattr(igs, "open_client", fake_open)
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: ("sid", "1"))
    assert cmd_hashtags_scrape_login(cfg) == 0
    assert seen == ["a", "b"]


def test_scrape_login_ignores_and_clears_an_active_freeze(tmp_path, monkeypatch):
    """The operator escape hatch: scrape-login is an explicit human act AFTER an unlock, so it must run
    with a freeze armed and clear it on success — otherwise a fixed account stays frozen for 12h."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import (cmd_hashtags_scrape_login, _cooldown_path, _persist_cooldown,
                                        _CHECKPOINT_DELAY_S)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    _persist_cooldown(cfg, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                      reason="checkpoint", delay_s=_CHECKPOINT_DELAY_S)
    assert _cooldown_path(cfg).exists()
    monkeypatch.setattr(igs, "open_client", lambda _c, **_k: object())
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: ("sid", "1"))
    assert cmd_hashtags_scrape_login(cfg) == 0              # NOT blocked by the freeze
    # Streak/until/reason cleared; day/used/accounts may remain (MOL-854 day budget).
    if _cooldown_path(cfg).exists():
        left = json.loads(_cooldown_path(cfg).read_text())
        assert "until" not in left and "streak" not in left and "reason" not in left
    else:
        left = {}


def test_instagrapi_floor_validates_saved_sessions(tmp_path):
    """Gate removal is only safe while instagrapi validates restored sessions (2.18.12+)."""
    import importlib.metadata as md
    ver = tuple(int(p) for p in md.version("instagrapi").split(".")[:3])
    assert ver >= (2, 18, 12)


def test_open_client_stale_session_opens_without_probe_or_login(tmp_path, monkeypatch):
    """Unattended dead dump + no profile sid → ScrapeUnavailable; on-disk dump unchanged."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import LoginRequired as _LR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "dead-dump"}'
    sess.write_text(original)
    seen = {"login": 0, "account_info": 0, "dump": 0}

    class _Stale:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise _LR("login_required")
        def account_info(self): seen["account_info"] += 1
        def login(self, *_a, **_k): seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            from pathlib import Path
            Path(p).write_text('{"overwritten": true}')
    try:
        open_client(cfg, client_factory=_Stale)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "scrape session" in str(e)
    assert seen == {"login": 0, "account_info": 0, "dump": 0}
    assert sess.read_text() == original


def test_open_client_default_path_never_reads_password(tmp_path, monkeypatch):
    """Default path must not touch scrape password — even with a session file on disk."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    _sess.write_text("{}")
    class _Ok:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q): return []
        def account_info(self): raise AssertionError("unattended must not probe")
        def login(self, *_a, **_k): raise AssertionError("login must not run")
        def dump_settings(self, _p): pass
    def _boom(_user):
        raise AssertionError("default path must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    open_client(cfg, client_factory=_Ok)


def test_open_client_allow_reauth_promotes_when_envelope_live(tmp_path, monkeypatch):
    """scrape-login with live envelope probes + promotes; never password-login."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text('{"keep": true}')
    seen = {"login": 0, "dump": 0}

    class _Stale:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, _p):
            seen["dump"] += 1
    open_client(cfg, client_factory=_Stale, allow_reauth=True, user="u")
    assert seen == {"login": 0, "dump": 1}


def test_open_client_allow_reauth_promotes_envelope_without_login(tmp_path, monkeypatch):
    """scrape-login: envelope probe + promote; never password-login; no cookie inject."""
    from pathlib import Path
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = {"search": 0, "login": 0, "dump": 0}

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"promoted": true}')
    c = open_client(cfg, client_factory=_Live, allow_reauth=True, user="u")
    assert c is not None
    assert seen["search"] == 1
    assert seen["login"] == 0
    assert seen["dump"] == 1



def test_open_client_unattended_envelope_probe_no_dump(tmp_path, monkeypatch):
    """Unattended envelope → one search probe, no login, no dump."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "dead"}'
    sess.write_text(original)
    seen = {"search": 0, "login": 0, "dump": 0}

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            from pathlib import Path
            Path(p).write_text('{"healed": true}')
    c = open_client(cfg, client_factory=_Live, user="u")
    assert c is not None
    assert seen == {"search": 1, "login": 0, "dump": 0}
    assert sess.read_text() == original



def test_open_client_unattended_loginrequired_without_chrome_leaves_dump(tmp_path, monkeypatch):
    """Unattended + no profile sid → no dump write, raises, dump file unchanged."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import ClientLoginRequired as _CLR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": true}'
    sess.write_text(original)
    seen = {"dump": 0, "login": 0, "account_info": 0}

    class _Dead:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise _CLR("login_required")
        def account_info(self):
            seen["account_info"] += 1
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            from pathlib import Path
            Path(p).write_text('{"overwritten": true}')
    try:
        open_client(cfg, client_factory=_Dead)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "scrape session" in str(e)
    assert seen == {"dump": 0, "login": 0, "account_info": 0}
    assert sess.read_text() == original


def test_open_client_unattended_live_dump_search_probes_then_dumps(tmp_path, monkeypatch):
    """Unattended live envelope + profile sid → search probe once, no dump, no login."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = {"search": 0, "login": 0, "account_info": 0, "dump": 0}

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def account_info(self):
            seen["account_info"] += 1
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, _p):
            seen["dump"] += 1
    c = open_client(cfg, client_factory=_Live)
    assert c is not None
    assert seen == {"search": 1, "login": 0, "account_info": 0, "dump": 0}


def test_open_client_probe_throttle_does_not_login(tmp_path, monkeypatch):
    """PleaseWait / rate-limit on a live dump must not become a password login."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")

    class PleaseWaitFewMinutes(Exception):
        pass

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise PleaseWaitFewMinutes("wait")
        def login(self, *_a, **_k):
            raise AssertionError("throttle must not login")
        def dump_settings(self, _p):
            raise AssertionError("throttle must not dump")
    try:
        open_client(cfg, client_factory=_Live, allow_reauth=True, user="u")
    except PleaseWaitFewMinutes:
        return
    raise AssertionError("throttle must propagate")


def test_open_client_unattended_throttle_does_not_overwrite_dump(tmp_path, monkeypatch):
    """Unattended PleaseWait / rate-limit must not login, dump, or read the password."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "live"}'
    sess.write_text(original)

    class PleaseWaitFewMinutes(Exception):
        pass

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise PleaseWaitFewMinutes("wait")
        def account_info(self):
            raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k):
            raise AssertionError("throttle must not login")
        def dump_settings(self, p):
            from pathlib import Path
            Path(p).write_text('{"overwritten": true}')
            raise AssertionError("throttle must not dump")
    try:
        open_client(cfg, client_factory=_Live)
    except PleaseWaitFewMinutes:
        assert sess.read_text() == original
        return
    raise AssertionError("throttle must propagate")


def test_open_client_failed_login_does_not_dump(tmp_path, monkeypatch):
    """A rejected profile probe must not overwrite the on-disk envelope or password-login."""
    from instagrapi.exceptions import LoginRequired
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text('{"keep": true}')
    seen = {"login": 0}

    class _Fail:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise LoginRequired("login_required")
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, _p):
            raise AssertionError("must not dump after failed profile probe")
    try:
        open_client(cfg, client_factory=_Fail, allow_reauth=True, user="u")
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable:
        pass
    assert seen["login"] == 0
    assert '"keep": true' in sess.read_text()


def test_open_client_valid_session_skips_login_on_both_paths(tmp_path, monkeypatch):
    """Session on disk → search probe then return; login() and account_info() never called."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    _sess.write_text("{}")
    for allow in (False, True):
        seen = {"login": 0, "account_info": 0, "search": 0}
        class _Ok:
            def load_settings(self, _p): pass
            def search_hashtags(self, _q):
                seen["search"] += 1
                return []
            def account_info(self):
                seen["account_info"] += 1
                return {"pk": 1}
            def login(self, *_a, **_k): seen["login"] += 1
            def dump_settings(self, _p): pass
        open_client(cfg, client_factory=_Ok, allow_reauth=allow)
        assert seen["login"] == 0, allow
        assert seen["account_info"] == 0, allow
        assert seen["search"] == 1, allow


def test_open_client_missing_session_refuses_on_default_path(tmp_path, monkeypatch):
    """Cold start is operator-only — unattended tick must not password-login with no session file."""
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    from fanops.ig_hashtag_scrape import scrape_session_path as _ssp
    assert not _ssp(cfg, "u").exists()
    seen = {"login": 0}
    class _Cold:
        def load_settings(self, _p): pass
        def account_info(self): raise AssertionError("no session to probe")
        def login(self, *_a, **_k): seen["login"] += 1
        def dump_settings(self, _p): pass
    try:
        open_client(cfg, client_factory=_Cold)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "no scrape session" in str(e)
    assert seen["login"] == 0


def test_open_client_callers_keep_reauth_default(tmp_path):
    """HT3 doctor offline + HT4 Safari-only runtime; scrape-login alone uses allow_reauth."""
    import inspect
    import fanops.doctor as doctor
    import fanops.fanops_hashtags as fh
    src_doc = inspect.getsource(doctor._hashtag_scrape_check)
    src_ref = inspect.getsource(fh._refresh_pass)
    src_login = inspect.getsource(fh.cmd_hashtags_scrape_login)
    assert "resolve_hashtag_scrape" not in src_doc
    assert "from fanops.ig_hashtag_scrape import open_client" not in src_doc
    assert "open_client" not in src_ref
    assert "open_web_session" in src_ref
    assert "allow_reauth=True" not in src_ref
    assert "allow_reauth=True" in src_login



def test_layer_a_fetch_does_not_call_graph_hashtag_apis():
    """Layer A `_fetch` stays on instagrapi scrape — never ig_hashtag_search / Graph top_media."""
    import inspect
    import fanops.fanops_hashtags as fh
    src = inspect.getsource(fh._refresh_pass)
    assert "ig_hashtag_search" not in src
    assert "meta_graph" not in src
    assert "resolve_hashtag_scrape" in src
    assert "measure_and_harvest_scrape" in src


def test_clear_cooldown_keeps_per_account_updated_at(tmp_path):
    """Per-user clear must not wipe accounts[user].updated_at that _persist_cooldown wrote."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import (_clear_cooldown, _cooldown_path, _persist_cooldown)
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    _persist_cooldown(cfg, t0, reason="checkpoint", delay_s=12 * 3600, user="mark", used_delta=1)
    frozen = json.loads(_cooldown_path(cfg).read_text())["accounts"]["mark"]
    assert frozen.get("updated_at") == t0.isoformat()
    t1 = t0 + timedelta(minutes=5)
    _clear_cooldown(cfg, now=t1, used_delta=2, user="mark")
    rec = json.loads(_cooldown_path(cfg).read_text())["accounts"]["mark"]
    assert "until" not in rec and "streak" not in rec and "reason" not in rec
    assert rec.get("updated_at") == t1.isoformat()
    assert rec.get("used") == 3
    # clear without now preserves prior account updated_at
    _clear_cooldown(cfg, user="mark")
    rec2 = json.loads(_cooldown_path(cfg).read_text())["accounts"]["mark"]
    assert rec2.get("updated_at") == t1.isoformat()


def test_healthy_scrape_users_lru_oldest_updated_at_first(tmp_path, monkeypatch):
    """LRU policy: missing updated_at first; then oldest; open_client(user=None) uses that head."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import (_clear_cooldown, _healthy_scrape_users, _persist_cooldown)
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b,c")
    t0 = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    for u in ("a", "b", "c"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    _persist_cooldown(cfg, t0, reason="throttle", user="a", used_delta=1)
    _clear_cooldown(cfg, now=t0, used_delta=0, user="a")
    _persist_cooldown(cfg, t0, reason="throttle", user="b", used_delta=1)
    _clear_cooldown(cfg, now=t0.replace(minute=30), used_delta=0, user="b")
    assert _healthy_scrape_users(cfg, t0) == ["c", "a", "b"]
    class _C:
        def __init__(self):
            self.delay_range = None
        def load_settings(self, _p): pass
        def dump_settings(self, _p): pass
    assert open_client(cfg, client_factory=_C, now=t0)._fanops_scrape_user == "c"


def test_healthy_scrape_users_default_skips_loginrequired_freeze(tmp_path, monkeypatch):
    """Layer A remesure must still skip a LoginRequired freeze (lock walk does not retry it)."""
    from datetime import datetime, timezone
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import _cooldown_path, _healthy_scrape_users
    from fanops.ig_hashtag_scrape import scrape_session_path
    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,cisum")
    t0 = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    for u in ("mark", "cisum"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {
            "mark": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                     "reason": "LoginRequired"},
            "cisum": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                      "reason": "throttle"},
        }})
    assert _healthy_scrape_users(cfg, t0) == []
    assert _healthy_scrape_users(cfg, t0, require_budget_room=True) == []
    assert _healthy_scrape_users(cfg, t0, require_budget_room=False) == []


def test_corrupt_cooldown_fails_open(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    cfg = Config(root=tmp_path); _persona(cfg)
    _write_sidecar(cfg, ["#hiphop"])
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
    assert out["measured"] == 0 and out["written"] is False and out.get("reason") == "zero measured"
    assert cfg.hashtags_path.read_bytes() == before
    assert cfg.hashtags_path.stat().st_mtime_ns == mtime
    assert json.loads(cfg.hashtags_path.read_text())["last_complete_pass"] == stamp
    assert calls["n"] == 0


def test_empty_due_queue_with_sessions_advances_complete_stamp(tmp_path, monkeypatch):
    """Empty due queue must not fake no_scrape when session peers exist; advance last_complete_pass."""
    from datetime import datetime, timezone, timedelta
    from fanops.ig_hashtag_scrape import scrape_session_path
    cfg = Config(root=tmp_path); _persona(cfg)
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "markmakmouly,cisumwolfhom,perca.late")
    for u in ("markmakmouly", "cisumwolfhom", "perca.late"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    measured_at = (now - timedelta(days=1)).isoformat()
    old_stamp = (now - timedelta(hours=13)).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "last_complete_pass": old_stamp,
        "#hiphop": {"graph_id": "1", "play_count": 100.0, "like_count": 10.0,
                    "media_count": 50_000.0, "media_count_at": measured_at,
                    "measured_at": measured_at},
    }))
    out = refresh_store(cfg, scrape_client=_FakeClient({}), now=now)
    blob = json.loads(cfg.hashtags_path.read_text())
    assert out.get("written") is True and out.get("tried") == 0
    assert out.get("aborted") is None
    assert blob["last_complete_pass"] == now.isoformat()


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


def _count_rederives(monkeypatch):
    """Count `_rederive_posting_corpora` calls while still running the real derive."""
    import fanops.fanops_hashtags as fh
    calls = {"n": 0}
    real = fh._rederive_posting_corpora
    def counted(*a, **k):
        calls["n"] += 1; return real(*a, **k)
    monkeypatch.setattr(fh, "_rederive_posting_corpora", counted)
    return calls


def _many_anchor_persona(cfg, n, *, pid="many"):
    """A posting persona with `n` distinct niche terms → n unmeasured anchors → n measures in one pass.
    Every co-tag a fake caption carries is itself an anchor, so nothing is discovered off-queue."""
    from fanops import personas as P
    niches = [f"seed{i:02d}" for i in range(n)]
    P.add_persona(cfg, name="Many", voice="x", niche=niches, id=pid)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    return {f"#{n_}": float(10 + i) for i, n_ in enumerate(niches)}


def test_rederive_runs_once_per_pass_not_once_per_midpass_flush(tmp_path, monkeypatch):
    """MOL-694: a 20-measure pass flushes 4 times for crash safety but derives corpora exactly ONCE.

    The flush is measurement durability; Layer B is a pure recompute of the WHOLE store, so running it
    per flush re-derived every posting persona ~N/5 times per pass for one usable result."""
    import fanops.fanops_hashtags as fh
    cfg = Config(root=tmp_path)
    metrics = _many_anchor_persona(cfg, 20)
    writes = {"n": 0}
    real_write = fh.write_json_atomic
    def counted_write(path, *a, **k):
        if getattr(path, "name", "") == "hashtags.json":
            writes["n"] += 1
        return real_write(path, *a, **k)
    monkeypatch.setattr(fh, "write_json_atomic", counted_write)
    calls = _count_rederives(monkeypatch)
    out = refresh_store(cfg, scrape_client=_FakeClient(metrics))
    assert out["written"] is True and out["measured"] == 20
    assert writes["n"] == 5                                  # KEEP: flushes at 5/10/15/20 + the final write
    assert calls["n"] == 1                                   # one derive round, at pass end
    from fanops.personas import Personas
    assert list(Personas.load(cfg).get("many").hashtag_corpus or [])   # and it actually landed


def test_throttled_pass_with_progress_rederives_once_at_stop(tmp_path, monkeypatch):
    """An early stop is still a pass END: measured>0 → exactly one derive round, never zero, never per-flush."""
    cfg = Config(root=tmp_path)
    metrics = _many_anchor_persona(cfg, 20)
    calls = _count_rederives(monkeypatch)
    out = refresh_store(cfg, scrape_client=_FakeClient(metrics, throttle_after=6))
    assert out["throttled"] is True and out["measured"] == 6  # crossed one mid-pass flush, then stopped
    assert out["written"] is True
    assert calls["n"] == 1


def test_try_cap_stop_with_progress_rederives_once(tmp_path, monkeypatch):
    """try_cap incompleteness ends the pass too — same single derive round as a clean finish."""
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "7")
    cfg = Config(root=tmp_path)
    metrics = _many_anchor_persona(cfg, 20)
    calls = _count_rederives(monkeypatch)
    out = refresh_store(cfg, scrape_client=_FakeClient(metrics))
    assert out["throttled"] is True and out["tried"] == 7 and out["measured"] == 7
    assert calls["n"] == 1


def test_eviction_only_write_does_not_rederive(tmp_path, monkeypatch):
    """MOL-694: rederive is keyed on measured>0, so a measured==0 pass that writes only because an orphan
    was evicted does NOT derive. Nothing is lost: the write moved hashtags.json, so the fingerprinted
    safety net (`persona_research.refresh_corpora_if_due`) sees changed inputs on the next tick."""
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); _persona(cfg)
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", "like_count": 10.0, "media_count": 100.0,
                    "media_count_at": now.isoformat(), "measured_at": now.isoformat()},
        "#orphan": {"graph_id": "id-orphan", "like_count": 9.0,
                    "measured_at": now.isoformat(), "from": {"#punchlines": 4}},
        "last_complete_pass": now.isoformat()}))
    calls = _count_rederives(monkeypatch)
    out = refresh_store(cfg, scrape_client=_FakeClient({}, refuse_tags={"#hiphop", "hiphop"}), now=now)
    assert out["measured"] == 0 and out["written"] is True
    assert "#orphan" not in load_measurements(cfg)
    assert calls["n"] == 0


def test_refresh_pass_priority_queue_due_tiers(tmp_path, monkeypatch):
    """MOL-855: unmeasured anchor → missing volume → ≥30d remesure (oldest first); <30d skipped."""
    from datetime import datetime, timezone, timedelta
    from fanops import personas as P
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="A", voice="x", niche=["newroot"], id="a")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#missingvol": {"graph_id": "id-missingvol", "like_count": 11.0,
                        "measured_at": (now - timedelta(hours=2)).isoformat(),
                        "from": {"#newroot": 2}},
        "#oldermeasure": {"graph_id": "id-oldermeasure", "like_count": 22.0, "media_count": 500.0,
                          "media_count_at": (now - timedelta(days=2)).isoformat(),
                          "measured_at": (now - timedelta(days=40)).isoformat(),
                          "from": {"#newroot": 2}},
        "#newermeasure": {"graph_id": "id-newermeasure", "like_count": 33.0, "media_count": 600.0,
                          "media_count_at": (now - timedelta(days=2)).isoformat(),
                          "measured_at": (now - timedelta(days=31)).isoformat(),
                          "from": {"#newroot": 2}},
        "#freshnoise": {"graph_id": "id-freshnoise", "like_count": 44.0, "media_count": 700.0,
                        "media_count_at": (now - timedelta(hours=1)).isoformat(),
                        "measured_at": (now - timedelta(days=8)).isoformat(),
                        "from": {"#newroot": 2}},
        "last_complete_pass": (now - timedelta(days=2)).isoformat()}))
    metrics = {"#newroot": 1, "#missingvol": 11, "#oldermeasure": 22, "#newermeasure": 33, "#freshnoise": 44}
    client = _FakeClient(metrics, media_count_by_tag={"#missingvol": 1000, "#newroot": 50,
                                                      "#oldermeasure": 500, "#newermeasure": 600,
                                                      "#freshnoise": 700})
    out = refresh_store(cfg, scrape_client=client, now=now)
    assert out["written"] is True
    assert "freshnoise" not in client.media_calls, "sub-30d measured tag must stay off the remesure queue"
    assert client.media_calls[:4] == ["newroot", "missingvol", "oldermeasure", "newermeasure"]
    # MOL-856: every due visit spends hashtag_info too (no medias_top-only remesure).
    for tag in ("newroot", "missingvol", "oldermeasure", "newermeasure"):
        assert tag in client.info_calls
    assert "freshnoise" not in client.info_calls


def test_due_remesure_always_calls_hashtag_info_and_medias_top(tmp_path, monkeypatch):
    """MOL-856: remesure with a still-fresh media_count_at still spends hashtag_info + medias_top."""
    from datetime import datetime, timezone, timedelta
    from fanops import personas as P
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="A", voice="x", niche=["hiphop"], id="a")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "a"}]}))
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", "like_count": 10.0, "media_count": 100.0,
                    "media_count_at": (now - timedelta(days=2)).isoformat(),
                    "measured_at": (now - timedelta(days=40)).isoformat()},
        "last_complete_pass": (now - timedelta(days=2)).isoformat()}))
    client = _FakeClient({"#hiphop": 55}, media_count_by_tag={"#hiphop": 999})
    out = refresh_store(cfg, scrape_client=client, now=now)
    assert out["written"] is True and out["measured"] == 1
    assert "hiphop" in client.info_calls, "due remesure must call hashtag_info"
    assert "hiphop" in client.media_calls, "due remesure must call medias_top"
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["media_count"] == 999.0 and rec["like_count"] == 55.0


def test_stalest_remeasure_reaches_known_before_fresh_anchor(tmp_path, monkeypatch):
    """MOL-855 due tiers: missing-volume known beats a freshly-measured anchor.

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
    assert client.media_calls[0] == "staletail", "volume-due known must beat a fresh measured anchor"


def test_refresh_store_try_cap_ends_pass_without_complete_stamp(tmp_path, monkeypatch):
    """Scrape pass budget: stop after _SCRAPE_TRY_CAP tries, write evidence, do NOT stamp complete."""
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "2")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", "0")   # no co-tag expansion in this proof
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
    """MOL-854 + MOL-855: per-pass try_cap is a small ceiling; UTC day budget is the local governor.
    Due-tiered queue means the small cap need not clear every cached tag each pass."""
    import fanops.fanops_hashtags as fh
    from fanops.settings import Settings
    assert fh._SCRAPE_TRY_CAP == 25
    assert fh._SCRAPE_DAY_BUDGET == 40
    assert Settings.model_fields["FANOPS_HASHTAG_SCRAPE_TRY_CAP"].default == 25
    assert fh._SCRAPE_COTAG_ENQUEUE_CAP == 40
    c = Config(root=tmp_path)
    assert c.hashtag_scrape_try_cap == 25
    assert c.hashtag_scrape_cotag_enqueue == 40
    assert fh._VOLUME_MAX_AGE_DAYS == 30
    assert fh._MEASURE_MAX_AGE_DAYS == 30
    assert not hasattr(fh, "_CORPUS_MAX_AGE_HOURS")



def test_used_counter_does_not_skip_refresh(tmp_path, monkeypatch):
    """HT3: day-budget exhausted → refresh skips as cooldown/budget (honest block, not telemetry)."""
    from datetime import datetime, timezone, timedelta
    import fanops.fanops_hashtags as fh
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path
    from fanops.controlio import write_json_atomic
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "last_complete_pass": (t0 - timedelta(hours=13)).isoformat(),
        "#hiphop": {"graph_id": "1", "play_count": 10.0,
                    "measured_at": (t0 - timedelta(hours=13)).isoformat()},
    }))
    cfg.control.mkdir(parents=True, exist_ok=True)
    write_json_atomic(_cooldown_path(cfg),
                      {"day": "2026-07-01", "used": fh._SCRAPE_DAY_BUDGET, "accounts": {}})
    nxt = _FakeClient({"#hiphop": 50})
    out = refresh_store_if_due(cfg, max_age_s=1, scrape_client=nxt, now=t0)
    assert out.get("reason") == "cooldown"
    assert out.get("cooldown_reason") == "budget"
    assert nxt.info_calls == [] and nxt.media_calls == []



def test_refresh_store_cotag_enqueue_cap(tmp_path, monkeypatch):
    """One anchor can harvest dozens of co-tags; only _SCRAPE_COTAG_ENQUEUE_CAP are measured this pass."""
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "50")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", "2")
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
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "2")            # hiphop + cotag only; remesure must not steal
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", "5")
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
    # No personas → early discovery_skip_no_niche (honest); still not a corrupt abort.
    assert out.get("aborted") == "discovery_skip_no_niche"
    assert out.get("written") is False and out.get("reason") == "no personas have a declared niche"


def test_refresh_store_if_due_corrupt_personas_does_not_abort(tmp_path, monkeypatch):
    """HV1-PR4: the tick remesures sidecar names and does not load personas.json."""
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#beta"])
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    accrued = json.dumps({"#measured": {"graph_id": "id-measured", METRIC_FIELD: 1.0,
                                        "measured_at": "2026-07-20T00:00:00+00:00"}}, indent=2)
    cfg.hashtags_path.write_text(accrued)
    _write_corrupt_personas(cfg)
    r = refresh_store_if_due(cfg, max_age_s=10, scrape_client=_FakeClient({"#beta": 900}))
    assert r.get("aborted") != "corrupt_personas"
    assert r["refreshed"] is True
    assert "#beta" in load_measurements(cfg)
    assert "#measured" in load_measurements(cfg)


def test_cmd_hashtags_refresh_no_sidecar_exits_2(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    rc = cmd_hashtags_refresh(cfg)
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert rc == 2
    aborted = next(r for r in recs if r["outcome"] == "refresh_aborted")
    assert aborted.get("aborted") == "no_sidecar"


def test_layer_a_emits_one_client_no_session_clones(tmp_path, monkeypatch):
    """MOL-698/MOL-855: no session clones; _refresh_pass is sequential (no ThreadPool / Lock / waves)."""
    import fanops.fanops_hashtags as fh
    import fanops.ig_hashtag_scrape as igs
    assert not hasattr(igs, "session_client"), "session-clone fan-out must stay deleted"
    src = inspect.getsource(fh._refresh_pass)
    assert "session_client" not in src
    assert "ThreadPoolExecutor" not in src
    assert "threading" not in src
    assert "\"parallel\"" not in inspect.getsource(fh._refresh_pass)  # summary key gone (MOL-912)
    monkeypatch.delenv("FANOPS_HASHTAG_SCRAPE_PARALLEL", raising=False)
    assert fh._scrape_parallel() == 1


def test_scrape_delay_range_paces_requests(monkeypatch):
    """MOL-698: requests fired back-to-back at ~2.4 req/s from one account. instagrapi's own
    delay_range is the pacing knob; it was never set. Default (1,3)s jitter, env-overridable,
    fail-safe to the default on garbage (a bad env var must never break Layer A)."""
    from fanops.ig_hashtag_scrape import _scrape_delay_range
    monkeypatch.delenv("FANOPS_HASHTAG_SCRAPE_DELAY", raising=False)
    assert _scrape_delay_range() == [1.0, 3.0]
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "2,5")
    assert _scrape_delay_range() == [2.0, 5.0]
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    assert _scrape_delay_range() is None                    # explicit opt-out (fakes / tests)
    for junk in ("abc", "", "5,2", "-1,3", "1,2,3"):        # inverted / negative / arity are junk too
        monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", junk)
        assert _scrape_delay_range() == [1.0, 3.0], junk


def test_open_client_sets_delay_range_on_the_client(tmp_path, monkeypatch):
    """The pacing must land ON the client instagrapi actually uses — set before any network call."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    monkeypatch.delenv("FANOPS_HASHTAG_SCRAPE_DELAY", raising=False)
    cfg = Config(root=tmp_path)
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    _sess.write_text("{}")
    seen = {}
    class _Fake:
        def load_settings(self, _p): pass
        def account_info(self): seen["delay_at_probe"] = self.delay_range
        def login(self, *_a, **_k): raise AssertionError("valid session must not login")
        def dump_settings(self, _p): pass
    # Unattended: delay is set before any call; this fake has no search so no probe runs.
    c = open_client(cfg, client_factory=_Fake)
    assert c.delay_range == [1.0, 3.0]
    assert "delay_at_probe" not in seen
    # Operator path: probe still sees delay_range already set (MOL-698).
    open_client(cfg, client_factory=_Fake, allow_reauth=True)
    assert seen["delay_at_probe"] == [1.0, 3.0]


def test_refresh_store_early_aborts_on_login_required(tmp_path, monkeypatch):
    """MOL-696: LoginRequired must stop the pass — not burn try_cap spinning refusals."""
    from instagrapi.exceptions import LoginRequired
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "40")
    cfg = Config(root=tmp_path); _persona(cfg)
    client = _FakeClient({}, refuse=LoginRequired("login_required"))
    out = refresh_store(cfg, scrape_client=client)
    assert out.get("aborted") == "auth_death"
    assert out.get("reason") == "auth_death"
    assert out["written"] is False and out["measured"] == 0
    assert out["tried"] == 1, f"must abort after first refusal, tried={out['tried']}"
    assert not cfg.hashtags_path.exists()
    assert len(client.info_calls) == 1


def test_per_account_freeze_rotates_via_open_client(tmp_path, monkeypatch):
    """MOL-858: open_client skips a frozen peer and opens the next session-bearing account."""
    from datetime import datetime, timezone
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    from fanops.fanops_hashtags import _persist_cooldown, _read_active_cooldown
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "dead,live")
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for u in ("dead", "live"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    _persist_cooldown(cfg, t0, reason="login_required", user="dead")
    seen = []
    class _Ok:
        def load_settings(self, p): seen.append(p)
        def account_info(self): pass
        def login(self, *_a, **_k): raise AssertionError("must not login")
        def dump_settings(self, _p): pass
    c = open_client(cfg, client_factory=_Ok, now=t0)
    assert str(scrape_session_path(cfg, "live")) in seen[0]
    assert getattr(c, "_fanops_scrape_user", None) == "live"
    assert _read_active_cooldown(cfg, t0) is None   # live peer keeps the tick open


def test_open_client_uses_budget_exhausted_unfrozen_session(tmp_path, monkeypatch):
    """Day budget is not 'no client'. A live session with used>=cap still opens for lock production."""
    from datetime import datetime, timezone
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    from fanops.fanops_hashtags import _SCRAPE_DAY_BUDGET, _cooldown_path
    from fanops.controlio import write_json_atomic
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "dead,spent")
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for u in ("dead", "spent"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {
            "dead": {"until": "2026-07-01T18:00:00+00:00", "streak": 4,
                     "reason": "LoginRequired", "day": "2026-07-01", "used": 3},
            "spent": {"day": "2026-07-01", "used": _SCRAPE_DAY_BUDGET,
                      "updated_at": "2026-07-01T03:49:33+00:00"},
        }})
    seen = []
    class _Ok:
        def load_settings(self, p): seen.append(p)
        def account_info(self): pass
        def login(self, *_a, **_k): raise AssertionError("must not login")
        def dump_settings(self, _p): pass
    c = open_client(cfg, client_factory=_Ok, now=t0)
    assert getattr(c, "_fanops_scrape_user", None) == "spent"
    assert str(scrape_session_path(cfg, "spent")) in seen[0]


def test_read_active_cooldown_used_peer_is_healthy(tmp_path, monkeypatch):
    """HT3: frozen peer + day-budget-exhausted peer → global cooldown (budget), not open."""
    from datetime import datetime, timezone
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.fanops_hashtags import (_SCRAPE_DAY_BUDGET, _cooldown_path, _read_active_cooldown)
    from fanops.controlio import write_json_atomic
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "dead,spent")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for u in ("dead", "spent"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {
            "dead": {"until": "2026-07-01T18:00:00+00:00", "streak": 4,
                     "reason": "LoginRequired", "day": "2026-07-01", "used": 3},
            "spent": {"day": "2026-07-01", "used": _SCRAPE_DAY_BUDGET,
                      "updated_at": "2026-07-01T03:49:33+00:00"},
        }})
    cool = _read_active_cooldown(cfg, t0)
    assert cool is not None and cool.get("reason") == "budget"


def test_refresh_store_opens_when_used_is_high(tmp_path, monkeypatch):
    """HT3: day budget exhausted → remesure skips (cooldown/budget), does not open Safari."""
    import fanops.ig_web_scrape as iws
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.fanops_hashtags import (_SCRAPE_DAY_BUDGET, _cooldown_path, _remesure_sidecar)
    from fanops.controlio import write_json_atomic
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "spent")
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    sess = scrape_session_path(cfg, "spent")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {"spent": {"day": "2026-07-01", "used": _SCRAPE_DAY_BUDGET}}})
    seen = []
    def fake(*_a, **_k):
        seen.append(1)
        raise AssertionError("budget exhausted must not open Safari")
    monkeypatch.setattr(iws, "open_web_session", fake)
    out = _remesure_sidecar(cfg, names=["#alpha"], now=t0)
    assert seen == []
    assert out.get("aborted") == "cooldown"
    assert out.get("reason") == "budget"


def test_all_peers_frozen_skips_refresh(tmp_path, monkeypatch):
    """MOL-858: skip with cooldown only when every scrape peer is frozen/budgeted."""
    from datetime import datetime, timezone, timedelta
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path)
    from fanops.controlio import write_json_atomic
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "last_complete_pass": (t0 - timedelta(hours=13)).isoformat()}))
    for u in ("a", "b"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {
            "a": {"until": (t0 + timedelta(hours=1)).isoformat(), "streak": 1,
                  "reason": "throttle", "day": "2026-07-01", "used": 1},
            "b": {"until": (t0 + timedelta(hours=1)).isoformat(), "streak": 1,
                  "reason": "LoginRequired", "day": "2026-07-01", "used": 0},
        }})
    nxt = _FakeClient({"#hiphop": 50})
    skip = refresh_store_if_due(cfg, max_age_s=1, scrape_client=nxt, now=t0)
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"
    assert skip.get("cooldown_reason") in ("throttle", "budget")
    assert nxt.info_calls == [] and nxt.media_calls == []


def test_scrape_login_clears_only_that_user_freeze(tmp_path, monkeypatch):
    """MOL-858: scrape-login success clears THAT user's freeze; peer freeze remains."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import (cmd_hashtags_scrape_login, _cooldown_path, _persist_cooldown)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _persist_cooldown(cfg, t0, reason="checkpoint", delay_s=12 * 3600, user="a")
    _persist_cooldown(cfg, t0, reason="throttle", user="b")
    def fake_open(_cfg, *, allow_reauth=False, user=None, **_k):
        assert allow_reauth is True
        return object()
    def fake_wait(_cfg, user, **_k):
        return ("sid", "1") if user == "a" else None
    monkeypatch.setattr(igs, "open_client", fake_open)
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", fake_wait)
    assert cmd_hashtags_scrape_login(cfg) == 0
    blob = json.loads(_cooldown_path(cfg).read_text())
    assert "until" not in blob.get("accounts", {}).get("a", {})
    assert "until" in blob["accounts"]["b"] and blob["accounts"]["b"]["reason"] == "throttle"


def test_per_account_throttle_persists_under_accounts_user(tmp_path, monkeypatch):
    """MOL-858: in-pass throttle arms accounts[user] (not a global top-level until) when user is known."""
    from datetime import datetime, timezone, timedelta
    from fanops.ig_hashtag_scrape import scrape_session_path
    from instagrapi.exceptions import RateLimitError
    from fanops.fanops_hashtags import refresh_store, _cooldown_path, _COOLDOWN_DELAYS_S
    from hashtag_scrape_fakes import _Media
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u1")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    sess = scrape_session_path(cfg, "u1")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    # Prior streak=2 with expired until so u1 is still pickable (not actively frozen).
    from fanops.controlio import write_json_atomic
    write_json_atomic(_cooldown_path(cfg), {"accounts": {"u1": {
        "streak": 2, "until": (t0 - timedelta(hours=1)).isoformat(),
        "reason": "throttle", "day": "2026-07-01", "used": 0}}})
    class _Partial:
        def __init__(self):
            self.media_calls = []
        def load_settings(self, _p): pass
        def account_info(self): pass
        def login(self, *_a, **_k): raise AssertionError("no login")
        def dump_settings(self, _p): pass
        def hashtag_info(self, name):
            class _Info: id = f"id-{name}"; media_count = 10
            return _Info()
        def hashtag_medias_top(self, name, amount=9):
            self.media_calls.append(name)
            if len(self.media_calls) == 1:
                return [_Media(play_count=50, caption_text="#alpha")]
            raise RateLimitError(**{"message": "", "error_type": "rate_limit_error", "status": "fail"})
    c = _Partial()
    c._fanops_scrape_user = "u1"
    out = refresh_store(cfg, scrape_client=c, now=t0)
    assert out["measured"] == 1 and out["throttled"] is True and out["written"] is True
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert "until" not in cd                                  # no global top-level freeze
    rec = cd["accounts"]["u1"]
    assert rec["reason"] == "RateLimitError" and rec["streak"] == 3  # no sawtooth clear
    assert rec["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[2])).isoformat()
    assert rec["day"] == "2026-07-01" and rec["used"] >= 1


def test_refresh_pass_two_ready_users_both_charged(tmp_path, monkeypatch):
    """MOL-900: remesure walk opens ≥2 Safari peers on the same sidecar queue."""
    import fanops.ig_web_scrape as iws
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.fanops_hashtags import _remesure_sidecar
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u1,u2")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "2")
    cfg = Config(root=tmp_path)
    niches = [f"seed{i}" for i in range(6)]
    names = [f"#{n}" for n in niches]
    for u in ("u1", "u2"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    opened: list[str] = []
    metrics = {f"#{n}": float(10 + i) for i, n in enumerate(niches)}

    def fake_open(cfg, user=None, **_k):
        assert user in ("u1", "u2")
        opened.append(user)
        c = _FakeClient(metrics)
        c._fanops_scrape_user = user
        return c

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = _remesure_sidecar(cfg, names=names, now=t0)
    assert out.get("written") is True and out["measured"] >= 2
    assert opened == ["u1", "u2"]
    # Remesure (harvest=False) charges wire spend only on platform stop — no day-budget blob yet.


def test_refresh_pass_head_throttle_peer_continues(tmp_path, monkeypatch):
    """MOL-900: head in-loop throttle freezes head; peer continues same queue cursor."""
    import fanops.ig_web_scrape as iws
    from fanops.ig_hashtag_scrape import scrape_session_path
    from instagrapi.exceptions import RateLimitError
    from fanops.fanops_hashtags import _remesure_sidecar, _cooldown_path
    from hashtag_scrape_fakes import _Media
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u1,u2")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "10")
    cfg = Config(root=tmp_path)
    niches = [f"seed{i}" for i in range(4)]
    names = [f"#{n}" for n in niches]
    for u in ("u1", "u2"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")

    class _HeadThenPeer:
        def __init__(self, user):
            self.user = user
            self.n = 0
        def hashtag_info(self, name):
            class _Info: id = f"id-{name}"; media_count = 10
            return _Info()
        def hashtag_medias_top(self, name, amount=9):
            self.n += 1
            if self.user == "u1":
                raise RateLimitError(**{"message": "", "error_type": "rate_limit_error", "status": "fail"})
            return [_Media(play_count=50, caption_text="#x"), _Media(play_count=50, caption_text="#x")]

    def fake_open(cfg, user=None, **_k):
        c = _HeadThenPeer(user)
        c._fanops_scrape_user = user
        return c

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = _remesure_sidecar(cfg, names=names, now=t0)
    assert out["measured"] >= 1
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["accounts"]["u1"]["reason"] == "RateLimitError"
    assert "until" in cd["accounts"]["u1"]
    # Peer u2 continues the queue; remesure does not increment accounts[user].used without a stop.


def test_refresh_pass_injected_client_no_roster_walk(tmp_path, monkeypatch):
    """MOL-900: scrape_client= stays single-client; does not open FANOPS_IG_SCRAPE_USER peers."""
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u1,u2")
    cfg = Config(root=tmp_path); _persona(cfg)
    opens = []

    import fanops.ig_web_scrape as iws
    def boom_open(*_a, **_k):
        opens.append(1)
        raise AssertionError("network opener must not run when scrape_client injected")

    monkeypatch.setattr(igs, "open_client", boom_open)
    monkeypatch.setattr(iws, "open_web_session", boom_open)
    out = refresh_store(cfg, scrape_client=_FakeClient({"#hiphop": 10}))
    assert out["written"] is True and opens == []

def test_platform_error_from_open_client_in_multi_account_walk_arms_cooldown(tmp_path, monkeypatch):
    """MOL-913 escape path: bare Exception from open_client in the multi-account walk arms cooldown
    via `_freeze_for` (B7). Without except Exception, platform errors skip freeze entirely."""
    from datetime import datetime, timezone, timedelta
    import fanops.ig_web_scrape as iws
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.fanops_hashtags import _remesure_sidecar, _cooldown_path, _AUTH_DEATH_DELAY_S
    from instagrapi.exceptions import ChallengeRequired
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u1,u2")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for u in ("u1", "u2"):
        sess = scrape_session_path(cfg, u)
        sess.parent.mkdir(parents=True, exist_ok=True)
        sess.write_text("{}")
    opens = []

    def fake_open(cfg, user=None, **_k):
        opens.append(user)
        if user == "u1":
            raise ChallengeRequired("challenge_required")
        c = _FakeClient({"#hiphop": 50})
        c._fanops_scrape_user = user
        return c

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    out = _remesure_sidecar(cfg, names=["#hiphop"], now=t0)
    assert "u1" in opens and "u2" in opens, "walk must continue to peer after platform stop"
    assert out.get("written") is True and out.get("measured", 0) >= 1
    cd = json.loads(_cooldown_path(cfg).read_text())
    rec = cd["accounts"]["u1"]
    assert rec["reason"] == "auth_death"
    assert rec["until"] == (t0 + timedelta(seconds=_AUTH_DEATH_DELAY_S)).isoformat()


def test_run_once_does_not_expand_vocab():
    """HV1-PR4: the run loop (_cmd_run_pass, historically _run_once) must not restock vocab."""
    import inspect
    from fanops.cli import _cmd_run_pass
    src = inspect.getsource(_cmd_run_pass)
    assert "expand_vocab_if_due" not in src
    assert "hashtag_vocab" not in src
    assert "refresh_store_if_due" in src


def test_refresh_store_if_due_empty_sidecar_is_clean_noop(tmp_path, monkeypatch):
    """Empty sidecar → refreshed False, not discovery_skip_no_niche, no scrape."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    client = _FakeClient({"#hiphop": 10, "#alpha": 9})
    out = refresh_store_if_due(cfg, scrape_client=client, now=t0)
    assert out["refreshed"] is False
    assert out.get("aborted") != "discovery_skip_no_niche"
    assert client.media_calls == [] and client.info_calls == []
    assert not cfg.hashtags_path.exists()


def test_refresh_store_if_due_queue_is_sidecar_not_persona_terms(tmp_path, monkeypatch):
    """Tick visits pile∪lock union only — persona niche is not the queue."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path); _persona(cfg)
    _write_sidecar(cfg, ["#alpha"], sid="s1", lock=["#beta"])
    _write_sidecar(cfg, ["#beta", "#gamma"], sid="s2")
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    client = _FakeClient({"#hiphop": 99, "#alpha": 10, "#beta": 11, "#gamma": 12})
    out = refresh_store_if_due(cfg, scrape_client=client, now=t0)
    assert out["refreshed"] is True
    assert set(client.media_calls) == {"alpha", "beta", "gamma"}
    assert "hiphop" not in client.media_calls
    m = load_measurements(cfg)
    assert "#alpha" in m and "#beta" in m and "#gamma" in m
    assert "#hiphop" not in m


def test_refresh_store_if_due_does_not_harvest_cotags_or_call_layer_a(tmp_path, monkeypatch):
    """Cotag / persona discovery must not run from the tick."""
    from datetime import datetime, timezone
    import fanops.fanops_hashtags as fh
    import fanops.persona_research as pr
    from fanops.fanops_hashtags import refresh_store_if_due
    cfg = Config(root=tmp_path); _persona(cfg)
    _write_sidecar(cfg, ["#alpha"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def boom(*_a, **_k):
        raise AssertionError("tick must not call Layer A discovery")

    monkeypatch.setattr(fh, "refresh_store", boom)
    monkeypatch.setattr(pr, "persona_terms", boom)
    client = _FakeClient({"#alpha": 10, "#freshco": 50}, cooccur="#freshco")
    out = refresh_store_if_due(cfg, scrape_client=client, now=t0)
    assert out["refreshed"] is True
    assert out.get("discovered", 0) == 0
    assert client.media_calls == ["alpha"]
    m = load_measurements(cfg)
    assert "#alpha" in m and "#freshco" not in m


def test_refresh_store_if_due_caps_30_unique_names_per_7_days(tmp_path, monkeypatch):
    """Exact-name quota: at most 30 unique sidecar names remesured in 7 days."""
    from datetime import datetime, timezone, timedelta
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "40")  # pass cap is 25; quota is 30
    cfg = Config(root=tmp_path)
    names = [f"#t{i:02d}" for i in range(35)]
    _write_sidecar(cfg, names)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    metrics = {n: float(10 + i) for i, n in enumerate(names)}
    client = _FakeClient(metrics)
    out = refresh_store_if_due(cfg, scrape_client=client, now=t0)
    assert out["refreshed"] is True
    assert len(client.media_calls) == 30
    assert len(load_measurements(cfg)) == 30
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (t0 - timedelta(hours=13)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    nxt = _FakeClient(metrics)
    skip = refresh_store_if_due(cfg, max_age_s=10, scrape_client=nxt, now=t0 + timedelta(hours=13))
    assert skip["refreshed"] is False and skip["reason"] == "quota"
    assert nxt.media_calls == []
    later = _FakeClient(metrics)
    aged = refresh_store_if_due(cfg, max_age_s=10, scrape_client=later,
                                now=t0 + timedelta(days=8))
    assert aged["refreshed"] is True
    assert len(later.media_calls) == 30


def test_caption_request_has_no_content_tags_key():
    """HV1-PR4 / PR3: request_captions and regen do not import or emit content_tags."""
    import inspect
    from fanops.caption import request_captions
    from fanops.studio.actions import regenerate_caption
    req = inspect.getsource(request_captions)
    assert "content_tag_candidates" not in req
    assert "content_tags" not in req
    regen = inspect.getsource(regenerate_caption)
    assert "content_tag_candidates" not in regen
    assert '"content_tags"' not in regen


def _web_fetch_for(tag: str, *, hid=None, media_count=50_000, like=10, play=100):
    """Safari XHR stub: GET tags/info + POST tags/sections for one remesure visit."""
    name = tag.lstrip("#")
    hid = hid or f"id-{name}"

    def fetch(method, url, body=None):
        q = name
        if "/tags/" in url:
            q = url.split("/tags/", 1)[1].split("/", 1)[0] or name
        if "/info/" in url:
            return {"name": q, "id": hid if q == name else f"id-{q}",
                    "media_count": media_count, "status": "ok"}
        return {"sections": [{"layout_content": {"medias": [{"media": {
            "pk": "1", "like_count": like, "play_count": play,
            "product_type": "clips", "taken_at": 1_783_000_000,
            "caption": {"text": f"#{q}"},
        }}]}}]}
    return fetch


def _boom_chrome_tick(monkeypatch):
    """Tick remesure must not touch instagrapi / Chrome dumps / Chrome launch."""
    import fanops.ig_hashtag_scrape as igs

    def boom(*_a, **_k):
        raise AssertionError("tick remesure must not use open_client / Chrome dumps")
    monkeypatch.setattr(igs, "open_client", boom)
    monkeypatch.setattr(igs, "launch_scrape_chrome", boom)
    monkeypatch.setattr(igs, "ensure_scrape_chrome", boom)
    monkeypatch.setattr(igs, "ensure_scrape_safari", boom)



def test_ht4_cmd_hashtags_refresh_uses_safari_remesure(tmp_path, monkeypatch):
    """Manual refresh must open Safari web session, never open_client / cookie inject."""
    import fanops.ig_hashtag_scrape as igs
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    from fanops.hashtags import load_measurements
    from fanops.ig_web_scrape import IgWebSession
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    monkeypatch.setattr(igs, "open_client", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("cmd_hashtags_refresh must not call open_client")))
    opened = []

    def fake_open(_cfg, user=None, **_k):
        opened.append(user)
        return IgWebSession(user or "u", fetch=_web_fetch_for("alpha"))

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    assert cmd_hashtags_refresh(cfg) == 0
    assert opened == ["u"]
    assert "#alpha" in load_measurements(cfg)


def test_ht4_refresh_store_harvest_without_client_refuses_instagrapi(tmp_path, monkeypatch):
    """Default refresh_store harvest path refuses silent instagrapi (Safari-only runtime)."""
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    _persona(cfg)
    out = refresh_store(cfg)
    assert out["aborted"] == "safari_only"
    assert "Safari" in out["reason"]


def test_tick_remesure_safari_no_envelope_not_no_scrape(tmp_path, monkeypatch):
    """HV1-LAYERA: sidecar + Safari stub, no chrome dumps, no envelope → remesure writes."""
    from datetime import datetime, timezone
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.hashtags import RECORD_NUM_FIELDS, RECORD_STR_FIELDS, load_measurements
    from fanops.ig_web_scrape import IgWebSession
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")  # must not steer the opener
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _boom_chrome_tick(monkeypatch)
    opened = []

    def fake_open(_cfg, user=None, **_k):
        opened.append(user)
        return IgWebSession(user or "u", fetch=_web_fetch_for("alpha"))

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out.get("aborted") != "no_scrape"
    assert out["refreshed"] is True
    assert opened == ["u"]
    rec = load_measurements(cfg)["#alpha"]
    assert rec["graph_id"] == "id-alpha" and rec["measured_at"]
    assert rec["media_count"] == 50_000.0
    assert rec["play_count"] == 100.0 and rec["like_count"] == 10.0
    extra = set(rec) - {"graph_id", "measured_at", "from"}
    assert extra <= set(RECORD_NUM_FIELDS) | set(RECORD_STR_FIELDS)


def test_tick_remesure_dumps_and_envelope_still_use_safari(tmp_path, monkeypatch):
    """Chrome dumps + session json present must not open_client or launch Chrome on the tick."""
    from datetime import datetime, timezone
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.ig_hashtag_scrape import scrape_chrome_profile_dir, scrape_session_path
    from fanops.ig_web_scrape import IgWebSession
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    chrome = scrape_chrome_profile_dir(cfg, "u")
    chrome.mkdir(parents=True, exist_ok=True)
    (chrome / "Cookies").write_text("not-a-real-dump")
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _boom_chrome_tick(monkeypatch)
    monkeypatch.setattr(iws, "open_web_session",
                        lambda _c, user=None, **_k: IgWebSession(user or "u",
                                                                 fetch=_web_fetch_for("alpha")))
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is True and out.get("aborted") != "no_scrape"


def test_refresh_store_if_due_password_does_not_count_as_configured(tmp_path, monkeypatch):
    """Password / dumps without FANOPS_IG_SCRAPE_USER is not configured."""
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.ig_hashtag_scrape import scrape_chrome_profile_dir, scrape_session_path
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    scrape_chrome_profile_dir(cfg, "u").mkdir(parents=True, exist_ok=True)
    out = refresh_store_if_due(cfg, max_age_s=1)
    assert out["refreshed"] is False and out["reason"] == "no scrape session"
    assert out.get("aborted") != "no_scrape"


def test_tick_remesure_used_does_not_block_lock_walk(tmp_path, monkeypatch):
    """HT3: used≥day budget blocks remesure cooldown and the Safari lock picker."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import fanops.ig_web_scrape as iws
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import (_SCRAPE_DAY_BUDGET, _cooldown_path, refresh_store_if_due,
                                       scrape_user_blocked)
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.source_tags import _iter_lock_clients
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {"u": {"day": "2026-07-01", "used": _SCRAPE_DAY_BUDGET}}})
    assert scrape_user_blocked(cfg, "u", t0) is True
    opens = []

    def fake_open(_cfg, user=None, **_k):
        opens.append(("web", user))
        raise iws.ScrapeUnavailable("no scrape profile session")

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    _boom_chrome_tick(monkeypatch)
    skip = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert skip.get("reason") == "cooldown"
    assert skip.get("cooldown_reason") == "budget"
    assert opens == []
    lock_seen = []

    def lock_opener(_cfg, user=None, **_k):
        lock_seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    opened = list(_iter_lock_clients(cfg, client=None, open_client_fn=lock_opener, now=t0))
    assert lock_seen == []
    assert opened == []


def test_lock_then_remesure_still_runs(tmp_path, monkeypatch):
    """Injected lock does not spend used; remesure via fetch still remesures."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.hashtags import load_measurements
    from fanops.ig_web_scrape import IgWebSession
    from fanops.source_tags import ensure_source_lock, load_source_tag_locks
    from hashtag_scrape_fakes import _Media
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    t0 = datetime.now(timezone.utc)

    class _LockCli:
        _fanops_scrape_user = "u"

        def search_hashtags(self, query):
            return [SimpleNamespace(name="alpha", id="1", media_count=2)]

        def hashtag_medias_top(self, name, amount=9):
            return [_Media(1, "", play_count=8)]

    ensure_source_lock(cfg, SimpleNamespace(id="src_1", title="t", language="en", transcript="x"),
                       client=_LockCli(), research_fn=lambda *_a: ["alpha"],
                       resolve_fn=lambda *_a: "gid-alpha", measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"] and rec["lock"] == ["#alpha"]
    _boom_chrome_tick(monkeypatch)
    monkeypatch.setattr(iws, "open_web_session",
                        lambda _c, user=None, **_k: IgWebSession(user or "u",
                                                                 fetch=_web_fetch_for("alpha")))
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is True
    assert "#alpha" in load_measurements(cfg)


def test_tick_remesure_source_has_no_dump_login_or_chrome():
    """Tick path source must not dump_settings, login(), or name Google Chrome."""
    import inspect
    import fanops.fanops_hashtags as fh
    src = inspect.getsource(fh._refresh_pass) + inspect.getsource(fh.refresh_store_if_due)
    assert "open_web_session" in src
    assert "dump_settings" not in src
    assert "login(" not in src
    assert "Google Chrome" not in src


def test_tick_remesure_igwebsession_fetch_writes_measurement_fields(tmp_path, monkeypatch):
    """Remesure _fetch on IgWebSession persists the load_measurements contract."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.hashtags import RECORD_NUM_FIELDS, RECORD_STR_FIELDS, load_measurements
    from fanops.ig_web_scrape import IgWebSession
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    sess = IgWebSession("u", fetch=_web_fetch_for("alpha", hid="1784", media_count=12_345))
    out = refresh_store_if_due(cfg, max_age_s=1, scrape_client=sess, now=t0)
    assert out["refreshed"] is True
    rec = load_measurements(cfg)["#alpha"]
    assert rec["graph_id"] == "1784"
    assert rec["media_count"] == 12_345.0
    assert rec.get("media_count_at")
    assert rec["play_count"] == 100.0
    extra = set(rec) - {"graph_id", "measured_at", "from"}
    assert extra <= set(RECORD_NUM_FIELDS) | set(RECORD_STR_FIELDS)


def test_tick_remesure_opens_web_session_per_listed_user(tmp_path, monkeypatch):
    """#1029 profile map: tick remesure calls open_web_session(cfg, user=u)."""
    from datetime import datetime, timezone
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import refresh_store_if_due
    from fanops.ig_web_scrape import IgWebSession
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "markmakmouly,cisumwolfhom")
    cfg = Config(root=tmp_path)
    _write_sidecar(cfg, ["#alpha"])
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    seen = []
    _boom_chrome_tick(monkeypatch)

    def fake_open(_cfg, user=None, **_k):
        seen.append(user)
        return IgWebSession(user or "markmakmouly", fetch=_web_fetch_for("alpha"))

    monkeypatch.setattr(iws, "open_web_session", fake_open)
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is True
    assert seen == ["markmakmouly"]



def test_ht3_auth_death_never_ladder(tmp_path, monkeypatch):
    """HT3 acceptance: LoginRequired / ChallengeRequired → auth_death, not 30m ladder."""
    from datetime import datetime, timezone, timedelta
    from instagrapi.exceptions import ChallengeRequired, LoginRequired
    from fanops.fanops_hashtags import (_AUTH_DEATH_DELAY_S, _AUTH_DEATH_REASON, _COOLDOWN_DELAYS_S,
                                       _freeze_for, _is_frozen, _persist_cooldown, scrape_user_blocked)
    reason, delay = _freeze_for(LoginRequired("login_required"))
    assert reason == _AUTH_DEATH_REASON and delay == _AUTH_DEATH_DELAY_S
    assert delay > _COOLDOWN_DELAYS_S[-1]
    reason2, delay2 = _freeze_for(ChallengeRequired("challenge_required"))
    assert reason2 == _AUTH_DEATH_REASON and delay2 == _AUTH_DEATH_DELAY_S
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    rec = _persist_cooldown(cfg, t0, reason=reason, delay_s=delay, user="u")
    assert rec["reason"] == _AUTH_DEATH_REASON
    # Past every ladder rung — still blocked; clock does not clear auth death.
    later = t0 + timedelta(hours=7)
    assert _is_frozen(rec, later) is True
    assert scrape_user_blocked(cfg, "u", later) is True


def test_ht3_scrape_user_blocked_day_budget(tmp_path, monkeypatch):
    """HT3 acceptance: day-budget exhausted accounts are blocked (not freeze-only)."""
    from datetime import datetime, timezone
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import (_SCRAPE_DAY_BUDGET, _cooldown_path, _day_room,
                                       _healthy_scrape_users, scrape_user_blocked)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "spent")
    cfg = Config(root=tmp_path)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {"spent": {"day": "2026-07-01", "used": _SCRAPE_DAY_BUDGET}}})
    assert _day_room(cfg, "spent", t0) == 0
    assert scrape_user_blocked(cfg, "spent", t0) is True
    assert _healthy_scrape_users(cfg, t0, require_budget_room=True, require_session=False) == []
    assert _healthy_scrape_users(cfg, t0, require_budget_room=False, require_session=False) == ["spent"]
