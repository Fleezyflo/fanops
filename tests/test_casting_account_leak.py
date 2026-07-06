# tests/test_casting_account_leak.py — WS1 (audit HIGH c5-f1 / xc-1): a persona-LESS but ACTIVE account
# must NOT silently post nothing for a cast source. The leak: request_moment_casting builds the brief only
# for accounts with a truthy casting_directive (casting.py:108-109 walrus filter), so a voice-less, lever-less
# account is NEVER a candidate; the LLM can't select it; ingest writes it no AccountSelection; and at crosspost
# account_selection_admits sees `sel is None` + others-selected -> DENY (casting.py:215-219). The fan-to-all
# fallback holds at MOMENT granularity (an uncast moment fans to all) but breaks at ACCOUNT granularity.
#
# The RF1 contract DELIBERATELY denies an in-brief-but-unpicked account (true differentiation) and warns that a
# SILENT auto-fan would resurrect the collapse RF1 closed. So the fix distinguishes the two cases: a NEVER-
# CANDIDATE active account (persona-less, absent from the brief) gets an EXPLICIT, LABELLED fan_all_default
# AccountSelection — visible, not silent — so it ships fan-to-all via the labelled gate branch (casting.py:220),
# while an in-brief-unpicked account still DENIES. SelectionMethod.fan_all_default already exists for exactly
# this (models.py:293).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, MomentState, MomentCastingDecision, SelectionMethod,
                           Clip, ClipState, Fmt)
from fanops.accounts import Accounts
from fanops.casting import request_moment_casting, ingest_moment_casting, account_selection_admits
from fanops.crosspost import crosspost_clips


def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

def _captioned_clip(handles=("a", "b")):
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {f"{h}/{p}": {"caption": "c", "hashtags": []}
                          for h in handles for p in ("instagram", "youtube")}
    return clip


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, *, persona="x", aid="1", platforms=("instagram", "youtube")):
    return {"handle": handle, "account_id": aid, "platforms": list(platforms), "status": "active", "persona": persona}

def _decided_moment(mid="mom_1"):
    return Moment(id=mid, parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                  transcript_excerpt="they slept on me", state=MomentState.decided, affinities=[])

def _src(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, language="en"))
    return led

def _run_casting(cfg, picks):
    """Drive request->respond->ingest for src_1; `picks` is the LLM selections dict. Returns the ingested led."""
    from fanops.agentstep import latest_request_id, response_path
    led = _src(cfg); led.add_moment(_decided_moment()); led.save(); led = Ledger.load(cfg)
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid or "", selections=picks).model_dump_json())
    return ingest_moment_casting(led, cfg, "src_1", accts), accts


# ---- the leak: a persona-less active account is denied on a cast source ----
def test_personaless_active_account_gets_explicit_fan_all_default(tmp_path):
    # @a has a persona (a candidate); @b is persona-less (never in the brief). The LLM casts mom_1 to @a only.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan"), _acct("b", persona="", aid="2")])
    led, _ = _run_casting(cfg, {"a": ["mom_1"]})
    sel_b = led.account_selection_for("src_1", "b")
    assert sel_b is not None, "persona-less active @b got NO AccountSelection -> silently denied on the cast source (the leak)"
    assert sel_b.method is SelectionMethod.fan_all_default, "@b must ship fan-to-all via the LABELLED branch, not a silent admit"

def test_personaless_account_is_admitted_at_crosspost(tmp_path):
    # the consequence: account_selection_admits must ADMIT @b for the cast moment (fan-to-all), not DENY it.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan"), _acct("b", persona="", aid="2")])
    led, _ = _run_casting(cfg, {"a": ["mom_1"]})
    mom = led.moments["mom_1"]
    assert account_selection_admits(cfg, led, mom, "b") is True, "persona-less @b denied on the cast moment — zero posts, silent"
    # @a's deliberate single-pick is unchanged (RF1 differentiation preserved)
    assert account_selection_admits(cfg, led, mom, "a") is True

def test_in_brief_unpicked_account_still_denies(tmp_path):
    # the RF1 contract MUST survive: an account that WAS in the brief (has a persona) but the LLM did not pick
    # for this moment is genuinely differentiated -> still DENY (we only rescue the NEVER-candidate case).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan"),
                         _acct("b", persona="a blunt critic", aid="2"),
                         _acct("c", persona="", aid="3")])
    led, _ = _run_casting(cfg, {"a": ["mom_1"]})        # @b in-brief but unpicked; @c never a candidate
    mom = led.moments["mom_1"]
    assert account_selection_admits(cfg, led, mom, "b") is False, "in-brief-unpicked @b must still DENY (RF1 differentiation)"
    assert account_selection_admits(cfg, led, mom, "c") is True,  "never-candidate @c must fan-to-all"


# ---- c8-f2: a clip consumed to `queued` with ZERO posts born must leave a crosspost breadcrumb ----
def test_zero_post_clip_logs_no_post_born(tmp_path, mocker):
    # casting ON, unbatched source; both CANDIDATE accounts have selections that EXCLUDE this clip's moment
    # -> every surface DENIED -> zero posts. Today the clip flips to queued with no crosspost-stage trace.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan"), _acct("b", persona="a blunt critic", aid="2")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          transcript_excerpt="x", state=MomentState.clipped, affinities=["ghost"]))
    led.add_clip(_captioned_clip())
    led.save(); led = Ledger.load(cfg); _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 0                                     # every surface denied (owner not active)
    assert led.clips["clip_1"].state is ClipState.queued          # clip is still consumed
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "no_post_born" in log and "clip_1" in log              # the silent drop now leaves a breadcrumb


def test_selection_denied_surface_leaves_per_surface_breadcrumb(tmp_path, mocker):
    # silent-post-drop-no-per-surface-breadcrumb (high): a selection-DENY skip (crosspost.py:169) returned 0
    # with NO per-surface log — so when many surfaces are silently denied the operator sees only one generic
    # no_post_born and cannot tell WHICH surfaces dropped or WHY (selection vs cap vs render). Each silent
    # skip must leave a skipped_surface breadcrumb naming the surface + reason.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan"), _acct("b", persona="a blunt critic", aid="2")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          transcript_excerpt="x", state=MomentState.clipped, affinities=["ghost"]))
    led.add_clip(_captioned_clip())
    led.save(); led = Ledger.load(cfg); _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "skipped_surface" in log and "not_cast" in log          # each denied surface names itself + the reason
    assert "a/instagram" in log or "b/instagram" in log          # the specific dropped surface is identified


# ---- MOL-149: crosspost no longer defers on failed-to-open casting gate — affinity gate fans per affinities ----
def test_crosspost_fans_per_affinities_when_gate_failed_to_open(tmp_path, mocker, monkeypatch):
    # casting ON, clipped moment with affinities=[] (uncast) + captioned clip, NO gate opened: P8 crosspost uses
    # affinity_admits only (no casting defer) -> uncast fans to all in one pass, no casting_pending_skip.
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", persona="a devoted fan")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          transcript_excerpt="x", state=MomentState.clipped, affinities=[]))
    led.add_clip(_captioned_clip(handles=("a",)))
    led.save(); led = Ledger.load(cfg); _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 2, "uncast moment (affinities=[]) fans to all surfaces in one pass"
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "casting_pending_skip" not in log
