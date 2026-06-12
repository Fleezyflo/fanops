from fanops.config import Config
from fanops.log import get_logger

def test_logger_writes_line(tmp_path):
    cfg = Config(root=tmp_path)
    log = get_logger(cfg)
    log("transcribe", "src_1", "ok", extra="turbo")
    line = cfg.log_path.read_text().splitlines()[0]
    # The TAB-delimited `ts\tstage\tunit\toutcome\textra` format is LOAD-BEARING: reconcile's
    # audit-trail check parses columns positionally and F51 mass-failure triage greps them.
    # Substring checks let a format regression (tab->space, column reorder) ride through green
    # (stage-6 audit) — assert the actual columns.
    cols = line.split("\t")
    assert len(cols) == 5
    assert "T" in cols[0]                                   # ISO timestamp leads
    assert cols[1] == "transcribe" and cols[2] == "src_1" and cols[3] == "ok"
    assert cols[4] == "extra=turbo"
