# tests/fixtures/variant_lineage.py — shared variant-learning ledger fixtures
"""Reusable lineage/validation helpers for variant_learning, variant_amplify,
variant_transfer, p4_dim_bias, and culmination tests."""

from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, Moment, Clip, SourceState


def validate_learning(cfg):
    """Mark the live-validation precondition (metrics_confirmed) so learning actuators may run."""
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})


def seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))


def variant_lineage(pid, acct, hook, lift, state=PostState.analyzed, *, src_id="s1"):
    # P9: hook lives on the owner moment; each analyzed post needs its own moment/clip lineage.
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    clip_id, moment_id = f"c_{pid}", f"m_{pid}"
    url = f"dryrun://{pid}" if state in _POST_TERMINAL_REQUIRES_URL else None
    moment = Moment(id=moment_id, parent_id=src_id, start=0.0, end=4.0, reason="r", hook=hook,
                    transcript_excerpt="ex")
    clip = Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4")
    post = Post(id=pid, parent_id=clip_id, account=acct, account_id="1", platform=Platform.instagram,
                caption="x", state=state, metrics={"lift_score": lift}, public_url=url)
    return moment, clip, post


def variant_post(pid, acct, hook, lift, state=PostState.analyzed, *, src_id="s1"):
    return variant_lineage(pid, acct, hook, lift, state, src_id=src_id)


def add_variant_lineage(led, triple, *, src_id="s1"):
    m, c, p = triple
    if not led.sources.get(src_id):
        led.add_source(Source(id=src_id, source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
    led.add_moment(m); led.add_clip(c); led.add_post(p)


def ledger_with_triples(cfg, triples, *, src_id="s1"):
    led = Ledger.load(cfg)
    for t in triples:
        add_variant_lineage(led, t, src_id=src_id)
    return led


def winset(n, hook, lift, start=1):
    # n analyzed posts of `hook` at `lift` + a runner-up far below so best_hooks fires.
    posts = [variant_post(str(start + i), "a", hook, lift) for i in range(n)]
    posts += [variant_post(str(start + n + i), "a", "LOSE", 1.0) for i in range(3)]
    return posts
