"""ASS subtitle builder for trial Reels/TikTok hooks (ffmpeg/libass burn-in).

Style contract (matches production stamps):
  Noto Naskh Arabic, 72pt, Alignment 8 (top-center), MarginV 320.
White text + heavy black outline reads on saturated purple neon and on dark/bright frames.
"""
from __future__ import annotations

import math
from pathlib import Path

# ASS colours are &HAABBGGRR (alpha + BGR hex).
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"

DEFAULT_FONT = "Noto Naskh Arabic"
DEFAULT_FONTSIZE = 72
DEFAULT_ALIGNMENT = 8  # top-center — Reels/TikTok safe zone
DEFAULT_MARGIN_V = 320
DEFAULT_MARGIN_LR = 80
DEFAULT_OUTLINE = 5
DEFAULT_SHADOW = 2
_HOOK_FADE_MS = 200


def _fmt_ts(seconds: float) -> str:
    """Format non-negative seconds as ASS H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_text(text: str) -> str:
    """Make text safe for an ASS Dialogue field (UTF-8 Arabic passes through)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\N")
    text = text.replace("{", "").replace("}", "")
    return text


def _hook_style_line(
    font: str,
    *,
    fontsize: int = DEFAULT_FONTSIZE,
    alignment: int = DEFAULT_ALIGNMENT,
    margin_v: int = DEFAULT_MARGIN_V,
) -> str:
    return (
        f"Style: HOOK,{font},{fontsize},{_WHITE},{_WHITE},{_BLACK},{_BLACK},"
        f"-1,0,0,0,100,100,0,0,1,{DEFAULT_OUTLINE},{DEFAULT_SHADOW},"
        f"{alignment},{DEFAULT_MARGIN_LR},{DEFAULT_MARGIN_LR},{margin_v},1"
    )


def write_ass(
    events: list[dict],
    font: str = DEFAULT_FONT,
    *,
    width: int = 1080,
    height: int = 1920,
    fontsize: int = DEFAULT_FONTSIZE,
    alignment: int = DEFAULT_ALIGNMENT,
    margin_v: int = DEFAULT_MARGIN_V,
) -> str:
    """Return ASS subtitle text for `events` (list of {start, end, text}).

    Each event is burned with the HOOK style (top-center, thick outline) so it
    stays legible on purple studio lighting and high-contrast footage.
    """
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        _hook_style_line(font, fontsize=fontsize, alignment=alignment, margin_v=margin_v),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    fade = f"{{\\fad(0,{_HOOK_FADE_MS})}}"
    dialogues: list[str] = []
    for ev in events or []:
        text = (ev.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(ev.get("start", 0.0)))
            end = max(start, float(ev.get("end", start + 2.5)))
        except (TypeError, ValueError):
            continue
        dialogues.append(
            f"Dialogue: 0,{_fmt_ts(start)},{_fmt_ts(end)},HOOK,,0,0,0,,{fade}{_escape_text(text)}"
        )
    if not dialogues:
        return ""
    lines.extend(dialogues)
    return "\n".join(lines) + "\n"


def write_ass_file(ass_text: str, path: str | Path) -> Path:
    """Write ASS `ass_text` to `path` (UTF-8) and return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ass_text, encoding="utf-8")
    return p


def hook_legibility_warnings(
    text: str,
    *,
    width: int = 1080,
    fontsize: int = DEFAULT_FONTSIZE,
    max_lines: int = 2,
) -> list[str]:
    """Heuristic warnings when a hook may not fit the top safe zone (fail-open)."""
    hook = (text or "").strip()
    if not hook:
        return []
    em_ratio = 0.45
    usable = max(1, width - 2 * DEFAULT_MARGIN_LR)
    glyph = em_ratio * fontsize
    warns: list[str] = []
    est_lines = int(math.ceil(len(hook) * glyph / usable))
    if est_lines > max_lines:
        warns.append(
            f"hook likely needs ~{est_lines} lines at {fontsize}px — may spill the top safe zone"
        )
    longest = max((len(w) for w in hook.split()), default=0)
    if longest * glyph > usable:
        warns.append(f"hook has a word too wide ({longest} chars) for the {usable}px card")
    return warns
