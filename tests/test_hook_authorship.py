# tests/test_hook_authorship.py — P6/P7: ONE hook on m.hook; hooks_by_persona removed (MOL-148).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (MomentPick, MomentHookDecision, Moment, Source, MomentDecision, MomentState,
                           SourceState, Platform)
from fanops.accounts import Accounts, Account, AccountStatus
from fanops.moments import request_moments, ingest_moments, request_moment_hooks, ingest_moment_hooks
from fanops.agentstep import request_path, response_path, latest_request_id
from fanops.prompts import moment_hook_prompt
from fanops.responder import screen_model_text
from tests.fixtures.speech_segments import talk_seg

def _accts(cfg, handles_personas):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h, platforms=[Platform.instagram],
                          status=AccountStatus.active, persona=p) for (h, p) in handles_personas]
    return a

def _seed_src(cfg, dur=60.0):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.signalled, language="en", duration=dur,
                          transcript=[talk_seg("they slept on me here", start=10.0, end=28.0)],
                          signal_peaks=[{"t": 16.0, "kind": "scene_cut", "score": 0.6}],
                          meta={"transcribed": True}))
    return led

def _pick(led, cfg, owner=None):
    request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    pick = MomentPick(start=10, end=28, reason="r")
    if owner: pick = pick.model_copy(update={"personas": [owner]})
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid, picks=[pick]).model_dump_json())
    return ingest_moments(led, cfg, "src_1")

def test_moment_hook_decision_has_no_hooks_by_persona_field():
    assert "hooks_by_persona" not in MomentHookDecision.model_fields

def test_moment_has_no_hooks_by_persona_field():
    assert "hooks_by_persona" not in Moment.model_fields

def test_moment_hook_prompt_no_hooks_by_persona_map():
    p = {"start": 10.0, "end": 28.0, "reason": "r", "transcript_excerpt": "", "language": "en",
         "guidance": "", "frames": [], "signal_peaks": [],
         "personas": [{"handle": "markmakmouly", "persona": "craft angle"}]}
    out = moment_hook_prompt(p)
    assert "hooks_by_persona" not in out and "markmakmouly" in out

def test_request_moment_hooks_sends_owner_only(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg, owner="markmakmouly")
    led = request_moment_hooks(led, cfg, "src_1", accounts=_accts(cfg, [("markmakmouly", "craft"), ("other", "x")]))
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00").read_text())
    assert len(req["personas"]) == 1 and req["personas"][0]["handle"] == "markmakmouly"

def test_ingest_moment_hooks_persists_m_hook_only(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg, owner="markmakmouly")
    led = request_moment_hooks(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00")
    dec = screen_model_text(MomentHookDecision(request_id=rid, hook="the part you'll replay"))
    response_path(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00").write_text(dec.model_dump_json())
    led = ingest_moment_hooks(led, cfg, "src_1")
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided and m.hook == "the part you'll replay"

# ---- null hook clean promotion (MOL-476 retry machinery removed) ------------------------------------
def test_moment_hook_prompt_allows_null():
    p = {"start": 10.0, "end": 28.0, "reason": "r", "transcript_excerpt": "", "language": "en",
         "guidance": "", "frames": [], "signal_peaks": [],
         "personas": [{"handle": "markmakmouly", "persona": "craft angle"}]}
    out = moment_hook_prompt(p)
    assert "hook: null" in out and "set dressing" in out

def test_moment_hook_prompt_forbids_null_license():
    p = {"start": 10.0, "end": 28.0, "reason": "r", "transcript_excerpt": "", "language": "en",
         "guidance": "", "frames": [], "signal_peaks": [],
         "personas": [{"handle": "markmakmouly", "persona": "craft angle"}]}
    out = moment_hook_prompt(p)
    assert "ships CLEAN (return hook = null)" not in out
    assert "better CLEAN (hook = null)" not in out

def test_null_hook_promotes_clean_to_decided(tmp_path):
    # Test 4: null author hook promotes CLEAN to decided (hook=None). MOL-476 retry is gone — a null
    # hook is no longer a retry signal; ingest_moment_hooks promotes it cleanly same pass.
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg, owner="markmakmouly")
    led = request_moment_hooks(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00")
    dec = screen_model_text(MomentHookDecision(request_id=rid, hook=None))
    response_path(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00").write_text(dec.model_dump_json())
    led = ingest_moment_hooks(led, cfg, "src_1")
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided and m.hook is None
    assert led.sources["src_1"].state is SourceState.moments_decided

def test_null_hook_never_errors_source(tmp_path):
    # Regression: a null hook must NEVER drive the source to SourceState.error (the MOL-476 path is gone).
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    led = _pick(led, cfg, owner="markmakmouly")
    led = request_moment_hooks(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00")
    dec = screen_model_text(MomentHookDecision(request_id=rid, hook=None))
    response_path(cfg, "moment_hooks", "src_1.markmakmouly.10.00-28.00").write_text(dec.model_dump_json())
    led = ingest_moment_hooks(led, cfg, "src_1")
    assert led.sources["src_1"].state is SourceState.moments_decided
    assert led.sources["src_1"].state is not SourceState.error
