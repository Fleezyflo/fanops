# tests/test_hook_authorship.py — the root fix: the frame-seeing MOMENT author (Opus) owns ALL on-screen
# hook authorship, including per-account variants keyed by handle. The blind caption gate (Sonnet) writes
# NO hook. RED until the schema fields + moment_prompt per-persona block land.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentPick, Moment, Source, MomentDecision, Platform
from fanops.accounts import Accounts, Account, AccountStatus
from fanops.moments import request_moments, ingest_moments
from fanops.agentstep import request_path, response_path, latest_request_id
from fanops.prompts import moment_prompt

def _accts(cfg, handles_personas):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h, platforms=[Platform.instagram],
                          status=AccountStatus.active, persona=p) for (h, p) in handles_personas]
    return a

def _seed_src(cfg, dur=60.0):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"), language="en", duration=dur))
    return led

# ---- Task 1: schema carries per-persona (handle-keyed) hooks ----

def test_momentpick_carries_hooks_by_persona():
    p = MomentPick(start=0, end=5, reason="r", hooks_by_persona={"markmakmouly": "watch the craft"})
    assert p.hooks_by_persona["markmakmouly"] == "watch the craft"

def test_momentpick_defaults_empty_hooks_by_persona():
    p = MomentPick(start=0, end=5, reason="r")           # old responses (no key) still validate
    assert p.hooks_by_persona == {}

def test_moment_carries_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r",
               hooks_by_persona={"markmakmouly": "x"})
    assert m.hooks_by_persona == {"markmakmouly": "x"}

def test_moment_defaults_empty_hooks_by_persona():
    m = Moment(id="m1", parent_id="s1", start=0, end=5, reason="r")   # old ledger rows load fine
    assert m.hooks_by_persona == {}

# ---- Task 1: moment_prompt asks for one frame-grounded hook PER HANDLE, in that persona's voice ----

def _payload(**extra):
    base = {"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""}
    base.update(extra)
    return base

def test_moment_prompt_asks_for_per_persona_hooks_when_personas_present():
    out = moment_prompt(_payload(personas=[
        {"handle": "markmakmouly", "persona": "champions craft, watch-for-the-craft angle"},
        {"handle": "perca.late", "persona": "underground raw, no-frills street attitude"}]))
    assert "hooks_by_persona" in out                     # the author is told to RETURN the per-handle map
    assert "markmakmouly" in out and "perca.late" in out  # keyed by handle
    assert "champions craft" in out                       # the persona voice reaches the frame-seeing author

def test_moment_prompt_byte_identical_without_personas():
    out = moment_prompt(_payload())                       # no personas key -> no block (back-compat)
    assert "hooks_by_persona" not in out

# ---- Task 2: request_moments threads active personas; ingest_moments persists hooks_by_persona ----

def test_request_moments_threads_active_personas(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    accts = _accts(cfg, [("markmakmouly", "champions craft"), ("perca.late", "underground raw")])
    request_moments(led, cfg, "src_1", accounts=accts)
    req = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert {p["handle"] for p in req["personas"]} == {"markmakmouly", "perca.late"}
    assert {p["persona"] for p in req["personas"]} == {"champions craft", "underground raw"}

def test_request_moments_no_personas_when_account_has_none(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    request_moments(led, cfg, "src_1", accounts=_accts(cfg, [("a", None)]))
    req = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert req["personas"] == []                           # no persona -> empty -> byte-identical prompt

def test_request_moments_no_personas_without_accounts(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    request_moments(led, cfg, "src_1")                     # accounts=None (legacy)
    req = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert req["personas"] == []

def test_ingest_moments_persists_hooks_by_persona(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    pick = MomentPick(start=10, end=28, reason="r",
                      hooks_by_persona={"markmakmouly": "watch the craft", "perca.late": "raw bars no polish"})
    response_path(cfg, "moments", "src_1").write_text(
        MomentDecision(source_id="src_1", request_id=rid, picks=[pick]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    m = led.moments_of("src_1")[0]
    assert m.hooks_by_persona == {"markmakmouly": "watch the craft", "perca.late": "raw bars no polish"}

def test_ingest_sanitizes_persona_hooks(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_src(cfg)
    request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    pick = MomentPick(start=10, end=28, reason="r",
                      hooks_by_persona={"markmakmouly": "watch the craft — closely", "perca.late": "  "})
    response_path(cfg, "moments", "src_1").write_text(
        MomentDecision(source_id="src_1", request_id=rid, picks=[pick]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    m = led.moments_of("src_1")[0]
    assert m.hooks_by_persona["markmakmouly"] == "watch the craft, closely"   # em-dash sanitized
    assert "perca.late" not in m.hooks_by_persona                             # blank dropped
