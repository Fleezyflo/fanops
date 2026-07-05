# tests/test_audio_energy.py
# Theme 1 (pipeline-quality) — pure ffmpeg astats RMS parser + dBFS->strength normalizer. No real
# ffmpeg: feed raw `ametadata=print` text fixtures to the pure parser (mirrors tests/test_signals.py).
import math
from fanops.audio_energy import energy_cmd, parse_energy, rms_to_strength

# A realistic slice of `astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level`
# output: each reset window prints a `frame:.. pts_time:T` line then the RMS_level key=value line.
_FIXTURE = """\
frame:0    pts:0       pts_time:0.000000
lavfi.astats.Overall.RMS_level=-23.450000
frame:43   pts:44032   pts_time:1.024000
lavfi.astats.Overall.RMS_level=-7.100000
frame:86   pts:88064   pts_time:2.048000
lavfi.astats.Overall.RMS_level=-inf
"""

def test_parse_energy_pairs_pts_time_with_rms():
    out = parse_energy(_FIXTURE)
    assert [(d["t"], d["rms"]) for d in out[:2]] == [(0.0, -23.45), (1.024, -7.1)]
    assert out[2]["t"] == 2.048 and out[2]["rms"] == float("-inf")   # digital silence -> -inf

def test_parse_energy_ignores_unpaired_and_garbled_lines():
    # An RMS line with no preceding pts_time, and noise lines, must be skipped — never raise.
    text = "garbage header\nlavfi.astats.Overall.RMS_level=-12.0\nframe:9 pts_time:3.5\nnot a metric line\n"
    out = parse_energy(text)
    assert out == []   # the lone RMS has no pts_time; the lone pts_time has no following RMS

def test_parse_energy_empty_text_is_empty_list():
    assert parse_energy("") == []

def test_rms_to_strength_maps_dbfs_into_unit_band():
    assert rms_to_strength(-7.1) > 0.9          # a loud drop -> near the top of the band
    assert rms_to_strength(-45.0) < 0.2         # quiet speech -> low strength
    assert rms_to_strength(0.0) == 1.0          # at/above the ceiling -> clamped to 1
    assert rms_to_strength(-90.0) == 0.0        # below the floor -> clamped to 0
    assert rms_to_strength(float("-inf")) == 0.0   # digital silence -> 0
    # monotonic: louder (less negative) is never weaker
    assert rms_to_strength(-10.0) >= rms_to_strength(-20.0) >= rms_to_strength(-30.0)

def test_rms_to_strength_is_bounded():
    for db in (-1000.0, -50.0, -5.0, 5.0, 1000.0):
        s = rms_to_strength(db)
        assert 0.0 <= s <= 1.0 and not math.isnan(s)

def test_energy_cmd_builds_the_astats_rms_pass():
    cmd = energy_cmd("/x/in.mp4")
    j = " ".join(cmd)
    assert cmd[0] == "ffmpeg" and "/x/in.mp4" in cmd
    assert "-vn" in cmd                                  # MOL-119: audio-only — never decode video
    assert "astats=metadata=1:reset=1" in j               # per-window RMS
    assert "ametadata=print:key=lavfi.astats.Overall.RMS_level" in j   # the metadata channel (NOT ebur128 M:)
    assert "-f" in cmd and "null" in cmd                  # null sink (analysis only)
