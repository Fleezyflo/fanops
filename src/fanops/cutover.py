"""Live-cutover validation harness (roadmap Phase 1) — the ONE safe, reversible path to prove the
pipeline against REAL Blotato, the lever that moves "proven effectiveness" off 2/10. Four explicit,
operator-driven steps (auth -> post -> metrics -> lift); each returns its result and the CLI prints
it and STOPS, so the human reads each step before deciding to proceed. NEVER reachable from
run/advance (no autonomous path imports this). Writes ONLY to 00_control/cutover.json, NEVER
ledger.json — the throwaway 2099-scheduled test post must never enter the real unit chain (never
ingested, never tracked, never amplified). 'Rollback' is: delete cutover.json (local) + delete the
2099 post in the Blotato dashboard (remote); neither touches production state."""
from __future__ import annotations
import json
import requests
from fanops.config import Config
from fanops.errors import CutoverError
from fanops.post.blotato_base import BASE_URL
from fanops.post.blotato_rest import _extract_submission_id
from fanops.post.metrics import _raise_for_auth
from fanops.post.payload import build_blotato_payload, default_target_fields
from fanops.track import _W, _default_list_posts, lift_score

# Hardcoded, NOT operator-supplied, so the probe post can NEVER go live: the operator deletes it in
# the Blotato dashboard long before 2099. No code path schedules it sooner.
CUTOVER_SCHEDULE = "2099-01-01T00:00:00Z"
CONFIRM_FLAG = "--i-understand-this-posts-to-a-real-account"


def _require_key(cfg: Config) -> str:
    key = cfg.blotato_api_key
    if not key:
        raise CutoverError("BLOTATO_API_KEY is not set — cutover needs a real key to prove the live path.")
    return key

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
    p = cfg.cutover_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**_load_state(cfg), **patch}, indent=2, default=str))


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


def build_cutover_payload(account_id: str) -> dict:
    # Twitter, text-only (no media required) at the 2099 schedule — the minimal real post that proves
    # auth + the POST contract without needing an uploaded clip.
    return build_blotato_payload(account_id=account_id, platform="twitter",
                                 text="fanops cutover probe — delete me", media_urls=[],
                                 scheduled_time=CUTOVER_SCHEDULE,
                                 extra_target=default_target_fields("twitter"))


def cutover_auth(cfg: Config, *, get=None) -> dict:
    """Step 1: prove the key authenticates. GET /posts (read-only, no write). 401 -> typed
    BlotatoAuthError (body redacted). Returns {ok, status_code}. Postiz backend dispatches to
    cutover_postiz (M3); the Blotato body below is byte-unchanged."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_auth(cfg)
    key = _require_key(cfg)
    g = get or requests.get
    resp = g(f"{BASE_URL}/posts", headers={"blotato-api-key": key}, params={"window": "1d"}, timeout=30)
    _raise_for_auth(resp)
    return {"ok": resp.status_code in (200, 201), "status_code": resp.status_code}


def cutover_post(cfg: Config, account_id: str, *, confirmed: bool, post=None) -> dict:
    """Step 2: publish ONE real post to a THROWAWAY account at the 2099 schedule. Refuses unless
    (a) the backend is live — dryrun posts nothing, wrong tool — and (b) the operator passed the
    explicit confirm flag. Records the submission_id to cutover.json (NOT the ledger). Postiz backend
    dispatches to cutover_postiz (account_id carries the operator-selected integration id); the Blotato
    body below is byte-unchanged."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_post(cfg, account_id, confirmed=confirmed, post=post)
    if cfg.poster_backend == "dryrun":
        raise CutoverError("cutover proves the LIVE path; FANOPS_POSTER=dryrun posts nothing — set FANOPS_POSTER=rest.")
    payload = build_cutover_payload(account_id)
    if not confirmed:
        raise CutoverError(f"refusing to POST to a real account — re-run with {CONFIRM_FLAG} once {account_id} is a THROWAWAY account.")
    key = _require_key(cfg)
    poster = post or requests.post
    resp = poster(f"{BASE_URL}/posts",
                  headers={"blotato-api-key": key, "Content-Type": "application/json"},
                  json=payload, timeout=30)
    _raise_for_auth(resp)
    if resp.status_code not in (200, 201):
        # Body WITHHELD (no-echo posture — matches cutover_postiz + the metrics/blotato_rest
        # redactions): a failure body could echo the key/PII into stdout/cutover.json.
        raise CutoverError(f"blotato post {resp.status_code}: response body withheld")
    body = resp.json()
    sub = _extract_submission_id(body)
    _save_state(cfg, {"submission_id": sub, "account_id": account_id,
                      "scheduled_time": CUTOVER_SCHEDULE, "post_response_keys": sorted(body.keys())})
    return {"submission_id": sub, "status_code": resp.status_code, "post_response_keys": sorted(body.keys())}


def cutover_metrics(cfg: Config, submission_id: str, *, list_posts=None) -> dict:
    """Step 3: pull the real metrics row for the cutover post and reconcile its fields against
    track._W. Saves the raw row + reconciliation to cutover.json and stamps metrics_confirmed=True
    (the flag Phase 2's validation gate keys off — the learning stack stays frozen until this runs).
    Postiz backend dispatches to cutover_postiz (M2's per-post client + raw-label reconcile); the
    Blotato body below is byte-unchanged."""
    if cfg.poster_backend == "postiz":
        from fanops import cutover_postiz; return cutover_postiz.postiz_metrics(cfg, submission_id, list_posts=list_posts)
    fetch = list_posts or _default_list_posts(cfg)
    row = next((r for r in fetch("30d") if r.get("postSubmissionId") == submission_id), None)
    if row is None:
        raise CutoverError(f"no metrics row for submission_id={submission_id} yet — Blotato may lag; retry later.")
    metrics = row.get("metrics", {})
    rec = reconcile_fields(metrics)
    _save_state(cfg, {"metrics_row": metrics, "reconciliation": rec, "metrics_confirmed": True})
    return {"metrics": metrics, "reconciliation": rec}


def cutover_lift(cfg: Config, submission_id: str) -> dict:
    """Step 4: compute one REAL lift_score from the captured row, end-to-end through the same
    lift_score + tuning weights the learning loop uses — proving the loop computes on real data."""
    metrics = _load_state(cfg).get("metrics_row")
    if not metrics:
        raise CutoverError("no captured metrics row — run `fanops cutover metrics <submission_id>` first.")
    weights = cfg.tuning().get("lift_weights")
    return {"lift_score": lift_score(metrics, weights), "metrics": metrics,
            "weights": weights or "default _W"}
