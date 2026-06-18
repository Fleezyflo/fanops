from fanops.config import Config
from fanops.post.metrics import BlotatoMetricsClient

class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def test_list_posts_returns_rows(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"items": [{"postSubmissionId": "s1", "metrics": {"saves": 5}}]}))
    rows = BlotatoMetricsClient(cfg).list_posts("30d")
    assert rows[0]["postSubmissionId"] == "s1" and rows[0]["metrics"]["saves"] == 5

import pytest

def test_list_posts_bare_list_response(tmp_path, monkeypatch, mocker):
    # A top-level array response must be returned as-is, not crash on .get
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"postSubmissionId": "s1"}, {"postSubmissionId": "s2"}]))
    rows = BlotatoMetricsClient(cfg).list_posts()
    assert [r["postSubmissionId"] for r in rows] == ["s1", "s2"]

def test_list_posts_non_2xx_raises(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(500, {"e": "down"}))
    with pytest.raises(RuntimeError, match="500"):
        BlotatoMetricsClient(cfg).list_posts()

class _RBadJson:
    # a 200 whose body is NOT JSON (HTML error page from a misconfigured proxy)
    def __init__(s, c, text): s.status_code = c; s.text = text
    def json(s): raise ValueError("Expecting value: line 1 column 1 (char 0)")

def test_list_posts_non_json_200_raises_clean_runtimeerror(tmp_path, monkeypatch, mocker):
    # ECC-review fix #4: a 200-with-HTML made resp.json() raise a raw JSONDecodeError that aborted
    # the WHOLE metrics pass (losing every post's metrics). It must become a diagnosable RuntimeError.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_RBadJson(200, "<html>502 Bad Gateway</html>"))
    with pytest.raises(RuntimeError, match="non-JSON"):
        BlotatoMetricsClient(cfg).list_posts()

def test_list_posts_401_is_typed_auth_with_redacted_body(tmp_path, monkeypatch, mocker):
    # Audit follow-up: the df85662 401-redaction missed the two metrics clients. A 401 here must
    # (a) raise BlotatoAuthError (so reconcile's halt-on-auth guard fires + track halts cleanly),
    # and (b) NOT embed resp.text (a 401 body echoing the key would leak via stdout/ledger/digest).
    from fanops.errors import BlotatoAuthError
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(401, {"e": "denied for key SENTINEL-KEY-ECHO"}))
    with pytest.raises(BlotatoAuthError) as ei:
        BlotatoMetricsClient(cfg).list_posts()
    assert "SENTINEL-KEY-ECHO" not in str(ei.value) and "401" in str(ei.value)

def test_get_status_401_is_typed_auth_with_redacted_body(tmp_path, monkeypatch, mocker):
    from fanops.errors import BlotatoAuthError
    from fanops.post.metrics import BlotatoStatusClient
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(401, {"e": "denied for key SENTINEL-KEY-ECHO"}))
    with pytest.raises(BlotatoAuthError) as ei:
        BlotatoStatusClient(cfg).get_status("sub_1")
    assert "SENTINEL-KEY-ECHO" not in str(ei.value) and "401" in str(ei.value)


# ---- M2: PostizMetricsClient — Postiz analytics/post array → the {postSubmissionId, metrics} contract ----
def _pcfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    return Config(root=tmp_path)

# the documented analytics array shape (docs.postiz.com) — the integration-checkpoint fixture
_DOC_ARRAY = [{"label": "Likes", "data": [{"total": "3", "date": "2026-06-10"}, {"total": "5", "date": "2026-06-12"}], "percentageChange": 2},
              {"label": "Impressions", "data": [{"total": "100", "date": "2026-06-12"}]}]

def test_postiz_list_posts_maps_documented_array(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, _DOC_ARRAY))
    rows = PostizMetricsClient(cfg, submission_ids=["sid1"]).list_posts("30d")
    assert rows == [{"postSubmissionId": "sid1", "metrics": {"likes": 5.0, "reach": 100.0},
                     "_raw_labels": ["Likes", "Impressions"]}]   # latest total wins, str→num, Impressions→reach

def test_postiz_list_posts_none_ids_makes_no_network_call(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    spy = mocker.patch("fanops.post.metrics.requests.get")
    assert PostizMetricsClient(cfg, submission_ids=None).list_posts("30d") == []
    spy.assert_not_called()

def test_postiz_unknown_label_dropped_from_metrics_but_kept_in_raw_labels(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    arr = [{"label": "Saves", "data": [{"total": "9", "date": "2026-06-12"}]},   # unmapped → dropped from metrics
           {"label": "Shares", "data": [{"total": "4", "date": "2026-06-12"}]}]
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, arr))
    row = PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()[0]
    assert row["metrics"] == {"shares": 4.0} and row["_raw_labels"] == ["Saves", "Shares"]

def test_postiz_empty_data_series_omits_key(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"label": "Likes", "data": []}]))
    assert PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()[0]["metrics"] == {}

def test_postiz_non_list_response_yields_empty_metrics(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"unexpected": "object"}))
    row = PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()[0]
    assert row["metrics"] == {} and row["_raw_labels"] == []

def test_postiz_401_is_typed_auth_with_redacted_body(tmp_path, monkeypatch, mocker):
    from fanops.errors import PostizAuthError
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(401, {"e": "denied for key SENTINEL-KEY-ECHO"}))
    with pytest.raises(PostizAuthError) as ei:
        PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()
    assert "SENTINEL-KEY-ECHO" not in str(ei.value) and "401" in str(ei.value)
    assert cfg.postiz_api_key not in str(ei.value)              # the KEY VALUE itself must never appear in the error

def test_postiz_fetch_one_non_2xx_raises_runtimeerror(tmp_path, monkeypatch, mocker):
    # _fetch_one still raises a RuntimeError on a 5xx (the per-post contract is unchanged at that
    # level). FIX 6 moved the ISOLATION up into list_posts (see below), so the loop catches this
    # per id rather than letting one 5xx abort the whole pass.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(503, "down"))
    with pytest.raises(RuntimeError, match="503"):
        PostizMetricsClient(cfg, submission_ids=["s"])._fetch_one("s", 7)

def test_postiz_list_posts_isolates_a_single_5xx(tmp_path, monkeypatch, mocker):
    # FIX 6: a single post's 5xx must NOT abort the pass — list_posts logs+skips it (empty row) and
    # still returns the (lone) row rather than raising, so a co-batched healthy post isn't lost.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(503, "down"))
    rows = PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()
    assert rows == [{"postSubmissionId": "s", "metrics": {}, "_raw_labels": []}]   # skipped, not fatal

# ---- M2 Task 2: lock the label→lift mapping + the window→date helper (the integration checkpoint) ----
def test_postiz_map_analytics_maps_four_documented_labels():
    from fanops.post.metrics import _map_analytics
    arr = [{"label": "Likes", "data": [{"total": "1", "date": "d"}]},
           {"label": "Shares", "data": [{"total": "2", "date": "d"}]},
           {"label": "Comments", "data": [{"total": "3", "date": "d"}]},
           {"label": "Impressions", "data": [{"total": "4", "date": "d"}]}]
    # comments is mapped (present in the dict) even though default _W ignores it — the whitelist is the gate, not the map
    assert _map_analytics(arr) == {"likes": 1.0, "shares": 2.0, "comments": 3.0, "reach": 4.0}

def test_postiz_window_days_table():
    from fanops.post.metrics import _window_days
    assert (_window_days("30d"), _window_days("7d"), _window_days(""), _window_days("garbage")) == (30, 7, 7, 7)


def test_postiz_list_posts_one_failing_sid_does_not_lose_the_others(tmp_path, monkeypatch, mocker):
    # FIX 6: the `for sid: self._fetch_one(...)` loop had no per-post isolation, so a single post's
    # 5xx analytics aborted the WHOLE pass and lost every other post's metrics. One failing sid must
    # be logged + skipped; the others' rows must still be collected.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    good = [{"label": "Likes", "data": [{"total": "7", "date": "2026-06-12"}]}]
    def fake_get(url, **kw):
        return _R(500, {"e": "down"}) if "BAD" in url else _R(200, good)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=fake_get)
    rows = PostizMetricsClient(cfg, submission_ids=["BAD", "OK1", "OK2"]).list_posts("30d")
    by_sid = {r["postSubmissionId"]: r for r in rows}
    assert by_sid["OK1"]["metrics"] == {"likes": 7.0}         # survivors collected
    assert by_sid["OK2"]["metrics"] == {"likes": 7.0}
    assert "BAD" not in by_sid or not by_sid["BAD"]["metrics"]  # failing sid skipped/empty, not fatal
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "BAD" in log                                       # breadcrumb for the failed fetch
