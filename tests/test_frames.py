"""Pure frame-strength scoring for P1's first-frame picker. No ffmpeg, no pixels: the score is
parsed from ffmpeg `signalstats` text (luma YAVG + contrast YMAX-YMIN) and ranked. The subprocess
that produces the text lives in clip.py (the mocked seam); these functions stay pure + unit-testable."""
from fanops.frames import parse_signalstats, frame_strength, pick_strongest


def _stats_block(yavg, ymin, ymax):
    return (f"[Parsed_metadata_1 @ 0x0] lavfi.signalstats.YMIN={ymin}\n"
            f"[Parsed_metadata_1 @ 0x0] lavfi.signalstats.YAVG={yavg}\n"
            f"[Parsed_metadata_1 @ 0x0] lavfi.signalstats.YMAX={ymax}\n")

def test_parse_signalstats_extracts_luma_and_contrast():
    luma, contrast = parse_signalstats(_stats_block(120.5, 16, 235))
    assert luma == 120.5
    assert contrast == 235 - 16            # YMAX - YMIN

def test_parse_signalstats_none_when_missing_fields():
    assert parse_signalstats("no stats here") is None
    assert parse_signalstats("lavfi.signalstats.YAVG=120\n") is None   # YMIN/YMAX absent -> no contrast

def test_frame_strength_rejects_near_black():
    assert frame_strength(luma=6.0, contrast=80.0) is None     # near-black opening = weakest still

def test_frame_strength_rejects_blown_white():
    assert frame_strength(luma=250.0, contrast=80.0) is None    # blown highlight, white hook unreadable

def test_frame_strength_rejects_flat_frame():
    assert frame_strength(luma=120.0, contrast=8.0) is None     # near-uniform = a transition/blur frame

def test_frame_strength_scores_a_good_frame():
    s = frame_strength(luma=120.0, contrast=90.0)
    assert s is not None and s > 0

def test_frame_strength_prefers_more_contrast():
    assert frame_strength(luma=120.0, contrast=120.0) > frame_strength(luma=120.0, contrast=60.0)

def test_pick_strongest_picks_highest_contrast_survivor():
    cands = [
        {"t": 10.0, "luma": 120.0, "contrast": 40.0, "scene": 0.0},
        {"t": 10.5, "luma": 120.0, "contrast": 110.0, "scene": 0.0},   # strongest
        {"t": 11.0, "luma": 4.0, "contrast": 200.0, "scene": 0.0},     # near-black -> rejected
    ]
    win = pick_strongest(cands)
    assert win is not None and win["t"] == 10.5

def test_pick_strongest_scene_cut_breaks_a_tie():
    cands = [
        {"t": 10.0, "luma": 120.0, "contrast": 90.0, "scene": 0.0},
        {"t": 10.5, "luma": 120.0, "contrast": 90.0, "scene": 0.8},    # same strength, on a real cut
    ]
    win = pick_strongest(cands)
    assert win["t"] == 10.5

def test_pick_strongest_none_when_all_degenerate():
    cands = [
        {"t": 10.0, "luma": 3.0, "contrast": 90.0, "scene": 0.0},      # black
        {"t": 10.5, "luma": 120.0, "contrast": 5.0, "scene": 0.0},     # flat
    ]
    assert pick_strongest(cands) is None

def test_pick_strongest_empty_is_none():
    assert pick_strongest([]) is None
