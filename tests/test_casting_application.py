# tests/test_casting_application.py — Phase 1: make per-account moment casting REACH the output.
# The casting BRAIN works (a live probe returned DISJOINT per-persona sets); the bug is APPLICATION timing:
# ingest only applied affinities to `decided` moments, but render flips decided->clipped in the same advance
# the casting request fires, so the answer (a cycle later) found the moment already `clipped` and skipped it
# forever -> permanent fan-to-all. The fix: (1) ingest applies to `clipped` too; (2) crosspost WAITS for the
# casting gate (mirrors how captions gate crosspost); (3) `awaiting` counts moment_casting; (4) the request
# gate re-opens once for a stranded clipped-uncast source. Overlap stays LEGAL (fan-accounts-repost-freely).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt, MomentCastingDecision
from fanops.accounts import Accounts
from fanops.casting import request_moment_casting, ingest_moment_casting, casting_gate_pending
from fanops.crosspost import crosspost_clips
from fanops.agentstep import latest_request_id, response_path, write_request


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, *, persona="x", aid="1", platforms=("instagram", "youtube")):
    return {"handle": handle, "account_id": aid, "platforms": list(platforms), "status": "active", "persona": persona}

def _clipped_moment(*, affinities=None, state=MomentState.clipped):
    return Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                  transcript_excerpt="they slept on me", state=state,
                  affinities=list(affinities) if affinities else [])

def _captioned_clip():
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {f"{h}/{p}": {"caption": "impact.", "hashtags": ["#x"]}
                          for h in ("@a", "@b") for p in ("instagram", "youtube")}
    return clip

def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

def _src(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, language="en"))
    return led


# ---- Task 1 + overlap-legal: ingest applies affinities to a CLIPPED moment ----
def test_ingest_applies_affinities_to_clipped_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[])); led.save(); led = Ledger.load(cfg)
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # Task 4: opens for a clipped-uncast moment
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections={"@a": ["mom_1"], "@b": ["mom_1"]}).model_dump_json())
    led = ingest_moment_casting(led, cfg, "src_1", accts)
    assert led.moments["mom_1"].affinities == ["@a", "@b"]            # applied to CLIPPED + overlap stays legal


# ---- Task 2: the casting_gate_pending predicate ----
def test_casting_gate_pending_states(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[]))
    accts = Accounts.load(cfg)
    assert casting_gate_pending(cfg, "src_1") is False                # no request yet -> nothing to wait for
    led = request_moment_casting(led, cfg, "src_1", accts)
    assert casting_gate_pending(cfg, "src_1") is True                 # request open, unanswered -> WAIT
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections={"@a": ["mom_1"]}).model_dump_json())
    assert casting_gate_pending(cfg, "src_1") is False                # answered -> proceed
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    assert casting_gate_pending(cfg, "src_1") is False                # OFF short-circuit


# ---- Task 2 wiring: crosspost WAITS for casting, then fans out scoped (the no-fan-to-all-leak proof) ----
def test_crosspost_skips_while_casting_pending_then_fans_scoped(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[])); led.add_clip(_captioned_clip())
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # gate OPEN, unanswered
    assert latest_request_id(cfg, "moment_casting", "src_1") is not None
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert led.posts == {}                                           # NO premature fan-to-all mint while pending
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections={"@a": ["mom_1"]}).model_dump_json())
    led = ingest_moment_casting(led, cfg, "src_1", accts)
    assert led.moments["mom_1"].affinities == ["@a"]
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"}         # the late answer GOVERNS; @b never posts


# ---- Task 2/OFF firewall: pending is inert + fan-to-all is byte-identical when casting OFF ----
def test_off_firewall_pending_inert_and_fans_all(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    write_request(cfg, kind="moment_casting", key="src_1", payload={"source_id": "src_1", "moments": [], "personas": []})
    assert casting_gate_pending(cfg, "src_1") is False                # OFF -> never waits, even with a request on disk
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=["@a"])); led.add_clip(_captioned_clip())
    accts = Accounts.load(cfg); _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a", "@b"}    # OFF ignores affinities -> fans to ALL


# ---- Task 3: awaiting counts moment_casting so `fanops run` waits for the gate ----
def test_awaiting_includes_moment_casting(tmp_path):
    from fanops.pipeline import advance
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert "moment_casting" in s["awaiting"] and s["awaiting"]["moment_casting"] == 0


# ---- Task 4: the request gate re-opens ONCE for a stranded clipped-uncast source (idempotent) ----
def test_request_opens_for_clipped_uncast_and_is_write_once(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[]))
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # clipped-uncast -> gate opens (was: skipped)
    rid1 = latest_request_id(cfg, "moment_casting", "src_1")
    assert rid1 is not None
    led = request_moment_casting(led, cfg, "src_1", accts)            # write-once: no re-cast storm
    assert latest_request_id(cfg, "moment_casting", "src_1") == rid1
