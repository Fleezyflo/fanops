# tests/test_hookedit.py — the FEED-AWARE hook editor (Phase 2 of the on-screen-hook framework).
# The moment responder answers each gate in ISOLATION (no cross-clip visibility), so it cannot
# diversify hooks across the feed — the diagnosed round-2 failure was template repetition
# ('before he was Moh Flow' x6). This pass sees EVERY decided hook at once and rewrites the
# weak/duplicated/templated ones into strong, DISTINCT hooks before any clip burns them. It reuses
# the deterministic guard (hookcheck.is_weak_hook) as the floor: a still-slop/still-dup rewrite is
# nulled to a clean clip. Default OFF (opt-in), fail-open — mirrors creative_variation.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState, HookEditDecision, HookEditItem
from fanops.agentstep import response_path, latest_request_id, pending
from fanops.hookedit import request_hook_edit, ingest_hook_edit, hook_edit_pending

def _src(led, cfg, sid, dur=20.0):
    led.add_source(Source(id=sid, source_path=str(cfg.sources / f"{sid}.mp4"),
                          state=SourceState.moments_decided, duration=dur, language="en",
                          meta={"transcribed": True}))

def _moment(led, sid, mid, hook, start=0.0, end=18.0, excerpt="they slept on me", reason="punchline"):
    led.add_moment(Moment(id=mid, parent_id=sid, state=MomentState.decided, start=start, end=end,
                          reason=reason, transcript_excerpt=excerpt, hook=hook, signal_score=0.5))

def _seed_feed(cfg):
    led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _src(led, cfg, "s2"); _src(led, cfg, "s3")
    _moment(led, "s1", "m1", "before he was Moh Flow")
    _moment(led, "s2", "m2", "before he was Moh Flow")   # cross-clip DUPLICATE (the bot tell)
    _moment(led, "s3", "m3", "his hardest bar")          # generic-superlative slop
    return led

def _answer(cfg, items):
    key = pending(cfg, kind="hookedit")[0]
    rid = latest_request_id(cfg, "hookedit", key)
    response_path(cfg, "hookedit", key).write_text(
        HookEditDecision(request_id=rid, items=items).model_dump_json())

def test_request_writes_feed_gate_with_all_hooked_moments(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    keys = pending(cfg, kind="hookedit")
    assert len(keys) == 1                              # ONE feed-level gate, not per-source
    payload = json.loads((cfg.agent_io / "requests" / f"hookedit__{keys[0]}.request.json").read_text())
    assert {it["moment_id"] for it in payload["items"]} == {"m1", "m2", "m3"}
    it = next(i for i in payload["items"] if i["moment_id"] == "m1")
    assert it["hook"] == "before he was Moh Flow"      # carries the current hook +
    assert it["transcript_excerpt"] and it["reason"]   # grounding context for the editor

def test_request_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_HOOK_EDITOR", raising=False)
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    assert pending(cfg, kind="hookedit") == []         # off by default -> no gate, no behavior change

def test_request_noop_when_no_hooked_moments(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _moment(led, "s1", "m1", None)   # clean clip -> nothing to edit
    led = request_hook_edit(led, cfg)
    assert pending(cfg, kind="hookedit") == []

def test_pending_true_until_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    assert hook_edit_pending(led, cfg) is True
    _answer(cfg, [HookEditItem(moment_id="m1", hook="before he was Moh Flow"),
                  HookEditItem(moment_id="m2", hook="no label, just raw talent"),
                  HookEditItem(moment_id="m3", hook="he names the day it changed")])
    assert hook_edit_pending(led, cfg) is False

def test_ingest_applies_rewritten_hooks_and_flips_edited(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    _answer(cfg, [HookEditItem(moment_id="m1", hook="before he was Moh Flow"),
                  HookEditItem(moment_id="m2", hook="no label, just raw talent"),
                  HookEditItem(moment_id="m3", hook="he names the day it changed")])
    led = ingest_hook_edit(led, cfg)
    assert led.moments["m2"].hook == "no label, just raw talent"   # diversified off the duplicate
    assert led.moments["m3"].hook == "he names the day it changed" # rewritten off the slop template
    assert all(led.moments[m].hook_edited for m in ("m1", "m2", "m3"))
    assert hook_edit_pending(led, cfg) is False                    # done; never re-edits (no loop)

def test_ingest_nulls_still_weak_or_still_duplicate_rewrite(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    # editor returns a STILL-duplicate pair + a STILL-generic one -> the deterministic guard nulls them
    _answer(cfg, [HookEditItem(moment_id="m1", hook="same hook"),
                  HookEditItem(moment_id="m2", hook="same hook"),           # cross-feed dup -> 2nd nulled
                  HookEditItem(moment_id="m3", hook="his coldest opener")])  # superlative slop -> nulled
    led = ingest_hook_edit(led, cfg)
    assert led.moments["m1"].hook == "same hook"
    assert led.moments["m2"].hook is None
    assert led.moments["m3"].hook is None
    assert all(led.moments[m].hook_edited for m in ("m1", "m2", "m3"))

def test_ingest_keeps_original_on_editor_omission_then_validates(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    # editor omits m1 entirely -> keep its original, but m1's original is a DUP of m2's kept rewrite,
    # so dedup still applies; m2 gets a fresh hook, m3 too.
    _answer(cfg, [HookEditItem(moment_id="m2", hook="before he was Moh Flow"),
                  HookEditItem(moment_id="m3", hook="he names the day it changed")])
    led = ingest_hook_edit(led, cfg)
    assert led.moments["m2"].hook == "before he was Moh Flow"   # editor's rewrite kept
    assert led.moments["m1"].hook is None                       # original was a dup of m2 -> nulled
    assert led.moments["m1"].hook_edited                        # still marked done (no infinite re-edit)

def test_ingest_sanitizes_em_dash_in_rewrite(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    _answer(cfg, [HookEditItem(moment_id="m1", hook="no label, no machine, just talent"),
                  HookEditItem(moment_id="m2", hook="she left the minute he won"),
                  HookEditItem(moment_id="m3", hook="the day before the deal — gone")])
    led = ingest_hook_edit(led, cfg)
    assert "—" not in (led.moments["m3"].hook or "")            # AI-tell em-dash stripped

def test_ingest_noop_when_response_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = request_hook_edit(led, cfg)
    led = ingest_hook_edit(led, cfg)                            # no response yet -> unchanged, still pending
    assert led.moments["m1"].hook == "before he was Moh Flow"
    assert not led.moments["m1"].hook_edited
    assert hook_edit_pending(led, cfg) is True

def test_ingest_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_HOOK_EDITOR", raising=False)
    cfg = Config(root=tmp_path); led = _seed_feed(cfg)
    led = ingest_hook_edit(led, cfg)                            # disabled -> never touches hooks
    assert led.moments["m1"].hook == "before he was Moh Flow"
    assert hook_edit_pending(led, cfg) is False
