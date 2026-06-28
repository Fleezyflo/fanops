# tests/test_transcribe_timeout.py — the whisper subprocess bound is duration-aware AND lock-aware.
# Root bug it guards: a long source (e.g. 58min) blew the fixed 45min in-lock cap every pass -> never
# transcribed -> frozen at `catalogued` forever. The OUT-OF-LOCK prewarm now scales its budget to the
# source length (no lock held, so a long run starves nothing); the IN-LOCK path keeps the tight flock cap.
from fanops.transcribe import _whisper_timeout, _WHISPER_TIMEOUT, _PREWARM_TIMEOUT_FACTOR


def test_in_lock_is_always_the_tight_fixed_cap():
    # in-lock holds the flock -> must never scale, however long the source.
    assert _whisper_timeout(60.0, lock_held=True) == _WHISPER_TIMEOUT
    assert _whisper_timeout(6000.0, lock_held=True) == _WHISPER_TIMEOUT
    assert _whisper_timeout(None, lock_held=True) == _WHISPER_TIMEOUT


def test_prewarm_scales_with_length_for_long_sources():
    # a 58min (3480s) source out-of-lock gets a budget that covers it (the wedge fix): 3480*1.5 = 5220 > 2700.
    assert _whisper_timeout(3480.0, lock_held=False) == 3480.0 * _PREWARM_TIMEOUT_FACTOR
    assert _whisper_timeout(3480.0, lock_held=False) > _WHISPER_TIMEOUT


def test_prewarm_never_below_the_floor():
    # a short source out-of-lock still gets at least the fixed floor (no needlessly tiny budget).
    assert _whisper_timeout(60.0, lock_held=False) == _WHISPER_TIMEOUT
    assert _whisper_timeout(None, lock_held=False) == _WHISPER_TIMEOUT
    assert _whisper_timeout(0.0, lock_held=False) == _WHISPER_TIMEOUT
