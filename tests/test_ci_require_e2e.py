import pytest
pytestmark = [pytest.mark.integration, pytest.mark.ci_hook_regression]
def test_integration_skip_must_not_pass_under_require_e2e():
    pytest.skip("toolchain absent")
