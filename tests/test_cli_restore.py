# tests/test_cli_restore.py — S01c: `fanops restore` exposes the reversible half of `fanops wipe`.
import json
from fanops.cli import main
from fanops.config import Config
from fanops.ledger import Ledger
from tests.test_ledger_wipe import _live_shaped


def test_restore_reverses_a_wipe_through_the_cli(tmp_path, monkeypatch, capsys):
    """End-to-end: `fanops wipe` prints its pre-wipe snapshot path; `fanops restore <that path>` brings
    the wiped rows back. This is the operator half of execute_wipe's reversibility promise, which before
    S01c reached no CLI or Studio path (RC-4: Ledger.restore_snapshot had zero production callers)."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    assert main(["wipe", "--i-understand-this-clears-unshipped-content"]) == 0
    snap = json.loads(capsys.readouterr().out)["snapshot"]          # the path the wipe printed
    assert "p_drop" not in Ledger.load(cfg).posts                   # the wipe removed it
    assert main(["restore", snap]) == 0                             # the exposed verb reverses it
    restored = Ledger.load(cfg).posts
    assert "p_drop" in restored and "p_keep" in restored            # both rows are back
    assert '"outcome":"restored"' in capsys.readouterr().err        # get_logger confirmation (-> run.log + stderr)


def test_restore_missing_snapshot_exits_2_clean(tmp_path, monkeypatch, capsys):
    """A snapshot path that does not exist is a ControlFileError -> main() renders a one-line exit-2
    (no traceback), so a typo'd path degrades cleanly rather than crashing."""
    monkeypatch.chdir(tmp_path)
    _live_shaped(Config(root=tmp_path))                             # healthy live db; only the path is wrong
    rc = main(["restore", str(tmp_path / "00_control" / "ledger.snapshot.nope.sqlite")])
    assert rc == 2
    assert "snapshot" in capsys.readouterr().err.lower()
