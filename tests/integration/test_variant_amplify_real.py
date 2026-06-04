"""Creative-variation v3 — variant-gated amplification closing END-TO-END ON DISK (the Integrate bar).

The unit tests prove the gate (`amplify_candidates`), the deterministic streak (`update_streaks`),
and that the actuator is amplify-only + fail-SAFE. This integration test proves the whole arrow lands
on real artifacts: a REAL ledger.json on disk where @a/instagram's "WIN" hook out-lifts a runner-up
over >= AMPLIFY_MIN_POSTS analyzed posts; `apply_variant_amplify` driven across enough DISTINCT
evidence windows (new analyzed posts between passes, each round-tripping the on-disk ledger) to
satisfy the streak with FANOPS_VARIANT_AMPLIFY=1; then the ACTUAL moment-request file read back from
`04_agent_io/requests/` — asserting the winning hook reached the amplify request AND the source state
flipped to moments_requested. That is the auto-propagate loop, closed, observed on disk. It also
asserts G2: the winning published/analyzed posts SURVIVE (v3 never deletes them)."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, SourceState
from fanops.variant_amplify import apply_variant_amplify
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _win(pid, hook, lift):
    return Post(id=pid, parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.analyzed, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})


def test_sustained_winner_amplifies_source_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    # Seed a REAL ledger on disk: a full lineage (source -> moment -> clip) + a clear surface winner:
    # @a/instagram's "WIN" (8 posts, mean 90) over a "LOSE" runner-up (3 posts, mean 1) — clears the
    # v2 floor (>=3, gap>=10) AND the v3 min_posts (8) and min_gap (25).
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
        led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=4.0, reason="r",
                              transcript_excerpt="ex"))
        led.add_clip(Clip(id="c1", parent_id="m1", path="c1.mp4"))
        for i in range(8):
            led.add_post(_win(str(i), "WIN", 95.0))
        for i in range(3):
            led.add_post(_win(f"l{i}", "LOSE", 1.0))

    # Drive >= min_streak windows, adding ONE new analyzed WIN post per window (distinct evidence) so
    # the streak genuinely accrues across multiple on-disk passes — never a single window.
    nid = 100
    for _ in range(cfg.variant_amplify_min_streak):
        with Ledger.transaction(cfg) as led:
            led = apply_variant_amplify(led, cfg)
        with Ledger.transaction(cfg) as led:
            led.add_post(_win(str(nid), "WIN", 95.0)); nid += 1

    # Final pass once the streak is satisfied -> amplify must fire.
    with Ledger.transaction(cfg) as led:
        led = apply_variant_amplify(led, cfg)

    led = Ledger.load(cfg)
    # The loop closed on disk: the source was amplified and the request carries the winning hook.
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WIN" in payload["guidance"]
    assert payload["guidance"].startswith("AMPLIFY:")           # the existing C1-fixed amplify path
    # G2 — the winning analyzed posts SURVIVE (v3 never deletes/retires real content).
    surviving = [p for p in led.posts.values()
                 if p.variant_hook == "WIN" and p.state is PostState.analyzed]
    assert surviving, "variant-amplify must never delete the winning posts (G2)"


def test_no_sustained_winner_never_amplifies_on_disk(tmp_path, monkeypatch):
    """Adversarial on-disk: a strong but SINGLE-window winner (one pass, no sustained streak) must
    NEVER amplify. Proves the streak gate holds against real on-disk evidence, not just in memory."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
        led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=4.0, reason="r",
                              transcript_excerpt="ex"))
        led.add_clip(Clip(id="c1", parent_id="m1", path="c1.mp4"))
        for i in range(20):
            led.add_post(_win(str(i), "WIN", 99.0))     # overwhelming, but ONE window
        for i in range(3):
            led.add_post(_win(f"l{i}", "LOSE", 1.0))

    # A SINGLE pass -> streak reaches only 1 -> must not amplify.
    with Ledger.transaction(cfg) as led:
        led = apply_variant_amplify(led, cfg)

    led = Ledger.load(cfg)
    assert led.sources["s1"].state is SourceState.transcribed   # NOT moments_requested
    assert not request_path(cfg, "moments", "s1").exists()      # no amplify request written
