"""Burned-in subtitles: build a styled ASS (libass) subtitle file for a clip, plus a cached
ffmpeg text-filter capability probe. PURE functions — no clip.py / ledger dependency, so they
are independently testable and reusable by the clip renderer.

build_ass() takes SOURCE-time segments ([{start,end,text}], the source transcript) and the clip
window [clip_start, clip_end] in source time. Each segment is REBASED into clip time and any
segment that does not overlap the window is dropped, so the .ass timeline starts at 0 and lines
up with the cut clip. The file carries two styles — a bold centered SUBTITLE (bottom third,
white + black outline for legibility) and a punchier HOOK (top third, larger) — and, when a hook
string is given, ONE hook Dialogue spanning the clip's first min(2.5, clip_len) seconds.

subtitles_vf() returns the ffmpeg `-vf` token that burns the .ass (the caller chains it after the
reframe with a comma). ffmpeg_has_textfilter() probes `ffmpeg -filters` ONCE and caches the
result so repeated clip renders don't re-spawn ffmpeg; it never raises if ffmpeg is absent.
"""
from __future__ import annotations
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

# Sentence/clause boundary for the deterministic hook: split on . ! ? or a newline.
_CLAUSE_SPLIT = re.compile(r"[.!?\n]")

# ASS colours are &HAABBGGRR (alpha+BGR, hex). White text + heavy black outline reads on ANY footage
# without a coloured box — the old amber-on-scrim hook card looked like a template (AI slop), so the
# hook now uses the same clean white-bold-outline treatment as the captions, just bigger and on top.
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"
# Fade the opener in/out (milliseconds) so the first-~2s hook pops instead of hard-cutting.
_HOOK_FADE_MS = 200

# Active captions (the "produced" short-form look — CapCut/Submagic style): show a FEW words at a
# time, synced to speech, big and centred, popping in — NOT the whole transcript dumped at once
# (that bulk dump reads as AI slop). A segment is split into <=_CAPTION_MAX_WORDS-word groups; each
# group is its own short Dialogue timed to when those words are spoken (real word timestamps when
# whisper provides them, else the segment window split evenly across its groups). The quick fade is
# the snappy pop-in (faster than the hook's, so captions feel kinetic, not laggy).
_CAPTION_MAX_WORDS = 3
_CAP_FADE_IN_MS = 100
_CAP_FADE_OUT_MS = 60

# Cached result of the ffmpeg text-filter probe. None = not yet probed; once probed it holds the
# bool so repeated clip renders reuse it instead of re-spawning ffmpeg (a per-render cost we pay
# for nothing — the filter set doesn't change within a process). Reset in tests via this name.
_TEXTFILTER_CACHE: bool | None = None


def _fmt_ts(seconds: float) -> str:
    """Format a non-negative time as ASS H:MM:SS.cc (centiseconds, 2 digits). Negative clamps to 0."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))               # centiseconds, rounded (ASS resolution)
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_text(text: str) -> str:
    """Make `text` safe to drop into an ASS Dialogue field:
      - normalise CRLF/CR then turn each newline into the ASS hard line break \\N (libass renders
        a literal '\\N' as a break; a raw newline would terminate the event line and corrupt the file),
      - strip stray braces, since `{...}` is an ASS override-tag block (a curly brace in the
        transcript would otherwise be eaten as styling or break parsing).
    Unicode (Arabic, etc.) passes through untouched — ASS is read as UTF-8."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\N")
    text = text.replace("{", "").replace("}", "")
    return text


# P1 T2 legibility guard (heuristic, fail-open). The hook burns white over a thick black outline (reads
# on any footage), so the residual legibility risk is a hook too LONG to read in its ~2.5s top card. With
# no font metrics available we estimate the rendered line width from a conservative em ratio and WARN —
# never block — when the line would need more than _MAX_HOOK_LINES to fit, or one unbreakable word
# overflows the card. The ratios MUST track build_ass's hook style (fontsize + MarginL/R).
_HOOK_EM_RATIO = 0.45
_MAX_HOOK_LINES = 2
_HOOK_FONTSIZE_RATIO = 0.072        # the CAP hook font ratio (big opener, ~138 at 1920 tall)
_HOOK_FONTSIZE_FLOOR = 0.052        # smallest hook font ratio (~100 at 1920) so a long hook never goes tiny
_HOOK_MARGIN_LR = 60                # == HOOK style MarginL/MarginR in build_ass

def _hook_fontsize(hook: str | None, width: int, height: int) -> int:
    """Largest hook font (<= the _HOOK_FONTSIZE_RATIO cap) that wraps `hook` to <=_MAX_HOOK_LINES lines
    within the usable card width, floored (_HOOK_FONTSIZE_FLOOR) so a long hook never shrinks to
    unreadable. A short hook keeps the big cap; a 5-6 word hook drops just enough to fit 2 lines -> no
    3-line top-safe-area spill and no lost wording. PURE; build_ass AND hook_legibility_warnings size
    from this so they always agree (the warning's ratios MUST track build_ass's hook style)."""
    cap = max(44, int(round(height * _HOOK_FONTSIZE_RATIO)))
    text = (hook or "").strip()
    if not text:
        return cap
    floor = max(40, int(round(height * _HOOK_FONTSIZE_FLOOR)))
    usable = max(1, width - 2 * _HOOK_MARGIN_LR)
    # len(text) chars must fit in _MAX_HOOK_LINES lines: chars/line = usable/(em*font), so
    # font <= lines * usable / (len * em). int() truncates DOWN (a slightly smaller font wraps more
    # safely), then clamp to [floor, cap].
    fit = int((_MAX_HOOK_LINES * usable) / (len(text) * _HOOK_EM_RATIO))
    return max(floor, min(cap, fit))

def hook_legibility_warnings(hook: str | None, *, width: int, height: int) -> list[str]:
    """Return legibility warnings for a burned hook, or [] if it should read fine. PURE + fail-open:
    the caller logs these once and renders the clip regardless (a hook is NEVER blocked). Heuristic
    only — estimates rendered width from a conservative em ratio, so a false warning costs one log line."""
    text = (hook or "").strip()
    if not text:
        return []
    fontsize = _hook_fontsize(text, width, height)   # warn against the SAME auto-fit font build_ass burns
    usable = max(1, width - 2 * _HOOK_MARGIN_LR)
    glyph = _HOOK_EM_RATIO * fontsize
    warns: list[str] = []
    est_lines = int(math.ceil(len(text) * glyph / usable))
    if est_lines > _MAX_HOOK_LINES:
        warns.append(f"hook '{text}' likely needs ~{est_lines} lines at {fontsize}px — may spill the top safe area")
    longest = max((len(w) for w in text.split()), default=0)
    if longest * glyph > usable:
        warns.append(f"hook '{text}' has a word too wide ({longest} chars) for the {usable}px card")
    return warns


def derive_hook(transcript_excerpt: str | None, *, max_words: int = 7) -> str | None:
    """Deterministic top-third hook from a moment's spoken text — NO LLM required.

    Take the FIRST sentence/clause (split on . ! ? or a newline), strip it, and trim to at most
    `max_words` words. Returns None for empty/whitespace-only input (nothing to show). The text is
    returned as-is (no re-casing) so the speaker's words are preserved; a future LLM may overwrite
    Moment.hook with something punchier."""
    if not transcript_excerpt or not transcript_excerpt.strip():
        return None
    # first non-empty clause (a leading delimiter would yield an empty piece first; skip those)
    first = ""
    for piece in _CLAUSE_SPLIT.split(transcript_excerpt):
        if piece.strip():
            first = piece.strip()
            break
    if not first:
        return None
    words = first.split()
    if len(words) > max_words:
        words = words[:max_words]
    return " ".join(words)


def _chunk(items: list, size: int) -> list[list]:
    """Split `items` into consecutive groups of at most `size` (the last group may be shorter)."""
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]


def caption_events(seg: dict, clip_start: float, clip_end: float, *, max_words: int = _CAPTION_MAX_WORDS):
    """Active-caption events for ONE source-time transcript segment, rebased into clip time and
    clamped to the clip window. Returns a list of (start, end, text) tuples — a few words each —
    instead of one bulk line. Two timing modes:
      • word timestamps present (seg['words'] = [{word,start,end}, ...], from whisper
        --word_timestamps): group consecutive words into <=max_words chunks, each timed to its own
        first-word-start .. last-word-end;
      • absent (the common case on already-transcribed footage): split the segment's text into
        <=max_words groups and distribute them EVENLY across the segment's (clip-clamped) window.
    A segment that does not overlap the clip, or yields no visible text, returns []."""
    seg_start = float(seg["start"]); seg_end = float(seg["end"])
    if seg_end <= clip_start or seg_start >= clip_end:
        return []
    out: list[tuple[float, float, str]] = []
    words_meta = seg.get("words")
    has_word_ts = (isinstance(words_meta, list) and words_meta
                   and all(isinstance(w, dict) and "word" in w for w in words_meta))
    if has_word_ts:
        for grp in _chunk(words_meta, max_words):
            txt = "".join(str(w.get("word", "")) for w in grp).strip()   # whisper tokens carry a leading space
            if not txt: continue
            # whisper occasionally emits a null start/end on the first/last word of a segment — a
            # PRESENT key with value None (so .get(key, default) won't fire). Guard explicitly and
            # fall back to the segment boundary so float(None) can't crash the render path.
            raw_s = grp[0].get("start"); gs = float(raw_s) if raw_s is not None else seg_start
            raw_e = grp[-1].get("end"); ge = float(raw_e) if raw_e is not None else seg_end
            ev_s = max(0.0, gs - clip_start); ev_e = min(clip_end, ge) - clip_start
            if ev_e > ev_s: out.append((ev_s, ev_e, txt))
        return out
    words = [w for w in str(seg.get("text", "")).split() if w]            # whitespace-split (eats \n too)
    if not words:
        return []
    groups = _chunk(words, max_words)
    win_s = max(clip_start, seg_start); win_e = min(clip_end, seg_end)
    span = win_e - win_s
    if span <= 0:
        return []
    per = span / len(groups)
    for i, grp in enumerate(groups):
        ev_s = max(0.0, (win_s + i * per) - clip_start)
        ev_e = (win_s + (i + 1) * per) - clip_start
        if ev_e > ev_s: out.append((ev_s, ev_e, " ".join(grp)))
    return out


def build_ass(segments, *, hook: str | None = None, clip_start: float, clip_end: float,
              width: int = 1080, height: int = 1920, font: str = "Arial Unicode MS",
              max_words: int = _CAPTION_MAX_WORDS) -> str:
    """Return the full text of an ASS subtitle file for the clip window [clip_start, clip_end]
    (source time). `segments` is the SOURCE-time transcript: list[{start,end,text[,words]}]. Each
    segment is rebased to clip time, dropped if it does not overlap, and rendered as ACTIVE CAPTIONS
    (a few words at a time, synced to speech — see caption_events), NOT one bulk line. If `hook` is a
    non-empty string, one clean top-third HOOK line (the retention opener) spans the clip's first
    min(2.5, clip_len) seconds. The default clip path passes the moment's retention hook; the
    transcript captions are layered in only when the caller opts in (clip._subtitles_vf / burn_subs)."""
    clip_len = max(0.0, clip_end - clip_start)

    lines: list[str] = []
    # --- [Script Info] : PlayRes drives libass coordinate scaling; must match the render size ---
    lines += [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
    ]
    # --- [V4+ Styles] : a big bold CAPTION (active, lower-third) and a punchier HOOK (top third) ---
    # Format columns are the standard libass V4+ set; field order is load-bearing.
    cap_fontsize = max(48, int(round(height * 0.075)))   # BIG active caption, ~144 at 1920 tall
    cap_margin_v = max(10, int(round(height * 0.16)))    # sit it in the lower third, raised off the edge
    hook_fontsize = _hook_fontsize(hook, width, height)  # auto-fit: big for short hooks, shrinks long ones to 2 lines
    hook_margin_v = max(10, int(round(height * 0.14)))   # sit it in the top third, off the edge
    lines += [
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        # CAPTION: BIG white BOLD text, thick black outline + drop shadow (reads on any footage),
        # Alignment=2 (bottom-centre), lower-third margin. Outline=4 thick, Shadow=2.
        (f"Style: CAPTION,{font},{cap_fontsize},{_WHITE},{_WHITE},{_BLACK},{_BLACK},"
         f"-1,0,0,0,100,100,0,0,1,4,2,2,80,80,{cap_margin_v},1"),
        # HOOK: same clean BIG white BOLD + thick black outline as the caption (NO amber, NO box —
        # that read as a template), Alignment=8 (top-centre), top-third margin. Outline=4, Shadow=2.
        (f"Style: HOOK,{font},{hook_fontsize},{_WHITE},{_WHITE},{_BLACK},{_BLACK},"
         f"-1,0,0,0,100,100,0,0,1,4,2,8,60,60,{hook_margin_v},1"),
        "",
    ]
    # --- [Events] : the optional hook card first, then the active-caption groups ---
    lines += [
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    if hook and hook.strip():
        hook_end = min(2.5, clip_len)
        fade = f"{{\\fad({_HOOK_FADE_MS},{_HOOK_FADE_MS})}}"   # ASS \fad(in,out) ms — produced pop
        events.append(
            f"Dialogue: 0,{_fmt_ts(0.0)},{_fmt_ts(hook_end)},HOOK,,0,0,0,,{fade}{_escape_text(hook)}"
        )
    cap_fade = f"{{\\fad({_CAP_FADE_IN_MS},{_CAP_FADE_OUT_MS})}}"   # snappy active-caption pop-in
    for seg in segments:
        for ev_start, ev_end, text in caption_events(seg, clip_start, clip_end, max_words=max_words):
            events.append(
                f"Dialogue: 0,{_fmt_ts(ev_start)},{_fmt_ts(ev_end)},CAPTION,,0,0,0,,{cap_fade}{_escape_text(text)}"
            )
    if not events:
        return ""                          # nothing to burn — caller treats "" as a no-op (no .ass, no filter)
    lines += events
    return "\n".join(lines) + "\n"


def write_ass(text: str, path) -> Path:
    """Write the .ass text to `path` (UTF-8) and return the path. Parent dirs are created."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p if isinstance(path, Path) else path


def subtitles_vf(ass_path) -> str:
    """Return JUST the ffmpeg `-vf` token that burns the ASS file at `ass_path`:
    `subtitles=<escaped path>`. The caller chains it after the reframe filter with a comma.

    ffmpeg filter-arg escaping: inside a filter, '\\' ',' and especially ':' are special. The
    robust, widely-documented form is to single-quote the whole filename AND backslash-escape the
    ':' (libass on some builds still splits on a bare ':' even inside quotes). We escape '\\' first
    (so we don't double-escape what we add), then ':' then ',' and wrap in single quotes."""
    p = str(ass_path)
    p = p.replace("\\", "\\\\")     # backslash first
    p = p.replace("'", "'\\''")     # ECC fix #2: embed a literal ' via close-quote/esc-quote/reopen
    p = p.replace(":", "\\:")       # colon is the filter option separator
    p = p.replace(",", "\\,")       # comma separates chained filters
    return f"subtitles='{p}'"


# Hard bounds (the llm.py timeout idiom): `-filters` is an instant capability probe — a hang
# means a broken install; the hook burn re-encodes a clip, so it gets the same 10min bound as
# clip.py's render. Both run inside lock-holding passes; unbounded, a hang wedged the flock.
_PROBE_TIMEOUT = 30.0
_FFMPEG_TIMEOUT = 600.0


def ffmpeg_has_textfilter() -> bool:
    """True iff this ffmpeg can burn text (the 'subtitles'/'ass' or 'drawtext' filter is present).
    Probes `ffmpeg -hide_banner -filters` ONCE and caches the result in a module global so
    repeated clip renders don't re-spawn ffmpeg. Never raises: if ffmpeg is absent/unspawnable
    (subprocess.run raises FileNotFoundError/OSError before the process starts), HUNG past the
    probe bound, or the probe fails, returns False (the caller then skips burning subtitles
    rather than crashing)."""
    global _TEXTFILTER_CACHE
    if _TEXTFILTER_CACHE is not None:
        return _TEXTFILTER_CACHE
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           check=False, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        out = (r.stdout or "") + (r.stderr or "")
        _TEXTFILTER_CACHE = ("subtitles" in out) or ("drawtext" in out)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        _TEXTFILTER_CACHE = False
    return _TEXTFILTER_CACHE


def burn_hook_only(base_clip_path: str, out_path: str, hook: str, *,
                   width: int = 1080, height: int = 1920, font: str = "Arial Unicode MS") -> bool:
    """Burn ONLY a hook (top-third) onto an already-rendered base clip -> out_path. Returns True if
    the hook was burned, False if it FAILED OPEN (no text filter or empty hook) — in which case
    out_path is a byte copy of the base clip (the caller still gets a usable per-account file).
    Cheap second pass for per-account creative variation: the base reframe+subtitle render is done
    once; this adds one account's hook."""
    if not hook or not hook.strip() or not ffmpeg_has_textfilter():
        shutil.copyfile(base_clip_path, out_path)        # fail-open: usable file, no hook
        return False
    # hook-only ass: no subtitle segments, hook over the first 2.5s of the (already-cut) base clip.
    ass_text = build_ass([], hook=hook, clip_start=0.0, clip_end=2.5, width=width, height=height, font=font)
    ass_path = str(Path(out_path).with_suffix(".ass"))
    write_ass(ass_text, ass_path)
    # ECC fix #8: the intermediate .ass is a render artifact, not an output — unlink it in a finally
    # so a failed/hung ffmpeg doesn't leave an orphan beside every per-account variant (unbounded
    # accumulation on high-volume runs). Best-effort: a missing/locked file never masks the result.
    try:
        cmd = ["ffmpeg", "-y", "-i", base_clip_path, "-vf", subtitles_vf(ass_path),
               "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", out_path]
        try:
            r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            shutil.copyfile(base_clip_path, out_path)    # ffmpeg vanished or hung: fail-open
            return False
        if r.returncode != 0 or not Path(out_path).exists():
            shutil.copyfile(base_clip_path, out_path)
            return False
        return True
    finally:
        try: os.unlink(ass_path)
        except OSError: pass
