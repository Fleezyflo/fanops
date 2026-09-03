"""Stateless Studio request/render helpers: arg parsers + chip context builders.

Lifted out of app.py so route-group modules can import them directly without
pulling in create_app. They close over flask.request + module imports only,
never cfg/app — lazy current_app imports inside individual parsers preserve the
original Flask import order and keep this module cycle-free."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import request

from fanops.config import Config
from fanops.studio import views
from fanops.timeutil import local_input_to_utc_z

logger = logging.getLogger(__name__)


def _bounded(cfg: Config, candidate) -> Path | None:
    """Require a servable path to resolve INSIDE cfg.base (the FanOps data tree). Ledger paths are
    trusted in normal operation, but a hand-edited/corrupt ledger must not turn the localhost
    cockpit into an arbitrary-file server (stage-5/6 audit) — anything else is a 404, not a serve."""
    if not candidate:
        return None
    p = Path(candidate).resolve()
    return p if p.is_relative_to(cfg.base.resolve()) else None


def _time_arg() -> str:
    # The datetime-local control submits naive LOCAL; convert to canonical UTC before the action sees it.
    # A Z/offset value passes through normalized; garbage passes through so reschedule_post raises 'bad time'.
    from flask import current_app
    cfg = current_app.config.get("FANOPS_CFG")
    return local_input_to_utc_z(request.form.get("new_time", ""), cfg=cfg)


def _offset_arg() -> int:
    # The grid show-more offset from ?offset=. A garbage/negative value -> 0 (paginate clamps too),
    # so a hand-typed URL can never 500 the grid.
    try:
        return max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _month_arg() -> tuple[int, int]:
    """U7: ?month=YYYY-MM for the Schedule calendar; absent -> current operator-local month."""
    raw = (request.args.get("month") or "").strip()
    if raw:
        try:
            y, m = raw.split("-", 1)
            return int(y), int(m)
        except (ValueError, TypeError):
            pass
    zone = timezone.utc
    try:
        from fanops.timeutil import _operator_zone
        from flask import current_app
        cfg = current_app.config.get("FANOPS_CFG")
        if cfg:
            zone = _operator_zone(cfg) or timezone.utc
    except Exception as exc:
        from fanops.errors import fail_open
        with fail_open("studio.app_request._month_arg"):
            raise exc
    today = datetime.now(zone).date()
    return today.year, today.month


def _account_all_arg():
    # S07: ?account=all is the explicit mixed worklist (None scope, but not bare-entry picker/auto-focus).
    return (request.args.get("account") or "").strip().lower() == "all"


def _account_arg():
    # P5: the per-account filter from ?account=. A blank/absent param -> None (the unfiltered "All"
    # view); read from request.args, so an htmx POST that carries account= in its action URL re-applies
    # the same scope after a mutation (R1). Never raises; an unknown handle simply matches zero rows.
    # @-agnostic: operators may type @handle while accounts.json/ledger use bare handles.
    # S07: account=all -> None (mixed view) BEFORE resolve_account_handle ("all" is not a handle).
    v = (request.args.get("account") or "").strip()
    if not v or v.lower() == "all":
        return None
    try:
        from flask import current_app
        cfg = current_app.config.get("FANOPS_CFG")
        if cfg:
            return views.resolve_account_handle(v, cfg)
    except Exception:
        logger.warning("account handle resolution failed (fail-open, using raw handle)", exc_info=True)
    return v


def _batch_arg():
    # Face 4 follow-up (B2): drill into ONE batch from ?batch=<Batch.id> (content-addressed id, NOT name —
    # names aren't unique). Mirrors _account_arg: blank/absent -> None (unfiltered); read from request.args so
    # an htmx POST carrying batch= in its action URL re-applies the same scope after a mutation (R1). Review-
    # local (NOT injected into the cross-tab nav like account) — a batch id is meaningless on other tabs.
    v = (request.args.get("batch") or "").strip()
    return v or None


def _delivery_arg():
    v = (request.args.get("delivery") or "").strip().lower()
    return v or None


def _failure_arg():
    v = (request.args.get("failure") or "").strip().lower()
    return v or None


def _compact_arg() -> bool:
    # M3c: the dense, video-less Review list mode from ?compact=. Read from request.args so it rides the
    # action/pagination URLs (templates carry compact=1) AND the htmx POST URL — so a mutation re-render
    # stays compact (R1). Truthy-words only; absent/blank/anything else -> False (the full video view).
    # Phase 4: ?compact=ultra is ALSO truthy here (so the compact code paths still fire) AND flips _ultra_arg.
    v = (request.args.get("compact") or "").strip().lower()
    return v in ("1", "true", "yes", "on", "ultra")


def _ultra_arg() -> bool:
    # Phase 4: the TRUE ultra-compact (zero-<video>, DOM-light) pivot mode from ?compact=ultra. The win at
    # 150 surfaces is the ELEMENT COUNT (one row per surface, no <video>, no poster fetch), not just preload.
    # Read from request.args so it rides the action/pagination URLs (R1). Anything but the exact word -> False.
    return (request.args.get("compact") or "").strip().lower() == "ultra"


def _source_arg():
    # Phase 4: the per-source filter from ?source=<Source.id>. Mirrors _account_arg/_batch_arg — blank/absent
    # -> None (unfiltered); read from request.args so an htmx POST carrying source= re-applies scope (R1).
    # Keyed on the STABLE source id (NOT the basename — two sources can share a filename); never raises.
    v = (request.args.get("source") or "").strip()
    return v or None


def _state_arg():
    # Phase 4: the per-state filter from ?state=. VALIDATED against the legal set (views._STATE_TO_BUCKET) —
    # an unknown word maps to None (the unfiltered view), so a hand-typed URL never 500s. Blank/absent -> None.
    v = (request.args.get("state") or "").strip().lower()
    return v if v in views._STATE_TO_BUCKET else None


def _focus_arg() -> bool:
    return (request.args.get("focus") or "").strip().lower() in ("1", "true", "yes", "on")


def _focus_idx_arg() -> int:
    try:
        return max(0, int(request.args.get("fi", 0)))
    except (TypeError, ValueError):
        return 0


def _view_arg():
    # Slice 2: the Review view mode from ?view=. 'list' -> the legacy moment-first cards; 'account' -> the
    # account-first PIVOT (one account's run as a flat list); 'lanes' (RF6) -> the account-first per-account
    # lanes; 'matrix' (or absent/unknown) -> the DEFAULT moment×account matrix. Read from request.args so it
    # rides the action/pagination URLs (R1).
    v = (request.args.get("view") or "").strip().lower()
    return v if v in ("account", "list", "matrix", "lanes") else None   # RF6: 'lanes' = the account-first per-account lanes


def _with_active(counts, active):
    # The chip UNIVERSE = the accounts present in the (unfiltered) list, PLUS the active filter itself, so
    # an account whose last item just left the list still shows its (active) chip — the filter stays
    # visible + recoverable ("No work for @a — clear the filter") instead of silently vanishing.
    accts = set(counts)
    if active:
        accts.add(active)
    return sorted(accts)


def _row_chips(rows, route, active):
    # Chip context for a row/dict-based surface: the distinct account UNIVERSE + per-account counts,
    # derived from the POSTS in this list (never accounts.json — a retired account's history stays
    # filterable). Splatted into render_template; the _account_filter.html include reads these.
    counts = Counter((r["account"] if isinstance(r, dict) else r.account) for r in rows)
    return {"chip_accounts": _with_active(counts, active), "chip_counts": dict(counts),
            "chip_route": route, "chip_total": len(rows), "active": active}


def _card_chips(cards, active):
    # Chip context for Review (cards have no scalar account — collect surface accounts; a fan-out card
    # contributes to each surface's account). chip_total counts cards, the count map counts surfaces.
    counts = Counter(s.account for c in cards for s in c.surfaces)
    return {"chip_accounts": _with_active(counts, active), "chip_counts": dict(counts),
            "chip_route": "review", "chip_total": len(cards), "active": active}
