# tests/test_hook_language_gate.py — language gate: hook language must match source language at ingest.
# Arabic source + English hook → clip ships CLEAN (hook_removed set, hook None).
# Arabic source + Arabic hook → hook kept.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (MomentPick, MomentHookDecision, Source, MomentDecision, MomentState,
                           SourceState)
from fanops.moments import ingest_moment_hooks, request_moment_hooks, ingest_moments, request_moments
from fanops.agentstep import response_path, latest_request_id
from fanops.responder import screen_model_text
from tests.fixtures.speech_segments import GOOD_AR, talk_seg


def _seed_led(cfg, language: str):
    led = Ledger.load(cfg)
    if language == "ar":
        segs = [{**GOOD_AR, "start": 10.0, "end": 28.0, "text": "الجزء اللي هتعيده"}]
    else:
        segs = [talk_seg("they slept on me here", start=10.0, end=28.0)]
    led.add_source(Source(id="src_ar", source_path=str(cfg.sources / "src_ar.mp4"),
                          state=SourceState.signalled, language=language, duration=60.0,
                          transcript=segs, meta={"transcribed": True}))
    return led


def _pick_and_request_hook(cfg, led):
    """Pick a moment and submit the hook request gate so ingest_moment_hooks can run."""
    request_moments(led, cfg, "src_ar")
    rid = latest_request_id(cfg, "moments", "src_ar")
    pick = MomentPick(start=10, end=28, reason="r")
    response_path(cfg, "moments", "src_ar").write_text(
        MomentDecision(source_id="src_ar", request_id=rid, picks=[pick]).model_dump_json())
    led = ingest_moments(led, cfg, "src_ar")
    led = request_moment_hooks(led, cfg, "src_ar")
    return led


def _submit_hook(cfg, hook_text: str | None):
    """Write the hook decision response for the gate key."""
    rid = latest_request_id(cfg, "moment_hooks", "src_ar.10.00-28.00")
    dec = screen_model_text(MomentHookDecision(request_id=rid, hook=hook_text))
    response_path(cfg, "moment_hooks", "src_ar.10.00-28.00").write_text(dec.model_dump_json())


# --- _hook_lang_base unit tests ---------------------------------------------------------------

def test_hook_lang_base_arabic():
    from fanops.moments import _hook_lang_base
    assert _hook_lang_base("الجزء اللي هتعيده") == 'ar'


def test_hook_lang_base_english():
    from fanops.moments import _hook_lang_base
    assert _hook_lang_base("the part you will replay") == 'en'


def test_hook_lang_base_empty():
    from fanops.moments import _hook_lang_base
    assert _hook_lang_base("") is None


def test_hook_lang_base_emoji_only():
    from fanops.moments import _hook_lang_base
    assert _hook_lang_base("🔥🎵") is None


# --- language gate integration tests ----------------------------------------------------------

def test_arabic_source_english_hook_removed(tmp_path):
    """Arabic source + English-language hook → clip ships CLEAN: hook=None, hook_removed preserved."""
    cfg = Config(root=tmp_path)
    led = _seed_led(cfg, language="ar")
    led = _pick_and_request_hook(cfg, led)
    _submit_hook(cfg, hook_text="the part you will replay")    # English hook on Arabic source
    led = ingest_moment_hooks(led, cfg, "src_ar")
    m = led.moments_of("src_ar")[0]
    assert m.state is MomentState.decided
    assert m.hook is None, "English hook on Arabic source must be rejected (language gate)"
    assert m.hook_removed == "the part you will replay", "rejected hook must be preserved in hook_removed"


def test_arabic_source_arabic_hook_kept(tmp_path):
    """Arabic source + Arabic-language hook → hook is kept as-is."""
    cfg = Config(root=tmp_path)
    led = _seed_led(cfg, language="ar")
    led = _pick_and_request_hook(cfg, led)
    arabic_hook = "الجزء اللي هتعيده"
    _submit_hook(cfg, hook_text=arabic_hook)    # Arabic hook on Arabic source
    led = ingest_moment_hooks(led, cfg, "src_ar")
    m = led.moments_of("src_ar")[0]
    assert m.state is MomentState.decided
    assert m.hook == arabic_hook, "Arabic hook on Arabic source must be kept"
    assert m.hook_removed is None


def test_english_source_english_hook_kept(tmp_path):
    """English source + English hook → hook kept (gate only fires on language mismatch)."""
    cfg = Config(root=tmp_path)
    led = _seed_led(cfg, language="en")
    led = _pick_and_request_hook(cfg, led)
    _submit_hook(cfg, hook_text="the part you will replay")
    led = ingest_moment_hooks(led, cfg, "src_ar")
    m = led.moments_of("src_ar")[0]
    assert m.state is MomentState.decided
    assert m.hook == "the part you will replay"


def test_source_no_language_hook_kept(tmp_path):
    """Source with no declared language → gate is skip (fail-open: hook kept)."""
    cfg = Config(root=tmp_path)
    led = _seed_led(cfg, language=None)
    led = _pick_and_request_hook(cfg, led)
    _submit_hook(cfg, hook_text="the part you will replay")
    led = ingest_moment_hooks(led, cfg, "src_ar")
    m = led.moments_of("src_ar")[0]
    assert m.state is MomentState.decided
    assert m.hook == "the part you will replay", "no source language → gate skip, hook kept"


# --- Path C prompt does NOT mandate ENGLISH ---------------------------------------------------

def test_path_c_no_english_mandate():
    """_hook_decision Path C must no longer mandate an ENGLISH hook for Arabic sources."""
    from fanops.prompts import moment_hook_prompt
    payload = {"start": 10.0, "end": 28.0, "reason": "r", "transcript_excerpt": "",
               "language": "ar", "guidance": "", "frames": [], "signal_peaks": [],
               "personas": [{"handle": "test", "persona": "craft angle"}]}
    out = moment_hook_prompt(payload)
    # Path C must still appear (Curiosity/Tension mechanism for dense Arabic), but the
    # "ENGLISH hook" mandate must be gone.
    assert "ENGLISH hook" not in out, "Path C must not mandate ENGLISH for Arabic sources"
    assert "Curiosity" in out or "Tension" in out, "Path C mechanism must still be present"
