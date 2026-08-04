import pytest
import secrets
from fanops.config import Config
from fanops import daemon

@pytest.mark.integration
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
