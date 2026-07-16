# tests/test_reframe_fallback_primitive.py — S1: the shared subject-aware fallback primitive
# (Track A, ADR-0103, framing spec F5). framing.subject_aware_fallback is a PURE reducer over the cached
# detect stats — it renders nothing and is wired into no route yet (S2-S5 consume it per defect population),
# so these fixtures drive the function DIRECTLY with synthetic per-frame face tuples. That deliberately
# avoids real detection: the primitive is a reducer, not a detector, so there is nothing for cv2 to do and
# the "headless YuNet can't detect drawn faces" fixture trap never applies. Each face tuple is the
# detect_window shape (cx, cy, fh, ey, score, fw), all normalized to [0,1].
#
# THE ONE S1 INVARIANT under test: given detected face-position evidence, the fallback returns an EXPLICIT
# structured composition whose retained horizontal span is a function of the detected subject positions — it
# anchors a single stable dominant subject or spans two persistent wide-separated subjects, excludes
# sub-threshold PIP tiles, and resolves to an explicit INSUFFICIENT (never a silent blind centre) when the
# evidence is below the confidence floor.
import pytest
from fanops.framing import (subject_aware_fallback, FallbackComposition,
                            FB_DOMINANT, FB_WIDE_PAIR, FB_PIP, FB_INSUFFICIENT)

# the blind 16:9 -> 9:16 centre crop retains only x in [0.342, 0.658] — width 0.316. A subject-aware
# composition that "preserves wider framing" must demand a span the blind centre could not have produced.
_BLIND_CENTRE_CX = 0.5
_BLIND_CENTRE_WIDTH = 0.316


def _face(cx, *, cy=0.40, fh=0.28, ey=None, score=0.95, fw=0.16):
    """One detected face in the detect_window tuple shape (cx, cy, fh, ey, score, fw)."""
    return (round(cx, 4), round(cy, 4), round(fh, 4), round(cy if ey is None else ey, 4), score, round(fw, 4))

def _stats(frames):
    return {"fps": 4.0, "frames": frames}


# ---- 1. stable dominant subject: anchor + span follow the off-centre subject, not the blind centre -------
def test_stable_dominant_subject_anchors_offcentre():
    comp = subject_aware_fallback(_stats([[_face(0.62)] for _ in range(10)]))
    assert comp.kind == FB_DOMINANT
    assert comp.is_actionable
    assert comp.cx == pytest.approx(0.62)                     # anchored ON the subject
    assert comp.x_min == pytest.approx(0.54) and comp.x_max == pytest.approx(0.70)   # span = the subject's box
    assert comp.confidence == pytest.approx(1.0)

def test_offcentre_subject_is_not_the_blind_centre():
    # the property the OLD blind-centre fallback VIOLATED: an off-centre subject must not resolve to 0.5.
    comp = subject_aware_fallback(_stats([[_face(0.72)] for _ in range(8)]))
    assert comp.cx != _BLIND_CENTRE_CX
    assert not (comp.x_min <= 0.342 and comp.x_max >= 0.658)  # the retained span is NOT the blind centre window


# ---- 2. intermittent secondary participant: frame the persistent dominant, not a pair (D1-B shape) -------
def test_intermittent_secondary_frames_dominant_not_pair():
    frames = [[_face(0.35, fh=0.30, fw=0.17)] for _ in range(9)]
    frames.append([_face(0.35, fh=0.30, fw=0.17), _face(0.75, fh=0.10, fw=0.07, score=0.7)])  # 2nd host: 1 frame
    comp = subject_aware_fallback(_stats(frames))
    assert comp.kind == FB_DOMINANT                           # an intermittent 2nd face does NOT make a wide pair
    assert comp.cx == pytest.approx(0.35)                     # framed on the persistent dominant, not empty centre


# ---- 3. persistent wide two-shot: span covers BOTH, both retained (D1-A shape) ---------------------------
def test_persistent_wide_two_shot_retains_both():
    frames = [[_face(0.20, fh=0.24, fw=0.14, score=0.93),
               _face(0.80, fh=0.24, fw=0.14, score=0.93)] for _ in range(8)]
    comp = subject_aware_fallback(_stats(frames))
    assert comp.kind == FB_WIDE_PAIR
    assert comp.x_min < 0.20 and comp.x_max > 0.80           # span BRACKETS both subjects (neither dropped)
    assert comp.cx == pytest.approx(0.50)                    # anchor is the pair midpoint
    assert (comp.x_max - comp.x_min) > _BLIND_CENTRE_WIDTH   # a wider crop than the blind centre -> both fit


# ---- 4. PIP tile grid: anchor the presenter, do NOT treat the tile column as a co-speaker (D2 shape) -----
def test_pip_tile_grid_anchors_presenter_not_a_pair():
    # REAL PIP tiles (raw-detections.json) are NOT sub-threshold — they CLEAR the phantom gate, so _two_cluster
    # fires. What keeps a PIP grid from being mistaken for a wide two-shot is the median face-count (4, not 2):
    # the S2 discriminator. A too-easy below-gate fixture would have masked exactly that.
    #
    # S4 gave the layout its OWN kind (FB_PIP) and anchors it by SIZE. This fixture's presenter out-scores the
    # tiles (0.95 vs 0.90), which the real evidence does NOT — there the tiles win the score max in 36/36. The
    # adversarial version (tiles out-scoring the presenter) lives in tests/test_reframe_s4_d2.py; this one stays
    # as the primitive's own contract check.
    tiles = [_face(0.85, cy=0.20, fh=0.24, fw=0.14, score=0.90),
             _face(0.85, cy=0.50, fh=0.24, fw=0.14, score=0.90),
             _face(0.85, cy=0.80, fh=0.24, fw=0.14, score=0.90)]
    frames = [[_face(0.30, cy=0.50, fh=0.42, fw=0.24, score=0.95)] + tiles for _ in range(8)]
    comp = subject_aware_fallback(_stats(frames))
    assert comp.kind == FB_PIP                               # a 4-face grid is its own layout — never a wide pair
    assert comp.kind != FB_WIDE_PAIR
    assert comp.cx == pytest.approx(0.30)                    # anchored on the presenter, not a remote tile
    assert comp.x_max < 0.50                                 # span is the presenter's box, NOT stretched to the tiles


# ---- 5. ambiguous evidence: explicit INSUFFICIENT, fail-safe ----------------------------------------------
def test_ambiguous_evidence_is_explicit_insufficient():
    frames = [[] for _ in range(7)] + [[_face(0.5)]]         # a face in 1 of 8 frames -> conf 0.125 < floor
    comp = subject_aware_fallback(_stats(frames))
    assert comp.kind == FB_INSUFFICIENT
    assert not comp.is_actionable
    assert comp.x_min is None and comp.cx is None            # no geometry -> caller keeps its safe default
    assert comp.confidence == pytest.approx(0.125)


# ---- 6. no detections: INSUFFICIENT for empty frames, {} and None ---------------------------------------
def test_no_detections_is_insufficient():
    for stats in (_stats([[] for _ in range(6)]), {}, None, {"frames": []}):
        comp = subject_aware_fallback(stats)
        assert comp.kind == FB_INSUFFICIENT
        assert comp.confidence == pytest.approx(0.0)
        assert comp.x_min is None and comp.x_max is None and comp.fh is None


# ---- the F5 core: the region is a FUNCTION of the positions (moves when they move) -----------------------
def test_region_moves_with_subject():
    left = subject_aware_fallback(_stats([[_face(0.30)] for _ in range(6)]))
    right = subject_aware_fallback(_stats([[_face(0.70)] for _ in range(6)]))
    assert left.cx < _BLIND_CENTRE_CX < right.cx            # neither is the fixed centre
    assert left.x_max < right.x_min                          # disjoint, ordered regions — a fixed centre cannot do this


# ---- purity: same stats -> byte-identical composition (deterministic, no cv2/no I/O) ---------------------
def test_deterministic_pure():
    frames = [[_face(0.20, fw=0.14), _face(0.80, fw=0.14)] for _ in range(8)]
    a = subject_aware_fallback(_stats(frames))
    b = subject_aware_fallback(_stats(frames))
    assert a == b
    assert isinstance(a, FallbackComposition)


def test_is_actionable_matches_kind():
    dom = subject_aware_fallback(_stats([[_face(0.6)] for _ in range(6)]))
    pair = subject_aware_fallback(_stats([[_face(0.2, fw=0.14), _face(0.8, fw=0.14)] for _ in range(8)]))
    none = subject_aware_fallback(None)
    assert dom.is_actionable and pair.is_actionable and not none.is_actionable
