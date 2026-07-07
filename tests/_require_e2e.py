import os
import pytest

def require_e2e() -> bool:
    return os.getenv("FANOPS_REQUIRE_E2E") == "1"

def skip_or_fail(reason: str) -> None:
    if require_e2e():
        pytest.fail(f"FANOPS_REQUIRE_E2E=1 but the real-tooling E2E could not run: {reason}")
    pytest.skip(reason)

def integration_skip_failure_longrepr(when: str, reason: str) -> str:
    return f"FANOPS_REQUIRE_E2E=1 but integration test skipped ({when}): {reason}"

def skip_reason_from_report(rep) -> str:
    lr = rep.longrepr
    if isinstance(lr, tuple) and len(lr) >= 3:
        return str(lr[2])
    if lr is not None:
        return str(lr)
    return "(no reason)"
