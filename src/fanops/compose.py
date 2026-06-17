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
import hashlib, json, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Parity with clip.py / overlay.py ffmpeg bound — a produced render re-encodes, same ceiling.
_RENDER_TIMEOUT = 600.0

# M6 (intro-tease): compose had NO fingerprint (it always re-rendered). A prepend render is heavy MoviePy
# that must run LOCK-FREE (the prewarm model), so — exactly like clip._render_fingerprint — this captures
# everything that determines the composed bytes (base + intro asset paths, the plan params, the base
# dimensions the prepend normalizes to) so the in-lock commit can adopt a prewarmed mp4 without re-rendering.
def _compose_fingerprint(base_path: str, intro_path: str, params: dict, base_w: int, base_h: int) -> str:
    payload = {"base": base_path, "intro": intro_path,
               "params": json.dumps(params, sort_keys=True, default=str), "w": base_w, "h": base_h}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

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


# M6 (intro-tease): the compose-PREPEND primitive. compose_clip only WRAPS a base (cards/overlay); this
# prepends a SECOND asset (an aspect-normalized intro video/photo) IN FRONT of the base over a continuous
# music bed (PRD: no silent opener). Same fail-open contract as compose_clip (any failure -> bare base copy,
# return False) PLUS a DURATION-validity gate: a real composite is intro_seconds + base_dur long, so a render
# that drops the intro or the audio bed lands a wrong total and is rejected (the bare clip already shipped).
def _probe(path: str) -> Optional[float]:
    from fanops.clip import _probe_duration                # reuse the ffprobe wrapper (mockable, fail-open None)
    return _probe_duration(path)

def prepend_intro(base_clip_path: str, intro_asset_path: str, out_path: str, *,
                  tease_text: str, intro_seconds: float,
                  timeout: float = _RENDER_TIMEOUT,
                  render: Optional[Callable] = None,
                  probe_duration: Optional[Callable[[str], Optional[float]]] = None,
                  log: Optional[Callable[[str], None]] = None) -> bool:
    """Prepend `intro_asset_path` (shown for `intro_seconds`, with `tease_text` burned over it) before
    `base_clip_path`, writing `out_path`. Returns True iff a VALID composite was produced; FAILS OPEN to a
    byte-copy of the base (returning False) on a missing intro, ANY render error, a missing/empty output, or
    a duration outside impact_cut.DURATION_TOLERANCE of (intro_seconds + base duration). `render(base, intro,
    out, *, tease_text, intro_seconds, timeout)` and `probe_duration(path)` are injectable for tests."""
    from fanops.impact_cut import DURATION_TOLERANCE
    base = Path(base_clip_path)
    if not Path(intro_asset_path).exists():
        _failopen(base, out_path, log, "prepend skipped: intro asset missing — using base clip")
        return False
    renderer = render or _moviepy_prepend_render
    probe = probe_duration or _probe
    try:
        renderer(base_clip_path, intro_asset_path, out_path, tease_text=tease_text,
                 intro_seconds=intro_seconds, timeout=timeout)
    except Exception as exc:                                # ImportError (no moviepy), API drift, render fail
        _failopen(base, out_path, log,
                  f"prepend failed ({type(exc).__name__}: {str(exc)[:160]}) — using base clip")
        return False
    out = Path(out_path)
    if not out.exists() or out.stat().st_size == 0:
        _failopen(base, out_path, log, "prepend produced no output — using base clip")
        return False
    base_dur, actual = probe(base_clip_path), probe(out_path)
    if base_dur is None or actual is None:                  # can't prove validity -> never ship a maybe-broken composite
        _failopen(base, out_path, log, "prepend duration unprobeable — using base clip")
        return False
    expected = base_dur + float(intro_seconds)
    if abs(actual - expected) > DURATION_TOLERANCE:         # dropped intro / lost audio bed -> wrong total
        _failopen(base, out_path, log,
                  f"prepend duration {round(actual, 2)} vs expected {round(expected, 2)} — using base clip")
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


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

def _moviepy_prepend_render(base_clip_path: str, intro_asset_path: str, out_path: str, *,
                            tease_text: str, intro_seconds: float, timeout: float) -> None:
    """The REAL MoviePy 2.x prepend: an aspect-normalized intro (image -> ImageClip, video -> trimmed
    VideoFileClip) shown for `intro_seconds` with `tease_text` burned over it, concatenated BEFORE the base.
    CONTINUOUS AUDIO (PRD): the base's music bed is laid as a SINGLE looped track spanning the whole composite
    (no silent opener, no tail gap, no seam restart); the segments' own audio is dropped in favor of that one
    unbroken bed — correct for music fan clips (the bed, not lip-sync, carries the clip). Lazy import keeps a
    no-[compose] install MoviePy-free; raises on ANY failure so prepend_intro fail-opens to the bare base.
    Runs OUTSIDE any ledger flock (the compose-layer safety property), so a hang never wedges the ledger."""
    from moviepy import VideoFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, afx
    base = VideoFileClip(base_clip_path)
    opened = [base]
    try:
        w, h = base.w, base.h
        if intro_asset_path.lower().endswith(_IMAGE_EXTS):
            intro = ImageClip(intro_asset_path).with_duration(intro_seconds).resized((w, h))
        else:
            iv = VideoFileClip(intro_asset_path); opened.append(iv)
            intro = iv.subclipped(0, min(intro_seconds, iv.duration or intro_seconds)).resized((w, h))
        opened.append(intro)
        tease = (_text_layer(tease_text, TemplateSpec(), w, h, top=False)
                 .with_duration(intro.duration).with_position("center"))
        opened.append(tease)
        intro_card = CompositeVideoClip([intro, tease]); opened.append(intro_card)
        video = concatenate_videoclips([intro_card.without_audio(), base.without_audio()], method="compose")
        opened.append(video)
        total = (intro.duration or intro_seconds) + (base.duration or 0.0)
        bed = base.audio.with_effects([afx.AudioLoop(duration=total)]) if base.audio else None
        final = video.with_audio(bed) if bed is not None else video
        opened.append(final)
        final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                              fps=int(base.fps or 30), logger=None, threads=2)
    finally:
        for c in opened:
            try:
                c.close()
            except Exception:
                pass


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
