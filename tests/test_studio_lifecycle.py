import json
import pytest
from unittest.mock import Mock, patch
from fanops.config import Config
from fanops import daemon

@pytest.fixture
def cfg(tmp_path):
    c = Config(root=tmp_path)
    c.reports.mkdir(parents=True, exist_ok=True)
    return c

def test_render_studio_plist_carries_generation(cfg):
    # MOL-728: Verify generation is injected into plist environment
    plist_str = daemon.render_studio_plist(cfg, generation="test-gen-123")
    import plistlib
    pl = plistlib.loads(plist_str.encode())
    assert pl["EnvironmentVariables"]["FANOPS_STUDIO_GENERATION"] == "test-gen-123"
    # Verify the launch command is --managed
    assert "studio" in pl["ProgramArguments"]
    assert "--managed" in pl["ProgramArguments"]
    assert "--install" not in pl["ProgramArguments"]

def test_studio_launch_cmd_matches_plist(cfg):
    # MOL-728: Verify _STUDIO_LAUNCH_CMD matches the managed invocation semantics
    from fanops import daemon
    cmd = daemon._STUDIO_LAUNCH_CMD
    assert "fanops studio --managed" in cmd
    assert "--host" in cmd
    assert "--port" in cmd

def test_install_studio_owns_generation(cfg, monkeypatch, mocker):
    # MOL-728: Verify install_studio generates a 32-char hex generation when none supplied
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    mocker.patch("fanops.daemon.studio_agent_status", return_value={"pid": None})
    mocker.patch("fanops.daemon._launchctl", return_value=Mock(returncode=0))
    mocker.patch("fanops.daemon._confirm_loaded", return_value=True)
    mocker.patch("fanops.daemon.studio_plist_path", return_value=cfg.root / "studio.plist")
    mocker.patch("fanops.daemon._studio_port_answers", return_value=True)
    mocker.patch("fanops.daemon.time.sleep", return_value=None)
    
    mocker.patch("fanops.daemon._studio_get_fingerprint")   # stubbed, not asserted on in this test

    # We capture the generation passed to write_text_atomic
    write_mock = mocker.patch("fanops.controlio.write_text_atomic")
    
    def side_effect(host, port, expect_sha, expect_gen, old_pid):
        # The verification must receive the SAME generation
        assert len(expect_gen) == 32
        try: int(expect_gen, 16)
        except ValueError: pytest.fail("Generation not hex")
        return True
        
    mocker.patch("fanops.daemon._studio_port_answers_within", side_effect=side_effect)
    
    res = daemon.install_studio(cfg)
    assert res["studio_loaded"] is True
    assert len(res["generation"]) == 32
    
    # Verify it reached the plist
    args, kwargs = write_mock.call_args
    plist_content = args[1]
    assert res["generation"] in plist_content

def test_install_studio_verifies_pid_and_generation(cfg, monkeypatch, mocker):
    # MOL-728: Verify install_studio captures old PID and waits for NEW PID + expected generation
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(cfg.root))
    
    # Mock status to return an "old" PID
    mocker.patch("fanops.daemon.studio_agent_status", return_value={"pid": 1234})
    mocker.patch("fanops.daemon._launchctl", return_value=Mock(returncode=0))
    mocker.patch("fanops.daemon._confirm_loaded", return_value=True)
    mocker.patch("fanops.daemon.studio_plist_path", return_value=cfg.root / "studio.plist")
    
    # Mock fingerprint endpoint
    get_fp = mocker.patch("fanops.daemon._studio_get_fingerprint")
    mocker.patch("fanops.daemon._studio_port_answers", return_value=True)
    mocker.patch("fanops.daemon.time.sleep", return_value=None)
    
    # Case 1: Success (New PID, Correct Generation)
    get_fp.return_value = {"pid": 5678, "generation": "gen-B", "sha": "sha-X"}
    res = daemon.install_studio(cfg, generation="gen-B")
    assert res["studio_loaded"] is True
    assert res["old_pid"] == 1234
    
    # Case 2: Failure (Old PID returned)
    get_fp.return_value = {"pid": 1234, "generation": "gen-B", "sha": "sha-X"}
    res = daemon.install_studio(cfg, generation="gen-B")
    assert res["studio_loaded"] is False
    
    # Case 3: Failure (Wrong generation)
    get_fp.return_value = {"pid": 5678, "generation": "gen-A", "sha": "sha-X"}
    res = daemon.install_studio(cfg, generation="gen-B")
    assert res["studio_loaded"] is False

def test_studio_app_fingerprint_payload(cfg):
    # MOL-728: Verify /_fingerprint returns the full payload including generation and PID
    from fanops.studio.app import create_app
    
    with patch("fanops.cli._running_code_sha", return_value="test-sha"), \
         patch("fanops.studio.app._GENERATION", "test-gen"), \
         patch("fanops.studio.app._PID", 9999):
        app = create_app(cfg)
        client = app.test_client()
        resp = client.get("/_fingerprint")
        
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["generation"] == "test-gen"
        assert data["pid"] == 9999
        assert data["sha"] == "test-sha"
        assert "start_time" in data

def test_redeploy_poll_rejection_cases(cfg, mocker):
    # MOL-728: Verify _studio_port_answers_within rejection logic
    mocker.patch("fanops.daemon._studio_port_answers", return_value=True)
    get_fp = mocker.patch("fanops.daemon._studio_get_fingerprint")
    mocker.patch("fanops.daemon.time.sleep", return_value=None)
    
    # Rejects missing generation
    get_fp.return_value = {"pid": 5678, "sha": "sha-X"} # no generation
    assert daemon._studio_port_answers_within(expect_gen="gen-B") is False
    
    # Rejects malformed response (None)
    get_fp.return_value = None
    assert daemon._studio_port_answers_within(expect_gen="gen-B") is False
    
    # Rejects endpoint unavailable (port_answers=False)
    mocker.patch("fanops.daemon._studio_port_answers", return_value=False)
    assert daemon._studio_port_answers_within(expect_gen="gen-B") is False

def test_redeploy_studio_verifies_full_lifecycle(cfg, monkeypatch, mocker):
    # MOL-728: Verify _redeploy_studio (fanops up) verifies PID, SHA, and Generation (from plist)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    
    # Mock an existing plist with a generation
    import plistlib
    pl = {"EnvironmentVariables": {"FANOPS_STUDIO_GENERATION": "existing-gen"}}
    mocker.patch("fanops.daemon.studio_plist_path", return_value=Mock(
        exists=lambda: True,
        read_bytes=lambda: plistlib.dumps(pl)
    ))
    
    mocker.patch("fanops.daemon.studio_agent_status", return_value={"pid": 1111})
    mocker.patch("fanops.daemon._launchctl", return_value=Mock(returncode=0))
    mocker.patch("fanops.daemon._version_signal", return_value=("new-sha", "git-head"))
    
    # Verification call capture
    verify_mock = mocker.patch("fanops.daemon._studio_port_answers_within", return_value=True)
    
    assert daemon._redeploy_studio(cfg, wait=True) is True
    
    # Assert it verified the existing generation and the old PID
    verify_mock.assert_called_with(expect_sha="new-sha", expect_gen="existing-gen", old_pid=1111)
