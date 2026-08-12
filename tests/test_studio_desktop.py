# tests/test_studio_desktop.py — fanops studio --app window (no Flask, no pywebview required in CI)
import importlib
import sys
import types


def test_open_studio_window_fails_closed_without_webview(monkeypatch, capsys):
    import builtins
    real = builtins.__import__
    def blocked(name, *a, **k):
        if name == "webview" or (isinstance(name, str) and name.startswith("webview.")):
            raise ImportError("No module named 'webview'")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", blocked)
    sys.modules.pop("webview", None)
    import fanops.studio_desktop as sd
    sd = importlib.reload(sd)
    assert sd.open_studio_window("http://127.0.0.1:8787") != 0
    err = capsys.readouterr().err
    assert "[desktop]" in err
    assert "pip install" in err


def test_open_studio_window_starts_webview(monkeypatch):
    fake = types.ModuleType("webview")
    calls = []
    fake.create_window = lambda *a, **k: calls.append("create")
    fake.start = lambda *a, **k: calls.append("start")
    monkeypatch.setitem(sys.modules, "webview", fake)
    import fanops.studio_desktop as sd
    sd = importlib.reload(sd)
    assert sd.open_studio_window("http://127.0.0.1:8787") == 0
    assert calls == ["create", "start"]


def test_studio_app_does_not_hit_unmanaged_refuse(tmp_path, monkeypatch, capsys, mocker):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    mocker.patch.object(cli, "_studio_port_busy", return_value=False)
    mocker.patch("fanops.studio_desktop.open_studio_window", return_value=0)
    rc = cli.main(["studio", "--app"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "unmanaged foreground" not in err
    assert "--install" in err
