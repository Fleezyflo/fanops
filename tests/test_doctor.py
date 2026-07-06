# tests/test_doctor.py — Phase 3b: `fanops doctor` read-only first-run health screen. Asserts only
# on env-controlled checks (key/claude/notes), never host-dependent toolchain presence.
import json
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


def test_doctor_claude_check_only_when_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert not any("claude" in c["label"].lower() for c in rep["checks"])
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    rep2 = doctor.doctor_report(Config(root=tmp_path))
    assert any("claude" in c["label"].lower() for c in rep2["checks"])

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
    assert c is not None and c["ok"] is False and "Connect Postiz" in c["hint"]

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


# --- T3: per-account ig_user_id required for active IG accounts (demote global to bootstrap-only) ---

def _ig_accts_cfg(tmp_path, rows):
    """Config with ACTIVE IG accounts from `rows` = [(handle, ig_user_id_or_None), ...]. Each carries the
    instagram platform + an id so accounts.validate() is happy (the ig-id check is orthogonal to mapping)."""
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    accts = [{"handle": h, "platforms": ["instagram"], "status": "active", "access": "postiz",
              "integrations": {"instagram": "ig_map_" + h.lstrip("@")}, "backends": {"instagram": "postiz"},
              "ig_user_id": iid} for h, iid in rows]
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))
    return cfg

def _igid_check(rep):
    return next((c for c in rep["checks"] if "ig_user_id" in c["label"].lower() or "ig user id" in c["label"].lower()), None)


def test_doctor_requires_distinct_ig_user_id_per_active_account(tmp_path, monkeypatch):
    # (i) CURRENT PROD STATE: 3 active IG accts, all ig_user_id=None, one global id -> all 3 borrow the
    # global (markmakmouly's) -> FAIL, naming the two SILENT borrowers (perca.late + cisumwolfhom).
    monkeypatch.setenv("META_IG_USER_ID", "17841400000000001")   # the single global id (markmakmouly's, historically)
    cfg = _ig_accts_cfg(tmp_path, [("markmakmouly", None), ("@perca.late", None), ("cisumwolfhom", None)])
    c = _igid_check(doctor.doctor_report(cfg))
    assert c is not None and c["ok"] is False
    assert "perca.late" in c["hint"] and "cisumwolfhom" in c["hint"]

    # (ii) 3 DISTINCT non-null ids -> every account is verified against its OWN media -> OK.
    cfg2 = _ig_accts_cfg(tmp_path, [("markmakmouly", "111"), ("@perca.late", "222"), ("cisumwolfhom", "333")])
    c2 = _igid_check(doctor.doctor_report(cfg2))
    assert c2 is not None and c2["ok"] is True

    # (iii) CORRUPT accounts.json -> FAIL CLOSED (unknown != silent pass), no crash.
    cfg3 = Config(root=tmp_path)
    cfg3.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg3.accounts_path.write_text("{ this is not json ]")
    c3 = _igid_check(doctor.doctor_report(cfg3))         # must not raise
    assert c3 is not None and c3["ok"] is False

    # (iv) a SINGLE active IG account legitimately using the global -> NOT a violation (no false positive).
    cfg4 = _ig_accts_cfg(tmp_path, [("markmakmouly", None)])
    c4 = _igid_check(doctor.doctor_report(cfg4))
    assert c4 is not None and c4["ok"] is True

    # (v) explicit DUPLICATE: two active handles resolve to the SAME non-None id -> FAIL naming both.
    cfg5 = _ig_accts_cfg(tmp_path, [("markmakmouly", "999"), ("@perca.late", "999"), ("cisumwolfhom", "333")])
    c5 = _igid_check(doctor.doctor_report(cfg5))
    assert c5 is not None and c5["ok"] is False
    assert "markmakmouly" in c5["hint"] and "perca.late" in c5["hint"]


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

    # (a) FAR FUTURE (90 days) -> ok, no warn, no fail.
    c = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(int(now + 90 * 86400))))
    assert c is not None and c["ok"] is True and not c.get("warn")

    # (b) INSIDE the lead window (<=10 days; use 5 days) -> WARN (ok stays True so it never blocks, warn set).
    c2 = _tokencheck(doctor.doctor_report(cfg, get=_debug_token_getter(int(now + 5 * 86400))))
    assert c2 is not None and c2.get("warn") is True and "expir" in (c2["hint"] + c2.get("warn_hint", "")).lower()

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
    """Append a valid run.log heartbeat line whose leading ISO ts is `age_seconds` in the past (mirrors
    log.py's TAB layout so daemon._heartbeat_age_s parses it)."""
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.log_path, "a", encoding="utf-8") as fh:
        fh.write(f"{ts}\theartbeat\t-\tok\theartbeat={ts} published_in_run=0\n")

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
