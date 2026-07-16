# tests/test_reframe_s2_d1a.py — S2 / Track A: D1-A empty-gap correction.
# A genuine wide TWO-SHOT (both hosts persistent) that yields no active-speaker track no longer falls to the
# blind CENTRE crop — which lands on the empty table BETWEEN the hosts, zero faces on screen (the D1-A
# defect). framing._resolve now composes BOTH hosts into a subject-derived VERTICAL STACK
# (content_type=RENDER_STACK_PAIR, focus = the two host anchors), rendered by clip.ffmpeg_stack_cmd.
#
# THE FINDING THIS SLICE RESTS ON (docs/design/reframe/evidence/raw-detections.json): ALL 67 clips trip
# framing._two_cluster, so it alone does NOT isolate D1-A. The primitive is strengthened with the
# data-derived discriminators — median face-count == 2, both hosts co-present, comparable size — so the
# stack fires on EXACTLY the 6 D1-A clips; D1-B (dominant host + intermittent 2nd) and D2 (presenter + PIP
# tile column) get FB_DOMINANT and stay centred, owned by their own slices (S3, S4/S5).
#
# THE ONE S2 INVARIANT: a no-track window with two persistent, comparable, co-present subject clusters
# resolves to a composition that RETAINS BOTH (a vertical stack), never a region that contains no
# participant. (Spec F1/F5/F6; AC-A1/A2/A3.)
#
# Fixtures are stats matched to the permanent-evidence distributions, driven directly (no detection, so the
# headless-YuNet fixture trap does not apply). Each face tuple is the detect_window shape (cx,cy,fh,ey,score,fw).
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
    """Stub the framing seams (mirrors test_framing_outcomes._stub). A (events, value) pair lets a strategy
    RECORD then RETURN NORMALLY — which is how the real fail-open strategies conclude a negative."""
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


def _face(cx, *, cy=0.45, fh=0.19, fw=0.13, score=0.93):
    return [round(cx, 4), round(cy, 4), round(fh, 4), round(cy, 4), score, round(fw, 4)]

def _stats(frames): return {"fps": 4.0, "frames": frames}

# Distributions from raw-detections.json: D1-A = 2 faces, symmetric, co-present; D1-B = 1 dominant host +
# intermittent 2nd; D2 = 4-face grid (presenter + tile column). All three trip _two_cluster.
_D1A = _stats([[_face(0.25), _face(0.75)] for _ in range(44)])
_D1B = _stats([[_face(0.35, fh=0.28, fw=0.17, score=0.95)] for _ in range(38)] +
              [[_face(0.35, fh=0.28, fw=0.17, score=0.95), _face(0.72, fh=0.20, fw=0.12, score=0.9)] for _ in range(22)])
_D2 = _stats([[_face(0.30, cy=0.5, fh=0.42, fw=0.24, score=0.95),
               _face(0.85, cy=0.2, fh=0.24, fw=0.14, score=0.9), _face(0.85, cy=0.5, fh=0.24, fw=0.14, score=0.9),
               _face(0.85, cy=0.8, fh=0.24, fw=0.14, score=0.9)] for _ in range(57)])


# ---- the primitive isolates D1-A from D1-B/D2 (all three trip _two_cluster; only D1-A is a genuine pair) ----
def test_primitive_isolates_d1a_wide_pair():
    c = framing.subject_aware_fallback(_D1A)
    assert c.kind == framing.FB_WIDE_PAIR
    assert c.left is not None and c.right is not None
    assert c.left[0] < 0.4 < c.right[0]                      # anchors on the two hosts, not the empty centre

def test_primitive_rejects_d1b_dominant_even_though_two_cluster_fires():
    assert framing._two_cluster(_D1B) is True                # the recall fires...
    assert framing.subject_aware_fallback(_D1B).kind == framing.FB_DOMINANT   # ...but a dominant host is not a pair (S3)

def test_primitive_rejects_d2_pip_even_though_two_cluster_fires():
    assert framing._two_cluster(_D2) is True                 # tiles clear the gate -> recall fires...
    assert framing.subject_aware_fallback(_D2).kind == framing.FB_DOMINANT    # ...but a 4-face grid is not a pair (S4/S5)


# ---- the resolver wiring: D1-A no-track -> STACKED_PAIR; D1-B / D2 stay the conservative centre ----
def test_d1a_no_track_resolves_to_stacked_pair(cfg, monkeypatch):
    """THE S2 INVARIANT. Before S2 this window went to CENTERED_MULTI_UNTRACKED (as_tuple None,None,None) —
    the empty-centre crop; now it carries the subject-derived pair composition. Exactly one strategy ran
    (speaker_track, a conclusive no_track), so this is a resolved composition, not a failure."""
    _stub(monkeypatch, detect_window=_D1A, classify_window=framing.CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.STACKED_PAIR and r.root_cause is None
    assert r.final_strategy is _FS.SUBJECT_PAIR
    assert r.content_type == framing.RENDER_STACK_PAIR
    assert r.focus is not None and len(r.focus) == 10 and all(isinstance(v, float) for v in r.focus)
    assert r.track is None
    assert r.classified_content_type == framing.CT_MULTI     # the classifier verdict is UNCHANGED (diagnostic only)
    assert [a.state for a in r.attempts] == [StrategyState.COMPLETED]

def test_d1b_no_track_is_never_reclassified_as_a_pair(cfg, monkeypatch):
    """D1-B (ONE dominant host + an intermittent 2nd) must never take D1-A's PAIR treatment — that is what the
    co-presence/prominence discriminators buy, and it is the enduring S2 invariant.

    S2 originally pinned it as "stays centred" because centred was then D1-B's only alternative. S3 gave D1-B
    its own subject-lock, so the assertion is stated in its DURABLE form: never a stack. The positive half of
    D1-B's routing is owned by tests/test_reframe_s3_d1b.py."""
    _stub(monkeypatch, detect_window=_D1B, classify_window=framing.CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert framing.subject_aware_fallback(_D1B).kind == framing.FB_DOMINANT   # the primitive still refuses to pair it
    assert r.final_outcome is not _FO.STACKED_PAIR
    assert r.content_type != framing.RENDER_STACK_PAIR
    assert r.final_outcome is _FO.SUBJECT_LOCKED                             # S3 owns D1-B's positive routing

def test_d2_no_track_stays_centred_not_stacked(cfg, monkeypatch):
    _stub(monkeypatch, detect_window=_D2, classify_window=framing.CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    r = framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.CENTERED_MULTI_UNTRACKED     # a PIP grid is not a live two-shot to stack
    assert r.as_tuple() == (None, None, None)


# ---- the stack render graph: both hosts retained (AC-A1/A2) ----
def _pair_focus():
    c = framing.subject_aware_fallback(_D1A)
    return tuple(float(v) for v in c.left) + tuple(float(v) for v in c.right)

def test_stack_graph_retains_both_hosts():
    fc = clip._stack_filter_complex(_pair_focus(), 1920, 1080, "9:16")
    assert fc.count("crop=") == 2                            # ONE crop per host -> neither dropped (AC-A1/A2)
    assert fc.count("scale=1080:960") == 2                   # each host fills its own half of the 1080x1920 frame
    assert "vstack=inputs=2[vout]" in fc

def test_stack_graph_burns_subtitles_on_the_stacked_frame():
    fc = clip._stack_filter_complex(_pair_focus(), 1920, 1080, "9:16", sub_token="subtitles=x.ass")
    assert "[sptop][spbot]vstack=inputs=2[vc]" in fc and fc.endswith("[vc]subtitles=x.ass[vout]")

def test_stack_cmd_maps_video_and_optional_audio():
    cmd = clip.ffmpeg_stack_cmd("s.mp4", "d.mp4", 1.0, 6.0, "9:16", _pair_focus(), src_w=1920, src_h=1080)
    assert "-filter_complex" in cmd and cmd[cmd.index("-map") + 1] == "[vout]"
    assert "0:a?" in cmd                                     # audio optional -> a video-only source never fails the map
    assert "5.000" in cmd                                    # -to is a DURATION (ce-cs), the fast-seek contract


# ---- fingerprint: EXACTLY the pair clips re-render; every other clip is byte-identical ----
def test_pair_fingerprint_flips_and_others_are_stable():
    base = dict(src_path="x.mp4", cs=0.0, ce=10.0, aspect_value="9:16", src_w=1920, src_h=1080, ass_text="")
    fp_centre = clip._render_fingerprint(**base, focus=None, track=None, content_type=None)
    fp_pair = clip._render_fingerprint(**base, focus=_pair_focus(), track=None, content_type=framing.RENDER_STACK_PAIR)
    assert fp_pair != fp_centre                              # the D1-A clip re-renders (focus None -> pair): the intended 6
    single = (0.6, 0.45, 0.3, 0.4, 0.16)                     # a single-subject clip: S2 added no field it carries...
    fp_a = clip._render_fingerprint(**base, focus=single, track=None, content_type=framing.CT_SINGLE)
    fp_b = clip._render_fingerprint(**base, focus=single, track=None, content_type=framing.CT_SINGLE)
    assert fp_a == fp_b                                      # ...so it is byte-identical -> it does NOT re-render
    assert fp_pair != fp_a


# ---- the defensive guard: a stray stack-pair focus never crashes reframe_filter ----
def test_reframe_filter_guard_centres_a_stray_pair():
    vf = clip.reframe_filter("9:16", 1920, 1080, focus=_pair_focus(), content_type=framing.RENDER_STACK_PAIR)
    assert vf == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"    # centred (the pair renders via render_reframed, not here)
