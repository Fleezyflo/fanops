"""Produced-clip compositing (operator-chosen 2026-06-14). Wraps a rendered base clip with
multi-layer TEMPLATE cards (optional intro/outro), a dynamic TITLE text layer, and CROSSFADE
transitions — the produced-content gap that libass/ffmpeg burn-in does NOT cover (that path stays in
clip.py/overlay.py). MoviePy (2.x ONLY — no moviepy.editor) is the OPTIONAL [compose] extra, imported
LAZILY inside the renderer so a no-[compose] install never touches it.

EVERYTHING FAILS OPEN, mirroring overlay.burn_hook_only: an empty spec, a missing MoviePy, an API
drift / render error, or a missing/empty output all degrade to a byte-copy of the base clip so the
caller ALWAYS ends up with a usable file at out_path (return False signals "used the base, did not
compose"; True signals a real composed render). Runs OUTSIDE any ledger flock (an operator verb), so
a long or hung render never wedges the ledger — the one safety property that keeps a heavy in-process
MoviePy render off the autonomous lock-holding path."""
from __future__ import annotations
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Parity with clip.py / overlay.py ffmpeg bound — a produced render re-encodes, same ceiling.
_RENDER_TIMEOUT = 600.0

# Cross-platform font fallback when the caller gives no font path. MoviePy 2.x TextClip wants a font
# FILE (not a family name), so a bare "Arial Unicode MS" won't resolve — we hand it a real .ttf/.ttc.
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


@dataclass(frozen=True)
class TemplateSpec:
    """What to composite onto the base clip — ALL optional, so an empty spec is a no-op (fail-open
    copy). `title`: dynamic text over the clip's first `title_sec`. `intro_text`/`outro_text`: brand
    cards before/after the clip. `transition_sec`: crossfade duration between cards and the clip.
    `brand_rgb`: card background. `font`: a font FILE path (None -> a system fallback is resolved)."""
    title: Optional[str] = None
    intro_text: Optional[str] = None
    outro_text: Optional[str] = None
    title_sec: float = 2.5
    card_sec: float = 1.5
    transition_sec: float = 0.5
    brand_rgb: tuple[int, int, int] = (20, 10, 40)
    font: Optional[str] = None
    # NB: no width/height — the renderer takes card + title dimensions from the BASE clip's actual
    # size (base.w/base.h), so the produced clip always matches the source aspect; a spec size would
    # be a dead, misleading field.

    def is_empty(self) -> bool:
        return not (self.title or self.intro_text or self.outro_text)


def _default_font() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _failopen(base: Path, out_path: str, log: Optional[Callable[[str], None]], reason: str) -> None:
    """Copy the base clip to out_path so the caller still gets a usable file. Logs the reason once.
    A copy failure (e.g. base missing) is swallowed — the caller verifies existence and reports."""
    if log:
        log(reason)
    try:
        if str(base) != str(out_path):
            shutil.copyfile(base, out_path)
    except OSError as exc:                              # disk full / base vanished — don't hide it
        if log:
            log(f"fail-open copy also failed ({type(exc).__name__}: {str(exc)[:120]}) — no usable output")


def compose_clip(base_clip_path: str, out_path: str, spec: TemplateSpec, *,
                 timeout: float = _RENDER_TIMEOUT,
                 render: Optional[Callable] = None,
                 log: Optional[Callable[[str], None]] = None) -> bool:
    """Produce `out_path` from `base_clip_path` per `spec`. Returns True iff MoviePy produced a real
    composed file; FAILS OPEN to a byte-copy of the base clip (returning False) on an empty spec,
    missing MoviePy, ANY render error, or a missing/empty output. `render(base, out, spec, *,
    timeout)` is injectable for tests; the default is the real MoviePy 2.x renderer."""
    base = Path(base_clip_path)
    if spec.is_empty():
        _failopen(base, out_path, log, "compose skipped: empty template spec — using base clip")
        return False
    renderer = render or _moviepy_render
    try:
        renderer(base_clip_path, out_path, spec, timeout=timeout)
    except Exception as exc:                            # ImportError (no moviepy), API drift, render fail
        _failopen(base, out_path, log,
                  f"compose failed ({type(exc).__name__}: {str(exc)[:160]}) — using base clip")
        return False
    out = Path(out_path)
    if not out.exists() or out.stat().st_size == 0:
        _failopen(base, out_path, log, "compose produced no output — using base clip")
        return False
    return True


def _text_layer(text: str, spec: TemplateSpec, w: int, h: int, *, top: bool):
    """A centered, stroked caption TextClip sized to the frame (MoviePy 2.x: font is a FILE path,
    font_size/text/color kwargs, method='caption' wraps to `size`). Raises if MoviePy/font is
    unusable — compose_clip's caller fail-opens."""
    from moviepy import TextClip
    font = spec.font or _default_font()
    font_size = max(28, int(round(h * (0.060 if top else 0.050))))
    kwargs = dict(text=text, font_size=font_size, color="white", stroke_color="black",
                  stroke_width=2, size=(int(w * 0.9), None), method="caption", text_align="center")
    if font:
        kwargs["font"] = font
    return TextClip(**kwargs)


def _moviepy_render(base_clip_path: str, out_path: str, spec: TemplateSpec, *, timeout: float) -> None:
    """The REAL MoviePy 2.x render: [intro card] -x-> [base clip + dynamic title] -x-> [outro card],
    crossfaded. Lazy import keeps a no-[compose] install MoviePy-free. Raises on ANY failure so the
    caller fail-opens to the base clip. `timeout` is advisory here (in-process render); the safety
    contract is that this runs OUTSIDE any ledger lock, so a hang never wedges the ledger."""
    from moviepy import (VideoFileClip, ColorClip, CompositeVideoClip,
                         concatenate_videoclips, vfx)
    base = VideoFileClip(base_clip_path)
    opened = [base]
    try:
        w, h = base.w, base.h
        tdur = max(0.0, float(spec.transition_sec))

        def _card(text: str):
            bg = ColorClip(size=(w, h), color=spec.brand_rgb).with_duration(spec.card_sec)
            opened.append(bg)
            layers = [bg]
            if text:
                txt = (_text_layer(text, spec, w, h, top=False)
                       .with_duration(spec.card_sec).with_position("center"))
                opened.append(txt)
                layers.append(txt)
            card = CompositeVideoClip(layers)
            opened.append(card)                        # track so the finally closes its handles too
            return card

        # main = base clip + dynamic title overlay over its first title_sec
        main_layers = [base]
        if spec.title:
            tsec = min(spec.title_sec, base.duration or spec.title_sec)
            title = (_text_layer(spec.title, spec, w, h, top=True)
                     .with_duration(tsec).with_start(0.0)
                     .with_position(("center", int(h * 0.08))))
            if tdur > 0:
                title = title.with_effects([vfx.CrossFadeIn(min(tdur, tsec)),
                                            vfx.CrossFadeOut(min(tdur, tsec))])
            opened.append(title)
            main_layers.append(title)
        main = CompositeVideoClip(main_layers)
        opened.append(main)

        segments = []
        if spec.intro_text:
            segments.append(_card(spec.intro_text))
        segments.append(main)
        if spec.outro_text:
            segments.append(_card(spec.outro_text))

        if len(segments) == 1:
            final = segments[0]
        elif tdur > 0:                                  # crossfade by overlapping each next segment
            segments = [segments[0]] + [s.with_effects([vfx.CrossFadeIn(tdur)]) for s in segments[1:]]
            final = concatenate_videoclips(segments, method="compose", padding=-tdur)
        else:
            final = concatenate_videoclips(segments, method="compose")
        opened.append(final)

        final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                              fps=int(base.fps or 30), logger=None, threads=2)
    finally:
        for c in opened:
            try:
                c.close()
            except Exception:
                pass
