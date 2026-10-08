"""MOL-298: unified health model — one owner, thin views."""
from fanops.config import Config
from fanops.health_model import HealthReport, build_health_report, dep_health_list, postiz_dep_health
from fanops import health


def test_health_report_composes_checks_deps_and_field_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    (tmp_path / ".env").write_text("POSTIZ_URL=http://localhost:4007/api\nPOSTIZ_API_KEY=k\nFANOPS_POSTER=postiz\n")
    cfg = Config(root=tmp_path)
    rep = build_health_report(cfg, postiz_probe=lambda c: type("H", (), {"healthy": True, "status_code": 200, "hint": ""})())
    assert isinstance(rep, HealthReport)
    assert rep.checks and rep.notes
    assert [d.name for d in rep.deps] == ["docker", "postiz", "zernio"]
    assert rep.field_shape is not None
    assert rep.field_shape["verdict"] == "NO-DATA"


def test_system_health_matches_dep_health_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(health, "dep_health_list", lambda c, **kw: dep_health_list(c))
    assert health.system_health(cfg) == dep_health_list(cfg)


def test_postiz_health_uses_unified_probe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    def probe(c):
        seen.append(1)
        return type("H", (), {"healthy": True, "status_code": 200, "hint": ""})()
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg2 = Config(root=tmp_path)
    h = postiz_dep_health(cfg2, probe=probe)
    assert seen and h.ok is True


def test_doctor_report_includes_deps_key(tmp_path, monkeypatch):
    from fanops.doctor import doctor_report
    monkeypatch.chdir(tmp_path)
    rep = doctor_report(Config(root=tmp_path))
    assert "checks" in rep and "notes" in rep
    assert "deps" in rep


def test_daemon_progress_absent_when_no_lease(tmp_path):
    from fanops.health_model import daemon_progress
    cfg = Config(root=tmp_path)
    alive, line, snap = daemon_progress(cfg)
    assert alive is False and line is None and snap is None


def _write_log_line(cfg, *, stage="stage", ts=None, outcome="ok"):
    """Append one run.log line at `ts` (any stage) — the activity signal daemon_progress reads."""
    import json
    from datetime import datetime, timezone
    cfg.reports.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now(timezone.utc).isoformat()
    rec = {"ts": ts, "level": "info", "stage": stage, "unit_id": "-", "outcome": outcome}
    with cfg.log_path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_daemon_progress_alive_when_fresh_stage(tmp_path):
    # ALIVE = a stage is held AND the log is FRESH (still emitting). The mid-pass line names the stage.
    import fcntl, os
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import note_stage, _lock_path
    cfg = Config(root=tmp_path)
    _write_log_line(cfg, stage="llm")                       # fresh run.log line == the pump is emitting
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        note_stage(cfg, "transcribe", "src-1")
        alive, line, snap = daemon_progress(cfg)
        assert alive is True and snap is not None
        assert line is not None and "mid-pass: transcribe" in line and "src-1" in line
        assert _STAGE_HANG_CEILING_S == 3600
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_daemon_progress_alive_when_stage_old_but_log_fresh(tmp_path):
    # THE DEVIATION PROOF: a long-running stage (stage_age >> ceiling) that is STILL LOGGING is ALIVE,
    # not wedged. Wedged is LOG SILENCE, never stage_age — a big transcribe legitimately runs >1h.
    import fcntl, json, os
    from datetime import datetime, timezone, timedelta
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import _lock_path
    cfg = Config(root=tmp_path)
    _write_log_line(cfg, stage="llm")                       # fresh activity NOW
    old = (datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": 1, "started": old, "stage": "transcribe", "unit": "src-1",
                             "stage_started": old}).encode())   # stage_age > ceiling
    try:
        alive, line, snap = daemon_progress(cfg)
        assert alive is True and snap is not None               # long-but-logging != wedged
        assert line is not None and "mid-pass: transcribe" in line
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_daemon_progress_wedged_when_stage_held_and_log_silent(tmp_path):
    # WEDGED = a stage IS held AND the newest run.log line is SILENT past the ceiling.
    import fcntl, json, os
    from datetime import datetime, timezone, timedelta
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import _lock_path
    cfg = Config(root=tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 120))
    _write_log_line(cfg, stage="transcribe", ts=old.isoformat())   # newest log line is SILENT > ceiling
    old_s = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": 1, "started": old_s, "stage": "transcribe", "unit": "src-1",
                             "stage_started": old_s}).encode())
    try:
        alive, line, snap = daemon_progress(cfg)
        assert alive is False and snap is not None
        assert line is not None and "transcribe" in line and "SILENT" in line
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_daemon_progress_halted_line_does_not_refresh_alive_window(tmp_path):
    # A fresh outcome=halted line must not extend the alive window out to the ceiling.
    from datetime import datetime, timezone, timedelta
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    cfg = Config(root=tmp_path)
    old = datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 30)
    _write_log_line(cfg, stage="llm", ts=old.isoformat())
    _write_log_line(cfg, stage="run", outcome="halted")
    alive, line, snap = daemon_progress(cfg)
    assert alive is False and line is None and snap is None


def test_daemon_progress_halted_line_alone_is_not_alive(tmp_path):
    from fanops.health_model import daemon_progress
    cfg = Config(root=tmp_path)
    _write_log_line(cfg, stage="run", outcome="halted")
    alive, line, snap = daemon_progress(cfg)
    assert alive is False and line is None and snap is None


def test_daemon_progress_halt_does_not_hide_fresh_real_activity(tmp_path):
    from fanops.health_model import daemon_progress
    cfg = Config(root=tmp_path)
    _write_log_line(cfg, stage="llm")
    _write_log_line(cfg, stage="run", outcome="halted")
    alive, line, snap = daemon_progress(cfg)
    assert alive is True and snap is None
    assert line is not None and line.startswith("active:")


def test_heartbeat_stale_shape_unchanged(tmp_path, monkeypatch):
    from fanops.health_model import heartbeat_stale
    from fanops import daemon
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon, "_heartbeat_age_s", lambda c: 42.5)
    age, stale, iv = heartbeat_stale(cfg, interval=600)
    assert age == 42.5 and stale is False and iv == 600
    monkeypatch.setattr(daemon, "_heartbeat_age_s", lambda c: 350.0)
    age2, stale2, iv2 = heartbeat_stale(cfg, interval=100)
    assert stale2 is True and iv2 == 100


def _zernio_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("ZERNIO_API_URL", "http://zernio.test/v1")
    return Config(root=tmp_path)


def _http(status_code):
    return type("R", (), {"status_code": status_code})()


def test_zernio_completed_get_is_not_up(tmp_path, monkeypatch):
    from fanops.health_model import HealthReport, project_prometheus_health, zernio_dep_health
    seen = []

    def fake_get(url, timeout=3):
        seen.append((url, timeout))
        return _http(200)

    monkeypatch.setattr("requests.get", fake_get)
    h = zernio_dep_health(_zernio_cfg(tmp_path, monkeypatch))
    assert seen == [("http://zernio.test/v1", 3)]
    assert h.name == "zernio" and h.ok is False and h.detail == "HTTP 200"
    body = "\n".join(project_prometheus_health(
        HealthReport(checks=[], notes=[], deps=[h]), heartbeat=(None, True, 600)))
    assert 'fanops_dep_up{dep="zernio"} 0' in body


def test_zernio_401_and_5xx_are_down(tmp_path, monkeypatch):
    from fanops.health_model import HealthReport, project_prometheus_health, zernio_dep_health
    cfg = _zernio_cfg(tmp_path, monkeypatch)
    for code in (401, 500, 503):
        monkeypatch.setattr("requests.get", lambda *a, code=code, **k: _http(code))
        h = zernio_dep_health(cfg)
        assert h.ok is False and h.detail == f"HTTP {code}"
        body = "\n".join(project_prometheus_health(
            HealthReport(checks=[], notes=[], deps=[h]), heartbeat=(None, True, 600)))
        assert 'fanops_dep_up{dep="zernio"} 0' in body


def test_zernio_transport_error_is_unreachable(tmp_path, monkeypatch):
    import requests
    from fanops.health_model import zernio_dep_health

    def boom(*_a, **_k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("requests.get", boom)
    h = zernio_dep_health(_zernio_cfg(tmp_path, monkeypatch))
    assert h.ok is False and h.detail == "unreachable"


def test_zernio_skipped_when_not_configured(tmp_path, monkeypatch):
    from fanops.health_model import zernio_dep_health
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    monkeypatch.setattr("fanops.secret_provider.get_secret", lambda *_a, **_k: None)
    monkeypatch.setattr("requests.get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")))
    h = zernio_dep_health(Config(root=tmp_path))
    assert h.ok is True and h.detail == "skipped (not configured)"
