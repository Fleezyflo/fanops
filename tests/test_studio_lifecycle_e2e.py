import sys

import pytest
import secrets
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
# CONSEQUENCE, stated rather than buried: daemon reload has NO CI coverage anywhere. Closing that
# needs this test on a macos runner, which MOL-830 records as the follow-up.
@pytest.mark.integration
@pytest.mark.macos_only
@pytest.mark.skipif(sys.platform != "darwin", reason="MOL-830: fanops daemon is launchd/macOS-only")
def test_studio_real_lifecycle_replacement(tmp_path):
    # MOL-728: Real lifecycle integration test on macOS.
    # This test uses a temporary launchd label and port to verify actual process replacement.
    
    # 1. Setup temporary root and label
    root = tmp_path / "fanops_root"
    root.mkdir()
    (root / ".env").write_text("")
    cfg = Config(root=root)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    
    # Use a unique label and a free port (found 9000 is free)
    temp_label = f"com.fanops.studio.test.{secrets.token_hex(4)}"
    temp_port = 9000
    
    # Monkeypatch the label in daemon module so it uses our test label
    import fanops.daemon
    original_label = fanops.daemon.STUDIO_LABEL
    fanops.daemon.STUDIO_LABEL = temp_label
    
    try:
        # 2. Install Generation A (let daemon generate it)
        print("\n[E2E] Installing Generation A")
        res_a = daemon.install_studio(cfg, port=temp_port)
        
        if not res_a.get("studio_loaded"):
            pytest.fail(f"Failed to load Studio Generation A: {res_a.get('error')}")
            
        gen_a = res_a["generation"]
        assert len(gen_a) == 32
        
        # Capture PID A and verify generation A
        fp_a = daemon._studio_get_fingerprint(port=temp_port)
        assert fp_a is not None, "Generation A failed to answer"
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
        fp_b = daemon._studio_get_fingerprint(port=temp_port)
        assert fp_b is not None, "Generation B failed to answer"
        pid_b = fp_b["pid"]
        assert fp_b["generation"] == gen_b, f"Expected generation {gen_b}, got {fp_b['generation']}"
        assert pid_b != pid_a, f"PID should have changed (old: {pid_a}, new: {pid_b})"
        assert gen_b != gen_a
        print(f"[E2E] Generation B ({gen_b}) serving on PID {pid_b}")
        
    finally:
        # 5. Cleanup
        print(f"[E2E] Cleaning up temporary service {temp_label}")
        daemon.stop_studio(cfg, remove=True)
        # Restore the original label
        fanops.daemon.STUDIO_LABEL = original_label
