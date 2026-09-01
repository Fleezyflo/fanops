import pytest
from fanops.config import Config

class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b


# ---- M2: PostizMetricsClient — Postiz analytics/post array → the {postSubmissionId, metrics} contract ----
def _pcfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    return Config(root=tmp_path)

# the VERIFIED-live analytics array shape (labels Views/Reach/Saves/Likes/Comments/Shares, confirmed
# against the running instance 2026-06-21) — the integration-checkpoint fixture
_DOC_ARRAY = [{"label": "Likes", "data": [{"total": "3", "date": "2026-06-10"}, {"total": "5", "date": "2026-06-12"}], "percentageChange": 2},
              {"label": "Reach", "data": [{"total": "100", "date": "2026-06-12"}]}]

def test_postiz_list_posts_maps_documented_array(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, _DOC_ARRAY))
    rows = PostizMetricsClient(cfg, submission_ids=["sid1"]).list_posts("30d")
    assert rows == [{"postSubmissionId": "sid1", "metrics": {"likes": 5.0, "reach": 100.0},
                     "_raw_labels": ["Likes", "Reach"]}]   # latest total wins, str→num, Reach→reach

def test_postiz_list_posts_none_ids_makes_no_network_call(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    spy = mocker.patch("fanops.post.metrics.requests.get")
    assert PostizMetricsClient(cfg, submission_ids=None).list_posts("30d") == []
    spy.assert_not_called()

def test_postiz_unknown_label_dropped_from_metrics_but_kept_in_raw_labels(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    arr = [{"label": "Retention", "data": [{"total": "9", "date": "2026-06-12"}]},   # genuinely absent from the live label map → dropped
           {"label": "Shares", "data": [{"total": "4", "date": "2026-06-12"}]}]
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, arr))
    row = PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()[0]
    assert row["metrics"] == {"shares": 4.0} and row["_raw_labels"] == ["Retention", "Shares"]

def test_postiz_empty_data_series_omits_key(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"label": "Likes", "data": []}]))
    assert PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()[0]["metrics"] == {}

def test_postiz_list_posts_skips_a_failed_fetch_not_an_empty_row(tmp_path, monkeypatch, mocker):
    # operability follow-up: a per-post analytics fetch failure SKIPS that id (its prior metrics survive,
    # re-polled next pass), NOT a metrics={} row that record_metrics would wholesale-zero the post with.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    def _flaky(url, **kw):
        if "s_bad" in url: raise RuntimeError("transient 503")
        return _R(200, _DOC_ARRAY)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=_flaky)
    rows = PostizMetricsClient(cfg, submission_ids=["s_ok", "s_bad"]).list_posts()
    assert [r["postSubmissionId"] for r in rows] == ["s_ok"]           # s_bad SKIPPED, no empty row emitted

def test_postiz_analytics_date_param_is_unix_ms_not_day_count(tmp_path, monkeypatch, mocker):
    # BUG (Context7-confirmed): /public/v1/analytics/post/{id} `date` is a Unix-MS TIMESTAMP, NOT a day
    # count. The old code sent date=_window_days(window) (7/30), which queries ~1970 -> empty metrics ->
    # a live Postiz post never feeds the learning loop. The `date` must be a real ms-epoch timestamp.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    g = mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, _DOC_ARRAY))
    PostizMetricsClient(cfg, submission_ids=["sid1"]).list_posts("30d")
    sent = g.call_args.kwargs.get("params", {}).get("date")
    assert isinstance(sent, int) and sent > 1_500_000_000_000   # a real ms-epoch timestamp (post-2017), never 7/30

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
    # FIX 6 + operability follow-up: a single post's 5xx must NOT abort the pass — list_posts logs+SKIPS it
    # (no row, so record_metrics can't wholesale-zero the post's real metrics) rather than raising. With only
    # the failing id that's an empty list; the co-batched-survivor case is test_postiz_list_posts_skips_a_failed_fetch.
    from fanops.post.metrics import PostizMetricsClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(503, "down"))
    rows = PostizMetricsClient(cfg, submission_ids=["s"]).list_posts()
    assert rows == []                              # the failed id is SKIPPED, not emitted as an empty row; no raise

# ---- M2 Task 2: lock the label→lift mapping to the VERIFIED-live label set (the integration checkpoint) ----
def test_postiz_map_analytics_maps_live_labels():
    # The 6 labels the live Postiz analytics endpoint actually emits (verified 2026-06-21): Views/Reach/
    # Saves/Likes/Comments/Shares. The OLD map keyed `impressions` (never present) and dropped saves+reach
    # — which silently froze the learning loop (reach gates learn_doctor; saves is the top lift weight).
    from fanops.post.metrics import _map_analytics
    arr = [{"label": "Likes", "data": [{"total": "1", "date": "d"}]},
           {"label": "Shares", "data": [{"total": "2", "date": "d"}]},
           {"label": "Comments", "data": [{"total": "3", "date": "d"}]},
           {"label": "Reach", "data": [{"total": "4", "date": "d"}]},
           {"label": "Saves", "data": [{"total": "5", "date": "d"}]},
           {"label": "Views", "data": [{"total": "6", "date": "d"}]}]
    # saves+reach are the lift-consumed keys (track._W); views/comments map but are present-but-unweighted
    assert _map_analytics(arr) == {"likes": 1.0, "shares": 2.0, "comments": 3.0,
                                   "reach": 4.0, "saves": 5.0, "views": 6.0}


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


# ---- MOL-783: ONE windowed /public/v1/posts fetch for the Postiz mirror (list_all) ----
# Live-verified 2026-08-07 against the running instance (http://localhost:4007/api/public/v1/posts):
# no params → HTTP 400 "startDate must be a valid ISO 8601 date string"; the maximal window returns
# {"posts":[…]} with NO envelope key (no page/total/nextCursor); page=2/limit=5 return byte-identical
# bodies (both ignored) ⇒ the endpoint does NOT paginate, so one call covers the window and a row's
# ABSENCE is a real observation. Per-post get_status was deleted in MOL-820; list_all is the sole reader.
_ROWS = [{"id": "p1", "state": "PUBLISHED", "releaseURL": "https://www.instagram.com/reel/A/", "releaseId": "1784100"},
         {"id": "p2", "state": "ERROR"},
         {"id": "p3", "state": "QUEUE"}]

def test_postiz_list_all_sends_the_mandatory_date_only_window_and_no_caller_knob(tmp_path, monkeypatch, mocker):
    # The window is the API's MANDATORY shape, not a curation knob: list_all takes no window argument
    # and sends a maximal date-only ISO pair, because a narrow window manufactures a false "absent".
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    g = mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": _ROWS}))
    PostizStatusClient(cfg).list_all()
    params = g.call_args.kwargs.get("params", {})
    assert len(params["startDate"]) == 10 and len(params["endDate"]) == 10      # date-only ISO, the accepted form
    assert params["startDate"] <= "2020-01-01" and params["endDate"] >= "2099-12-31"   # maximal, not a slice
    assert "page" not in params and "limit" not in params                       # no page walk — the endpoint ignores both
    assert g.call_count == 1                                                    # ONE call returns the whole corpus

def test_postiz_list_all_indexes_rows_and_carries_raw_state_alongside_mapped_status(tmp_path, monkeypatch, mocker):
    # Each value carries BOTH vocabularies: the mapped backend-agnostic `status` AND
    # the raw Postiz `state` token (the mirror persists the raw token), plus the untouched row in `raw`.
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": _ROWS}))
    out = PostizStatusClient(cfg).list_all()
    assert set(out) == {"p1", "p2", "p3"}                                       # indexed by row id
    assert (out["p1"]["status"], out["p1"]["state"]) == ("published", "PUBLISHED")
    assert (out["p2"]["status"], out["p2"]["state"]) == ("failed", "ERROR")
    assert (out["p3"]["status"], out["p3"]["state"]) == ("scheduled", "QUEUE")  # unknown/queued parked, never failed
    assert out["p1"]["releaseURL"] == "https://www.instagram.com/reel/A/" and out["p1"]["releaseId"] == "1784100"
    assert out["p2"]["releaseURL"] is None and out["p2"]["releaseId"] is None   # absent keys → None, never a KeyError
    assert out["p1"]["raw"] is _ROWS[0]                                         # the untouched row rides along

def test_postiz_list_all_parses_a_bare_list_body(tmp_path, monkeypatch, mocker):
    # Both observed shapes parse: {"posts":[…]} (the live shape) and a bare list.
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, _ROWS))
    assert set(PostizStatusClient(cfg).list_all()) == {"p1", "p2", "p3"}

def test_postiz_list_all_401_is_typed_auth_with_body_withheld(tmp_path, monkeypatch, mocker):
    from fanops.errors import PostizAuthError
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(401, {"e": "denied for key SENTINEL-KEY-ECHO"}))
    with pytest.raises(PostizAuthError) as ei:
        PostizStatusClient(cfg).list_all()
    assert "SENTINEL-KEY-ECHO" not in str(ei.value) and "401" in str(ei.value)
    assert cfg.postiz_api_key not in str(ei.value)              # the KEY VALUE itself must never appear

def test_postiz_list_all_5xx_raises_runtimeerror(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(503, "down"))
    with pytest.raises(RuntimeError, match="503"):
        PostizStatusClient(cfg).list_all()

def test_postiz_list_all_hits_exactly_one_endpoint(tmp_path, monkeypatch, mocker):
    # The anti-second-channel pin: the mirror's sole reader issues exactly ONE GET to /public/v1/posts.
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    g = mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": _ROWS}))
    assert PostizStatusClient(cfg).list_all()["p1"]["status"] == "published"
    assert g.call_count == 1
    assert g.call_args.args[0] == "https://postiz.example.com/api/public/v1/posts"

def test_postiz_fetch_posts_first_row_wins_on_a_duplicate_id(tmp_path, monkeypatch, mocker):
    # setdefault keeps the FIRST match; the id-keyed index must not silently flip that to last-wins.
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    dupes = [{"id": "p1", "state": "PUBLISHED", "releaseURL": "first"}, {"id": "p1", "state": "ERROR"}]
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": dupes}))
    row = PostizStatusClient(cfg).list_all()["p1"]
    assert row["status"] == "published" and row["releaseURL"] == "first"

def test_postiz_fetch_posts_skips_non_dict_and_idless_rows(tmp_path, monkeypatch, mocker):
    # A malformed row must not abort the whole window (and must not become a None-keyed entry).
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    body = {"posts": ["junk", None, {"state": "PUBLISHED"}, {"id": 7, "state": "PUBLISHED"}, {"id": "p1", "state": "PUBLISHED"}]}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    assert set(PostizStatusClient(cfg).list_all()) == {"p1"}

def test_poster_fail_reason_prefers_human_text_over_stack():
    from fanops.post.metrics import poster_fail_reason
    assert poster_fail_reason("API access blocked.") == "API access blocked."
    blob = {"cause": {"failure": {"message": "bad_body", "stackTrace": "ApplicationFailure:\n    at x"}}}
    assert poster_fail_reason(blob) == "bad_body"
    assert poster_fail_reason(None, "", {"error": "platform rejected"}) == "platform rejected"

def test_local_postiz_errors_silent_under_pytest_without_lookup(tmp_path, monkeypatch):
    from fanops.postiz_lifecycle import local_postiz_errors
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    assert local_postiz_errors(cfg, ["cmtgxb3ma000ep87wxbmawjms"]) == {}


def test_local_postiz_errors_honors_injected_lookup(tmp_path, monkeypatch):
    from fanops.postiz_lifecycle import local_postiz_errors
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    got = local_postiz_errors(cfg, ["abc"], lookup=lambda ids: {ids[0]: "Refresh channel needed"})
    assert got == {"abc": "Refresh channel needed"}


def test_list_all_fills_stripped_error_from_row_lookup(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizStatusClient
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"posts": [{"id": "p1", "state": "ERROR"}]}))
    mocker.patch("fanops.postiz_lifecycle.local_postiz_errors",
                 return_value={"p1": "Refresh channel needed"})
    row = PostizStatusClient(cfg).list_all()["p1"]
    assert row["status"] == "failed"
    assert row["error"] == "Refresh channel needed"
    assert row["errorMessage"] == "Refresh channel needed"


def test_list_all_does_not_overwrite_public_error_field(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": [
        {"id": "p1", "state": "ERROR", "error": "API access blocked."}]}))
    mocker.patch("fanops.postiz_lifecycle.local_postiz_errors",
                 return_value={"p1": "should not win"})
    row = PostizStatusClient(cfg).list_all()["p1"]
    assert row["error"] == "API access blocked."


def test_list_all_json_post_error_uses_poster_fail_reason(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizStatusClient
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    raw = '{"cause":{"failure":{"message":"getaddrinfo ENOTFOUND example.invalid","stackTrace":"Error:\\n    at x"}}}'
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"posts": [{"id": "p1", "state": "ERROR"}]}))
    mocker.patch("fanops.postiz_lifecycle.local_postiz_errors", return_value={"p1": raw})
    row = PostizStatusClient(cfg).list_all()["p1"]
    assert row["errorMessage"] == "getaddrinfo ENOTFOUND example.invalid"

def test_postiz_error_row_keeps_error_field(tmp_path, monkeypatch, mocker):
    from fanops.post.metrics import PostizStatusClient
    cfg = _pcfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"posts": [
        {"id": "p1", "state": "ERROR", "error": "API access blocked."}]}))
    row = PostizStatusClient(cfg).list_all()["p1"]
    assert row["status"] == "failed" and row["error"] == "API access blocked."
