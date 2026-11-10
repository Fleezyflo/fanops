# C1-F01: an unrestorable snapshot on the wipe path surfaces through get_logger, not stdlib logging.
import json

from fanops.config import Config
from fanops import ledger_wipe


def test_unrestorable_snapshot_surfaces_through_get_logger(tmp_path):
    cfg = Config(root=tmp_path)
    unrestorable = tmp_path / "snap_dir"
    unrestorable.mkdir()
    assert ledger_wipe.snapshot_is_restorable(unrestorable, cfg=cfg) is False
    rec = json.loads(cfg.log_path.read_text().splitlines()[0])
    assert rec["stage"] == "ledger_wipe"
    assert rec["outcome"] == "snapshot_unrestorable"
