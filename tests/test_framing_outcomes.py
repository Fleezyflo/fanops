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
from fanops.errors import ToolchainMissingError
from fanops.framing_outcomes import (HARD_FAILURE_EVENTS, LEGITIMATE_CENTER_OUTCOMES, NEGATIVE_RESULT_EVENTS,
                                     POSITIVE_EVENTS, RESOLVED_OUTCOMES, UNRESOLVED_OUTCOMES, FramingEventType,
                                     FramingOutcome, FramingStrategy, FramingTrace, ResolverInvariantError,
                                     StrategyAttempt, StrategyState, redact_evidence)

_ROOT = Path(__file__).resolve().parents[1]
_FIX = _ROOT / "tests" / "fixtures"
_FE, _FO, _FS = FramingEventType, FramingOutcome, FramingStrategy

_FOCUS = (0.61, 0.44, 0.30, 0.38)
_SAL = (0.61, 0.44)


class _Src:
    id = "src_t"; source_path = "/none/x.mp4"; width = 1920; height = 1080
    duration = 60.0; transcript = []; language = "en"; meta = {}; sha256 = "d"; signal_peaks = []


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    return Config(root=tmp_path)


def _seed(cfg, *, detect, track=None, saliency=None, start=0.0, end=10.0):
    """Warm detect/track/saliency sidecars so _resolve never probes ffmpeg or patches framing.*."""
    key = f"{round(start, 2)}-{round(end, 2)}"
    root = cfg.agent_io / "framing"
    root.mkdir(parents=True, exist_ok=True)
    (root / "src_t.detect.json").write_text(json.dumps(
        {"v": framing._DETECT_V, "windows": {key: detect}}))
    (root / "src_t.track.json").write_text(json.dumps(
        {"v": framing._SIDECAR_V, "windows": {key: [] if track is None else track}}))
    (root / "src_t.saliency.json").write_text(json.dumps(
        {"v": framing._SIDECAR_V, "windows": {key: [] if saliency is None else saliency}}))


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


# ---------------------------------------------------------------------------- routing semantics (write-path sidecars; no fanops.* setattr)

def test_unattributed_none_is_unknown_never_benign(cfg):
    # A cached detect miss with no event is UNRESOLVED/UNKNOWN, never a legitimate centre.
    _seed(cfg, detect=None)
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.UNKNOWN
    assert r.final_outcome not in LEGITIMATE_CENTER_OUTCOMES


def test_empty_room_with_no_motion_is_the_only_legitimate_centre(cfg):
    empty = {"fps": 4.0, "frames": [[], [], [], []]}
    _seed(cfg, detect=empty, saliency=[])
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.CENTERED_NO_SUBJECT and r.root_cause is None
    assert r.as_tuple() == (None, None, None)
    assert r.final_outcome in LEGITIMATE_CENTER_OUTCOMES


def test_saliency_success_keeps_content_type_None(cfg):
    """C-2 / D9. A 2-tuple focus carries no face height, so nothing zooms.

    THIS assertion is the guard, NOT the fingerprint golden: _render_fingerprint gates `ct` behind `geom`,
    and geom is False for a 2-tuple — so returning a ct here would change the fingerprint of exactly
    NOTHING and would slip through a fingerprint test unnoticed."""
    empty = {"fps": 4.0, "frames": [[], [], [], []]}
    _seed(cfg, detect=empty, saliency=list(_SAL))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.MOTION_FOCUS
    assert r.as_tuple() == (_SAL, None, None)               # <- the third element MUST be None
    assert r.classified_content_type == framing.CT_NOPEOPLE


def test_skipped_required_strategy_cannot_license_a_centre():
    """Unreachable via the real resolver (§6). Pins the rule's defensiveness over a synthetic attempt."""
    skipped = StrategyAttempt(strategy=_FS.MOTION_SALIENCY, applicable=True, required_for_center=True,
                              started=False, completed=False, failure_event=None, negative_result=None,
                              produced_focus=False)
    assert skipped.state is StrategyState.SKIPPED
    assert skipped.state is not StrategyState.COMPLETED      # so §6's `is not COMPLETED` -> UNRESOLVED


# ---------------------------------------------------------------------------- C-1: exception compatibility

def test_C1_capture_failures_defaults_to_False():
    """A flipped default would silently convert production fail-loud into fail-open."""
    import inspect
    assert inspect.signature(framing._resolve).parameters["capture_failures"].default is False


def test_C1_preflight_is_fatal_in_both_modes(cfg, monkeypatch):
    import cv2
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: None))
    for cap in (False, True):
        framing._reset_yunet_cache()
        with pytest.raises(ToolchainMissingError):
            framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=cap)
    framing._reset_yunet_cache()
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, _Src(), 0.0, 10.0)


def test_C1_render_account_cut_handlers_are_untouched(cfg, monkeypatch, tmp_path):
    """ToolchainMissingError still RE-RAISES from a real render_account_cut call (no source scan)."""
    import cv2
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, MomentState, Fmt
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: None))
    framing._reset_yunet_cache()
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=60.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=0, end=6, reason="r", state=MomentState.clipped))
    with pytest.raises(ToolchainMissingError):
        clipmod.render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                                   hook="", out_path=str(cfg.clips / "acct.mp4"))


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
