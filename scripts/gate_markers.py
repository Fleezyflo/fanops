"""Single source for pytest marker expressions used by check.sh and check-full.sh."""

PYTEST_FAST = "not integration and not slow"
PYTEST_WITH_SLOW = "not integration"
