"""Tests for fanops.overlay — transcript->ASS builder + cached ffmpeg text-filter probe.

The builder is a PURE function (no clip.py / ledger dependency): given source-time segments and
a clip window, it rebases each segment into clip time, drops non-overlapping ones, and emits a
styled ASS file (subtitle style bottom-third, optional hook top-third). The path-escaping helper
and the cached capability probe are likewise standalone so a clip render probes ffmpeg once.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import fanops.overlay as overlay
from fanops.overlay import build_ass, write_ass, subtitles_vf, ffmpeg_has_textfilter

import pytest

@pytest.fixture(autouse=True)
def _clean_textfilter_cache():
    # The probe caches in a module global (overlay._TEXTFILTER_CACHE). Hand-resetting it inside
    # each test was fragile: one forgotten reset leaks a True/False into whichever overlay test
    # runs next (order-dependent flakiness) — stage-6 audit. Reset around EVERY test in this file.
    overlay._TEXTFILTER_CACHE = None
    yield
    overlay._TEXTFILTER_CACHE = None


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

def _hook_style_fields(ass: str) -> list[str]:
    """The comma-split fields of the `Style: HOOK,...` row (V4+ Format order)."""
    line = [ln for ln in ass.splitlines() if ln.startswith("Style: HOOK,")][0]
    return line.split(",")

def test_build_ass_hook_is_clean_outline_not_boxed():
    # The hook is the same CLEAN look as the captions — big WHITE bold text with a thick black
    # OUTLINE (BorderStyle 1), NOT an amber-on-box template card (that read as AI slop). Top-centred.
    a = build_ass([], hook="WATCH THIS", clip_start=0.0, clip_end=6.0)
    f = _hook_style_fields(a)
    assert f[15] == "1"                              # BorderStyle 1 = outline+shadow, NOT a box (3)
    assert f[3] == "&H00FFFFFF"                      # PrimaryColour white (no amber)
    assert f[5] == "&H00000000"                      # OutlineColour solid black (no scrim box)
    assert f[18] == "8"                              # Alignment 8 = top-centre

def test_build_ass_hook_fades_in_and_out():
    # An opener should pop in/out, not hard-cut — a produced touch on the first ~2s card.
    a = build_ass([], hook="WATCH THIS", clip_start=0.0, clip_end=6.0)
    hook_line = [ln for ln in a.splitlines() if ",HOOK," in ln][0]
    assert "\\fad(" in hook_line                     # ASS fade override present
    assert hook_line.rstrip().endswith("WATCH THIS")  # the hook text survives after the override tag


def _caption_dialogues(ass_text: str) -> list[str]:
    """Active-caption Dialogue lines (the CAPTION style), excluding any HOOK card."""
    return [ln for ln in _dialogues(ass_text) if ",CAPTION," in ln]


def test_build_ass_active_captions_chunk_into_short_groups():
    # The produced look: a long spoken segment becomes SEVERAL short caption events (a few words
    # each), NOT one bulk line dumped on screen (the AI-slop tell). 7 words, <=3/group -> 3 events.
    segs = [{"start": 0.0, "end": 6.0, "text": "they really slept on me back then"}]  # 7 words
    cap = _caption_dialogues(build_ass(segs, clip_start=0.0, clip_end=6.0, max_words=3))
    assert len(cap) == 3                                  # 7 words / 3 -> 3 groups, 3 events
    # every caption event carries at most max_words words (after the {\fad(..)} override prefix)
    for ln in cap:
        text = ln.split(",,", 1)[1].split("}", 1)[-1]     # drop the leading {\fad(..)} tag
        assert 1 <= len(text.split()) <= 3
    assert ",CAPTION," in cap[0] and "\\fad(" in cap[0]   # styled CAPTION + snappy pop-in


def test_build_ass_uses_word_timestamps_when_present():
    # When whisper word timestamps ride on the segment, each group is timed to its OWN words
    # (first-word-start .. last-word-end), not an even split of the segment.
    seg = {"start": 0.0, "end": 4.0, "text": "alpha beta gamma delta",
           "words": [{"word": "alpha", "start": 0.0, "end": 0.4},
                     {"word": " beta", "start": 0.4, "end": 0.8},
                     {"word": " gamma", "start": 2.0, "end": 2.5},
                     {"word": " delta", "start": 2.5, "end": 3.0}]}
    cap = _caption_dialogues(build_ass([seg], clip_start=0.0, clip_end=4.0, max_words=2))
    assert len(cap) == 2
    # group 1 = "alpha beta" over [0.00, 0.80]; group 2 = "gamma delta" over [2.00, 3.00]
    assert "0:00:00.00" in cap[0] and "0:00:00.80" in cap[0] and "alpha beta" in cap[0]
    assert "0:00:02.00" in cap[1] and "0:00:03.00" in cap[1] and "gamma delta" in cap[1]


def test_build_ass_word_timestamps_with_null_edges_does_not_crash():
    # whisper sometimes emits a word token with a null start/end (typically the first/last word of a
    # segment). The caption builder must NOT raise (float(None)) — it falls back to the segment
    # boundary for the missing edge, and the word's TEXT is still shown.
    seg = {"start": 0.0, "end": 4.0, "text": "alpha beta",
           "words": [{"word": "alpha", "start": None, "end": 0.6},
                     {"word": " beta", "start": 0.6, "end": None}]}
    cap = _caption_dialogues(build_ass([seg], clip_start=0.0, clip_end=4.0, max_words=3))
    assert len(cap) == 1                                  # one group, no crash
    assert "alpha beta" in cap[0]                         # text preserved
    assert "0:00:00.00" in cap[0] and "0:00:04.00" in cap[0]   # null start->seg start, null end->seg end


def test_build_ass_even_splits_without_word_timestamps():
    # No word timestamps -> the segment window is split EVENLY across its groups. 4 words, 2/group
    # over a clip-time [0,4] window -> group1 [0,2], group2 [2,4].
    seg = {"start": 0.0, "end": 4.0, "text": "one two three four"}
    cap = _caption_dialogues(build_ass([seg], clip_start=0.0, clip_end=4.0, max_words=2))
    assert len(cap) == 2
    assert "0:00:00.00" in cap[0] and "0:00:02.00" in cap[0]
    assert "0:00:02.00" in cap[1] and "0:00:04.00" in cap[1]


def test_build_ass_returns_empty_when_nothing_to_burn():
    # No segments and no/blank hook -> "" (nothing to burn). The caller treats "" as a no-op so it
    # never writes a stale event-less .ass or runs ffmpeg's subtitles filter for nothing.
    assert build_ass([], hook=None, clip_start=0.0, clip_end=5.0) == ""
    assert build_ass([], hook="   ", clip_start=0.0, clip_end=5.0) == ""
    # a real hook still produces a file
    assert build_ass([], hook="wait for it", clip_start=0.0, clip_end=5.0) != ""


def test_build_ass_escapes_and_handles_arabic():
    # a curly brace (an ASS override-block delimiter) is stripped so it can't corrupt the event
    braced = build_ass([{"start": 10.0, "end": 12.0, "text": "drop {these}"}],
                       clip_start=8.0, clip_end=14.0)
    brace_dlg = [ln for ln in _caption_dialogues(braced) if "drop" in ln]
    assert brace_dlg and "{these}" not in brace_dlg[0] and "these" in brace_dlg[0]

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


def test_subtitles_vf_escapes_single_quote_in_path():
    # ECC-review fix #2: a single quote in the path closed the wrapping single-quote prematurely,
    # making ffmpeg reject the filter graph (silent on-screen-text loss via fail-open). The path
    # must be embedded with the ffmpeg "'\\''" sequence so the value parses as the literal path.
    vf = subtitles_vf("/clips/it's a clip.ass")
    assert vf.startswith("subtitles='") and vf.endswith("'")
    # No BARE single quote may remain inside the wrapped value (every embedded ' must be the
    # close-quote/escaped-quote/reopen sequence "'\\''"). Strip the outer wrapping quotes, then
    # assert no lone "'" survives that isn't part of that 4-char sequence.
    assert "'\\''" in vf                                  # the embedded quote is ffmpeg-escaped
    assert "it'" not in vf.replace("'\\''", "")           # no unescaped quote leaks through


def test_ffmpeg_has_textfilter_is_cached(monkeypatch):
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


def test_ffmpeg_has_textfilter_absent_does_not_raise(monkeypatch):
    # ffmpeg off PATH: subprocess.run raises FileNotFoundError BEFORE the process starts. The
    # probe must swallow it and return False (a clip render then simply skips burning subtitles)
    # rather than crash an autonomous run.

    def _absent(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    monkeypatch.setattr(overlay.subprocess, "run", _absent)
    assert ffmpeg_has_textfilter() is False


def test_ffmpeg_has_textfilter_timeout_does_not_raise(monkeypatch):
    # `ffmpeg -filters` is instant on a healthy install; a HANG means a broken one. The probe is
    # time-bounded and must swallow TimeoutExpired exactly like ffmpeg-absent — return False
    # (renders skip burning subtitles) rather than crash an autonomous run.

    def _hung(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr(overlay.subprocess, "run", _hung)
    assert ffmpeg_has_textfilter() is False


def test_write_ass_writes_file(tmp_path):
    p = tmp_path / "sub.ass"
    out = write_ass("[Script Info]\nPlayResX: 1080\n", p)
    assert out == p
    assert p.read_text().startswith("[Script Info]")


# RF5: the verbatim-transcript hook fallback (overlay.derive_hook) was DELETED — it lifted raw third-person
# transcript as a title, the exact anti-pattern this PRD starves at the source. Its tests are gone with it.
# INTENDED operator-visible side effect: cmd_compose now defaults its title to the clip's real on-screen
# hook ONLY (mom.hook); a hookless clip yields title=None -> the "nothing to compose" early-out in cli.py,
# with no silent transcript substitute. (The hook that IS present is still burned by build_ass, tested below.)


def test_burn_hook_only_builds_hook_ass_and_cmd(tmp_path, mocker):
    import fanops.overlay as overlay
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # the .ass exists DURING the ffmpeg run; capture its content here (ECC fix #8 unlinks it after)
        ass = list(tmp_path.glob("*.ass"))
        captured["ass_text"] = ass[0].read_text() if ass else ""
        Path(cmd[-1]).write_bytes(b"VARIANT")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.overlay.subprocess.run", side_effect=fake_run)
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920, font="Arial Unicode MS")
    assert ok is True and out.exists()
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "subtitles=" in vf                      # the hook is burned via an ass
    # ATOMIC: ffmpeg writes the .part temp; burn_hook_only os.replace's it onto out_path on success.
    assert captured["cmd"][-1] == str(out) + ".part"   # output is the temp (atomic-write convention)
    # the hook text reached the .ass (read during the run)
    assert "WATCH THIS" in captured["ass_text"]
    # ECC fix #8 + atomic: the intermediate .ass AND the .part temp are cleaned up — no orphans
    assert list(tmp_path.glob("*.ass")) == [] and list(tmp_path.glob("*.part")) == []


def test_burn_hook_only_atomic_no_partial_on_crash(tmp_path, mocker):
    # A crash mid-ffmpeg (the subprocess raises after writing a PARTIAL .part) must NEVER leave a
    # half-written file at out_path — the serve route would otherwise stream a truncated mp4.
    import fanops.overlay as overlay
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    def boom(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"PARTIAL")          # a truncated temp was written...
        raise OSError("ffmpeg crashed")                 # ...then the process dies mid-write
    mocker.patch("fanops.overlay.subprocess.run", side_effect=boom)
    overlay.burn_hook_only(str(base), str(out), "HOOK")
    # fail-open re-copies the base atomically -> out is a COMPLETE base copy, never the PARTIAL temp;
    # and no .part orphan survives.
    assert out.read_bytes() == b"BASE" and list(tmp_path.glob("*.part")) == []

def test_burn_hook_only_failopen_when_no_textfilter(tmp_path, mocker):
    import fanops.overlay as overlay
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
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    ran = mocker.patch("fanops.overlay.subprocess.run")
    ok = overlay.burn_hook_only(str(base), str(out), "", width=1080, height=1920)
    assert ok is False and out.exists() and out.read_bytes() == b"BASE"
    ran.assert_not_called()

def test_burn_hook_only_failopen_on_timeout(tmp_path, mocker):
    # A HUNG hook burn fails OPEN exactly like ffmpeg-absent: the bounded run is killed, the base
    # clip is byte-copied to out_path (the caller still gets a usable per-account file, just
    # hookless) and False is returned — never a raise out of the variation pass.
    import fanops.overlay as overlay
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.overlay.subprocess.run", side_effect=hung)
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920)
    assert ok is False and out.read_bytes() == b"BASE"    # fail-open: usable file, no hook
    assert seen.get("timeout") == 600.0                   # the bound is actually wired


# --- P1 T2: burned-hook legibility guard --------------------------------------------------------
# The hook already burns white with a thick black outline (reads on any footage), so the remaining
# legibility risk is a hook too long to read in its ~2.5s top-card window. hook_legibility_warnings
# is a PURE, fail-open heuristic: it never blocks a clip — the caller logs once and renders anyway.

def test_hook_legibility_clean_for_a_short_hook():
    assert overlay.hook_legibility_warnings("wait for the drop", width=1080, height=1920) == []

def test_hook_legibility_empty_for_no_hook():
    assert overlay.hook_legibility_warnings("", width=1080, height=1920) == []
    assert overlay.hook_legibility_warnings(None, width=1080, height=1920) == []

def test_hook_legibility_warns_on_overlong_hook():
    long_hook = "wait for the absolutely incredible unbelievable final climactic drop here"
    warns = overlay.hook_legibility_warnings(long_hook, width=1080, height=1920)
    assert warns, "an overlong hook should produce a legibility warning"

def test_hook_legibility_warns_on_unbreakable_long_word():
    warns = overlay.hook_legibility_warnings("a" * 60, width=1080, height=1920)
    assert warns, "a single word too wide to fit should warn"


# --- round-3: auto-fit hook font (a 5-6 word hook must FIT 2 lines, not spill 3 lines off the top) ---
def test_hook_fontsize_caps_for_short_hooks():
    cap = int(round(1920 * overlay._HOOK_FONTSIZE_RATIO))
    assert overlay._hook_fontsize("wait for the drop", 1080, 1920) == cap   # short -> the full big cap

def test_hook_fontsize_shrinks_long_hook_and_clears_the_warning():
    # The real round-3 case: a 6-word hook warned at the fixed font; auto-fit drops it just enough to
    # fit 2 lines, so the font is smaller AND the legibility warning is gone.
    h = "been through the worst, came up anyway"
    cap = int(round(1920 * overlay._HOOK_FONTSIZE_RATIO))
    assert overlay._hook_fontsize(h, 1080, 1920) < cap
    assert overlay.hook_legibility_warnings(h, width=1080, height=1920) == []

def test_hook_fontsize_never_below_floor():
    floor = int(round(1920 * overlay._HOOK_FONTSIZE_FLOOR))
    assert overlay._hook_fontsize("x" * 80, 1080, 1920) >= floor

def test_build_ass_burns_a_smaller_font_for_a_long_hook():
    long_h = "been through the worst, came up anyway"
    f_long = _hook_style_fields(build_ass([], hook=long_h, clip_start=0.0, clip_end=6.0))
    f_short = _hook_style_fields(build_ass([], hook="wait for it", clip_start=0.0, clip_end=6.0))
    assert int(f_long[2]) < int(f_short[2])    # the long hook's burned Fontsize is smaller (field 2)

def test_build_supercut_ass_fad_tag_has_no_form_feed_byte():
    # L01: {\\fad(...)} must not embed a literal form-feed (0x0C) from an unescaped \\f in the f-string.
    from fanops.overlay import build_supercut_ass
    ass = build_supercut_ass([{"start": 0.0, "end": 2.0, "text": "hi"}], spans=[(0.0, 5.0)], hook="WATCH")
    assert b"\x0c" not in ass.encode("utf-8")
    assert "\\fad(" in ass
