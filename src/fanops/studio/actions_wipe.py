"""ledger-rebuild M4 (MOL-33): the Studio action layer for the fall-away wipe. Bridges the Studio surface
to fanops.ledger_wipe. Two operator actions:
  - preview_wipe: a READ-ONLY would-remove report (id-set + per-entity counts) shown BEFORE the confirm.
  - confirm_wipe: gated on a TYPED CONFIRM (the operator types CONFIRM_WORD — the Go-Live-confirm shape,
    strengthened to a typed word because this is destructive). It takes a mandatory pre-wipe SNAPSHOT
    (MOL-32), VERIFIES it restorable, then runs ledger_wipe.execute_wipe (which itself re-checks the
    snapshot + confirm in code). The snapshot path is returned so the surface reports the rollback point.

MACHINERY ONLY — the operator triggers this through the Studio confirm; nothing here runs automatically.
The typed word is a UI gate; execute_wipe is the code gate (both must hold). fan-accounts-repost-freely:
this removes UNBACKED cache, never adds supersede/dedupe; no new auto-publish path."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops import ledger_wipe
from fanops.log import get_logger
from fanops.studio.actions_common import ActionResult

# The exact word the operator must type to confirm the destructive wipe (mirrors the Go-Live confirm gate,
# strengthened to a typed word for a destructive one-shot). Case-insensitive match, trimmed.
CONFIRM_WORD = "REMOVE"


def preview_wipe(cfg: Config) -> ActionResult:
    """Read-only preview: compute the would-remove id-set + per-entity counts. NEVER mutates the ledger
    (a pure ledger_wipe.wipe_preview over a lock-free load). Fail-closed on a torn ledger (a clean error,
    not a 500) — the operator must see the ledger is unreadable, not a silent empty preview."""
    try:
        led = Ledger.load(cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"ledger unreadable: {str(exc)[:160]}. Run `fanops doctor` first.")
    return ActionResult(ok=True, detail=ledger_wipe.wipe_preview(led))


def confirm_wipe(cfg: Config, *, typed: str, token: str = "") -> ActionResult:
    """Execute the wipe — GATED on (a) the typed confirm word AND (b) a server-verified preview token
    (MOL-71). The token proves the operator saw the read-only preview of EXACTLY this would-remove set: it is
    recomputed here against a FRESH preview, so a confirm that never previewed (empty token) or previewed a
    since-changed ledger (stale token) is REFUSED before any snapshot or removal. Then snapshot-first (MOL-32):
    a verified pre-wipe snapshot, then the code-gated execute_wipe. Additive only — the typed-word/snapshot
    gates are untouched. Every outcome is logged (audit) — never a silent removal."""
    log = get_logger(cfg)
    if (typed or "").strip().upper() != CONFIRM_WORD:
        log("ledger_wipe", "-", "wipe_refused_bad_confirm")
        return ActionResult(ok=False, error=f'type "{CONFIRM_WORD}" to confirm — nothing was removed.')
    # preview-ran gate (MOL-71): the confirm token must match a FRESH preview of the current would-remove set.
    fresh = preview_wipe(cfg)
    if not fresh.ok:
        return fresh
    expected = fresh.detail.get("token", "")
    if not (token or "").strip():
        log("ledger_wipe", "-", "wipe_refused_no_preview")
        return ActionResult(ok=False, error="run the preview first — nothing was removed.")
    if (token or "").strip() != expected:
        log("ledger_wipe", "-", "wipe_refused_stale_preview")
        return ActionResult(ok=False, error="the preview is stale (the ledger changed) — refresh the preview and try again. Nothing was removed.")
    # snapshot FIRST (mandatory, verified restorable) — the wipe cannot proceed without it.
    try:
        snap = Ledger.snapshot(cfg)
    except Exception as exc:
        log("ledger_wipe", "-", "wipe_refused_snapshot_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"could not take the mandatory pre-wipe snapshot: {str(exc)[:160]}")
    if not ledger_wipe.snapshot_is_restorable(snap):
        log("ledger_wipe", "-", "wipe_refused_snapshot_unverified")
        return ActionResult(ok=False, error="the pre-wipe snapshot did not verify restorable — refused.")
    try:
        result = ledger_wipe.execute_wipe(cfg, confirmed=True, snapshot_path=snap)
    except Exception as exc:
        log("ledger_wipe", "-", "wipe_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"wipe failed (snapshot preserved at {snap}): {str(exc)[:160]}")
    log("ledger_wipe", "-", "wipe_done", snapshot=str(snap), **result["removed"])
    return ActionResult(ok=True, detail={"snapshot": str(snap), "removed": result["removed"]})
