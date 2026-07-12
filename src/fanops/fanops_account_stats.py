"""U3: throttled per-handle IG follower snapshot — off the Studio render path, called from fanops run."""
from __future__ import annotations
import json
import time
from fanops.accounts import Accounts
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.log import get_logger
from fanops.meta_graph import account_overview


def refresh_account_stats_if_due(cfg: Config, *, max_age_s: int = 43200, get=None) -> dict:
    """Refresh account_stats.json at most once per max_age_s (default 12h). Iterates active IG handles,
    merges per-handle follower snapshots. FAIL-OPEN: never raises."""
    try:
        p = cfg.account_stats_path
        if p.exists() and (time.time() - p.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        store: dict = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                store = raw if isinstance(raw, dict) else {}
            except Exception as exc:
                get_logger(cfg)("account_stats", "-", "read_error", err=str(exc)[:120])
                store = {}
        updated = 0
        for a in Accounts.load(cfg).active():
            if not any(getattr(pl, "value", pl) == "instagram" for pl in a.platforms):
                continue
            snap = account_overview(cfg, a.handle, get=get)
            if snap:
                store[a.handle] = snap
                updated += 1
        cfg.account_stats_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(cfg.account_stats_path, store)
        return {"refreshed": True, "updated": updated, "total": len(store)}
    except Exception as exc:
        get_logger(cfg)("account_stats", "-", "refresh_error", err=str(exc)[:120])
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
