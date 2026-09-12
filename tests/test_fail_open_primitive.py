# tests/test_fail_open_primitive.py — fail_open must not swallow; resolve_account_handle fails closed.
import pytest

from fanops.config import Config


def test_fail_open_does_not_swallow_runtime_error():
    """Swallowing RuntimeError is a defect, not a passing contract. KeyboardInterrupt/SystemExit stay raised."""
    from fanops.errors import fail_open
    with pytest.raises(RuntimeError, match="boom"):
        with fail_open("test.site"):
            raise RuntimeError("boom")


def test_fail_open_propagates_keyboard_interrupt():
    from fanops.errors import fail_open
    with pytest.raises(KeyboardInterrupt):
        with fail_open("test.site"):
            raise KeyboardInterrupt()


def test_fail_open_propagates_system_exit():
    from fanops.errors import fail_open
    with pytest.raises(SystemExit):
        with fail_open("test.site"):
            raise SystemExit(1)


def test_resolve_account_handle_fails_closed_on_unreadable_accounts(tmp_path):
    """Torn accounts.json (directory where the file belongs) must raise, not return the raw handle."""
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.mkdir()
    with pytest.raises(OSError):
        views.resolve_account_handle("@someone", cfg)
