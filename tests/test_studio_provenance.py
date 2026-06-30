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
    # a persona-linked account whose PERSONA supplies the profile (persona_owns_profile, stamped at hydration):
    # length_cause names the PERSONA; framing_cause + cast_cause name the account
    cfg = Config(root=tmp_path)
    from fanops.ledger import Ledger
    from fanops.accounts import Account
    from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    acct = Account(handle="@a", account_id="1", persona_id="hype", clip_profile="long", framing="center", persona_owns_profile=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped, affinities=["@a"]))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="long", public_url="dryrun://p"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona="hype", now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=["@a"])
    assert sp.length_cause == "persona long" and sp.framing_cause == "@a center" and sp.cast_cause == "picked for @a"


def _seed_for_cast(cfg, *, method, moment_ids, affinities):
    from fanops.ledger import Ledger
    from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt,
                               AccountSelection, account_selection_id)
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped, affinities=affinities))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p"))
        if method is not None:
            led.add_account_selection(AccountSelection(id=account_selection_id("s", "@a"), source_id="s",
                                                       account="@a", moment_ids=moment_ids, method=method))
    return Ledger.load(cfg)


def test_surface_cast_cause_reads_selection_method(tmp_path):
    # RF1: cast_cause reflects the DURABLE AccountSelection method (not just affinities / post-existence).
    from fanops.models import SelectionMethod
    cfg = Config(root=tmp_path)
    led = _seed_for_cast(cfg, method=SelectionMethod.llm, moment_ids=["m"], affinities=["@a"])
    sp = views._surface(led.posts["p"], persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc),
                        cfg=cfg, led=led, acct=None, affinities=["@a"])
    assert sp.cast_cause == "picked for @a (llm)"


def test_surface_cast_cause_flags_fan_all_default_visibly(tmp_path):
    # an operator/migration fan_all_default -> a VISIBLE labelled fan-to-all (⚠), never a silent gap.
    from fanops.models import SelectionMethod
    cfg = Config(root=tmp_path)
    led = _seed_for_cast(cfg, method=SelectionMethod.fan_all_default, moment_ids=[], affinities=[])
    sp = views._surface(led.posts["p"], persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc),
                        cfg=cfg, led=led, acct=None, affinities=[])
    assert sp.cast_cause and "fans to all" in sp.cast_cause and "⚠" in sp.cast_cause


def test_surface_cast_cause_legacy_affinity_fallback_unchanged(tmp_path):
    # a pre-v9 source (NO AccountSelection) keeps the exact legacy string — byte-identical fallback.
    cfg = Config(root=tmp_path)
    led = _seed_for_cast(cfg, method=None, moment_ids=[], affinities=["@a"])
    sp = views._surface(led.posts["p"], persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc),
                        cfg=cfg, led=led, acct=None, affinities=["@a"])
    assert sp.cast_cause == "picked for @a"


def test_surface_persona_link_without_owned_profile_names_account(tmp_path):
    # the audit's MEDIUM-1: a persona-LINKED account whose persona supplies NO profile (persona_owns_profile
    # False) but the ACCOUNT has its own pin -> the profile came from the account pin, NOT the persona. The
    # chip must name the account ("@a long"), never falsely claim "persona long".
    cfg = Config(root=tmp_path)
    from fanops.ledger import Ledger
    from fanops.accounts import Account
    from fanops.models import Source, Clip, Post, Platform, PostState, ClipState, Fmt
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    acct = Account(handle="@a", account_id="1", persona_id="hype", clip_profile="long")   # linked, but persona didn't own the cut
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="long", public_url="dryrun://p"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona="hype", now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=())
    assert sp.length_cause == "@a long"                                   # account-owned, not "persona long"


def test_surface_mismatched_account_pin_yields_no_attribution(tmp_path):
    # the audit's MEDIUM-2: the account pin DIFFERS from the post's stamped profile (config drifted after mint).
    # Naming "@a long" when the account pins "short" is a false attribution -> the chip must render BARE (None).
    cfg = Config(root=tmp_path)
    from fanops.ledger import Ledger
    from fanops.accounts import Account
    from fanops.models import Source, Clip, Post, Platform, PostState, ClipState, Fmt
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    acct = Account(handle="@a", account_id="1", clip_profile="short")     # account pins SHORT; post stamped LONG
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="long", public_url="dryrun://p"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=())
    assert sp.length_cause is None                                        # pin != stamped profile -> no false credit


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
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, clip_profile="short", public_url="dryrun://p"))
    led = Ledger.load(cfg); post = led.posts["p"]
    sp = views._surface(post, persona=None, now=datetime(2026, 6, 24, tzinfo=timezone.utc), cfg=cfg, led=led, acct=acct, affinities=())
    assert sp.length_cause == "@a short" and sp.cast_cause is None        # account-pinned; uncast → no cast cause


def test_macro_renders_value_and_cause(tmp_path):
    html = _macro(Config(root=tmp_path), views.ProvChip("28–45s", "persona long", ""))
    assert "28–45s" in html and "persona long" in html and "title=" in html and "chip" in html


def test_macro_renders_value_only_when_cause_none(tmp_path):
    html = _macro(Config(root=tmp_path), views.ProvChip("center", None, ""))
    assert "center" in html and "title=" not in html and "cause" not in html   # bare value, no empty attribution
