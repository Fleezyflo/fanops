"""Minimal structured run logger -> 07_reports/run.log + stderr. Every stage emits
(stage, unit_id, outcome, **fields) so a silent mass-failure (e.g. 401/429 across the queue)
is visible (FIX F51). No external deps."""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from fanops.config import Config

def get_logger(cfg: Config):
    cfg.reports.mkdir(parents=True, exist_ok=True)
    def log(stage: str, unit_id: str, outcome: str, **fields) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{ts}\t{stage}\t{unit_id}\t{outcome}\t{extra}".rstrip()
        # Append-only diagnostics: O_APPEND makes each write atomic at EOF, so overlapping
        # `advance` re-runs interleave lines but never tear them. (run.log is not authoritative
        # state — the ledger's temp-file+replace+lock is; do not "upgrade" this to a shared handle.)
        with open(cfg.log_path, "a") as fh:
            fh.write(line + "\n")
        print(line, file=sys.stderr)
    return log
