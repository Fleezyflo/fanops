# tests/fixtures/speech_segments.py — shared speech-trust segment corpus (Plan K)
"""Reusable transcript segment dicts for speech-trust / transcribe boundary tests."""

GOOD_AR = {"start": 0.0, "end": 2.0, "text": " ورا الستارة",
           "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}

MUSIC_HALLUC = {"start": 0.0, "end": 2.0, "text": "background noise",
                "avg_logprob": -0.2, "no_speech_prob": 0.9, "compression_ratio": 1.2}

LATIN_JUNK_AR = {"start": 0.0, "end": 2.0, "text": "man shay khbar hada",
                 "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}

CJK_JUNK_EN = {"start": 0.0, "end": 2.0, "text": "man東西test",
               "avg_logprob": -0.2, "no_speech_prob": 0.05, "compression_ratio": 1.2}

LEGACY_EN = {"start": 0.0, "end": 2.0, "text": "they slept on me"}


def talk_seg(text, **kw):
    """Segment dict with good quality-metadata defaults for framing / adopt tests."""
    seg = {"start": 0.0, "end": 2.0, "text": text,
           "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.5}
    seg.update(kw)
    return seg
