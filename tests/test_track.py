import json
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.timeutil import iso_z
from fanops.track import lift_score, record_metrics, pull_metrics

_PUB = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

def test_lift_weights_saves_shares_over_likes():
    hi = lift_score({"likes": 10, "saves": 50, "shares": 40, "retention": 0.8, "reach": 1000})
    lo = lift_score({"likes": 500, "saves": 1, "shares": 0, "retention": 0.1, "reach": 1000})
    assert hi > lo


def test_lift_score_pins_exact_weighted_formula():
    # AUDIT H9: hi>lo alone lets a sign-flip or a dropped weight slip through. Pin the EXACT
    # score for a known input so any change to a weight (magnitude, sign, or a removed key)
    # fails loudly. Expected = 4*saves + 4*shares + 3*retention + 0.001*reach + 0.05*likes.
    m = {"saves": 10, "shares": 5, "retention": 2, "reach": 1000, "likes": 20}
    # 40 + 20 + 6 + 1 + 1 = 68.0
    assert lift_score(m) == 68.0


def test_lift_score_each_weight_contributes_with_expected_sign_and_magnitude():
    # Isolate each term: one unit of each metric yields exactly its weight, and all weights are
    # POSITIVE (a sign flip would make a "good" metric reduce lift — caught here).
    assert lift_score({"saves": 1}) == 4.0
    assert lift_score({"shares": 1}) == 4.0
    assert lift_score({"retention": 1}) == 3.0
    assert lift_score({"reach": 1000}) == 1.0      # 0.001 * 1000
    assert lift_score({"likes": 100}) == 5.0       # 0.05 * 100
    # saves/shares dominate likes per-unit (the whole point of the weighting)
    assert lift_score({"saves": 1}) > lift_score({"likes": 1})

def test_lift_ignores_unknown_and_nonnumeric_keys(tmp_path):
    # FIX F23/F42: unexpected Blotato fields must not crash.
    s = lift_score({"saves": 10, "views": 99999, "comments": 5, "title": "x", "nested": {"a": 1}})
    assert isinstance(s, float) and s >= 40.0          # 10*4 from saves; unknowns ignored

def test_record_advances_published_to_analyzed(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))
    led = record_metrics(led, "p1", {"saves": 20, "shares": 12, "retention": 0.7})
    assert led.posts["p1"].metrics["saves"] == 20 and "lift_score" in led.posts["p1"].metrics
    assert led.posts["p1"].state is PostState.analyzed

# ---- T4: honest-lift marker — flag when the lift_score is computed without a high-weight metric ----

def _pub(led, pid="p1"):
    led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))

def test_record_marks_lift_degraded_when_high_weight_metric_absent(tmp_path):
    # A Postiz-shaped row (no saves/retention — Postiz can't deliver them) -> the lift_score is
    # partial: stamp lift_degraded + name the missing high-weight keys so the objective is HONEST.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"reach": 50000, "shares": 30, "likes": 200})
    m = led.posts["p1"].metrics
    assert m["lift_degraded"] is True
    assert m["lift_missing_keys"] == ["retention", "saves"]   # the high-weight _W keys absent from the row
    assert "lift_score" in m                                  # still scored (on the present metrics)

def test_record_not_degraded_on_full_metric_set(tmp_path):
    # A full row (every high-weight _W key present) is NOT degraded -> no marker -> today's behavior.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 20, "shares": 12, "retention": 0.7, "reach": 1000, "likes": 5})
    m = led.posts["p1"].metrics
    assert "lift_degraded" not in m and "lift_missing_keys" not in m

def test_lift_degraded_is_relative_to_the_active_weight_map(tmp_path):
    # Degraded is judged against the ACTIVE weights (a tuning override REPLACES _W). With weights whose
    # only high-weight key IS present, the lift is complete -> not degraded.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"reach": 9000}, weights={"reach": 4.0})
    assert "lift_degraded" not in led.posts["p1"].metrics

def test_lift_degraded_marker_does_not_corrupt_a_later_lift_score(tmp_path):
    # The marker keys are NOT weights, so a re-pull's lift_score ignores them (no double-count / crash).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"shares": 10})            # degraded (saves/retention absent)
    degraded_score = led.posts["p1"].metrics["lift_score"]
    assert degraded_score == lift_score({"shares": 10})        # marker did not change the score
    assert led.posts["p1"].metrics["lift_degraded"] is True

def test_record_marks_degraded_when_high_weight_metric_is_present_but_null(tmp_path):
    # D1: a backend row carrying a primary key with a NULL value (e.g. Postiz returns {"saves": None})
    # is AS untrustworthy as one omitting it — lift_score drops the null (isinstance guard), so the
    # objective is partial. The marker must catch present-but-null, not only present-but-absent.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": None, "shares": 12, "retention": 0.7, "reach": 1000})
    m = led.posts["p1"].metrics
    assert m["lift_degraded"] is True
    assert m["lift_missing_keys"] == ["saves"]                # null saves is a MISSING high-weight key
    assert m["lift_score"] == lift_score({"saves": None, "shares": 12, "retention": 0.7, "reach": 1000})

def test_present_but_null_high_weight_blocks_auto_validation(tmp_path, monkeypatch):
    # D1 (the stakes): a present-but-null primary metric must NOT auto-unfreeze learning. Before the fix
    # the null escaped lift_degraded, so _auto_validate_metrics_shape saw a "clean" analyzed row and
    # stamped learning_validated on an unproven shape. With the fix the row is degraded -> never stamps.
    from fanops.validation_gate import learning_validated
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    assert not learning_validated(cfg)
    record_metrics(led, "p1", {"saves": None, "shares": 12, "retention": 0.7})
    from fanops.track import _auto_validate_metrics_shape
    _auto_validate_metrics_shape(led, cfg)
    assert not learning_validated(cfg)                        # degraded null-shape never proves the gate

def test_pull_matches_by_submission_id_and_skips_failed(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published, submission_id="s_A"))
    led.add_post(Post(id="p2", parent_id="c", account="@a", account_id="1",
                      platform=Platform.tiktok, caption="y", state=PostState.failed, submission_id=None))
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 30, "shares": 25, "retention": 0.8}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    assert led.posts["p1"].metrics["saves"] == 30 and led.posts["p1"].state is PostState.analyzed
    assert "lift_score" not in led.posts["p2"].metrics      # failed post untouched

def test_lift_score_empty_is_zero():
    assert lift_score({}) == 0.0

def test_record_metrics_guards_non_published(tmp_path):
    # A failed post must NOT be advanced to analyzed by a direct record_metrics call.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed))
    led = record_metrics(led, "pf", {"saves": 99})
    assert led.posts["pf"].state is PostState.failed          # unchanged
    assert "lift_score" not in led.posts["pf"].metrics        # not recorded

def test_record_metrics_analyzed_is_repollable_latest_updates_state_stays(tmp_path):
    # P3 REPLACES the old "analyzed is a no-op": an analyzed post is now RE-POLLABLE so its series can
    # accumulate later offsets through the year. A later call updates Post.metrics (LATEST) and appends a
    # later series row, but NEVER reverts state and NEVER rewrites an earlier row. (A non-(published|
    # analyzed) post — failed/error/rejected/needs_reconcile — is still an absolute no-op; see below.)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pa", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))
    led = record_metrics(led, "pa", {"saves": 10}, offset="4h", captured_at="t0")   # published -> analyzed
    assert led.posts["pa"].state is PostState.analyzed
    led = record_metrics(led, "pa", {"saves": 999}, offset="24h", captured_at="t1") # analyzed re-poll
    p = led.posts["pa"]
    assert p.state is PostState.analyzed                       # stays analyzed (no revert)
    assert p.metrics["saves"] == 999                           # LATEST updated (NOT a no-op anymore)
    assert [r["offset"] for r in p.metrics_series] == ["4h", "24h"]
    assert p.metrics_series[0]["saves"] == 10                  # earlier row preserved verbatim

def test_pull_leaves_unmatched_published_post(tmp_path):
    # A published post with NO matching metrics row stays published (documented stuck-state;
    # Task 23 digest will surface it). It is NOT analyzed and gets no lift_score.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pmatch", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published, submission_id="s_HIT"))
    led.add_post(Post(id="pmiss", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="y", state=PostState.published, submission_id="s_NOROW"))
    rows = [{"postSubmissionId": "s_HIT", "metrics": {"saves": 5}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    assert led.posts["pmatch"].state is PostState.analyzed
    assert led.posts["pmiss"].state is PostState.published     # no row -> stays published
    assert "lift_score" not in led.posts["pmiss"].metrics

def test_pull_default_binding_requires_key(tmp_path, monkeypatch):
    # The default (non-injected) path actually wires BlotatoMetricsClient, which needs a key. Slice-5: the
    # per-post router builds a client ONLY for backends that have pollable posts, so the key is demanded
    # when there IS something to fetch (a published post) — the real contract, not an empty-ledger no-op.
    # H1: the no-override channel resolves via effective_provider, so it needs a LIVE legacy global to
    # bridge to Blotato (rest) — a dryrun global would now correctly SKIP the post (no client, no key).
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="s1"))
    import pytest
    with pytest.raises(RuntimeError, match="BLOTATO_API_KEY"):
        pull_metrics(led, cfg)                                 # no list_posts injected -> default binding

# --- T2 (audit b): the lift weights (the optimization target) are operator-tunable via tuning.json ---

def _write_tuning(cfg, obj):
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.tuning_path.write_text(json.dumps(obj))

def test_lift_weights_overridable_from_tuning_json(tmp_path):
    cfg = Config(root=tmp_path)
    _write_tuning(cfg, {"lift_weights": {"likes": 10.0}})
    weights = cfg.tuning().get("lift_weights")
    # one like is now worth 10.0 (vs the default 0.05) under the operator override.
    assert lift_score({"likes": 1}, weights) == 10.0
    # and a metric NOT in the override map contributes nothing (REPLACE: the map is the whole set).
    assert lift_score({"saves": 1}, weights) == 0.0

def test_defaults_unchanged_without_tuning_json(tmp_path):
    # No tuning.json -> lift_score uses the default _W exactly (weights=None path unchanged).
    cfg = Config(root=tmp_path)
    assert not cfg.tuning_path.exists()
    assert cfg.tuning() == {}
    assert lift_score({"likes": 1}) == 0.05                    # default weight intact
    assert lift_score({"saves": 1}) == 4.0
    assert lift_score({"saves": 10, "shares": 5, "retention": 2, "reach": 1000, "likes": 20}) == 68.0


# ---- M2 Task 3: backend-polymorphic _default_list_posts + BOTH id-threading sites (postiz per-post fetch) ----
class _R:                                                       # FileStorage-free fake response (mirrors test_metrics._R)
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def _postiz_env(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)

def _published(pid, sid):
    return Post(id=pid, parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.published, submission_id=sid)

def test_default_list_posts_postiz_backend_fetches_per_post(tmp_path, monkeypatch, mocker):
    from fanops.track import _default_list_posts
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"label": "Shares", "data": [{"total": "4", "date": "2026-06-12"}]}]))
    rows = list(_default_list_posts(cfg, submission_ids=["sid1"])("30d"))
    assert rows == [{"postSubmissionId": "sid1", "metrics": {"shares": 4.0}, "_raw_labels": ["Shares"]}]

def test_default_list_posts_postiz_no_ids_yields_empty(tmp_path, monkeypatch, mocker):
    from fanops.track import _default_list_posts
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    spy = mocker.patch("fanops.post.metrics.requests.get")
    assert list(_default_list_posts(cfg)("30d")) == []          # positional → submission_ids=None → [] no-op
    spy.assert_not_called()

def test_default_list_posts_rest_backend_returns_blotato_client(tmp_path, monkeypatch):
    from fanops.track import _default_list_posts
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    fetch = _default_list_posts(Config(root=tmp_path), submission_ids=["ignored"])  # kwarg inert for Blotato
    assert fetch.__self__.__class__.__name__ == "BlotatoMetricsClient"

def test_cmd_track_postiz_threads_published_ids(tmp_path, monkeypatch, mocker):
    # 3a: cmd_track must snapshot the ledger's published ids and thread them into the postiz client —
    # WITHOUT this, it builds submission_ids=None → fetches [] → the post is never matched (silent regression).
    from fanops.cli import cmd_track
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_published("p1", "sid1"))
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, [{"label": "Shares", "data": [{"total": "7", "date": "2026-06-12"}]}]))
    cmd_track(cfg, "30d")
    assert "analytics/post/sid1" in spy.call_args[0][0]          # the snapshot id was threaded, not an empty fetch
    assert Ledger.load(cfg).posts["p1"].state is PostState.analyzed

def test_pull_metrics_no_list_posts_postiz_fetches_published_ids(tmp_path, monkeypatch, mocker):
    # 3b: the no-list_posts learn-pass caller (cli.py:594) must also thread ids, else postiz fetches [].
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_published("p1", "sid1"))
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, [{"label": "Shares", "data": [{"total": "9", "date": "2026-06-12"}]}]))
    led = pull_metrics(led, cfg)                                 # no list_posts injected → default postiz binding
    assert "analytics/post/sid1" in spy.call_args[0][0]
    assert led.posts["p1"].state is PostState.analyzed and led.posts["p1"].metrics["shares"] == 9.0

def test_pull_metrics_blotato_path_unaffected_by_id_threading(tmp_path, monkeypatch, mocker):
    # 3b back-compat: the Blotato default path is byte-identical — submission_ids is passed ONLY to the
    # postiz branch, never to BlotatoMetricsClient; the bulk fetch + match is unchanged.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_published("p1", "s_A"))
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"postSubmissionId": "s_A", "metrics": {"saves": 30}}]))
    led = pull_metrics(led, cfg)                                 # default → BlotatoMetricsClient bulk, ignores ids
    assert led.posts["p1"].state is PostState.analyzed and led.posts["p1"].metrics["saves"] == 30


# ---- M2 Task 6: the full chain — documented Postiz array → analyzed + EXACT weighted lift_score ----
def test_pull_metrics_postiz_computes_lift_score(tmp_path, monkeypatch, mocker):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_published("p1", "sid1"))
    arr = [{"label": "Likes", "data": [{"total": "2", "date": "d"}]},
           {"label": "Shares", "data": [{"total": "5", "date": "d"}]},
           {"label": "Reach", "data": [{"total": "1000", "date": "d"}]}]
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, arr))
    led = pull_metrics(led, cfg)
    p = led.posts["p1"]
    assert p.state is PostState.analyzed
    assert p.metrics["lift_score"] == round(0.05 * 2 + 4.0 * 5 + 0.001 * 1000, 4)   # 0.1 + 20 + 1.0 = 21.1


# ============================ P3: multi-interval metrics time-series ============================
def _pub_at(led, pid="p1", sub="s_A", pub=_PUB, state=PostState.published, **kw):
    led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=state, submission_id=sub,
                      published_at=(iso_z(pub) if pub else None), **kw))

# ---- T2: Post.metrics_series field + the LATEST-snapshot byte-identical contract ----
def test_metrics_series_defaults_empty(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    assert led.posts["p1"].metrics_series == []

def test_record_keeps_post_metrics_latest_and_appends_one_provenance_row(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 20, "shares": 12, "retention": 0.7},
                         offset="4h", captured_at="2026-01-01T04:00:00Z")
    p = led.posts["p1"]
    # Post.metrics is the LATEST snapshot EXACTLY as today — no offset/captured_at keys leak into it.
    assert p.metrics == {"saves": 20, "shares": 12, "retention": 0.7,
                         "lift_score": lift_score({"saves": 20, "shares": 12, "retention": 0.7})}
    assert len(p.metrics_series) == 1
    row = p.metrics_series[0]
    assert row["offset"] == "4h" and row["captured_at"] == "2026-01-01T04:00:00Z"
    assert row["saves"] == 20 and row["lift_score"] == p.metrics["lift_score"]   # row = snapshot + provenance

# ---- T3: record_metrics appends at the due offset, widens re-entry, terminal UNCHANGED ----
def test_record_published_flips_analyzed_and_appends_4h_row(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 5}, offset="4h", captured_at="t0")
    assert led.posts["p1"].state is PostState.analyzed
    assert [r["offset"] for r in led.posts["p1"].metrics_series] == ["4h"]

def test_record_duplicate_offset_does_not_duplicate_row_but_updates_latest(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 5}, offset="4h", captured_at="t0")
    led = record_metrics(led, "p1", {"saves": 8}, offset="4h", captured_at="t0b")   # same offset again
    p = led.posts["p1"]
    assert [r["offset"] for r in p.metrics_series] == ["4h"]    # not duplicated
    assert p.metrics_series[0]["saves"] == 5                    # the captured row is unchanged
    assert p.metrics["saves"] == 8                              # but LATEST snapshot DOES update

def test_record_failed_post_is_absolute_noop_no_row(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed))
    led = record_metrics(led, "pf", {"saves": 99}, offset="4h", captured_at="t0")
    assert led.posts["pf"].state is PostState.failed
    assert led.posts["pf"].metrics_series == [] and "lift_score" not in led.posts["pf"].metrics

def test_record_no_offset_updates_latest_appends_no_row(tmp_path):
    # back-compat: a legacy no-offset call updates Post.metrics + flips published->analyzed, NO series row.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 7})
    assert led.posts["p1"].state is PostState.analyzed
    assert led.posts["p1"].metrics["saves"] == 7 and led.posts["p1"].metrics_series == []

def test_series_is_bounded_at_twenty_by_the_finite_cadence(tmp_path):
    from fanops.metrics_schedule import CADENCE_OFFSETS
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    for off in CADENCE_OFFSETS:
        led = record_metrics(led, "p1", {"saves": 1}, offset=off, captured_at="t")
    led = record_metrics(led, "p1", {"saves": 1}, offset="52w", captured_at="t")   # re-poll terminal -> no dup
    assert len(led.posts["p1"].metrics_series) == 20            # one row per offset, bounded, no pruning

# ---- T4: pull_metrics computes the due offset per post (clock-injected) ----
def test_pull_5h_old_gets_one_4h_row(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_at(led)
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 30, "shares": 25, "retention": 0.8}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=_PUB + timedelta(hours=5))
    p = led.posts["p1"]
    assert p.state is PostState.analyzed
    assert [r["offset"] for r in p.metrics_series] == ["4h"]

def test_pull_too_soon_flips_analyzed_but_no_row(tmp_path):
    # R1: a matched post flips to analyzed on the FIRST poll regardless of timing; the SERIES row only
    # lands once an offset is due (1h-old -> due_offset None -> no row, but still analyzed + LATEST set).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_at(led)
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 3}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=_PUB + timedelta(hours=1))
    p = led.posts["p1"]
    assert p.state is PostState.analyzed and p.metrics["saves"] == 3 and p.metrics_series == []

def test_pull_30h_old_gets_24h_row(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_at(led)
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 9}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=_PUB + timedelta(hours=30))
    assert [r["offset"] for r in led.posts["p1"].metrics_series] == ["24h"]

def test_pull_repolls_an_analyzed_post_accumulating_a_later_offset(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _pub_at(led, state=PostState.analyzed, metrics={"saves": 1, "lift_score": 4.0},
            metrics_series=[{"saves": 1, "lift_score": 4.0, "offset": "4h", "captured_at": "t0"}])
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 12}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=_PUB + timedelta(hours=30))
    p = led.posts["p1"]
    assert p.state is PostState.analyzed
    assert [r["offset"] for r in p.metrics_series] == ["4h", "24h"]   # widened match-set re-polls it

def test_pull_no_published_at_flips_analyzed_no_row(tmp_path):
    # back-compat (why the existing skip-failed/match tests still pass): a published post WITHOUT a
    # published_at is matched -> analyzed + LATEST set, but due_offset is None -> no series row.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_at(led, pub=None)
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 7}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    p = led.posts["p1"]
    assert p.state is PostState.analyzed and p.metrics["saves"] == 7 and p.metrics_series == []

# ---- T5: Postiz-degraded honesty on the series row ----
def test_pull_postiz_row_carries_lift_degraded(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_at(led)
    rows = [{"postSubmissionId": "s_A", "metrics": {"shares": 30, "reach": 50000, "likes": 200}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows, now=_PUB + timedelta(hours=5))
    row = led.posts["p1"].metrics_series[0]
    assert row["lift_degraded"] is True
    assert row["lift_missing_keys"] == ["retention", "saves"]   # asserted on the ROW, not only Post.metrics

def test_cmd_track_prints_series_and_degraded_summary(tmp_path, monkeypatch, mocker, capsys):
    # cmd_track summarizes the pass: series rows ADDED and how many were degraded. A Postiz-shaped row
    # (shares only) on a >4h-old post adds exactly one degraded row.
    from fanops.cli import cmd_track
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _pub_at(led, sub="sid1")                                # published 2026-01-01 -> well past 4h vs real now
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"label": "Shares", "data": [{"total": "7", "date": "d"}]}]))
    cmd_track(cfg, "30d")
    out = capsys.readouterr().out
    assert "series_rows+=1" in out and "degraded=1" in out

def test_cmd_track_threads_analyzed_post_ids_too(tmp_path, monkeypatch, mocker):
    # P3 widened the published-id snapshot to published|analyzed so an analyzed post keeps being
    # re-polled (its series accumulates later offsets through the year). Pin that an ANALYZED post's
    # submission_id is threaded into the Postiz per-post fetch, not silently dropped (review completeness).
    from fanops.cli import cmd_track
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="pa", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.analyzed, submission_id="sidA",
                          published_at=iso_z(_PUB), metrics={"saves": 1, "lift_score": 4.0}))
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, [{"label": "Shares", "data": [{"total": "3", "date": "d"}]}]))
    cmd_track(cfg, "30d")
    assert "analytics/post/sidA" in spy.call_args[0][0]   # analyzed post's id threaded, not dropped


# ---- de-gated learning: real non-degraded live metrics auto-confirm the shape (NO operator cutover step) ----
def _pub_post(led, sid="sub1"):
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id=sid))

_FULL = {"saves": 10, "shares": 5, "retention": 0.8, "reach": 1000, "likes": 3}   # all high-weight keys present
_DEGRADED = {"likes": 3, "reach": 1000}                                            # missing saves/shares/retention

def test_pull_live_non_degraded_auto_validates_learning(tmp_path, monkeypatch):
    # The first REAL, non-degraded analyzed metric from a LIVE backend proves the metric field-shape against
    # _W — exactly what `fanops cutover metrics` did by hand. learning_validated flips True with NO operator step.
    from fanops.validation_gate import learning_validated
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_post(led); led.save()
    assert learning_validated(cfg) is False
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub1", "metrics": _FULL}])
    assert learning_validated(cfg) is True

def test_pull_dryrun_never_auto_validates(tmp_path, monkeypatch):
    from fanops.validation_gate import learning_validated
    monkeypatch.delenv("FANOPS_POSTER", raising=False)            # dryrun: no real analytics, never proves the shape
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_post(led); led.save()
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub1", "metrics": _FULL}])
    assert learning_validated(cfg) is False

def test_pull_degraded_metric_never_auto_validates(tmp_path, monkeypatch):
    # A degraded row (a primary weighted key absent) is the unproven/mis-keyed case the gate exists for.
    from fanops.validation_gate import learning_validated
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub_post(led); led.save()
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub1", "metrics": _DEGRADED}])
    assert learning_validated(cfg) is False


def test_pull_skips_post_with_fanops_token_submission_id(tmp_path):
    # CULM-3: a published post still carrying the fanops_ birth token must NOT be attributed (the analytics
    # endpoint 404s a fanops_ id) — it's a logged un-attributable outcome, never a silent freeze.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="fanops_abc"))
    rows = [{"postSubmissionId": "fanops_abc", "metrics": {"saves": 9}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    assert "lift_score" not in led.posts["p1"].metrics            # never attributed to a fake id
    assert led.posts["p1"].state is PostState.published           # not flipped to analyzed


def test_partial_row_does_not_regress_a_complete_snapshot(tmp_path):
    # CULM-6: a transiently-partial pull (backend momentarily drops a primary key) must NOT overwrite a
    # complete snapshot and regress lift. Carry forward the dropped primary key; score the MERGED row.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _pub(led)
    led = record_metrics(led, "p1", {"saves": 50, "shares": 20, "retention": 0.8})   # complete
    full_lift = led.posts["p1"].metrics["lift_score"]
    led = record_metrics(led, "p1", {"shares": 20, "retention": 0.8})               # partial: saves dropped
    assert led.posts["p1"].metrics["saves"] == 50                                  # carried forward
    assert led.posts["p1"].metrics["lift_score"] == full_lift                      # no regression
    assert not led.posts["p1"].metrics.get("lift_degraded")                        # merged row is complete
