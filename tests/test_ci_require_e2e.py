"""Prove FANOPS_REQUIRE_E2E=1 turns integration-marked skips into failures (CI-01 / MOL-181)."""
import pytest

pytestmark = pytest.mark.integration


def test_require_e2e_fails_on_skip():
    pytest.skip("toolchain absent")
