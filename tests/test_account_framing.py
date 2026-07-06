# tests/test_account_framing.py — M2c: per-account FRAMING (Account.framing + Config.resolve_top_bias
# + the add_account framing write path), wired into the per-account render CUT. An account can pin its vertical
# crop bias ("top" = head-safe upper-third, "center" = default) independent of the GLOBAL aware_reframe;
# when the resolved framing differs from the global, the account's Render becomes a real per-account CUT
# (its own framing) — content-addressed with a frame tag so two accounts sharing one hook+band but a
# different crop never collide on one file. An account inheriting the global framing is byte-identical.
import json
import pytest
from fanops.config import Config, FRAMING_NAMES
from fanops.accounts import Accounts, add_account
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
from fanops.crosspost import crosspost_clips


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, **extra):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active", **extra}


# ---------------------------------------------------------------- FRAMING_NAMES + Account.framing ----
def test_framing_names_exported():
    assert FRAMING_NAMES == {"top", "center"}                  # the validatable set the write boundary enforces

def test_account_framing_defaults_none(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a")])
    assert Accounts.load(cfg).accounts[0].framing is None       # absent field -> None (additive, no migration)

def test_account_framing_persists_when_set(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="top")])
    assert Accounts.load(cfg).accounts[0].framing == "top"

def test_load_unknown_framing_does_not_crash(tmp_path):
    # fail-open: a hand-edited/legacy unknown framing reloads (resolve_top_bias ignores it -> global).
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="weird")])
    assert Accounts.load(cfg).accounts[0].framing == "weird"    # persisted, inert downstream


# ---------------------------------------------------------------- Config.resolve_top_bias ----
def test_resolve_top_bias_top_account_true(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="top")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is True                      # account pins head-safe top crop

def test_resolve_top_bias_center_account_overrides_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")             # global ON...
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="center")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False                     # ...but the account pins center -> override wins

def test_resolve_top_bias_none_account_falls_back_to_global(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    assert cfg.resolve_top_bias(None) is False                  # default global aware_reframe OFF
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    assert Config(root=tmp_path).resolve_top_bias(None) is True  # global flows through

def test_resolve_top_bias_unset_account_inherits_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a")])      # no per-account framing
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is True                      # inherits the global

def test_resolve_top_bias_blank_falls_back(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="  ")])  # whitespace-only is no override
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False

def test_resolve_top_bias_unknown_falls_back(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a", framing="weird")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_top_bias(a) is False                     # unknown is inert -> global (validate-or-default on read)


# ---------------------------------------------------------------- add_account framing write path ----
def test_add_account_with_framing_persists(tmp_path):
    cfg = Config(root=tmp_path)
    assert add_account(cfg, "@a", ["instagram"], framing="top") == "a"
    assert Accounts.load(cfg).accounts[0].framing == "top"

def test_add_account_rejects_unknown_framing(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_account(cfg, "@a", ["instagram"], framing="weird")


# ---------------------------------------------------------------- crosspost wiring (P9: moment-level framing) ----
def _seed_clip(led, cfg, *, m_hook=None, m_framing=None, m_profile=None, surfaces):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook, framing=m_framing, clip_profile=m_profile))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _run(cfg, mocker):
    rendered = Clip(id="clip_mom_1_9x16", parent_id="mom_1", path=str(cfg.clips / "clip_mom_1_9x16.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    mocker.patch("fanops.crosspost.render_moment", return_value=(Ledger.load(cfg), rendered))
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save()
    return Ledger.load(cfg)

def test_moment_framing_top_stamps_top_bias(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", m_framing="top", surfaces=("a/instagram",)); led.save()
    led = _run(cfg, mocker)
    assert next(iter(led.posts.values())).top_bias is True

def test_moment_framing_none_inherits_global(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("a/instagram",)); led.save()
    led = _run(cfg, mocker)
    assert next(iter(led.posts.values())).top_bias is False

def test_moment_framing_center_overrides_global_on(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("c", framing="center")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", m_framing="center", surfaces=("c/instagram",)); led.save()
    led = _run(cfg, mocker)
    assert next(iter(led.posts.values())).top_bias is False

def test_render_spec_distinct_framing_ids(tmp_path):
    from fanops.crosspost import render_spec
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    rid_top, _, _, tb_top = render_spec(cfg, clip=clip, hook="SAME", moment=Moment(id="mom_1", parent_id="s", start=0, end=7, reason="r", framing="top"))
    rid_ctr, _, _, tb_ctr = render_spec(cfg, clip=clip, hook="SAME", moment=Moment(id="mom_1", parent_id="s", start=0, end=7, reason="r", framing="center"))
    assert rid_top != rid_ctr and tb_top is True and tb_ctr is False

def test_moment_framing_top_matches_global_on(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("top", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", m_framing="top", surfaces=("top/instagram",)); led.save()
    led = _run(cfg, mocker)
    assert next(iter(led.posts.values())).top_bias is True

def test_moment_profile_long_stamped_on_post(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("bandonly", clip_profile="long"), _acct("both", account_id="2", clip_profile="long", framing="top")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="SAME", m_profile="long", surfaces=("bandonly/instagram",)); led.save()
    led = _run(cfg, mocker)
    assert next(iter(led.posts.values())).clip_profile == "long"

def test_render_spec_band_and_framing_compose(tmp_path):
    from fanops.crosspost import render_spec
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    rid_long = render_spec(cfg, clip=clip, hook="SAME", moment=Moment(id="m1", parent_id="s", start=0, end=7, reason="r", clip_profile="long", framing="top"))[0]
    rid_talk = render_spec(cfg, clip=clip, hook="SAME", moment=Moment(id="m2", parent_id="s", start=0, end=7, reason="r", clip_profile="talk", framing="top"))[0]
    assert rid_long != rid_talk
