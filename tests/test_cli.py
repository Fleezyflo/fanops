import json
from fanops.cli import main

def test_main_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0

def test_advance_exits_cleanly_when_ffprobe_absent(tmp_path, monkeypatch, capsys):
    # ffprobe missing at ingest (ingest_drops runs OUTSIDE the pipeline quarantine) must NOT crash
    # `fanops advance` with a raw traceback. It surfaces as a typed ToolchainMissingError ->
    # cli.main prints one operator-actionable line ("install ffmpeg") + exit 2, like a config error.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    inbox = cfg.inbox; inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "a.mp4").write_bytes(b"V")                      # a drop so ingest attempts ffprobe
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    monkeypatch.setattr("fanops.ingest.subprocess.run", absent)
    rc = main(["advance"])
    assert rc == 2                                           # clean nonzero, not a crash, not 0
    err = capsys.readouterr().err
    assert "ffprobe" in err and "Traceback" not in err

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

def test_status_surfaces_needs_reconcile(tmp_path, monkeypatch, capsys):
    # AUDIT C1: a post parked in needs_reconcile (ambiguous publish — may be live on the platform)
    # is actionable. The operator running `fanops status` must see it without opening the digest,
    # alongside the published/failed counts.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="prec", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.needs_reconcile))
    led.save()
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0 and "needs_reconcile=1" in out

def test_main_has_track_adjust_gc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # these subcommands must exist (FIX F04) — they no-op cleanly on an empty ledger
    assert main(["track"]) == 0
    assert main(["adjust"]) == 0
    assert main(["gc"]) == 0


def test_reconcile_command_skips_without_key(tmp_path, monkeypatch, capsys):
    # AUDIT H4: `fanops reconcile` needs a key (no live status source in dryrun) — skip cleanly,
    # like track, rather than crash.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x"))
    led.save()
    rc = main(["reconcile"])
    assert rc == 0 and "reconcile skipped" in capsys.readouterr().out


def test_reconcile_command_promotes_published(tmp_path, monkeypatch, capsys, mocker):
    # End-to-end through the CLI with a stubbed status client: a needs_reconcile post with an id
    # is promoted to published.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x"))
    led.save()
    import fanops.cli as cli
    mocker.patch.object(cli, "reconcile_posts",
                        side_effect=lambda led_, cfg_: _promote(led_))
    rc = main(["reconcile"])
    assert rc == 0
    again = Ledger.load(cfg)
    assert again.posts["p"].state is PostState.published


def _promote(led):
    from fanops.models import PostState
    for p in led.posts.values():
        if p.state is PostState.needs_reconcile:
            p.state = PostState.published
    return led

def test_run_halts_cleanly_on_advance_error(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    # make advance raise (simulating e.g. a fatal auth error escaping publish_due)
    mocker.patch.object(cli, "advance", side_effect=RuntimeError("Blotato 401 unauthorized"))
    rc = cli.main(["run"])
    assert rc == 1                                   # halted cleanly with nonzero, no traceback


def test_advance_exits_cleanly_on_auth_error(tmp_path, monkeypatch, mocker, capsys):
    # AUDIT H8: a BlotatoAuthError (bad/missing key) escaping advance is operator-actionable —
    # `fanops advance` must print a clean one-line pointer and exit nonzero, not crash-dump.
    from fanops.errors import BlotatoAuthError
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    mocker.patch.object(cli, "advance", side_effect=BlotatoAuthError("Blotato 401 — check BLOTATO_API_KEY"))
    # advance gates on _check_accounts first; give it a valid active account so we reach advance().
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    rc = cli.main(["advance"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err and "BLOTATO_API_KEY" in err


def test_run_halts_cleanly_when_responder_raises(tmp_path, monkeypatch, mocker, capsys):
    # AUDIT H7: `fanops run` is the REQUIRED unattended mode. If the LLM responder raises
    # (model call error, a malformed response failing validation), the run loop must DEGRADE
    # cleanly — nonzero exit + one log line — not crash the cron loop with a raw traceback.
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    class _Boom:
        def answer_pending(self, cfg):
            raise RuntimeError("LLM responder exploded mid-gate")
    mocker.patch.object(cli, "get_responder", return_value=_Boom())
    rc = cli.main(["run"])
    assert rc == 1                                   # clean nonzero, loop did not crash
    err = capsys.readouterr().err
    assert "Traceback" not in err                    # degraded, not a stack dump
    assert "RuntimeError" in err                     # the one-line halt message names the cause

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
