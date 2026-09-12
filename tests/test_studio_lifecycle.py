import http.client
import json
import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fanops.config import Config
from fanops import daemon


@pytest.fixture
def cfg(tmp_path):
    c = Config(root=tmp_path)
    c.reports.mkdir(parents=True, exist_ok=True)
    return c


def _darwin_home(cfg, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(cfg.root))
    (cfg.root / "Library" / "LaunchAgents").mkdir(parents=True, exist_ok=True)


def _stub_launchctl(monkeypatch, *, pid=5678, list_rc=0):
    real = subprocess.run

    def fake(cmd, **kw):
        if cmd and cmd[0] == "launchctl":
            if len(cmd) > 1 and cmd[1] == "list":
                stdout = f'"PID" = {pid};\n' if list_rc == 0 else ""
                return subprocess.CompletedProcess(list(cmd), list_rc, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(list(cmd), 0, stdout="", stderr="")
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake)


def _stub_port(monkeypatch, *, up=True):
    if up:
        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Sock())
    else:
        def _down(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(socket, "create_connection", _down)


def _stub_fingerprint(monkeypatch, payload):
    class _Resp:
        def __init__(self):
            if payload is None:
                self.status = 500
                self._body = b""
            else:
                self.status = 200
                self._body = json.dumps(payload).encode()

        def read(self):
            return self._body

    class _Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            if payload is False:
                raise OSError("fingerprint down")

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", _Conn)


def test_render_studio_plist_carries_generation(cfg):
    # MOL-728: Verify generation is injected into plist environment
    plist_str = daemon.render_studio_plist(cfg, generation="test-gen-123")
    pl = plistlib.loads(plist_str.encode())
    assert pl["EnvironmentVariables"]["FANOPS_STUDIO_GENERATION"] == "test-gen-123"
    # Verify the launch command is --managed
    assert "studio" in pl["ProgramArguments"]
    assert "--managed" in pl["ProgramArguments"]
    assert "--install" not in pl["ProgramArguments"]


def test_studio_launch_cmd_matches_plist(cfg):
    # MOL-728: Verify _STUDIO_LAUNCH_CMD matches the managed invocation semantics
    cmd = daemon._STUDIO_LAUNCH_CMD
    assert "fanops studio --managed" in cmd
    assert "--host" in cmd
    assert "--port" in cmd


def test_install_studio_owns_generation(cfg, monkeypatch):
    # MOL-728: install_studio generates a 32-char hex generation when none supplied
    _darwin_home(cfg, monkeypatch)
    _stub_launchctl(monkeypatch, list_rc=1)
    res = daemon.install_studio(cfg)
    assert res["studio_loaded"] is True
    assert len(res["generation"]) == 32
    assert int(res["generation"], 16) >= 0
    plist = Path.home() / "Library" / "LaunchAgents" / "com.fanops.studio.plist"
    assert plist.is_file()
    assert res["generation"] in plist.read_text()


def test_install_studio_verifies_pid_and_generation(cfg, monkeypatch):
    # MOL-728: wait=True confirms NEW PID + expected generation via the real port/fingerprint edges
    _darwin_home(cfg, monkeypatch)
    _stub_launchctl(monkeypatch, pid=1234)
    _stub_port(monkeypatch, up=True)
    fp = {"pid": 5678, "generation": "gen-B", "sha": "sha-X"}
    _stub_fingerprint(monkeypatch, fp)

    res = daemon.install_studio(cfg, generation="gen-B", wait=True)
    assert res["studio_loaded"] is True
    assert res["old_pid"] == 1234

    fp["pid"] = 1234
    res = daemon.install_studio(cfg, generation="gen-B", wait=True)
    assert res["studio_loaded"] is False

    fp["pid"] = 5678
    fp["generation"] = "gen-A"
    res = daemon.install_studio(cfg, generation="gen-B", wait=True)
    assert res["studio_loaded"] is False

    res = daemon.install_studio(cfg, generation="gen-B")
    assert res["studio_loaded"] is True


def test_studio_app_fingerprint_payload(cfg):
    # MOL-728: GET /_fingerprint returns the resident's real pid/generation/sha
    from fanops.studio.app import create_app
    app = create_app(cfg)
    client = app.test_client()
    resp = client.get("/_fingerprint")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["pid"] == os.getpid()
    assert "start_time" in data
    assert "sha" in data
    assert "generation" in data


def test_redeploy_poll_rejection_cases(cfg, monkeypatch):
    # MOL-728: _studio_port_answers_within rejection logic at socket/http edges
    _stub_port(monkeypatch, up=True)
    _stub_fingerprint(monkeypatch, {"pid": 5678, "sha": "sha-X"})
    assert daemon._studio_port_answers_within(expect_gen="gen-B", tries=1, step=0) is False

    _stub_fingerprint(monkeypatch, None)
    assert daemon._studio_port_answers_within(expect_gen="gen-B", tries=1, step=0) is True

    _stub_port(monkeypatch, up=False)
    assert daemon._studio_port_answers_within(expect_gen="gen-B", tries=1, step=0) is False


def test_redeploy_studio_verifies_full_lifecycle(cfg, monkeypatch):
    # MOL-728: _redeploy_studio (fanops up) verifies PID, SHA, and Generation from the real plist
    _darwin_home(cfg, monkeypatch)
    _stub_launchctl(monkeypatch, pid=1111)
    _stub_port(monkeypatch, up=True)
    plist = Path.home() / "Library" / "LaunchAgents" / "com.fanops.studio.plist"
    plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"FANOPS_STUDIO_GENERATION": "existing-gen"}}))
    sha, _src = daemon._version_signal(cfg)
    _stub_fingerprint(monkeypatch, {"pid": 9999, "generation": "existing-gen", "sha": sha})
    assert daemon._redeploy_studio(cfg, wait=True) is True
