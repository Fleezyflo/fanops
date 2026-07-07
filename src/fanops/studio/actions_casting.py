# src/fanops/studio/actions_casting.py — P13 (MOL-152/153): the operator cast OVERRIDE, ported OFF the deleted
# durable AccountSelection ONTO Moment.affinities (the single gate input after the casting teardown). cast_add
# appends a handle to a decided moment's affinities; cast_remove pops it. A moment is single-owner by CONVENTION
# (the picker attributes ONE owner), but the operator override may deliberately CO-OWN — add a 2nd handle here
# (the human escape hatch; the len<=1 validator applies to the PICKER, not this override). Removing the last
# owner leaves affinities==[] -> the moment fans to all (the persona-blind path), never a stuck record.
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.models import validate_account_handle
from fanops.studio.actions_common import ActionResult


def cast_add(cfg, source_id: str, account: str, moment_id: str) -> ActionResult:
    """Add `account` to `moment_id`'s affinities (the cast set). Rejects a moment that isn't a child of this
    source (a stale/hand-crafted POST must not cast a foreign moment). Idempotent on a re-add (sorted-set union).
    The operator may co-own a moment already owned by another handle (deliberate override)."""
    try:
        account = validate_account_handle(account)
    except ValueError:
        return ActionResult.failure(f"invalid account {account!r}")
    try:
        with Ledger.transaction(cfg) as led:
            m = led.moments.get(moment_id)
            if m is None or m.parent_id != source_id:
                return ActionResult.failure(f"unknown moment {moment_id} for source {source_id}")
            led.moments[moment_id].affinities = sorted(set(m.affinities or []) | {account})
    except Exception as exc:
        return ActionResult.failure(f"cast add failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "added": True})


def cast_remove(cfg, source_id: str, account: str, moment_id: str) -> ActionResult:
    """Remove `account` from `moment_id`'s affinities. Emptying the set leaves affinities==[] (the moment fans
    to all — the persona-blind path), never an illegal stuck record. A missing/foreign moment is a clean no-op."""
    try:
        account = validate_account_handle(account)
    except ValueError:
        return ActionResult.failure(f"invalid account {account!r}")
    try:
        with Ledger.transaction(cfg) as led:
            m = led.moments.get(moment_id)
            if m is None or m.parent_id != source_id:
                return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "noop": True})
            led.moments[moment_id].affinities = sorted(set(m.affinities or []) - {account})
    except Exception as exc:
        return ActionResult.failure(f"cast remove failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "removed": True})
