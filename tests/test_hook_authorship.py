# tests/test_hook_authorship.py — the root fix: the frame-seeing MOMENT author (Opus) owns ALL on-screen
# hook authorship, including per-account variants keyed by handle. The blind caption gate (Sonnet) writes
# NO hook. RED until the schema fields + moment_prompt per-persona block land.
from fanops.models import MomentPick, Moment
from fanops.prompts import moment_prompt

# ---- Task 1: schema carries per-persona (handle-keyed) hooks ----

def test_momentpick_carries_hooks_by_persona():
    p = MomentPick(start=0, end=5, reason="r", hooks_by_persona={"markmakmouly": "watch the craft"})
    assert p.hooks_by_persona["markmakmouly"] == "watch the craft"

def test_momentpick_defaults_empty_hooks_by_persona():
    p = MomentPick(start=0, end=5, reason="r")           # old responses (no key) still validate
    assert p.hooks_by_persona == {}

def test_moment_carries_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r",
               hooks_by_persona={"markmakmouly": "x"})
    assert m.hooks_by_persona == {"markmakmouly": "x"}

def test_moment_defaults_empty_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r")   # old ledger rows load fine
    assert m.hooks_by_persona == {}

# ---- Task 1: moment_prompt asks for one frame-grounded hook PER HANDLE, in that persona's voice ----

def _payload(**extra):
    base = {"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""}
    base.update(extra)
    return base

def test_moment_prompt_asks_for_per_persona_hooks_when_personas_present():
    out = moment_prompt(_payload(personas=[
        {"handle": "markmakmouly", "persona": "champions craft, watch-for-the-craft angle"},
        {"handle": "perca.late", "persona": "underground raw, no-frills street attitude"}]))
    assert "hooks_by_persona" in out                     # the author is told to RETURN the per-handle map
    assert "markmakmouly" in out and "perca.late" in out  # keyed by handle
    assert "champions craft" in out                       # the persona voice reaches the frame-seeing author

def test_moment_prompt_byte_identical_without_personas():
    out = moment_prompt(_payload())                       # no personas key -> no block (back-compat)
    assert "hooks_by_persona" not in out
