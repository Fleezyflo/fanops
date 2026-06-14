# tests/test_studio_views.py — CREATE
from datetime import datetime, timezone, timedelta
from fanops.studio.views import _imminent, IMMINENT_THRESHOLD_MINUTES

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def test_imminent_none_is_true():
    assert _imminent(None, NOW) is True

def test_imminent_unparseable_is_true():
    assert _imminent("garbage", NOW) is True

def test_imminent_naive_is_true():
    # naive time can't be safely compared / would fail publish_due -> treat as non-editable
    assert _imminent("2026-06-06T13:00:00", NOW) is True

def test_imminent_past_is_true():
    assert _imminent(_z(NOW - timedelta(minutes=1)), NOW) is True

def test_imminent_within_threshold_is_true():
    assert _imminent(_z(NOW + timedelta(minutes=IMMINENT_THRESHOLD_MINUTES - 1)), NOW) is True

def test_not_imminent_when_far_future():
    assert _imminent(_z(NOW + timedelta(hours=2)), NOW) is False

# (the former test_dataclasses_construct lived here — deleted in the stage-7 clean: it asserted
# only that dataclass construction echoes its inputs, which cannot fail meaningfully; the view
# dataclasses are exercised through real code paths by the behavioral tests below)


import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.views import review_buckets

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _lineage(led):
    led.add_source(Source(id="src_1", source_path="/videos/show.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="big drop", transcript_excerpt="here we go", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/clips/clip_1.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))

def test_review_buckets_editable_recent_held(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active", "persona": "hype"}])
    led = Ledger.load(cfg); _lineage(led)
    # held clip (never crossposted)
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/clips/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk: foo"))
    # editable post (far-future queued)
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="EDIT ME", hashtags=["#x"],
                      state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    # imminent post (queued but ~1 min out) -> shown, not editable
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPING", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=1))))
    # recent published post (within 24h)
    led.add_post(Post(id="p_recent", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPED", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=2))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    by_bucket = {}
    for c in cards:
        by_bucket.setdefault(c.bucket, []).append(c)
    # held bucket present with reason
    assert any(c.held and c.held_reason == "brand risk: foo" for c in by_bucket.get("held", []))
    # editable card carries clip_1 with both queued surfaces; only the far-future one is editable
    ed = [c for c in by_bucket.get("editable", []) if c.clip_id == "clip_1"][0]
    sp = {s.post_id: s for s in ed.surfaces}
    assert sp["p_edit"].editable is True and sp["p_edit"].imminent is False
    assert sp["p_imm"].editable is False and sp["p_imm"].imminent is True
    assert ed.source_name == "show.mp4" and ed.moment_window == "0–7" and ed.reason == "big drop"
    assert sp["p_edit"].media_url == "/media/p_edit" and sp["p_edit"].persona == "hype"
    # recent bucket holds the published post, read-only
    rc = [c for c in by_bucket.get("recent", []) if c.clip_id == "clip_1"][0]
    assert all(not s.editable for s in rc.surfaces)
    assert any(s.post_id == "p_recent" for s in rc.surfaces)

def test_review_buckets_variant_media_url_is_post_scoped(tmp_path):
    # media_url is always /media/<post_id> (route resolves variant vs base); not the clip path.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_v", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="v", state=PostState.queued,
                      media_urls=["file:///clips/clip_1_variant.mp4"],
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    sp = [s for c in cards for s in c.surfaces if s.post_id == "p_v"][0]
    assert sp.media_url == "/media/p_v"

from fanops.studio.views import schedule_rows

def test_schedule_rows_sorted_with_recent_and_imminent_flags(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_far", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="far", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=5))))
    led.add_post(Post(id="p_soon", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="soon", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=1))))
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="imm", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.add_post(Post(id="p_done", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="done", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=1))))
    rows = schedule_rows(led, cfg, now=NOW)
    ids = [r.post_id for r in rows]
    # chronological by scheduled_time (recent published first since it is earliest)
    assert ids == ["p_done", "p_imm", "p_soon", "p_far"]
    by_id = {r.post_id: r for r in rows}
    assert by_id["p_far"].editable is True and by_id["p_far"].imminent is False
    assert by_id["p_imm"].editable is False and by_id["p_imm"].imminent is True
    assert by_id["p_done"].editable is False   # published -> read-only
    assert by_id["p_far"].clip_id == "clip_1" and by_id["p_far"].platform == "instagram"


from fanops.studio.views import lift_rows

def test_lift_empty_no_analyzed_posts(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued))
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert "No analyzed posts yet" in view.variant_empty_reason
    assert view.amplify_present is False   # cfg.variant_amplify default OFF -> section absent

def test_lift_empty_state_names_postiz(tmp_path, monkeypatch):
    # M2: a Postiz operator must not be told only to set a Blotato key — the empty-state names Postiz too.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued))
    reason = lift_rows(led, cfg, Accounts.load(cfg)).variant_empty_reason
    assert "postiz" in reason.lower() and "POSTIZ_API_KEY" in reason   # no key value rendered, just the env var name

def test_lift_analyzed_but_no_variant_key(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 50.0}))   # analyzed but no variant_key
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert "Creative variation" in view.variant_empty_reason

def test_lift_ranks_variants_by_lift_score(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_lo", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="lo", state=PostState.analyzed,
                      variant_key="vk_lo", variant_hook="CALM", metrics={"lift_score": 10.0}))
    led.add_post(Post(id="p_hi", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="hi", state=PostState.analyzed,
                      variant_key="vk_hi", variant_hook="HYPE", metrics={"lift_score": 90.0}))
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_empty_reason is None
    assert [r.variant_hook for r in view.variant_rows] == ["HYPE", "CALM"]   # desc by lift_score
    assert view.variant_rows[0].lift_score == 90.0
    assert isinstance(view.variant_rows[0].loop_state, str) and view.variant_rows[0].loop_state

def test_lift_amplify_section_present_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.amplify_present is True
    assert view.amplify_rows == [] and view.amplify_empty_reason is not None
