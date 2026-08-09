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


def test_refresh_store_checkpoint_is_its_own_abort(tmp_path, monkeypatch):
    """A locked account must abort as `checkpoint`, not `no_scrape` — the remedies differ (in-app
    verification vs re-running scrape-login), and the cache stays untouched either way."""
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path); _persona(cfg)
    def locked(_cfg, **_k):
        raise igs.ScrapeCheckpoint("account checkpointed by Instagram — verify the login in the "
                                   "official Instagram app or web, then re-run scrape-login")
    monkeypatch.setattr(igs, "open_client", locked)
    out = refresh_store(cfg)
    assert out["written"] is False and out["aborted"] == "checkpoint"
    assert "Instagram app" in out["reason"]
    assert not cfg.hashtags_path.exists()


def test_scrape_checkpoint_classification(tmp_path, monkeypatch):
    """`_is_checkpoint` must fire on challenge/checkpoint cues and NOT on a plain expired session,
    and ScrapeCheckpoint must stay catchable as ScrapeUnavailable (every existing abort path)."""
    from fanops.ig_hashtag_scrape import (ScrapeCheckpoint, ScrapeUnavailable, _is_checkpoint,
                                          open_client)
    assert issubclass(ScrapeCheckpoint, ScrapeUnavailable)
    class ChallengeRequired(Exception): pass
    assert _is_checkpoint(ChallengeRequired("Manual verification required via native challenge flow"))
    assert _is_checkpoint(Exception("checkpoint_required")) is True
    assert _is_checkpoint(Exception("login_required")) is False   # expiry: scrape-login DOES fix it
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "secret-must-not-leak")
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")                 # probe path, not login()
    class _Locked:
        def load_settings(self, _p): pass
        def account_info(self): raise ChallengeRequired("challenge_required")
        def login(self, *_a, **_k): raise AssertionError("default path must not call login()")
        def dump_settings(self, _p): pass
    try:
        open_client(cfg, client_factory=_Locked)
        raise AssertionError("expected ScrapeCheckpoint")
    except ScrapeCheckpoint as e:
        assert "Instagram app" in str(e) and "secret-must-not-leak" not in str(e)


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
    assert out0["throttled"] is True and out0.get("reason") == "no personas have a declared niche"
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


def test_checkpoint_freezes_layer_a_and_stops_reopening_scrape(tmp_path, monkeypatch):
    """MOL-699: a lock had NO backoff — the checkpoint abort returned before writing anything, so the
    next due tick logged in again against a locked account, which is what deepens a lock. It must arm a
    LONG freeze (not the rate-limit ladder) and the next tick must not open a client at all."""
    from datetime import datetime, timezone, timedelta
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import (refresh_store_if_due, _cooldown_path, _CHECKPOINT_DELAY_S)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    opens = {"n": 0}
    def locked(_cfg, **_k):
        opens["n"] += 1
        raise igs.ScrapeCheckpoint("account checkpointed by Instagram — verify the login in the app")
    monkeypatch.setattr(igs, "open_client", locked)
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is False and out["aborted"] == "checkpoint"
    assert opens["n"] == 1
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == "checkpoint"
    assert cd["until"] == (t0 + timedelta(seconds=_CHECKPOINT_DELAY_S)).isoformat()
    # Every tick inside the freeze must skip WITHOUT touching Instagram — the whole point.
    for mins in (1, 60, 600):
        skip = refresh_store_if_due(cfg, max_age_s=1, now=t0 + timedelta(minutes=mins))
        assert skip["refreshed"] is False and skip["reason"] == "cooldown"
        assert skip["cooldown_reason"] == "checkpoint"      # run.log says WHY it is frozen
    assert opens["n"] == 1, f"re-opened a locked account {opens['n']} times"
    assert not cfg.hashtags_path.exists()                   # cache untouched on every abort path


def test_login_required_arms_the_laddered_cooldown(tmp_path, monkeypatch):
    """MOL-699: a dead session was re-probed every tick forever. It gets the normal 30m→1h→2h→6h
    ladder (an expiry IS transient, unlike a lock), tagged with its own reason."""
    from datetime import datetime, timezone, timedelta
    from fanops.ig_hashtag_scrape import ScrapeRefused
    from fanops.fanops_hashtags import _cooldown_path, _COOLDOWN_DELAYS_S
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = refresh_store(cfg, scrape_client=_FakeClient({}, refuse=ScrapeRefused("login_required")), now=t0)
    assert out["aborted"] == "login_required" and out["written"] is False
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == "login_required" and cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[0])).isoformat()
    t1 = t0 + timedelta(hours=2)
    refresh_store(cfg, scrape_client=_FakeClient({}, refuse=ScrapeRefused("login_required")), now=t1)
    cd2 = json.loads(_cooldown_path(cfg).read_text())
    assert cd2["streak"] == 2                               # decaying retry, not every-tick hammering
    assert cd2["until"] == (t1 + timedelta(seconds=_COOLDOWN_DELAYS_S[1])).isoformat()
    assert not cfg.hashtags_path.exists()


def test_expired_session_at_open_arms_the_login_cooldown(tmp_path, monkeypatch):
    """MOL-727: the IN-PASS login_required refusal got the ladder, but an expiry raised while OPENING
    the client returned `no_scrape` with NO backoff — so every due tick re-probed a dead session
    forever. Same account, same failure, same remedy: same cooldown, one seam earlier."""
    from datetime import datetime, timezone, timedelta
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import refresh_store_if_due, _cooldown_path, _COOLDOWN_DELAYS_S
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    opens = {"n": 0}
    def expired(_cfg, **_k):
        opens["n"] += 1
        raise igs.ScrapeSessionExpired("session expired — run fanops hashtags scrape-login")
    monkeypatch.setattr(igs, "open_client", expired)
    out = refresh_store_if_due(cfg, max_age_s=1, now=t0)
    assert out["refreshed"] is False and out["aborted"] == "login_required"   # not `no_scrape`
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == "login_required" and cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[0])).isoformat()
    assert out["cooldown_until"] == cd["until"] and out["cooldown_streak"] == 1
    for mins in (1, 20, 29):                               # inside the floor: no client opened at all
        skip = refresh_store_if_due(cfg, max_age_s=1, now=t0 + timedelta(minutes=mins))
        assert skip["refreshed"] is False and skip["reason"] == "cooldown"
        assert skip["cooldown_reason"] == "login_required"  # run.log says WHY, and what fixes it
    assert opens["n"] == 1, f"re-probed a dead session {opens['n']} times"
    assert not cfg.hashtags_path.exists()                  # cache untouched on the abort path
    # The ladder still decays: the next pass after the floor deepens the streak (never a flat retry).
    refresh_store_if_due(cfg, max_age_s=1, now=t0 + timedelta(minutes=31))
    cd2 = json.loads(_cooldown_path(cfg).read_text())
    assert cd2["streak"] == 2 and opens["n"] == 2


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
    from fanops.ig_hashtag_scrape import ScrapeRefused
    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 8, 7, 10, 23, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", METRIC_FIELD: 50.0,
                    "measured_at": (t0 - timedelta(hours=1)).isoformat()},
        "last_complete_pass": (t0 - timedelta(hours=1)).isoformat()}))
    refresh_store(cfg, scrape_client=_FakeClient({}, refuse=ScrapeRefused("login_required")), now=t0)
    arms = _log_recs(cfg, "scrape_cooldown")
    assert arms and arms[-1]["streak"] == "1" and arms[-1]["level"] == "info"   # routine first arm
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["last_complete_pass"] = (t0 - timedelta(days=5)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    refresh_store(cfg, scrape_client=_FakeClient({}, refuse=ScrapeRefused("login_required")),
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
    assert cd["reason"] == "throttle"
    assert cd["streak"] == 3                               # prior 2 NOT cleared — bump to 3 (no sawtooth)
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[2])).isoformat()   # 2h rung
    assert cd["day"] == "2026-07-01" and cd["used"] >= 1 and isinstance(cd.get("accounts"), dict)
    assert out["cooldown_until"] == cd["until"]
    nxt = _FakeClient({"#hiphop": 50})
    skip = refresh_store_if_due(cfg, max_age_s=1, scrape_client=nxt, now=t0 + timedelta(minutes=29))
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"
    assert nxt.info_calls == [] and nxt.media_calls == []   # not one request against a throttling account


def test_partial_progress_then_login_required_still_arms_a_cooldown(tmp_path, monkeypatch):
    """MOL-727 sibling: the same chain swallowed a dead session after partial progress. It must arm the
    login ladder AND leave the pass marked incomplete — a queue cut short mid-way has not completed."""
    from datetime import datetime, timezone, timedelta
    from fanops.ig_hashtag_scrape import ScrapeRefused
    from fanops.fanops_hashtags import _cooldown_path, _COOLDOWN_DELAYS_S

    class _DeadAfterFirst(_FakeClient):
        """First tag lands; the session dies on the next medias_top (the MOL-696 shape: the client
        opens fine and hashtag_info still answers, but the read comes back login_required)."""
        def hashtag_medias_top(self, name, amount=9):
            if self.media_calls:
                raise ScrapeRefused("login_required")
            return super().hashtag_medias_top(name, amount=amount)

    cfg = Config(root=tmp_path); _persona(cfg)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out = refresh_store(cfg, scrape_client=_DeadAfterFirst({"#hiphop": 50, "#alpha": 100},
                                                           cooccur="#alpha"), now=t0)
    assert out["measured"] == 1 and out["written"] is True
    assert out["throttled"] is True                        # incomplete queue == incomplete pass
    raw = json.loads(cfg.hashtags_path.read_text())
    assert "#hiphop" in raw and "last_complete_pass" not in raw
    cd = json.loads(_cooldown_path(cfg).read_text())
    assert cd["reason"] == "login_required" and cd["streak"] == 1
    assert cd["until"] == (t0 + timedelta(seconds=_COOLDOWN_DELAYS_S[0])).isoformat()
    assert cd["day"] == "2026-07-01" and cd["used"] >= 1


def test_scrape_login_ignores_and_clears_an_active_freeze(tmp_path, monkeypatch):
    """The operator escape hatch: scrape-login is an explicit human act AFTER an unlock, so it must run
    with a freeze armed and clear it on success — otherwise a fixed account stays frozen for 12h."""
    from datetime import datetime, timezone
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import (cmd_hashtags_scrape_login, _cooldown_path, _persist_cooldown,
                                        _CHECKPOINT_DELAY_S)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u"); monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    _persist_cooldown(cfg, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                      reason="checkpoint", delay_s=_CHECKPOINT_DELAY_S)
    assert _cooldown_path(cfg).exists()
    monkeypatch.setattr(igs, "open_client", lambda _c, **_k: object())
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


def test_open_client_stale_session_refuses_without_login(tmp_path, monkeypatch):
    """Unattended path: LoginRequired from account_info → ScrapeUnavailable; login() never called."""
    from fanops.ig_hashtag_scrape import ScrapeSessionExpired, ScrapeUnavailable, open_client
    assert issubclass(ScrapeSessionExpired, ScrapeUnavailable)   # every existing abort path still catches it
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    seen = {"login": 0}
    class LoginRequired(Exception): pass
    class _Stale:
        def load_settings(self, _p): pass
        def account_info(self): raise LoginRequired("login_required")
        def login(self, *_a, **_k): seen["login"] += 1
        def dump_settings(self, _p): pass
    try:
        open_client(cfg, client_factory=_Stale)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "session expired" in str(e)
        # MOL-727: Layer A arms the login cooldown off the TYPE, so this raise must stay the subclass
        # — classifying by grepping the message text is what would rot on a reword.
        assert isinstance(e, ScrapeSessionExpired)
    assert seen["login"] == 0


def test_open_client_default_path_never_reads_password(tmp_path, monkeypatch):
    """Default path must not touch ig_scrape_password — even when a stale session probes LoginRequired."""
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    class LoginRequired(Exception): pass
    class _Stale:
        def load_settings(self, _p): pass
        def account_info(self): raise LoginRequired("login_required")
        def login(self, *_a, **_k): raise AssertionError("login must not run")
        def dump_settings(self, _p): pass
    def _boom(_self):
        raise AssertionError("default path must not read ig_scrape_password")
    monkeypatch.setattr(Config, "ig_scrape_password", property(_boom))
    try:
        open_client(cfg, client_factory=_Stale)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "session expired" in str(e)


def test_open_client_allow_reauth_calls_login_relogin_once(tmp_path, monkeypatch):
    """Operator path: LoginRequired → login(..., relogin=True) exactly once."""
    from fanops.ig_hashtag_scrape import open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    seen = []
    class LoginRequired(Exception): pass
    class _Stale:
        def load_settings(self, _p): pass
        def account_info(self): raise LoginRequired("login_required")
        def login(self, user, pw, relogin=False):
            seen.append((user, pw, relogin))
        def dump_settings(self, _p): pass
    c = open_client(cfg, client_factory=_Stale, allow_reauth=True)
    assert c is not None
    assert seen == [("u", "p", True)]


def test_open_client_valid_session_skips_login_on_both_paths(tmp_path, monkeypatch):
    """account_info success → return client; login() never called, with or without allow_reauth."""
    from fanops.ig_hashtag_scrape import open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    for allow in (False, True):
        seen = {"login": 0}
        class _Ok:
            def load_settings(self, _p): pass
            def account_info(self): return {"pk": 1}
            def login(self, *_a, **_k): seen["login"] += 1
            def dump_settings(self, _p): pass
        open_client(cfg, client_factory=_Ok, allow_reauth=allow)
        assert seen["login"] == 0, allow


def test_open_client_missing_session_refuses_on_default_path(tmp_path, monkeypatch):
    """Cold start is operator-only — unattended tick must not password-login with no session file."""
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    assert not cfg.ig_scrape_session_path.exists()
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
    """doctor + _refresh_pass must call open_client without allow_reauth (password re-auth is operator-only)."""
    import inspect
    import fanops.doctor as doctor
    import fanops.fanops_hashtags as fh
    src_doc = inspect.getsource(doctor._hashtag_scrape_check)
    src_ref = inspect.getsource(fh._refresh_pass)
    src_login = inspect.getsource(fh.cmd_hashtags_scrape_login)
    assert "allow_reauth=True" not in src_doc
    assert "allow_reauth=True" not in src_ref
    assert "allow_reauth=True" in src_login


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
    assert out["measured"] == 0 and out["written"] is False and out.get("reason") == "no personas have a declared niche"
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
    import fanops.fanops_hashtags as fh
    monkeypatch.setattr(fh, "_SCRAPE_TRY_CAP", 7)
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
    """MOL-854: per-pass try_cap is a small ceiling; UTC day budget is the local governor."""
    import fanops.fanops_hashtags as fh
    from fanops.settings import Settings
    assert fh._SCRAPE_TRY_CAP == 25
    assert fh._SCRAPE_DAY_BUDGET == 40
    assert Settings.model_fields["FANOPS_HASHTAG_SCRAPE_TRY_CAP"].default == 25
    assert fh._SCRAPE_COTAG_ENQUEUE_CAP == 40
    assert fh._VOLUME_MAX_AGE_DAYS == 7
    assert fh._SCRAPE_PARALLEL == 1              # MOL-698: one account, one in-flight request



def test_day_budget_exhaustion_skips_refresh(tmp_path, monkeypatch):
    """MOL-854: when cooldown blob day/used hits `_SCRAPE_DAY_BUDGET`, refresh_store_if_due skips
    with reason=budget and opens no scrape — even with no ladder `until` armed."""
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
    skip = refresh_store_if_due(cfg, max_age_s=1, scrape_client=nxt, now=t0)
    assert skip["refreshed"] is False and skip["reason"] == "cooldown"
    assert skip.get("cooldown_reason") == "budget"
    assert nxt.info_calls == [] and nxt.media_calls == []



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
    assert out.get("written") is False and out.get("reason") == "no personas have a declared niche"


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


def test_layer_a_emits_one_client_no_session_clones(tmp_path, monkeypatch):
    """MOL-698: the 2026-07-29 challenge_required was earned by 4 concurrent clients sharing ONE
    dumped session (same device fingerprint, 4 simultaneous private-API calls) — the loudest
    anti-bot signal we emit, and instagrapi is not thread-safe. The clone fan-out is GONE: one
    client, serialized behind client_lock, and the session-clone helper no longer exists."""
    import fanops.fanops_hashtags as fh
    import fanops.ig_hashtag_scrape as igs
    assert not hasattr(igs, "session_client"), "session-clone fan-out must stay deleted"
    assert "session_client" not in inspect.getsource(fh)
    monkeypatch.delenv("FANOPS_HASHTAG_SCRAPE_PARALLEL", raising=False)
    assert fh._scrape_parallel() == 1                       # serial BY DEFAULT, not by env


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
    """The pacing must land ON the client instagrapi actually uses — set before any call."""
    from fanops.ig_hashtag_scrape import open_client
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.delenv("FANOPS_HASHTAG_SCRAPE_DELAY", raising=False)
    cfg = Config(root=tmp_path)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    seen = {}
    class _Fake:
        def load_settings(self, _p): pass
        def account_info(self): seen["delay_at_probe"] = self.delay_range
        def login(self, *_a, **_k): raise AssertionError("valid session must not login")
        def dump_settings(self, _p): pass
    c = open_client(cfg, client_factory=_Fake)
    assert c.delay_range == [1.0, 3.0]
    assert seen["delay_at_probe"] == [1.0, 3.0]             # paced through the probe, not just fetches


def test_refresh_store_early_aborts_on_login_required(tmp_path, monkeypatch):
    """MOL-696: login_required must stop the pass — not burn try_cap spinning refusals."""
    import fanops.fanops_hashtags as fh
    from fanops.ig_hashtag_scrape import ScrapeRefused
    monkeypatch.setattr(fh, "_SCRAPE_TRY_CAP", 40)
    cfg = Config(root=tmp_path); _persona(cfg)
    client = _FakeClient({}, refuse=ScrapeRefused("login_required"))
    out = refresh_store(cfg, scrape_client=client)
    assert out.get("aborted") == "login_required"
    assert out.get("reason") == "login_required"
    assert out["written"] is False and out["measured"] == 0
    assert out["tried"] == 1, f"must abort after first refusal, tried={out['tried']}"
    assert not cfg.hashtags_path.exists()
    assert len(client.info_calls) == 1
