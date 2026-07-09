import json
from fanops.config import Config
from fanops.log import get_logger

def test_logger_writes_line(tmp_path):
    cfg = Config(root=tmp_path)
    log = get_logger(cfg)
    log("transcribe", "src_1", "ok", extra="turbo")
    rec = json.loads(cfg.log_path.read_text().splitlines()[0])
    assert "T" in rec["ts"]
    assert rec["stage"] == "transcribe" and rec["unit_id"] == "src_1" and rec["outcome"] == "ok"
    assert rec["level"] == "info" and rec["extra"] == "turbo"


def test_logger_sanitizes_newlines_and_tabs(tmp_path):
    cfg = Config(root=tmp_path)
    get_logger(cfg)("reconcile", "p_1", "poll-error", err="line1\nFORGED\tcol\rsplit")
    lines = cfg.log_path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["stage"] == "reconcile" and rec["unit_id"] == "p_1" and rec["outcome"] == "poll-error"
    assert "FORGED" in rec["err"] and "\n" not in lines[0] and "\r" not in lines[0]
