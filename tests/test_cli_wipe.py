# tests/test_cli_wipe.py — MOL-223: fanops wipe CLI (read-only preview + snapshot-gated execute).
import json
from fanops.cli import main
from fanops.config import Config
from fanops.ledger import Ledger
from tests.test_ledger_wipe import _live_shaped


def test_wipe_preview_is_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    before = Ledger.load(cfg)._to_doc()
    assert main(["wipe"]) == 0
    assert Ledger.load(cfg)._to_doc() == before
    out = json.loads(capsys.readouterr().out)
    assert out["kept_posts"] == 1 and set(out["post_ids"]) == {"p_drop"}


def test_wipe_execute_refuses_without_scoped_confirm(tmp_path, monkeypatch, capsys):
    # --include-shipped-history without the total erase confirm is a refused execute attempt, not preview.
    monkeypatch.chdir(tmp_path)
    _live_shaped(Config(root=tmp_path))
    assert main(["wipe", "--include-shipped-history"]) == 2
    err = capsys.readouterr().err
    assert "i-understand-this-erases-shipped-history" in err.lower()


def test_wipe_total_mode_refuses_without_erase_confirm(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _live_shaped(Config(root=tmp_path))
    rc = main(["wipe", "--include-shipped-history", "--i-understand-this-clears-unshipped-content"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "i-understand-this-erases-shipped-history" in err.lower()


def test_wipe_scoped_happy_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    assert main(["wipe", "--i-understand-this-clears-unshipped-content"]) == 0
    led = Ledger.load(cfg)
    assert "p_drop" not in led.posts and "p_keep" in led.posts
    out = json.loads(capsys.readouterr().out)
    assert out["removed"]["posts"] == 1 and "snapshot" in out
