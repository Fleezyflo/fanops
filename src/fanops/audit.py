"""R3: append-only operator audit trail for state-changing Studio/CLI actions.

The ledger records OUTCOME state; this module records the ACTION that caused it. One
JSON line per state-changing call to 00_control/studio_audit.log, so an operator can
reconstruct WHO/WHAT/WHEN even after the ledger has moved on (the 5 ghost-publishes
of 2026-06-29 had no such trail — pinned in R3 PRD).

Contract: write_audit NEVER raises. The action must complete even if the audit write
fails (audit is observability, never a blocker). Owner-only chmod 0o600 — the audit
carries action context, not secrets, but the convention matches log.py."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Sequence
from fanops.config import Config


def write_audit(cfg: Config, action: str, post_ids: Sequence[str], *,
                reason: str, **kw) -> None:
    """Append ONE JSON line per state-changing action to 00_control/studio_audit.log.

    Schema: {ts, action, post_ids, reason, **kw}. kw lets callers carry per-action
    context (suggested_iso, rescheduled, url, handle, etc) without growing this API.

    NEVER raises: the caller's action MUST complete even if the audit write fails
    (disk full, dir-where-file-should-be, perms). The log is a tail-and-grep surface,
    not load-bearing for correctness."""
    try:
        cfg.control.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": str(action),
            "post_ids": list(post_ids or []),
            "reason": str(reason),
        }
        for k, v in (kw or {}).items():
            entry[str(k)] = v
        path = cfg.control / "studio_audit.log"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def read_audit_tail(cfg: Config, n: int = 20) -> list[str]:
    """Return the last N lines of the audit log (raw JSON strings; the caller may
    json.loads each). Missing log -> empty list. Read-only, never raises."""
    path = cfg.control / "studio_audit.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return lines[-n:] if n > 0 else lines
    except Exception:
        return []
