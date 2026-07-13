# tests/test_speech_trust_chain.py — Plan G: trusted segments downstream (subs + snap) end-to-end
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import render_moment, snap_window, _trusted_transcript
from fanops import overlay
from tests.fixtures.speech_segments import talk_seg, MUSIC_HALLUC, LEGACY_EN


def _fake_run_writing_clip(captured):
    from pathlib import Path
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    return fake_run


def _vf_of(cmd):
    i = cmd.index("-vf") + 1
    return cmd[i]


def test_talk_window_subs_and_snap_use_trusted_only(tmp_path, mocker, monkeypatch):
    """Talk source: junk boundaries are ignored for snap; junk text is excluded from burned subs."""
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    junk = {**MUSIC_HALLUC, "start": 9.4, "end": 9.8, "text": "junk start"}
    good = talk_seg("they slept on me", start=9.3, end=12.0)
    good_end = talk_seg("watch this part", start=15.0, end=17.2)
    junk_end = {**MUSIC_HALLUC, "start": 16.0, "end": 16.6, "text": "junk end"}
    tr = [junk, good, good_end, junk_end]
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0, language="en", transcript=tr))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10.0, end=16.5, reason="r", state=MomentState.decided, hook=""))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    cmd = captured["cmd"]
    assert float(cmd[cmd.index("-ss") + 1]) == 9.3
    assert round(float(cmd[cmd.index("-ss") + 1]) + float(cmd[cmd.index("-to") + 1]), 1) == 22.0
    ass = next(cfg.clips.glob("*.ass")).read_text(encoding="utf-8")
    assert "they slept on me" in ass and "junk" not in ass.lower()


def test_music_window_junk_excluded_from_subs_and_snap(tmp_path, mocker, monkeypatch):
    """Music/b-roll window: only rejected ASR -> no junk in subs; snap ignores junk boundaries."""
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    tr = [{**MUSIC_HALLUC, "start": 9.6, "end": 10.0, "text": "background noise"},
          {**MUSIC_HALLUC, "start": 21.5, "end": 22.2, "text": "more noise"}]
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=60.0, language="en", transcript=tr))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10.0, end=22.0, reason="r", state=MomentState.decided, hook=""))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert "subtitles=" not in _vf_of(captured["cmd"])     # no trusted lines -> no transcript burn
    cmd = captured["cmd"]
    assert float(cmd[cmd.index("-ss") + 1]) == 10.0        # junk start 9.6 ignored — no trusted boundaries
    assert round(float(cmd[cmd.index("-ss") + 1]) + float(cmd[cmd.index("-to") + 1]), 1) == 22.0
    src = led.sources["src_1"]
    assert _trusted_transcript(src) == []
    assert snap_window(10.0, 22.0, tr) == (9.6, 22.2)    # raw junk WOULD snap — proves the filter matters
    assert snap_window(10.0, 22.0, _trusted_transcript(src)) == (10.0, 22.0)


def test_degraded_legacy_segments_excluded_from_trusted_chain(tmp_path):
    """Legacy segments without quality metadata are degraded — not full-trust downstream consumers."""
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    src = Source(id="src_1", source_path="/x.mp4", language="en",
                 transcript=[{**LEGACY_EN, "start": 0.0, "end": 3.0},
                             talk_seg("trusted words", start=3.0, end=6.0)])
    led.add_source(src)
    trusted = _trusted_transcript(led.sources["src_1"])
    assert len(trusted) == 1 and trusted[0]["text"] == "trusted words"
    assert snap_window(1.0, 5.5, src.transcript) != snap_window(1.0, 5.5, trusted)
