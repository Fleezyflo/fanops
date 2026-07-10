"""com.fanops.studio — KeepAlive launchd resident for the localhost Studio cockpit."""
from __future__ import annotations
import os, plistlib, subprocess

import pytest

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


def test_render_studio_plist_keepalive_resident(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_studio_plist(cfg).encode())
    assert pl["Label"] == daemon.STUDIO_LABEL
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert pl["RunAtLoad"] is True
    assert "StartInterval" not in pl
    assert pl["WorkingDirectory"] == str(tmp_path)
    assert pl["LSMultipleInstancesProhibited"] is True
    assert pl["ThrottleInterval"] == daemon._MIN_INTERVAL
    assert pl["ProgramArguments"][-5:] == ["studio", "--host", "127.0.0.1", "--port", "8787"]


def test_install_studio_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    uid = os.getuid()

    res = daemon.install_studio(cfg)

    pp = daemon.studio_plist_path()
    assert pp.exists()
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(pp)] in fake.calls
    assert res["studio_loaded"] is True
    assert res["studio_plist"] == str(pp)
    assert res["host"] == "127.0.0.1"
    assert res["port"] == 8787


def test_stop_studio_remove_unlinks_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(0, ""), list=(1, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    pp = daemon.studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(daemon.render_studio_plist(cfg))

    res = daemon.stop_studio(cfg, remove=True)

    assert res["stopped"] is True
    assert res.get("removed") is True
    assert not pp.exists()


def test_install_studio_raises_on_non_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    cfg = Config(root=tmp_path)
    with pytest.raises(RuntimeError, match="macOS"):
        daemon.install_studio(cfg)
