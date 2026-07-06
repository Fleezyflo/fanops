# tests/test_account_first_e2e.py — Account-First Studio: the ONE cross-face end-to-end test (E1).
# Walks a named, account-targeted ingest batch all the way to a queued, strictly-future post for ONLY the
# targeted account. Slow UNIT (`@pytest.mark.slow` — CI `unit` still runs it via `-m "not integration"`),
# fully deterministic: time injected at every seam, ffmpeg faked, dryrun-FORCED, stops at `queued` (never publishes).
#   create_batch -> batch-stamped Source -> moment/captioned clip -> crosspost (batch-target skip, casting
#   OFF) -> posts born ONLY for the targeted account with Post.batch_id denormalized + awaiting_approval
#   -> approve_posts -> strictly-future queued. No single-face test exercises this join:
import json, subprocess
import pytest
pytestmark = pytest.mark.slow
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, BatchState, PostState, ClipState, MomentState, Fmt
from fanops.accounts import Accounts
from fanops.batches import create_batch
from fanops.crosspost import crosspost_clips
from fanops.studio.actions import approve_posts
from fanops.timeutil import parse_iso

FIXED = "2026-06-21T00:00:00.000001Z"
FIXED_DT = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _seed_accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "ib", "platforms": ["instagram"], "status": "active"}]}))


def _fake_ffmpeg(mocker):
    # mirror tests/test_crosspost.py:30-41 — fake ONLY ffmpeg render commands, pass any other spawn through.
    real_run = subprocess.run
    def fake_run(cmd, **kw):
        if not (isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg"):
            return real_run(cmd, **kw)
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)


def test_account_first_batch_to_queued_only_targeted_account(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")            # belt-and-braces over the autouse strip; never live
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")        # casting now DEFAULTS ON; pin OFF to isolate the batch-target path
    cfg = Config(root=tmp_path); _seed_accounts(cfg); _fake_ffmpeg(mocker)
    assert cfg.account_casting is False                      # casting explicitly OFF: batch-targeting is what's under test here

    # 1) a named batch targeting ONLY @a (the lever: this ingest is for @a, not "everything for everything")
    led = Ledger.load(cfg)
    b = create_batch(led, name="Launch", target_accounts=["a"], now_iso=FIXED)
    assert led.get_batch(b.id) is b and b.target_accounts == ["a"] and b.state is BatchState.open

    # 2) batch-stamped source -> moment -> captioned clip (BOTH @a and @b captions, so @b is a REAL surface)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, batch_id=b.id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"a/instagram": {"caption": "a", "hashtags": []},
                          "b/instagram": {"caption": "b", "hashtags": []}}
    led.add_clip(clip); led.save()

    # 3) crosspost (casting OFF) -> posts born ONLY for @a; @b's surface is batch-target-SKIPPED (enforced, not a no-op)
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time=FIXED)
    led.save()                                                     # persist so approve_posts (own txn) sees the born posts
    assert {p.account for p in led.posts.values()} == {"a"}        # @b dropped by the batch target
    assert led.posts, "at least one post born for @a"
    for p in led.posts.values():
        assert p.batch_id == b.id                                  # denormalized through crosspost (carried by repost_post too)
        assert p.state is PostState.awaiting_approval              # the approval gate: NOTHING auto-publishes
        assert led.moments["mom_1"].hook in (None, "")               # P9: no owner-moment hook -> shared clip path at approve
    assert led.moments["mom_1"].affinities in (None, [], {})       # casting did NOT run (Moment.affinities default [])

    # 4) operator approves -> queued AND strictly-future (suggest_time landed a future slot, never "now")
    a_pid = next(iter(led.posts))
    assert approve_posts(cfg, [a_pid], now=FIXED_DT).ok is True
    ap = Ledger.load(cfg).posts[a_pid]                            # reload: approve_posts commits its own transaction
    assert ap.state is PostState.queued and parse_iso(ap.scheduled_time) > FIXED_DT
