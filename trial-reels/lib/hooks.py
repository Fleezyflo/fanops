"""Hook edit policies for trial reels.

Each policy defines a distinct in/out window (as cite_start percentages) and which
lyric events receive ASS stamps.  Cut always starts at cite_start with length
min(8, remaining).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

HOOK_POLICIES: tuple[str, ...] = (
    "result_first",
    "mid_action",
    "direct_you",
    "bold_claim",
    "cold_proof",
)

MAX_CUT_S = 8.0


@dataclass(frozen=True)
class LyricEvent:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class HookWindow:
    policy: str
    hook_in_s: float
    hook_out_s: float
    cut_start_s: float
    cut_length_s: float


@dataclass(frozen=True)
class PolicySpec:
  name: str
  in_pct: float   # fraction of cite_start → hook visible from here
  out_pct: float  # fraction of cut_length → hook visible until here
  stamp_mode: str  # first_half | middle | you_lines | opening | closing


_POLICY_TABLE: dict[str, PolicySpec] = {
    "result_first": PolicySpec("result_first", in_pct=0.0, out_pct=0.18, stamp_mode="first_half"),
    "mid_action": PolicySpec("mid_action", in_pct=0.22, out_pct=0.58, stamp_mode="middle"),
    "direct_you": PolicySpec("direct_you", in_pct=0.04, out_pct=0.28, stamp_mode="you_lines"),
    "bold_claim": PolicySpec("bold_claim", in_pct=0.0, out_pct=0.14, stamp_mode="first_line"),
    "cold_proof": PolicySpec("cold_proof", in_pct=0.38, out_pct=0.72, stamp_mode="closing"),
}


def policy_spec(policy: str) -> PolicySpec:
    if policy not in _POLICY_TABLE:
        raise ValueError(f"unknown hook policy: {policy!r}")
    return _POLICY_TABLE[policy]


def cut_spec(
    cite_start_s: float,
    total_duration_s: float,
) -> tuple[float, float]:
    """Return (cut_start, cut_length) — start at cite, length min(8, remaining)."""
    remaining = max(0.0, total_duration_s - cite_start_s)
    return cite_start_s, min(MAX_CUT_S, remaining)


def hook_window(
    policy: str,
    *,
    cite_start_s: float,
    total_duration_s: float,
) -> HookWindow:
    """Compute hook in/out and cut window for *policy*."""
    spec = policy_spec(policy)
    cut_start, cut_length = cut_spec(cite_start_s, total_duration_s)
    hook_in = cut_start + spec.in_pct * cut_length
    hook_out = cut_start + spec.out_pct * cut_length
    return HookWindow(
        policy=policy,
        hook_in_s=hook_in,
        hook_out_s=hook_out,
        cut_start_s=cut_start,
        cut_length_s=cut_length,
    )


def _stamp_first_half(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    mid = cut_start + cut_length * 0.5
    return [e for e in events if e.start_s < mid]


def _stamp_middle(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    lo = cut_start + cut_length * 0.25
    hi = cut_start + cut_length * 0.75
    return [e for e in events if lo <= e.start_s <= hi]


def _stamp_you_lines(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    cut_end = cut_start + cut_length
    return [
        e for e in events
        if cut_start <= e.start_s < cut_end and "you" in e.text.lower()
    ]


def _stamp_first_line(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    cut_end = cut_start + cut_length
    in_cut = [e for e in events if cut_start <= e.start_s < cut_end]
    return in_cut[:1]


def _stamp_opening(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    hi = cut_start + cut_length * 0.3
    return [e for e in events if cut_start <= e.start_s <= hi]


def _stamp_closing(events: Sequence[LyricEvent], cut_start: float, cut_length: float) -> list[LyricEvent]:
    lo = cut_start + cut_length * 0.55
    cut_end = cut_start + cut_length
    return [e for e in events if lo <= e.start_s < cut_end]


_STAMPERS = {
    "first_half": _stamp_first_half,
    "middle": _stamp_middle,
    "you_lines": _stamp_you_lines,
    "first_line": _stamp_first_line,
    "opening": _stamp_opening,
    "closing": _stamp_closing,
}


def stamp_lyric_events(
    policy: str,
    events: Sequence[LyricEvent],
    *,
    cite_start_s: float,
    total_duration_s: float,
    drop_last: bool = False,
) -> list[LyricEvent]:
    """Return lyric events that receive ASS stamps for *policy*."""
    spec = policy_spec(policy)
    cut_start, cut_length = cut_spec(cite_start_s, total_duration_s)
    stamper = _STAMPERS[spec.stamp_mode]
    stamped = stamper(events, cut_start, cut_length)
    if drop_last and stamped:
        stamped = stamped[:-1]
    return stamped


def drop_last_lyric(events: Sequence[LyricEvent]) -> list[LyricEvent]:
    """Remove the final lyric — open_loop unresolved edit."""
    if len(events) <= 1:
        return list(events)
    return list(events[:-1])


def policies_differ() -> bool:
    """True when every policy has distinct (in_pct, out_pct, stamp_mode)."""
    specs = list(_POLICY_TABLE.values())
    keys = {(s.in_pct, s.out_pct, s.stamp_mode) for s in specs}
    return len(keys) == len(specs)


def policy_identity_key(policy: str, cite_start_s: float, total_duration_s: float) -> tuple:
    """Hashable identity for a policy at a cite point (used by tests)."""
    w = hook_window(policy, cite_start_s=cite_start_s, total_duration_s=total_duration_s)
    spec = policy_spec(policy)
    return (round(w.hook_in_s, 4), round(w.hook_out_s, 4), spec.stamp_mode)
