# tests/test_postiz_lifecycle.py — on-demand Postiz start is SAFE: never shells docker in the suite.
import types
import fanops.postiz_lifecycle as pl


def _cfg(backend="postiz"):
    return types.SimpleNamespace(poster_backend=backend)


def test_is_local():
    assert pl._is_local("http://localhost:4007")
    assert pl._is_local("http://127.0.0.1:4007/api")
    assert not pl._is_local("https://api.postiz.com")
    assert not pl._is_local("")


def test_backend_is_postiz_accepts_str_and_enum():
    assert pl._backend_is_postiz(_cfg("postiz"))
    assert not pl._backend_is_postiz(_cfg("dryrun"))
    assert pl._backend_is_postiz(_cfg(types.SimpleNamespace(value="postiz")))
    assert not pl._backend_is_postiz(_cfg(types.SimpleNamespace(value="zernio")))


def test_should_autostart_false_under_pytest():
    # the suite always runs under pytest -> ensure_up must short-circuit (never touch docker)
    assert pl._should_autostart(_cfg("postiz")) is False


def test_ensure_up_is_inert_in_tests(monkeypatch):
    called = {"n": 0}
    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("subprocess must NOT run during the test suite")
    monkeypatch.setattr(pl.subprocess, "run", boom)
    pl.ensure_up(_cfg("postiz"))          # must not raise, must not call subprocess
    pl.ensure_up(_cfg("dryrun"))
    assert called["n"] == 0
