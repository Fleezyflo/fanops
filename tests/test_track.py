from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.track import lift_score, record_metrics, pull_metrics

def test_lift_weights_saves_shares_over_likes():
    hi = lift_score({"likes": 10, "saves": 50, "shares": 40, "retention": 0.8, "reach": 1000})
    lo = lift_score({"likes": 500, "saves": 1, "shares": 0, "retention": 0.1, "reach": 1000})
    assert hi > lo

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

def test_record_metrics_already_analyzed_is_noop(tmp_path):
    # An already-analyzed post is not re-overwritten by a stray direct call.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pa", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))
    led = record_metrics(led, "pa", {"saves": 10})            # published -> analyzed
    assert led.posts["pa"].state is PostState.analyzed
    first = dict(led.posts["pa"].metrics)
    led = record_metrics(led, "pa", {"saves": 999})           # now analyzed -> guard no-ops
    assert led.posts["pa"].metrics == first                    # unchanged (not re-overwritten)

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
    # The default (non-injected) path actually wires BlotatoMetricsClient, which needs a key.
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    import pytest
    with pytest.raises(RuntimeError, match="BLOTATO_API_KEY"):
        pull_metrics(led, cfg)                                 # no list_posts injected -> default binding
