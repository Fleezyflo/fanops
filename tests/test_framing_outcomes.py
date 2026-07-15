# tests/test_framing_outcomes.py — the framing-outcome CONTRACT + the legacy-equivalence proof.
#
# Two evidence layers, never merged:
#   Layer 1 (framing_routing_vectors.json) — OBSERVED legacy behaviour. The new routing must reproduce
#           the legacy tuple, call sequence, call arguments and escaped exceptions EXACTLY.
#   Layer 2 (framing_contract_expectations.json) — AUTHORED against the spec. The new diagnostics.
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from fanops import clip as clipmod
from fanops import framing
from fanops.config import Config
from fanops.errors import StageBusyError, ToolchainMissingError
from fanops.framing_outcomes import (HARD_FAILURE_EVENTS, LEGITIMATE_CENTER_OUTCOMES, NEGATIVE_RESULT_EVENTS,
                                     POSITIVE_EVENTS, RESOLVED_OUTCOMES, UNRESOLVED_OUTCOMES, FramingEventType,
                                     FramingOutcome, FramingStrategy, FramingTrace, ResolverInvariantError,
                                     StrategyAttempt, StrategyState, redact_evidence)

_ROOT = Path(__file__).resolve().parents[1]
_FIX = _ROOT / "tests" / "fixtures"
_FE, _FO, _FS = FramingEventType, FramingOutcome, FramingStrategy

CT_MULTI, CT_SINGLE = framing.CT_MULTI, framing.CT_SINGLE
CT_MUSIC, CT_SILENT, CT_NOPEOPLE = framing.CT_MUSIC, framing.CT_SILENT, framing.CT_NOPEOPLE

_STATS = {"fps": 4.0, "frames": [[[0.5, 0.5, 0.3, 0.42, 0.9]]]}
_FOCUS = (0.61, 0.44, 0.30, 0.38)
_SAL = (0.61, 0.44)
_TRACK = [(0.0, 5.0, 0.3, 0.5, 0.28, 0.4), (5.0, 10.0, 0.7, 0.5, 0.28, 0.4)]


class _Src:
    id = "src_t"; source_path = "/none/x.mp4"; width = 1920; height = 1080
    duration = 60.0; transcript = []; language = "en"; meta = {}; sha256 = "d"; signal_peaks = []


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    return Config(root=tmp_path)


def _stub(monkeypatch, **spec):
    """Stub the framing seams. A value may be a plain return, an exception to raise, or a
    (events, value) pair so a strategy can RECORD then RETURN NORMALLY — which is what the real
    fail-open strategies do, and the whole reason completion cannot be inferred from a return."""
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", lambda c: object())

    def mk(name, s):
        def fn(*a, _trace=None, **kw):
            if isinstance(s, BaseException):
                raise s
            events, value = s if isinstance(s, tuple) and len(s) == 2 and isinstance(s[0], list) else ([], s)
            for e in events:
                if _trace is not None:
                    _trace.record(e)
            return value
        return fn
    for name, s in spec.items():
        monkeypatch.setattr(framing, name, mk(name, s))


# ---------------------------------------------------------------------------- contract + lifecycle

def test_enum_partitions_are_exhaustive_and_disjoint():
    assert HARD_FAILURE_EVENTS | NEGATIVE_RESULT_EVENTS | POSITIVE_EVENTS == set(FramingEventType)
    assert not HARD_FAILURE_EVENTS & NEGATIVE_RESULT_EVENTS
    assert RESOLVED_OUTCOMES | LEGITIMATE_CENTER_OUTCOMES | UNRESOLVED_OUTCOMES == set(FramingOutcome)
    assert not RESOLVED_OUTCOMES & LEGITIMATE_CENTER_OUTCOMES


def test_detection_raised_exists_and_is_a_hard_failure():
    """C-1: a detection-phase exception has its OWN event. It is never STRATEGY_RAISED."""
    assert _FE.DETECTION_RAISED in HARD_FAILURE_EVENTS
    assert _FE.DETECTION_RAISED is not _FE.STRATEGY_RAISED


@pytest.mark.parametrize("kw, msg", [
    (dict(applicable=False, required_for_center=True, started=False, completed=False,
          failure_event=None, negative_result=None, produced_focus=False), "required_for_center implies applicable"),
    (dict(applicable=True, required_for_center=True, started=False, completed=True,
          failure_event=None, negative_result=None, produced_focus=False), "completed implies started"),
    (dict(applicable=True, required_for_center=True, started=True, completed=False,
          failure_event=None, negative_result=None, produced_focus=True), "produced_focus implies completed"),
    (dict(applicable=True, required_for_center=True, started=True, completed=True,
          failure_event=_FE.NO_FRAMES, negative_result=None, produced_focus=False), "never also complete"),
])
def test_invalid_attempts_are_rejected_at_construction(kw, msg):
    with pytest.raises(ResolverInvariantError, match=msg):
        StrategyAttempt(strategy=_FS.SUBJECT_FOCUS, **kw)


def test_not_applicable_and_skipped_are_distinct():
    """Without `applicable`, 'a required strategy that was SKIPPED cannot license a centre' is unprovable."""
    common = dict(strategy=_FS.MOTION_SALIENCY, completed=False, failure_event=None,
                  negative_result=None, produced_focus=False)
    assert StrategyAttempt(applicable=False, required_for_center=False, started=False,
                           **common).state is StrategyState.NOT_APPLICABLE
    assert StrategyAttempt(applicable=True, required_for_center=True, started=False,
                           **common).state is StrategyState.SKIPPED


def test_unattributed_attempt_is_failed_never_benign():
    a = StrategyAttempt(strategy=_FS.SUBJECT_FOCUS, applicable=True, required_for_center=True, started=True,
                        completed=False, failure_event=_FE.UNKNOWN, negative_result=None, produced_focus=False)
    assert a.state is StrategyState.FAILED


def test_span_finalize_is_atomic_and_not_rerunnable():
    tr = FramingTrace()
    span = framing._AttemptSpan(tr, _FS.SUBJECT_FOCUS, applicable=True, required_for_center=True)
    with span:
        span.set_result(_FOCUS)
    a = span.finalize()
    assert a.produced_focus and a.completed and a.state is StrategyState.COMPLETED
    with pytest.raises(ResolverInvariantError):
        span.finalize()
    with pytest.raises(ResolverInvariantError):
        span.set_result(None)


def test_hard_failure_outranks_a_negative_recorded_in_the_same_call():
    """The strategies fail OPEN: they return None NORMALLY after a hard failure. Completion is decided by
    the EVIDENCE, never by the fact of returning."""
    tr = FramingTrace()
    span = framing._AttemptSpan(tr, _FS.SUBJECT_FOCUS, applicable=True, required_for_center=True)
    with span:
        tr.record(_FE.DETECTOR_RUNTIME_FAILED)
        tr.record(_FE.NO_FACE)                                  # a trailing negative must NOT launder it
        span.set_result(None)
    a = span.finalize()
    assert a.state is StrategyState.FAILED and a.failure_event is _FE.DETECTOR_RUNTIME_FAILED
    assert a.completed is False and a.negative_result is None


def test_events_attribute_to_the_innermost_open_span():
    tr = FramingTrace()
    tr.record(_FE.FACES_DETECTED)                               # detection phase: unscoped
    span = framing._AttemptSpan(tr, _FS.SUBJECT_FOCUS, applicable=True, required_for_center=True)
    with span:
        tr.record(_FE.NO_FACE)
    assert [(e.event, e.strategy) for e in tr.events] == [
        (_FE.FACES_DETECTED, None), (_FE.NO_FACE, _FS.SUBJECT_FOCUS)]
    assert tr.detection_hard_failure() is None


def test_evidence_is_allowlisted_and_carries_no_message_or_path():
    ev = redact_evidence({"exc_type": "OSError", "rc": 1, "frames": 4, "conf": 0.512345,
                          "message": "boom", "path": "/Users/x/secret.mp4", "stderr": "trace"})
    assert ev == {"exc_type": "OSError", "rc": 1, "frames": 4, "conf": 0.5123}
    assert redact_evidence({"exc_type": "/etc/passwd"}) == {}   # a path-shaped value is DROPPED, not escaped


# ---------------------------------------------------------------------------- routing semantics

def test_ct_single_no_face_is_the_only_legitimate_centre(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_SINGLE,
          subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.CENTERED_NO_SUBJECT and r.root_cause is None
    assert r.as_tuple() == (None, None, None)
    assert [a.state for a in r.attempts] == [StrategyState.COMPLETED]


def test_missing_ffmpeg_is_never_a_legitimate_centre(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=([_FE.FFMPEG_UNAVAILABLE], None), classify_window=CT_NOPEOPLE,
          motion_saliency=None)
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.FFMPEG_UNAVAILABLE
    assert r.final_outcome not in LEGITIMATE_CENTER_OUTCOMES


def test_empty_glob_is_no_frames_not_ffmpeg_unavailable(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=([_FE.NO_FRAMES], None), classify_window=CT_NOPEOPLE, motion_saliency=None)
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.root_cause is _FE.NO_FRAMES


def test_unattributed_none_is_unknown_never_benign(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=None, classify_window=CT_SINGLE, subject_focus=None)
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.UNKNOWN


def test_ct_multi_track_FAILED_plus_no_face_is_unresolved_not_centred(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_MULTI,
          speaker_track=([_FE.STRATEGY_RAISED], None), subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.STRATEGY_RAISED


def test_ct_multi_track_COMPLETED_no_track_is_conservative_centre(cfg, monkeypatch):
    """E3: a real 2-shot with no clean track is CONSERVATIVELY CENTRED (both seats), never a one-person
    static lock that would crop the other speaker out. subject_focus is not part of the MULTI route, so
    exactly ONE attempt runs (speaker_track, COMPLETED with a conclusive no_track). Still a LEGITIMATE
    centre — a completed negative, not a failure."""
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.CENTERED_MULTI_UNTRACKED and r.root_cause is None
    assert r.final_outcome in LEGITIMATE_CENTER_OUTCOMES
    assert r.as_tuple() == (None, None, None)
    assert [a.state for a in r.attempts] == [StrategyState.COMPLETED]     # ONLY speaker_track — no subject_focus


def test_ct_multi_track_hard_fail_is_unresolved_never_degraded_single(cfg, monkeypatch):
    """E3: a MULTI whose track HARD-FAILS is UNRESOLVED — it no longer falls to a degraded one-person lock
    (the pilot's 'empty seat'). subject_focus is not run; the failed required track pins the outcome, and
    the centre we return is NOT a defensible one (a broken toolchain never licenses a centre)."""
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_MULTI,
          speaker_track=([_FE.STRATEGY_RAISED], None), subject_focus=([_FE.FOCUS_PLACED], _FOCUS))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.STRATEGY_RAISED
    assert r.as_tuple() == (None, None, None)                 # NOT the _FOCUS single lock the old routing produced
    assert r.degraded_strategies == (_FS.SPEAKER_TRACK,)


def test_no_people_alone_never_authorizes_a_centre(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_NOPEOPLE,
          motion_saliency=([_FE.DETECTOR_RUNTIME_FAILED], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.DETECTOR_RUNTIME_FAILED
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_NOPEOPLE,
          motion_saliency=([_FE.NO_MOTION], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.CENTERED_NO_SUBJECT       # saliency ALSO had to complete


def test_saliency_success_keeps_content_type_None(cfg, monkeypatch):
    """C-2 / D9. A 2-tuple focus carries no face height, so nothing zooms.

    THIS assertion is the guard, NOT the fingerprint golden: _render_fingerprint gates `ct` behind `geom`,
    and geom is False for a 2-tuple — so returning a ct here would change the fingerprint of exactly
    NOTHING and would slip through a fingerprint test unnoticed."""
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_MUSIC,
          subject_focus=([_FE.NO_FACE], None), motion_saliency=([_FE.MOTION_PLACED], _SAL))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.MOTION_FOCUS
    assert r.as_tuple() == (_SAL, None, None)               # <- the third element MUST be None
    assert r.classified_content_type == CT_MUSIC             # the classification is still reported, separately


def test_skipped_required_strategy_cannot_license_a_centre():
    """Unreachable via the real resolver (§6). Pins the rule's defensiveness over a synthetic attempt."""
    skipped = StrategyAttempt(strategy=_FS.MOTION_SALIENCY, applicable=True, required_for_center=True,
                              started=False, completed=False, failure_event=None, negative_result=None,
                              produced_focus=False)
    assert skipped.state is StrategyState.SKIPPED
    assert skipped.state is not StrategyState.COMPLETED      # so §6's `is not COMPLETED` -> UNRESOLVED


# ---------------------------------------------------------------------------- C-1: exception compatibility

@pytest.mark.parametrize("exc,expect_root", [
    (StageBusyError("busy"), _FE.STAGE_LOCK_BUSY),
    (OSError(28, "No space left on device"), _FE.DETECTION_RAISED),
])
def test_C1_detection_phase_exception(cfg, monkeypatch, exc, expect_root):
    """PRODUCTION propagates it byte-for-byte. The DRY-RUN converts it to a per-clip UNRESOLVED, with NO
    fabricated strategy attribution — no strategy had even started."""
    _stub(monkeypatch, detect_window=exc, classify_window=CT_SINGLE, subject_focus=None, motion_saliency=None)

    with pytest.raises(type(exc)):                            # capture_failures defaults to FALSE
        framing._resolve(cfg, _Src(), 0.0, 10.0)
    with pytest.raises(type(exc)):                            # and it escapes the production entry point
        clipmod._resolve_framing(cfg, _Src(), 0.0, 10.0)

    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is expect_root
    assert r.attempts == ()                                   # no strategy ran -> none is invented
    assert all(e.strategy is None for e in r.events)          # attributed to the DETECTION phase
    assert _FE.STRATEGY_RAISED not in [e.event for e in r.events]


@pytest.mark.parametrize("exc,expect_root", [
    (StageBusyError("busy"), _FE.STAGE_LOCK_BUSY),
    (RuntimeError("boom"), _FE.STRATEGY_RAISED),
])
def test_C1_strategy_exception(cfg, monkeypatch, exc, expect_root):
    _stub(monkeypatch, detect_window=_STATS, classify_window=CT_SINGLE, subject_focus=exc)
    with pytest.raises(type(exc)):
        framing._resolve(cfg, _Src(), 0.0, 10.0)
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is expect_root
    ev = [e for e in r.events if e.event is expect_root]
    assert ev and ev[0].strategy is _FS.SUBJECT_FOCUS         # attributed to the RIGHT span
    assert [a.state for a in r.attempts] == [StrategyState.FAILED]


def test_C1_capture_failures_defaults_to_False():
    """A flipped default would silently convert production fail-loud into fail-open."""
    import inspect
    assert inspect.signature(framing._resolve).parameters["capture_failures"].default is False


def test_C1_preflight_is_fatal_in_both_modes(cfg, monkeypatch):
    def boom(_c):
        raise ToolchainMissingError("no cv2")
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", boom)
    for cap in (False, True):
        with pytest.raises(ToolchainMissingError):
            framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=cap)
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, _Src(), 0.0, 10.0)


def test_C1_render_account_cut_handlers_are_untouched(cfg, monkeypatch, tmp_path):
    """ToolchainMissingError still RE-RAISES; every other exception still fails open."""
    src = Path(clipmod.__file__).read_text()
    body = src[src.index("def render_account_cut"):]
    assert "except ToolchainMissingError:" in body and "raise" in body
    assert "except Exception:" in body and "return False, None" in body


# ---------------------------------------------------------------------------- Layer 1: legacy equivalence

def _layer1():
    return json.loads((_FIX / "framing_routing_vectors.json").read_text())


def test_layer1_fixture_checksum_and_provenance():
    sys.path.insert(0, str(_ROOT / "scripts"))
    from gen_framing_vectors import fixture_checksum
    doc = _layer1()
    assert fixture_checksum(doc) == doc["fixture_checksum"], "Layer-1 fixture has been edited by hand"
    for k in ("legacy_source_commit_sha", "generator_commit_sha", "generator_file_sha256",
              "legacy_function_qualname", "python_version", "vector_schema_version"):
        assert doc[k], f"missing provenance: {k}"


def test_layer1_carries_none_of_the_new_semantic_fields():
    """The legacy resolver CANNOT emit these. Recording them under a legacy SHA would present authored
    expectations as observed history — the design certifying itself."""
    blob = (_FIX / "framing_routing_vectors.json").read_text()
    for forbidden in ("applicable", "required_for_center", "failure_event", "negative_result",
                      "degraded_strategies", "produced_focus", "final_outcome", "root_cause"):
        assert f'"{forbidden}"' not in blob, f"Layer 1 must not contain {forbidden!r} — that is Layer 2"


def test_layer1_distinguishes_return_from_raise():
    kinds = {s["stubs"][k]["kind"] for s in _layer1()["scenarios"] for k in s["stubs"]}
    assert kinds == {"return", "raise"}
    results = {s["observed"]["result"]["kind"] for s in _layer1()["scenarios"]}
    assert results == {"return", "raise"}, "the fixture must record BOTH escapes and returns"


# E3 is the FIRST deliberate behaviour change since the legacy characterization: a CT_MULTI window with no
# clean active-speaker track now CENTRES (both seats) instead of falling to a one-person subject_focus lock.
# These two legacy scenarios therefore MUST diverge; every OTHER scenario is still reproduced exactly. The
# legacy fixture is left BYTE-IDENTICAL — its checksum + provenance still attest the old behaviour truthfully;
# we do not relabel history, we enumerate exactly where we departed from it.
_E3_DIVERGED = {"ct_multi_no_track_then_focus", "ct_multi_no_track_no_focus"}


@pytest.mark.parametrize("sid", [s["id"] for s in _layer1()["scenarios"] if s["id"] not in _E3_DIVERGED])
def test_layer1_new_routing_reproduces_legacy_exactly(sid, tmp_path, monkeypatch):
    """FOR THE COMMITTED CHARACTERIZATION SCENARIOS (except the E3-diverged two, pinned separately below) the
    new routing reproduces the legacy resolver's directly observed tuple, call sequence, call arguments and
    escaped-exception behaviour.

    Scenario-scoped by construction — this is not a claim of universal equivalence."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    from gen_framing_vectors import run_scenario
    monkeypatch.setenv("FANOPS_FIXTURE_ROOT", str(tmp_path))
    scenario = next(s for s in _layer1()["scenarios"] if s["id"] == sid)
    now = run_scenario(scenario, resolve=clipmod._resolve_framing, framing_mod=framing, cfg_cls=Config)
    assert now["result"] == scenario["observed"]["result"], "the returned 3-tuple / escaped exception changed"
    assert now["calls"] == scenario["observed"]["calls"], "the call sequence or its arguments changed"


@pytest.mark.parametrize("sid", sorted(_E3_DIVERGED))
def test_layer1_E3_intentionally_diverges_from_legacy(sid, tmp_path, monkeypatch):
    """The E3 carve-out, pinned explicitly. For each diverged legacy scenario: the legacy resolver CALLED
    subject_focus (and, for ..._then_focus, returned its one-person lock); the new resolver stops after
    speaker_track and returns the conservative centre (None, None, None) — no subject_focus call, no
    one-person crop. This documents the departure without touching the frozen legacy evidence."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    from gen_framing_vectors import run_scenario
    monkeypatch.setenv("FANOPS_FIXTURE_ROOT", str(tmp_path))
    scenario = next(s for s in _layer1()["scenarios"] if s["id"] == sid)
    legacy = scenario["observed"]
    now = run_scenario(scenario, resolve=clipmod._resolve_framing, framing_mod=framing, cfg_cls=Config)
    assert any(c["fn"] == "subject_focus" for c in legacy["calls"]), "the legacy behaviour we departed from"
    assert [c["fn"] for c in now["calls"]] == ["_framing_runtime_or_raise", "detect_window",
                                               "classify_window", "speaker_track"]   # no subject_focus under E3
    assert now["result"] == {"kind": "return", "value": [None, None, None]}          # conservative centre, both seats
    assert (now["result"] != legacy["result"]) or (now["calls"] != legacy["calls"]), "must be a real divergence"


def test_layer2_is_labelled_as_authored_not_observed():
    doc = json.loads((_FIX / "framing_contract_expectations.json").read_text())
    assert doc["layer"] == 2
    assert "NOT_attributed_to" in doc and "legacy" in doc["NOT_attributed_to"]
    assert doc["expectations"], "Layer 2 must carry the authored contract"


# ---------------------------------------------------------------------------- static dependency graph

def _imports_of(path: Path) -> set:
    """Every fanops module imported — INCLUDING lazy in-function imports, which a top-level scan misses
    (framing imports keyframes inside three functions)."""
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("fanops"):
            out.add(n.module)
            for a in n.names:
                out.add(f"{n.module}.{a.name}")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("fanops"):
                    out.add(a.name)
    return out


def test_framing_outcomes_is_dependency_neutral():
    """It must NEVER import framing, keyframes or clip — they import IT. That is what keeps the graph
    acyclic through the lazy in-function imports."""
    imp = _imports_of(_ROOT / "src" / "fanops" / "framing_outcomes.py")
    assert not [m for m in imp if m.startswith(("fanops.framing", "fanops.keyframes", "fanops.clip"))
                and m != "fanops.framing_outcomes"], f"framing_outcomes must be stdlib-only, got {imp}"


def test_keyframes_does_not_import_framing():
    imp = _imports_of(_ROOT / "src" / "fanops" / "keyframes.py")
    assert "fanops.framing" not in imp, "keyframes -> framing would close a cycle"
    assert any(m.startswith("fanops.framing_outcomes") for m in imp)
