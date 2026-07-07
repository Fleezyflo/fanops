import pytest
from tests._require_e2e import require_e2e, skip_or_fail, integration_skip_failure_longrepr

def test_require_e2e_false_by_default(monkeypatch):
    monkeypatch.delenv("FANOPS_REQUIRE_E2E", raising=False)
    assert require_e2e() is False

def test_require_e2e_true_when_set(monkeypatch):
    monkeypatch.setenv("FANOPS_REQUIRE_E2E", "1")
    assert require_e2e() is True

def test_skip_or_fail_skips_when_not_required(monkeypatch):
    monkeypatch.delenv("FANOPS_REQUIRE_E2E", raising=False)
    with pytest.raises(pytest.skip.Exception) as exc:
        skip_or_fail("no ffmpeg")
    assert exc.value.msg == "no ffmpeg"

def test_skip_or_fail_fails_when_required(monkeypatch):
    monkeypatch.setenv("FANOPS_REQUIRE_E2E", "1")
    with pytest.raises(pytest.fail.Exception) as exc:
        skip_or_fail("no ffmpeg")
    assert str(exc.value) == "FANOPS_REQUIRE_E2E=1 but the real-tooling E2E could not run: no ffmpeg"

def test_integration_skip_failure_longrepr():
    msg = integration_skip_failure_longrepr("call", "toolchain absent")
    assert msg == "FANOPS_REQUIRE_E2E=1 but integration test skipped (call): toolchain absent"
