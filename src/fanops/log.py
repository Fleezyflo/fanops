"""Minimal structured run logger -> 07_reports/run.log + stderr. Every stage emits
(stage, unit_id, outcome, **fields) so a silent mass-failure (e.g. 401/429 across the queue)
is visible (FIX F51). No external deps."""
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone
from fanops.config import Config

def get_logger(cfg: Config):
    cfg.reports.mkdir(parents=True, exist_ok=True)
    try:                                   # owner-only at rest (audit): run.log carries per-stage diagnostics
        os.close(os.open(cfg.log_path, os.O_CREAT | os.O_WRONLY, 0o600))   # create 0600 if absent (no truncate)
        os.chmod(cfg.log_path, 0o600)      # tighten a pre-existing world-readable log too
    except OSError: pass                   # best-effort — never block logging on a perms quirk
    def _san(v) -> str:
        # L1 (audit): collapse the structural chars of the TAB-delimited layout so a field value (often a
        # remote API response body / exception text) can't forge a log line or shift the positional columns.
        return str(v).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    def log(stage: str, unit_id: str, outcome: str, **fields) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        extra = " ".join(f"{_san(k)}={_san(v)}" for k, v in fields.items())
        line = f"{ts}\t{_san(stage)}\t{_san(unit_id)}\t{_san(outcome)}\t{extra}".rstrip()
        # Append-only diagnostics: O_APPEND makes each write atomic at EOF, so overlapping
        # `advance` re-runs interleave lines but never tear them. (run.log is not authoritative
        # state — the ledger's temp-file+replace+lock is; do not "upgrade" this to a shared handle.)
        with open(cfg.log_path, "a") as fh:
            fh.write(line + "\n")
        print(line, file=sys.stderr)
    return log
