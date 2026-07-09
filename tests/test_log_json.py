"""MOL-356: JSON run.log stream + level on get_logger (heartbeat contract preserved)."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from fanops.config import Config
from fanops.log import get_logger
from fanops import daemon


def _read_record(cfg) -> dict:
    line = cfg.log_path.read_text().splitlines()[0]
    return json.loads(line)


def test_logger_emits_parseable_json_with_level(tmp_path):
    cfg = Config(root=tmp_path)
    get_logger(cfg)("transcribe", "src_1", "ok", extra="turbo")
    rec = _read_record(cfg)
    assert rec["stage"] == "transcribe" and rec["unit_id"] == "src_1" and rec["outcome"] == "ok"
    assert rec["level"] == "info"                                 # default when callers omit level
    assert rec["extra"] == "turbo"
    assert "T" in rec["ts"]


def test_logger_level_param(tmp_path):
    cfg = Config(root=tmp_path)
    get_logger(cfg)("publish", "p1", "error", level="error", err="nope")
    rec = _read_record(cfg)
    assert rec["level"] == "error" and rec["err"] == "nope"


def test_logger_sanitizes_newlines_and_tabs(tmp_path):
    cfg = Config(root=tmp_path)
    get_logger(cfg)("reconcile", "p_1", "poll-error", err="line1\nFORGED\tcol\rsplit")
    lines = cfg.log_path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["stage"] == "reconcile" and rec["unit_id"] == "p_1" and rec["outcome"] == "poll-error"
    assert "FORGED" in rec["err"] and "\n" not in lines[0] and "\r" not in lines[0]


def test_logger_uses_o_append(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    opened: list[int] = []
    real_open = os.open
    def track_open(path, flags, *a, **k):
        opened.append(flags)
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", track_open)
    get_logger(cfg)("ping", "-", "ok")
    assert any(f & os.O_APPEND for f in opened)


def test_logger_sets_0600_perms(tmp_path):
    cfg = Config(root=tmp_path)
    get_logger(cfg)("x", "-", "ok")
    assert oct(cfg.log_path.stat().st_mode & 0o777) == "0o600"


def test_heartbeat_contract_for_daemon_age(tmp_path):
    cfg = Config(root=tmp_path)
    ts = datetime.now(timezone.utc).isoformat()
    get_logger(cfg)("heartbeat", "-", "ok", heartbeat=ts, published_in_run=0)
    age = daemon._heartbeat_age_s(cfg)
    assert age is not None and age < 5
