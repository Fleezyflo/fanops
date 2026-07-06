# tests/test_persona_cut_derivation.py — P2: a persona's content_focus DERIVE a default CUT spec
# (length band + framing) so a distinct persona produces a distinct CLIP with NO hand-set clip_profile. The
# wire (hydrate -> resolve -> wants_cut -> render_account_cut) is already built; this only supplies its inputs.
# Pin always wins; derivation is the floor; identical signals derive identically (dedup-safe); empty -> global.
import json
from types import SimpleNamespace
from fanops.config import Config, FRAMING_NAMES
from fanops.personas import (Persona, derive_cut_spec, resolved_cut_spec, compose_breakdown,
                             _FOCUS_PROFILE, _FRAMING_MAP, add_persona)
from fanops.accounts import Accounts, link_persona
from fanops.bands import PROFILE_NAMES


def _p(**kw): return Persona(id="p", **kw)

def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle="a"):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active"}


# ---- derivation table: every emitted value is a valid engine name (a typo would silently kill the cut) ----
def test_derivation_values_are_valid_names():
    assert set(_FOCUS_PROFILE.values()) <= PROFILE_NAMES
    assert set(_FRAMING_MAP.values()) <= FRAMING_NAMES

def test_focus_maps_to_length():
    assert derive_cut_spec(_p(content_focus=["storytelling"]))[0] == "long"
    assert derive_cut_spec(_p(content_focus=["punchlines"]))[0] == "short"
    assert derive_cut_spec(_p(content_focus=["emotional"]))[0] == "medium"

def test_content_focus_maps_to_framing():
    assert derive_cut_spec(_p(content_focus=["punchlines"]))[1] == "center"
    assert derive_cut_spec(_p(content_focus=["storytelling"]))[1] == "top"
    assert derive_cut_spec(_p(content_focus=[]))[1] is None

def test_multi_focus_is_deterministic_longer_bias_first():
    assert derive_cut_spec(_p(content_focus=["punchlines", "storytelling"]))[0] == "long"
    assert derive_cut_spec(_p(content_focus=["storytelling", "punchlines"]))[0] == "long"   # order-independent

def test_empty_persona_derives_nothing():
    assert derive_cut_spec(_p()) == (None, None)


# ---- resolved_cut_spec: pin OVER derived OVER None. M3d: the per-PERSONA pin was retired (it was an invisible
# duplicate of the derived cut); the pin now lives ONLY on the Account CARRIER. resolved_cut_spec stays duck-
# typed, so an Account-shaped object's clip_profile/framing still wins over the derived spec. ----
def test_account_carrier_pin_beats_derived():
    # the Account carrier (a SimpleNamespace stand-in) keeps the explicit pin; it wins over the derived spec.
    acc = SimpleNamespace(content_focus=["punchlines", "hype"], clip_profile="medium", framing="top")
    assert resolved_cut_spec(acc) == ("medium", "top")           # explicit carrier pin wins over derived long/center

def test_persona_can_no_longer_pin_only_derives():
    # a Persona has no clip_profile/framing field (M3d) — a stray pin value is ignored; it DERIVES from levers.
    p = _p(content_focus=["punchlines", "hype"], clip_profile="medium", framing="top")
    assert resolved_cut_spec(p) == ("short", "center")            # derived (punchlines+hype->short/center); pin ignored

def test_derived_when_unpinned():
    assert resolved_cut_spec(_p(content_focus=["punchlines", "hype"])) == ("short", "center")

def test_global_when_bare():
    assert resolved_cut_spec(_p(voice="v")) == (None, None)


# ---- hydration: a linked signal-bearing persona drives the account's cut; unlinked is unchanged ----
def test_hydration_applies_derived_spec(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    pid = add_persona(cfg, name="Storyteller", voice="v", content_focus=["storytelling", "emotional"])
    link_persona(cfg, "@a", pid)
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.clip_profile == "long" and acc.framing == "top"    # derived from content_focus

# (M3d: test_hydration_pin_wins removed — a Persona can no longer pin clip_profile; only the DERIVED spec
# hydrates onto the account, covered by test_hydration_applies_derived_spec above. The Account carrier's own
# pin is set via set_clip_profile and tested in test_account_framing.)

def test_unlinked_account_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.clip_profile is None and acc.framing is None       # no link -> byte-identical (global stands)


# ---- compose_breakdown surfaces the derived source truthfully (persona / derived / global) ----
def test_compose_breakdown_cut_source_three_way(tmp_path):
    cfg = Config(root=tmp_path)
    # "persona"/pinned source is reachable only via an explicit carrier pin now (M3d) — a Persona never pins.
    pinned = SimpleNamespace(clip_profile="short", content_focus=[], selection_scope=None, hook_angle=None,
                             hashtag_corpus=[], voice="v")
    assert compose_breakdown(cfg, pinned)["cut"]["source"] == "persona"
    assert compose_breakdown(cfg, _p(content_focus=["storytelling"]))["cut"]["source"] == "derived"
    assert compose_breakdown(cfg, _p(voice="v"))["cut"]["source"] == "global"
    assert "28-45s" in compose_breakdown(cfg, _p(content_focus=["storytelling"]))["cut"]["band"]   # the derived long band shows

def test_voice_match_hydrates_without_persona_id(tmp_path):
    cfg = Config(root=tmp_path)
    voice = "music-blogger curator who champions craft."
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": voice}])
    add_persona(cfg, name="Craft", voice=voice, content_focus=["storytelling", "emotional"])
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.persona_id is None and acc.clip_profile == "long" and acc.framing == "top"   # voice match, no persisted link


# ---- render_spec: owner-moment clip_profile/framing (P9) ----
def _clip_stub():
    from fanops.models import Clip, ClipState, Fmt
    return Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)

def test_render_spec_reads_moment_not_account(tmp_path):
    from fanops.crosspost import render_spec
    from fanops.models import Moment, MomentState
    cfg = Config(root=tmp_path)
    clip = _clip_stub()
    m = Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
               state=MomentState.clipped, hook="H", clip_profile="long", framing="top")
    _, wants, profile, top = render_spec(cfg, clip=clip, hook="H", moment=m)
    assert wants is True and profile == "long" and top is True

def test_approve_does_not_materialize_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    from fanops.ledger import Ledger
    from fanops.crosspost import crosspost_clips
    from fanops.studio.actions_approve import approve_posts
    from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
    cut = mocker.patch("fanops.crosspost.render_account_cut")
    cfg = Config(root=tmp_path)
    _accounts(cfg, [_acct("story")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook="H", clip_profile="long", framing="top"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"story/instagram": {"caption": "cap", "hashtags": ["#x"]}}
    led.add_clip(clip); led.save()
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z"); led.save()
    approve_posts(cfg, [p.id for p in led.posts.values()])
    cut.assert_not_called()
    assert Ledger.load(cfg).renders == {}

