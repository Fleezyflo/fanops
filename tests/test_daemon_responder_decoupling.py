"""The daemon-setup remediation: scheduling (the launchd agent) and the AI switch (FANOPS_RESPONDER)
are DECOUPLED, and every enable surface DISCLOSES that hands-off + llm means recurring `claude` runs.

Three structural changes pinned here:
  1. render_plist is responder-AGNOSTIC — it bakes NO FANOPS_RESPONDER; the resident `run --loop`
     reloads .env each tick via load_dotenv(override=True) + Config(cfg.root).
  2. `daemon install --responder` defaults to `inherit` (resolve ambient, write nothing); an EXPLICIT
     llm/manual is PERSISTED to .env (durable). The CLI DISCLOSES the recurring-LLM cost when it resolves
     to llm, and points at `--responder manual` for no-LLM scheduling.
  3. The Studio ingest-kick no longer hardcodes llm — it inherits the same resolved responder, so there
     is no hidden third default that silently spends LLM."""
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


# ── 1. render_plist is responder-agnostic (decoupling) ───────────────────────────────────────

def test_render_plist_bakes_no_responder(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    env = pl.get("EnvironmentVariables") or {}
    assert "FANOPS_RESPONDER" not in env
    assert pl["ProgramArguments"][0] == daemon._fanops_bin()
    assert pl["ProgramArguments"][1:] == ["run", "--loop", "--interval", "600"]


def test_resolve_responder_reports_fire_time_mode(tmp_path, monkeypatch):
    # resolve_responder == Config.responder_mode: what a hands-off fire WILL run as. Explicit env wins.
    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    assert daemon.resolve_responder(cfg) == "manual"
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    assert daemon.resolve_responder(cfg) == "llm"


# ── 2. install: explicit choice persists to .env; inherit writes nothing; return discloses ────

def test_install_explicit_manual_persists_to_env_and_flags_no_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="manual")

    assert res["responder"] == "manual" and res["discloses_llm"] is False
    assert "FANOPS_RESPONDER=manual" in (tmp_path / ".env").read_text()    # durable, the real switch
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert "FANOPS_RESPONDER" not in (pl.get("EnvironmentVariables") or {})


def test_install_inherit_writes_no_responder_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")                          # ambient -> resolves llm
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="inherit")

    assert res["responder"] == "llm" and res["discloses_llm"] is True      # disclosed, not silent
    env = tmp_path / ".env"
    assert (not env.exists()) or "FANOPS_RESPONDER" not in env.read_text() # inherit persists NOTHING


def test_install_default_responder_is_inherit(tmp_path, monkeypatch):
    # The default must be inherit (not the old silent llm bake) — installing scheduling never FORCES the AI on.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    res = daemon.install(cfg, interval=600)                                # no responder= -> inherit
    env = tmp_path / ".env"
    assert (not env.exists()) or "FANOPS_RESPONDER" not in env.read_text()
    assert "responder" in res and "discloses_llm" in res


# ── CLI disclosure ───────────────────────────────────────────────────────────────────────────

def test_cli_install_discloses_recurring_llm(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")                          # host-independent: resolves llm
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    from fanops.cli import main
    assert main(["daemon", "install"]) == 0
    out = capsys.readouterr().out
    assert "responder llm" in out
    assert "LLM CLI" in out and "manual" in out                           # discloses cost + the opt-out


def test_cli_install_manual_is_silent_on_llm(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    from fanops.cli import main
    assert main(["daemon", "install", "--responder", "manual"]) == 0
    out = capsys.readouterr().out
    assert "responder manual" in out
    assert "invokes `claude`" not in out                                  # no false LLM warning on a no-LLM install


def test_cli_install_rejects_unknown_responder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    with pytest.raises(SystemExit):                                       # argparse choices guard
        main(["daemon", "install", "--responder", "bogus"])


# ── 3. the Studio ingest-kick no longer hardcodes llm ────────────────────────────────────────

def test_kick_prepare_does_not_inject_llm(tmp_path, monkeypatch):
    from fanops.studio import actions_run
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    captured = {}
    class _P:
        def __init__(self, *a, **k): captured["env"] = k.get("env", {}); self.pid = 424242   # kick reads proc.pid for the liveness-debounce
    monkeypatch.setattr(actions_run.subprocess, "Popen", _P)
    cfg = Config(root=tmp_path); cfg.control.mkdir(parents=True, exist_ok=True)

    assert actions_run.kick_prepare(cfg) is True
    # No hidden third default: the kick must NOT force FANOPS_RESPONDER=llm — the run resolves it itself.
    assert captured["env"].get("FANOPS_RESPONDER") != "llm"


def test_kick_prepare_honours_explicit_responder(tmp_path, monkeypatch):
    # An operator's explicit mode still rides through os.environ — honored, never overridden.
    from fanops.studio import actions_run
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    captured = {}
    class _P:
        def __init__(self, *a, **k): captured["env"] = k.get("env", {}); self.pid = 424242   # kick reads proc.pid for the liveness-debounce
    monkeypatch.setattr(actions_run.subprocess, "Popen", _P)
    cfg = Config(root=tmp_path); cfg.control.mkdir(parents=True, exist_ok=True)

    assert actions_run.kick_prepare(cfg) is True
    assert captured["env"].get("FANOPS_RESPONDER") == "manual"
