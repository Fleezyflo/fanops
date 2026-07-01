"""Live-cutover validation harness (roadmap Phase 1) — the ONE safe, reversible path to prove the
pipeline against a REAL Postiz backend, the lever that moves "proven effectiveness" off 2/10. Four
explicit, operator-driven steps (auth -> post -> metrics -> lift); each returns its result and the CLI
prints it and STOPS, so the human reads each step before deciding to proceed. NEVER reachable from
run/advance (no autonomous path imports this). Writes ONLY to 00_control/cutover.json, NEVER
ledger.json — the throwaway 2099-scheduled test post must never enter the real unit chain (never
ingested, never tracked, never amplified). 'Rollback' is: delete cutover.json (local) + delete the
2099 post in the Postiz dashboard (remote); neither touches production state. Each step dispatches to
cutover_postiz (the Postiz cutover body); a non-postiz backend fails closed with CutoverError."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.errors import CutoverError
from fanops.track import _W, lift_score

# Hardcoded, NOT operator-supplied, so the probe post can NEVER go live: the operator deletes it in
# the Postiz dashboard long before 2099. No code path schedules it sooner.
CUTOVER_SCHEDULE = "2099-01-01T00:00:00Z"
CONFIRM_FLAG = "--i-understand-this-posts-to-a-real-account"


def _load_state(cfg: Config) -> dict:
    p = cfg.cutover_path
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}                                            # corrupt scratch file -> start clean, never crash

def _save_state(cfg: Config, patch: dict) -> None:
    # XC-3: atomic (tmp + os.replace) like every other control file (controlio.write_json_atomic). A crash
    # mid-write must leave the PRIOR valid cutover.json, never a torn one — a torn file reads fail-closed
    # (validation_gate.learning_validated -> False) and would silently RE-FREEZE learning. write_json_atomic
    # serializes the dict itself (every persisted value here is JSON-native), so drop the manual json.dumps.
    write_json_atomic(cfg.cutover_path, {**_load_state(cfg), **patch})


def reconcile_fields(metrics: dict) -> dict:
    """The load-bearing output: diff the live metrics row's numeric keys against track._W (the keys
    lift_score actually weights). Tells the operator EXACTLY which weighted fields the live API
    returns (`scored`), which live fields _W ignores (`present_unweighted` — candidate new weights),
    and which weighted fields never came back (`weighted_absent` — dead weights to drop/re-tune via
    tuning.json before trusting the learning loop)."""
    present = {k for k, v in metrics.items() if isinstance(v, (int, float))}
    weighted = set(_W)
    return {"scored": sorted(present & weighted),
            "present_unweighted": sorted(present - weighted),
            "weighted_absent": sorted(weighted - present)}


def cutover_auth(cfg: Config, *, get=None) -> dict:
    """Step 1: prove the key authenticates (read-only, no write). Postiz dispatches to cutover_postiz;
    any non-postiz backend fails closed (no other backend)."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_auth(cfg)
    raise CutoverError(f"cutover supports the postiz backend only (got {cfg.poster_backend!r}).")


def cutover_post(cfg: Config, account_id: str, *, confirmed: bool, post=None) -> dict:
    """Step 2: publish ONE real post to a THROWAWAY account at the 2099 schedule. Refuses unless the
    operator passed the explicit confirm flag. Records the submission_id to cutover.json (NOT the
    ledger). Postiz dispatches to cutover_postiz (account_id carries the operator-selected integration
    id); any non-postiz backend fails closed (no other backend supported)."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_post(cfg, account_id, confirmed=confirmed, post=post)
    raise CutoverError(f"cutover supports the postiz backend only (got {cfg.poster_backend!r}).")


def cutover_metrics(cfg: Config, submission_id: str, *, list_posts=None) -> dict:
    """Step 3: pull the real metrics row for the cutover post and reconcile its fields against
    track._W. Saves the raw row + reconciliation to cutover.json and stamps metrics_confirmed=True
    (the flag Phase 2's validation gate keys off — the learning stack stays frozen until this runs).
    Postiz dispatches to cutover_postiz (M2's per-post client + raw-label reconcile); any non-postiz
    backend fails closed (no other backend supported)."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_metrics(cfg, submission_id, list_posts=list_posts)
    raise CutoverError(f"cutover supports the postiz backend only (got {cfg.poster_backend!r}).")


def cutover_lift(cfg: Config, submission_id: str) -> dict:
    """Step 4: compute one REAL lift_score from the captured row, end-to-end through the same
    lift_score + tuning weights the learning loop uses — proving the loop computes on real data."""
    metrics = _load_state(cfg).get("metrics_row")
    if not metrics:
        raise CutoverError("no captured metrics row — run `fanops cutover metrics <submission_id>` first.")
    weights = cfg.tuning().get("lift_weights")
    return {"lift_score": lift_score(metrics, weights), "metrics": metrics,
            "weights": weights or "default _W"}
