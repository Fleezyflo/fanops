# tests/test_moment_hook_learning.py — P4(c): proven hook STYLES into the moment (vision author) prompt.
import json
from fanops.agentstep import request_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, SourceState
from fanops.accounts import Accounts, Account, AccountStatus
from fanops.moment_hook_learning import proven_hook_styles
from fanops.moments import request_moments
from fanops.prompts import moment_prompt


def _vpost(led, pid, account, hook, lift, platform=Platform.instagram):
    led.add_post(Post(id=pid, parent_id="c1", account=account, account_id="1", platform=platform,
                      caption="x", state=PostState.analyzed, variant_key=f"vk_{pid}", variant_hook=hook,
                      metrics={"lift_score": lift}))

def _gated_winner(led, account, hook, platform=Platform.instagram):
    # 3 posts of `hook` (>= variant_min_posts) at high lift + 2 LOSE far below -> best_hooks fires.
    for i in range(3): _vpost(led, f"{account}{platform.value}w{i}", account, hook, 100.0, platform)
    for i in range(2): _vpost(led, f"{account}{platform.value}l{i}", account, "LOSE", 5.0, platform)

def _accts(cfg, *specs):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id="1", platforms=list(plats), status=AccountStatus.active)
                  for h, plats in specs]
    return a

def _on(monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "on")
    monkeypatch.setenv("FANOPS_MOMENT_HOOK_LEARNING", "on")

def _signalled_source(led, sid="s1"):
    led.add_source(Source(id=sid, source_path="x.mp4", state=SourceState.signalled, duration=30.0,
                          transcript=[], signal_peaks=[], language="en"))


# ---- C1: proven_hook_styles — gated cross-surface union, dual-flag, fail-open ----
def test_proven_hook_styles_unions_gated_winners(tmp_path, monkeypatch):
    _on(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _gated_winner(led, "@a", "WIN_A"); _gated_winner(led, "@b", "WIN_B")
    accts = _accts(cfg, ("@a", [Platform.instagram]), ("@b", [Platform.instagram]))
    assert proven_hook_styles(led, cfg, accts) == ["WIN_A", "WIN_B"]   # ordered, de-duped union

def test_proven_hook_styles_master_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    monkeypatch.setenv("FANOPS_MOMENT_HOOK_LEARNING", "on")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _gated_winner(led, "@a", "WIN_A")
    assert proven_hook_styles(led, cfg, _accts(cfg, ("@a", [Platform.instagram]))) == []

def test_proven_hook_styles_moment_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "on")
    monkeypatch.delenv("FANOPS_MOMENT_HOOK_LEARNING", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _gated_winner(led, "@a", "WIN_A")
    assert proven_hook_styles(led, cfg, _accts(cfg, ("@a", [Platform.instagram]))) == []

def test_proven_hook_styles_none_accounts(tmp_path, monkeypatch):
    _on(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    assert proven_hook_styles(led, cfg, None) == []

def test_proven_hook_styles_fail_open(tmp_path, monkeypatch, mocker):
    _on(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg); _gated_winner(led, "@a", "WIN_A")
    mocker.patch("fanops.moment_hook_learning.best_hooks", side_effect=RuntimeError("boom"))
    assert proven_hook_styles(led, cfg, _accts(cfg, ("@a", [Platform.instagram]))) == []   # logged + []

def test_proven_hook_styles_uses_ucb_when_variant_ucb_on(tmp_path, monkeypatch, mocker):
    # reuses caption.py's scorer selection: variant_ucb on -> ucb_rank, off -> best_hooks.
    _on(monkeypatch); monkeypatch.setenv("FANOPS_VARIANT_UCB", "on")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    ucb = mocker.patch("fanops.moment_hook_learning.ucb_rank", return_value=["UCB_WIN"])
    bh = mocker.patch("fanops.moment_hook_learning.best_hooks", return_value=["GREEDY"])
    out = proven_hook_styles(led, cfg, _accts(cfg, ("@a", [Platform.instagram])))
    assert out == ["UCB_WIN"] and ucb.called and not bh.called


# ---- C3: moment_prompt renders the STYLE block; absent/empty -> byte-identical ----
_BASE = {"duration": 30.0, "language": "en", "transcript": [], "signal_peaks": []}

def test_moment_prompt_renders_learned_hooks_block():
    withh = moment_prompt({**_BASE, "learned_hooks": ["WIN_A"]})
    assert "WIN_A" in withh and "do NOT copy verbatim" in withh
    assert "WIN_A" not in moment_prompt(_BASE)

def test_moment_prompt_absent_or_empty_learned_hooks_is_byte_identical():
    base = moment_prompt(_BASE)
    assert moment_prompt({**_BASE, "learned_hooks": []}) == base
    assert moment_prompt({**_BASE, "learned_hooks": None}) == base


# ---- C5: request_moments injects the learned_hooks KEY; off/None -> no key (byte-identical) ----
def test_request_moments_injects_learned_hooks_key(tmp_path, monkeypatch):
    _on(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _gated_winner(led, "@a", "WIN_A"); _signalled_source(led)
    led = request_moments(led, cfg, "s1", accounts=_accts(cfg, ("@a", [Platform.instagram])))
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert payload["learned_hooks"] == ["WIN_A"]
    assert "guidance" in payload                            # base guidance key untouched (learned_hooks is separate)

def test_request_moments_no_key_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_MOMENT_HOOK_LEARNING", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _gated_winner(led, "@a", "WIN_A"); _signalled_source(led)
    led = request_moments(led, cfg, "s1", accounts=_accts(cfg, ("@a", [Platform.instagram])))
    assert "learned_hooks" not in json.loads(request_path(cfg, "moments", "s1").read_text())

def test_request_moments_no_key_when_accounts_none(tmp_path, monkeypatch):
    _on(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg); _signalled_source(led)
    led = request_moments(led, cfg, "s1")                   # accounts=None default
    assert "learned_hooks" not in json.loads(request_path(cfg, "moments", "s1").read_text())
