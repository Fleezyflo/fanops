# tests/test_m_series_gates.py — R4: the M-series time-gate contract. A single `is_due_or_past`
# helper in timeutil owns the "ready to fire on the next tick" gate (publish_due / go_live `<=`
# semantics); `_seconds_away` owns the imminent-fire band. `reschedule_bucket` MUST leave every
# queued post strictly-future when it returns — an unparseable scheduled_time is sent back to
# Review (R3 audit trail), never left in the bucket as a silent landmine the next `publish_due`
# would fire on.
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Source, Moment, MomentState, Fmt
from fanops.studio import actions
from fanops.timeutil import iso_z

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _z(dt): return iso_z(dt)


def _seed_one(cfg, *, pid, state=PostState.queued, when):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "shared", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_integ_1"}}]}))
    with Ledger.transaction(cfg) as led:
        # Idempotent seed: the parent chain is created once; this helper can be called many times.
        if "src_1" not in led.sources:
            led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
            led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
            led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4",
                              aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id="clip_1", account="a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="x", state=state,
                          scheduled_time=when, public_url=f"dryrun://{pid}"))


# ---- A: the helper itself ----
def test_is_due_or_past_contract():
    """A1 (converted to the live helper): `is_due_or_past` mirrors publish_due's `<=` gate — past
    and exactly-now both fire; future does not. None -> False (no time is never 'due'); an
    UNPARSEABLE time -> True (treat-as-stale — the safe direction for the go_live readiness check:
    a torn time blocks the flip, never silently passes). Never raises."""
    from fanops.timeutil import is_due_or_past
    assert is_due_or_past(_z(_NOW - timedelta(minutes=1)), _NOW) is True
    assert is_due_or_past(_z(_NOW + timedelta(minutes=1)), _NOW) is False
    assert is_due_or_past(_z(_NOW), _NOW) is True                                 # equal IS due (`<=`)
    assert is_due_or_past(None, _NOW) is False
    assert is_due_or_past("garbage", _NOW) is True                                # torn time blocks the flip


# ---- B: regression pins — reschedule_bucket leaves no past-due / no garbage ----
# These pass on the unfixed code TODAY (the spreader incidentally overwrites both) — they're pinned
# so the upcoming consolidation refactor at golive.py / views.py / post/run.py does not regress the
# invariant. The test_m_series_gates.py file as a whole exists to make the contract explicit.
def test_reschedule_bucket_result_is_strictly_future(tmp_path):
    """B1: after reschedule_bucket returns OK, every queued post it moved has a scheduled_time
    strictly > now. Holds today by virtue of suggest_times_for_batch; pinned so the helper
    consolidation doesn't break it."""
    cfg = Config(root=tmp_path)
    _seed_one(cfg, pid="late_1", when=_z(_NOW - timedelta(hours=2)))            # past-due
    _seed_one(cfg, pid="late_2", when=_z(_NOW - timedelta(minutes=10)))         # past-due
    _seed_one(cfg, pid="far",    when=_z(_NOW + timedelta(hours=9)))            # already future
    r = actions.reschedule_bucket(cfg, now=_NOW)
    assert r.ok, f"reschedule failed: {r.error}"
    led = Ledger.load(cfg)
    for p in led.posts.values():
        if p.state is PostState.queued and p.scheduled_time:
            from fanops.timeutil import parse_iso
            dt = parse_iso(p.scheduled_time)
            assert dt > _NOW, f"post {p.id} respread to {dt!r}, not > {_NOW!r}"


def test_reschedule_bucket_replaces_unparseable_time(tmp_path):
    """B2: a post that somehow lands in the bucket with an unparseable scheduled_time is overwritten
    with a fresh strictly-future time by reschedule_bucket. Pinned regression — today this works
    incidentally via suggest_times_for_batch; the consolidation must not regress it."""
    cfg = Config(root=tmp_path)
    _seed_one(cfg, pid="ok",  when=_z(_NOW + timedelta(hours=3)))
    _seed_one(cfg, pid="bad", when=_z(_NOW + timedelta(hours=4)))
    with Ledger.transaction(cfg) as led:
        led.posts["bad"].scheduled_time = "garbage"                              # smuggled past Pydantic
    actions.reschedule_bucket(cfg, now=_NOW)
    led = Ledger.load(cfg)
    bad = led.posts["bad"]
    assert bad.scheduled_time != "garbage", "an unparseable time must not survive a respread"
    from fanops.timeutil import parse_iso
    assert parse_iso(bad.scheduled_time) > _NOW


# ---- C: the consolidation — every site reads the same shared time-gate helper ----
def test_golive_past_due_gate_uses_shared_helper():
    """C1: the M6 go_live past-due gate reads the shared timeutil helper (`is_due_or_past`) — the
    consolidation contract: there is ONE definition of 'ready to fire', never an open-coded
    parse_iso(scheduled_time) <= now at the call site."""
    import fanops.studio.golive as gl_mod
    src = pathlib.Path(gl_mod.__file__).read_text()
    assert "is_due_or_past" in src, (
        "golive.py must use the shared timeutil helper, not an open-coded "
        "parse_iso(scheduled_time) <= now")


# Path import for the C1 source-grep
import pathlib
