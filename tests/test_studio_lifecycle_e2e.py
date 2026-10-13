import socket
import sys

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
def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind((daemon.STUDIO_DEFAULT_HOST, 0))
        return sock.getsockname()[1]


@pytest.mark.integration
@pytest.mark.macos_only
@pytest.mark.skipif(sys.platform != "darwin", reason="MOL-830: fanops daemon is launchd/macOS-only")
@pytest.mark.timeout(300)  # two _STUDIO_PORT_TRIES × _STUDIO_PORT_STEP budgets exceed the global per-test timeout
def test_studio_real_lifecycle_replacement(tmp_path, monkeypatch):
    # Cold start: install_studio(wait=True) polls _studio_port_answers_within until the
    # expected generation answers. A free port and a temporary label keep this off the
    # production Studio job. install/stop/render read daemon_studio.STUDIO_LABEL.
    root = tmp_path / "fanops_root"
    root.mkdir()
    (root / ".env").write_text("")
    cfg = Config(root=root)
    cfg.reports.mkdir(parents=True, exist_ok=True)

    temp_label = f"com.fanops.studio.test.{secrets.token_hex(4)}"
    temp_port = _free_loopback_port()
    host = daemon.STUDIO_DEFAULT_HOST
    assert temp_label != "com.fanops.studio"
    assert temp_port != daemon.STUDIO_DEFAULT_PORT
    monkeypatch.setattr(studio_mod, "STUDIO_LABEL", temp_label)
    monkeypatch.setattr(daemon, "STUDIO_LABEL", temp_label)
    assert studio_mod.studio_plist_path().name == f"{temp_label}.plist"

    try:
        res_a = daemon.install_studio(cfg, host=host, port=temp_port, wait=True)
        if not res_a.get("studio_loaded"):
            pytest.fail(f"Generation A did not answer within the Studio port budget: {res_a.get('error')}")
        gen_a = res_a["generation"]
        assert len(gen_a) == 32
        fp_a = daemon._studio_get_fingerprint(host=host, port=temp_port)
        assert fp_a is not None and fp_a.get("generation") == gen_a
        pid_a = fp_a["pid"]

        res_b = daemon.install_studio(cfg, host=host, port=temp_port, wait=True)
        if not res_b.get("studio_loaded"):
            pytest.fail(f"Generation B did not answer within the Studio port budget: {res_b.get('error')}")
        gen_b = res_b["generation"]
        assert len(gen_b) == 32
        fp_b = daemon._studio_get_fingerprint(host=host, port=temp_port)
        assert fp_b is not None and fp_b.get("generation") == gen_b
        assert fp_b["pid"] != pid_a
        assert gen_b != gen_a
    finally:
        daemon.stop_studio(cfg, remove=True)
