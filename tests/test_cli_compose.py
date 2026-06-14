# tests/test_cli_compose.py — the `fanops compose` operator verb: guards (unknown clip / missing
# file) + the happy path that builds a TemplateSpec from the clip's hook and hands it to the engine.
# The engine itself (real MoviePy) is covered by test_compose.py; here compose_clip is injected so the
# verb's argument-wiring is tested without a render.
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
        cfg.clips.mkdir(parents=True, exist_ok=True); path.write_bytes(b"BASE")
    led.add_clip(Clip(id="clip_x", parent_id="m1", path=str(path), aspect=Fmt.r9x16, state=ClipState.rendered))
    led.save(); return path


def test_compose_unknown_clip_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["compose", "nope"])
    assert rc == 2 and "no such clip" in capsys.readouterr().out.lower()

def test_compose_missing_file_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); _seed_clip(Config(root=tmp_path), with_file=False)
    rc = main(["compose", "clip_x"])
    assert rc == 2 and "missing on disk" in capsys.readouterr().out.lower()

def test_compose_happy_path_builds_spec_from_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); _seed_clip(Config(root=tmp_path))
    seen = {}
    def fake_compose(base, out, spec, **kw): seen.update(spec=spec, out=out, base=base); return True
    monkeypatch.setattr("fanops.compose.compose_clip", fake_compose)
    rc = main(["compose", "clip_x"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["composed"] is True and data["title"] == "THE DROP"
    assert seen["spec"].title == "THE DROP"                  # default title = the clip's hook
    assert seen["spec"].intro_text == "Moh Flow"            # default branded intro = artist_name
    assert seen["out"].endswith("_composed.mp4")

def test_compose_failopen_exits_1_with_reason(tmp_path, monkeypatch, capsys):
    # fail-open (MoviePy absent / render error) -> the verb exits 1 (so a scripted `compose && upload`
    # can tell a real produced render from the base-copy fallback) and surfaces the reason.
    monkeypatch.chdir(tmp_path); _seed_clip(Config(root=tmp_path))
    def failopen(base, out, spec, *, log=None):
        if log: log("compose failed (ImportError: No module named 'moviepy') — using base clip")
        return False
    monkeypatch.setattr("fanops.compose.compose_clip", failopen)
    rc = main(["compose", "clip_x"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["composed"] is False
    assert "moviepy" in data.get("reason", "").lower()

def test_compose_overrides_title_disable_intro_add_outro(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); _seed_clip(Config(root=tmp_path))
    seen = {}
    monkeypatch.setattr("fanops.compose.compose_clip",
                        lambda base, out, spec, **kw: seen.update(spec=spec) or True)
    rc = main(["compose", "clip_x", "--title", "CUSTOM", "--intro", "", "--outro", "@moh.flow"])
    assert rc == 0
    assert seen["spec"].title == "CUSTOM"
    assert seen["spec"].intro_text is None                   # --intro '' disables the branded card
    assert seen["spec"].outro_text == "@moh.flow"
