# tests/test_intro_retry_disambiguation.py — WS6 (audit c7-f3): the intro_tease retry cap counted EVERY
# not-warm commit pass as a failed attempt, conflating two very different causes: (a) a GENUINE compose
# failure (the lock-free prewarm ran prepend_intro for this pairing and it produced no composite — a flaky
# matcher pair / unrenderable intro asset), which SHOULD be bounded and parked; and (b) a TRANSIENT/structural
# miss (the prewarm hasn't produced the composite yet, or was skipped because the base wasn't a valid base
# this pass), which should keep WAITING. Conflating them parks a perfectly renderable pair as "unrenderable"
# after 3 transient misses. The fix: the prewarm writes a {cid}.introfail.json marker ONLY when it attempted
# the compose and it failed; the commit burns the cap only on a matching marker, and waits (no burn) otherwise.
import json
from pathlib import Path
from fanops.config import Config
from fanops.models import StitchState
import fanops.stitch_render as sr
from fanops.stitch_render import render_approved_stitches, _stitch_clip_id

# reuse the canonical seed helper from the sibling stitch test module
from test_stitch_render import _seed_intro_approved


def _intro_fail_marker(cfg, led):
    """Lay down the genuine-compose-failure marker the prewarm would write for iplan's stitch clip (fp-matched)."""
    from fanops.compose import _compose_fingerprint
    base = led.clips["clip_base"]; intro = led.sources["intro1"]
    cid = _stitch_clip_id("iplan", base.aspect.value)
    fp = _compose_fingerprint(base.path, intro.source_path, led.stitch_plans["iplan"].plan_params, 1920, 1080)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{cid}.introfail.json").write_text(json.dumps({"fp": fp}))
    return cid


def test_transient_prewarm_miss_does_not_burn_retry_cap(tmp_path):
    # No failure marker (the prewarm never attempted this pairing) -> a transient miss. Committing many times
    # must NOT burn the cap and must NEVER park the plan: it just waits for a future prewarm.
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    for _ in range(sr.MAX_INTRO_RENDER_ATTEMPTS + 3):
        render_approved_stitches(led, cfg)
    p = led.stitch_plans["iplan"]
    assert p.render_attempts == 0, "a transient miss burned the retry cap"
    assert p.state is StitchState.approved, "a renderable pair was parked as unrenderable on transient misses"


def test_genuine_compose_failure_burns_and_parks(tmp_path):
    # A genuine-failure marker present (prewarm attempted + failed) -> each commit burns one attempt; at the cap
    # the plan is PARKED with the compose-failed reason.
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    for i in range(sr.MAX_INTRO_RENDER_ATTEMPTS - 1):
        _intro_fail_marker(cfg, led)                       # the prewarm would re-write it each failing pass
        render_approved_stitches(led, cfg)
        assert led.stitch_plans["iplan"].state is StitchState.approved
        assert led.stitch_plans["iplan"].render_attempts == i + 1
    _intro_fail_marker(cfg, led)
    render_approved_stitches(led, cfg)                     # the capping pass
    p = led.stitch_plans["iplan"]
    assert p.state is StitchState.error and "compose failed after" in (p.error_reason or "")


def test_prewarm_writes_failure_marker_on_compose_fail(tmp_path, mocker):
    # The prewarm records the genuine-failure marker when prepend_intro returns False (no composite produced),
    # which is exactly what lets the commit tell a real failure from a not-yet-warmed pairing.
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    mocker.patch("fanops.compose.prepend_intro", return_value=False)   # attempted, produced nothing
    sr.prewarm_approved_stitches(led, cfg, lambda *a, **k: None)
    cid = _stitch_clip_id("iplan", "9:16")
    assert (cfg.clips / f"{cid}.introfail.json").exists(), "prewarm did not record the genuine-failure marker"


def test_successful_prewarm_clears_a_stale_failure_marker(tmp_path, mocker):
    # A prior genuine failure left a marker; a later successful prewarm must CLEAR it so the now-warm pairing
    # adopts instead of being treated as still-failing.
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    cid = _intro_fail_marker(cfg, led)                     # stale failure from an earlier pass
    def fake_prepend(b, i, o, *, tease_text, intro_seconds, **kw):
        Path(o).parent.mkdir(parents=True, exist_ok=True); Path(o).write_bytes(b"COMPOSED"); return True
    mocker.patch("fanops.compose.prepend_intro", side_effect=fake_prepend)
    sr.prewarm_approved_stitches(led, cfg, lambda *a, **k: None)
    assert not (cfg.clips / f"{cid}.introfail.json").exists(), "success did not clear the stale failure marker"
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.in_use
