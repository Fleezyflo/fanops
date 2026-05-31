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
