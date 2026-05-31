from fanops.config import Config
from fanops.log import get_logger

def test_logger_writes_line(tmp_path):
    cfg = Config(root=tmp_path)
    log = get_logger(cfg)
    log("transcribe", "src_1", "ok", extra="turbo")
    text = cfg.log_path.read_text()
    assert "transcribe" in text and "src_1" in text and "ok" in text
