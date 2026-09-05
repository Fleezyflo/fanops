# tests/test_doctor.py — Phase 3b: `fanops doctor` read-only first-run health screen. Asserts only
# on env-controlled checks (key/claude/notes), never host-dependent toolchain presence.
import json
import logging
from fanops.config import Config
from fanops import doctor

_KEY = "sk-postiz-LEAK-CANARY-1234567890"          # a recognizable sentinel: must never reach the report

def _postiz_cfg(tmp_path, *, mapped=True, validated=True):
    """A Config for the M4 Postiz-learning-ready check: one ACTIVE account (fully mapped, or with an
    unmapped instagram channel) + cutover.json (metrics_confirmed or not). Writes via cfg paths so the
    data-root layout (MohFlow-FanOps/00_control) is never hand-guessed (mirrors test_doctor_notes_review_queue_count)."""
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    integ = {"instagram": "ig_1"} if mapped else {}
    # R2: a mapped channel pairs integrations[p] with backends[p] (no drift); unmapped stays empty.
    backs = {"instagram": "postiz"} if mapped else {}
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@probe", "platforms": ["instagram"], "status": "active", "access": "postiz",
         "integrations": integ, "backends": backs}]}))
    cfg.cutover_path.write_text(json.dumps({"metrics_confirmed": bool(validated)}))
    return cfg

def _learning_check(rep):
    return next((c for c in rep["checks"] if "learning" in c["label"].lower() and "postiz" in c["label"].lower()), None)


def _env_check(rep):
    return next((c for c in rep["checks"] if "strict Settings" in c["label"]), None)


def test_doctor_fails_on_bad_fanops_poster_typo(tmp_path, monkeypatch):
    # strict doctor path must FAIL LOUD on a typo'd FANOPS_POSTER — runtime Config still dryruns (W4).
    monkeypatch.setenv("FANOPS_POSTER", "positz")        # typo of "postiz"
    rep = doctor.doctor_report(Config(root=tmp_path))
    ec = _env_check(rep)
    assert ec is not None and ec["ok"] is False
    assert "FANOPS_POSTER" in ec["hint"]


def test_doctor_passes_valid_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    ec = _env_check(rep)
    assert ec is not None and ec["ok"] is True


def test_runtime_config_failopen_on_poster_typo_while_doctor_fails(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("FANOPS_POSTER", "positz")
    with caplog.at_level(logging.WARNING):
        c = Config(root=tmp_path)
    assert c.poster_backend == "dryrun"
    assert any("FANOPS_POSTER" in r.getMessage() for r in caplog.records)
    rep = doctor.doctor_report(c)
    assert _env_check(rep)["ok"] is False


def test_doctor_flags_missing_brand_brief(tmp_path):
    # context.md is the #1 output lever; its ABSENCE used to be silent. doctor must surface it as a
    # readiness failure so an operator never runs an ungrounded engine without knowing.
    cfg = Config(root=tmp_path)                          # no context.md written
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is False and "context.md" in bc["hint"]


def test_doctor_passes_with_brand_brief(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("BRAND: confident, bilingual. Pick the bars.")
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is True


def test_doctor_flags_empty_brand_brief(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("   \n\t  ")             # present but blank -> still ungrounded
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is False


def test_doctor_always_checks_llm_cli(tmp_path, monkeypatch):
    # Gates are answered ONLY by the LLM (the manual responder was retired), so the CLI check is ALWAYS
    # surfaced — empty/unset OR the literal 'llm' both resolve to llm; there is no responder that skips it.
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("claude" in c["label"].lower() for c in rep["checks"])
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    rep2 = doctor.doctor_report(Config(root=tmp_path))
    assert any("claude" in c["label"].lower() for c in rep2["checks"])


def test_doctor_flags_bad_responder_and_still_checks_cli(tmp_path, monkeypatch):
    # An unknown FANOPS_RESPONDER is a HARD REFUSE surfaced as its OWN failing check (never a traceback);
    # the CLI check is still emitted because it is unconditional now.
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    rep = doctor.doctor_report(Config(root=tmp_path))
    resp_checks = [c for c in rep["checks"] if "FANOPS_RESPONDER" in c["label"]]
    assert resp_checks and all(not c["ok"] for c in resp_checks)
    assert any("claude" in c["label"].lower() for c in rep["checks"])


def test_doctor_cursor_transport_checks_cursor_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "cursor")
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("cursor-agent" in c["label"].lower() for c in rep["checks"])
    # Absolute transport: no silent claude vision fallback — doctor fails the vision gate loudly.
    vision = next(c for c in rep["checks"] if "vision" in c["label"].lower())
    assert vision["ok"] is False and "claude" in (vision.get("hint") or "").lower()

def test_doctor_notes_learning_unvalidated(tmp_path, monkeypatch):
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("cutover" in n.lower() for n in rep["notes"])    # points at the go-live harness

def test_doctor_notes_review_queue_count(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "a.jpg").write_bytes(b"J"); (cfg.review / "b.jpg").write_bytes(b"J")
    rep = doctor.doctor_report(cfg)
    assert any("2" in n and "review" in n.lower() for n in rep["notes"])

def test_cli_doctor_runs_and_prints(tmp_path, monkeypatch, capsys):
    from fanops.cli import main
    monkeypatch.chdir(tmp_path)
    rc = main(["doctor"])
    assert rc in (0, 1)
    assert "doctor" in capsys.readouterr().out.lower()

# --- M4: Postiz-learning readiness check + Blotato-string fixes ---

def test_doctor_postiz_learning_ready_all_green(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is True            # key set + every channel mapped + cutover confirmed

def test_doctor_postiz_learning_not_ready_key_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    c = _learning_check(rep)
    assert c is None  # B11: no Postiz creds -> learning checks omitted (not a vacuous pass)

def test_doctor_postiz_learning_not_ready_channel_unmapped(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, mapped=False))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is False and "map" in c["hint"].lower()

def test_doctor_postiz_learning_not_ready_cutover_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, validated=False))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is False and "Validate learning" in c["hint"]

def test_doctor_report_never_leaks_postiz_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    assert _KEY not in json.dumps(rep)                  # the key VALUE must never reach a label/hint/note

def test_doctor_accounts_hint_names_studio_not_blotato(tmp_path, monkeypatch):
    # line-38 Blotato-string fix: the accounts-mapping hint must name the real post-PR#22 path (Studio), not Blotato
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, mapped=False))     # @probe instagram unmapped -> problem surfaced
    ac = [c for c in rep["checks"] if "accounts.json" in c["label"]][0]
    assert ac["ok"] is False and "Blotato" not in ac["hint"] and "Studio" in ac["hint"]

def test_doctor_learning_note_names_studio_validate(tmp_path, monkeypatch):
    # line-57 Blotato-string fix: the unvalidated-learning note names the Studio Validate learning step
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("Validate learning" in n for n in rep["notes"])


def test_doctor_flags_insights_blocked_scope(tmp_path):
    # Leg 2: when Graph media-insights was refused for lack of instagram_manage_insights, the persisted
    # breadcrumb must surface LOUDLY in doctor (the one external gate), with the exact next action.
    import json
    cfg = Config(root=tmp_path)
    cfg.insights_blocked_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.insights_blocked_path.write_text(json.dumps({"blocked": True}))
    rep = doctor.doctor_report(cfg)
    ic = next((c for c in rep["checks"] if "insights" in c["label"].lower()), None)
    assert ic is not None and ic["ok"] is False and "instagram_manage_insights" in ic["hint"]


def test_doctor_insights_check_passes_when_not_blocked(tmp_path):
    # No breadcrumb -> the insights read is healthy -> the check passes (never a false alarm).
    cfg = Config(root=tmp_path)
    rep = doctor.doctor_report(cfg)
    ic = next((c for c in rep["checks"] if "insights" in c["label"].lower()), None)
    assert ic is not None and ic["ok"] is True


# --- T9: Meta token expiry preflight (debug_token) + rotation runbook ---

class _FakeResp:
    def __init__(self, payload, status=200): self._p = payload; self.status_code = status
    def json(self): return self._p

def _debug_token_getter(expires_at, *, is_valid=True, status=200):
    """A fake requests.get for the Graph debug_token endpoint: returns {data:{expires_at,is_valid}}.
    expires_at is epoch seconds (0 = never-expires long-lived token). Records nothing about the token
    value (the test also asserts the token never leaks)."""
    def _get(url, params=None, timeout=None, **kw):
        return _FakeResp({"data": {"expires_at": expires_at, "is_valid": is_valid}}, status)
    return _get

def _tokencheck(rep):
    return next((c for c in rep["checks"] if "meta" in c["label"].lower() and "token" in c["label"].lower()), None)

_META_TOK = "EAA-META-LEAK-CANARY-0987654321"     # sentinel: must never reach the report


def test_doctor_warns_on_expiring_meta_token(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("META_GRAPH_TOKEN", _META_TOK)
    monkeypatch.setenv("META_IG_USER_ID", "17841400000000001")
    cfg = Config(root=tmp_path)
    now = time.time()

    # (a) FAR FUTURE (90 days) -> Severity.OK.
    c = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(int(now + 90 * 86400))))
    assert c is not None and c["ok"] is True and c.get("severity") == "ok"

    # (b) INSIDE the lead window (<=10 days; use 5 days) -> Severity.WARN (non-blocking).
    c2 = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(int(now + 5 * 86400))))
    assert c2 is not None and c2.get("severity") == "warn" and "expir" in (c2.get("hint") or "").lower()

    # (c) EXPIRED (past) -> FAIL.
    c3 = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(int(now - 86400))))
    assert c3 is not None and c3["ok"] is False

    # (d) UNREADABLE introspection (non-200 / no data) -> FAIL CLOSED (unknown != pass), no crash.
    c4 = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(0, status=500)))
    assert c4 is not None and c4["ok"] is False

    # (e) the token value must NEVER appear anywhere in the report.
    import json as _json
    assert _META_TOK not in _json.dumps(doctor.doctor_report(cfg, get=_debug_token_getter(int(now + 5 * 86400))))


def test_doctor_meta_token_check_absent_when_no_token(tmp_path, monkeypatch):
    # No Meta token configured -> the expiry check is simply not applicable (no false alarm, no crash).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    rep = doctor.doctor_report(tmp_path if False else Config(root=tmp_path))
    c = _tokencheck(rep)
    # either omitted, or present-and-ok (never a false FAIL when there is no token to introspect)
    assert c is None or c["ok"] is True


# --- T10: Postiz real-probe + Zernio auth in doctor (network-injected) ---

def _postiz_check(rep):
    return next((c for c in rep["checks"] if "postiz" in c["label"].lower() and ("reachable" in c["label"].lower() or "backend" in c["label"].lower())), None)

def _zernio_check(rep):
    return next((c for c in rep["checks"] if "zernio" in c["label"].lower()), None)


def test_doctor_postiz_real_probe_healthy(tmp_path, monkeypatch):
    # A postiz deployment WITH a key + a HEALTHY real probe -> the Postiz-reachable check passes.
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    cfg = _postiz_cfg(tmp_path)
    rep = doctor.doctor_report(cfg, postiz_probe=lambda c: PostizHealth(True, 200, ""))
    c = _postiz_check(rep)
    assert c is not None and c["ok"] is True


def test_doctor_postiz_real_probe_unhealthy(tmp_path, monkeypatch):
    # The nginx health-check LIES; the real probe (GET /integrations) is 502 -> doctor reports Postiz down,
    # with the POSTIZ_OPS pointer, NEVER the key.
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    cfg = _postiz_cfg(tmp_path)
    down = PostizHealth(False, 502, "Postiz backend unreachable (502) — see docs/POSTIZ_OPS.md.")
    rep = doctor.doctor_report(cfg, postiz_probe=lambda c: down)
    c = _postiz_check(rep)
    assert c is not None and c["ok"] is False and "POSTIZ_OPS" in c["hint"]
    assert _KEY not in json.dumps(rep)


def test_doctor_zernio_auth_ok_and_fail(tmp_path, monkeypatch):
    # A zernio-routed deployment: a good key -> ok; a 401 (ZernioAuthError) / unreachable (False) -> fail-closed.
    from fanops.errors import ZernioAuthError
    monkeypatch.setenv("ZERNIO_API_KEY", "zk-LEAK-CANARY")
    # one active tiktok channel routed to zernio so the check is applicable
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@tk", "platforms": ["tiktok"], "status": "active", "access": "zernio",
         "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}]}))
    ok_rep = doctor.doctor_report(cfg, zernio_auth=lambda c: True)
    zc = _zernio_check(ok_rep)
    assert zc is not None and zc["ok"] is True

    def _boom(c): raise ZernioAuthError("401")
    bad = doctor.doctor_report(cfg, zernio_auth=_boom)
    zc2 = _zernio_check(bad)
    assert zc2 is not None and zc2["ok"] is False

    unreachable = doctor.doctor_report(cfg, zernio_auth=lambda c: False)
    zc3 = _zernio_check(unreachable)
    assert zc3 is not None and zc3["ok"] is False


# --- T12: permanent daemon-liveness + past-due-backlog guard (fail-closed) ---

def _daemon_check(rep):
    return next((c for c in rep["checks"] if "daemon" in c["label"].lower() or "pump" in c["label"].lower()), None)

def _write_heartbeat(cfg, *, age_seconds):
    """Append a valid run.log heartbeat JSON line whose ts is `age_seconds` in the past (mirrors
    log.py so daemon._heartbeat_age_s parses it)."""
    import json
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": ts, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok", "origin": "loop",
           "heartbeat": ts, "published_in_run": "0"}
    with open(cfg.log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

def _seed_queued_post(cfg, *, when):
    """Add ONE queued post with scheduled_time=`when` (an ISO string) via the ledger."""
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="pq", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=when))
    led.save()


# --- MOL-474: faster-whisper [asr] import probe (transcribe._fw_available) ---

def _fw_check(rep):
    return next((c for c in rep["checks"] if "faster-whisper" in c["label"].lower()), None)


def test_doctor_fails_when_faster_whisper_unavailable(tmp_path, monkeypatch):
    # Bare install (no [asr] extra) -> doctor fails closed with the venv recipe.
    monkeypatch.setattr("fanops.transcribe._fw_available", lambda: False)
    c = _fw_check(doctor.doctor_report(Config(root=tmp_path)))
    assert c is not None and c["ok"] is False and "[asr]" in c["hint"]


def test_doctor_passes_when_faster_whisper_available(tmp_path, monkeypatch):
    # [asr] installed -> the faster-whisper probe passes (engine selection matches transcribe_source).
    monkeypatch.setattr("fanops.transcribe._fw_available", lambda: True)
    c = _fw_check(doctor.doctor_report(Config(root=tmp_path)))
    assert c is not None and c["ok"] is True


def test_doctor_fails_on_dead_daemon_or_past_due_backlog(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    FUT = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    PAST = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()

    # (a) MISSING signal (no run.log heartbeat ever) -> FAIL CLOSED (unknown != healthy), no crash.
    cfg = Config(root=tmp_path)
    c = _daemon_check(doctor.doctor_report(cfg))
    assert c is not None and c["ok"] is False

    # (b) STALE tick (heartbeat 3h old) + a future-only queue -> FAIL (dead/stopped pump).
    cfg_b = Config(root=tmp_path / "b")
    _write_heartbeat(cfg_b, age_seconds=3 * 3600)
    _seed_queued_post(cfg_b, when=FUT)
    c_b = _daemon_check(doctor.doctor_report(cfg_b))
    assert c_b is not None and c_b["ok"] is False

    # (c) LIVE tick (fresh 30s heartbeat) but N PAST-DUE queued -> FAIL naming the count + oldest age.
    cfg_c = Config(root=tmp_path / "c")
    _write_heartbeat(cfg_c, age_seconds=30)
    _seed_queued_post(cfg_c, when=PAST)
    c_c = _daemon_check(doctor.doctor_report(cfg_c))
    assert c_c is not None and c_c["ok"] is False and "1" in c_c["hint"]

    # (d) HEALTHY: fresh tick + only-future queued -> PASS.
    cfg_d = Config(root=tmp_path / "d")
    _write_heartbeat(cfg_d, age_seconds=30)
    _seed_queued_post(cfg_d, when=FUT)
    c_d = _daemon_check(doctor.doctor_report(cfg_d))
    assert c_d is not None and c_d["ok"] is True


def _append_fresh_line(cfg, *, stage="llm"):
    """Append one FRESH run.log line of any kind — the activity signal daemon_progress reads to prove
    a long stage is still emitting (== alive)."""
    import json
    from datetime import datetime, timezone
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": "info", "stage": stage,
           "unit_id": "-", "outcome": "ok"}
    with open(cfg.log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def test_doctor_passes_stale_heartbeat_during_live_mid_pass(tmp_path, monkeypatch):
    # The daemon check inherits the fix: a stale LOOP heartbeat but FRESH run.log activity (the pass is
    # still emitting) reads alive, not "pump wedged". Activity — not stage_age, not the loop heartbeat.
    import fcntl, os
    from datetime import datetime, timezone, timedelta
    from fanops.pipeline_run import note_stage, _lock_path
    cfg = Config(root=tmp_path)
    _write_heartbeat(cfg, age_seconds=3 * 3600)             # loop heartbeat is 3h stale
    _append_fresh_line(cfg, stage="llm")                    # ...but the pass IS still emitting NOW
    FUT = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    _seed_queued_post(cfg, when=FUT)
    monkeypatch.setattr("fanops.daemon.subprocess.run",
                        _fake_launchctl_daemon(list=(0, '\t"PID" = 1;\n')))
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        note_stage(cfg, "transcribe", "src-1")
        c = _daemon_check(doctor.doctor_report(cfg))
        assert c is not None and c["ok"] is True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_doctor_hint_says_log_silent_when_stage_wedged(tmp_path, monkeypatch):
    # Change 1d wording: when a stage IS held AND the log has gone SILENT past the ceiling, the doctor
    # mid-pass hint says "log SILENT {n}s", NOT "has run {stage_age}s" — silence is the wedged signal.
    import fcntl, json, os
    from datetime import datetime, timezone, timedelta
    from fanops.health_model import _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import _lock_path
    cfg = Config(root=tmp_path)
    _write_heartbeat(cfg, age_seconds=3 * 3600)             # stale loop heartbeat
    sil = datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 200)
    rec = {"ts": sil.isoformat(), "level": "info", "stage": "transcribe", "unit_id": "src-1", "outcome": "ok"}
    with open(cfg.log_path, "a", encoding="utf-8") as fh:   # newest line is SILENT > ceiling
        fh.write(json.dumps(rec) + "\n")
    monkeypatch.setattr("fanops.daemon.subprocess.run",
                        _fake_launchctl_daemon(list=(0, '\t"PID" = 1;\n')))
    s = sil.strftime("%Y-%m-%dT%H:%M:%SZ")
    lp = _lock_path(cfg); lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": 1, "started": s, "stage": "transcribe", "unit": "src-1",
                             "stage_started": s}).encode())
    try:
        c = _daemon_check(doctor.doctor_report(cfg))
        assert c is not None and c["ok"] is False
        assert "log SILENT" in c["hint"] and "has run" not in c["hint"]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_setup_next_action_awaiting_gate_wording(tmp_path, monkeypatch):
    # Change 4 wording: operator prose says "awaiting gate answer(s)", never "blocked on gate(s)"
    # (pending work the cursor responder is clearing — not a fault). The internal field name
    # blocked_on_gates is untouched (machine-scraped); only the human sentence changed.
    from fanops import pipeline_status
    from fanops.pipeline_status import SourceBacklog
    from fanops.doctor import setup_next_action
    cfg = Config(root=tmp_path)
    fake = SourceBacklog(actionable=0, blocked_on_gates=3, recoverable=0, inventory=0, held=0, rows=[])
    monkeypatch.setattr(pipeline_status, "source_backlog", lambda led, c: fake)
    msg = setup_next_action(cfg)
    assert "awaiting gate answer(s)" in msg
    assert "blocked on gate" not in msg


def _fake_launchctl_daemon(**spec):
    import subprocess
    def run(cmd, *a, **k):
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


# --- Hashtag Layer A scrape session (instagrapi) ---
def test_doctor_hashtag_scrape_session_check(tmp_path, monkeypatch):
    """MOL-965: soft setup incompleteness is N/A (omitted) — never ok=True pretend PASS."""
    from fanops import doctor
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path)
    rep = doctor.doctor_report(cfg)
    assert not any("hashtag Layer A scrape" in c["label"] for c in rep["checks"])
    assert doctor._hashtag_scrape_check(cfg) is None
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    # Credentials alone (no session file) still N/A — setup-in-progress, not a green PASS.
    rep2 = doctor.doctor_report(Config(root=tmp_path))
    assert not any("hashtag Layer A scrape" in c["label"] for c in rep2["checks"])
    assert doctor._hashtag_scrape_check(Config(root=tmp_path)) is None



def test_doctor_hashtag_scrape_soft_ok_when_any_session_among_users(tmp_path, monkeypatch):
    """HT3: any listed user session is enough — presence only, no open_client / tag probe."""
    from fanops import doctor
    from fanops.config import Config
    from fanops.ig_hashtag_scrape import scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "b")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    opens = {"n": 0}
    def boom_open(_c):
        opens["n"] += 1
        raise AssertionError("doctor must not open_client")
    def boom_probe(*_a, **_k):
        raise AssertionError("doctor must not probe tags")
    row = doctor._hashtag_scrape_check(cfg, open_client=boom_open, probe_resolve=boom_probe)
    assert row["ok"] is True and row["hint"] == ""
    assert opens["n"] == 0
    assert "present" in row["label"]


def test_doctor_hashtag_scrape_session_presence_ok_without_probe(tmp_path, monkeypatch):
    """HT3: session file present → PASS; open_client / tag probe never called (even if they would fail)."""
    from fanops import doctor
    from fanops.config import Config
    from fanops.ig_hashtag_scrape import ScrapeUnavailable
    from instagrapi.exceptions import ChallengeRequired, LoginRequired
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "secret-password-must-not-leak")
    cfg = Config(root=tmp_path)
    from fanops.ig_hashtag_scrape import scrape_session_path
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    _sess.write_text("{}")
    calls = {"open": 0, "probe": 0}
    def boom(_cfg):
        calls["open"] += 1
        raise ScrapeUnavailable("scrape login failed: login_required")
    def boom_probe(*_a, **_k):
        calls["probe"] += 1
        raise LoginRequired("login_required")
    row = doctor._hashtag_scrape_check(cfg, open_client=boom, probe_resolve=boom_probe)
    assert row["ok"] is True and row["hint"] == ""
    assert calls == {"open": 0, "probe": 0}
    assert "secret-password" not in row["hint"] and "secret-password" not in row["label"]
    # Challenge inject also ignored — offline presence only.
    def locked(_cfg):
        calls["open"] += 1
        raise ChallengeRequired("challenge_required")
    row2 = doctor._hashtag_scrape_check(cfg, open_client=locked)
    assert row2["ok"] is True and calls["open"] == 0


def test_doctor_hashtag_scrape_check_source_has_no_live_probe():
    """HT3 acceptance: doctor hashtag check must not call tag API / open_client."""
    import inspect
    from fanops import doctor
    src = inspect.getsource(doctor._hashtag_scrape_check)
    assert "resolve_hashtag_scrape" not in src
    assert "open_client as" not in src
    assert "opener(" not in src
    assert "probe(" not in src
    assert "any_scrape_session" in src


def test_doctor_ast_never_references_persist_or_freeze():
    """CPDP-WP4: doctor.py must not Name/ImportFrom/alias `_persist_cooldown` or `_freeze_for`."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / "fanops" / "doctor.py").read_text())
    banned = {"_persist_cooldown", "_freeze_for"}
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in banned:
            hits.append(n.id)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name in banned or (a.asname or "") in banned:
                    hits.append(a.asname or a.name)
        elif isinstance(n, ast.alias) and (n.name in banned or (n.asname or "") in banned):
            hits.append(n.asname or n.name)
    assert hits == [], f"doctor must not reference persist/freeze: {hits}"


# ---- Wave 4: doctor operational sensors (WARN unless fatal) + honesty guards ----

def _by_label(rep, needle):
    return next((c for c in rep["checks"] if needle in c["label"]), None)


def test_cli_on_path_is_warn_not_a_silent_authenticated_pass(tmp_path, monkeypatch):
    """PATH ok != authenticated. When the LLM CLI binary is present, severity=WARN (non-blocking)
    makes explicit that PATH is NOT proof of login — never a silent authenticated PASS."""
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: "/usr/local/bin/stub")
    rep = doctor.doctor_report(Config(root=tmp_path))
    cli = next((c for c in rep["checks"] if "on PATH" in c["label"] and "NOT proof" in (c.get("hint") or "")), None)
    assert cli is not None and cli["ok"] is True and cli.get("severity") == "warn"
    assert "NOT proof" in cli.get("hint", "")


def test_half_live_never_fails_open_to_a_silent_healthy_pass(tmp_path, monkeypatch):
    """If the live-route coherence check cannot be COMPUTED (route read raises), half-live must not present
    solid LIVE — ok=False with a hint that LIVE was not confirmed."""
    monkeypatch.setenv("FANOPS_LIVE", "1")
    def _boom(self):
        raise RuntimeError("route read hiccup")
    monkeypatch.setattr(type(Config(root=tmp_path)), "live_route_exists", property(_boom))
    rep = doctor.doctor_report(Config(root=tmp_path))
    lr = _by_label(rep, "live route exists")
    assert lr is not None and lr["ok"] is False
    assert "not confirmed" in (lr.get("hint") or "").lower()


def test_operational_sensors_warn_on_backlog_and_parked_reopen(tmp_path, monkeypatch):
    """blocked_on_gates, degraded/errored sources, and parked machine re-opens surface as
    Severity.FAIL so report_is_healthy / doctor exit are NONZERO (MOL-960/MOL-965)."""
    from fanops import pipeline_status
    from fanops.pipeline_status import SourceBacklog
    cfg = Config(root=tmp_path)
    fake = SourceBacklog(actionable=0, blocked_on_gates=2, recoverable=1, inventory=0, held=0, rows=[])
    monkeypatch.setattr(pipeline_status, "source_backlog", lambda led, c, *a, **k: fake)
    class _Src:
        meta = {"pending_reopen": {"origin": "amplify"}}
    class _Led:
        sources = {"s1": _Src()}
    monkeypatch.setattr("fanops.ledger.Ledger.load", classmethod(lambda cls, c: _Led()))
    checks = doctor._operational_sensor_checks(cfg)
    labels = {c["label"]: c for c in checks}
    assert labels["no sources awaiting gate answers"]["severity"] == "fail"
    assert labels["no degraded/errored sources"]["severity"] == "fail"
    assert labels["no parked machine re-opens"]["severity"] == "fail"
    assert all(c["ok"] is False for c in checks)             # progress-blocking → unhealthy


def test_operational_sensor_warns_on_stale_pending_gate(tmp_path, monkeypatch):
    """A pending agent-gate older than _GATE_STALE_TICKS ticks is Severity.FAIL (progress-blocking);
    a fresh gate would not surface. MOL-960/MOL-965: doctor exit must not stay green on a stuck responder."""
    from datetime import datetime, timezone
    from fanops import pipeline_status, daemon
    cfg = Config(root=tmp_path)
    old = datetime.now(timezone.utc).timestamp() - 10_000
    monkeypatch.setattr(pipeline_status, "_pending_gates", lambda c: [(old, "moments", "k1")])
    monkeypatch.setattr(daemon, "installed_interval", lambda c: 600)
    monkeypatch.setattr("fanops.ledger.Ledger.load",
                        classmethod(lambda cls, c: (_ for _ in ()).throw(RuntimeError("isolate gate sensor"))))
    gate = next((c for c in doctor._operational_sensor_checks(cfg) if "stale agent gates" in c["label"]), None)
    assert gate is not None and gate["ok"] is False and gate.get("severity") == "fail"


def test_operational_sensor_warns_on_unknown_gate_age(tmp_path, monkeypatch):
    """R1b: missing opened_at → None epoch → Severity.UNKNOWN 'gate age unknown' (not silent green)."""
    from fanops import pipeline_status, daemon
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(pipeline_status, "_pending_gates", lambda c: [(None, "moments", "legacy")])
    monkeypatch.setattr(daemon, "installed_interval", lambda c: 600)
    monkeypatch.setattr("fanops.ledger.Ledger.load",
                        classmethod(lambda cls, c: (_ for _ in ()).throw(RuntimeError("isolate gate sensor"))))
    gate = next((c for c in doctor._operational_sensor_checks(cfg) if "stale agent gates" in c["label"]), None)
    assert gate is not None and gate["ok"] is False and gate.get("severity") == "unknown"
    assert "gate age unknown" in (gate.get("hint") or "")


def test_approval_backlog_is_info_note_only_not_a_warn(tmp_path, monkeypatch):
    """Approval backlog is EXPECTED (nothing auto-publishes) — it appears as an INFO note, never as a
    warn-tier check, because the human Review gate is deliberately kept."""
    from fanops.models import PostState
    cfg = Config(root=tmp_path)
    class _Led:
        def state_histogram(self, **k):
            return {PostState.awaiting_approval: 3}
    monkeypatch.setattr("fanops.ledger.Ledger.load", classmethod(lambda cls, c: _Led()))
    notes = doctor._doctor_notes(cfg)
    assert any("approval backlog" in n.lower() and "3" in n for n in notes)
    # ...and it is NOT emitted as an operational (warn) sensor.
    assert not any("approval" in c["label"].lower() for c in doctor._operational_sensor_checks(cfg))


def test_postiz_ondemand_missing_script_is_an_info_note(tmp_path, monkeypatch):
    """FANOPS_POSTIZ_ONDEMAND is a bootstrap-only path override. If set to a missing script, doctor emits
    a lean INFO note (a `fanops up` bring-up concern) — not a publish-path failure."""
    monkeypatch.setenv("FANOPS_POSTIZ_ONDEMAND", str(tmp_path / "nope" / "postiz-ondemand.sh"))
    notes = doctor._doctor_notes(Config(root=tmp_path))
    assert any("FANOPS_POSTIZ_ONDEMAND" in n and "does not exist" in n for n in notes)


def test_doctor_quota_check_omitted_without_graph(tmp_path):
    cfg = Config(root=tmp_path)
    assert doctor._graph_hashtag_quota_check(cfg) is None


def test_doctor_quota_not_exhausted_with_stale_graph_mute(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("META_GRAPH_TOKEN", "t")
    monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops.source_tags import GRAPH_TAG_CACHE_NAME
    p = cfg.control / GRAPH_TAG_CACHE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps({"tags": {}, "searches": [], "quota_exhausted_at": now}))
    row = doctor._graph_hashtag_quota_check(cfg)
    assert row is not None
    assert row["ok"] is True
    assert row.get("severity") != "warn"


def test_doctor_surfaces_hashtag_search_quota(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("META_GRAPH_TOKEN", "t")
    monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops.source_tags import GRAPH_TAG_CACHE_NAME
    p = cfg.control / GRAPH_TAG_CACHE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    searches = [{"tag": f"#t{i}", "at": now} for i in range(30)]
    p.write_text(json.dumps({"tags": {}, "searches": searches}))
    row = doctor._graph_hashtag_quota_check(cfg)
    assert row is not None
    assert row["severity"] == "warn"
    assert "30/30" in row["hint"]
    assert "2207034" in row["hint"]
