# tests/test_account_framing.py — M2c: per-account FRAMING (Account.framing + Config.resolve_top_bias
# + the set_framing mutator), wired into the per-account render CUT. An account can pin its vertical
# crop bias ("top" = head-safe upper-third, "center" = default) independent of the GLOBAL aware_reframe;
# when the resolved framing differs from the global, the account's Render becomes a real per-account CUT
# (its own framing) — content-addressed with a frame tag so two accounts sharing one hook+band but a
# different crop never collide on one file. An account inheriting the global framing is byte-identical.
import json
import pytest
from pathlib import Path
from fanops.config import Config, FRAMING_NAMES
from fanops.accounts import Accounts, set_framing, add_account
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
from fanops.crosspost import crosspost_clips
from fanops.ids import child_id


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, **extra):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active", **extra}


# ---------------------------------------------------------------- FRAMING_NAMES + Account.framing ----
def test_framing_names_exported():
    assert FRAMING_NAMES == {"top", "center"}                  # the validatable set the write boundary enforces

def test_account_framing_defaults_none(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])
    assert Accounts.load(cfg).accounts[0].framing is None       # absent field -> None (additive, no migration)

def test_account_framing_persists_when_set(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="top")])
    assert Accounts.load(cfg).accounts[0].framing == "top"

def test_load_unknown_framing_does_not_crash(tmp_path):
    # fail-open: a hand-edited/legacy unknown framing reloads (resolve_top_bias ignores it -> global).
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="weird")])
    assert Accounts.load(cfg).accounts[0].framing == "weird"    # persisted, inert downstream


# ---------------------------------------------------------------- Config.resolve_top_bias ----
def test_resolve_top_bias_top_account_true(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="top")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is True                      # account pins head-safe top crop

def test_resolve_top_bias_center_account_overrides_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")             # global ON...
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="center")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False                     # ...but the account pins center -> override wins

def test_resolve_top_bias_none_account_falls_back_to_global(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    assert cfg.resolve_top_bias(None) is False                  # default global aware_reframe OFF
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    assert Config(root=tmp_path).resolve_top_bias(None) is True  # global flows through

def test_resolve_top_bias_unset_account_inherits_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])      # no per-account framing
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is True                      # inherits the global

def test_resolve_top_bias_blank_falls_back(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="  ")])  # whitespace-only is no override
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False

def test_resolve_top_bias_unknown_falls_back(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a", framing="weird")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False                     # unknown is inert -> global (validate-or-default on read)


# ---------------------------------------------------------------- set_framing mutator ----
def test_set_framing_sets_and_clears_preserving_siblings(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@a", note="keep me"),
                {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"}])
    assert set_framing(cfg, "@a", "top") == "@a"
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "@a")
    assert a["framing"] == "top" and a["note"] == "keep me"     # set + sibling/unknown field intact
    set_framing(cfg, "@a", "")                                  # blank clears
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "@a")
    assert a["framing"] is None
    b = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "@b")
    assert b["account_id"] == "x"                               # sibling untouched throughout

def test_set_framing_center(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])
    set_framing(cfg, "@a", "center")
    assert Accounts.load(cfg).accounts[0].framing == "center"

def test_set_framing_rejects_unknown(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])
    with pytest.raises(ValueError):
        set_framing(cfg, "@a", "diagonal")                     # not a known framing -> never written

def test_set_framing_unknown_handle_raises(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])
    with pytest.raises(KeyError):
        set_framing(cfg, "@nope", "top")

def test_set_framing_round_trips_through_resolve(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("@a")])
    set_framing(cfg, "@a", "top")
    assert cfg.resolve_top_bias(Accounts.load(cfg).accounts[0]) is True

def test_add_account_with_framing_persists(tmp_path):
    cfg = Config(root=tmp_path)
    assert add_account(cfg, "@a", ["instagram"], framing="top") == "@a"
    assert Accounts.load(cfg).accounts[0].framing == "top"

def test_add_account_rejects_unknown_framing(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_account(cfg, "@a", ["instagram"], framing="weird")


# ---------------------------------------------------------------- crosspost wiring (integration) ----
def _seed_clip(led, cfg, *, hooks_by_persona, surfaces):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hooks_by_persona=hooks_by_persona))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _patch_cut(mocker, *, returns=True):
    calls = []
    def cut(led, cfg, moment_id, *, aspect, profile, hook, out_path, top_bias=False):
        calls.append({"profile": profile, "hook": hook, "out_path": out_path, "top_bias": top_bias})
        if returns:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True); Path(out_path).write_bytes(b"ACUT")
        return returns
    mocker.patch("fanops.crosspost.render_account_cut", side_effect=cut)
    return calls

def _patch_burn(mocker):
    calls = []
    def burn(base, out, hook, **kw):
        calls.append({"out": out}); Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"BURN"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)
    return calls

def _run(cfg):
    return crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")


def test_framing_override_triggers_cut_with_top_bias(tmp_path, monkeypatch, mocker):
    # global aware_reframe OFF (default); the account pins framing=top -> a real per-account CUT at top_bias=True,
    # even though its LENGTH band is the global one (only the framing differs).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@top", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@top": "H"}, surfaces=("@top/instagram",)); led.save()
    led = _run(cfg)
    assert len(cut_calls) == 1 and cut_calls[0]["top_bias"] is True
    assert cut_calls[0]["profile"] == "talk"                    # same global LENGTH; only framing diverges
    assert burn_calls == []                                     # the shared-clip burn path was NOT used

def test_framing_none_account_no_cut_byte_identical(tmp_path, monkeypatch, mocker):
    # account framing None + global band -> wants_cut False -> shared-clip burn, un-tagged id (byte-identical)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@a")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "H"}, surfaces=("@a/instagram",)); led.save()
    led = _run(cfg)
    assert cut_calls == [] and len(burn_calls) == 1
    assert next(iter(led.posts.values())).render_id == child_id("render", "clip_1", "H")   # un-tagged

def test_framing_center_account_no_cut_when_global_off(tmp_path, monkeypatch, mocker):
    # an account pinning framing=center while the global is OFF resolves to the SAME framing -> NO cut.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@c", framing="center")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@c": "H"}, surfaces=("@c/instagram",)); led.save()
    led = _run(cfg)
    assert cut_calls == [] and len(burn_calls) == 1             # center == global-off -> no divergence
    assert next(iter(led.posts.values())).render_id == child_id("render", "clip_1", "H")

def test_same_hook_different_framing_distinct_renders(tmp_path, monkeypatch, mocker):
    # @top (framing differs) and @c (inherits global center) with the SAME hook must NOT share one file.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@top", framing="top"),
                {"handle": "@c", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@top": "SAME", "@c": "SAME"},
               surfaces=("@top/instagram", "@c/instagram")); led.save()
    led = _run(cfg)
    rids = {p.render_id for p in led.posts.values()}
    assert len(rids) == 2 and len(led.renders) == 2            # @top frame-tagged; @c un-tagged -> distinct

def test_framing_top_account_no_cut_when_global_on(tmp_path, monkeypatch, mocker):
    # mirror of the center+global-off case: an account pinning top while the global is ON resolves to the
    # SAME framing -> NO cut, un-tagged id (byte-identical). Guards the symmetric no-divergence path.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")             # global ON
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@top", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@top": "H"}, surfaces=("@top/instagram",)); led.save()
    led = _run(cfg)
    assert cut_calls == [] and len(burn_calls) == 1            # top == global-on -> no divergence
    assert next(iter(led.posts.values())).render_id == child_id("render", "clip_1", "H")

def test_framing_center_account_cut_when_global_on(tmp_path, monkeypatch, mocker):
    # the global=ON mirror of the primary case: an account pinning center while the global biases top
    # diverges -> a real per-account CUT at top_bias=False (the account's centred crop wins).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")             # global ON
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@c", framing="center")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@c": "H"}, surfaces=("@c/instagram",)); led.save()
    led = _run(cfg)
    assert len(cut_calls) == 1 and cut_calls[0]["top_bias"] is False   # center overrides global-on top
    assert burn_calls == []

def test_band_and_framing_compose_in_render_id(tmp_path, monkeypatch, mocker):
    # @bandonly differs in band only; @both differs in band AND framing -> distinct ids despite same hook+band.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("@bandonly", clip_profile="long"),
                _acct("@both", account_id="2", clip_profile="long", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@bandonly": "SAME", "@both": "SAME"},
               surfaces=("@bandonly/instagram", "@both/instagram")); led.save()
    led = _run(cfg)
    rids = {p.render_id for p in led.posts.values()}
    assert len(rids) == 2 and len(led.renders) == 2           # the frame tag composes on top of the band tag
