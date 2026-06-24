# tests/test_studio_regenerate.py — Regenerate (review-first milestone 3): re-run the caption model
# for ONE queued post from the browser, honoring the operator's typed guidance and re-applying the
# SAME brand-risk guard the pipeline uses — so "press regenerate and it gives it to me again" never
# bypasses the off-brand HOLD and never asks the operator to hand-write a caption.
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.errors import ToolchainMissingError
from fanops.studio.actions import regenerate_caption

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
# Fixed far-future schedule so the seeded post stays editable (not imminent) under BOTH the unit
# tests (now=NOW) and the route tests (real wall-clock now, which NOW would already be behind).
FUTURE = "2099-01-01T00:00:00Z"
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg, caption="OLD", lang="en", state=PostState.queued):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language=lang))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="the beat drops here", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption=caption, state=state,
                      scheduled_time=FUTURE))
    led.save(); return led

def _model(caption="A FRESH TAKE", hashtags=None, surface="@a/instagram"):
    """A fake caption model: returns a CaptionSet-shaped dict for the post's surface (no network)."""
    def m(prompt, schema):
        return {"items": [{"surface": surface, "caption": caption,
                           "hashtags": hashtags or [], "language": "en"}]}
    return m


def test_regenerate_rewrites_queued_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = regenerate_caption(cfg, "p_edit", "punchier", model=_model("PUNCHIER LINE"), now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).posts["p_edit"].caption == "PUNCHIER LINE"
    assert res.detail["caption"] == "PUNCHIER LINE"

def test_regenerate_passes_operator_guidance_and_context_to_model(tmp_path):
    # The operator's typed hint AND the clip's transcript excerpt must both reach the model — that is
    # what makes the new take reflect "change this information" rather than a blind re-roll.
    cfg = Config(root=tmp_path); _seed(cfg)
    seen = {}
    def m(prompt, schema):
        seen["prompt"] = prompt
        return {"items": [{"surface": "@a/instagram", "caption": "ok", "language": "en"}]}
    regenerate_caption(cfg, "p_edit", "mention the beat drop", model=m, now=NOW)
    assert "mention the beat drop" in seen["prompt"]            # operator hint reached the model
    assert "the beat drops here" in seen["prompt"]              # clip transcript excerpt is context

def test_regenerate_rejects_offbrand_without_persisting(tmp_path):
    # the SAME brand-risk guard the pipeline applies (caption.brand_risk_flag) — a regenerated
    # "link in bio" must be REJECTED, never written, so regenerate can't bypass the off-brand HOLD.
    cfg = Config(root=tmp_path); _seed(cfg, caption="CLEAN")
    res = regenerate_caption(cfg, "p_edit", "", model=_model("check the link in bio"), now=NOW)
    assert res.ok is False and "brand" in (res.error or "").lower()
    assert Ledger.load(cfg).posts["p_edit"].caption == "CLEAN"   # unchanged — no guardrail bypass

def test_regenerate_guards_non_queued(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].state = PostState.published; led.save()
    res = regenerate_caption(cfg, "p_edit", "", model=_model(), now=NOW)
    assert res.ok is False and "queued" in (res.error or "").lower()

def test_regenerate_guards_imminent(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW + timedelta(minutes=1)); led.save()
    res = regenerate_caption(cfg, "p_edit", "", model=_model(), now=NOW)
    assert res.ok is False and "imminent" in (res.error or "").lower()

def test_regenerate_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = regenerate_caption(cfg, "nope", "", model=_model(), now=NOW)
    assert res.ok is False and "no such post" in (res.error or "").lower()

def test_regenerate_surfaces_missing_claude(tmp_path):
    # No `claude` on PATH -> the model raises ToolchainMissingError; surface a clean, actionable
    # message (run autopilot), never a 500, and never touch the caption.
    cfg = Config(root=tmp_path); _seed(cfg, caption="KEEP")
    def boom(prompt, schema): raise ToolchainMissingError("claude not found on PATH")
    res = regenerate_caption(cfg, "p_edit", "", model=boom, now=NOW)
    assert res.ok is False and "claude" in (res.error or "").lower()
    assert Ledger.load(cfg).posts["p_edit"].caption == "KEEP"    # unchanged

def test_regenerate_picks_exact_surface_among_many(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    def m(prompt, schema):
        return {"items": [{"surface": "@b/youtube", "caption": "WRONG", "language": "en"},
                          {"surface": "@a/instagram", "caption": "RIGHT", "language": "en"}]}
    res = regenerate_caption(cfg, "p_edit", "", model=m, now=NOW)
    assert res.ok is True and res.detail["caption"] == "RIGHT"

def test_regenerate_accepts_lone_item_on_surface_mismatch(tmp_path):
    # Single-surface regen: if the model returns exactly ONE item whose surface label differs from
    # ours (off-by-format), accept it rather than failing — the production happy path (one surface).
    cfg = Config(root=tmp_path); _seed(cfg)
    def m(prompt, schema): return {"items": [{"surface": "wrong/label", "caption": "LONE", "language": "en"}]}
    res = regenerate_caption(cfg, "p_edit", "", model=m, now=NOW)
    assert res.ok is True and res.detail["caption"] == "LONE"

def test_regenerate_malformed_model_output_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, caption="KEEP")
    def m(prompt, schema): return {"items": [{"surface": "@a/instagram"}]}   # missing required caption
    res = regenerate_caption(cfg, "p_edit", "", model=m, now=NOW)
    assert res.ok is False
    assert Ledger.load(cfg).posts["p_edit"].caption == "KEEP"    # unchanged


# ---- Flask wiring ----
def test_regenerate_route_swaps_edit_field(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    # the route uses the default model (claude_json); patch it at its module so the lazy import binds
    # to the fake — proves the real HTTP path persists and re-renders the edit field with the new text.
    monkeypatch.setattr("fanops.llm.claude_json",
                        lambda prompt, schema, **kw: {"items": [{"surface": "@a/instagram",
                                                                 "caption": "ROUTED", "language": "en"}]})
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/regenerate/p_edit", data={"guidance": "punchier"})
    assert r.status_code == 200 and b"ROUTED" in r.data
    assert Ledger.load(cfg).posts["p_edit"].caption == "ROUTED"

def test_regenerate_route_unknown_post_shows_clean_error(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/regenerate/nope", data={"guidance": ""})
    assert r.status_code == 200 and b"no such post" in r.data

def test_review_card_renders_regenerate_button(tmp_path):
    # Review shows awaiting_approval posts (the approve worklist); the edit/regenerate controls work pre-approval.
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.awaiting_approval)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review?view=list")
    assert r.status_code == 200 and b"Regenerate" in r.data
