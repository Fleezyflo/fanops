"""Unit-level guard for the E2E's transcript assertion (CI-2).

The real-tooling E2E (tests/integration/test_e2e_real.py) synthesizes speech with whatever TTS
is on the host (`say` on macOS, espeak on the Linux CI runner) and transcribes it with whisper.
Its OLD assertion — `assert "slept" in joined` — bet on one literal token surviving a specific
vocoder. It held for macOS `say` but FAILED in CI: espeak + whisper-tiny decodes the same sample
as "Nice lap, Tommy, not anymore." (no "slept").

The E2E's real contract (its own comment) is "the transcript is non-empty and carries the words"
— i.e. REAL whisper produced a REAL, substantive transcript, NOT that one specific word survived.
`real_transcript_signal()` encodes that contract so it can be unit-tested against fixtures here
(both engines' ACTUAL transcripts) without a 30s real-tooling run. RED/GREEN is proven against the
real espeak transcript captured from the failing CI log.
"""
from fanops.transcribe import real_transcript_signal

# The two transcripts whisper-tiny ACTUALLY produced from the E2E's sample, verbatim:
#   - macOS `say`  : "They slept on me not anymore." (passed the old "slept" check)
#   - Linux espeak : "Nice lap, Tommy, not anymore." (FAILED the old "slept" check — the CI red)
# Both are real whisper output with real segment timing; both must satisfy the new contract.
_SAY_TRANSCRIPT = [{"start": 0.0, "end": 1.84, "text": "They slept on me not anymore."}]
_ESPEAK_TRANSCRIPT = [{"start": 0.0, "end": 1.68, "text": "Nice lap, Tommy, not anymore."}]

# What a regression to a fake/empty/stub transcript looks like — the v1 bug this E2E guards against.
_EMPTY = []
_STUB_ONE_WORD = [{"start": 0.0, "end": 0.5, "text": "x"}]
_NO_TIMING = [{"text": "they slept on me not anymore"}]  # words but no real whisper segment shape


def test_signal_accepts_real_macos_say_transcript():
    assert real_transcript_signal(_SAY_TRANSCRIPT) is True


def test_signal_accepts_real_espeak_transcript():
    # THE CI-2 REGRESSION GUARD: the espeak transcript that broke the old `"slept"` assertion
    # must PASS the new contract — it IS a real, substantive whisper transcript.
    assert real_transcript_signal(_ESPEAK_TRANSCRIPT) is True


def test_signal_robust_anchor_survives_both_vocoders():
    # The robust content anchor ("anymore") is present in BOTH engines' real output, unlike
    # "slept" which only `say` produced. Proven directly against the recorded text.
    assert "anymore" in _SAY_TRANSCRIPT[0]["text"].lower()
    assert "anymore" in _ESPEAK_TRANSCRIPT[0]["text"].lower()


def test_signal_rejects_empty_transcript():
    assert real_transcript_signal(_EMPTY) is False


def test_signal_rejects_one_word_stub():
    # `len(joined) > 0` would wrongly accept this; the multi-word requirement rejects it.
    assert real_transcript_signal(_STUB_ONE_WORD) is False


def test_signal_rejects_segments_without_real_whisper_timing():
    # A fabricated string with the right words but no real (start,end) segment shape is NOT
    # proof whisper ran — reject it (structure requirement).
    assert real_transcript_signal(_NO_TIMING) is False


def test_old_slept_rule_would_have_failed_on_espeak():
    # Documents WHY CI went red: the old assertion over-specified one token. This pins the
    # regression so nobody reintroduces a single-vocoder-specific word check.
    joined = " ".join(seg["text"].lower() for seg in _ESPEAK_TRANSCRIPT)
    assert "slept" not in joined  # the old `assert "slept" in joined` raised here in CI
