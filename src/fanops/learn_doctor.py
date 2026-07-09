# src/fanops/learn_doctor.py
"""F2 — read-only learning-loop field-shape doctor. Answers ONE question: does the LIVE Postiz
analytics field shape carry the signal the learning loop optimizes? The loop weights track._W, and the
live Postiz backend delivers the labels _POSTIZ_LABEL_MAP maps (likes/shares/comments/reach/saves/views);
`retention` is genuinely absent from the live label set — a known gap, NOT a doctor failure. So the
verdict gates ONLY on `reach` (mapped from the live `reach` label), the one weighted key M4's
reach-attribution consumes. Tri-state, so 0 posts is never a vacuous PASS:
  PASS    — sampled posts carry a reach signal (the reach label reconciles)
  FAIL    — sampled posts carry analytics labels but NONE yields `reach`
  NO-DATA — no shipped posts, or none with usable analytics yet
Genuinely read-only of the ledger: pulls analytics, never writes the ledger / flips a flag / calls
record_metrics. The CLI persists the verdict to its OWN sidecar (00_control/learn_doctor.json) so M4
gates on a machine-readable PASS — a SEPARATE gate from cutover.json/metrics_confirmed. The POSTIZ key
is sent as auth by PostizMetricsClient and never logged/echoed here (mirror its sentinel discipline)."""
from __future__ import annotations
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.log import get_logger
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.track import _W

# The ONE weighted key the verdict gates on: mapped from the live Postiz `reach` label and the field
# M4 reach-attribution reads. `retention` is absent from the live label set (reported, never gated).
_GATING_KEY = "reach"


def _sampled_submission_ids(led: Ledger) -> list[str]:
    # Shipped posts carry a submission id; sample both published AND analyzed (a post stays shippable
    # evidence after a learn pass advances it to analyzed) so the doctor still has data to inspect.
    return [p.submission_id for p in led.posts.values()
            if p.submission_id and p.state in (PostState.published, PostState.analyzed)]


def _default_fetch(led: Ledger, cfg: Config):
    # Lazy import keeps requests/postiz off the dryrun/core path (mirrors track._default_list_posts).
    from fanops.post.metrics import PostizMetricsClient
    return PostizMetricsClient(cfg, submission_ids=_sampled_submission_ids(led)).list_posts


def field_shape_report(led: Ledger, cfg: Config, *, window: str = "30d", list_posts=None) -> dict:
    """Pure read: pull sampled posts' live analytics and judge the `reach` field shape."""
    return _field_shape_report_core(led, cfg, window=window, list_posts=list_posts)


def _field_shape_report_core(led: Ledger, cfg: Config, *, window: str = "30d", list_posts=None) -> dict:
    """Pure read: pull sampled posts' live analytics and judge the `reach` field shape. `list_posts`
    is injectable for tests; None -> the per-post PostizMetricsClient over the shipped-post ids."""
    fetch = list_posts or _default_fetch(led, cfg)
    rows = fetch(window)
    posts_sampled = len(rows)
    labels_seen = sorted({lbl for r in rows for lbl in (r.get("_raw_labels") or [])})
    metric_keys = {k for r in rows for k in (r.get("metrics") or {})}
    mapped_lift_keys = _mapped_lift_keys()
    unmapped_weight_keys = sorted(k for k in _W if k not in mapped_lift_keys)   # retention (saves now maps)
    reach_present = _GATING_KEY in metric_keys
    if posts_sampled == 0 or not (labels_seen or metric_keys):
        verdict, detail = "NO-DATA", "no shipped posts with usable analytics yet — nothing to judge."
    elif reach_present:
        verdict, detail = "PASS", "the `reach` signal reconciles (the reach label is present)."
    else:
        verdict, detail = "FAIL", "`reach` absent from sampled analytics (the reach label did not reconcile)."
    return {"posts_sampled": posts_sampled, "labels_seen": labels_seen,
            "weight_keys": sorted(_W), "gating_key": _GATING_KEY, "reach_present": reach_present,
            "unmapped_weight_keys": unmapped_weight_keys, "verdict": verdict, "detail": detail}


def _mapped_lift_keys() -> set:
    # The lift keys the live Postiz backend can actually deliver (the label map's targets). Imported
    # lazily so the metrics module (requests) stays off the core path.
    from fanops.post.metrics import _POSTIZ_LABEL_MAP
    return set(_POSTIZ_LABEL_MAP.values())


def _persist_verdict(cfg: Config, report: dict) -> None:
    # XC-3: atomic like every other control file (controlio.write_json_atomic). A crash mid-write must leave
    # the PRIOR verdict, never a torn sidecar.
    # write_json_atomic serializes the dict (every value here is JSON-native), so no json.dumps.
    write_json_atomic(cfg.learn_doctor_path, report)


def cmd_learn_doctor(cfg: Config, *, list_posts=None) -> int:
    """`fanops learn doctor` — print the field-shape verdict and persist it for M4. Read-only; exits 0
    on every branch (a diagnostic never aborts a pipeline). On a non-postiz backend or missing key it
    prints guidance and returns without touching the network."""
    if cfg.poster_backend != "postiz" or not cfg.postiz_api_key:
        get_logger(cfg)("learn_doctor", "-", "missing_backend", level="warning",
                        hint="set FANOPS_POSTER=postiz and POSTIZ_API_KEY")
        return 0
    import requests
    from fanops.errors import PostizAuthError
    led = Ledger.load(cfg)                                # lock-free read; the doctor never mutates it
    try:
        report = field_shape_report(led, cfg, list_posts=list_posts)
    # Swallow ONLY documented transport failures (the Postiz client raises PostizAuthError on 401 and
    # RuntimeError on a 5xx/non-JSON body; requests/OSError on transport) — these are transient/diagnostic.
    # A genuine code bug (TypeError/KeyError/ImportError) is NOT caught here and surfaces as a traceback.
    except (PostizAuthError, RuntimeError, requests.RequestException, OSError) as e:  # key never echoed (class name only)
        get_logger(cfg)("learn_doctor", "-", "fetch_failed", level="warning", err=type(e).__name__,
                        detail="retry when Postiz analytics are reachable")
        return 0
    log = get_logger(cfg)
    log("learn_doctor", "-", "report", posts_sampled=report["posts_sampled"],
        labels_seen=report["labels_seen"] or "(none)", weight_keys=report["weight_keys"],
        unmapped_weight_keys=report["unmapped_weight_keys"], verdict=report["verdict"], detail=report["detail"])
    if report["verdict"] != "PASS":
        log("learn_doctor", "-", "not_validated", level="warning",
            detail="Do NOT enable variant_* / reach-attribution paths yet")
    _persist_verdict(cfg, report)
    return 0
