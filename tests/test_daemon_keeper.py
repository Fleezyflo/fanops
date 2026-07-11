"""L2 daemon keeper: com.fanops.keeper re-asserts main pump via `fanops daemon ensure`."""
from __future__ import annotations
import os, plistlib, subprocess

from fanops.config import Config
from fanops import daemon


def _fake_launchctl(**spec):
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print" and len(cmd) > 2:
            key = cmd[2]
            rc, out = spec.get(key, spec.get("print", (0, "")))
        else:
            rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


def test_ensure_noop_when_pump_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(**{main_print: (0, "")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    res = daemon.ensure(cfg)

    assert res == {"label": daemon.LABEL, "loaded": True, "action": "none"}
    assert not any(c[1] == "bootstrap" for c in fake.calls)


def test_ensure_bootstraps_when_pump_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().write_text(daemon.render_plist(cfg, interval=600))
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(0, ""), **{main_print: (1, "")})
    def run(cmd, *a, **k):
        if len(cmd) > 1 and cmd[1] == "print" and cmd[2] == main_print:
            if any(c[1:3] == ["bootstrap", f"gui/{uid}"] for c in fake.calls):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return fake(cmd, *a, **k)
    run.calls = fake.calls
    monkeypatch.setattr(daemon.subprocess, "run", run)

    res = daemon.ensure(cfg)

    assert res["loaded"] is True
    assert res["action"] == "bootstrap"
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(daemon.plist_path())] in fake.calls


def test_ensure_rewrites_stale_plist_when_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(**{main_print: (0, "")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    iv = 600
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    stale = plistlib.loads(daemon.render_plist(cfg, interval=iv).encode())
    stale["ProgramArguments"] = ["/bin/bash", str(cfg.control / "fanops-run.sh")]
    daemon.plist_path().write_bytes(plistlib.dumps(stale))
    (cfg.control / "fanops-run.sh").write_text("#!/bin/bash\n# legacy wrapper\n")
    res = daemon.ensure(cfg)
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl["ProgramArguments"] == [daemon._fanops_bin(), "run", "--loop", "--interval", str(iv)]
    assert not (cfg.control / "fanops-run.sh").exists()
    assert res["loaded"] is True
    assert res["action"] == "rewrite_plist"


def test_install_installs_keeper_plist_and_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    uid = os.getuid()

    res = daemon.install(cfg, interval=600, responder="inherit")

    kp = daemon.keeper_plist_path()
    assert kp.exists()
    pl = plistlib.loads(kp.read_bytes())
    assert pl["Label"] == daemon.KEEPER_LABEL
    assert pl["StartInterval"] == daemon.KEEPER_POLL_INTERVAL_S
    assert pl["ProgramArguments"][-2:] == ["daemon", "ensure"]
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(kp)] in fake.calls
    assert res["keeper_loaded"] is True
    assert res["keeper_plist"] == str(kp)


def test_keeper_in_sibling_agents_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl())

    reps = daemon.sibling_agents_status()
    keeper = next(r for r in reps if r["label"] == daemon.KEEPER_LABEL)

    assert keeper["short"] == "daemon keeper"
    assert keeper["poll_interval_s"] == daemon.KEEPER_POLL_INTERVAL_S
