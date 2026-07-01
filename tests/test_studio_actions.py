# tests/test_studio_actions.py — CREATE
import dataclasses
import pytest
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.actions import reschedule_post, edit_caption, snooze_clip, release_held_clip, ActionResult


# ---- M4.1: ActionResult is frozen (no accidental post-construction mutation) + ergonomic factories ----
def test_action_result_is_frozen():
    r = ActionResult(ok=True, detail={"x": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = False                                     # frozen: a result can't be mutated after construction

def test_action_result_success_factory():
    r = ActionResult.success({"sources": 2})
    assert r.ok is True and r.error is None and r.detail == {"sources": 2}
    assert ActionResult.success().detail is None         # detail optional

def test_action_result_failure_factory():
    r = ActionResult.failure("nope")
    assert r.ok is False and r.error == "nope" and r.detail is None

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    return led

def test_reschedule_persists_tz_aware_z(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == _z(NOW + timedelta(hours=8))

def test_reschedule_naive_input_never_persists_naive(tmp_path):
    # spec §9 fix #5: a naive time would later mark the post failed in publish_due. Must be coerced
    # to tz-aware UTC Z before it touches the ledger.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "2026-06-06T20:00:00", now=NOW)   # NAIVE (no Z/offset)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == "2026-06-06T20:00:00Z"   # coerced to UTC Z

def test_reschedule_garbage_time_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "not-a-time", now=NOW)
    assert res.ok is False and res.error
    assert Ledger.load(cfg).posts["p_edit"].scheduled_time == _z(NOW + timedelta(hours=3))  # unchanged

def test_reschedule_unknown_post_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "nope", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "no such post" in res.error.lower()

def test_reschedule_non_queued_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].state = PostState.published; led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "queued" in res.error.lower()

def test_reschedule_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW + timedelta(minutes=1)); led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "imminent" in res.error.lower()

# ---- P1: clear_time (atomic unapprove-then-clear for queued) ----
def _seed_awaiting(cfg):
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_aw", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    return led

def test_clear_time_sets_none_on_awaiting_post(tmp_path):
    from fanops.studio.actions import clear_time
    cfg = Config(root=tmp_path); _seed_awaiting(cfg)
    res = clear_time(cfg, "p_aw", now=NOW)
    assert res.ok is True
    p = Ledger.load(cfg).posts["p_aw"]
    assert p.scheduled_time is None and p.state is PostState.awaiting_approval   # awaiting: just clear

def test_clear_time_on_queued_unapproves_and_clears(tmp_path):
    from fanops.studio.actions import clear_time
    cfg = Config(root=tmp_path); _seed(cfg)            # p_edit is queued, future
    res = clear_time(cfg, "p_edit", now=NOW)
    assert res.ok is True
    p = Ledger.load(cfg).posts["p_edit"]
    assert p.state is PostState.awaiting_approval and p.scheduled_time is None   # back to review AND timeless

def test_clear_time_rejects_imminent_queued(tmp_path):
    from fanops.studio.actions import clear_time
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW + timedelta(minutes=1)); led.save()   # imminent
    res = clear_time(cfg, "p_edit", now=NOW)
    assert res.ok is False and "imminent" in res.error.lower()
    assert Ledger.load(cfg).posts["p_edit"].state is PostState.queued    # untouched — still about to ship

def test_clear_time_unknown_post(tmp_path):
    from fanops.studio.actions import clear_time
    cfg = Config(root=tmp_path)
    res = clear_time(cfg, "ghost", now=NOW)
    assert res.ok is False and "no such post" in res.error.lower()

def test_clear_time_never_leaves_queued_with_none(tmp_path):
    # INVARIANT: after any successful clear_time, no post is simultaneously queued AND timeless
    # (that combination = silent publish-now in publish_due).
    from fanops.studio.actions import clear_time
    cfg = Config(root=tmp_path); _seed(cfg)
    assert clear_time(cfg, "p_edit", now=NOW).ok
    for p in Ledger.load(cfg).posts.values():
        assert not (p.state is PostState.queued and p.scheduled_time is None)


def test_edit_caption_persists(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = edit_caption(cfg, "p_edit", "BRAND NEW CAPTION", now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).posts["p_edit"].caption == "BRAND NEW CAPTION"

def test_edit_caption_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW - timedelta(minutes=1)); led.save()  # already due
    res = edit_caption(cfg, "p_edit", "TOO LATE", now=NOW)
    assert res.ok is False
    assert Ledger.load(cfg).posts["p_edit"].caption == "OLD"

def test_snooze_pushes_all_clip_posts_far_out(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.add_post(Post(id="p2", parent_id="clip_1", account="@b", account_id="2",
                      platform=Platform.youtube, caption="y", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=4))))
    # one imminent post on the same clip should be left alone
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@c", account_id="3",
                      platform=Platform.tiktok, caption="t", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.save()
    res = snooze_clip(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["count"] == 2   # p_edit + p2 (not p_imm)
    out = Ledger.load(cfg)
    from fanops.timeutil import parse_iso
    assert parse_iso(out.posts["p_edit"].scheduled_time) >= NOW + timedelta(days=364)
    assert parse_iso(out.posts["p2"].scheduled_time) >= NOW + timedelta(days=364)
    assert out.posts["p_imm"].scheduled_time == _z(NOW + timedelta(minutes=2))   # untouched

# ---- M5.1: release a brand-risk-held clip back into the caption gate (UI twin of `fanops unhold`) ----
def _seed_held(cfg):
    _seed(cfg)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk: slur"))
    led.save()

def test_release_held_clip_clears_hold(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg)
    res = release_held_clip(cfg, "clip_held")
    assert res.ok is True and res.detail["state"] == "captions_requested"
    c = Ledger.load(cfg).clips["clip_held"]
    assert c.held is False and c.held_reason is None and c.state is ClipState.captions_requested

def test_release_unknown_clip_fails(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg)
    res = release_held_clip(cfg, "nope")
    assert res.ok is False and "no such clip" in res.error

def test_release_non_held_clip_rejected_state_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg)         # clip_1 is queued, not held
    res = release_held_clip(cfg, "clip_1")
    assert res.ok is False and "not held" in res.error
    assert Ledger.load(cfg).clips["clip_1"].state is ClipState.queued   # a stray click never churns a live clip

def test_release_held_clip_preserves_existing_posts(tmp_path):
    # a clip held AFTER posts were generated: release re-runs the caption gate but does NOT purge the
    # existing posts (matches the CLI unhold) — they survive and re-surface in the editable bucket.
    cfg = Config(root=tmp_path); _seed_held(cfg)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_held", parent_id="clip_held", account="@a", account_id="1",
                      platform=Platform.instagram, caption="C", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=5))))
    led.save()
    res = release_held_clip(cfg, "clip_held")
    assert res.ok is True
    out = Ledger.load(cfg)
    assert out.clips["clip_held"].state is ClipState.captions_requested
    assert out.posts["p_held"].state is PostState.queued and out.posts["p_held"].parent_id == "clip_held"

def test_actions_use_single_transaction(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    spy = mocker.spy(Ledger, "transaction")
    reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert spy.call_count == 1   # exactly one lock acquisition per mutation (no lock-free load+save)


# ---- FIX 2: publish_now must not let a NON-auth exception from publish_post escape as a Flask 500 ----
def test_publish_now_non_auth_error_yields_ok_false_not_raise(tmp_path, monkeypatch, mocker):
    from fanops.studio.actions import publish_now
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg)
    # publish_post raises a NON-auth error (e.g. media upload RuntimeError / corrupt clip.path)
    mocker.patch("fanops.post.run.publish_post", side_effect=RuntimeError("media upload boom"))
    res = publish_now(cfg, "p_edit")
    assert res.ok is False                                    # surfaced cleanly, not a raise (500)
    assert "publish failed" in (res.error or "")
    assert "boom" in (res.error or "")


# ---- content-lifecycle Phase 4: cross-account reuse (crosspost_to_account / crosspost_all_to_account) ----
def _seed_xacct(cfg, *, accounts=None, ig_caption=True, window=(0.0, 7.0), aspects=(Fmt.r9x16,)):
    # source + moment + clip(s); accounts.json with the given accounts; an optional IG caption on the clip.
    import json
    accounts = accounts or [{"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                              start=window[0], end=window[1], reason="r", state=MomentState.clipped))
        for i, asp in enumerate(aspects):
            cpath = cfg.clips / f"c{i}.mp4"; cpath.write_bytes(b"\x00")   # real render file — #10 guard checks existence
            clip = Clip(id=f"clip_{i}", parent_id="mom_1", path=str(cpath), aspect=asp, state=ClipState.queued)
            if ig_caption and asp is Fmt.r9x16:
                clip.meta_captions = {"@b/instagram": {"caption": "reuse me", "hashtags": ["#x"]}}
            led.add_clip(clip)

def test_crosspost_to_account_mints_awaiting(tmp_path):
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg)
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok and r.detail["already_exists"] is False
    led = Ledger.load(cfg)
    p = led.posts[r.detail["post_id"]]
    assert p.state is PostState.awaiting_approval and p.scheduled_time is None
    assert p.account == "@b" and p.account_id == "ig_b" and p.created_at
    assert led.clips["clip_0"].state is ClipState.queued          # clip state UNCHANGED (no pipeline re-open)

def test_crosspost_to_account_inherits_clip_batch_lineage(tmp_path):
    # AUDIT M2: a cross-account reuse post must inherit its clip's ingest-batch lineage (Source.batch_id), so
    # it groups + approves with its batched siblings. Born batch_id=None it showed in the ?batch= drill-in (the
    # Review card derives bid from a batched sibling) but approve_account(batch=Y) silently SKIPPED it. The
    # batch targets @a (NOT @b) — reuse fans freely; the post still belongs to the clip's source-batch lineage.
    from fanops.studio.actions import crosspost_to_account, approve_account
    from fanops.models import Batch
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", batch_id="batch_y"))
        led.add_batch(Batch(id="batch_y", name="launch", target_accounts=["@a"]))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped))
        cpath = cfg.clips / "c0.mp4"; cpath.write_bytes(b"\x00")
        led.add_clip(Clip(id="clip_0", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16, state=ClipState.queued))
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok
    p = Ledger.load(cfg).posts[r.detail["post_id"]]
    assert p.batch_id == "batch_y"                               # inherits the clip's source-batch lineage
    res = approve_account(cfg, "@b", batch="batch_y", now=NOW)   # the batch-scoped approve now clears it...
    assert res.detail["approved"] == 1                           # ...instead of silently under-approving
    assert Ledger.load(cfg).posts[r.detail["post_id"]].state is PostState.queued

def test_approve_moment_approves_all_channels_and_clips_of_one_moment(tmp_path):
    # Matrix 'approve row': one moment may span multiple clips (aspects) and multiple (handle×platform) channels.
    # approve_moment promotes EVERY awaiting post under that moment — and ONLY that moment — to queued.
    from fanops.studio.actions import approve_moment
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/s.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_moment(Moment(id="m2", parent_id="src1", content_token="10-20", start=10, end=20, reason="r2", state=MomentState.clipped))
        led.add_clip(Clip(id="c1a", parent_id="m1", path="/c1a.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_clip(Clip(id="c1b", parent_id="m1", path="/c1b.mp4", aspect=Fmt.r9x16, state=ClipState.queued))  # 2nd clip, same moment
        led.add_clip(Clip(id="c2", parent_id="m2", path="/c2.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p_a_ig", parent_id="c1a", account="@a", account_id="1", platform=Platform.instagram, caption="A", state=PostState.awaiting_approval, public_url="dryrun://p_a_ig"))
        led.add_post(Post(id="p_b_ig", parent_id="c1a", account="@b", account_id="2", platform=Platform.instagram, caption="B", state=PostState.awaiting_approval, public_url="dryrun://p_b_ig"))
        led.add_post(Post(id="p_b_tt", parent_id="c1b", account="@b", account_id="2", platform=Platform.tiktok, caption="Bt", state=PostState.awaiting_approval, public_url="dryrun://p_b_tt"))  # 2nd clip of m1
        led.add_post(Post(id="p_a_done", parent_id="c1a", account="@a", account_id="1", platform=Platform.instagram, caption="X", state=PostState.queued, public_url="dryrun://p_a_done"))  # already approved → not re-counted
        led.add_post(Post(id="p_m2", parent_id="c2", account="@a", account_id="1", platform=Platform.instagram, caption="M2", state=PostState.awaiting_approval, public_url="dryrun://p_m2"))  # other moment
    res = approve_moment(cfg, "m1", now=NOW)
    assert res.ok and res.detail["approved"] == 3 and res.detail["moment"] == "m1"
    led = Ledger.load(cfg)
    assert all(led.posts[pid].state is PostState.queued for pid in ("p_a_ig", "p_b_ig", "p_b_tt"))
    assert led.posts["p_m2"].state is PostState.awaiting_approval   # a DIFFERENT moment is never touched (source-implicit scope)

def test_approve_account_platform_scopes_to_one_channel(tmp_path):
    # Matrix 'approve column': a column is a (handle × platform) CHANNEL. approve_account(platform=...) must
    # clear ONLY that channel — @b's IG posts — and leave @b's TikTok column awaiting (else one click clears two columns).
    from fanops.studio.actions import approve_account
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/s.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p_ig", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram, caption="ig", state=PostState.awaiting_approval, public_url="dryrun://p_ig"))
        led.add_post(Post(id="p_tt", parent_id="c1", account="@b", account_id="2", platform=Platform.tiktok, caption="tt", state=PostState.awaiting_approval, public_url="dryrun://p_tt"))
    res = approve_account(cfg, "@b", platform="instagram", source="src1", now=NOW)
    assert res.ok and res.detail["approved"] == 1 and res.detail["platform"] == "instagram"
    led = Ledger.load(cfg)
    assert led.posts["p_ig"].state is PostState.queued                  # the IG channel cleared...
    assert led.posts["p_tt"].state is PostState.awaiting_approval       # ...the TikTok channel untouched

def test_approve_account_no_platform_is_byte_identical(tmp_path):
    # platform=None (the default) keeps the legacy whole-account behavior — both channels approve.
    from fanops.studio.actions import approve_account
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/s.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p_ig", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram, caption="ig", state=PostState.awaiting_approval, public_url="dryrun://p_ig"))
        led.add_post(Post(id="p_tt", parent_id="c1", account="@b", account_id="2", platform=Platform.tiktok, caption="tt", state=PostState.awaiting_approval, public_url="dryrun://p_tt"))
    res = approve_account(cfg, "@b", source="src1", now=NOW)
    assert res.ok and res.detail["approved"] == 2

def test_approve_moment_unknown_is_clean_noop(tmp_path):
    # An unknown/dangling moment id matches no clip → no post → idempotent no-op, never a 500.
    from fanops.studio.actions import approve_moment
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/s.mp4"))
    res = approve_moment(cfg, "nope", now=NOW)
    assert res.ok and res.detail["approved"] == 0

def test_crosspost_to_account_no_collision_and_already_exists(tmp_path):
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path)
    _seed_xacct(cfg, accounts=[
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"},
        {"handle": "@c", "account_id": "ig_c", "platforms": ["instagram"], "status": "active"}])
    id_b = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW).detail["post_id"]
    id_c = crosspost_to_account(cfg, "clip_0", "@c", "instagram", now=NOW).detail["post_id"]
    assert id_b != id_c                                          # distinct surface -> distinct content-addressed id
    n_before = len(Ledger.load(cfg).posts)
    r2 = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)   # re-mint to B
    assert r2.ok and r2.detail["already_exists"] is True and r2.detail["post_id"] == id_b
    assert len(Ledger.load(cfg).posts) == n_before              # no duplicate (setdefault + honest report)

def test_crosspost_to_account_repost_freely(tmp_path):
    # a clip already posted to @a can ALSO post to @b — no "already posted to N accounts" guard, no supersede.
    from fanops.studio.actions import crosspost_to_account
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        cfg.clips.mkdir(parents=True, exist_ok=True)
        cpath = cfg.clips / "c.mp4"; cpath.write_bytes(b"\x00")          # real render file — #10 guard checks existence
        c = Clip(id="clip_0", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16, state=ClipState.queued)
        c.meta_captions = {"@b/instagram": {"caption": "x", "hashtags": []}}
        led.add_clip(c)
        led.add_post(Post(id="p_a", parent_id="clip_0", account="@a", account_id="ig_a",
                          platform=Platform.instagram, caption="on A", state=PostState.published, public_url="dryrun://p_a"))
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok and r.detail["already_exists"] is False         # the A post does NOT block fanning to B
    assert Ledger.load(cfg).posts["p_a"].state is PostState.published   # A untouched (no supersede)

def test_crosspost_to_account_caption_fallback(tmp_path):
    # no per-surface caption -> EMPTY caption + empty hashtags (operator edits in Review), NOT a skip.
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg, ig_caption=False)
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok and r.detail["already_exists"] is False
    p = Ledger.load(cfg).posts[r.detail["post_id"]]
    assert p.caption == "" and p.hashtags == []                 # minted with an empty caption, not dropped

def test_crosspost_to_account_rejects_over_cap(tmp_path):
    # a clip whose moment window exceeds the platform cap (IG 90s) -> clean ok=False, no post minted.
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg, window=(0.0, 120.0))   # 120s > IG 90s
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert not r.ok and "exceeds" in (r.error or "")
    assert not Ledger.load(cfg).posts                           # nothing minted

def test_crosspost_to_account_aspect_correct(tmp_path):
    # cross-post to a 16:9 surface (twitter) when both a 9:16 and a 16:9 render exist -> the minted post
    # binds the 16:9 clip + aspect (via _clip_for_aspect), NOT the 9:16 input. No render needed (16:9
    # reusable). (youtube is now 9:16 Shorts; twitter is the surviving 16:9 surface for this case.)
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path)
    _seed_xacct(cfg, accounts=[{"handle": "@b", "account_id": "tw_b", "platforms": ["twitter"], "status": "active"}],
                ig_caption=False, window=(0.0, 30.0), aspects=(Fmt.r9x16, Fmt.r16x9))
    r = crosspost_to_account(cfg, "clip_0", "@b", "twitter", now=NOW)   # clip_0 is the 9:16; twitter wants 16:9
    assert r.ok
    p = Ledger.load(cfg).posts[r.detail["post_id"]]
    assert p.aspect is Fmt.r16x9 and p.parent_id == "clip_1"    # bound the 16:9 render, not the 9:16 input

def test_crosspost_to_account_rejects_unknown_surface_platform_and_held(tmp_path):
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg)
    # unknown platform string
    assert not crosspost_to_account(cfg, "clip_0", "@b", "myspace", now=NOW).ok
    # unknown (account, platform) surface
    r = crosspost_to_account(cfg, "clip_0", "@nope", "instagram", now=NOW)
    assert not r.ok and "no active surface" in (r.error or "")
    # missing clip
    assert not crosspost_to_account(cfg, "clip_missing", "@b", "instagram", now=NOW).ok
    # held clip
    with Ledger.transaction(cfg) as led:
        led.clips["clip_0"] = led.clips["clip_0"].model_copy(update={"held": True})
    assert not crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW).ok

def test_crosspost_all_to_account_bulk(tmp_path):
    # three clips posted to A -> three minted on B; a re-run reports already_exists; empty source -> failure.
    from fanops.studio.actions import crosspost_all_to_account
    import json
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4"))
        for i in range(3):                                       # 3 distinct moments -> 3 distinct target clips
            led.add_moment(Moment(id=f"mom_{i}", parent_id="src_1", content_token=f"{i}", start=i, end=i + 5, reason="r", state=MomentState.clipped))
            cpath = cfg.clips / f"c{i}.mp4"; cpath.write_bytes(b"\x00")   # real render file — #10 guard checks existence
            c = Clip(id=f"clip_{i}", parent_id=f"mom_{i}", path=str(cpath), aspect=Fmt.r9x16, state=ClipState.queued)
            c.meta_captions = {"@b/instagram": {"caption": f"c{i}", "hashtags": []}}
            led.add_clip(c)
            led.add_post(Post(id=f"p_a{i}", parent_id=f"clip_{i}", account="@a", account_id="ig_a",
                              platform=Platform.instagram, caption="on A", state=PostState.published, public_url="dryrun://ig_a"))
    r = crosspost_all_to_account(cfg, "@a", "@b", "instagram", now=NOW)
    assert r.ok and r.detail["minted"] == 3 and r.detail["already_exists"] == 0
    r2 = crosspost_all_to_account(cfg, "@a", "@b", "instagram", now=NOW)   # idempotent
    assert r2.ok and r2.detail["minted"] == 0 and r2.detail["already_exists"] == 3
    r3 = crosspost_all_to_account(cfg, "@nobody", "@b", "instagram", now=NOW)
    assert not r3.ok                                            # empty source -> clean failure


def test_crosspost_refuses_when_render_file_missing(tmp_path):
    # #10: a reused clip whose .mp4 was gc-swept (retired/analyzed sweep) still passes the STATE check,
    # so without a guard a post is minted on a missing file and fails only later AT PUBLISH. Refuse at mint.
    import os
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg)
    os.remove(Ledger.load(cfg).clips["clip_0"].path)            # simulate the gc sweep (#10 trigger)
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert not r.ok and "render missing" in (r.error or "")
    assert not Ledger.load(cfg).posts                          # nothing minted (would have died at publish)


def test_crosspost_all_skips_missing_render_files(tmp_path):
    # #10 bulk: a missing render counts as a clean skip, never a mint. minted==0, skipped>0, ok False.
    import json
    from fanops.studio.actions import crosspost_all_to_account
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4"))
        for i in range(2):
            led.add_moment(Moment(id=f"mom_{i}", parent_id="src_1", content_token=f"{i}", start=i, end=i + 5, reason="r", state=MomentState.clipped))
            led.add_clip(Clip(id=f"clip_{i}", parent_id=f"mom_{i}", path=str(cfg.clips / f"gone_{i}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))   # path never written
            led.add_post(Post(id=f"p_a{i}", parent_id=f"clip_{i}", account="@a", account_id="ig_a", platform=Platform.instagram, caption="A", state=PostState.published, public_url="dryrun://ig_a"))
    r = crosspost_all_to_account(cfg, "@a", "@b", "instagram", now=NOW)
    assert not r.ok and r.detail["minted"] == 0 and r.detail["skipped"] == 2


def test_crosspost_common_path_never_renders(tmp_path, monkeypatch):
    # #6: when a present same-aspect render exists, crosspost must NOT invoke ffmpeg at all (neither in the
    # warm nor under the lock). Monkeypatch render_moment to BLOW UP if called -> a green mint proves it.
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_xacct(cfg)              # clip_0 9:16 file present; IG wants 9:16 -> reuse
    def _boom(*a, **k): raise AssertionError("render_moment called on the common reuse path")
    monkeypatch.setattr("fanops.crosspost.render_moment", _boom)
    r = crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok and r.detail["already_exists"] is False


def test_crosspost_warms_target_aspect_before_opening_the_lock(tmp_path, monkeypatch):
    # #4: the target-aspect render is resolved on a lock-free snapshot BEFORE Ledger.transaction opens, so a
    # first fan-out never runs ffmpeg (600s) under the flock. Assert the warm precedes the lock.
    from fanops.studio import actions as A
    cfg = Config(root=tmp_path); _seed_xacct(cfg)
    order = []
    real_warm, real_txn = A._warm_target_aspect, Ledger.transaction
    def spy_warm(*a, **k): order.append("warm"); return real_warm(*a, **k)
    def spy_txn(c): order.append("txn"); return real_txn(c)
    monkeypatch.setattr(A, "_warm_target_aspect", spy_warm)
    monkeypatch.setattr(Ledger, "transaction", spy_txn)
    r = A.crosspost_to_account(cfg, "clip_0", "@b", "instagram", now=NOW)
    assert r.ok and order[:2] == ["warm", "txn"], order        # warm ran lock-free BEFORE the transaction


def test_publish_now_live_dryrun_url_rejected(tmp_path, monkeypatch, mocker):
    import json
    from fanops.studio.actions import publish_now
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path); _seed(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}]}))
    mocker.patch("fanops.post.run.publish_post", return_value="published")
    dry_post = Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                    platform=Platform.instagram, caption="OLD", state=PostState.published,
                    public_url="dryrun://p_edit")
    led_guard = Ledger.load(cfg)
    led_after = Ledger.load(cfg); led_after.posts["p_edit"] = dry_post
    mocker.patch("fanops.ledger.Ledger.load", side_effect=[led_guard, led_after, led_after])
    res = publish_now(cfg, "p_edit", confirmed=True)
    assert res.ok is False
    assert "dryrun" in (res.error or "").lower()


# ---- Sprint 1: recover_posts (failed-tab bulk recovery) ----
def _fail_post(pid, reason):
    return Post(id=pid, parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.failed, error_reason=reason)


def test_recover_posts_retry_requeues_retryable(tmp_path):
    from fanops.studio.actions import recover_posts
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("ok", "postiz 429"))
    led.posts["ok"].submission_id = "old_sub"
    big = _fail_post("big", "zernio 413"); big.platform = Platform.tiktok; led.add_post(big)
    led.save()
    res = recover_posts(cfg, ["ok", "big"], action="retry", reason="studio_retry")
    assert res.ok
    led2 = Ledger.load(cfg)
    assert led2.posts["ok"].state is PostState.queued
    assert led2.posts["ok"].submission_id is None and led2.posts["ok"].error_reason is None
    assert led2.posts["big"].state is PostState.failed
    assert res.detail["retried"] == 1 and res.detail["skipped"] == 1


def test_recover_posts_retry_lands_a_schedule_when_timeless(tmp_path):
    # timeless-queued: recover_posts (retry) set queued but never guaranteed scheduled_time. A recovered post
    # whose scheduled_time was cleared/corrupt lands in queued but TIMELESS -> _due_or_fail parks it forever
    # (silent, invisible in the UI). The recovery must land a strictly-future time so it publishes on the lead
    # cycle instead of never.
    from fanops.studio.actions import recover_posts
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    p = _fail_post("t", "postiz 429"); p.scheduled_time = None; led.add_post(p)   # a TIMELESS failed post
    led.save()
    res = recover_posts(cfg, ["t"], action="retry", reason="studio_retry")
    assert res.ok
    p2 = Ledger.load(cfg).posts["t"]
    assert p2.state is PostState.queued
    assert p2.scheduled_time                                          # NOT timeless -> the daemon publishes it, never parks it forever
    assert parse_iso(p2.scheduled_time) is not None                  # ...a valid ISO time the scheduler can act on


def test_recover_posts_discard_terminal(tmp_path):
    from fanops.studio.actions import recover_posts
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("gone", "zernio 413"))
    led.save()
    res = recover_posts(cfg, ["gone"], action="discard", reason="oversize discard")
    assert res.ok
    assert Ledger.load(cfg).posts["gone"].state is PostState.rejected


def test_recover_posts_review_clears_publish_fields(tmp_path):
    from fanops.studio.actions import recover_posts
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    p = _fail_post("back", "postiz 429")
    p.public_url = "https://example.com/x"
    p.scheduled_time = _z(NOW + timedelta(hours=1))
    led.add_post(p)
    led.save()
    res = recover_posts(cfg, ["back"], action="review", reason="oversize re-render")
    assert res.ok
    q = Ledger.load(cfg).posts["back"]
    assert q.state is PostState.awaiting_approval
    assert q.public_url == "" and q.scheduled_time is None


def test_retry_oversize_failures_requeues_when_shrink_ok(tmp_path, monkeypatch, mocker):
    from fanops.studio.actions import retry_oversize_failures
    from fanops.accounts import add_account, set_backend
    monkeypatch.setenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "4")
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    vid = tmp_path / "big.mp4"
    vid.write_bytes(b"Z" * 100)
    led.add_post(Post(id="big", parent_id="clip_1", account="@tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.failed, error_reason="zernio upload 413 entity too large",
                      media_urls=[f"file://{vid}"]))
    led.save()
    mocker.patch("fanops.post.compress.apply_shrink_to_post", return_value=True)
    res = retry_oversize_failures(cfg)
    assert res.ok and res.detail["retried"] == 1
    p = Ledger.load(cfg).posts["big"]
    assert p.state is PostState.queued and p.error_reason is None and p.submission_id is None


def test_retry_oversize_skips_when_shrink_fails(tmp_path, monkeypatch, mocker):
    from fanops.studio.actions import retry_oversize_failures
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("big", "zernio 413"))
    led.posts["big"].platform = Platform.tiktok
    led.save()
    mocker.patch("fanops.post.compress.apply_shrink_to_post", return_value=False)
    res = retry_oversize_failures(cfg)
    assert res.ok and res.detail["retried"] == 0 and res.detail["skipped"] == 1
    assert Ledger.load(cfg).posts["big"].state is PostState.failed
