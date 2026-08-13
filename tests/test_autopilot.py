"""Tests for `fanops autopilot` — the one-command 'make me autonomous' verb. Gates are answered ONLY by
the LLM now (the manual responder was retired), so autopilot NO LONGER flips an AI switch: it installs
the supervising daemon (mocked launchctl, HOME/platform sandboxed) and reports readiness (dryrun by
default, no Blotato dependency). set_env_var is the idempotent .env updater still used by the Go-Live
tab (MUST preserve other keys/secrets); its tests stay here. os.environ mutation is guarded per-test."""
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


# ── set_env_var (idempotent .env updater — still used by the Go-Live tab) ─────────────────────

def test_set_env_var_creates_file_when_absent(tmp_path):
    env = tmp_path / ".env"
    autopilot.set_env_var(env, "FANOPS_LLM_TRANSPORT", "claude")
    assert "FANOPS_LLM_TRANSPORT=claude" in env.read_text()


def test_set_env_var_preserves_other_lines(tmp_path):
    # The .env holds secrets (POSTIZ_API_KEY etc.) — setting one key must NEVER drop the others.
    env = tmp_path / ".env"
    env.write_text("POSTIZ_API_KEY=s3cret\nFANOPS_WHISPER_MODEL=turbo\n")
    autopilot.set_env_var(env, "FANOPS_LLM_TRANSPORT", "claude")
    body = env.read_text()
    assert "POSTIZ_API_KEY=s3cret" in body
    assert "FANOPS_WHISPER_MODEL=turbo" in body
    assert "FANOPS_LLM_TRANSPORT=claude" in body


def test_set_env_var_updates_in_place_no_duplicate(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FANOPS_LLM_TRANSPORT=cursor\n")
    autopilot.set_env_var(env, "FANOPS_LLM_TRANSPORT", "claude")
    body = env.read_text()
    assert "FANOPS_LLM_TRANSPORT=claude" in body
    assert "cursor" not in body
    assert body.count("FANOPS_LLM_TRANSPORT") == 1            # updated in place, not appended


def test_set_env_var_updates_export_prefixed_line(tmp_path):
    # python-dotenv accepts `export KEY=value`; updating must match it in place (keeping the export
    # prefix) instead of appending a confusing duplicate that shadows it.
    env = tmp_path / ".env"
    env.write_text("export FANOPS_LLM_TRANSPORT=cursor\n")
    autopilot.set_env_var(env, "FANOPS_LLM_TRANSPORT", "claude")
    body = env.read_text()
    assert "export FANOPS_LLM_TRANSPORT=claude" in body
    assert body.count("FANOPS_LLM_TRANSPORT") == 1            # one line, updated — not a duplicate
    assert "cursor" not in body


def test_set_env_var_rejects_newline_in_value(tmp_path):
    # A value with an embedded newline would inject an arbitrary KEY=VALUE line into .env (could
    # silently overwrite POSTIZ_API_KEY/BLOTATO_API_KEY). Reject it; leave the file untouched.
    import pytest
    env = tmp_path / ".env"
    env.write_text("BLOTATO_API_KEY=keep\n")
    with pytest.raises(ValueError):
        autopilot.set_env_var(env, "POSTIZ_API_KEY", "good\nBLOTATO_API_KEY=hijacked")
    body = env.read_text()
    assert "hijacked" not in body and "BLOTATO_API_KEY=keep" in body     # no partial write


def test_set_env_var_is_atomic_no_tmp_leftover(tmp_path):
    # Written via temp + os.replace so a crash mid-write never truncates the secrets-bearing .env.
    env = tmp_path / ".env"
    autopilot.set_env_var(env, "POSTIZ_URL", "https://p.example.com")
    assert env.read_text().strip().endswith("https://p.example.com")
    assert list(tmp_path.glob("*.tmp")) == []                            # temp file cleaned up


def test_set_env_var_handles_spaces_and_skips_comment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# FANOPS_LLM_TRANSPORT=commented\nFANOPS_LLM_TRANSPORT = cursor\n")
    autopilot.set_env_var(env, "FANOPS_LLM_TRANSPORT", "claude")
    body = env.read_text()
    assert "# FANOPS_LLM_TRANSPORT=commented" in body          # comment preserved, not treated as the key
    assert "FANOPS_LLM_TRANSPORT=claude" in body
    assert "= cursor" not in body                              # the real (spaced) assignment was updated


# ── autopilot — installs the daemon + reports readiness; NEVER writes FANOPS_RESPONDER ──────────

def test_autopilot_installs_daemon_and_reports_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)     # unset -> resolves to llm (the only mode)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)

    res = autopilot.autopilot(cfg, interval=600, install_daemon=True)

    assert res["responder"] == "llm"                          # reported (validate-or-refuse), never written
    assert res["daemon"]["loaded"] is True
    assert daemon.plist_path().exists()
    env = tmp_path / ".env"
    assert (not env.exists()) or "FANOPS_RESPONDER" not in env.read_text()   # autopilot persists NOTHING


def test_autopilot_no_daemon_skips_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=False)
    assert res["daemon"] is None
    assert not daemon.plist_path().exists()
    assert res["responder"] == "llm"


def test_autopilot_off_darwin_skips_daemon(tmp_path, monkeypatch):
    # Non-darwin: the launchd agent is skipped with a note, not a crash; readiness still reports.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=True)
    assert res["responder"] == "llm"
    assert res["daemon"] is None
    assert res["daemon_note"] and "macOS" in res["daemon_note"]


def test_autopilot_reports_dryrun_and_no_blotato_requirement(tmp_path, monkeypatch):
    # No Blotato: in dryrun the readiness report must NOT demand a BLOTATO_API_KEY.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    res = autopilot.autopilot(cfg, interval=600, install_daemon=False)
    assert res["backend"] == "dryrun"
    assert not any("BLOTATO" in c["label"].upper() for c in res["checks"])


# ── CLI wiring ──────────────────────────────────────────────────────────────────────────────

def test_main_autopilot_nonzero_when_fixture_unhealthy(tmp_path, monkeypatch, capsys):
    # MOL-965 WP1: exit honesty — incomplete tmp fixture → report_is_healthy false → nonzero.
    # Soft-lie "always 0" retired; healthy-path 0 covered by constructing a healthy report below.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    from fanops.cli import main
    assert main(["autopilot", "--no-daemon"]) == 1         # --no-daemon -> host-independent (no launchctl)
    out = capsys.readouterr().out
    assert "llm" in out
    env = tmp_path / ".env"
    assert (not env.exists()) or "FANOPS_RESPONDER" not in env.read_text()   # autopilot writes no responder


def test_main_autopilot_returns_0_when_healthy(tmp_path, monkeypatch, capsys):
    # Construct a healthy autopilot result so exit-0 path stays covered under severity honesty.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    from fanops import autopilot
    from fanops.doctor import _check
    from fanops.health_model import DepHealth
    monkeypatch.setattr(
        autopilot,
        "autopilot",
        lambda cfg, interval, install_daemon=True: {
            "responder": "llm",
            "backend": "dryrun",
            "checks": [_check("ok", True)],
            "notes": [],
            "deps": [DepHealth("docker", True, "up")],
            "daemon": None,
            "daemon_note": "skipped",
        },
    )
    from fanops.cli import main
    assert main(["autopilot", "--no-daemon"]) == 0
    assert "llm" in capsys.readouterr().out


def test_main_autopilot_bad_responder_exits_2(tmp_path, monkeypatch, capsys):
    # A bad FANOPS_RESPONDER is a HARD REFUSE (cfg.responder_mode raises ValueError) — cmd_autopilot must
    # degrade to one clean stderr line + exit 2, never a traceback.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "bogus")
    from fanops.cli import main
    assert main(["autopilot", "--no-daemon"]) == 2
    assert "autopilot:" in capsys.readouterr().err
