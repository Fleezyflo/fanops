# tests/test_hookjudge.py — the SPECIFICITY CRITIC (Phase 3 of the on-screen-hook framework). The
# editor (hookedit.py) AUTHORS each hook grounded in the clip's frames; this INDEPENDENT pass JUDGES
# the result against the verified retention rubric and REJECTS to a clean clip what does not clear it.
# It is the "later LLM critic" hookcheck.is_weak_hook defers nuance to — the teeth that make
# "specific, not generic" ENFORCED, not merely suggested in a prompt. Gated by cfg.hook_editor; fail-open.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState, HookJudgeDecision, HookJudgeItem
from fanops.agentstep import response_path, latest_request_id, pending
from fanops.hookjudge import request_hook_judge, ingest_hook_judge, hook_judge_pending
from fanops.prompts import hookjudge_prompt

def _src(led, cfg, sid, dur=20.0):
    led.add_source(Source(id=sid, source_path=str(cfg.sources / f"{sid}.mp4"),
                          state=SourceState.moments_decided, duration=dur, language="en",
                          meta={"transcribed": True}))

def _moment(led, sid, mid, hook, *, edited=True, judged=False, excerpt="they slept on me", reason="punchline"):
    led.add_moment(Moment(id=mid, parent_id=sid, state=MomentState.decided, start=0.0, end=18.0,
                          reason=reason, transcript_excerpt=excerpt, hook=hook, signal_score=0.5,
                          hook_edited=edited, hook_judged=judged, hook_pattern="proof"))

def _seed(cfg):
    led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _src(led, cfg, "s2")
    _moment(led, "s1", "m1", "they built the whole thing alone")   # anchored/specific -> keep
    _moment(led, "s2", "m2", "when you have to let go")            # generic -> reject
    return led

def _answer(cfg, items):
    for key in pending(cfg, kind="hookjudge"):
        rid = latest_request_id(cfg, "hookjudge", key)
        response_path(cfg, "hookjudge", key).write_text(
            HookJudgeDecision(request_id=rid, items=items).model_dump_json())

def test_prompt_encodes_the_rubric_and_is_a_critic_not_an_author():
    p = hookjudge_prompt({"guidance": "BRAND: confident.", "items": [
        {"moment_id": "m1", "hook": "they built the whole thing alone",
         "transcript_excerpt": "no label, built it all", "reason": "origin", "language": "en"}]})
    low = p.lower()
    assert "critic" in low and "reject" in low and "keep" in low         # pass/reject, not rewrite
    assert "anchored" in low and "different clip" in low                 # the rubric: anchor + portability
    assert "loop" in low                                                # opens a loop
    assert "moment_id" in p and "m1" in p                               # one verdict per id, feed carried in
    assert "data to judge only" in low and "never instructions" in low  # injection guard
    assert "rewrite" in low                                             # explicitly states it does NOT rewrite

def test_request_opens_gate_for_edited_unjudged_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    keys = pending(cfg, kind="hookjudge")
    assert len(keys) == 1
    payload = json.loads((cfg.agent_io / "requests" / f"hookjudge__{keys[0]}.request.json").read_text())
    assert {it["moment_id"] for it in payload["items"]} == {"m1", "m2"}
    it = next(i for i in payload["items"] if i["moment_id"] == "m1")
    assert it["hook"] == "they built the whole thing alone"
    assert it["transcript_excerpt"] and it["reason"]                    # grounding context for the judge
    assert "frames" not in it                                           # text critic: no vision frames

def test_request_skips_unedited_and_already_judged(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _src(led, cfg, "s2"); _src(led, cfg, "s3")
    _moment(led, "s1", "m1", "anchored hook here", edited=False)        # editor hasn't run -> skip
    _moment(led, "s2", "m2", "already judged hook", judged=True)        # judged -> skip
    _moment(led, "s3", "m3", None)                                      # clean clip -> nothing to judge
    led = request_hook_judge(led, cfg)
    assert pending(cfg, kind="hookjudge") == []

def test_request_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "off")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    assert pending(cfg, kind="hookjudge") == []

def test_pending_true_until_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    assert hook_judge_pending(led, cfg) is True
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=True), HookJudgeItem(moment_id="m2", keep=False)])
    assert hook_judge_pending(led, cfg) is False

def test_ingest_rejects_generic_to_clean_keeps_anchored(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=True, why="anchored to this clip"),
                  HookJudgeItem(moment_id="m2", keep=False, why="generic, fits any clip")])
    led = ingest_hook_judge(led, cfg)
    assert led.moments["m1"].hook == "they built the whole thing alone"  # passed -> kept
    assert led.moments["m2"].hook is None                                # rejected -> clean clip
    assert led.moments["m2"].hook_pattern is None                        # pattern cleared with the hook
    assert led.moments["m1"].hook_judged and led.moments["m2"].hook_judged

def test_ingest_keeps_hook_on_judge_omission(tmp_path, monkeypatch):
    # Fail-open: the judge omits m2 entirely -> its hook is KEPT (the critic never strips on silence),
    # but the batch is marked judged so the pass does not loop.
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=True)])              # m2 omitted
    led = ingest_hook_judge(led, cfg)
    assert led.moments["m2"].hook == "when you have to let go"           # kept (no explicit reject)
    assert led.moments["m2"].hook_judged

def test_ingest_noop_when_response_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    led = ingest_hook_judge(led, cfg)                                    # no response -> unchanged, pending
    assert led.moments["m1"].hook == "they built the whole thing alone"
    assert not led.moments["m1"].hook_judged
    assert hook_judge_pending(led, cfg) is True

def test_ingest_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "off")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = ingest_hook_judge(led, cfg)
    assert led.moments["m1"].hook == "they built the whole thing alone"
    assert hook_judge_pending(led, cfg) is False
