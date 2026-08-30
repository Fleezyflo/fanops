# tests/fixtures/speech_segments.py — shared speech-trust segment corpus (Plan K)
"""Reusable transcript segment dicts for speech-trust / transcribe boundary tests."""

GOOD_AR = {"start": 0.0, "end": 2.0, "text": " ورا الستارة",
           "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}

# Sung/rap: window-level no_speech_prob is high (beat looks like "not speech") but the decoder
# committed to the tokens. L1 trusts avg_logprob + compression_ratio — this is full, not junk.
MUSIC_HALLUC = {"start": 0.0, "end": 2.0, "text": "background noise",
                "avg_logprob": -0.2, "no_speech_prob": 0.9, "compression_ratio": 1.2}

# Actual L1 reject: decoder is unsure. Use this where a test needs untrusted ASR.
LOW_LOGPROB = {"start": 0.0, "end": 2.0, "text": "gibberish line",
               "avg_logprob": -1.5, "no_speech_prob": 0.1, "compression_ratio": 1.2}

LATIN_JUNK_AR = {"start": 0.0, "end": 2.0, "text": "man shay khbar hada",
                 "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}

CJK_JUNK_EN = {"start": 0.0, "end": 2.0, "text": "東西東西test",
               "avg_logprob": -0.2, "no_speech_prob": 0.05, "compression_ratio": 1.2}

LEGACY_EN = {"start": 0.0, "end": 2.0, "text": "they slept on me"}


def talk_seg(text, **kw):
    """Segment dict with good quality-metadata defaults for framing / adopt tests."""
    seg = {"start": 0.0, "end": 2.0, "text": text,
           "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}
    seg.update(kw)
    return seg
