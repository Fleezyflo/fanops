# src/fanops/batches.py — Account-First Studio: mint a named, account-targeted ingest Batch.
# Pure on an already-loaded Ledger (the caller holds the transaction, mirroring crosspost_clips); the id
# is content-addressed on (name, now_iso) so a re-submit is idempotent. now_iso is INJECTED (no clock in
# the ledger), mirroring the approve_post now_iso precedent.
from __future__ import annotations
from fanops.models import Batch, batch_id


def create_batch(led, *, name: str, target_accounts, now_iso: str) -> Batch:
    """Validate + normalize at the boundary, mint a content-addressed id, idempotent-add to `led`.
    `name` is required non-blank (stripped → canonical, so the id is stable; ValueError otherwise).
    `target_accounts` is normalized to a stripped, blank-dropped, deduped HANDLE list preserving
    first-occurrence order; [] is kept as the ALL-ACTIVE-ACCOUNTS sentinel. Returns the Batch."""
    name = (name or "").strip()
    if not name: raise ValueError("batch name must be non-blank")
    seen, tgt = set(), []
    for h in target_accounts or []:
        h = (h or "").strip()
        if h and h not in seen: seen.add(h); tgt.append(h)
    b = Batch(id=batch_id(name, now_iso), name=name, target_accounts=tgt, created_at=now_iso)
    led.add_batch(b)
    return b
