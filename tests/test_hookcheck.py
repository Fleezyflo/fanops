# tests/test_hookcheck.py — the deterministic hook-quality guard. Fail-cases are the ACTUAL weak
# hooks the model produced in round 1 (regression-locked); pass-cases are the actual strong ones.
# High-precision by design: it rejects only clear slop patterns, leaving nuanced calls to the prompt
# (and a later LLM critic). A rejected hook becomes None -> a clean clip (clean beats slop).
import pytest
from fanops.hookcheck import is_weak_hook

@pytest.mark.parametrize("hook", ["", "   ", None])
def test_empty_hook_is_weak(hook):
    assert is_weak_hook(hook) is True

@pytest.mark.parametrize("hook", [
    "his hardest bar", "his coldest opener",        # superlative template — now the CRITIC's call, not a regex
    "watch how he cuts",                             # hooking on the editing
    "drone up, crowd in", "zoom in slow",            # shot description
    "the bar everyone replayed", "the intro hits different",   # cliche
])
def test_is_weak_hook_mechanical_only(hook):
    # v2: the SEMANTIC slop-regexes (_SUPERLATIVE/_EDITING/_SHOT_DESC/_CLICHES) are deleted — quality is
    # the reasoning critic's job. is_weak_hook is now a MECHANICAL floor only (empty / exact-dup /
    # opening-template cluster), so these formerly regex-rejected hooks PASS the floor (critic judges them).
    assert is_weak_hook(hook) is False

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
    # The "before he was Moh Flow x6" failure: distinct STRINGS that share an opening TEMPLATE read like
    # a bot. v2.1 tune: a cluster needs >=3 accepted hooks sharing the first THREE words before the next
    # is rejected (was 2 words / 2 hooks — too aggressive: it nuked good distinct hooks that merely shared
    # a 2-word opener). This is the no-LLM floor that closes feed-clustering for EVERY config.
    used = {"wait for the beat drop", "wait for the last line", "wait for the hometown bar"}  # 3 share "wait for the"
    assert is_weak_hook("wait for the final verse", used=used) is True   # the 4th on the same 3-word opener -> cluster

def test_shared_two_words_distinct_third_word_survives():
    # The real over-strip this tune fixes (forensic: 6/51 corpus hooks were blanked exactly here). 'you
    # ever X' hooks share only the first TWO words but diverge on the third — distinct, good hooks that the
    # old 2-token rule wrongly clustered to blank. They MUST pass the floor now.
    used = {"you ever felt lost and alive", "you ever just figured it out", "you ever know your moment"}
    assert is_weak_hook("you ever win and still lose", used=used) is False

def test_third_shared_opening_still_allowed():
    used = {"wait for the beat drop", "wait for the last line"}        # only two share "wait for the"
    assert is_weak_hook("wait for the hometown bar", used=used) is False  # the 3rd is fine; not yet a cluster (need >=3 priors)

def test_distinct_openings_are_not_clustered():
    used = {"wait for the beat drop", "wait for the last line"}        # a "wait for" cluster exists
    assert is_weak_hook("nobody clipped this part", used=used) is False  # a DIFFERENT opening is unaffected

def test_common_first_word_different_second_not_clustered():
    used = {"the bar nobody saw", "the last word lands"}               # share only "the", not 2 tokens
    assert is_weak_hook("the line he kept", used=used) is False        # second token differs -> not a cluster
