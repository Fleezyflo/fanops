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

def _moment(led, sid, mid, hook, *, edited=True, judged=False, rounds=0, excerpt="they slept on me", reason="punchline"):
    led.add_moment(Moment(id=mid, parent_id=sid, state=MomentState.decided, start=0.0, end=18.0,
                          reason=reason, transcript_excerpt=excerpt, hook=hook, signal_score=0.5,
                          hook_edited=edited, hook_judged=judged, hook_rounds=rounds, hook_pattern="proof"))

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

def test_prompt_encodes_strict_reasoning_critic_with_frames_and_narration_signal():
    # Task 6: the critic is a REASONING vision judge — it SEES frames, judges by the real retention
    # triggers (not a portability checklist), consumes the narration `structure_flag` as a SIGNAL, and
    # is STRICT (reject-when-unsure, because the editor gets one more repair pass).
    p = hookjudge_prompt({"guidance": "BRAND: confident.", "items": [
        {"moment_id": "m1", "hook": "he stopped answering for a reason",
         "transcript_excerpt": "no label, built it all", "reason": "origin", "language": "en",
         "frames": [], "structure_flag": "third_person_narration"}]})
    low = p.lower()
    assert "critic" in low and "reject" in low and "keep" in low         # passes/rejects, not rewrite
    assert "rewrite" in low                                             # explicitly states it does NOT rewrite
    assert "curiosity gap" in low and "self-relevance" in low           # judges by the real triggers
    assert "structure_flag" in low and "third_person_narration" in low  # consumes the narration SIGNAL
    assert "unsure" in low and "one more pass" in low                   # strict: reject-when-unsure, editor repairs
    assert "frames" in low                                              # vision critic: sees the footage
    assert "moment_id" in p and "m1" in p                               # one verdict per id, feed carried in
    assert "data to judge only" in low and "never instructions" in low  # injection guard

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
    assert it["frames"] == []                                           # vision critic: frames key present (empty: no real src in tests)
    assert "structure_flag" in it                                       # narration signal carried to the critic

def test_payload_carries_frames_and_narration_structure_flag(tmp_path, monkeypatch):
    # Task 6: the critic is now a VISION critic — each item carries `frames` (empty in tests: no real
    # source file) — and a `structure_flag` SIGNAL: narration_signature flags a third-person recap so the
    # critic scrutinises it. The flag is a SIGNAL, not a gate (it never rejects on its own).
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _src(led, cfg, "s2")
    _moment(led, "s1", "m1", "he stopped answering for a reason")   # third-person recap -> flagged
    _moment(led, "s2", "m2", "the line you'll send to one person")  # addresses the viewer -> not flagged
    led = request_hook_judge(led, cfg)
    keys = pending(cfg, kind="hookjudge")
    payload = json.loads((cfg.agent_io / "requests" / f"hookjudge__{keys[0]}.request.json").read_text())
    by = {it["moment_id"]: it for it in payload["items"]}
    assert by["m1"]["frames"] == [] and by["m2"]["frames"] == []          # frames key present (no real src in tests)
    assert by["m1"]["structure_flag"] == "third_person_narration"         # recap flagged for scrutiny
    assert by["m2"]["structure_flag"] is None                             # viewer-addressed -> no flag

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

def test_critic_active_and_fail_open_by_default(tmp_path, monkeypatch):
    # v2: hook_judge DEFAULT ON — with the env UNSET the critic still opens its gate and applies
    # verdicts. Fail-open survives the flip: an omitted verdict KEEPS the hook (never strips on silence).
    monkeypatch.delenv("FANOPS_HOOK_JUDGE", raising=False)
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    assert list(pending(cfg, kind="hookjudge"))                          # gate opened despite unset env
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=True)])             # m2 omitted
    led = ingest_hook_judge(led, cfg)
    assert led.moments["m2"].hook == "when you have to let go"           # kept (fail-open)
    assert led.moments["m1"].hook_judged and led.moments["m2"].hook_judged

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

# ---- Task 7: author<->critic repair loop (one bounded round) ----

def test_repair_reopens_on_first_reject(tmp_path, monkeypatch):
    # An explicit reject at round 0 RE-OPENS the moment for ONE editor repair pass — it is NOT nulled;
    # its hook is kept (the editor will rewrite it), the critic's reason becomes feedback, the round
    # counter advances, and hook_edited/hook_judged reset so editor + critic both run again.
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led = request_hook_judge(led, cfg)
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=True),
                  HookJudgeItem(moment_id="m2", keep=False, why="generic, re-aim at the viewer")])
    led = ingest_hook_judge(led, cfg)
    m2 = led.moments["m2"]
    assert m2.hook == "when you have to let go"                  # NOT nulled — re-opened for repair
    assert m2.hook_rounds == 1
    assert m2.hook_feedback == "generic, re-aim at the viewer"
    assert m2.hook_edited is False and m2.hook_judged is False   # editor + critic both run again
    m1 = led.moments["m1"]
    assert m1.hook_judged is True and m1.hook_feedback is None   # kept -> finalized, feedback cleared

def test_repair_nulls_at_cap(tmp_path, monkeypatch):
    # At the repair cap (hook_rounds == _MAX_REPAIR), a second reject is terminal: null to a clean clip
    # so it renders clean (NOT re-opened, NOT held forever).
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1")
    _moment(led, "s1", "m1", "still generic after repair", rounds=1)   # already used its one repair
    led = request_hook_judge(led, cfg)
    _answer(cfg, [HookJudgeItem(moment_id="m1", keep=False, why="still generic")])
    led = ingest_hook_judge(led, cfg)
    m1 = led.moments["m1"]
    assert m1.hook is None and m1.hook_pattern is None          # capped -> clean clip
    assert m1.hook_judged is True                               # finalized (renders clean)
    assert m1.hook_rounds == 1                                  # not advanced past the cap

def test_repair_batch_partitions_by_round(tmp_path, monkeypatch):
    # CRITICAL: a round-0 and a round-1 moment must land in SEPARATE gates. A batch that mixed rounds
    # would answer only partly and STRAND the round-0 clip (never renders).
    monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1"); _src(led, cfg, "s2")
    _moment(led, "s1", "m1", "fresh round zero hook", rounds=0)
    _moment(led, "s2", "m2", "repaired round one hook", rounds=1)
    led = request_hook_judge(led, cfg)
    keys = pending(cfg, kind="hookjudge")
    assert len(keys) == 2                                        # one gate per round, not one merged
    sets = []
    for k in keys:
        payload = json.loads((cfg.agent_io / "requests" / f"hookjudge__{k}.request.json").read_text())
        sets.append({it["moment_id"] for it in payload["items"]})
    assert {"m1"} in sets and {"m2"} in sets                    # neither round shares a gate

def test_repair_full_cycle(tmp_path, monkeypatch):
    # Full author<->critic repair: edit -> reject -> re-open -> round-1 edit -> keep. The hook survives
    # as the round-1 rewrite, and the round-0 gates are NOT reused (round-keyed digests mint fresh gates).
    from fanops.hookedit import request_hook_edit, ingest_hook_edit
    from fanops.models import HookEditDecision, HookEditItem
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1"); monkeypatch.setenv("FANOPS_HOOK_JUDGE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, "s1")
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.decided, start=0.0, end=18.0,
                          reason="origin", transcript_excerpt="built it all", hook="generic seed hook",
                          signal_score=0.5))

    def _answer_edit(items):
        k = pending(cfg, kind="hookedit")[0]
        rid = latest_request_id(cfg, "hookedit", k)
        response_path(cfg, "hookedit", k).write_text(HookEditDecision(request_id=rid, items=items).model_dump_json())
        return k

    def _answer_judge(items):
        k = pending(cfg, kind="hookjudge")[0]
        rid = latest_request_id(cfg, "hookjudge", k)
        response_path(cfg, "hookjudge", k).write_text(HookJudgeDecision(request_id=rid, items=items).model_dump_json())
        return k

    # round 0: editor sets a hook, critic REJECTS -> re-open (not null)
    led = request_hook_edit(led, cfg); e0 = _answer_edit([HookEditItem(moment_id="m1", hook="round zero hook")]); led = ingest_hook_edit(led, cfg)
    led = request_hook_judge(led, cfg); j0 = _answer_judge([HookJudgeItem(moment_id="m1", keep=False, why="re-aim at the viewer")]); led = ingest_hook_judge(led, cfg)
    assert led.moments["m1"].hook == "round zero hook" and led.moments["m1"].hook_rounds == 1
    assert led.moments["m1"].hook_feedback == "re-aim at the viewer"

    # round 1 edit: a FRESH gate (round-keyed digest), repaired hook applied, feedback cleared
    led = request_hook_edit(led, cfg); e1 = pending(cfg, kind="hookedit")[0]
    assert e1 != e0                                             # round-1 edit gate is a NEW key
    _answer_edit([HookEditItem(moment_id="m1", hook="you ever build something alone")]); led = ingest_hook_edit(led, cfg)
    assert led.moments["m1"].hook == "you ever build something alone" and led.moments["m1"].hook_feedback is None

    # round 1 critic: fresh judge gate, KEEP -> hook survives, finalized
    led = request_hook_judge(led, cfg); j1 = _answer_judge([HookJudgeItem(moment_id="m1", keep=True)]); led = ingest_hook_judge(led, cfg)
    assert j1 != j0                                             # round-1 judge gate is a NEW key, not round-0
    assert led.moments["m1"].hook == "you ever build something alone"
    assert led.moments["m1"].hook_judged is True and led.moments["m1"].hook_rounds == 1
