# src/fanops/framing_outcomes.py
"""The framing-outcome CONTRACT: why a clip was framed the way it was, as first-class data.

`clip._resolve_framing` erases the reason a clip was centred. A detection FAILURE and a genuinely
EMPTY ROOM arrive at it as the same value, three erasures deep:

  * `keyframes._run_grid_extract` — ffmpeg absent, a timeout, `rc != 0`, and `rc == 0` with an empty
    glob ALL collapse to one `[]`.
  * `framing.detect_window` — `frames == []` and `except Exception` both collapse to one `None`.
  * `framing.classify_window` — `_face_count(None)` is 0, so a FAILED detection MANUFACTURES
    `CT_NOPEOPLE`. Its own docstring says it: "Stats None -> no-people".

A clip centred because ffmpeg was MISSING is therefore indistinguishable from one centred because
there was NO FACE. This module makes the difference observable, so `fp_new == fp_old` stops being
mistaken for evidence of a legitimate centre.

Two vocabularies, deliberately distinct:

  * `FramingEventType` — DIAGNOSTIC. Appended by the lower layers (keyframes, the detector, each
    strategy) as they run. Authorizes nothing. Many per resolution.
  * `FramingOutcome` — FINAL. Set ONCE by the resolver, after routing completes. Exactly one per
    resolution. There are TWO legitimate centres — `CENTERED_NO_SUBJECT` (the detector completed and
    found no subject) and `CENTERED_MULTI_UNTRACKED` (E3: a real two-shot we could not cleanly track,
    conservatively centred so neither speaker is cropped out); everything a hard failure touched is
    `UNRESOLVED` and carries its `root_cause`.

DEPENDENCY-NEUTRAL BY CONTRACT: stdlib only. This module must NEVER import `framing`, `keyframes`,
or `clip` — they import IT. That keeps the graph acyclic even through the lazy in-function imports
(`framing.py` imports `keyframes` inside three functions), and it is what lets the contract be
unit-tested with no cv2, no ffmpeg, and no Config."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ResolverInvariantError(RuntimeError):
    """A StrategyAttempt / FramingResolution invariant was violated. This is a BUG in the resolver,
    never an input condition — it is raised, never fail-opened, so an invalid intermediate state can
    not leak into a manifest and be read as evidence."""


class UnknownFramingOutcome(RuntimeError):
    """A FramingOutcome the classifier has no rule for. Raised rather than silently mapped: a new
    outcome that quietly fell into a default bucket is exactly the erasure this module exists to end."""


class FramingEventType(str, Enum):
    """One observable thing that happened. DIAGNOSTIC — appended by lower layers; authorizes nothing."""
    # --- conclusive negatives: the strategy RAN TO COMPLETION and found nothing. NOT failures. ---
    NO_PEOPLE = "no_people"                       # detection succeeded; the room is empty
    NO_FACE = "no_face"                           # detection succeeded; too few frames carried a face
    NO_MOTION = "no_motion"                       # frames extracted; the pixel layer found no motion
    NO_TRACK = "no_track"                         # frames + faces; no real 2-shot to follow
    # --- hard failures: the strategy could not run to a conclusion. ---
    NO_FRAMES = "no_frames"                       # ffmpeg exited 0 and wrote nothing
    FFMPEG_UNAVAILABLE = "ffmpeg_unavailable"     # ffmpeg absent from PATH
    FRAME_EXTRACTION_FAILED = "frame_extraction_failed"   # ffmpeg ran and failed (rc != 0 / timeout / OSError)
    DETECTOR_RUNTIME_FAILED = "detector_runtime_failed"   # the detector/pixel pass raised mid-window
    INVALID_WINDOW = "invalid_window"             # not (end > start)
    STRATEGY_RAISED = "strategy_raised"           # an exception inside a STRATEGY span
    DETECTION_RAISED = "detection_raised"         # an exception in the DETECTION phase, before any span opened
    STAGE_LOCK_BUSY = "stage_lock_busy"           # StageBusyError — a producer lock we could not take
    CV2_UNAVAILABLE = "cv2_unavailable"           # ‡ legacy `_rt=None` path only
    MODEL_MISSING = "model_missing"               # ‡ legacy `_rt=None` path only
    DETECTOR_INIT_FAILED = "detector_init_failed"  # ‡ legacy `_rt=None` path only
    UNKNOWN = "unknown"                           # a None nobody attributed — NEVER read as benign
    # --- positives ---
    FACES_DETECTED = "faces_detected"             # the detector ran to completion over the grid
    TRACK_ASSEMBLED = "track_assembled"
    FOCUS_PLACED = "focus_placed"
    MOTION_PLACED = "motion_placed"


# ‡ CV2_UNAVAILABLE / MODEL_MISSING / DETECTOR_INIT_FAILED are unreachable on the production `_rt`
# path — `framing._framing_runtime_or_raise` has already raised ToolchainMissingError by then. They
# are kept because the legacy `_rt=None` self-build path (framing.detect_window) still emits them and
# the Layer-1 characterization exercises it.

_FE = FramingEventType

HARD_FAILURE_EVENTS = frozenset({
    _FE.NO_FRAMES, _FE.FFMPEG_UNAVAILABLE, _FE.FRAME_EXTRACTION_FAILED, _FE.DETECTOR_RUNTIME_FAILED,
    _FE.INVALID_WINDOW, _FE.STRATEGY_RAISED, _FE.DETECTION_RAISED, _FE.STAGE_LOCK_BUSY,
    _FE.CV2_UNAVAILABLE, _FE.MODEL_MISSING, _FE.DETECTOR_INIT_FAILED, _FE.UNKNOWN})
NEGATIVE_RESULT_EVENTS = frozenset({_FE.NO_PEOPLE, _FE.NO_FACE, _FE.NO_MOTION, _FE.NO_TRACK})
POSITIVE_EVENTS = frozenset({_FE.FACES_DETECTED, _FE.TRACK_ASSEMBLED, _FE.FOCUS_PLACED, _FE.MOTION_PLACED})


class FramingOutcome(str, Enum):
    """The FINAL verdict for one resolution. Set ONCE, by the resolver, after routing completes."""
    DETECTED_MULTI = "detected_multi"             # a real 2-shot -> active-speaker track
    DETECTED_SINGLE = "detected_single"           # a single subject -> static lock
    MUSIC_FOCUS = "music_focus"                   # a subject in a music/performance window
    MOTION_FOCUS = "motion_focus"                 # no face -> follow the action
    CENTERED_NO_SUBJECT = "centered_no_subject"   # a legitimate centre: the detector found no subject
    CENTERED_MULTI_UNTRACKED = "centered_multi_untracked"  # E3: a real 2-shot with no clean track -> conservative centre
    CENTERED_PIP_LAYOUT = "centered_pip_layout"   # S4/D2: a presenter-dominant PIP grid, recognised as such and kept
                                                  # OUT of the active-speaker path (F4). Still centred — composing it
                                                  # is S5's slice — but the centre is now an identified layout, not an
                                                  # untracked "two-shot" we failed to follow.
    STACKED_PAIR = "stacked_pair"                 # S2/D1-A: a genuine wide two-shot, no track -> subject-derived vertical stack (both retained)
    SUBJECT_LOCKED = "subject_locked"             # S3/D1-B: one persistently dominant host, no track -> mild re-anchor onto THAT host
    PIP_PRESENTER_FRAMED = "pip_presenter_framed"  # S5/D2: a PIP grid's presenter re-anchored out of the dead space (F3/F2)
    UNRESOLVED = "unresolved"                     # carries root_cause: FramingEventType


_FO = FramingOutcome
RESOLVED_OUTCOMES = frozenset({_FO.DETECTED_MULTI, _FO.DETECTED_SINGLE, _FO.MUSIC_FOCUS, _FO.MOTION_FOCUS,
                               _FO.STACKED_PAIR, _FO.SUBJECT_LOCKED, _FO.PIP_PRESENTER_FRAMED})
LEGITIMATE_CENTER_OUTCOMES = frozenset({_FO.CENTERED_NO_SUBJECT, _FO.CENTERED_MULTI_UNTRACKED,
                                        _FO.CENTERED_PIP_LAYOUT})
UNRESOLVED_OUTCOMES = frozenset({_FO.UNRESOLVED})


class FramingStrategy(str, Enum):
    SPEAKER_TRACK = "speaker_track"
    SUBJECT_FOCUS = "subject_focus"
    SUBJECT_PAIR = "subject_pair"                 # S2/D1-A: compose BOTH persistent hosts into a vertical stack (no track needed)
    SUBJECT_LOCK = "subject_lock"                 # S3/D1-B: re-anchor the crop onto the ONE dominant host (no track needed)
    PIP_LAYOUT = "pip_layout"                     # S4/D2: a presenter-dominant PIP grid — routed AWAY from active-speaker
    MOTION_SALIENCY = "motion_saliency"
    CENTERED = "centered"                         # not a strategy that runs — the terminal fallback


class StrategyState(str, Enum):
    NOT_APPLICABLE = "not_applicable"             # routing never included it for this content type
    SKIPPED = "skipped"                           # included, but a prior strategy resolved first
    FAILED = "failed"                             # a hard failure, or an unattributed nothing
    COMPLETED = "completed"                       # ran to a conclusion: a focus, or a conclusive negative


def _partition_or_raise() -> None:
    """The three event sets and the three outcome sets each PARTITION their enum. Checked at import —
    with a real raise, not `assert` (which `python -O` strips): a new member that silently belongs to
    no set would be classified by omission, which is the failure mode this module exists to end."""
    ev = HARD_FAILURE_EVENTS | NEGATIVE_RESULT_EVENTS | POSITIVE_EVENTS
    if ev != set(FramingEventType):
        raise ResolverInvariantError(f"FramingEventType not partitioned: {set(FramingEventType) ^ ev}")
    if (HARD_FAILURE_EVENTS & NEGATIVE_RESULT_EVENTS) or (HARD_FAILURE_EVENTS & POSITIVE_EVENTS) \
            or (NEGATIVE_RESULT_EVENTS & POSITIVE_EVENTS):
        raise ResolverInvariantError("FramingEventType sets overlap")
    oc = RESOLVED_OUTCOMES | LEGITIMATE_CENTER_OUTCOMES | UNRESOLVED_OUTCOMES
    if oc != set(FramingOutcome):
        raise ResolverInvariantError(f"FramingOutcome not partitioned: {set(FramingOutcome) ^ oc}")
    if (RESOLVED_OUTCOMES & LEGITIMATE_CENTER_OUTCOMES) or (RESOLVED_OUTCOMES & UNRESOLVED_OUTCOMES) \
            or (LEGITIMATE_CENTER_OUTCOMES & UNRESOLVED_OUTCOMES):
        raise ResolverInvariantError("FramingOutcome sets overlap")


_partition_or_raise()


# ---- Evidence: allowlisted, JSON-safe, redacted ----------------------------------------------------
# An exception contributes `type(e).__name__` ONLY — never `str(e)`. No paths, no filenames, no stderr.
# The manifest is an artifact an operator reads and may share; it carries no source paths and no
# message text that could leak a filesystem layout or a token. Anything not on the allowlist, and any
# value that looks like a path or a message, is DROPPED AT CONSTRUCTION rather than sanitized later.
_EVIDENCE_KEYS = frozenset({"frames", "rc", "exc_type", "faces", "conf", "fps", "width", "window_s", "segments"})
_MAX_EVIDENCE_STR = 64


def redact_evidence(raw: dict | None) -> dict:
    """Allowlist + type-narrow + drop-anything-path-or-message-shaped. Never raises."""
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in _EVIDENCE_KEYS: continue
        if v is None or isinstance(v, bool): out[k] = v
        elif isinstance(v, int): out[k] = v
        elif isinstance(v, float): out[k] = round(v, 4)
        elif isinstance(v, str):
            s = v.strip()
            if not s or len(s) > _MAX_EVIDENCE_STR: continue           # a message, not a token
            if any(c in s for c in ("/", "\\", "\n", "\r", "\t")): continue   # never a path
            out[k] = s
    return out


@dataclass(frozen=True)
class FramingEvent:
    event: FramingEventType
    strategy: FramingStrategy | None               # None -> the DETECTION phase (the only unscoped context)
    evidence: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"event": self.event.value, "strategy": (self.strategy.value if self.strategy else None),
                "evidence": dict(self.evidence)}


@dataclass(frozen=True)
class StrategyAttempt:
    """One strategy's IMMUTABLE record. Execution is mutable (`framing._AttemptSpan`); evidence is not.
    They are different objects, and only a FINALIZED attempt ever enters a FramingResolution."""
    strategy: FramingStrategy
    applicable: bool                               # routing INCLUDED this strategy for this content type
    required_for_center: bool                      # terminal centering DEPENDS on this strategy completing
    started: bool
    completed: bool
    failure_event: FramingEventType | None
    negative_result: FramingEventType | None
    produced_focus: bool

    def __post_init__(self) -> None:               # ENFORCED, not documented
        if self.required_for_center and not self.applicable:
            raise ResolverInvariantError("required_for_center implies applicable")
        if self.completed and not self.started:
            raise ResolverInvariantError("completed implies started")
        if self.produced_focus and not self.completed:
            raise ResolverInvariantError("produced_focus implies completed")
        if self.failure_event is not None and self.completed:
            raise ResolverInvariantError("a failed attempt is never also complete")
        if self.failure_event is not None and self.failure_event not in HARD_FAILURE_EVENTS:
            raise ResolverInvariantError(f"failure_event must be a hard failure: {self.failure_event}")
        if self.negative_result is not None and self.negative_result not in NEGATIVE_RESULT_EVENTS:
            raise ResolverInvariantError(f"negative_result must be a conclusive negative: {self.negative_result}")

    @property
    def state(self) -> StrategyState:
        if not self.applicable: return StrategyState.NOT_APPLICABLE     # routing never included it
        if not self.started: return StrategyState.SKIPPED               # included; a prior strategy resolved
        if self.failure_event is not None: return StrategyState.FAILED  # OUTRANKS negative_result
        if self.completed and (self.produced_focus or self.negative_result is not None):
            return StrategyState.COMPLETED
        return StrategyState.FAILED                                     # unattributed -> NEVER benign

    def to_json(self) -> dict:
        return {"strategy": self.strategy.value, "state": self.state.value,
                "applicable": self.applicable, "required_for_center": self.required_for_center,
                "started": self.started, "completed": self.completed,
                "failure_event": (self.failure_event.value if self.failure_event else None),
                "negative_result": (self.negative_result.value if self.negative_result else None),
                "produced_focus": self.produced_focus}


class FramingTrace:
    """The mutable event recorder threaded through the lower layers as `_trace=`.

    Holds the SPAN STACK: `record()` attributes an event to the INNERMOST OPEN span. The detection
    phase (detect_window + classify_window) runs before any strategy and is the only UNSCOPED context
    — its events carry `strategy=None`. Strategy spans never nest with each other, but helper calls
    inside a strategy DO nest (notably subject_focus -> detect_window), and those events attribute to
    the ENCLOSING strategy, which is correct: a hard failure occurring WHILE subject_focus executes is
    subject_focus's failure."""
    __slots__ = ("_events", "_stack")

    def __init__(self) -> None:
        self._events: list[FramingEvent] = []
        self._stack: list[FramingStrategy] = []

    @property
    def events(self) -> tuple[FramingEvent, ...]:
        return tuple(self._events)

    def record(self, event: FramingEventType, **evidence) -> None:
        strat = self._stack[-1] if self._stack else None
        self._events.append(FramingEvent(event=event, strategy=strat, evidence=redact_evidence(evidence)))

    def open_span(self, strategy: FramingStrategy) -> None:
        self._stack.append(strategy)

    def close_span(self, strategy: FramingStrategy) -> None:
        if not self._stack or self._stack[-1] is not strategy:
            raise ResolverInvariantError(f"span stack corrupt: closing {strategy} over {self._stack}")
        self._stack.pop()

    def events_for(self, strategy: FramingStrategy) -> list[FramingEvent]:
        return [e for e in self._events if e.strategy is strategy]

    def detection_events(self) -> list[FramingEvent]:
        return [e for e in self._events if e.strategy is None]

    def detection_hard_failure(self) -> FramingEventType | None:
        """The FIRST hard failure recorded in the UNSCOPED detection phase, or None.

        This is the hard trust gate: `ct` itself is untrustworthy after one of these, because a failed
        detection MANUFACTURES CT_NOPEOPLE (framing.classify_window). A resolution it touched can never
        be a trusted resolution nor a legitimate centre, whatever a downstream strategy then produces."""
        for e in self._events:
            if e.strategy is None and e.event in HARD_FAILURE_EVENTS:
                return e.event
        return None


def record(trace: FramingTrace | None, event: FramingEventType, **evidence) -> None:
    """Append `event` to `trace` when one is threaded; a NO-OP when it is None.

    This one-liner is what makes every instrumented lower layer byte-identical in production: the
    production call path passes `_trace=None`, so every `record(...)` compiles down to one `is None`
    test and returns. No allocation, no branch on the hot path, no behaviour change."""
    if trace is not None:
        trace.record(event, **evidence)
