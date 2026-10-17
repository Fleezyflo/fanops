# tests/test_cli_compose.py — the `fanops compose` operator verb. Guards (unknown clip / missing
# file) + the verb's spec wiring via stdout JSON. The engine itself (real MoviePy) is covered by
# test_compose.py; a garbage base clip fail-opens (composed=false, exit 1) without mocking compose_clip.
import json
from fanops.cli import main
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt


def _seed_clip(cfg, *, with_file=True, hook="THE DROP"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          hook=hook, transcript_excerpt="the beat drops here", state=MomentState.clipped))
    path = cfg.clips / "clip_x.mp4"
    if with_file:
        cfg.clips.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"BASE")
    led.add_clip(Clip(id="clip_x", parent_id="m1", path=str(path), aspect=Fmt.r9x16, state=ClipState.rendered))
    led.save()
    return path


def test_compose_unknown_clip_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["compose", "nope"])
    assert rc == 2 and "no such clip" in capsys.readouterr().out.lower()


def test_compose_missing_file_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_clip(Config(root=tmp_path), with_file=False)
    rc = main(["compose", "clip_x"])
    assert rc == 2 and "missing on disk" in capsys.readouterr().out.lower()


def test_compose_wires_spec_from_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_clip(Config(root=tmp_path))
    rc = main(["compose", "clip_x"])
    data = json.loads(capsys.readouterr().out)
    assert data["title"] == "THE DROP"
    assert data["intro"] == "Moh Flow"
    assert str(data["out"]).endswith("_composed.mp4")
    assert rc == 1 and data["composed"] is False
    assert data.get("reason")


def test_compose_failopen_exits_1_with_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = _seed_clip(Config(root=tmp_path))
    rc = main(["compose", "clip_x"])
    data = json.loads(capsys.readouterr().out)
    out = path.with_name(path.stem + "_composed.mp4")
    assert rc == 1 and data["composed"] is False
    assert data.get("reason")
    assert out.exists() and out.read_bytes() == path.read_bytes()


def test_compose_overrides_title_disable_intro_add_outro(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_clip(Config(root=tmp_path))
    rc = main(["compose", "clip_x", "--title", "CUSTOM", "--intro", "", "--outro", "@moh.flow"])
    data = json.loads(capsys.readouterr().out)
    assert data["title"] == "CUSTOM"
    assert data["intro"] is None
    assert data["outro"] == "@moh.flow"
    assert rc == 1 and data["composed"] is False
