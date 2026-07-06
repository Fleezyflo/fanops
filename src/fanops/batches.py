# src/fanops/batches.py — Account-First Studio: mint a named, account-targeted ingest Batch.
# Pure on an already-loaded Ledger (the caller holds the transaction, mirroring crosspost_clips); the id
# is content-addressed on (name, now_iso) so a re-submit is idempotent. now_iso is INJECTED (no clock in
# the ledger), mirroring the approve_post now_iso precedent.
from __future__ import annotations
from datetime import datetime, timezone
from fanops.models import Batch, batch_id, validate_account_handle
from fanops.timeutil import iso_z


def _resolver_now_utc() -> datetime:
    """Resolver clock — extracted so tests can pin the date deterministically (mirrors the explicit
    now_iso-injection style of create_batch; the only difference is that the auto-resolver picks its
    own clock because every caller of ingest_drops already has, in spirit, accepted the current date)."""
    return datetime.now(timezone.utc)


def resolve_or_mint_drop_batch(led) -> Batch:
    """Return the day's drop-batch — mint it once, reuse it for every later ingest pass that day. The
    name is `drop-YYYY-MM-DD` so the id is stable across passes (content-addressed on (name, now_iso),
    same now_iso => same id; we use the day's midnight as that anchor). target_accounts=[] preserves
    today's byte-identical fan-to-all default for an unbatched ingest — the operator's named batches
    (Studio "Add video") still override via an explicit batch_id passed to ingest_drops."""
    now = _resolver_now_utc()
    day = now.date()                                  # YYYY-MM-DD anchor — one batch per UTC day
    anchor_iso = iso_z(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    name = f"drop-{day.isoformat()}"
    bid = batch_id(name, anchor_iso)                  # same day -> same id -> add_batch is idempotent (setdefault)
    existing = led.get_batch(bid)
    if existing is not None: return existing
    return create_batch(led, name=name, target_accounts=[], now_iso=anchor_iso)


def create_batch(led, *, name: str, target_accounts, now_iso: str, active_handles: set[str] | None = None,
                 burn_subs: bool | None = None) -> Batch:
    """Validate + normalize at the boundary, mint a content-addressed id, idempotent-add to `led`.
    `name` is required non-blank (stripped → canonical, so the id is stable; ValueError otherwise).
    `target_accounts` is normalized to a stripped, blank-dropped, deduped HANDLE list preserving
    first-occurrence order; [] is kept as the ALL-ACTIVE-ACCOUNTS sentinel. Returns the Batch.
    `active_handles` (optional): when supplied, a non-empty target intersecting NO active handle is an
    ADVISORY zero-result batch — surfaced via Batch.error_reason (state stays open; the batch still mints,
    the operator may re-activate the handle later), NOT a raised error. None (default) => no validation =>
    byte-identical to today. The [] ALL-sentinel is NEVER flagged (guard `and tgt`)."""
    name = (name or "").strip()
    if not name: raise ValueError("batch name must be non-blank")
    seen, tgt = set(), []
    for h in target_accounts or []:
        try:
            h = validate_account_handle(h)
        except ValueError:
            h = (h or "").strip()
        if h and h not in seen: seen.add(h); tgt.append(h)
    err = None
    if active_handles is not None and tgt and not (set(tgt) & active_handles):
        err = "targets no active account: " + ", ".join(sorted(tgt))   # advisory, mirrors ingest.py origin/batch_conflict
    b = Batch(id=batch_id(name, now_iso), name=name, target_accounts=tgt, created_at=now_iso, error_reason=err,
              burn_subs=burn_subs)
    led.add_batch(b)
    return b
