import http.client
import os
import plistlib
import socket
import subprocess
import sys
import time

import pytest
import secrets
import fanops.daemon_studio as studio_mod
from fanops.config import Config
from fanops import daemon

# MOL-830: this test drives launchd. `daemon._require_darwin` raises unconditionally off macOS, so
# on the ubuntu e2e runner it did not fail -- it COULD NOT PASS, and it reddened `real-tooling E2E
# (must run, not skip)` on every nightly. A permanent red is worse than the skip that job exists to
# forbid: it trains readers to ignore the job, hiding a genuine break in the other 25 tests.
# Two guards, deliberately, because one is not enough:
#   * `macos_only` is what the ubuntu job DESELECTS. A skip alone cannot work there -- that job sets
#     FANOPS_REQUIRE_E2E=1, and conftest's pytest_runtest_makereport turns any integration-marked
#     skip into a failure, with no allowlist (tests/_require_e2e.py).
#   * `skipif` is for humans: a developer on Linux running `-m integration` gets a clean skip with a
#     reason instead of a RuntimeError traceback out of daemon.py.
# CONSEQUENCE, stated rather than buried: the `launchd-e2e` job in `.github/workflows/ci-e2e.yml`
# runs this test on `macos-latest` (`python -m pytest -q tests/test_studio_lifecycle_e2e.py -m macos_only`).
_ERR_TAIL_LINES = 40


def _pin_test_label(monkeypatch, label: str) -> None:
    # install/stop/render read daemon_studio.STUDIO_LABEL. Rebinding fanops.daemon.STUDIO_LABEL
    # leaves the production label in the plist and in launchctl.
    monkeypatch.setattr(studio_mod, "STUDIO_LABEL", label)
    monkeypatch.setattr(daemon, "STUDIO_LABEL", label)


def _free_loopback_port() -> int:
    banned = daemon.STUDIO_DEFAULT_PORT
    for _ in range(16):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((daemon.STUDIO_DEFAULT_HOST, 0))
            port = int(sock.getsockname()[1])
        if port != banned:
            return port
    raise RuntimeError(f"loopback only offered the production Studio port {banned}")


def _tail_text(path, n: int = _ERR_TAIL_LINES) -> str:
    if not path.is_file():
        return f"(absent: {path})"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(empty)"
    return "\n".join(lines[-n:])


def _launchctl_evidence(label: str) -> str:
    uid = os.getuid()
    chunks = []
    for args in (("print", f"gui/{uid}/{label}"), ("list", label)):
        shown = " ".join(args)
        try:
            result = daemon._launchctl(*args)
        except Exception as exc:
            chunks.append(f"launchctl {shown}: {type(exc).__name__}: {exc}")
            continue
        chunks.append(
            f"launchctl {shown} rc={result.returncode}\n"
            f"stdout:\n{result.stdout or ''}\n"
            f"stderr:\n{result.stderr or ''}"
        )
    return "\n".join(chunks)


def _fingerprint_failure_message(*, which: str, host: str, port: int, label: str,
                                 probe_error: str | None, err_tail: str, launchctl_text: str) -> str:
    probe = probe_error or "(no probe exception recorded)"
    return (
        f"{which} failed to answer on {host}:{port} label={label}\n"
        f"probe: {probe}\n"
        f"studio.err:\n{err_tail}\n"
        f"{launchctl_text}"
    )


def _await_studio_fingerprint(cfg, *, host: str, port: int, label: str, which: str,
                              sleep=time.sleep) -> dict:
    """Poll /_fingerprint until a dict, or the Studio port budget (tries × step) is spent."""
    if not label.startswith("com.fanops.studio.test."):
        raise RuntimeError(f"refusing launchctl evidence for label {label}")
    if port == daemon.STUDIO_DEFAULT_PORT:
        raise RuntimeError(f"refusing production Studio port {port}")
    last_error = None
    for _ in range(daemon._STUDIO_PORT_TRIES):
        noted: list[str] = []
        fp = daemon._studio_get_fingerprint(host, port, probe_error=noted)
        if isinstance(fp, dict):
            return fp
        if noted:
            last_error = noted[-1]
        sleep(daemon._STUDIO_PORT_STEP)
    pytest.fail(_fingerprint_failure_message(
        which=which,
        host=host,
        port=port,
        label=label,
        probe_error=last_error,
        err_tail=_tail_text(cfg.reports / "studio.err"),
        launchctl_text=_launchctl_evidence(label),
    ))


def test_free_loopback_port_avoids_production_studio_port():
    assert _free_loopback_port() != daemon.STUDIO_DEFAULT_PORT


def test_pinned_label_is_the_plist_label(tmp_path, monkeypatch):
    label = "com.fanops.studio.test.deadbeef"
    _pin_test_label(monkeypatch, label)
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_studio_plist(cfg, port=9321).encode())
    assert pl["Label"] == label
    assert pl["ProgramArguments"][-1] == "9321"


def test_unanswered_fingerprint_names_probe_and_launchd(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    lines = [f"cut-{i:02d}" for i in range(50)]
    (cfg.reports / "studio.err").write_text("\n".join(lines) + "\n")
    label = "com.fanops.studio.test.unit"
    port = 9322
    slept: list[float] = []
    calls: list[tuple] = []

    class _Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise ConnectionRefusedError("[Errno 61] Connection refused")

        def close(self):
            pass

    def fake_launchctl(*args, **_kwargs):
        calls.append(args)
        verb = args[0]
        return subprocess.CompletedProcess(
            ["launchctl", *args], 0, stdout=f"{verb}-stdout", stderr=f"{verb}-stderr")

    monkeypatch.setattr(http.client, "HTTPConnection", _Conn)
    monkeypatch.setattr(daemon, "_launchctl", fake_launchctl)

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _await_studio_fingerprint(
            cfg, host="127.0.0.1", port=port, label=label, which="Generation A",
            sleep=slept.append,
        )
    text = str(excinfo.value)
    assert "ConnectionRefusedError" in text
    assert "[Errno 61] Connection refused" in text
    assert "cut-49" in text
    assert "cut-10" in text
    assert "cut-09" not in text
    assert "cut-00" not in text
    assert "print-stdout" in text
    assert "print-stderr" in text
    assert "list-stdout" in text
    assert "list-stderr" in text
    assert label in text
    uid = os.getuid()
    assert ("print", f"gui/{uid}/{label}") in calls
    assert ("list", label) in calls
    for args in calls:
        for arg in args:
            assert arg != "com.fanops.studio"
            assert not str(arg).endswith("/com.fanops.studio")
    assert daemon._STUDIO_PORT_TRIES == 60
    assert daemon._STUDIO_PORT_STEP == 2.0
    assert slept == [daemon._STUDIO_PORT_STEP] * daemon._STUDIO_PORT_TRIES


def test_await_fingerprint_returns_first_dict(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)

    def fake_fp(*_a, **_k):
        return {"pid": 7, "generation": "abc"}

    def launchctl_forbidden(*_a, **_k):
        raise AssertionError("launchctl")

    monkeypatch.setattr(daemon, "_studio_get_fingerprint", fake_fp)
    monkeypatch.setattr(daemon, "_launchctl", launchctl_forbidden)
    slept: list[float] = []
    fp = _await_studio_fingerprint(
        cfg, host="127.0.0.1", port=9325, label="com.fanops.studio.test.ok",
        which="Generation A", sleep=slept.append,
    )
    assert fp == {"pid": 7, "generation": "abc"}
    assert slept == []


@pytest.mark.integration
@pytest.mark.macos_only
@pytest.mark.skipif(sys.platform != "darwin", reason="MOL-830: fanops daemon is launchd/macOS-only")
@pytest.mark.timeout(0)  # plugin default is 60s; the Studio port budget is 60 × 2.0s
def test_studio_real_lifecycle_replacement(tmp_path, monkeypatch):
    # MOL-728: Real lifecycle integration test on macOS.
    # This test uses a temporary launchd label and port to verify actual process replacement.

    # 1. Setup temporary root and label
    root = tmp_path / "fanops_root"
    root.mkdir()
    (root / ".env").write_text("")
    cfg = Config(root=root)
    cfg.reports.mkdir(parents=True, exist_ok=True)

    temp_label = f"com.fanops.studio.test.{secrets.token_hex(4)}"
    temp_port = _free_loopback_port()
    _pin_test_label(monkeypatch, temp_label)
    assert studio_mod.STUDIO_LABEL == temp_label
    assert daemon.STUDIO_LABEL == temp_label
    assert temp_label.startswith("com.fanops.studio.test.")
    assert temp_port != daemon.STUDIO_DEFAULT_PORT

    try:
        # 2. Install Generation A (let daemon generate it). wait stays False: studio_loaded
        # means launchd accepted the job. The fingerprint poll is the answer.
        print("\n[E2E] Installing Generation A")
        res_a = daemon.install_studio(cfg, port=temp_port)

        if not res_a.get("studio_loaded"):
            pytest.fail(f"Failed to load Studio Generation A: {res_a.get('error')}")

        gen_a = res_a["generation"]
        assert len(gen_a) == 32

        print(
            f"[E2E] Waiting for Generation A fingerprint "
            f"({daemon._STUDIO_PORT_TRIES} x {daemon._STUDIO_PORT_STEP}s)"
        )
        fp_a = _await_studio_fingerprint(
            cfg, host=res_a["host"], port=temp_port, label=temp_label, which="Generation A")
        pid_a = fp_a["pid"]
        assert fp_a["generation"] == gen_a, f"Expected generation {gen_a}, got {fp_a['generation']}"
        print(f"[E2E] Generation A ({gen_a}) serving on PID {pid_a}")

        # 3. Install Generation B (Replacement, let daemon generate it)
        print("[E2E] Installing Generation B")
        res_b = daemon.install_studio(cfg, port=temp_port)

        if not res_b.get("studio_loaded"):
            pytest.fail(f"Failed to load Studio Generation B: {res_b.get('error')}")

        gen_b = res_b["generation"]
        assert len(gen_b) == 32

        # 4. Verify PID changed and generation changed
        print(
            f"[E2E] Waiting for Generation B fingerprint "
            f"({daemon._STUDIO_PORT_TRIES} x {daemon._STUDIO_PORT_STEP}s)"
        )
        fp_b = _await_studio_fingerprint(
            cfg, host=res_b["host"], port=temp_port, label=temp_label, which="Generation B")
        pid_b = fp_b["pid"]
        assert fp_b["generation"] == gen_b, f"Expected generation {gen_b}, got {fp_b['generation']}"
        assert pid_b != pid_a, f"PID should have changed (old: {pid_a}, new: {pid_b})"
        assert gen_b != gen_a
        print(f"[E2E] Generation B ({gen_b}) serving on PID {pid_b}")

    finally:
        # 5. Cleanup — label is still the temp one; monkeypatch restores after this returns.
        print(f"[E2E] Cleaning up temporary service {temp_label}")
        daemon.stop_studio(cfg, remove=True)
