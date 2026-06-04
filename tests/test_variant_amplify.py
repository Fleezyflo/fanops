# tests/test_variant_amplify.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, Moment, Clip, SourceState
from fanops.variant_amplify import update_streaks


def _post(pid, acct, hook, lift, state=PostState.analyzed):
    return Post(id=pid, parent_id="c1", account=acct, account_id="1", platform=Platform.instagram,
                caption="x", state=state, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})


def _led(cfg, posts):
    led = Ledger.load(cfg)
    for p in posts:
        led.add_post(p)
    return led


def _winset(n, hook, lift, start=1):
    # n analyzed posts of `hook` at `lift` + a runner-up far below so best_hooks fires.
    posts = [_post(str(start + i), "@a", hook, lift) for i in range(n)]
    posts += [_post(str(start + n + i), "@a", "LOSE", 1.0) for i in range(3)]
    return posts


def _seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))


# ---- Task 4: update_streaks — deterministic, idempotent streak tracker ----------------------------

def test_first_sighting_sets_streak_one(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_POSTS default 8
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN" and e["streak"] == 1


def test_same_winner_new_evidence_increments(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    led.add_post(_post("99", "@a", "WIN", 90.0))   # NEW analyzed evidence (new post id)
    update_streaks(led, cfg)               # streak 2
    assert led.variant_streaks["@a|instagram"]["streak"] == 2


def test_same_evidence_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    snap = dict(led.variant_streaks["@a|instagram"])
    update_streaks(led, cfg)               # SAME evidence -> no change
    update_streaks(led, cfg)               # and again
    assert led.variant_streaks["@a|instagram"] == snap


def test_winner_change_resets_to_one(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # WIN streak 1
    # Now make a DIFFERENT hook the clear leader: add 9 NEW posts of "WIN2" at a lift that beats the
    # WIN runner-up (mean 90) by >= variant_min_gap (10) — else best_hooks would see no comparative
    # winner and return []. 110 mean -> WIN2 leads WIN by 20 (clears the floor gate).
    for i in range(9):
        led.add_post(_post(f"2{i}", "@a", "WIN2", 110.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN2" and e["streak"] == 1     # reset, not continued


def test_winner_disappears_resets_to_zero(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    # Drop below the floor: make the gap tiny so best_hooks now returns [] (raise the losers).
    for p in led.posts.values():
        if p.variant_hook == "LOSE":
            p.metrics["lift_score"] = 89.0
    update_streaks(led, cfg)
    assert led.variant_streaks["@a|instagram"]["streak"] == 0


def test_update_streaks_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    a = dict(led.variant_streaks["@a|instagram"])
    led2 = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led2, cfg)
    b = dict(led2.variant_streaks["@a|instagram"])
    assert a == b                          # same ledger state -> identical streak entry
