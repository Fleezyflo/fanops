# tests/test_reframe_s4_d2.py — S4 / Track A: D2 layout-aware routing.
# A presenter-dominant PIP grid (one large presenter + a column of small, inert remote tiles) is a UI layout,
# not a live two-shot. It must not enter the active-speaker path at all: framing._resolve now recognises the
# layout BEFORE speaker_track and routes it to CENTERED_PIP_LAYOUT / _FS.PIP_LAYOUT.
#
# THE ONE S4 INVARIANT (spec F4, AC-D1): a presenter-dominant PIP layout does not enter the
# active-speaker/two-shot path. "Layout is a geometry signal: one large face + a column of small faces — no
# audio." (framing-spec.md AC-D1, verbatim.)
#
# THE FINDING THIS SLICE RESTS ON — and it CORRECTS the RCDR. RCDR:85-86/:148 says a "lock the largest face"
# alternative "mislocks onto a remote tile ... whenever the presenter's face is small — the tile out-scores the
# distant presenter". Measured against the permanent evidence (raw-detections.json, all 36 D2):
#   * the presenter is the LARGEST face in 36/36, by 1.60-2.07x (L_fh 0.396-0.491 vs R_fh 0.212-0.267);
#   * the presenter is NEVER small — 0/36 fall under _SMALL_FACE_FRAC, so the RCDR's precondition never fires;
#   * the mislock is REAL but SCORE-caused: dom_cx is on the tile side in 36/36 and dom_fh is smaller than even
#     the tile median in 36/36 — _pick_dominant_face ranks by YuNet score (height is only a tie-break), and the
#     3-tile column draws the score maximum against the presenter's single draw. Max-of-three beats one.
#   * score cannot separate them: the presenter out-scores the tiles in only 25/36, margins -0.010..+0.064.
# So the thing to ban is _pick_dominant_face, NOT size. Size is what the spec mandates and what works 36/36.
#
# NOT in this slice: composition (S5). focus/track/content_type stay None, so the render and fingerprint are
# byte-identical and NOTHING re-renders. AC-D4 (tile retention) stays P1-gated -> Track B.
#
# Fixtures are matched to the evidence distributions and are deliberately ADVERSARIAL: the tiles OUT-SCORE the
# presenter, exactly as the real data does, so a score-based anchor would fail them.
import pytest
from fanops.config import Config
from fanops import framing, clip
from fanops.framing_outcomes import (FramingOutcome as _FO, FramingStrategy as _FS,
                                     FramingEventType as _FE, StrategyState)


class _Src:
    id = "src_t"; source_path = "/none/x.mp4"; width = 1920; height = 1080
    duration = 60.0; transcript = []; language = "en"; meta = {}; sha256 = "d"; signal_peaks = []


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    return Config(root=tmp_path)


def _stub(monkeypatch, **spec):
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", lambda c: object())
    def mk(s):
        def fn(*a, _trace=None, **kw):
            events, value = s if (isinstance(s, tuple) and len(s) == 2 and isinstance(s[0], list)) else ([], s)
            for e in events:
                if _trace is not None: _trace.record(e)
            return value
        return fn
    for name, s in spec.items():
        monkeypatch.setattr(framing, name, mk(s))


def _face(cx, *, cy=0.45, fh=0.276, fw=0.166, score=0.93):
    return [round(cx, 4), round(cy, 4), round(fh, 4), round(cy, 4), score, round(fw, 4)]

def _stats(frames): return {"fps": 4.0, "frames": frames}

# D2 from raw-detections.json: presenter L_cx~0.358 L_fh~0.404 L_sc~0.9303; tiles R_cx~0.76 R_fh~0.242
# R_sc~0.9167, median face-count 4. ADVERSARIAL BY DESIGN: every tile out-scores the presenter and the
# SMALLEST tile scores highest — so _pick_dominant_face picks a tile, as it does on all 36 real clips.
_PRESENTER_CX = 0.358
_TILES = [_face(0.76, cy=0.18, fh=0.242, fw=0.145, score=0.95),
          _face(0.78, cy=0.50, fh=0.242, fw=0.145, score=0.96),
          _face(0.77, cy=0.82, fh=0.170, fw=0.102, score=0.97)]
_D2 = _stats([[_face(_PRESENTER_CX, cy=0.52, fh=0.404, fw=0.235, score=0.9303)] + _TILES for _ in range(86)])
_D1A = _stats([[_face(0.25, fh=0.19, fw=0.13), _face(0.75, fh=0.19, fw=0.13)] for _ in range(44)])
_D1B = _stats([[_face(0.352)] for _ in range(38)] +
              [[_face(0.352), _face(0.74, fh=0.20, fw=0.12, score=0.88)] for _ in range(22)])
# a grid with NO presenter — four real co-speakers at comparable size. NOT presenter+tiles.
_PANEL = _stats([[_face(0.2, fh=0.26, fw=0.15), _face(0.4, fh=0.25, fw=0.15),
                  _face(0.6, fh=0.26, fw=0.15), _face(0.8, fh=0.25, fw=0.15)] for _ in range(40)])


def _resolve(monkeypatch, cfg, stats, *, spy=None):
    track_fn = spy if spy is not None else (lambda *a, _trace=None, **k: None)
    _stub(monkeypatch, detect_window=stats, classify_window=framing.CT_MULTI,
          subject_focus=([_FE.NO_FACE], None))
    monkeypatch.setattr(framing, "speaker_track", track_fn)
    return framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)


class _Spy:
    """Records whether speaker_track was entered. AC-D1 is about ENTRY, so a spy is the only honest probe."""
    def __init__(self): self.calls = 0
    def __call__(self, *a, _trace=None, **k):
        self.calls += 1
        if _trace is not None: _trace.record(_FE.NO_TRACK)
        return None


# ---- the premise: the score-based picker really does mislock ----------------------------------------

def test_pick_dominant_face_lands_on_a_tile_this_is_why_size_is_used():
    """Pins WHY _pick_dominant_face is banned here. It ranks by score (height only breaks ties), so on a PIP
    grid the 3-tile column takes the score max against the presenter's single draw. Reproduces the evidence:
    dom on the tile side in 36/36, and the SMALLEST face in 36/36."""
    dom = framing._pick_dominant_face(_D2["frames"][0])
    assert dom[0] > 0.6                                        # tile side, not the presenter at 0.358
    assert dom[2] == min(f[2] for f in _D2["frames"][0])       # and it is the SMALLEST face in frame


# ---- the invariant ---------------------------------------------------------------------------------

def test_pip_layout_never_enters_the_active_speaker_path(monkeypatch, cfg):
    """AC-D1, stated as ENTRY: speaker_track must not run at all. A conclusive no-track afterwards would reach
    the same centre for the wrong reason — that is not what F4 asks for."""
    spy = _Spy()
    r = _resolve(monkeypatch, cfg, _D2, spy=spy)
    assert spy.calls == 0                                      # THE invariant: never entered
    assert r.final_strategy is _FS.PIP_LAYOUT                  # ...and the layout owns the decision
    assert r.final_outcome is not _FO.DETECTED_MULTI           # never the active-speaker outcome
    assert r.final_outcome is not _FO.CENTERED_MULTI_UNTRACKED  # nor the "we tried and failed" centre

def test_pip_anchors_the_presenter_by_size_not_the_score_max(monkeypatch, cfg):
    comp = framing.subject_aware_fallback(_D2)
    assert comp.kind == framing.FB_PIP
    assert comp.cx == pytest.approx(_PRESENTER_CX, abs=0.02)   # the presenter, though every tile out-scores him
    assert comp.fh == pytest.approx(0.404, abs=0.02)           # his height, not a tile's
    assert comp.x_max < 0.5                                    # span is his own box, never stretched to the tiles
    assert comp.is_actionable

def test_pip_records_speaker_track_as_skipped_not_failed(monkeypatch, cfg):
    """The routing decision must be observable and truthful: SKIPPED means "routing included it, but a prior
    decision resolved first" — which is exactly what happened. FAILED would fabricate a toolchain problem."""
    r = _resolve(monkeypatch, cfg, _D2, spy=_Spy())
    assert [(a.strategy, a.state) for a in r.attempts] == [(_FS.SPEAKER_TRACK, StrategyState.SKIPPED)]
    assert r.root_cause is None                                # a decision, not a failure


# ---- both gates are load-bearing -------------------------------------------------------------------

def test_an_equal_size_panel_is_not_a_pip_grid(monkeypatch, cfg):
    """A grid of four comparable co-speakers is NOT presenter+tiles. Without the size gate it would be framed
    presenter-only on an arbitrary face. Ambiguity resolves to an explicit no-op, never a guess."""
    assert framing.subject_aware_fallback(_PANEL).kind != framing.FB_PIP
    spy = _Spy()
    r = _resolve(monkeypatch, cfg, _PANEL, spy=spy)
    assert r.final_outcome is not _FO.CENTERED_PIP_LAYOUT
    assert spy.calls == 1                                      # it DOES take the normal multi path

def test_the_face_count_gate_keeps_d1b_out(monkeypatch, cfg):
    """The size gate ALONE would capture 7/25 D1-B (their L/R ratio reaches 1.53). _PIP_MIN_FACES is what
    makes 'a grid' a precondition — it is load-bearing, not decoration."""
    assert framing._face_count(_D1B) < framing._PIP_MIN_FACES
    assert framing._face_count(_D1A) < framing._PIP_MIN_FACES
    assert framing._face_count(_D2) >= framing._PIP_MIN_FACES


# ---- the other two defect classes are untouched -----------------------------------------------------

def test_d1a_wide_two_shot_routing_is_unaffected(monkeypatch, cfg):
    spy = _Spy()
    r = _resolve(monkeypatch, cfg, _D1A, spy=spy)
    assert r.final_outcome is _FO.STACKED_PAIR
    assert spy.calls == 1                                      # still enters the active-speaker path, as before

def test_d1b_subject_lock_routing_is_unaffected(monkeypatch, cfg):
    spy = _Spy()
    r = _resolve(monkeypatch, cfg, _D1B, spy=spy)
    assert r.final_outcome is _FO.SUBJECT_LOCKED
    assert spy.calls == 1


# ---- blast radius: S4 re-renders NOTHING ------------------------------------------------------------

def test_pip_never_produces_a_track(monkeypatch, cfg):
    """S4 was routing only, so it asserted the whole 3-tuple was untouched. S5 then composed the presenter
    (focus + content_type), which is that slice's business — see tests/test_reframe_s5_d2.py. What S4 owns
    durably is the TRACK: a PIP layout must never yield an active-speaker track, whatever composes it."""
    r = _resolve(monkeypatch, cfg, _D2, spy=_Spy())
    assert r.track is None
    assert clip._REFRAME_GEOM_V == 5
