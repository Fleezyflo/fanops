# tests/test_hook_authorship.py — the root fix: the frame-seeing MOMENT author (Opus) owns ALL on-screen
# hook authorship, including per-account variants keyed by handle. The blind caption gate (Sonnet) writes
# NO hook. M1b: the hook (and its per-account variants) is authored in the PASS-2 moment_hooks gate, which
# sees each picked window's OWN frames — so per-account authorship lives on MomentHookDecision /
# request_moment_hooks / ingest_moment_hooks now, not the pick gate.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (MomentPick, MomentHookDecision, Moment, Source, MomentDecision, MomentState,
                           SourceState, Platform)
from fanops.accounts import Accounts, Account, AccountStatus
from fanops.moments import request_moments, ingest_moments, request_moment_hooks, ingest_moment_hooks
from fanops.agentstep import request_path, response_path, latest_request_id
from fanops.prompts import moment_hook_prompt

def _accts(cfg, handles_personas):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h, platforms=[Platform.instagram],
                          status=AccountStatus.active, persona=p) for (h, p) in handles_personas]
    return a

def _seed_src(cfg, dur=60.0):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.signalled, language="en", duration=dur,
                          signal_peaks=[{"t": 16.0, "kind": "scene_cut", "score": 0.6}]))
    return led

def _decide_one_hook(led, cfg, source_id, token, *, hook=None, hooks_by_persona=None, accounts=None):
    """Two-pass driver for ONE pick: open the hook gates, answer the named pick's gate, then ingest."""
    from fanops.responder import screen_model_text
    led = request_moment_hooks(led, cfg, source_id, accounts=accounts)
    key = f"{source_id}.{token}"
    rid = latest_request_id(cfg, "moment_hooks", key)
    dec = screen_model_text(MomentHookDecision(request_id=rid, hook=hook, hooks_by_persona=hooks_by_persona or {}))
    response_path(cfg, "moment_hooks", key).write_text(dec.model_dump_json())
    return ingest_moment_hooks(led, cfg, source_id)

# ---- the hook DECISION carries per-persona (handle-keyed) hooks ----

def test_moment_hook_decision_carries_hooks_by_persona():
    d = MomentHookDecision(request_id="r", hook="x", hooks_by_persona={"markmakmouly": "watch the craft"})
    assert d.hooks_by_persona["markmakmouly"] == "watch the craft"

def test_moment_hook_decision_defaults_empty_hooks_by_persona():
    d = MomentHookDecision(request_id="r")               # old responses (no key) still validate
    assert d.hooks_by_persona == {}

def test_moment_carries_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r",
               hooks_by_persona={"markmakmouly": "x"})
    assert m.hooks_by_persona == {"markmakmouly": "x"}

def test_moment_defaults_empty_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r")   # old ledger rows load fine
    assert m.hooks_by_persona == {}

# ---- moment_hook_prompt asks for one frame-grounded hook PER HANDLE, in that persona's voice ----

def _payload(**extra):
    base = {"start": 10.0, "end": 28.0, "reason": "r", "transcript_excerpt": "",
            "language": "en", "guidance": "", "frames": [], "signal_peaks": []}
    base.update(extra)
    return base

def test_moment_hook_prompt_asks_for_per_persona_hooks_when_personas_present():
    out = moment_hook_prompt(_payload(personas=[
        {"handle": "markmakmouly", "persona": "champions craft, watch-for-the-craft angle"},
        {"handle": "perca.late", "persona": "underground raw, no-frills street attitude"}]))
    assert "hooks_by_persona" in out                      # the author is told to RETURN the per-handle map
    assert "markmakmouly" in out and "perca.late" in out  # keyed by handle
    assert "champions craft" in out                       # the persona voice reaches the frame-seeing author

def test_moment_hook_prompt_byte_identical_without_personas():
    out = moment_hook_prompt(_payload())                  # no personas key -> no block (back-compat)
    assert "hooks_by_persona" not in out

# ---- request_moment_hooks threads active personas; ingest_moment_hooks persists hooks_by_persona ----

def _pick(led, cfg, source_id="src_1", token="10.00-28.00", start=10.0, end=28.0):
    request_moments(led, cfg, source_id)
    rid = latest_request_id(cfg, "moments", source_id)
    response_path(cfg, "moments", source_id).write_text(MomentDecision(
        source_id=source_id, request_id=rid,
        picks=[MomentPick(start=start, end=end, reason="r")]).model_dump_json())
    return ingest_moments(led, cfg, source_id)

def test_request_moment_hooks_threads_active_personas(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg)
    accts = _accts(cfg, [("markmakmouly", "champions craft"), ("perca.late", "underground raw")])
    led = request_moment_hooks(led, cfg, "src_1", accounts=accts)
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.10.00-28.00").read_text())
    assert {p["handle"] for p in req["personas"]} == {"markmakmouly", "perca.late"}
    assert {p["persona"] for p in req["personas"]} == {"champions craft", "underground raw"}

def test_request_moment_hooks_floor_slot_when_account_has_no_persona(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg)
    led = request_moment_hooks(led, cfg, "src_1", accounts=_accts(cfg, [("a", None)]))
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.10.00-28.00").read_text())
    assert len(req["personas"]) == 1 and req["personas"][0]["handle"] == "a"   # floor slot, not omitted

def test_request_moment_hooks_no_personas_without_accounts(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg)
    led = request_moment_hooks(led, cfg, "src_1")          # accounts=None (legacy)
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.10.00-28.00").read_text())
    assert req["personas"] == []

def test_ingest_moment_hooks_persists_hooks_by_persona(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg)
    led = _decide_one_hook(led, cfg, "src_1", "10.00-28.00", hook="the part you'll replay",
                           hooks_by_persona={"markmakmouly": "watch the craft", "perca.late": "raw bars no polish"})
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided
    assert m.hooks_by_persona == {"markmakmouly": "watch the craft", "perca.late": "raw bars no polish"}

def test_persona_hooks_screened_at_responder_boundary(tmp_path):
    # MOL-166: em-dash sanitization happens at the responder write boundary (screen_model_text), not in ingest.
    import inspect
    from fanops import moments as moments_mod
    assert "sanitize_generated_text" not in inspect.getsource(moments_mod.ingest_moment_hooks)
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg)
    led = _decide_one_hook(led, cfg, "src_1", "10.00-28.00", hook="the part you'll replay",
                           hooks_by_persona={"markmakmouly": "watch the craft — closely", "perca.late": "  "})
    m = led.moments_of("src_1")[0]
    assert m.hooks_by_persona["markmakmouly"] == "watch the craft, closely"   # screened before ingest
    assert "perca.late" not in m.hooks_by_persona                             # blank dropped at ingest gate

from fanops.personas import hook_author_slot

def test_hook_author_slot_floor():
    a = Account(handle="tiktokfan", account_id="1", platforms=[Platform.tiktok], status=AccountStatus.active, persona=None)
    assert "tiktokfan" in hook_author_slot(a)
