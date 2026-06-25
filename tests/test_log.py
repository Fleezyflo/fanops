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


def test_logger_sanitizes_newlines_and_tabs(tmp_path):
    # L1 (audit): a field value carrying \n/\r/\t (e.g. a remote API error body) must NOT forge extra log lines
    # or shift the load-bearing TAB columns — the structural chars are collapsed to spaces so the line stays
    # single and positionally parseable.
    cfg = Config(root=tmp_path)
    get_logger(cfg)("reconcile", "p_1", "poll-error", err="line1\nFORGED\tcol\rsplit")
    lines = cfg.log_path.read_text().splitlines()
    assert len(lines) == 1                                  # one physical line — no injected rows
    cols = lines[0].split("\t")
    assert len(cols) == 5                                   # exactly the 5 positional columns — no shifted tab
    assert cols[1] == "reconcile" and cols[2] == "p_1" and cols[3] == "poll-error"
    assert "FORGED" in cols[4] and "\n" not in lines[0] and "\r" not in lines[0]
