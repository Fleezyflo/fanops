# tests/test_imported_insights.py — ledger-rebuild M3: the Graph insights read fills metrics for
# ImportedMedia rows by media_id, so a live-only post carries real performance (not just an id). It
# CONSUMES the empty-metric guard shipped in meta_graph.media_insights (an unresolved/None product_type
# yields an empty derived metric set, which media_insights refuses PRE-FLIGHT — no HTTP, no scope block).
# CRITICAL acceptance (PRD): the unresolved-product_type ImportedMedia case makes ZERO HTTP calls and
# writes NO scope-block breadcrumb. Pure-fixture (injected `get=`), no real network.
from fanops.config import Config
from fanops.models import ImportedMedia
from fanops.ledger import Ledger
from fanops import track

_TOKEN = "SECRET-meta-token-xyz"


def _cfg(tmp_path, monkeypatch, *, token=_TOKEN, ig="ig-123"):
    monkeypatch.setenv("META_GRAPH_TOKEN", token) if token else monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.setenv("META_IG_USER_ID", ig) if ig else monkeypatch.delenv("META_IG_USER_ID", raising=False)
    return Config(root=tmp_path)


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _insights_get(by_id):
    """A fake requests.get for /{media_id}/insights. Records every call url so a test can assert zero calls."""
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        for mid, resp in by_id.items():
            if f"/{mid}/insights" in url:
                return resp
        return _Resp(404, None)
    get.calls = calls
    return get


def _reels_insights(**vals):
    return _Resp(200, {"data": [{"name": k, "values": [{"value": v}]} for k, v in vals.items()]})


def _led(cfg, rows):
    led = Ledger(cfg)
    for im in rows:
        led.add_imported_media(im)
    return led


def test_insights_fill_metrics_for_imported_by_media_id(tmp_path, monkeypatch):
    # A resolved ImportedMedia (media_id + product_type) gets its metrics filled from the Graph read.
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS")])
    track.pull_imported_insights(led, cfg, get=_insights_get({
        "M1": _reels_insights(reach=1000, saved=50, shares=10, likes=200, comments=5)}))
    im = led.imported_media["M1"]
    assert im.metrics.get("reach") == 1000
    assert im.metrics.get("saves") == 50            # Graph `saved` -> our `saves` (the shared map)
    assert "lift_score" in im.metrics               # lift computed like a Post


def test_unresolved_product_type_makes_ZERO_http_calls(tmp_path, monkeypatch):
    # THE critical acceptance: an ImportedMedia with product_type=None derives an EMPTY metric set, which
    # media_insights refuses PRE-FLIGHT — so the insights `get` is NEVER called (no empty `metric=` request).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type=None)])
    get = _insights_get({"M1": _reels_insights(reach=1)})
    track.pull_imported_insights(led, cfg, get=get)
    assert get.calls == []                           # ZERO HTTP calls — the empty-metric request was never built
    assert led.imported_media["M1"].metrics == {}    # no metrics filled (stays re-resolvable)


def test_unresolved_product_type_writes_no_scope_block(tmp_path, monkeypatch):
    # The pre-flight refusal is TRANSIENT-shaped (re-resolve next pass), NOT a scope refusal — so it must
    # NOT write the LOUD insights-blocked breadcrumb (which would false-alarm the doctor/Home surface).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type=None)])
    track.pull_imported_insights(led, cfg, get=_insights_get({"M1": _reels_insights(reach=1)}))
    assert not cfg.insights_blocked_path.exists()    # no scope-block breadcrumb written


def test_insights_append_metrics_series_row(tmp_path, monkeypatch):
    # The read appends an append-only metrics_series row (mirroring Post.metrics_series) so an imported
    # post accumulates a performance history, not just a latest snapshot.
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS")])
    track.pull_imported_insights(led, cfg, get=_insights_get({"M1": _reels_insights(reach=1000, saved=50)}))
    im = led.imported_media["M1"]
    assert im.metrics_series and im.metrics_series[-1].get("reach") == 1000
    assert im.metrics_series[-1].get("captured_at") is not None


def test_insights_fail_open_no_creds(tmp_path, monkeypatch):
    # No creds -> media_insights returns None (transient) -> no metrics filled, no crash, no series row.
    cfg = _cfg(tmp_path, monkeypatch, token=None, ig=None)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS")])
    track.pull_imported_insights(led, cfg, get=_insights_get({"M1": _reels_insights(reach=1)}))
    assert led.imported_media["M1"].metrics == {}


def test_insights_transient_none_preserves_prior_metrics(tmp_path, monkeypatch):
    # A transient miss (None) on a row that ALREADY has metrics must NOT erase them (mirrors the Post path:
    # a partial/failed pull never regresses a stored snapshot).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS",
                                   metrics={"reach": 777, "lift_score": 0.7})])
    # 500 -> media_insights returns None (transient)
    track.pull_imported_insights(led, cfg, get=_insights_get({"M1": _Resp(500, None)}))
    assert led.imported_media["M1"].metrics.get("reach") == 777    # prior metrics preserved


def test_insights_only_reads_imported_not_posts(tmp_path, monkeypatch):
    # pull_imported_insights operates on the imported_media map ONLY — an empty map is a clean no-op (it does
    # not touch posts; the forward Post path is pull_metrics, untouched).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])
    track.pull_imported_insights(led, cfg, get=_insights_get({}))
    assert led.imported_media == {}
