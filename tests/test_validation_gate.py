# tests/test_validation_gate.py — Phase 2: the OFF-until-proven gate. learning_validated(cfg) is
# True once cutover.json metrics_confirmed is set — manually OR auto-stamped on the first live
# shape-proven analyzed metric (track._auto_validate_metrics_shape).
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.validation_gate import learning_validated
from fanops.track import pull_metrics, _shape_proves_learning
from fanops import cutover


def test_unvalidated_without_cutover_file(tmp_path):
    assert learning_validated(Config(root=tmp_path)) is False

def test_validated_after_metrics_confirmed(tmp_path):
    cfg = Config(root=tmp_path)
    cutover._save_state(cfg, {"metrics_confirmed": True})
    assert learning_validated(cfg) is True

def test_unvalidated_when_only_posted_not_metrics(tmp_path):
    cfg = Config(root=tmp_path)
    cutover._save_state(cfg, {"submission_id": "s1"})    # posted, but metrics not yet confirmed
    assert learning_validated(cfg) is False

def test_unvalidated_on_corrupt_cutover(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.cutover_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cutover_path.write_text("{not json")
    assert learning_validated(cfg) is False


def test_postiz_shaped_live_metrics_auto_validate_learning(tmp_path, monkeypatch):
    # Postiz delivers shares/reach/likes but NEVER retention — rows stay lift_degraded yet the live
    # shape is proven once reach + a primary engagement key reconcile (learn_doctor gates on reach).
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="sub1", public_url="https://x"))
    assert learning_validated(cfg) is False
    postiz = {"shares": 30, "reach": 50000, "likes": 200}   # no saves/retention — Postiz-shaped
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub1", "metrics": postiz}])
    assert led.posts["p1"].metrics.get("lift_degraded") is True   # honest partial objective
    assert learning_validated(cfg) is True                        # shape proven -> auto-stamp

def test_shape_proves_learning_postiz_row_not_full_primary_set():
    m = {"lift_score": 1.0, "lift_degraded": True, "lift_missing_keys": ["retention", "saves"],
         "shares": 30, "reach": 50000, "likes": 200}
    assert _shape_proves_learning(m) is True

def test_shape_proves_learning_rejects_reach_only_noise():
    assert _shape_proves_learning({"lift_score": 1.0, "likes": 3, "reach": 1000}) is False

def test_shape_proves_learning_rejects_present_but_null_primary():
    assert _shape_proves_learning({"lift_score": 1.0, "saves": None, "shares": 12, "retention": 0.7,
                                   "reach": 1000}) is False

def test_learning_validated_after_postiz_cutover(tmp_path, monkeypatch):
    # M3: the SINGLE freeze flag flips on the Postiz path too — _postiz_metrics writes metrics_confirmed,
    # which learning_validated already reads. No parallel "postiz_validated" flag (one flag, two writers).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    rows = [{"postSubmissionId": "pz1", "metrics": {"likes": 5}, "_raw_labels": ["Likes"]}]
    cutover.cutover_metrics(cfg, "pz1", list_posts=lambda w: rows)
    assert learning_validated(cfg) is True
