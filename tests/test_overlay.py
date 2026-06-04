"""Tests for fanops.overlay — transcript->ASS builder + cached ffmpeg text-filter probe.

The builder is a PURE function (no clip.py / ledger dependency): given source-time segments and
a clip window, it rebases each segment into clip time, drops non-overlapping ones, and emits a
styled ASS file (subtitle style bottom-third, optional hook top-third). The path-escaping helper
and the cached capability probe are likewise standalone so a clip render probes ffmpeg once.
"""
from __future__ import annotations
from pathlib import Path

import fanops.overlay as overlay
from fanops.overlay import build_ass, write_ass, subtitles_vf, ffmpeg_has_textfilter, derive_hook


def _dialogues(ass_text: str) -> list[str]:
    """The Dialogue lines from an [Events] section (one subtitle/hook event per line)."""
    return [ln for ln in ass_text.splitlines() if ln.startswith("Dialogue:")]


def test_build_ass_rebases_segment_times():
    # source-time segment [10,12] in a clip starting at 8.0 -> clip-time [2.00, 4.00]
    ass = build_ass([{"start": 10.0, "end": 12.0, "text": "hi"}], clip_start=8.0, clip_end=14.0)
    dlg = _dialogues(ass)
    assert any("0:00:02.00" in ln and "0:00:04.00" in ln and "hi" in ln for ln in dlg), dlg


def test_build_ass_drops_nonoverlapping_segments():
    segs = [
        {"start": 0.0, "end": 4.0, "text": "BEFORE"},     # entirely before clip_start (8.0)
        {"start": 20.0, "end": 25.0, "text": "AFTER"},    # entirely after clip_end (14.0)
        {"start": 10.0, "end": 12.0, "text": "INSIDE"},   # overlaps -> kept
    ]
    ass = build_ass(segs, clip_start=8.0, clip_end=14.0)
    assert "INSIDE" in ass
    assert "BEFORE" not in ass
    assert "AFTER" not in ass


def test_build_ass_includes_hook_when_present():
    # hook present -> exactly one HOOK-style Dialogue carrying the hook text, starting at 0
    with_hook = build_ass([{"start": 10.0, "end": 12.0, "text": "hi"}],
                          hook="WATCH THIS", clip_start=8.0, clip_end=14.0)
    hook_lines = [ln for ln in _dialogues(with_hook) if "WATCH THIS" in ln]
    assert len(hook_lines) == 1, hook_lines
    assert ",HOOK," in hook_lines[0]            # rendered on the HOOK style
    assert "0:00:00.00" in hook_lines[0]        # hook starts at clip time 0

    # absent hook -> no hook Dialogue at all
    no_hook = build_ass([{"start": 10.0, "end": 12.0, "text": "hi"}], clip_start=8.0, clip_end=14.0)
    assert ",HOOK," not in no_hook


def test_build_ass_escapes_and_handles_arabic():
    # newline -> ASS \N hard line break (literal backslash-N, not a real newline inside the event)
    nl = build_ass([{"start": 10.0, "end": 12.0, "text": "line one\nline two"}],
                   clip_start=8.0, clip_end=14.0)
    nl_dlg = [ln for ln in _dialogues(nl) if "line one" in ln][0]
    assert "\\N" in nl_dlg
    assert "line one\nline two" not in nl_dlg   # the raw newline must not survive inside the event

    # an Arabic string round-trips unmangled
    arabic = "مرحبا بالعالم"
    ar = build_ass([{"start": 10.0, "end": 12.0, "text": arabic}], clip_start=8.0, clip_end=14.0)
    assert arabic in ar


def test_subtitles_vf_escapes_path():
    vf = subtitles_vf("/a/b c.ass")
    assert vf.startswith("subtitles=")
    # ffmpeg filter-arg escaping: ':' is special inside a filter and must be backslash-escaped
    # if it appears in a path. Wrapping the filename in single quotes is the standard form.
    assert "/a/b c.ass" in vf or "/a/b\\ c.ass" in vf  # path present (quoted or space-escaped)


def test_subtitles_vf_escapes_colon_in_path():
    # a colon in the path (Windows-ish / odd mount) must be backslash-escaped per filter-arg rules
    vf = subtitles_vf("/weird:dir/x.ass")
    assert "subtitles=" in vf
    assert "\\:" in vf                          # the ':' is escaped, never left bare


def test_ffmpeg_has_textfilter_is_cached(monkeypatch):
    overlay._TEXTFILTER_CACHE = None            # reset the module-global cache for a clean probe
    calls = {"n": 0}

    class _R:                                   # mirrors subprocess.CompletedProcess(capture_output)
        stdout = "Filters:\n subtitles  ...\n drawtext  ...\n"
        stderr = ""
        returncode = 0

    def _counting_run(*a, **kw):
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(overlay.subprocess, "run", _counting_run)
    first = ffmpeg_has_textfilter()
    second = ffmpeg_has_textfilter()
    assert first is True and second is True
    assert calls["n"] <= 1                      # cached: probe runs at most ONCE
    overlay._TEXTFILTER_CACHE = None            # leave the cache clean for other tests


def test_ffmpeg_has_textfilter_absent_does_not_raise(monkeypatch):
    # ffmpeg off PATH: subprocess.run raises FileNotFoundError BEFORE the process starts. The
    # probe must swallow it and return False (a clip render then simply skips burning subtitles)
    # rather than crash an autonomous run.
    overlay._TEXTFILTER_CACHE = None

    def _absent(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    monkeypatch.setattr(overlay.subprocess, "run", _absent)
    assert ffmpeg_has_textfilter() is False
    overlay._TEXTFILTER_CACHE = None


def test_write_ass_writes_file(tmp_path):
    p = tmp_path / "sub.ass"
    out = write_ass("[Script Info]\nPlayResX: 1080\n", p)
    assert out == p
    assert p.read_text().startswith("[Script Info]")


def test_derive_hook_takes_punchy_first_clause():
    # the FIRST clause (split on . ! ? or newline), stripped — a deterministic top-third line
    # with NO LLM. Here "They slept on me" (4 words) before the first period.
    assert derive_hook("They slept on me. Not anymore, watch this whole thing.") == "They slept on me"

    # empty / whitespace-only input -> None (no hook to show)
    assert derive_hook("") is None
    assert derive_hook("   \n  ") is None

    # a first clause longer than max_words is trimmed to max_words words (default 7)
    long = "one two three four five six seven eight nine. trailing clause"
    assert derive_hook(long) == "one two three four five six seven"
    assert len(derive_hook(long).split()) == 7
    # explicit max_words is honoured
    assert derive_hook(long, max_words=3) == "one two three"


def test_burn_hook_only_builds_hook_ass_and_cmd(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"VARIANT")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.overlay.subprocess.run", side_effect=fake_run)
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920, font="Arial Unicode MS")
    assert ok is True and out.exists()
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "subtitles=" in vf                      # the hook is burned via an ass
    assert captured["cmd"][-1] == str(out)         # output is last (matches fake_run + clip.py convention)
    # a .ass containing the hook text was written next to the output
    ass = list(tmp_path.glob("*.ass"))
    assert ass and "WATCH THIS" in ass[0].read_text()

def test_burn_hook_only_failopen_when_no_textfilter(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=False)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    ran = mocker.patch("fanops.overlay.subprocess.run")
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920)
    assert ok is False                              # fail-open: signalled no burn
    assert out.exists() and out.read_bytes() == b"BASE"   # output is a copy of the base, unchanged
    ran.assert_not_called()                         # no ffmpeg invoked

def test_burn_hook_only_failopen_when_hook_empty(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    ran = mocker.patch("fanops.overlay.subprocess.run")
    ok = overlay.burn_hook_only(str(base), str(out), "", width=1080, height=1920)
    assert ok is False and out.exists() and out.read_bytes() == b"BASE"
    ran.assert_not_called()
