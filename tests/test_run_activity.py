# tests/test_run_activity.py — S01: run-activity heartbeat via .run.lock advisory body.
import fcntl
import json
import logging
import os

from fanops.config import Config
from fanops.models import Source, SourceState
from fanops.ledger import Ledger
from fanops.pipeline_run import note_stage, run_status_line, _lock_path


def _hold_lock(cfg, body: dict | None = None):
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    if body is not None:
        os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(body).encode())
    return fd


def test_note_stage_appears_in_run_status_line(tmp_path):
    cfg = Config(root=tmp_path)
    fd = _hold_lock(cfg, {"pid": 4242, "started": "2020-01-01T00:00:00Z"})
    try:
        note_stage(cfg, "produce", "src-1")
        line = run_status_line(cfg)
        assert "stage=produce:src-1" in line
        assert "stage_age=" in line
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_released_lease_shows_idle_despite_stale_body(tmp_path):
    cfg = Config(root=tmp_path)
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({"pid": 4242, "started": "2020-01-01T00:00:00Z",
                              "stage": "produce", "unit": "src-1", "stage_started": "2020-01-01T00:00:00Z"}))
    assert run_status_line(cfg) == "run=idle"


def test_garbage_body_under_lease_short_form(tmp_path):
    cfg = Config(root=tmp_path)
    fd = _hold_lock(cfg)
    os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, b"{not json")
    try:
        line = run_status_line(cfg)
        assert line.startswith("run=")
        assert "stage=" not in line
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_pipeline_status_stamps_matching_backlog_row(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src-1", source_path="x.mp4", state=SourceState.catalogued))
    fd = _hold_lock(cfg, {"pid": 4242, "started": "2020-01-01T00:00:00Z"})
    try:
        note_stage(cfg, "produce", "src-1")
        st = views.pipeline_status(cfg)
        assert st.get("run_chip") == "produce:src-1"
        rows = {r["id"]: r for r in st["backlog_rows"]}
        assert rows["src-1"].get("active_stage") == "produce"
        assert "stage_age" in rows["src-1"]
        assert "active_stage" not in {k for r in st["backlog_rows"] if r["id"] != "src-1" for k in r}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_note_stage_io_error_fail_open(tmp_path, mocker, caplog):
    cfg = Config(root=tmp_path)
    fd = _hold_lock(cfg, {"pid": 4242, "started": "2020-01-01T00:00:00Z"})
    mocker.patch("fanops.pipeline_run.os.write", side_effect=OSError("disk full"))
    try:
        with caplog.at_level(logging.WARNING, logger="fanops.pipeline_run"):
            note_stage(cfg, "produce", "src-1")
            note_stage(cfg, "produce", "src-1")
        assert len(caplog.records) == 1
        assert "note_stage fail-open" in caplog.records[0].message
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_daemon_health_includes_run_line_when_held(tmp_path, monkeypatch):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    fd = _hold_lock(cfg, {"pid": 4242, "started": "2020-01-01T00:00:00Z"})
    try:
        note_stage(cfg, "moments", "-")
        monkeypatch.setattr("fanops.daemon.status", lambda c, **k: {"verdict": "alive", "loaded": True,
                            "pid": 1, "last_exit": 0, "heartbeat_age_s": 5})
        monkeypatch.setattr("fanops.daemon.installed_interval", lambda c: 600)
        monkeypatch.setattr("fanops.pipeline.pending_gate_count", lambda c: 0)
        monkeypatch.setattr("fanops.daemon.sibling_agents_status", lambda: [])
        dh = views.daemon_health(cfg)
        assert dh is not None
        assert dh.get("run_line") is not None
        assert "stage=moments" in dh["run_line"]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
