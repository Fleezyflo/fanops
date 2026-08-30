"""Duration-machine kill + omit pick poem — certification tests for the operator unit."""
from __future__ import annotations
import inspect
import json

import pytest

from fanops.accounts import Accounts, Account, _hydrate_from_personas
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentPick
from fanops.moments import validate_pick, _persona_entry, _pick_personas, request_moment_hooks
from fanops.personas import Persona, add_persona, compose_breakdown, produces_summary
from fanops.prompts import moment_pick_prompt
from fanops.clip import render_moment, fit_window
from tests.test_moments import _src


def _write_personas(cfg, personas):
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(json.dumps({"personas": personas}))


def _write_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


# 1. Hydrate does not call derive_cut_spec; cut_policy does not stamp medium / 16–26.
def test_hydrate_skips_derive_cut_spec(tmp_path):
    cfg = Config(root=tmp_path)
    pid = add_persona(cfg, name="Emo", voice="v", cut_policy=["emotional"], niche=["hiphop"])
    _write_accounts(cfg, [{"handle": "a", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "persona_id": pid, "clip_profile": "talk"}])
    src = inspect.getsource(_hydrate_from_personas)
    assert "derive_cut_spec" not in src and "resolved_cut_spec" not in src
    accts = Accounts.load(cfg)
    a = accts.accounts[0]
    assert a.cut_policy == ["emotional"]
    assert a.clip_profile == "talk"          # account pin stands — not overwritten to medium
    assert a.persona_owns_profile is False


def test_fit_window_default_has_no_talk_floor():
    # The primitive itself must not grow a short pick to 12s when callers omit lo/hi.
    assert fit_window(10.0, 13.0, 120.0) == (10.0, 13.0)
    assert fit_window(10.0, 40.0, 120.0) == (10.0, 40.0)


# 2. Three fit_window sites pass explicit non-band EOF clamp (lo=0, hi=duration|inf).
@pytest.mark.parametrize("site", ["render_moment", "render_account_cut", "request_moment_hooks"])
def test_fit_window_sites_eof_clamp_only(tmp_path, mocker, site):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _src(led, cfg, dur=60.0)
    (cfg.sources / "src_1.mp4").parent.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_1.mp4").write_bytes(b"\x00")
    led.add_moment(__import__("fanops.models", fromlist=["Moment"]).Moment(
        id="mom_1", parent_id="src_1", content_token="14.00-18.00",
        start=14.0, end=18.0, reason="r", state=__import__("fanops.models", fromlist=["MomentState"]).MomentState.picked))
    spy = mocker.patch("fanops.clip.fit_window", wraps=fit_window)
    if site == "render_moment":
        mocker.patch("fanops.clip.subprocess.run", return_value=type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
        mocker.patch("fanops.clip.render_reframed", return_value=type("R", (), {"returncode": 0})())
        from fanops.models import Fmt, MomentState
        led.moments["mom_1"] = led.moments["mom_1"].model_copy(update={"state": MomentState.decided})
        try:
            render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
        except Exception:
            pass
        assert spy.called
        kw = spy.call_args.kwargs
        assert kw.get("lo") == 0.0 and kw.get("hi") == 60.0
    elif site == "render_account_cut":
        from fanops.clip import render_account_cut
        from fanops.models import Fmt
        mocker.patch("fanops.clip.render_reframed", return_value=type("R", (), {"returncode": 0})())
        render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                           hook="H", out_path=str(tmp_path / "out.mp4"))
        kw = spy.call_args.kwargs
        assert kw.get("lo") == 0.0 and kw.get("hi") == 60.0
    else:
        spy_m = mocker.patch("fanops.moments.fit_window", wraps=fit_window)
        mocker.patch("fanops.moments.extract_keyframes", return_value=[])
        mocker.patch("fanops.moments.latest_request_id", return_value=None)
        mocker.patch("fanops.moments.write_request")
        mocker.patch("fanops.transcribe.window_has_trusted_speech", return_value=True)
        led.moments["mom_1"] = led.moments["mom_1"].model_copy(update={"start": 14.0, "end": 22.0})
        request_moment_hooks(led, cfg, "src_1")
        kw = spy_m.call_args.kwargs
        assert kw.get("lo") == 0.0 and kw.get("hi") == 60.0


def test_moment_pick_prompt_includes_persona_lens(tmp_path):
    cfg = Config(root=tmp_path)
    accts = Accounts.load(cfg)
    accts.accounts = [Account(handle="trust", account_id="1", platforms=["instagram"], status="active",
                              persona="underground fan voice", content_focus="bars only",
                              cut_policy=["emotional"], selection_scope="credibility_first",
                              hook_angle="curiosity")]
    entry = _persona_entry(cfg, accts.accounts[0])
    assert entry["directive"]
    assert "16-26" in entry["band"]
    assert entry["selection_scope"]
    p = moment_pick_prompt({"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en",
                            "guidance": "", "personas": [entry]})
    assert "select_rule=" in p
    assert "band=" in p
    assert "16-26" in p
    assert "owning persona" in p.lower()


def test_pick_personas_opens_gates_without_directive(tmp_path):
    cfg = Config(root=tmp_path)
    accts = Accounts.load(cfg)
    accts.accounts = [Account(handle="a", account_id="1", platforms=["instagram"], status="active",
                              persona="voice only")]
    assert len(_pick_personas(cfg, accts)) == 1


# 5. validate_pick rejects <=6s; 0.51s does not ingest.
def test_validate_pick_rejects_six_seconds_or_less():
    assert validate_pick(MomentPick(start=10.0, end=10.5, reason="r"), duration=20.0) is not None
    assert validate_pick(MomentPick(start=10.0, end=16.1, reason="r"), duration=20.0) is None
    assert validate_pick(MomentPick(start=10.0, end=16.0, reason="r"), duration=20.0) is not None


# 6. produces_summary / compose contain no length-band tokens.
def test_compose_and_produces_summary_no_length_tokens(tmp_path):
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="v", cut_policy=["emotional", "storytelling"], hook_angle="curiosity",
                hashtag_corpus=["#tag"])
    d = compose_breakdown(cfg, p)
    blob = json.dumps(d) + " ".join(produces_summary(d))
    for token in ("8-15", "16-26", "12-22", "short", "medium", "talk"):
        assert token not in blob


# 7. No new keys on Persona, Account, or Moment.
def test_no_new_model_fields():
    from fanops.models import Moment
    from fanops.accounts import Account
    for forbidden in ("pick_lens", "recipe", "open", "shape", "hook_burn"):
        assert forbidden not in Persona.model_fields
        assert forbidden not in Account.model_fields
        assert forbidden not in Moment.model_fields


# 8. FANOPS_VISUAL_START still global; pick still offers segments.
def test_visual_start_global_and_segments_rule_remain(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)
    cfg = Config(root=tmp_path)
    assert cfg.visual_start is True
    p = moment_pick_prompt({"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""})
    assert "`segments`" in p and "dead air" in p.lower()
