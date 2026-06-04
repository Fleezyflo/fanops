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
import re
import shutil
import subprocess
from pathlib import Path

# Sentence/clause boundary for the deterministic hook: split on . ! ? or a newline.
_CLAUSE_SPLIT = re.compile(r"[.!?\n]")

# ASS colours are &HAABBGGRR (alpha+BGR, hex). White text, black outline/shadow for legibility on
# any footage; the hook gets a punchy amber (&H0000C8FF == RGB FFC800) to pop against the subtitle.
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"
_HOOK_COLOR = "&H0000C8FF"

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


def build_ass(segments, *, hook: str | None = None, clip_start: float, clip_end: float,
              width: int = 1080, height: int = 1920, font: str = "Arial Unicode MS") -> str:
    """Return the full text of an ASS subtitle file for the clip window [clip_start, clip_end]
    (source time). `segments` is the SOURCE-time transcript: list[{start,end,text}]. Each segment
    is rebased to clip time and dropped if it does not overlap the window. If `hook` is a non-empty
    string, one HOOK-style Dialogue spans the clip's first min(2.5, clip_len) seconds."""
    clip_len = max(0.0, clip_end - clip_start)
    margin_v = max(10, int(round(height * 0.12)))      # generous bottom margin -> bottom third

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
    # --- [V4+ Styles] : a bold centered SUBTITLE (bottom third) and a punchier HOOK (top third) ---
    # Format columns are the standard libass V4+ set; field order is load-bearing.
    sub_fontsize = max(24, int(round(height * 0.045)))   # ~86 at 1920 tall
    hook_fontsize = max(28, int(round(height * 0.060)))  # larger, ~115 at 1920 tall
    lines += [
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        # SUBTITLE: white text, black outline+shadow, BOLD, Alignment=2 (bottom-centre), bottom-third margin.
        (f"Style: SUBTITLE,{font},{sub_fontsize},{_WHITE},{_WHITE},{_BLACK},{_BLACK},"
         f"-1,0,0,0,100,100,0,0,1,3,2,2,60,60,{margin_v},1"),
        # HOOK: amber text, black outline, BOLD, Alignment=8 (top-centre), top-third margin, LARGER.
        (f"Style: HOOK,{font},{hook_fontsize},{_HOOK_COLOR},{_HOOK_COLOR},{_BLACK},{_BLACK},"
         f"-1,0,0,0,100,100,0,0,1,4,3,8,60,60,{margin_v},1"),
        "",
    ]
    # --- [Events] : Dialogue lines (hook first so it draws beneath/over per layer, then subtitles) ---
    lines += [
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    if hook and hook.strip():
        hook_end = min(2.5, clip_len)
        events.append(
            f"Dialogue: 0,{_fmt_ts(0.0)},{_fmt_ts(hook_end)},HOOK,,0,0,0,,{_escape_text(hook)}"
        )
    for seg in segments:
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        # drop segments that do not overlap [clip_start, clip_end]
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
        ev_start = max(0.0, seg_start - clip_start)
        ev_end = min(clip_end, seg_end) - clip_start
        if ev_end <= 0.0 or ev_end <= ev_start:
            continue                                    # nothing visible after clamping
        text = _escape_text(str(seg.get("text", "")))
        events.append(
            f"Dialogue: 0,{_fmt_ts(ev_start)},{_fmt_ts(ev_end)},SUBTITLE,,0,0,0,,{text}"
        )
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
    p = p.replace(":", "\\:")       # colon is the filter option separator
    p = p.replace(",", "\\,")       # comma separates chained filters
    return f"subtitles='{p}'"


def ffmpeg_has_textfilter() -> bool:
    """True iff this ffmpeg can burn text (the 'subtitles'/'ass' or 'drawtext' filter is present).
    Probes `ffmpeg -hide_banner -filters` ONCE and caches the result in a module global so
    repeated clip renders don't re-spawn ffmpeg. Never raises: if ffmpeg is absent/unspawnable
    (subprocess.run raises FileNotFoundError/OSError before the process starts) or the probe
    fails, returns False (the caller then skips burning subtitles rather than crashing)."""
    global _TEXTFILTER_CACHE
    if _TEXTFILTER_CACHE is not None:
        return _TEXTFILTER_CACHE
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           check=False, capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        _TEXTFILTER_CACHE = ("subtitles" in out) or ("drawtext" in out)
    except (FileNotFoundError, OSError):
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
    cmd = ["ffmpeg", "-y", "-i", base_clip_path, "-vf", subtitles_vf(ass_path),
           "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", out_path]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        shutil.copyfile(base_clip_path, out_path)        # ffmpeg vanished mid-run: fail-open
        return False
    if r.returncode != 0 or not Path(out_path).exists():
        shutil.copyfile(base_clip_path, out_path)
        return False
    return True
