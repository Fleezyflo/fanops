"""P1 T1: pick_visual_start refines the cut start onto the STRONGEST opening frame within a bounded
shift (the top muted-autoplay lever after the text hook). It probes a few candidate frames via ffmpeg
signalstats (the clip.py subprocess seam), scores them purely (frames.py), and CACHES the decision in
a sidecar so the in-lock commit pass adopts it with no ffmpeg — preserving Phase D's lock-free render."""
from pathlib import Path
from fanops.clip import pick_visual_start, _vstart_candidate_times


def _stats(yavg, ymin, ymax):
    return (f"lavfi.signalstats.YMIN={ymin}\n"
            f"lavfi.signalstats.YAVG={yavg}\n"
            f"lavfi.signalstats.YMAX={ymax}\n")

_BLACK = _stats(3.0, 0, 200)     # near-black opening -> rejected by the strength floor

def _probe(strong_at, *, ymax=200):
    """A subprocess.run side_effect: strong stats when -ss matches strong_at, near-black otherwise."""
    def run(cmd, **kw):
        t = float(cmd[cmd.index("-ss") + 1])
        class R:
            returncode = 0; stderr = ""
            stdout = _stats(120.0, 16, ymax) if abs(t - strong_at) < 1e-3 else _BLACK
        return R()
    return run


def test_candidate_times_start_at_cut_and_stay_in_bounds():
    times = _vstart_candidate_times(10.0, 28.0)
    assert times[0] == 10.0                    # the current start is always a candidate (allows "no move")
    assert all(10.0 <= t <= 28.0 for t in times)
    assert times == sorted(times) and len(times) >= 2

def test_moves_cut_to_strongest_frame(tmp_path, mocker):
    target = _vstart_candidate_times(10.0, 28.0)[2]
    mocker.patch("fanops.clip.subprocess.run", side_effect=_probe(target))
    start, kind = pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=[], out_dir=tmp_path)
    assert kind == "visual"
    assert abs(start - target) < 1e-3

def test_falls_back_to_start_when_all_weak(tmp_path, mocker):
    def allblack(cmd, **kw):
        class R: returncode = 0; stderr = ""; stdout = _BLACK
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=allblack)
    start, kind = pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=[], out_dir=tmp_path)
    assert start == 10.0 and kind == "transcript"

def test_decision_is_cached_in_a_sidecar_no_reprobe(tmp_path, mocker):
    # The Phase-D-preserving property: a 2nd call (same source+window) reads the sidecar and runs NO
    # subprocess — so the in-lock commit never re-spawns frame-probe ffmpeg under the flock.
    def allblack(cmd, **kw):
        class R: returncode = 0; stderr = ""; stdout = _BLACK
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=allblack)
    pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=[], out_dir=tmp_path)
    assert list(Path(tmp_path).glob("vstart_*.json")), "expected a written decision sidecar"
    spy = mocker.patch("fanops.clip.subprocess.run")
    start, kind = pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=[], out_dir=tmp_path)
    spy.assert_not_called()
    assert start == 10.0 and kind == "transcript"

def test_fail_open_when_ffmpeg_absent(tmp_path, mocker):
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.clip.subprocess.run", side_effect=absent)
    start, kind = pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=[], out_dir=tmp_path)  # never raises
    assert start == 10.0 and kind == "transcript"

def test_scene_cut_breaks_a_strength_tie(tmp_path, mocker):
    times = _vstart_candidate_times(10.0, 28.0)
    a, b = times[1], times[3]                  # two equally-strong frames
    def run(cmd, **kw):
        t = float(cmd[cmd.index("-ss") + 1])
        on = abs(t - a) < 1e-3 or abs(t - b) < 1e-3
        class R:
            returncode = 0; stderr = ""
            stdout = _stats(120.0, 16, 136) if on else _BLACK   # equal contrast (120) for a and b
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    scene = [{"t": b, "kind": "scene_cut", "score": 0.9}]       # b sits on a real cut
    start, kind = pick_visual_start("/s/x.mp4", 10.0, 28.0, scene_peaks=scene, out_dir=tmp_path)
    assert abs(start - b) < 1e-3 and kind == "visual"
