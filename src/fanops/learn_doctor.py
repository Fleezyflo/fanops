# src/fanops/learn_doctor.py
"""F2 — read-only learning-loop field-shape doctor CLI. Delegates field-shape logic to field_shape;
this module wires the `fanops learn doctor` command and re-exports field_shape_report for tests."""
from __future__ import annotations

from fanops.config import Config
from fanops.field_shape import _field_shape_report_core
from fanops.log import get_logger
from fanops.ledger import Ledger

# Backward-compatible re-export — health_model and other callers may import from learn_doctor.
__all__ = ["field_shape_report", "cmd_learn_doctor", "_field_shape_report_core"]


def field_shape_report(led: Ledger, cfg: Config, *, window: str = "30d", list_posts=None) -> dict:
    """Pure read: pull sampled posts' live analytics and judge the `reach` field shape."""
    return _field_shape_report_core(led, cfg, window=window, list_posts=list_posts)


def cmd_learn_doctor(cfg: Config, *, list_posts=None) -> int:
    """`fanops learn doctor` — print the field-shape verdict. Read-only; exits 0 on every branch
    (a diagnostic never aborts a pipeline). On a non-postiz backend or missing key it prints guidance
    and returns without touching the network."""
    if not cfg.backend_has_creds("postiz"):
        get_logger(cfg)("learn_doctor", "-", "missing_backend", level="warning",
                        hint="connect Postiz in Studio Go-Live (POSTIZ_API_KEY) and route a channel to postiz")
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
    return 0
