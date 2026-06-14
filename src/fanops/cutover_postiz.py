# src/fanops/cutover_postiz.py — the Postiz half of the live-cutover validation harness (M3).
"""Mirrors the four cutover steps for a FANOPS_POSTER=postiz deployment; dispatched from cutover.py by
backend so the Blotato path stays byte-identical. Writes ONLY 00_control/cutover.json (NEVER the ledger
— the 2099-scheduled throwaway probe must never enter the unit chain). The POSTIZ_API_KEY is never
logged, echoed, or returned (only an `ok` bool); a 401 raises PostizAuthError with the body withheld.
cutover.py symbols (_save_state/reconcile_fields/CUTOVER_SCHEDULE) are lazy-imported inside the
functions to avoid a module-load cycle (cutover.py lazy-imports this module from its dispatch branches)."""
from __future__ import annotations
import requests
from fanops.config import Config
from fanops.errors import CutoverError, PostizAuthError
from fanops.post import postiz
from fanops.post.postiz import _base, _key, _PUBLIC, build_postiz_payload, _extract_postiz_id


def _require_postiz(cfg: Config) -> str:
    # Operator-refusal (CutoverError -> CLI exit 2), NOT PostizAuthError: a missing key is a config
    # problem to fix, not a live 401. A real mid-step 401 still raises PostizAuthError (cli's AuthError arm).
    key = cfg.postiz_api_key
    if not key:
        raise CutoverError("POSTIZ_API_KEY is not set — cutover needs a real key to prove the live path.")
    return key


def postiz_auth(cfg: Config) -> dict:
    """Step 1: prove the Postiz key authenticates (read-only integrations probe). postiz_check_auth
    returns True / raises PostizAuthError on 401 / False on any other failure — surfaced as {ok}."""
    _require_postiz(cfg)
    return {"ok": bool(postiz.postiz_check_auth(cfg)), "backend": "postiz", "status_code": 200}


def postiz_post(cfg: Config, integration_id: str, *, confirmed: bool, post=None) -> dict:
    """Step 2: publish ONE real throwaway post to the OPERATOR-SELECTED integration at the 2099 schedule,
    capture the Postiz post id, write ONLY cutover.json. Refuses dryrun + unless confirmed + unless the id
    is one of the operator's mapped integrations. Platform is DERIVED from the chosen integration (Postiz
    is integration-level; the payload settings.__type must match the channel, so it is NOT hardcoded)."""
    from fanops.cutover import CUTOVER_SCHEDULE, CONFIRM_FLAG, _save_state
    if cfg.poster_backend == "dryrun":
        raise CutoverError("cutover proves the LIVE path; FANOPS_POSTER=dryrun posts nothing — GO LIVE on Postiz first.")
    if not confirmed:
        raise CutoverError(f"refusing to POST to a real account — confirm the selected channel is a THROWAWAY ({CONFIRM_FLAG}).")
    _require_postiz(cfg)
    integration = next((i for i in postiz.postiz_list_integrations(cfg) if i.id == integration_id), None)
    if integration is None:
        raise CutoverError(f"unknown postiz integration id {integration_id!r} — pick one of your mapped channels.")
    payload = build_postiz_payload(integration_id=integration_id, platform=integration.platform,
                                   content="fanops cutover probe — delete me", media_urls=[],
                                   scheduled_time=CUTOVER_SCHEDULE)
    poster = post or requests.post
    resp = poster(f"{_base(cfg)}{_PUBLIC}/posts",
                  headers={"Authorization": _key(cfg), "Content-Type": "application/json"},
                  json=payload, timeout=30)
    if resp.status_code == 401:
        raise PostizAuthError("Postiz 401 on cutover post — check POSTIZ_API_KEY (response body withheld)")
    if resp.status_code not in (200, 201):
        raise CutoverError(f"postiz post failed ({resp.status_code}) — check your Postiz instance (response body withheld).")
    try:
        body = resp.json()
    except ValueError:
        raise CutoverError(f"postiz post returned {resp.status_code} but a non-JSON body — cannot capture the post id.")
    sub = _extract_postiz_id(body)
    if not sub:
        raise CutoverError("postiz 2xx but no recognizable post id in the response — cannot track the cutover post.")
    _save_state(cfg, {"submission_id": sub, "integration_id": integration_id, "platform": integration.platform,
                      "scheduled_time": CUTOVER_SCHEDULE, "backend": "postiz",
                      "post_response_keys": sorted(body.keys()) if isinstance(body, dict) else []})
    return {"submission_id": sub, "status_code": resp.status_code, "integration_id": integration_id}


def postiz_metrics(cfg: Config, submission_id: str, *, list_posts=None) -> dict:
    """Step 3: pull the cutover post's REAL metrics (M2's per-post PostizMetricsClient on this one id),
    reconcile the row's mapped fields against track._W, and write metrics_confirmed=True PLUS the
    CONFIRMED FIELD MAP — the raw Postiz labels (from the M2 row, NO self-fetch), the documented
    label→lift map M2 used, and the reconciliation — so the operator sees the exact divergence. A
    missing row reads as 'retry later' (Postiz analytics lag), not a hard failure."""
    from fanops.cutover import reconcile_fields, _save_state
    from fanops.post.metrics import PostizMetricsClient, _POSTIZ_LABEL_MAP
    fetch = list_posts or PostizMetricsClient(cfg, submission_ids=[submission_id]).list_posts
    row = next((r for r in fetch("30d") if r.get("postSubmissionId") == submission_id), None)
    if row is None:
        raise CutoverError(f"no metrics row for submission_id={submission_id} yet — Postiz analytics may lag; retry later.")
    metrics = row.get("metrics", {})
    rec = reconcile_fields(metrics)
    labels = row.get("_raw_labels", [])
    _save_state(cfg, {"metrics_row": metrics, "reconciliation": rec, "postiz_labels": labels,
                      "label_map": dict(_POSTIZ_LABEL_MAP), "metrics_confirmed": True, "backend": "postiz"})
    return {"metrics": metrics, "reconciliation": rec, "postiz_labels": labels}
