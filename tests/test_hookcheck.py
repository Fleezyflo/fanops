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

def test_opening_template_cluster_is_rejected():
    # The "before he was Moh Flow x6" failure: distinct STRINGS that share an opening TEMPLATE read
    # like a bot. Once >=2 accepted hooks share the first two words, the next one is rejected. This is
    # the no-LLM floor that closes feed-clustering for EVERY config (not just hook-editor-on).
    used = {"wait for the beat drop", "wait for the last line"}        # 2 already share "wait for"
    assert is_weak_hook("wait for the hometown line", used=used) is True   # the 3rd -> cluster, rejected

def test_second_shared_opening_still_allowed():
    used = {"wait for the beat drop"}                                  # only one so far
    assert is_weak_hook("wait for the last line", used=used) is False  # the 2nd is fine; not yet a cluster

def test_distinct_openings_are_not_clustered():
    used = {"wait for the beat drop", "wait for the last line"}        # a "wait for" cluster exists
    assert is_weak_hook("nobody clipped this part", used=used) is False  # a DIFFERENT opening is unaffected

def test_common_first_word_different_second_not_clustered():
    used = {"the bar nobody saw", "the last word lands"}               # share only "the", not 2 tokens
    assert is_weak_hook("the line he kept", used=used) is False        # second token differs -> not a cluster


# --- round-3 (operator: "the hook writing is not good"): two NEW deterministic floors. The hooks
# drifted CRYPTIC ('the rose lands on one word') and SHOT-DESCRIPTIVE ('drone up, crowd in'). The
# legibility judgment stays in the prompt+editor; these two kills are the high-precision floor. ---
@pytest.mark.parametrize("hook", [
    "drone up, crowd in", "zoom in slow", "the camera pans out", "aerial over the stage",
])
def test_shot_description_hooks_are_rejected(hook):
    # A hook that narrates the CAMERA/shot is slop: the viewer already SEES the shot, so it carries no
    # curiosity. Narrow term list (drone/zoom/pan/aerial/camera) -> high precision, no false positives.
    assert is_weak_hook(hook) is True

# NB: the no-antecedent "pronoun soup" call ('she says it back') is intentionally NOT a deterministic
# kill — a regex over-fires (it would also reject the legible 'he names the day it changed'). The
# _hook_spec COLD-VIEWER GATE + the LLM editor own that legibility judgment; this floor stays precise.
@pytest.mark.parametrize("hook", [
    "he started by copying his idols",   # opens 'he' but the object is concrete -> MUST pass
    "cross him, meet the beast",         # 'him' is an object, payoff is concrete
    "loyal, until you cross him",
    "POV: you found him first",          # the hook-spec's own pov example
    "when you have to let go",
    "the stage flips a switch",
    "all that bravado, then this",       # 'this' deictic but NOT a pronoun-subject opener
    "the coldest way to say goodbye",    # 'coldest' is not 'his -est' -> not the superlative template
    "from rock bottom, a lane opened",
    "been through the worst, came up anyway",
])
def test_cold_viewer_floors_do_not_false_positive(hook):
    # The two new floors are narrow on purpose: real, legible hooks (incl. my round-3 batch) must pass.
    assert is_weak_hook(hook) is False
