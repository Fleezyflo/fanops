# src/fanops/fanops_hashtags.py
"""Hashtag measurement cache writer (00_control/hashtags.json) — thin facade.

Implementation lives in `hashtag_scrape_policy` (cooldown/budget), `ig_safari_shell`
(Safari tick slot), and `hashtag_refresh` (refresh/remesure orchestration). All public
symbols remain importable from this module for backward compatibility."""
from __future__ import annotations

from fanops.config import Config
from fanops.log import get_logger
from fanops.controlio import write_json_atomic  # noqa: F401 — tests patch via fh.write_json_atomic

# Backward-compat re-exports (tests / ig_web_scrape / doctor import via fanops_hashtags)
from fanops.hashtag_scrape_policy import (  # noqa: F401
    _AUTH_DEATH_DELAY_S,
    _AUTH_DEATH_NAMES,
    _AUTH_DEATH_REASON,
    _AUTH_HOLD_REASONS,
    _CHECKPOINT_DELAY_S,
    _COOLDOWN_DELAYS_S,
    _SCRAPE_COTAG_ENQUEUE_CAP,
    _SCRAPE_DAY_BUDGET,
    _SCRAPE_TRY_CAP,
    _account_rec,
    _block_view_for_rec,
    _charge_scrape_user,
    _clear_cooldown,
    _cooldown_delay_s,
    _cooldown_path,
    _day_room,
    _day_used,
    _freeze_for,
    _healthy_scrape_users,
    _is_auth_hold,
    _is_frozen,
    _load_cooldown_blob,
    _outage_level,
    _persist_cooldown,
    _pick_healthy_scrape_user,
    _read_active_cooldown,
    _scrub_expired_accounts,
    _user_attempt_room,
    _utc_day,
    scrape_user_blocked,
)
from fanops.ig_safari_shell import (  # noqa: F401
    mark_safari_tick_slot,
    reset_safari_tick_slot,
    safari_tick_slot_claimed,
)
from fanops.hashtag_refresh import (  # noqa: F401
    _MEASURE_MAX_AGE_DAYS,
    _REFRESH_CADENCE_S,
    _VOLUME_MAX_AGE_DAYS,
    _pass_lease,
    _refresh_pass,
    _remesure_sidecar,
    _scrape_cotag_enqueue_cap,
    _scrape_parallel,
    _scrape_try_cap,
    cmd_hashtags_refresh,
    cmd_hashtags_scrape_login,
    refresh_store,
    refresh_store_if_due,
)


def cmd_hashtags_discover(cfg: Config) -> int:
    """`fanops hashtags discover` — each native source's lock. READ-ONLY, ZERO NETWORK."""
    from fanops.ledger import Ledger
    from fanops.source_tags import load_source_tag_locks
    log = get_logger(cfg)
    try:
        led = Ledger.load(cfg)
    except Exception as exc:                                  # noqa: BLE001 — a report must never break a schedule
        get_logger(cfg)("hashtags", "-", "discover_skipped", level="warning", err=str(exc)[:160]); return 0
    table = load_source_tag_locks(cfg)
    n = 0
    for src in led.sources.values():
        if getattr(src, "origin_kind", "native") == "third_party":
            continue
        sid = str(getattr(src, "id", "") or "")
        if not sid:
            continue
        rec = table.get(sid) if isinstance(table.get(sid), dict) else {}
        lock = [t for t in (rec.get("lock") or []) if isinstance(t, str)]
        at = rec.get("researched_at")
        if isinstance(at, str) and at.strip():
            state = "empty" if not lock else "ready"
        elif rec:
            state = "in_progress"
        else:
            state = "missing"
        log("hashtags", sid, "lock", state=state, n=len(lock), tags=", ".join(lock[:12]))
        n += 1
    if not n:
        log("hashtags", "-", "no_sources", level="warning",
            hint="ingest a native source first"); return 0
    log("hashtags", "-", "discover_done", sources=n,
        hint="posted tags = lock intersect model picks, cap 4")
    return 0
