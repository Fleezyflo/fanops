# tests/test_validation_gate.py — Phase 2: the OFF-until-proven gate. learning_validated(cfg) is
# True only after `fanops cutover metrics` reconciled a REAL row (cutover.json metrics_confirmed).
from fanops.config import Config
from fanops.validation_gate import learning_validated
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
