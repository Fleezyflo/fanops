"""FFmpeg filter-graph builders for trial-reel edit stacks.

Constraints (lane contract):
- ffmpeg-full only (``FFMPEG_BIN``).
- ASS subtitles via ``subtitles`` filter — no drawtext, Pillow, or PNG overlays.
- ``loudnorm=I=-14`` lives inside ``filter_complex`` on ``[a]``; never pair ``-af`` with ``-filter_complex``.
- Graphs must survive 10-bit ``yuv420p10le`` input (normalise with ``format=yuv420p`` early).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Sequence

FFMPEG_BIN = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

STACK_NAMES: tuple[str, ...] = ("punch_cuts", "open_loop", "fake_out", "end_loop")

PUNCH_MIN_DURATION_S = 3.6
FAKE_OUT_FLASH_S = 0.15
END_LOOP_REPEAT_S = 1.0
OPEN_LOOP_TRIM_END_S = 0.6  # unresolved tail — clip ends before the payoff lyric lands

# Normalise pixel format before any pixel op (10-bit safe).
_FMT = "format=yuv420p"


def resolve_ffmpeg_bin() -> str:
    """Return ffmpeg-full path; fall back to PATH ``ffmpeg`` when homebrew build absent (CI)."""
    if os.path.isfile(FFMPEG_BIN) and os.access(FFMPEG_BIN, os.X_OK):
        return FFMPEG_BIN
    found = shutil.which("ffmpeg")
    if found:
        return found
    return FFMPEG_BIN


@dataclass(frozen=True)
class StackGraph:
    """One stack's ffmpeg graph fragments."""

    stack: str
    filter_complex: str
    maps: tuple[str, ...]
    gate_passes: bool
    has_audio_chain: bool


def stack_gate_passes(stack: str, duration_s: float) -> bool:
    """Return whether *stack* may run on a clip of *duration_s* seconds."""
    if stack == "punch_cuts":
        return duration_s >= PUNCH_MIN_DURATION_S
    if stack == "open_loop":
        return duration_s >= 2.0
    if stack == "fake_out":
        return duration_s >= 1.0
    if stack == "end_loop":
        return duration_s >= END_LOOP_REPEAT_S + 0.5
    raise ValueError(f"unknown stack: {stack!r}")


def _punch_segments(duration_s: float) -> list[tuple[float, float]]:
    """Three jump-cut windows scaled to *duration_s* (equal thirds with 15 % gaps)."""
    third = duration_s / 3.0
    gap = third * 0.15
    seg_len = (third - gap) / 1.0
    segs: list[tuple[float, float]] = []
    for i in range(3):
        base = i * third
        segs.append((base, base + seg_len))
    return segs


def _video_trim_chain(label_in: str, start: float, end: float, label_out: str) -> str:
    return (
        f"[{label_in}]{_FMT},trim=start={start:.6f}:end={end:.6f},"
        f"setpts=PTS-STARTPTS[{label_out}]"
    )


def _audio_trim_chain(label_in: str, start: float, end: float, label_out: str) -> str:
    return (
        f"[{label_in}]atrim=start={start:.6f}:end={end:.6f},"
        f"asetpts=PTS-STARTPTS[{label_out}]"
    )


def _loudnorm_chain(label_in: str, label_out: str = "aout") -> str:
    return f"[{label_in}]loudnorm=I=-14:TP=-1.5:LRA=11[{label_out}]"


def _subtitles_chain(label_in: str, sub_path: str, label_out: str = "vout") -> str:
    escaped = sub_path.replace("'", r"\'")
    return f"[{label_in}]subtitles='{escaped}'[{label_out}]"


def _split_video_chain(video_in: str, count: int) -> tuple[list[str], list[str]]:
    """Return per-branch input labels and optional split filter fragments.

    Intermediate labels (e.g. ``vprep``) may only be consumed once; multi-trim stacks
    need an explicit ``split`` when *count* > 1.
    """
    if count <= 1:
        return [video_in], []
    labels = [f"vsplit{i}" for i in range(count)]
    split_in = "".join(f"[{lbl}]" for lbl in labels)
    return labels, [f"[{video_in}]split={count}{split_in}"]


def _punch_cuts_graph(
    duration_s: float,
    *,
    has_audio: bool,
    sub_path: str | None,
    video_in: str = "0:v",
    audio_in: str = "0:a",
) -> str:
    segs = _punch_segments(duration_s)
    v_inputs, split_parts = _split_video_chain(video_in, len(segs))
    parts: list[str] = list(split_parts)
    v_labels: list[str] = []
    a_labels: list[str] = []
    for i, (s, e) in enumerate(segs):
        vl, al = f"pv{i}", f"pa{i}"
        parts.append(_video_trim_chain(v_inputs[i], s, e, vl))
        v_labels.append(vl)
        if has_audio:
            parts.append(_audio_trim_chain(audio_in, s, e, al))
            a_labels.append(al)
    v_in = "".join(f"[{lbl}]" for lbl in v_labels)
    parts.append(f"{v_in}concat=n=3:v=1:a=0[vcat]")
    v_last = "vcat"
    if has_audio:
        a_in = "".join(f"[{lbl}]" for lbl in a_labels)
        parts.append(f"{a_in}concat=n=3:v=0:a=1[acat]")
        parts.append(_loudnorm_chain("acat"))
    if sub_path:
        parts.append(_subtitles_chain(v_last, sub_path))
        v_last = "vout"
    else:
        parts.append(f"[{v_last}]null[vout]")
    return ";".join(parts)


def _open_loop_graph(
    duration_s: float,
    *,
    has_audio: bool,
    sub_path: str | None,
    video_in: str = "0:v",
    audio_in: str = "0:a",
) -> str:
    """Shorter unresolved cut — trim before the tail so the last lyric never resolves."""
    end = max(0.5, duration_s - OPEN_LOOP_TRIM_END_S)
    parts = [
        _video_trim_chain(video_in, 0.0, end, "vtrim"),
    ]
    if has_audio:
        parts.append(_audio_trim_chain(audio_in, 0.0, end, "atrim"))
        parts.append(_loudnorm_chain("atrim"))
    if sub_path:
        parts.append(_subtitles_chain("vtrim", sub_path))
    else:
        parts.append("[vtrim]null[vout]")
    return ";".join(parts)


def _fake_out_graph(
    duration_s: float,
    width: int,
    height: int,
    *,
    has_audio: bool,
    sub_path: str | None,
    video_in: str = "0:v",
    audio_in: str = "0:a",
) -> str:
    """Insert a 0.15 s black flash via ``color=`` (never ``geq``)."""
    flash_at = max(0.1, duration_s * 0.55)
    pre_end = flash_at
    post_start = flash_at
    post_end = duration_s
    v_inputs, split_parts = _split_video_chain(video_in, 2)
    parts: list[str] = list(split_parts)
    parts.extend([
        _video_trim_chain(v_inputs[0], 0.0, pre_end, "vpre"),
        (
            f"color=c=black:s={width}x{height}:d={FAKE_OUT_FLASH_S:.3f},"
            f"fps=30,{_FMT}[vflash]"
        ),
        _video_trim_chain(v_inputs[1], post_start, post_end, "vpost"),
        "[vpre][vflash][vpost]concat=n=3:v=1:a=0[vcat]",
    ])
    if has_audio:
        parts.extend([
            _audio_trim_chain(audio_in, 0.0, pre_end, "apre"),
            f"anullsrc=r=48000:cl=stereo:d={FAKE_OUT_FLASH_S:.3f}[aflash]",
            _audio_trim_chain(audio_in, post_start, post_end, "apost"),
            "[apre][aflash][apost]concat=n=3:v=0:a=1[acat]",
            _loudnorm_chain("acat"),
        ])
    v_last = "vcat"
    if sub_path:
        parts.append(_subtitles_chain(v_last, sub_path))
    else:
        parts.append("[vcat]null[vout]")
    return ";".join(parts)


def _end_loop_graph(
    duration_s: float,
    *,
    has_audio: bool,
    sub_path: str | None,
    video_in: str = "0:v",
    audio_in: str = "0:a",
) -> str:
    """Repeat the last 1 s after the main body."""
    loop_start = max(0.0, duration_s - END_LOOP_REPEAT_S)
    v_inputs, split_parts = _split_video_chain(video_in, 2)
    parts: list[str] = list(split_parts)
    parts.extend([
        _video_trim_chain(v_inputs[0], 0.0, duration_s, "vbody"),
        _video_trim_chain(v_inputs[1], loop_start, duration_s, "vloop"),
        "[vbody][vloop]concat=n=2:v=1:a=0[vcat]",
    ])
    if has_audio:
        parts.extend([
            _audio_trim_chain(audio_in, 0.0, duration_s, "abody"),
            _audio_trim_chain(audio_in, loop_start, duration_s, "aloop"),
            "[abody][aloop]concat=n=2:v=0:a=1[acat]",
            _loudnorm_chain("acat"),
        ])
    v_last = "vcat"
    if sub_path:
        parts.append(_subtitles_chain(v_last, sub_path))
    else:
        parts.append("[vcat]null[vout]")
    return ";".join(parts)


def build_stack_graph(
    stack: str,
    *,
    duration_s: float,
    width: int = 1080,
    height: int = 1920,
    has_audio: bool = True,
    sub_path: str | None = None,
    video_in: str = "0:v",
    audio_in: str = "0:a",
) -> StackGraph:
    """Build the ``filter_complex`` string and output maps for *stack*."""
    if stack not in STACK_NAMES:
        raise ValueError(f"unknown stack: {stack!r}")
    gate = stack_gate_passes(stack, duration_s)
    if not gate:
        return StackGraph(
            stack=stack,
            filter_complex="",
            maps=(),
            gate_passes=False,
            has_audio_chain=False,
        )

    v_in, a_in = video_in, audio_in
    builders = {
        "punch_cuts": lambda: _punch_cuts_graph(
            duration_s, has_audio=has_audio, sub_path=sub_path, video_in=v_in, audio_in=a_in
        ),
        "open_loop": lambda: _open_loop_graph(
            duration_s, has_audio=has_audio, sub_path=sub_path, video_in=v_in, audio_in=a_in
        ),
        "fake_out": lambda: _fake_out_graph(
            duration_s,
            width,
            height,
            has_audio=has_audio,
            sub_path=sub_path,
            video_in=v_in,
            audio_in=a_in,
        ),
        "end_loop": lambda: _end_loop_graph(
            duration_s, has_audio=has_audio, sub_path=sub_path, video_in=v_in, audio_in=a_in
        ),
    }
    fc = builders[stack]()
    maps: list[str] = ["[vout]"]
    if has_audio:
        maps.append("[aout]")
    return StackGraph(
        stack=stack,
        filter_complex=fc,
        maps=tuple(maps),
        gate_passes=True,
        has_audio_chain=has_audio,
    )


def ffmpeg_cmd(
    stack: str,
    *,
    input_path: str,
    output_path: str,
    duration_s: float,
    width: int = 1080,
    height: int = 1920,
    has_audio: bool = True,
    sub_path: str | None = None,
    cite_start_s: float = 0.0,
    video_in: str = "0:v",
    audio_in: str = "0:a",
    vertical_prep: str | None = None,
) -> list[str]:
    """Assemble a full ffmpeg argv list for *stack* (no ``-af`` when graph carries audio)."""
    graph = build_stack_graph(
        stack,
        duration_s=duration_s,
        width=width,
        height=height,
        has_audio=has_audio,
        sub_path=sub_path,
        video_in=video_in,
        audio_in=audio_in,
    )
    if not graph.gate_passes:
        raise ValueError(f"stack {stack!r} gated off for duration {duration_s:.3f}s")

    bin_path = resolve_ffmpeg_bin()
    cmd = [bin_path, "-y"]
    if cite_start_s > 0.0:
        cmd.extend(["-ss", f"{cite_start_s:.6f}"])
    cmd.extend(["-t", f"{duration_s:.6f}", "-i", input_path])
    fc = graph.filter_complex
    if vertical_prep:
        fc = f"{vertical_prep};{fc}"
    cmd.extend(["-filter_complex", fc])
    for m in graph.maps:
        cmd.extend(["-map", m])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if has_audio:
        cmd.extend(["-c:a", "aac"])
    cmd.append(output_path)
    return cmd


def rehooks_for_stack(stack: str, rehooks_s: Sequence[float]) -> tuple[float, ...]:
    """Return rehook timestamps; ``open_loop`` never rehooks."""
    if stack == "open_loop":
        return ()
    return tuple(rehooks_s)


def scaled_punch_duration(cut_length_s: float) -> float:
    """Effective punch-cuts body length after three jump trims (≈85 % of source)."""
    return cut_length_s * 0.85


def punch_fits_cut(source_duration_s: float, cut_length_s: float) -> bool:
    """True when punch_cuts jump segments fit inside *cut_length_s* after scaling."""
    if source_duration_s < PUNCH_MIN_DURATION_S:
        return False
    needed = scaled_punch_duration(source_duration_s)
    return needed <= cut_length_s + 1e-6


def graph_uses_loudnorm_in_a(filter_complex: str) -> bool:
    return "loudnorm=I=-14" in filter_complex and "[aout]" in filter_complex


def graph_has_no_geq(filter_complex: str) -> bool:
    return "geq=" not in filter_complex and "geq " not in filter_complex


def graph_has_format_normalisation(filter_complex: str) -> bool:
    return _FMT in filter_complex
