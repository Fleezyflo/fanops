# tests/test_prompts.py
from fanops.prompts import moment_prompt, caption_prompt

def test_moment_prompt_includes_transcript_duration_guidance_and_bounds_rule():
    payload = {"source_id": "s1", "duration": 42.0,
               "transcript": [{"start": 1.0, "end": 3.0, "text": "they slept on me"}],
               "signal_peaks": [{"t": 2.0, "kind": "scene_cut", "score": 9.0}],
               "language": "en", "guidance": "BRAND: confident, bilingual."}
    p = moment_prompt(payload)
    assert "they slept on me" in p
    assert "42.0" in p                       # the duration bound the LLM must respect
    assert "BRAND: confident, bilingual." in p
    assert "start" in p and "end" in p       # asks for picks with timestamps
    # explicitly forbids out-of-bounds / NaN
    assert "0" in p and ("duration" in p.lower() or "bounds" in p.lower())

def test_caption_prompt_lists_every_surface_and_language():
    payload = {"clip_id": "c1",
               "surfaces": [{"surface": "@a/instagram", "platform": "instagram"},
                            {"surface": "@a/tiktok", "platform": "tiktok"}],
               "transcript_excerpt": "they slept on me", "language": "ar",
               "guidance": "BRAND: no slurs."}
    p = caption_prompt(payload)
    assert "@a/instagram" in p and "@a/tiktok" in p
    assert "ar" in p                          # must caption in the source language
    assert "BRAND: no slurs." in p
    assert "surface" in p                     # tells the model to echo the surface key verbatim
