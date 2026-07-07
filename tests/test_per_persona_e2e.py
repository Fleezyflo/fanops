# tests/test_per_persona_e2e.py — P15 capstone: single-owner per-persona E2E + closed-loop metric proof.
# N personas → one source gate → owner-attributed picks → one hook/clip/caption per owner-moment →
# crosspost mints ONLY on owner surfaces. Slow UNIT; deterministic (injected time, faked ffmpeg, stub backends).
import subprocess
import pytest
pytestmark = pytest.mark.slow
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, MomentPick, MomentState, ClipState, PostState,
                           SourceState, Fmt)
from tests.test_persona_fixtures import ensure_archetype_personas
from fanops.moments import (request_moments, ingest_moments, request_moment_hooks, ingest_moment_hooks)
from fanops.crosspost import crosspost_clips
from fanops.studio.actions import approve_posts
from fanops.agentstep import response_path, latest_request_id
from fanops.responder import screen_model_text
from fanops.models import MomentDecision, MomentHookDecision
from fanops.timeutil import parse_iso
from fanops.post.run import publish_due
from fanops.reconcile import reconcile_posts
from fanops.track import pull_metrics

FIXED = "2026-06-21T00:00:00.000001Z"
FIXED_DT = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _fake_ffmpeg(mocker):
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


def _ingest_picks(led, cfg, source_id, picks):
    rid = latest_request_id(cfg, "moments", source_id)
    dec = screen_model_text(MomentDecision(source_id=source_id, request_id=rid, picks=picks))
    response_path(cfg, "moments", source_id).write_text(dec.model_dump_json())
    return ingest_moments(led, cfg, source_id)


def _decide_hooks(led, cfg, source_id, hooks, accounts):
    led = request_moment_hooks(led, cfg, source_id, accounts=accounts)
    for m in [m for m in led.moments.values() if m.parent_id == source_id and m.state is MomentState.picked]:
        key = f"{source_id}.{m.content_token}"
        rid = latest_request_id(cfg, "moment_hooks", key)
        dec = screen_model_text(MomentHookDecision(request_id=rid, hook=hooks.get(m.content_token)))
        response_path(cfg, "moment_hooks", key).write_text(dec.model_dump_json())
    return ingest_moment_hooks(led, cfg, source_id, accounts=accounts)


def _seed_persona_accounts(cfg):
    return ensure_archetype_personas(cfg)


def test_per_persona_single_owner_e2e_through_crosspost(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); accts = _seed_persona_accounts(cfg); _fake_ffmpeg(mocker)
    cfg.clips.mkdir(parents=True, exist_ok=True)

    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.signalled, duration=60.0, language="en",
                          transcript=[{"start": 0, "end": 8, "text": "facts matter"},
                                      {"start": 20, "end": 28, "text": "they started the beef"}],
                          signal_peaks=[{"t": 4.0, "kind": "scene_cut", "score": 0.5},
                                        {"t": 24.0, "kind": "scene_cut", "score": 0.8}],
                          meta={"transcribed": True}))

    # PASS 1: one source gate, N owner-attributed picks
    led = request_moments(led, cfg, "src_1", accounts=accts)
    picks = [MomentPick(start=0, end=8, reason="credible window", personas=["trust"]),
             MomentPick(start=20, end=28, reason="rivalry window", personas=["drama"])]
    led = _ingest_picks(led, cfg, "src_1", picks)
    moms = led.moments_of("src_1")
    assert len(moms) == 2
    for m in moms:
        assert len(m.affinities) == 1                          # single-owner: exactly one handle
        assert m.hook is None and m.state is MomentState.picked

    # PASS 2: one hook per owner-moment (hook gate sends ONLY the owner)
    hooks = {"0.00-8.00": "pass on the sensational cut", "20.00-28.00": "who started it though"}
    led = _decide_hooks(led, cfg, "src_1", hooks, accts)
    for m in led.moments_of("src_1"):
        assert m.state is MomentState.decided and m.hook

    # owner-scoped captions (owner × platform — no AccountSelection fan-out)
    for m in led.moments_of("src_1"):
        owner = m.affinities[0]
        clip = Clip(id=f"clip_{owner}", parent_id=m.id, path=str(cfg.clips / f"{owner}.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.captioned)
        clip.meta_captions = {
            f"{owner}/instagram": {"caption": f"cap for {owner}", "hashtags": ["#x"]},
            "extra/instagram": {"caption": "should not ship", "hashtags": []},   # wrong owner surface
        }
        led.add_clip(clip)
    led.save()

    # crosspost: posts ONLY on each moment's owner — @extra never gets a post
    led = crosspost_clips(Ledger.load(cfg), cfg, accts, base_time=FIXED)
    led.save()
    assert {p.account for p in led.posts.values()} == {"trust", "drama"}
    for p in led.posts.values():
        m = led.moments[led.clips[p.parent_id].parent_id]
        assert p.account == m.affinities[0]
        assert p.state is PostState.awaiting_approval
        assert "hooks_by_persona" not in Moment.model_fields
        assert not hasattr(led, "account_selections") or not getattr(led, "account_selections", None)

    # approve → strictly-future queued
    pids = list(led.posts)
    assert approve_posts(cfg, pids, now=FIXED_DT).ok is True
    for pid in pids:
        ap = Ledger.load(cfg).posts[pid]
        assert ap.state is PostState.queued and parse_iso(ap.scheduled_time) > FIXED_DT


def test_closed_loop_single_owner_lift_round_trip(tmp_path, monkeypatch, mocker):
    """P15 closed-loop: crosspost mint → approve → publish (stub) → reconcile permalink → Graph lift."""
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("META_GRAPH_TOKEN", "mtok")
    monkeypatch.setenv("META_IG_USER_ID", "ig-1")
    cfg = Config(root=tmp_path); accts = _seed_persona_accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "trust.mp4"; base.write_bytes(b"X")

    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(id="mom_trust", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.decided, hook="one owner hook", affinities=["trust"]))
    clip = Clip(id="clip_trust", parent_id="mom_trust", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"trust/instagram": {"caption": "owner cap", "hashtags": ["#facts"]}}
    led.add_clip(clip); led.save()

    led = crosspost_clips(Ledger.load(cfg), cfg, accts, base_time=FIXED)
    led.save()
    assert len(led.posts) == 1 and led.posts[next(iter(led.posts))].account == "trust"
    pid = next(iter(led.posts))
    approve_posts(cfg, [pid], now=FIXED_DT)
    p = Ledger.load(cfg).posts[pid]
    p.scheduled_time = "2020-01-01T00:00:00Z"; p.media_urls = ["https://h/v.mp4"]
    with Ledger.transaction(cfg) as tx:
        tx.posts[pid] = p

    import fanops.post.run as run
    class _OkPoster:
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = "sub_trust"
            led_.posts[post_id].public_url = "https://www.instagram.com/reel/TRUST/"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster())
    publish_due(cfg, now="2020-01-01T01:00:00Z")
    led = Ledger.load(cfg)
    assert led.posts[pid].state is PostState.published

    def _status(sid):
        return {"status": "published", "publicUrl": "https://www.instagram.com/reel/TRUST/"}
    led = reconcile_posts(led, cfg, get_status=lambda sid: _status(sid))
    p = led.posts[pid]
    assert p.public_url and "instagram.com" in p.public_url
    p.media_id = "M_trust"; p.cut_seconds = 7.0
    with Ledger.transaction(cfg) as tx:
        tx.posts[pid] = p

    mocker.patch("fanops.meta_graph.media_insights",
                 return_value={"reach": 4200, "saves": 30, "shares": 12, "retention": 0.62})
    led = pull_metrics(led, cfg)
    p = led.posts[pid]
    assert p.state is PostState.analyzed
    assert p.account == "trust"
    assert "lift_score" in p.metrics and p.metrics["lift_score"] > 0
    assert p.metrics.get("reach") == 4200
