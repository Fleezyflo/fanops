# tests/test_fail_open_primitive.py — Brief 05: fail_open primitive + resolve_account_handle exemplar.
import logging

import pytest

from fanops.config import Config


def test_fail_open_logs_every_failure_not_once_per_process(tmp_path, caplog):
    from fanops.errors import fail_open
    n = 0

    def _boom():
        nonlocal n
        n += 1
        raise RuntimeError(f"boom-{n}")

    with caplog.at_level(logging.WARNING, logger="fanops.errors"):
        with fail_open("test.site"):
            _boom()
        with fail_open("test.site"):
            _boom()
    assert len(caplog.records) == 2
    assert all("test.site fail-open" in r.message for r in caplog.records)


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


def test_resolve_account_handle_logs_and_preserves_raw_on_load_error(tmp_path, monkeypatch, caplog):
    from fanops.studio import views
    cfg = Config(root=tmp_path)

    def _boom(_cfg):
        raise OSError("accounts unreadable")

    monkeypatch.setattr("fanops.studio.views.Accounts.load", _boom)
    with caplog.at_level(logging.WARNING, logger="fanops.errors"):
        assert views.resolve_account_handle("@someone", cfg) == "@someone"
        assert views.resolve_account_handle("@someone", cfg) == "@someone"
    assert len(caplog.records) == 2
    assert all("studio.views.resolve_account_handle fail-open" in r.message for r in caplog.records)
