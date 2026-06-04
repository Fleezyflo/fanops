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
    # is promoted to published. (Phase-B-followup: cmd_reconcile now binds the status poller via
    # _default_get_status and polls OUTSIDE a transaction, then applies inside it — so we stub the
    # poller seam, which exercises the REAL reconcile_posts through the new transactional path,
    # a stronger check than the old stub-out-reconcile_posts version.)
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x"))
    led.save()
    import fanops.cli as cli
    mocker.patch.object(cli, "_default_get_status",
                        return_value=lambda sid: {"status": "published", "publicUrl": "https://x/p"})
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


# --- T1: startup preflight auth-check (the silent-zero-output guard) ------------------------
# Catches the #1 cutover trap BEFORE a run does silent nothing: FANOPS_RESPONDER=llm with no
# ANTHROPIC_API_KEY (claude --bare reads no OAuth/keychain -> zero content, no loud log) and
# a live poster (rest/mcp) with no BLOTATO_API_KEY (publish will 401). Mirrors _check_accounts:
# 0 clean / prints an actionable line + returns 2. Tests call _check_preflight directly to
# isolate from _check_accounts, plus one main(["advance"]) to prove the dispatch wiring.

def test_preflight_blocks_llm_without_anthropic_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # the trap: llm responder, no key
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)     # isolate the assertion to the llm case
    from fanops.config import Config
    from fanops.cli import _check_preflight
    rc = _check_preflight(Config(root=tmp_path))
    assert rc == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "Traceback" not in err


def test_preflight_blocks_llm_without_anthropic_key_via_advance(tmp_path, monkeypatch, capsys):
    # Wiring proof: the gate is actually called in the advance dispatch branch (after _check_accounts).
    # A valid active account makes _check_accounts pass so we reach _check_preflight.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    rc = main(["advance"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "Traceback" not in err


def test_preflight_blocks_rest_without_blotato_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)     # the trap: live poster, no key -> 401
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # isolate the assertion to the poster case
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)    # default manual responder
    from fanops.config import Config
    from fanops.cli import _check_preflight
    rc = _check_preflight(Config(root=tmp_path))
    assert rc == 2
    err = capsys.readouterr().err
    assert "BLOTATO_API_KEY" in err and "Traceback" not in err


def test_preflight_blocks_mcp_without_blotato_key(tmp_path, monkeypatch, capsys):
    # The mcp backend is auth-gated identically to rest — both 401 without the key.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "mcp")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    rc = _check_preflight(Config(root=tmp_path))
    assert rc == 2
    assert "BLOTATO_API_KEY" in capsys.readouterr().err


def test_preflight_passes_default_dryrun_manual(tmp_path, monkeypatch):
    # The default cutover config (manual responder + dryrun poster, no keys) must pass cleanly.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 0


def test_preflight_passes_llm_with_anthropic_key(tmp_path, monkeypatch):
    # The correctly-configured live cutover (llm + key, rest + key) must pass — the gate blocks the
    # MISSING-key case only, never a valid one.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "blot-test")
    from fanops.config import Config
    from fanops.cli import _check_preflight
    assert _check_preflight(Config(root=tmp_path)) == 0


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

def test_run_learning_pass_is_guarded_to_live_backends(tmp_path, monkeypatch):
    # E1 (learning_pass_guard): the new post-loop learning pass (pull_metrics -> classify ->
    # amplify -> retire) runs ONLY when poster_backend != "dryrun" AND blotato_api_key is set
    # (the identical reconcile guard at pipeline.py:106). In dryrun (the default, FANOPS_POSTER
    # unset) the guard short-circuits, the pass is never entered, and `run` still converges and
    # exits 0 — a regression guard that the learning pass does NOT run in dryrun and does NOT
    # break run's exit code.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)       # dryrun backend
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0

def test_run_learning_pass_not_entered_in_dryrun(tmp_path, monkeypatch, mocker):
    # E1 HARDEN (mutation-proven): the exit==0 assertion above is BLIND to whether the learning
    # pass actually ran — with the guard removed (`if True:`), pull_metrics in dryrun-no-key raises
    # RuntimeError from BlotatoMetricsClient.__init__, which the swallow-all `except Exception`
    # (cli.py:211) eats, so the exit code STAYS 0 and the hollow test still passes. This test binds
    # the real guarantee by SPYING on the learning-pass entry point: spy fanops.cli.pull_metrics
    # (so even an ungated body cannot reach the real client) and assert it is NEVER called in
    # dryrun. Removing/weakening the cli.py:204 guard makes this FAIL (spy.call_count==1).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)       # dryrun backend (default)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)     # no key
    import fanops.cli as cli
    spy = mocker.patch.object(cli, "pull_metrics", side_effect=lambda led, cfg, **kw: led)
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    rc = main(["run", "--base-time", "2026-06-02T18:00:00Z"])
    assert rc == 0                                            # run still converges + exits 0
    assert spy.call_count == 0                                # the learning pass is NEVER entered in dryrun

def test_run_learning_pass_entered_with_live_backend_and_key(tmp_path, monkeypatch, mocker):
    # E1 HARDEN (positive branch): with a LIVE backend (FANOPS_POSTER=rest) AND a key set, the
    # guard's true-branch fires and the learning pass runs EXACTLY ONCE per run. pull_metrics /
    # classify_outcomes / amplify / retire are all spied to harmless no-ops so nothing touches the
    # network. This pins that the guard is a real branch (not dead code): flip either condition off
    # and the spy stops being called (covered by the dryrun test above).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "rest")              # live backend
    monkeypatch.setenv("BLOTATO_API_KEY", "k-test")          # key present
    import fanops.cli as cli
    spy = mocker.patch.object(cli, "pull_metrics", side_effect=lambda led, cfg, **kw: led)
    mocker.patch.object(cli, "classify_outcomes", return_value={"winners": [], "losers": []})
    mocker.patch.object(cli, "amplify", side_effect=lambda led, cfg, winners, **kw: led)
    mocker.patch.object(cli, "retire", side_effect=lambda led, losers, **kw: led)
    from fanops.config import Config
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    rc = main(["run", "--base-time", "2026-06-02T18:00:00Z"])
    assert rc == 0
    assert spy.call_count == 1                                # learning pass runs once when live+keyed

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
    line = next(l for l in out.splitlines() if '"heartbeat"' in l)
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
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t"))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p"]) == 0
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published and led.posts["p1"].public_url == "https://x/p"

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
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t"))
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
        led.add_post(Post(id="pub", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.published, submission_id="s"))
        led.add_post(Post(id="que", parent_id="c2", account="@a", account_id="1", platform=Platform.instagram,
                          caption="y", state=PostState.queued))
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
