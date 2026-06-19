"""Pure frame-strength scoring for P1's first-frame picker. No ffmpeg, no pixels: the score is
parsed from ffmpeg `signalstats` text (luma YAVG + contrast YMAX-YMIN) and ranked. The subprocess
that produces the text lives in clip.py (the mocked seam); these functions stay pure + unit-testable."""
from fanops.frames import parse_signalstats, parse_sharpness, frame_strength, pick_strongest


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

# ---- Theme 3: sharpness term (a relative edge-energy proxy) discriminates blur the contrast floor passes ----

def test_frame_strength_none_sharpness_is_contrast_byte_identical():
    # No sharpness supplied -> the score is EXACTLY today's contrast (the contrast-only path is untouched).
    assert frame_strength(luma=120.0, contrast=60.0) == 60.0
    assert frame_strength(luma=120.0, contrast=60.0, sharpness=None) == 60.0

def test_frame_strength_sharpness_demotes_blurry_high_contrast():
    # A busy-but-SOFT frame (high contrast, low edge energy) must score BELOW a crisp frame with less
    # contrast — the whole point of adding sharpness (contrast alone ranks the blurry one first).
    blurry = frame_strength(luma=120.0, contrast=100.0, sharpness=2.0)
    crisp = frame_strength(luma=120.0, contrast=60.0, sharpness=20.0)
    assert crisp > blurry

def test_frame_strength_sharpness_still_honors_brightness_and_flat_floors():
    assert frame_strength(luma=6.0, contrast=100.0, sharpness=50.0) is None    # near-black still rejected
    assert frame_strength(luma=250.0, contrast=100.0, sharpness=50.0) is None  # blown still rejected
    assert frame_strength(luma=120.0, contrast=10.0, sharpness=50.0) is None   # flat still rejected (sharp ≠ busy)

def test_parse_sharpness_reads_edge_energy_yavg():
    # sharpness proxy = YAVG of a Laplacian-convolved gray frame (mean edge energy). Higher = sharper.
    assert parse_sharpness(_stats_block(33.7, 0, 200)) == 33.7
    assert parse_sharpness("no stats") is None

def test_pick_strongest_uses_sharpness_to_break_blur():
    # Among floor-passing candidates, the crisp frame wins even though another is busier but soft.
    cands = [
        {"t": 10.0, "luma": 120.0, "contrast": 100.0, "sharpness": 2.0, "scene": 0.0},   # busy but soft
        {"t": 10.5, "luma": 120.0, "contrast": 60.0, "sharpness": 25.0, "scene": 0.0},   # crisp
    ]
    assert pick_strongest(cands)["t"] == 10.5

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
