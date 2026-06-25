# src/fanops/studio/actions_casting.py — RF1 Task 6: the operator cast OVERRIDE. Add or remove a single
# (moment, account) from that account's durable AccountSelection, method=operator (a human decision SUPERSEDES
# the llm/migrated provenance). The sum-type stays honest: removing an account's LAST moment DROPS the record
# (back to "no selection" -> the gate denies it on a cast source), never an illegal empty `operator` row.
from __future__ import annotations
from datetime import datetime, timezone
from fanops.ledger import Ledger
from fanops.models import AccountSelection, SelectionMethod, account_selection_id
from fanops.timeutil import iso_z
from fanops.studio.actions_common import ActionResult


def cast_add(cfg, source_id: str, account: str, moment_id: str) -> ActionResult:
    """Add `moment_id` to `account`'s selection for `source_id` (creating it if absent), method=operator.
    Rejects a moment that isn't a decided child of this source (a stale/hand-crafted POST must not mint a
    selection for a foreign moment). Idempotent on a re-add (sorted-set union)."""
    try:
        with Ledger.transaction(cfg) as led:
            m = led.moments.get(moment_id)
            if m is None or m.parent_id != source_id:
                return ActionResult.failure(f"unknown moment {moment_id} for source {source_id}")
            sel = led.account_selection_for(source_id, account)
            ids = sorted(set(sel.moment_ids) | {moment_id}) if sel else [moment_id]
            led.add_account_selection(AccountSelection(
                id=account_selection_id(source_id, account), source_id=source_id, account=account,
                moment_ids=ids, method=SelectionMethod.operator,
                batch_id=(sel.batch_id if sel else None), created_at=iso_z(datetime.now(timezone.utc))))
    except Exception as exc:
        return ActionResult.failure(f"cast add failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "added": True})


def cast_remove(cfg, source_id: str, account: str, moment_id: str) -> ActionResult:
    """Remove `moment_id` from `account`'s selection. Removing the LAST moment DROPS the whole record (so the
    gate denies the account on this cast source) rather than leaving an illegal empty `operator` row. A missing
    selection is a clean no-op (nothing to remove)."""
    try:
        with Ledger.transaction(cfg) as led:
            sel = led.account_selection_for(source_id, account)
            if sel is None:
                return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "noop": True})
            ids = sorted(set(sel.moment_ids) - {moment_id})
            if ids:
                led.add_account_selection(AccountSelection(
                    id=account_selection_id(source_id, account), source_id=source_id, account=account,
                    moment_ids=ids, method=SelectionMethod.operator,
                    batch_id=sel.batch_id, created_at=iso_z(datetime.now(timezone.utc))))
            else:
                led.drop_account_selection(source_id, account)
    except Exception as exc:
        return ActionResult.failure(f"cast remove failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "account": account, "moment": moment_id, "removed": True})
