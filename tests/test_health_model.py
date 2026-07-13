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


def test_daemon_progress_alive_when_fresh_stage(tmp_path):
    import fcntl, os
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import note_stage, _lock_path
    cfg = Config(root=tmp_path)
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


def test_daemon_progress_stuck_when_stage_age_above_ceiling(tmp_path):
    import fcntl, json, os
    from datetime import datetime, timezone, timedelta
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    from fanops.pipeline_run import _lock_path
    cfg = Config(root=tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": 1, "started": old, "stage": "transcribe", "unit": "src-1",
                             "stage_started": old}).encode())
    try:
        alive, line, snap = daemon_progress(cfg)
        assert alive is False and snap is not None
        assert line is not None and "transcribe" in line
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


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
