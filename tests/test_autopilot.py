"""Tests for `fanops autopilot` — the one-command 'make me autonomous' verb. set_env_var is the
idempotent .env updater (MUST preserve other keys/secrets); autopilot enables the llm responder,
optionally installs the daemon (mocked launchctl, HOME/platform sandboxed), and reports readiness
with NO Blotato dependency (dryrun by default). os.environ mutation is guarded per-test via
monkeypatch.setenv so it never leaks."""
from __future__ import annotations
import subprocess

from fanops.config import Config
from fanops import autopilot, daemon


def _fake_launchctl(**spec):
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


# ── set_env_var (idempotent .env updater) ────────────────────────────────────────────────────

def test_set_env_var_creates_file_when_absent(tmp_path):
    env = tmp_path / ".env"
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    assert "FANOPS_RESPONDER=llm" in env.read_text()


def test_set_env_var_preserves_other_lines(tmp_path):
    # The .env holds secrets (POSTIZ_API_KEY etc.) — setting one key must NEVER drop the others.
    env = tmp_path / ".env"
    env.write_text("POSTIZ_API_KEY=s3cret\nFANOPS_WHISPER_MODEL=turbo\n")
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    body = env.read_text()
    assert "POSTIZ_API_KEY=s3cret" in body
    assert "FANOPS_WHISPER_MODEL=turbo" in body
    assert "FANOPS_RESPONDER=llm" in body


def test_set_env_var_updates_in_place_no_duplicate(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FANOPS_RESPONDER=manual\n")
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    body = env.read_text()
    assert "FANOPS_RESPONDER=llm" in body
    assert "manual" not in body
    assert body.count("FANOPS_RESPONDER") == 1            # updated in place, not appended


def test_set_env_var_updates_export_prefixed_line(tmp_path):
    # python-dotenv accepts `export KEY=value`; updating must match it in place (keeping the export
    # prefix) instead of appending a confusing duplicate that shadows it.
    env = tmp_path / ".env"
    env.write_text("export FANOPS_RESPONDER=manual\n")
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    body = env.read_text()
    assert "export FANOPS_RESPONDER=llm" in body
    assert body.count("FANOPS_RESPONDER") == 1            # one line, updated — not a duplicate
    assert "manual" not in body


def test_set_env_var_handles_spaces_and_skips_comment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# FANOPS_RESPONDER=commented\nFANOPS_RESPONDER = manual\n")
    autopilot.set_env_var(env, "FANOPS_RESPONDER", "llm")
    body = env.read_text()
    assert "# FANOPS_RESPONDER=commented" in body          # comment preserved, not treated as the key
    assert "FANOPS_RESPONDER=llm" in body
    assert "= manual" not in body                          # the real (spaced) assignment was updated


# ── autopilot ─────────────────────────────────────────────────────────────────────────────────

def test_autopilot_enables_llm_and_installs_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")       # baseline; monkeypatch restores after
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)

    res = autopilot.autopilot(cfg, interval=600, install_daemon=True)

    assert "FANOPS_RESPONDER=llm" in (tmp_path / ".env").read_text()   # durable across future runs
    assert res["responder"] == "llm"
    assert res["daemon"]["loaded"] is True
    assert daemon.plist_path().exists()


def test_autopilot_no_daemon_skips_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=False)
    assert res["daemon"] is None
    assert not daemon.plist_path().exists()
    assert "FANOPS_RESPONDER=llm" in (tmp_path / ".env").read_text()   # llm still enabled


def test_autopilot_off_darwin_enables_llm_but_skips_daemon(tmp_path, monkeypatch):
    # Non-darwin: still enable autonomy (llm), but the launchd agent is skipped with a note, not a crash.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=True)
    assert res["responder"] == "llm"
    assert res["daemon"] is None
    assert res["daemon_note"] and "macOS" in res["daemon_note"]


def test_autopilot_reports_dryrun_and_no_blotato_requirement(tmp_path, monkeypatch):
    # No Blotato: in dryrun the readiness report must NOT demand a BLOTATO_API_KEY.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=False)
    assert res["backend"] == "dryrun"
    assert not any("BLOTATO" in c["label"].upper() for c in res["checks"])


# ── CLI wiring ──────────────────────────────────────────────────────────────────────────────

def test_main_autopilot_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    from fanops.cli import main
    assert main(["autopilot", "--no-daemon"]) == 0         # --no-daemon -> host-independent (no launchctl)
    out = capsys.readouterr().out
    assert "llm" in out
    assert "FANOPS_RESPONDER=llm" in (tmp_path / ".env").read_text()


def test_main_autopilot_env_write_error_exits_2(tmp_path, monkeypatch, capsys):
    # A read-only .env / unwritable fs must degrade to one clean stderr line + exit 2, not a traceback.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    import fanops.autopilot as ap
    monkeypatch.setattr(ap, "set_env_var", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))
    from fanops.cli import main
    assert main(["autopilot", "--no-daemon"]) == 2
    assert "autopilot:" in capsys.readouterr().err
