# tests/test_hookscore.py — the narration detector (Task 5) is the critic-INDEPENDENT viewer-POV
# METER + a SIGNAL to the critic. It NEVER gates (rejects nothing). High-precision on purpose: it
# flags only CLEAR third-person-pronoun recaps with no viewer address, accepting misses (the critic
# owns the nuanced calls). Fixtures are the operator's REAL failures + real viewer-POV wins.
import pytest
from fanops.hookscore import narration_signature, hook_quality
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState

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

# ---- Task 9: read-only hook scoreboard with a critic-INDEPENDENT viewer-POV meter ----

def _decided(led, sid, mid, hook, rounds=0, judged=False):
    led.add_moment(Moment(id=mid, parent_id=sid, state=MomentState.decided, start=0.0, end=18.0,
                          reason="r", hook=hook, hook_rounds=rounds, hook_judged=judged))

def test_hook_quality_counts(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    _decided(led, "s1", "m1", "you ever build something alone")   # viewer hook
    _decided(led, "s1", "m2", "the line you'll replay")           # viewer hook
    _decided(led, "s1", "m3", None)                               # clean clip -> null
    _decided(led, "s1", "m4", "you don't expect this", rounds=1)  # repaired (rounds>0, has a hook)
    q = hook_quality(led)
    assert q["decided"] == 4
    assert q["with_hook"] == 3
    assert q["null"] == 1
    assert q["repaired"] == 1
    assert q["viewer_pov_rate"] == 1.0          # all 3 shipped hooks address the viewer

def test_viewer_pov_rate_independent_of_critic(tmp_path):
    # A kept-but-narration hook LOWERS the rate even though the critic KEPT it (hook_judged True). The
    # meter is computed from narration_signature, NOT the critic's verdict — so a loosened/biased critic
    # cannot inflate it. This is the whole point of an independent scoreboard.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    _decided(led, "s1", "m1", "you ever build something alone", judged=True)   # viewer-POV, kept
    _decided(led, "s1", "m2", "he stopped answering for a reason", judged=True) # narration, but KEPT
    q = hook_quality(led)
    assert q["with_hook"] == 2
    assert q["viewer_pov_rate"] == 0.5          # 1 of 2 shipped hooks is narration -> 0.5, despite both kept

def test_hook_quality_pov_rate_when_no_hooks(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    _decided(led, "s1", "m1", None)
    q = hook_quality(led)
    assert q["with_hook"] == 0 and q["viewer_pov_rate"] == 1.0   # no hooks shipped -> vacuously full POV (no div/0)

def test_log_hook_quality_returns_digest_and_is_read_only(tmp_path):
    from fanops.hookscore import log_hook_quality
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    _decided(led, "s1", "m1", "you ever build something alone")
    q = log_hook_quality(led, cfg)
    assert q == hook_quality(led)        # delegates to the pure scoreboard; logging is the only side effect
