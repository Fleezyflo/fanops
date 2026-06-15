# tests/test_hookcheck.py — the deterministic hook-quality guard. Fail-cases are the ACTUAL weak
# hooks the model produced in round 1 (regression-locked); pass-cases are the actual strong ones.
# High-precision by design: it rejects only clear slop patterns, leaving nuanced calls to the prompt
# (and a later LLM critic). A rejected hook becomes None -> a clean clip (clean beats slop).
import pytest
from fanops.hookcheck import is_weak_hook

# --- clear slop the guard MUST reject (verbatim from the round-1 dump) ---
@pytest.mark.parametrize("hook", [
    "his coldest opener", "his hardest bravado run", "his realest bar yet",
    "his most slept-on bars", "his most slept on bar", "his most replayed hook",  # generic superlative template
    "the bar everyone replayed",                                                   # cliche
    "the intro hits different",                                                    # cliche
    "watch how he cuts", "watch the cuts speed up",                                # hooking on the editing
])
def test_known_weak_hooks_are_rejected(hook):
    assert is_weak_hook(hook) is True

@pytest.mark.parametrize("hook", ["", "   ", None])
def test_empty_hook_is_weak(hook):
    assert is_weak_hook(hook) is True

# --- strong concrete hooks the guard MUST keep (verbatim strong examples) ---
@pytest.mark.parametrize("hook", [
    "before he was Moh Flow",
    "no label, no machine, just Harmony",
    "the word he repeated twice",
    "indie artists live or die in week one",
    "Moh Flow just teased the album",
    "the bar that called his bluff",
    "اسمع الكلام قبل ما يوجع",                      # Arabic concrete hook must pass (no English-only bias)
])
def test_strong_concrete_hooks_pass(hook):
    assert is_weak_hook(hook) is False

def test_duplicate_against_used_is_rejected():
    # the same hook already used on another clip this run -> reject the repeat (kills cross-feed
    # repetition, the 'reads like a bot' tell). Case/space-insensitive.
    used = {"wait for the switch up"}
    assert is_weak_hook("Wait For The Switch Up", used=used) is True
    assert is_weak_hook("wait for the punch in", used=used) is False   # a different, fresh hook passes
