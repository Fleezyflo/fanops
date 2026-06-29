# tests/test_transcribe_timeout.py — the whisper subprocess bound is duration-aware AND single-mode.
# Root bug it guards: a long source (e.g. 58min) blew the fixed 45min cap every pass -> never transcribed
# -> frozen at `catalogued` forever. The fix is to scale the budget to the source length so a long source
# actually finishes.
#
# M1 collapse: the OLD dual-mode contract (in-lock tight cap vs out-of-lock length-scaled cap) is GONE.
# transcribe_source no longer runs inside the ledger flock — it runs inside the per-(stage,source)
# stage_lock (src/fanops/stage_lock.py), which serializes ONLY the same source against itself. So the
# "tight cap to protect the flock" reason no longer applies; one mode, length-scaled with a floor.
from fanops.transcribe import _whisper_timeout, _WHISPER_TIMEOUT, _PREWARM_TIMEOUT_FACTOR


def test_long_source_scales_with_length():
    # a 58min (3480s) source gets a budget that covers it (the wedge fix): 3480*1.5 = 5220 > 2700.
    assert _whisper_timeout(3480.0) == 3480.0 * _PREWARM_TIMEOUT_FACTOR
    assert _whisper_timeout(3480.0) > _WHISPER_TIMEOUT


def test_short_or_unknown_duration_uses_the_floor():
    # a short / unknown / zero-duration source still gets at least the fixed floor — no tiny budget.
    assert _whisper_timeout(60.0) == _WHISPER_TIMEOUT
    assert _whisper_timeout(None) == _WHISPER_TIMEOUT
    assert _whisper_timeout(0.0) == _WHISPER_TIMEOUT
