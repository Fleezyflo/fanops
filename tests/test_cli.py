import json
from fanops.cli import main

def test_main_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0

def test_corrupt_ledger_exits_cleanly_no_traceback(tmp_path, monkeypatch, capsys):
    # A hand-edit typo in ledger.json must NOT brick every command with a raw traceback.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text('{"sources": {,}}')          # not valid JSON
    rc = main(["status"])                                    # status loads the ledger first
    assert rc == 2                                           # clean nonzero (not a crash, not 0)
    err = capsys.readouterr().err
    assert "ledger.json invalid:" in err and "Traceback" not in err

def test_corrupt_accounts_exits_cleanly_no_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text('{"accounts": [oops]}')     # not valid JSON
    rc = main(["advance"])                                   # advance loads accounts via pipeline
    assert rc == 2
    err = capsys.readouterr().err
    assert "accounts.json invalid:" in err and "Traceback" not in err

def test_active_account_missing_id_caught_before_run(tmp_path, monkeypatch, capsys):
    # README promise: "An empty account_id on an active account is caught before a run."
    # advance/run must refuse up front with the readable problem from Accounts.validate().
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "", "platforms": ["instagram"], "status": "active"}]}))
    rc = main(["advance"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "account_id" in err and "@x" in err and "Traceback" not in err

def test_status_tolerates_incomplete_accounts(tmp_path, monkeypatch):
    # An active-but-incomplete account is a *run* blocker, not a reason to brick read-only
    # commands. status must still report (validate() is only gated on advance/run).
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "", "platforms": ["instagram"], "status": "active"}]}))
    assert main(["status"]) == 0

def test_main_has_track_adjust_gc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # these subcommands must exist (FIX F04) — they no-op cleanly on an empty ledger
    assert main(["track"]) == 0
    assert main(["adjust"]) == 0
    assert main(["gc"]) == 0

def test_run_halts_cleanly_on_advance_error(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    # make advance raise (simulating e.g. a fatal auth error escaping publish_due)
    mocker.patch.object(cli, "advance", side_effect=RuntimeError("Blotato 401 unauthorized"))
    rc = cli.main(["run"])
    assert rc == 1                                   # halted cleanly with nonzero, no traceback

def test_gc_removes_old_analyzed_clip_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import os, time
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "old.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"X")
    old = time.time() - 60 * 86400                   # 60 days old
    os.utime(f, (old, old))
    led.add_clip(Clip(id="cold", parent_id="m", path=str(f), state=ClipState.analyzed))
    led.save()
    from fanops.cli import main
    rc = main(["gc", "--keep-days", "30"])
    assert rc == 0 and not f.exists()                # the 60d-old analyzed clip file removed
