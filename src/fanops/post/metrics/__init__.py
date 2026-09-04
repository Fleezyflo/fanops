"""Real metrics-read clients (FIX F05 — v1 had none). list_posts(window) returns rows keyed by
postSubmissionId with a metrics dict. Postiz and Zernio per-post reads live in postiz_read / zernio_read;
IG Graph insights in common. Re-exports preserve `from fanops.post.metrics import X` backward compat."""
from __future__ import annotations

import requests

from fanops.post.metrics.common import (
    _json_or_raise,
    _safe,
    poster_fail_reason,
)
from fanops.post.metrics.postiz_read import (
    PostizMetricsClient,
    PostizStatusClient,
    _POSTIZ_LABEL_MAP,
    _POSTIZ_STATE_MAP,
    _latest_total,
    _map_analytics,
)
from fanops.post.metrics.zernio_read import (
    ZernioMetricsClient,
    ZernioStatusClient,
    _TIKTOK_OEMBED,
    _ZERNIO_LABEL_MAP,
    _ZERNIO_STATE_MAP,
    _ZERNIO_WRAPS,
    _extract_zernio_state,
    _fetch_zernio_analytics_body,
    _handle_key,
    _map_zernio_analytics,
    _oembed_author_key,
    _zernio_analytics_payload,
    _zernio_num,
    _zernio_platform_metric_payload,
    _zernio_platform_rows,
    _zernio_raw_labels,
    verify_tiktok_permalink,
    zernio_analytics_url_and_username,
    zernio_permalink_from_analytics,
    zernio_reported_tiktok_username,
)

__all__ = [
    "PostizMetricsClient",
    "PostizStatusClient",
    "ZernioMetricsClient",
    "ZernioStatusClient",
    "poster_fail_reason",
    "verify_tiktok_permalink",
    "zernio_analytics_url_and_username",
    "zernio_permalink_from_analytics",
    "zernio_reported_tiktok_username",
    # test / internal re-exports (backward compat)
    "_POSTIZ_LABEL_MAP",
    "_POSTIZ_STATE_MAP",
    "_TIKTOK_OEMBED",
    "_ZERNIO_LABEL_MAP",
    "_ZERNIO_STATE_MAP",
    "_ZERNIO_WRAPS",
    "_extract_zernio_state",
    "_fetch_zernio_analytics_body",
    "_handle_key",
    "_json_or_raise",
    "_latest_total",
    "_map_analytics",
    "_map_zernio_analytics",
    "_oembed_author_key",
    "_safe",
    "_zernio_analytics_payload",
    "_zernio_num",
    "_zernio_platform_metric_payload",
    "_zernio_platform_rows",
    "_zernio_raw_labels",
    "requests",
]
