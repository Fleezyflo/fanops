# tests/test_studio_provenance.py — S2: the reusable PROVENANCE primitive. Review already shows WHAT a value is
# (28–45s, center, cut, shared-hook); it never shows WHY. provenance_chips() turns a surface into ordered
# "value ← cause" chips (length←persona/account, framing←account, cut/shared-cut, shared-hook, cast←picked-for),
# rendered by the _prov.html cause_chip macro. Pure projection: [] on an undifferentiated surface (OFF firewall),
# never a ledger read, never raises. S4/S7/S8 CONSUME it; this slice ships the macro+helper+fields+tests only.
from datetime import datetime, timezone
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.studio import views


def _sp(**over):
    base = dict(post_id="p", account="@a", platform="instagram", persona=None, caption="", hashtags=[],
                scheduled_time=None, media_url="/media/p", state="awaiting_approval", imminent=False, editable=True)
    base.update(over)
    return views.SurfacePost(**base)


def _macro(cfg, chip):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    tmpl = app.jinja_env.from_string("{% from '_prov.html' import cause_chip %}{{ cause_chip(c) }}")
    return tmpl.render(c=chip)


def test_provenance_chips_empty_for_undifferentiated_surface():
    assert views.provenance_chips(_sp()) == []                          # OFF-firewall / legacy shape → zero chips


def test_length_chip_carries_its_cause():
    chips = views.provenance_chips(_sp(length_label="28–45s", length_cause="persona long"))
    assert len(chips) == 1 and chips[0].value == "28–45s" and chips[0].cause == "persona long" and chips[0].tone == ""


def test_length_chip_bare_when_global_inherited():
    chips = views.provenance_chips(_sp(length_label="12–22s", length_cause=None))
    assert chips[0].value == "12–22s" and chips[0].cause is None         # value renders bare, no misleading attribution


def test_framing_chip_carries_account_cause():
    chips = views.provenance_chips(_sp(framing="center", framing_cause="@a center"))
    assert chips[0].value == "center" and chips[0].cause == "@a center"


def test_cut_chip_is_ok_tone():
    c = [x for x in views.provenance_chips(_sp(is_account_cut=True)) if x.value == "cut"][0]
    assert c.tone == "ok" and "own cut" in c.cause


def test_shared_cut_warn_only_when_variation_on():
    on = views.provenance_chips(_sp(is_account_cut=False), creative_variation=True)
    assert any(x.value == "shared-cut" and x.tone == "warn" for x in on)
    off = views.provenance_chips(_sp(is_account_cut=False), creative_variation=False)
    assert not any(x.value == "shared-cut" for x in off)                # OFF: a shared cut is expected, not a warning


def test_shared_hook_chip_is_warn():
    c = [x for x in views.provenance_chips(_sp(hook_source="shared_fallback")) if x.value == "shared-hook"][0]
    assert c.tone == "warn" and "fell back" in c.cause


def test_cast_chip_only_when_cause_present():
    assert any(x.value == "cast" and x.cause == "picked for @a" for x in views.provenance_chips(_sp(cast_cause="picked for @a")))
    assert not any(x.value == "cast" for x in views.provenance_chips(_sp(cast_cause=None)))   # uncast fans to all → no chip


def test_provenance_helper_never_raises():
    class Weird: pass                                                   # an object missing every attr
    assert views.provenance_chips(Weird()) == []                       # fail-open: a list, never an exception


def test_surface_stamps_attribution_via_persona(tmp_path):
    # a persona-linked account: length_cause names the PERSONA; framing_cause + cast_cause name the account
    cfg = Config(root=tmp_path)
    from fanops.ledger import Ledger
    from fanops.accounts import Account
    from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    acct = Account(handle="@a", account_id="1", persona_id="hype", clip_profile="long", framing="center")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped, affinities=["@a"]))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="long"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona="hype", now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=["@a"])
    assert sp.length_cause == "persona long" and sp.framing_cause == "@a center" and sp.cast_cause == "picked for @a"


def test_surface_attribution_is_account_when_no_persona(tmp_path):
    # clip_profile pinned on the account itself (no persona link) → length_cause names the ACCOUNT, not a persona
    cfg = Config(root=tmp_path)
    from fanops.ledger import Ledger
    from fanops.accounts import Account
    from fanops.models import Source, Clip, Post, Platform, PostState, ClipState, Fmt
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    acct = Account(handle="@a", account_id="1", clip_profile="short")     # account pin, persona_id None
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="short"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=())
    assert sp.length_cause == "@a short" and sp.cast_cause is None        # account-pinned; uncast → no cast cause


def test_macro_renders_value_and_cause(tmp_path):
    html = _macro(Config(root=tmp_path), views.ProvChip("28–45s", "persona long", ""))
    assert "28–45s" in html and "persona long" in html and "title=" in html and "chip" in html


def test_macro_renders_value_only_when_cause_none(tmp_path):
    html = _macro(Config(root=tmp_path), views.ProvChip("center", None, ""))
    assert "center" in html and "title=" not in html and "cause" not in html   # bare value, no empty attribution
