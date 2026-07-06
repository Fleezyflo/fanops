# tests/test_studio_review_preview.py — content-first Review: the card shows ONE MASTER/source clip on the
# left and each account's HOOK + CAPTION as TEXT in a per-account column on the right (the operator watches
# the master, reads what each account would post, then burns on approval). Supersedes the old per-account
# burned-video switcher — the burn is deferred to approval, so there is no per-account video at review time.
import json
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")    # Studio is an optional [studio] extra — skip cleanly when Flask is absent
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt, Render, RenderState

NOW = datetime.now(timezone.utc).replace(microsecond=0)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config["TESTING"] = True
    return app.test_client()

def _seed_personas(cfg, *, hooks=True, two_clips=False):
    """Two awaiting surfaces on ONE clip (markmakmouly + perca.late), each with its OWN burned-hook variant
    mp4 — the persona-differentiation shape the Review card must display. hooks=False => OFF mode (no
    variant_hook, no media_urls, shared base clip). two_clips=True => a 2nd card to test radio-name uniqueness."""
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "markmakmouly", "account_id": "", "platforms": ["instagram"], "status": "active", "access": "postiz", "persona": "craft", "integrations": {"instagram": "ig1"}},
        {"handle": "perca.late", "account_id": "", "platforms": ["instagram"], "status": "active", "access": "postiz", "persona": "raw", "integrations": {"instagram": "ig2"}}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    va = cfg.clips / "va.mp4"; va.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANTA")
    vb = cfg.clips / "vb.mp4"; vb.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANTB")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped,
                          hook=("WATCH THE CRAFT" if hooks else None)))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    def _post(pid, acct):
        return Post(id=pid, parent_id="clip_1", account=acct, account_id="", platform=Platform.instagram,
                    caption=f"#{acct[:4]}tag", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=5)),
                    media_urls=([f"file://{va}"] if hooks else []))
    led.add_post(_post("p_mark", "markmakmouly"))
    led.add_post(_post("p_perc", "perca.late"))
    if two_clips:
        led.add_moment(Moment(id="mom_2", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped, hook="SECOND CARD"))
        led.add_clip(Clip(id="clip_2", parent_id="mom_2", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p_two", parent_id="clip_2", account="markmakmouly", account_id="", platform=Platform.instagram,
                          caption="#x", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=6)),
                          media_urls=[f"file://{va}"]))
    led.save()

def test_card_renders_master_clip_and_per_account_text(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    # ONE master/source clip player per card (left), NOT a per-account burned video each
    assert "/clips/clip_1" in html
    assert html.count("<video") == 1
    assert "/media/p_mark" not in html and "/media/p_perc" not in html   # per-account video previews dropped
    # each account's hook + caption shown as TEXT (right columns) so personas compare side by side
    # P9: owner-moment hook is shared by every surface on this clip
    assert "WATCH THE CRAFT" in html
    assert "#marktag" in html and "#perctag" in html

def test_card_degrades_when_no_variant(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg, hooks=False)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "/clips/clip_1" in html                                # master clip still shown (no per-account media)
    assert "WATCH THE CRAFT" not in html                          # no hook line when nothing was burned

def test_each_card_shows_its_own_master(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg, two_clips=True)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "/clips/clip_1" in html and "/clips/clip_2" in html    # each card players its OWN master clip
    assert "card-clip_1" in html and "card-clip_2" in html        # distinct card containers

def test_per_account_column_a11y(tmp_path):
    cfg = Config(root=tmp_path); _seed_personas(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    # each account column is an accessibly-named group referencing its account
    assert 'aria-label="markmakmouly instagram' in html and 'aria-label="perca.late instagram' in html

def test_card_shows_per_account_length_cut_and_framing(tmp_path):
    # M3a: the operator SEES the per-account differentiation — clip LENGTH band, a CUT marker for a genuine
    # per-account cut, and the pinned FRAMING — rendered on the surface in the Review HTML.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@long", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "clip_profile": "long", "framing": "top"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    r = cfg.clips / "r.mp4"; r.write_bytes(b"\x00\x00\x00\x18ftypmp42CUT")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(r), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_render(Render(id="r1", clip_id="clip_1", account="long", surface_key="long/instagram",
                          hook_text="H", path=str(r), state=RenderState.rendered, is_account_cut=True))
    led.add_post(Post(id="p_long", parent_id="clip_1", account="long", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, render_id="r1", clip_profile="long",
                      scheduled_time=_z(NOW + timedelta(hours=5)))); led.save()
    html = _client(cfg).get("/review?view=list").data.decode()
    # S4: the surface-spec now renders via the shared _prov cause_chip macro — each chip carries its WHY.
    assert "28–45s" in html                                    # the long band length label (value)
    assert "long long" in html                                # length cause — the account pins long
    assert "long&#39;s own cut" in html                       # the cut chip's cause (genuine per-account render)
    assert ">top " in html and "long top" in html             # framing chip value + its cause
