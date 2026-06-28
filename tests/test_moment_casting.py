# tests/test_moment_casting.py — M1: LLM-driven per-account moment SELECTION (Option C, generous).
# A frame/persona-aware agent gate (kind="moment_casting") chooses, per account, that account's OWN set of
# moments from the shared decided pool — writing Moment.affinities, which the EXISTING crosspost affinity
# gate already honors (a cast moment fans ONLY to its accounts). GENEROUS: no count cap — an account gets
# every moment the selector assigns it; overlap across accounts is allowed (a moment can suit several
# personas). Mirrors the moments gate request->respond->ingest harness.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, MomentState, MomentCastingDecision)
from fanops.accounts import Accounts
from fanops.agentstep import response_path, latest_request_id, pending
from fanops.casting import request_moment_casting, ingest_moment_casting


def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle, persona="x", aid="1"):
    return {"handle": handle, "account_id": aid, "platforms": ["instagram"], "status": "active", "persona": persona}

def _moment(led, mid, *, reason="r", hook=None, signal=0.0, transcript="", state=MomentState.decided):
    led.add_moment(Moment(id=mid, parent_id="src_1", content_token=mid, start=0, end=7, reason=reason,
                          hook=hook, signal_score=signal, transcript_excerpt=transcript, state=state))

def _seed(cfg, accts, moments=("m0", "m1", "m2")):
    _accounts(cfg, accts)
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    for mid in moments: _moment(led, mid, signal=1.0)
    led.save(); return Ledger.load(cfg)

def _respond_and_ingest(led, cfg, selections, *, source_id="src_1"):
    """Write a MomentCastingDecision (handle -> [moment_id]) keyed to the open request, then ingest it."""
    rid = latest_request_id(cfg, "moment_casting", source_id)
    response_path(cfg, "moment_casting", source_id).write_text(
        MomentCastingDecision(request_id=rid, selections=selections).model_dump_json())
    return ingest_moment_casting(led, cfg, source_id, Accounts.load(cfg))


# ---- request gate ----
def test_request_writes_gate_with_moments_and_personas(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    assert latest_request_id(cfg, "moment_casting", "src_1") is not None
    payload = json.loads(_req_path(cfg, "src_1").read_text())
    assert {m["moment_id"] for m in payload["moments"]} == {"m0", "m1", "m2"}
    assert {p["handle"] for p in payload["personas"]} == {"@a", "@b"}

def test_request_is_write_once(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    rid1 = latest_request_id(cfg, "moment_casting", "src_1")
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))   # never re-stamp an in-flight gate
    assert latest_request_id(cfg, "moment_casting", "src_1") == rid1

def test_request_skipped_when_no_personas(tmp_path):
    # selection needs personas to differentiate; no account carries a persona -> no gate (heuristic territory).
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", persona="")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    assert not list(pending(cfg, kind="moment_casting"))


# ---- ingest -> generous affinities ----
def test_ingest_stamps_per_account_selection(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m0", "m1"], "@b": ["m2"]})
    assert led.moments["m0"].affinities == ["@a"]
    assert led.moments["m1"].affinities == ["@a"]
    assert led.moments["m2"].affinities == ["@b"]

def test_ingest_overlap_is_sorted_union(tmp_path):
    # a moment selected by BOTH accounts -> both in affinities (overlap allowed), sorted + deduped.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@b": ["m0"], "@a": ["m0", "m0"]})   # dup + cross-account
    assert led.moments["m0"].affinities == ["@a", "@b"]

def test_ingest_is_generous_no_count_cap(tmp_path):
    # the wired LLM selection has NO count cap by design: an account gets ALL its picks (the operator does not
    # want output capped for cost). There is no per-account budget knob — the heuristic's own `budget` arg is
    # the only cap and it is unwired.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")], moments=tuple(f"m{i}" for i in range(7)))
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": [f"m{i}" for i in range(7)]})
    assert {m.id for m in led.moments.values() if m.affinities == ["@a"]} == {f"m{i}" for i in range(7)}

def test_ingest_ignores_unknown_moment_and_inactive_handle(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m0", "nope"], "@ghost": ["m1"]})
    assert led.moments["m0"].affinities == ["@a"]
    assert led.moments["m1"].affinities == []     # @ghost is not an active account -> ignored
    assert "nope" not in led.moments              # a moment id that doesn't exist is silently skipped

def test_ingest_noop_without_response(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = ingest_moment_casting(led, cfg, "src_1", Accounts.load(cfg))   # no response written yet
    assert all(m.affinities == [] for m in led.moments.values())

def test_ingest_only_casts_decided_moments(tmp_path):
    # a still-picked (hookless) moment must not be cast — only decided moments are render/post candidates.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")])
    _moment(led, "m_picked", state=MomentState.picked); led.save(); led = Ledger.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m_picked"]})
    assert led.moments["m_picked"].affinities == []


# ---- re-decision discards the stale gate (amplify/re-pick safety) ----
def test_re_decision_discards_casting_gate(tmp_path):
    # A NEW pick decision (amplify/re-pick) reconciles the moment set; the prior per-source casting gate
    # must be DISCARDED so a fresh selection fires (else surviving moments keep stale affinities and new
    # moments never cast). Mirrors the moment_hooks discard in ingest_moments.
    from fanops.moments import request_moments, ingest_moments
    from fanops.models import MomentPick, MomentDecision
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg)); led.save()
    assert latest_request_id(cfg, "moment_casting", "src_1") is not None
    led = request_moments(Ledger.load(cfg), cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(
        MomentDecision(source_id="src_1", request_id=rid,
                       picks=[MomentPick(start=1.0, end=8.0, reason="fresh window")]).model_dump_json())
    ingest_moments(Ledger.load(cfg), cfg, "src_1")
    assert latest_request_id(cfg, "moment_casting", "src_1") is None   # discarded -> a fresh selection will fire


# ---- wiring: responder dispatch + prompt ----
def test_responder_answers_casting_gate(tmp_path):
    # the responder is wired (_SCHEMA/_PROMPT registered): a fake model answers the moment_casting gate, then
    # ingest applies it — proving the gate validates against MomentCastingDecision end-to-end (no live LLM).
    from fanops.responder import LlmResponder
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg)); led.save()
    def fake_model(kind, payload):
        assert kind == "moment_casting" and {p["handle"] for p in payload["personas"]} == {"@a", "@b"}
        return {"selections": {"@a": ["m0"], "@b": ["m1", "m2"]}}
    LlmResponder(cfg, model=fake_model).answer_pending(cfg)
    led = ingest_moment_casting(Ledger.load(cfg), cfg, "src_1", Accounts.load(cfg))
    assert led.moments["m0"].affinities == ["@a"]
    assert led.moments["m1"].affinities == ["@b"] and led.moments["m2"].affinities == ["@b"]

def test_moment_casting_prompt_builds():
    from fanops.prompts import moment_casting_prompt
    out = moment_casting_prompt({"moments": [{"moment_id": "m0", "reason": "guitar solo", "start": 0, "end": 7,
                                              "signal_score": 1.0, "hook": "watch this"}],
                                 "personas": [{"handle": "@a", "persona": "guitar nerd"}], "language": "en"})
    assert "@a" in out and "m0" in out and "GENEROUS" in out.upper()


def _req_path(cfg, source_id):
    from fanops.agentstep import request_path
    return request_path(cfg, "moment_casting", source_id)


# ---- M4b: the LLM gate's selection lands a durable selection FACT (method=llm) ----
def test_ingest_writes_llm_selection_facts(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m0", "m1"], "@b": ["m2"]})
    fa = {f.moment_id: f for f in led.selection_facts_of_account("@a")}
    assert set(fa) == {"m0", "m1"}                                # one fact per LLM-selected (account, moment)
    assert fa["m0"].method == "llm" and fa["m0"].reason == "r"    # the moment's editorial reason is the WHY
    assert fa["m0"].overlap is None and fa["m0"].rank is None and fa["m0"].signal is None   # LLM-chosen: no heuristic score
    assert fa["m0"].source_id == "src_1" and fa["m0"].created_at is not None
    fb = led.selection_facts_of_account("@b")
    assert len(fb) == 1 and fb[0].moment_id == "m2" and fb[0].method == "llm"


# ---- MOM-2: a persona-bearing candidate given ZERO picks becomes an EXPLICIT, labeled state (no silent vanish) ----
def test_persona_bearing_zero_pick_emits_breadcrumb_not_silent(tmp_path):
    # @a is picked; @b (persona-bearing, in the brief) gets ZERO moments from the selector. @b must NOT silently
    # vanish: a labeled breadcrumb names it (the operator can cast manually) and NO auto-fan record is written
    # (the no-fan-leak contract). An account that WAS picked is not named.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "hype"), _acct("@b", "lyric", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m0", "m1"], "@b": []})   # @b: zero picks
    src = led.sources["src_1"]
    assert src.degraded_reason and "@b" in src.degraded_reason            # @b named, VISIBLE
    assert "@a" not in (src.degraded_reason or "")                       # @a was picked -> not named
    assert led.account_selection_for("src_1", "@b") is None              # NO auto-fan record (no-fan-leak)
    assert led.account_selection_for("src_1", "@a") is not None          # @a got its real selection


def test_zero_cast_candidate_shows_review_badge(tmp_path):
    # The same zero-pick @b surfaces a "0 cast" badge in the Review lane (cast_count 0 + zero_cast True), while a
    # picked @a does not. OFF byte-identity: with no chosen selection on the source the badge never fires.
    from datetime import datetime, timezone
    from fanops.studio.views_review import account_lanes
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [_acct("@a", "hype"), _acct("@b", "lyric", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_and_ingest(led, cfg, {"@a": ["m0"], "@b": []})
    led.save(); led = Ledger.load(cfg)
    lanes = account_lanes(led, Accounts.load(cfg), cfg, source_id="src_1", now=datetime.now(timezone.utc))
    lane_b = next(ln for ln in lanes.lanes if ln.account == "@b")
    assert lane_b.cast_count == 0 and lane_b.zero_cast is True           # the explicit labeled state
    lane_a = next(ln for ln in lanes.lanes if ln.account == "@a")
    assert lane_a.zero_cast is False                                     # @a was picked -> not flagged
