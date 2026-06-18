# tests/test_hookscore.py — the narration detector (Task 5) is the critic-INDEPENDENT viewer-POV
# METER + a SIGNAL to the critic. It NEVER gates (rejects nothing). High-precision on purpose: it
# flags only CLEAR third-person-pronoun recaps with no viewer address, accepting misses (the critic
# owns the nuanced calls). Fixtures are the operator's REAL failures + real viewer-POV wins.
import pytest
from fanops.hookscore import narration_signature

# Real third-person scene-narration the engine actually shipped (the regression) -> flagged.
@pytest.mark.parametrize("hook", [
    "he stopped answering for a reason",
    "started in a bedroom copying his brother",
    "the only one who stops him",
    "the promise he made himself",
    "she ran a minute he made it",
])
def test_narration_signature_flags_third_person_recaps(hook):
    assert narration_signature(hook) is True

# Real viewer-POV hooks (operator golds + craft priors) -> NOT flagged.
@pytest.mark.parametrize("hook", [
    "maybe your favorite artist copied too",
    "you ever heard a song and you just felt that shit?",
    "the line you'll send to one person",
    "this one's for who you can't get over",
    "you don't expect a rapper to make you pray",
    "wait for what he admits",          # 'he' present BUT opens with the imperative 'wait' -> addresses viewer
    "the part you'll replay",
])
def test_narration_signature_passes_viewer_hooks(hook):
    assert narration_signature(hook) is False

@pytest.mark.parametrize("hook", ["", "   ", None])
def test_narration_signature_empty_is_false(hook):
    assert narration_signature(hook) is False   # nothing to flag (not a recap)
