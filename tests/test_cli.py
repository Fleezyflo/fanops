import json
import pytest
from fanops.cli import main

def test_hashtags_cli_help_is_safari_sidecar(capsys):
    with pytest.raises(SystemExit) as e:
        main(["hashtags", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "source-lock measurement cache (instagrapi envelope)" in out
    assert "chrome" not in out.lower()
    with pytest.raises(SystemExit) as e:
        main(["hashtags", "refresh", "--help"])
    assert e.value.code == 0
    refresh = capsys.readouterr().out
    assert "remesure sidecar pile and lock names now via instagrapi envelope" in refresh
    assert "harvest" not in refresh.lower()
    assert "Safari" not in refresh
    with pytest.raises(SystemExit) as e:
        main(["hashtags", "scrape-login", "--help"])
    assert e.value.code == 0
    login = capsys.readouterr().out
    assert "instagrapi password login and promote the device envelope" in login
    assert "Chrome" not in login
    assert "Safari" not in login
    with pytest.raises(SystemExit) as e:
        main(["hashtags", "discover", "--help"])
    assert e.value.code == 0
    disc = capsys.readouterr().out
    assert "report each source lock (read-only, zero network)" in disc


def test_gates_blocked_note_flags_remaining_gates():
    # After the run loop, gates still awaiting must produce a LOUD, distinct signal — not be buried
    # in the summary. (B1 already makes a sustained rate-limit halt loud; this covers gates that stay
    # stuck for any reason after the iteration cap.)
    from fanops.cli import _gates_blocked_note
    assert _gates_blocked_note({"awaiting": {"moments": 2, "captions": 0}}) is not None
    assert "BLOCKED" in _gates_blocked_note({"awaiting": {"moments": 0, "captions": 3}})
    assert _gates_blocked_note({"awaiting": {"moments": 0, "captions": 0}}) is None   # converged -> quiet
    assert _gates_blocked_note(None) is None                                          # no status -> quiet

def test_main_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0

def test_main_learn_doctor_dispatches_read_only(tmp_path, monkeypatch, capsys):
    # `fanops learn doctor` wires to the read-only field-shape verdict; on a default (dryrun) backend
    # it logs guidance and exits 0 without touching the network (MOL-358: structured log, not stdout).
    from fanops.config import Config
    monkeypatch.chdir(tmp_path)
    assert main(["learn", "doctor"]) == 0
    log = Config(root=tmp_path).log_path.read_text().lower()
    assert "postiz" in log

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

def test_pull_exits_cleanly_when_ytdlp_hangs(tmp_path, monkeypatch, capsys):
    # A hung yt-dlp (dead network, stalled CDN) is killed at download_url's hard bound; the
    # TimeoutExpired reaches cli.main (pull runs pre-Source, outside any quarantine), which must
    # print ONE operator-actionable line + exit 2 — never a raw traceback, never an infinite hang.
    monkeypatch.chdir(tmp_path)
    import subprocess
    def hung(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 600))
    monkeypatch.setattr("fanops.ingest.subprocess.run", hung)
    rc = main(["pull", "https://example.com/v"])
    assert rc == 2                                           # clean nonzero, not a crash
    err = capsys.readouterr().err
    assert "timed out" in err and "Traceback" not in err

def test_corrupt_ledger_exits_cleanly_no_traceback(tmp_path, monkeypatch, capsys):
    # A hand-edit typo in ledger.json must NOT brick every command with a raw traceback.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_bytes(b'{"sources": {,}}')
    rc = main(["status"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ledger.sqlite invalid:" in err and "Traceback" not in err

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
    assert "account_id" in err and "x" in err and "Traceback" not in err

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
    led.add_post(Post(id="prec", parent_id="c", account="a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.needs_reconcile, public_url="dryrun://prec"))
    led.save()
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0 and "needs_reconcile=1" in out

def test_status_surfaces_moments_empty(tmp_path, monkeypatch, capsys):
    # V2 M1/F8: a source the model produced ZERO picks for is actionable (re-runnable via
    # retry-source). `fanops status` surfaces the count so under-production is never silent.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x.mp4", state=SourceState.moments_empty))
    led.save()
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0 and "moments_empty=1" in out

def test_status_surfaces_error_sources(tmp_path, monkeypatch, capsys):
    # Audit: a source parked SourceState.error (e.g. a transient whisper model-download failure) is NOT
    # auto-retried by design — `fanops status` must surface it so the operator knows to run retry-source.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x.mp4", state=SourceState.error,
                          error_reason="whisper produced no JSON (rc=1): connection error fetching model"))
    led.save()
    rc = main(["status"])
    assert rc == 0 and "sources_error=1" in capsys.readouterr().out

def test_retry_source_re_runs_a_moments_empty_source(tmp_path, monkeypatch):
    # V2 M1/F8 + audit H7 (guard): a moments_empty source (model produced nothing) MUST stay
    # re-runnable. retry-source resets it to catalogued so the next run re-transcribes + re-requests.
    # This locks the behavior: if a state-guard is ever added to retry-source, moments_empty must not
    # become silently stranded.
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x.mp4", state=SourceState.moments_empty))
    led.save()
    assert main(["retry-source", "s1"]) == 0
    assert Ledger.load(cfg).sources["s1"].state is SourceState.catalogued

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
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p"))
    led.save()
    rc = main(["reconcile"])
    assert rc == 0
    out = capsys.readouterr().out
    assert Ledger.load(cfg).posts["p"].state is PostState.needs_reconcile  # no provider -> not polled
    assert "reconciled" in out


# --- T1: startup preflight auth-check (the silent-zero-output guard) ------------------------
# Catches the #1 cutover trap BEFORE a run does silent nothing. AUTH (2026-06-04): the responder
# uses the operator's EXISTING `claude` subscription (plain `claude -p`, NOT `--bare`/API key), so
# the llm trap is "FANOPS_RESPONDER=llm but `claude` is NOT on PATH" (no binary -> every gate raises
# -> zero content), NOT a missing ANTHROPIC_API_KEY. The poster trap is unchanged: rest/mcp with no
# BLOTATO_API_KEY (publish 401). Mirrors _check_accounts: 0 clean / actionable line + returns 2.
# Tests patch shutil.which to control `claude` presence deterministically.

def test_preflight_blocks_llm_when_claude_absent(tmp_path, monkeypatch, mocker, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)     # isolate the assertion to the llm case
    mocker.patch("shutil.which", return_value=None)          # the trap: llm responder, no `claude`
    from fanops.config import Config
    from fanops.cli import _check_preflight
    rc = _check_preflight(Config(root=tmp_path))
    assert rc == 2
    err = capsys.readouterr().err
    assert "claude" in err and "claude login" in err and "Traceback" not in err


def test_preflight_blocks_llm_when_claude_absent_via_advance(tmp_path, monkeypatch, mocker, capsys):
    # Wiring proof: the gate is actually called in the advance dispatch branch (after _check_accounts).
    # A valid active account makes _check_accounts pass so we reach _check_preflight.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    mocker.patch("shutil.which", return_value=None)
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    rc = main(["advance"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "claude" in err and "Traceback" not in err


def test_preflight_refuses_manual_responder(tmp_path, monkeypatch, capsys):
    # The no-op manual responder was retired: FANOPS_RESPONDER=manual is a HARD REFUSE at preflight
    # (exit 2 + one clean stderr line), NEVER a silent no-LLM run. There is no manual mode to fall back to.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 2
    err = capsys.readouterr().err
    assert "FANOPS_RESPONDER" in err and "Traceback" not in err


def test_preflight_blocks_default_llm_when_claude_absent(tmp_path, monkeypatch, mocker, capsys):
    # Fail closed: empty/unset FANOPS_RESPONDER → llm, so missing CLI refuses the same as explicit llm.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    mocker.patch("shutil.which", return_value=None)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    rc = _check_preflight(Config(root=tmp_path))
    assert rc == 2
    err = capsys.readouterr().err
    assert "claude" in err and "Traceback" not in err


def test_preflight_passes_llm_when_claude_present_no_api_key(tmp_path, monkeypatch, mocker):
    # The correctly-configured live cutover with the EXISTING subscription: FANOPS_RESPONDER=llm,
    # `claude` ON PATH, NO ANTHROPIC_API_KEY (we ride OAuth, not a key), postiz + its key.
    # Must pass — the gate blocks only the absent-`claude` case, and crucially does NOT require an
    # API key (that was the old --bare contract this change removes).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # NO api key — riding the subscription
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    mocker.patch("shutil.which", return_value="/usr/local/bin/claude")  # claude IS logged-in-capable
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 0


def test_preflight_blocks_cursor_when_cursor_agent_absent(tmp_path, monkeypatch, mocker, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "cursor")
    mocker.patch("shutil.which", side_effect=lambda b: "/usr/local/bin/claude" if b == "claude" else None)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 2
    err = capsys.readouterr().err
    assert "cursor-agent" in err and "Traceback" not in err


def test_preflight_cursor_blocks_when_vision_unsupported(tmp_path, monkeypatch, mocker, capsys):
    # Absolute transport: cursor cannot run vision gates — refuse until Go-Live flips to claude.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "cursor")
    mocker.patch("shutil.which", side_effect=lambda b: "/usr/local/bin/cursor-agent" if b == "cursor-agent" else None)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 2
    err = capsys.readouterr().err
    assert "vision" in err.lower() and "go-live" in err.lower() and "fallback" in err.lower()




def test_preflight_cursor_blocks_even_if_claude_present(tmp_path, monkeypatch, mocker, capsys):
    # Having claude on PATH must NOT paper over transport=cursor — no silent dual-CLI.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "cursor")
    mocker.patch("shutil.which", return_value="/usr/local/bin/x")
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 2
    err = capsys.readouterr().err.lower()
    assert "go-live" in err and "fallback" in err

def _write_active_account(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    return cfg


def test_run_learning_pass_is_guarded_to_live_backends(tmp_path, monkeypatch):
    # Dryrun / live-off must not pull_metrics. Observe ledger posts + learn-pass log, not a spy.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    from fanops.ledger import Ledger
    cfg = _write_active_account(tmp_path)
    Ledger.load(cfg).save()
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    after = Ledger.load(cfg)
    assert all(not p.metrics for p in after.posts.values())
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "amplify_skipped" not in blob
    assert "learn degrade" not in blob


def test_run_learning_pass_not_entered_in_dryrun(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.ledger import Ledger
    cfg = _write_active_account(tmp_path)
    Ledger.load(cfg).save()
    rc = main(["run", "--base-time", "2026-06-02T18:00:00Z"])
    assert rc == 0
    assert all(not p.metrics for p in Ledger.load(cfg).posts.values())
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "amplify_skipped" not in blob

def test_run_learning_pass_entered_with_live_backend_and_key(tmp_path, monkeypatch, mocker):
    # Empty ledger: no pollable posts, so learn_pass does no network. amplify_skipped in the log
    # is the breadcrumb that the live+keyed guard entered the pass (dryrun sibling asserts absent).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k-test")
    mocker.patch("shutil.which", side_effect=lambda b: "/usr/local/bin/claude" if b == "claude" else None)
    from fanops.ledger import Ledger
    cfg = _write_active_account(tmp_path)
    Ledger.load(cfg).save()
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "amplify_skipped" in blob

def test_run_learning_pass_entered_with_postiz_backend_and_key(tmp_path, monkeypatch, mocker):
    mocker.patch("shutil.which", side_effect=lambda b: "/usr/local/bin/claude" if b == "claude" else None)
    cfg = _live_postiz_empty_tree(tmp_path, monkeypatch)
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "amplify_skipped" in blob

def _live_postiz_empty_tree(tmp_path, monkeypatch):
    """Live postiz+key, empty ledger (no pollable posts → learn_pass does no network). Real apply."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk-test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.ledger import Ledger
    cfg = _write_active_account(tmp_path)
    Ledger.load(cfg).save()
    return cfg


def test_run_fires_p4_dim_bias_when_flag_on_and_live(tmp_path, monkeypatch):
    # Real apply_p4_dim_bias (no patch). Unvalidated cutover → skipped_unvalidated log, no new sources.
    monkeypatch.setenv("FANOPS_P4_DIM_BIAS", "1")
    cfg = _live_postiz_empty_tree(tmp_path, monkeypatch)
    from fanops.ledger import Ledger
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "skipped_unvalidated" in blob
    assert list(Ledger.load(cfg).sources) == []


def test_run_skips_p4_dim_bias_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_P4_DIM_BIAS", raising=False)
    cfg = _live_postiz_empty_tree(tmp_path, monkeypatch)
    from fanops.ledger import Ledger
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    blob = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "skipped_unvalidated" not in blob
    assert list(Ledger.load(cfg).sources) == []

def test_run_prints_heartbeat_with_version(tmp_path, monkeypatch, capsys):
    # B5/E2: every `fanops run` must emit a heartbeat line on stdout carrying the fanops version,
    # so a monitor diffing consecutive lines can distinguish 'alive-but-idle' from 'cron is dead'.
    # Today fanops.__version__ is undefined (AttributeError) and no heartbeat line is printed.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)       # dryrun backend
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    out = capsys.readouterr().out
    import fanops
    assert fanops.__version__ in out
    assert "heartbeat" in out

def _heartbeat_value(out: str) -> str:
    # Extract the "heartbeat" ts from the single JSON heartbeat line on stdout.
    line = next(ln for ln in out.splitlines() if '"heartbeat"' in ln)
    return json.loads(line)["heartbeat"]

def test_run_heartbeat_timestamp_changes_between_runs(tmp_path, monkeypatch, capsys):
    # B5/E2 (mutation-proven dead-man's-switch): the heartbeat ts is the load-bearing signal — an
    # external monitor diffing consecutive lines reads 'cron is dead' iff the ts STOPS advancing.
    # The hollow committed test only checks the constant JSON key "heartbeat" is present, which a
    # FROZEN ts (the exact B5 'dead-cron-looks-alive' regression) still satisfies. This test runs
    # `run` TWICE and asserts the two heartbeat ts VALUES DIFFER — freezing the ts in cli._heartbeat
    # makes it FAIL. (datetime.now(timezone.utc).isoformat() is microsecond-resolution, so two real
    # invocations always differ; we also assert each is a parseable ISO timestamp, not a constant.)
    from datetime import datetime
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)       # dryrun backend
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))

    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    hb1 = _heartbeat_value(capsys.readouterr().out)
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    hb2 = _heartbeat_value(capsys.readouterr().out)

    # both are real ISO timestamps (a frozen constant string would not be monotonic) ...
    t1, t2 = datetime.fromisoformat(hb1), datetime.fromisoformat(hb2)
    # ... and the ts ADVANCED run-to-run: a frozen ts (B5 regression) gives hb1 == hb2 -> FAIL here.
    assert hb1 != hb2
    assert t2 >= t1

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

def test_resolve_promotes_a_needs_reconcile_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t", public_url="dryrun://p1"))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p", "--submission-id", "blotato_9"]) == 0
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert p.state is PostState.published and p.public_url == "https://x/p"
    assert p.submission_id == "blotato_9"

def test_resolve_with_submission_id_sets_tracking_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform, is_real_submission_id
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t",
                          reconcile_candidate_id="cand-99", public_url=""))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p", "--submission-id", "zernio-real-42"]) == 0
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.published
    assert p.public_url == "https://x/p"
    assert p.submission_id == "zernio-real-42"
    assert is_real_submission_id(p.submission_id)
    assert p.published_at
    assert p.publish_hour is not None and p.publish_dow is not None
    assert p.reconcile_candidate_id is None
    assert list(cfg.published.rglob("p1.json")), "resolve with sid must archive like reconcile promote"

def test_resolve_with_submission_id_pull_metrics_binds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    from fanops.track import _metrics_trackable, pull_metrics
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.failed, submission_id="fanops_t", public_url=""))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p", "--submission-id", "zernio-real-42"]) == 0
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert _metrics_trackable(cfg, p.submission_id)
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "zernio-real-42", "metrics": {"likes": 3.0}}])
    assert led.posts["p1"].metrics.get("likes") == 3.0

def test_resolve_rejects_fanops_submission_id(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t", public_url=""))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p", "--submission-id", "fanops_dead"]) == 2
    assert "fanops_" in capsys.readouterr().err
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.needs_reconcile and p.submission_id == "fanops_t"

def test_unhold_resets_a_held_clip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.held, held=True,
                          held_reason="brand risk"))
    from fanops.cli import main
    assert main(["unhold", "c1"]) == 0
    c = Ledger.load(cfg).clips["c1"]
    assert c.state is ClipState.captions_requested and c.held is False

def test_retry_source_resets_error_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="toolchain missing: ffmpeg"))
    from fanops.cli import main
    assert main(["retry-source", "s1"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.error_reason is None


# ── Phase F hardening (the adversarial skeptics found these paths correct in the live
# binary but UNCOVERED — deleting a guard / mis-mapping a branch passed the suite undetected,
# the exact H9 blind spot. These pin the unknown-id exits, the resolve `failed` branch, the
# retry-source re-transcribe flag, and the retry-metrics published/not-published split.) ──

def test_resolve_can_fail_a_post_and_unknown_id_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t", public_url="dryrun://p1"))
    from fanops.cli import main
    # the `failed` branch (the committed test only exercised `published` -> a mis-map slipped through)
    assert main(["resolve", "p1", "failed"]) == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.failed
    # unknown post -> clean exit 2 + stderr, NOT a KeyError traceback
    assert main(["resolve", "nope", "published"]) == 2
    assert "no such post: nope" in capsys.readouterr().err


def test_unhold_unknown_clip_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.cli import main
    Config(root=tmp_path)
    assert main(["unhold", "nope"]) == 2          # guard fires -> exit 2, not an uncaught KeyError
    assert "no such clip: nope" in capsys.readouterr().err


def test_retry_source_forces_real_retranscribe_and_unknown_id_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        # a source that ALREADY transcribed once (meta flag set) — a state-only reset would
        # re-enter the pipeline but transcribe.py:82 SKIPS it (meta.transcribed is True), leaving
        # a stale transcript. retry-source must clear the flag to force a real re-transcribe.
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="boom", meta={"transcribed": True}))
    from fanops.cli import main
    assert main(["retry-source", "s1"]) == 0
    assert Ledger.load(cfg).sources["s1"].meta["transcribed"] is False
    # unknown source -> clean exit 2
    assert main(["retry-source", "nope"]) == 2
    assert "no such source: nope" in capsys.readouterr().err


def test_retry_metrics_published_vs_not_vs_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="pub", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.published, submission_id="s", public_url="dryrun://pub"))
        led.add_post(Post(id="que", parent_id="c2", account="a", account_id="1", platform=Platform.instagram,
                          caption="y", state=PostState.queued, public_url="dryrun://que"))
    from fanops.cli import main
    # published -> exit 0, and the post STAYS published so the next `track` re-pulls (no state flip)
    assert main(["retry-metrics", "pub"]) == 0
    assert Ledger.load(cfg).posts["pub"].state is PostState.published
    # not published -> exit 2 with the state in the message
    assert main(["retry-metrics", "que"]) == 2
    assert "not published" in capsys.readouterr().err
    # unknown post -> exit 2
    assert main(["retry-metrics", "nope"]) == 2
    assert "no such post: nope" in capsys.readouterr().err


def test_discover_and_intake_via_cli(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config

    def _media_run(cmd, **kw):
        if cmd and cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"J")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd and cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout="0\n0\n0.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr("fanops.media_probe.subprocess.run", _media_run)
    monkeypatch.setattr("fanops.discover.subprocess.run", _media_run)
    bank = tmp_path / "bank"; bank.mkdir()
    (bank / "v.mp4").write_bytes(b"VID")
    from fanops.cli import main
    assert main(["discover", str(bank)]) == 0
    cfg = Config(root=tmp_path)
    assert (cfg.review / "manifest.json").exists()
    from fanops.ingest import sha256_of
    eid = sha256_of(bank / "v.mp4")[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    assert main(["intake"]) == 0
    assert (cfg.inbox / "v.mp4").exists()

def test_discover_unknown_folder_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    assert main(["discover", str(tmp_path / "nope")]) == 2
    assert "no such" in capsys.readouterr().err.lower() and "Traceback" not in capsys.readouterr().err


def test_amplify_variants_verb_runs_and_is_noop_below_gate(tmp_path, monkeypatch, capsys):
    # The verb is registered, runs clean, and (empty ledger) amplifies nothing.
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    Ledger.load(Config(root=tmp_path)).save()            # empty ledger on disk
    rc = main(["amplify-variants"])
    assert rc == 0
    assert "variant-amplify" in capsys.readouterr().out  # printed a summary line


def test_amplify_variants_inert_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_AMPLIFY", raising=False)
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    before = sorted(Ledger.load(cfg).sources)
    assert main(["amplify-variants"]) == 0
    assert sorted(Ledger.load(cfg).sources) == before


def test_studio_refuses_non_loopback_host(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    assert cli.main(["studio", "--host", "0.0.0.0", "--dev-reload"]) == 2
    assert "loopback" in capsys.readouterr().err.lower()


def test_studio_refuses_unmanaged_foreground(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli

    assert cli.main(["studio", "--host", "127.0.0.1", "--port", "9999"]) == 2

    err = capsys.readouterr().err
    assert "unmanaged foreground Studio" in err
    assert "--install" in err
    assert "--dev-reload" in err


def test_studio_app_refuses_when_not_serving(tmp_path, monkeypatch, capsys):
    import socket
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
    finally:
        probe.close()
    rc = cli.main(["studio", "--app", "--host", host, "--port", str(port)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "unmanaged foreground" not in err
    assert "--install" in err


def test_studio_port_busy_probes_liveness(tmp_path):
    import socket
    import fanops.cli as cli

    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        host, port = srv.getsockname()
        assert cli._studio_port_busy(host, port) is True
        srv.close()
        assert cli._studio_port_busy(host, port) is False
    finally:
        srv.close()


def test_studio_managed_guard_trips_when_port_serving(
    tmp_path,
    monkeypatch,
    capsys,
):
    import socket
    import sys as _sys

    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    monkeypatch.setattr(_sys, "platform", "darwin")
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        host, port = srv.getsockname()
        assert cli.main(["studio", "--managed", "--host", host, "--port", str(port)]) == 0
        assert "already serving" in capsys.readouterr().out
    finally:
        srv.close()


def test_pull_rejects_non_http_url(tmp_path, monkeypatch, capsys):
    # Stage-4 audit: the pull url is handed to yt-dlp verbatim. Validate the scheme at the argparse
    # boundary: file:///-style schemes and flag-lookalike args ('-foo', argument injection into
    # yt-dlp) must die with the standard argparse usage error (exit 2), never reach a subprocess.
    import pytest
    monkeypatch.chdir(tmp_path)
    for bad in ("file:///etc/passwd", "ftp://x/y", "-U"):
        with pytest.raises(SystemExit) as ei:
            main(["pull", bad])
        assert ei.value.code == 2


def test_gc_surfaces_oserror_not_silent(tmp_path, monkeypatch, capsys, mocker):
    # FIX 9: cmd_gc's `except OSError: pass` hid a failed clip removal (could mask a disk-fill / perms
    # problem). An OSError during removal must be surfaced to stderr; gc still completes (exit 0).
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState, Fmt
    cfg = Config(root=tmp_path)
    clip_file = tmp_path / "old_clip.mp4"; clip_file.write_bytes(b"X")
    import os as _os
    _os.utime(clip_file, (0, 0))                              # mtime far in the past -> past the cutoff
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c_old", parent_id="m", path=str(clip_file), aspect=Fmt.r9x16,
                      state=ClipState.analyzed))
    led.save()
    mocker.patch("os.remove", side_effect=OSError(13, "Permission denied"))
    rc = main(["gc", "--keep-days", "1"])                    # keep-days 0 is now refused (wipe-safety); 1 still sweeps the epoch-mtime file
    assert rc == 0                                            # gc still completes
    err = capsys.readouterr().err
    assert "gc:" in err and "old_clip.mp4" in err            # the failed removal is surfaced, not silent


def test_run_learn_block_logs_auth_error_with_type_name(tmp_path, monkeypatch, mocker):
    # A 401 from the real Postiz analytics GET is AuthError; the unattended run stays 0 and logs the type.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk-test")
    mocker.patch("shutil.which", side_effect=lambda b: "/usr/local/bin/claude" if b == "claude" else None)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="x", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.published, submission_id="sid-live-1",
                          public_url="https://instagram.com/p/x"))
    class _R:
        status_code = 401
        text = "denied"
        def json(self):
            return {}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R())
    rc = main(["run", "--base-time", "2026-06-02T18:00:00Z"])
    assert rc == 0
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "PostizAuthError" in log

def test_main_hashtags_refresh_writes_the_cache_failopen(tmp_path, monkeypatch, capsys):
    # Without scrape session Layer A aborts loudly (exit 2) and leaves the cache untouched.
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["hashtags", "refresh"]) == 2
    log = Config(root=tmp_path).log_path.read_text().lower()
    compact = log.replace(" ", "")
    assert "refresh_aborted" in compact or "no_scrape" in compact or "aborted" in compact
    assert not (tmp_path / "MohFlow-FanOps" / "00_control" / "hashtags.json").exists()


def test_main_hashtags_verbs_are_refresh_and_discover_only(tmp_path, monkeypatch, capsys):
    # The one-shot `hashtags migrate` verb (the corpus-hygiene sweep) is GONE with the curated corpus it
    # rewrote — argparse must REJECT it rather than silently accept an unknown subcommand.
    import pytest
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        main(["hashtags", "migrate"])
    assert ei.value.code == 2                             # argparse "invalid choice", not a silent no-op
