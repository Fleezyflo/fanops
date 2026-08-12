"""The daemon is SCHEDULING ONLY. Gates are answered ONLY by the LLM (the manual responder was retired),
so there is no longer an AI switch to couple/decouple from the launchd agent: `daemon install` writes NO
FANOPS_RESPONDER, takes no `--responder` product choice, and the resident `run --loop` answers every gate
with the LLM. These tests pin that reality:

  1. render_plist is responder-AGNOSTIC — it bakes NO FANOPS_RESPONDER; the resident `run --loop`
     reloads .env each tick via load_dotenv(override=True) + Config(cfg.root).
  2. install persists NOTHING to .env (no responder product choice) and reports the fire-time mode (llm).
  3. The Studio ingest-kick injects NO responder default — the spawned `fanops run` resolves it itself."""
from __future__ import annotations
import plistlib, subprocess

import pytest

from fanops.config import Config
from fanops import daemon


def _fake_launchctl(**spec):
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


# ── 1. render_plist is responder-agnostic ────────────────────────────────────────────────────

def test_render_plist_bakes_no_responder(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    env = pl.get("EnvironmentVariables") or {}
    assert "FANOPS_RESPONDER" not in env
    assert pl["ProgramArguments"][0] == daemon._fanops_bin()
    assert pl["ProgramArguments"][1:] == ["run", "--loop", "--interval", "600"]


# ── 2. resolve_responder + install: always llm, persist nothing ───────────────────────────────

def test_resolve_responder_reports_llm(tmp_path, monkeypatch):
    # resolve_responder == Config.responder_mode: empty/unset OR 'llm' -> 'llm'; anything else is a HARD
    # REFUSE (there is no manual mode to fall back to).
    cfg = Config(root=tmp_path)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    assert daemon.resolve_responder(cfg) == "llm"
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    assert daemon.resolve_responder(cfg) == "llm"
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    with pytest.raises(ValueError):
        daemon.resolve_responder(cfg)


def test_install_writes_no_responder_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600)

    assert res["responder"] == "llm"                                      # reported for the status line
    assert "discloses_llm" not in res                                     # vestigial AI-switch field removed
    env = tmp_path / ".env"
    assert (not env.exists()) or "FANOPS_RESPONDER" not in env.read_text()  # scheduling install persists NOTHING
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert "FANOPS_RESPONDER" not in (pl.get("EnvironmentVariables") or {})


def test_install_takes_no_responder_kwarg(tmp_path, monkeypatch):
    # The AI-switch product choice was removed: install must no longer accept a `responder=` keyword.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    with pytest.raises(TypeError):
        daemon.install(cfg, interval=600, responder="manual")   # type: ignore[call-arg]


# ── CLI ────────────────────────────────────────────────────────────────────────────────────

def test_cli_install_reports_llm_and_gates(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    from fanops.cli import main
    assert main(["daemon", "install"]) == 0
    out = capsys.readouterr().out
    assert "responder llm" in out
    assert "LLM CLI" in out                                    # hands-off answers gates with the LLM CLI


def test_cli_install_rejects_removed_responder_flag(tmp_path, monkeypatch):
    # --responder was retired (there is no manual/inherit product choice) — argparse rejects it.
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    with pytest.raises(SystemExit):
        main(["daemon", "install", "--responder", "manual"])


# ── 3. the Studio ingest-kick injects no responder default ────────────────────────────────────

def test_kick_prepare_injects_no_responder(tmp_path, monkeypatch):
    from fanops.studio import actions_run
    import fanops.pipeline_run as pipeline_run
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    monkeypatch.setattr(pipeline_run, "run_held", lambda _cfg: False)   # no driver owns the workspace
    captured = {}
    class _P:
        def __init__(self, *a, **k): captured["env"] = k.get("env", {}); self.pid = 424242
    monkeypatch.setattr(actions_run.subprocess, "Popen", _P)
    cfg = Config(root=tmp_path); cfg.control.mkdir(parents=True, exist_ok=True)

    assert actions_run.kick_prepare(cfg) is True
    # No hidden default: the kick must NOT inject FANOPS_RESPONDER — the run resolves it itself (always llm).
    assert captured["env"].get("FANOPS_RESPONDER") in (None, "")
