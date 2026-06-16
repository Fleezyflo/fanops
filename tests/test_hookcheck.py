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
