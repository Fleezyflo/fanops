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
from fanops.timeutil import parse_iso

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
    # awaiting_approval post (the approve worklist) — editable, never imminent (gated, can't ship)
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="EDIT ME", hashtags=["#x"],
                      state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    # an already-approved (queued) post has LEFT Review for the Schedule -> must NOT appear in editable
    led.add_post(Post(id="p_appr", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="APPROVED", state=PostState.queued,
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
    # editable card carries clip_1 with the awaiting surface (editable); the approved one is absent
    ed = [c for c in by_bucket.get("editable", []) if c.clip_id == "clip_1"][0]
    sp = {s.post_id: s for s in ed.surfaces}
    assert "p_appr" not in sp                          # approved -> Schedule, not Review
    assert sp["p_edit"].editable is True and sp["p_edit"].imminent is False
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
                      platform=Platform.instagram, caption="v", state=PostState.awaiting_approval,
                      media_urls=["file:///clips/clip_1_variant.mp4"],
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    sp = [s for c in cards for s in c.surfaces if s.post_id == "p_v"][0]
    assert sp.media_url == "/media/p_v"

def test_review_buckets_surfaces_postless_clips_as_prepared(tmp_path):
    # THE 57-clips-0-posts bug: produced clips (queued / captions_requested) that have NO posts must
    # surface in a 'prepared' bucket so the operator can SEE + advance them — they used to vanish, and
    # Review lied with "nothing in the ledger yet". held stays held; clips WITH a post stay editable;
    # terminal states (retired/error) are NOT prepare-able.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)                   # clip_1 = queued, NO posts
    led.add_clip(Clip(id="clip_cap", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captions_requested))
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/h.mp4", aspect=Fmt.r9x16, state=ClipState.held, held=True, held_reason="brand risk"))
    led.add_clip(Clip(id="clip_retired", parent_id="mom_1", path="/r.mp4", aspect=Fmt.r9x16, state=ClipState.retired))
    led.add_clip(Clip(id="clip_posted", parent_id="mom_1", path="/p.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="clip_posted", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    by_bucket = {}
    for c in cards: by_bucket.setdefault(c.bucket, []).append(c)
    prepared = {c.clip_id: c for c in by_bucket.get("prepared", [])}
    assert set(prepared) == {"clip_1", "clip_cap"}          # post-less, non-held, non-terminal clips only
    assert prepared["clip_1"].surfaces == [] and prepared["clip_1"].clip_state == "queued"
    assert prepared["clip_cap"].surfaces == [] and prepared["clip_cap"].clip_state == "captions_requested"
    assert prepared["clip_1"].source_name == "show.mp4"     # lineage still resolves for a post-less clip
    assert "clip_held" not in prepared and any(c.held for c in by_bucket.get("held", []))
    assert "clip_retired" not in prepared                   # terminal -> not surfaced as prepare-able
    assert "clip_posted" not in prepared and any(c.clip_id == "clip_posted" for c in by_bucket.get("editable", []))

def test_held_clip_with_queued_post_only_in_held_not_editable(tmp_path):
    # a held clip that ALSO carries a queued post must appear ONLY in the held bucket (the release
    # gate), never double-listed in editable — held is a brand-risk quarantine that outranks scheduling.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)                   # clip_1 = queued
    led.clips["clip_1"].held = True; led.clips["clip_1"].held_reason = "pulled back"; led.clips["clip_1"].state = ClipState.held
    led.add_post(Post(id="p_q", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    assert [c.bucket for c in cards if c.clip_id == "clip_1"] == ["held"]

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
    assert "No results yet" in view.variant_empty_reason
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

def test_lift_row_carries_degraded_marker(tmp_path):
    # T4: a degraded lift (a primary metric absent from the row) must surface in the Studio lift view,
    # not just the digest — the operator should see the score is partial before trusting it.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_deg", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk", variant_hook="HYPE",
                      metrics={"lift_score": 50.0, "lift_degraded": True, "lift_missing_keys": ["saves", "retention"]}))
    led.add_post(Post(id="p_ok", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="y", state=PostState.analyzed,
                      variant_key="vk2", variant_hook="CALM", metrics={"lift_score": 40.0}))
    rows = {r.variant_hook: r for r in lift_rows(led, cfg, Accounts.load(cfg)).variant_rows}
    assert rows["HYPE"].lift_degraded is True and "saves" in (rows["HYPE"].lift_missing or [])
    assert rows["CALM"].lift_degraded is False        # a full-objective row is not flagged

def test_lift_amplify_section_present_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.amplify_present is True
    assert view.amplify_rows == [] and view.amplify_empty_reason is not None


def test_golive_status_reports_learning_validated(tmp_path):
    # M3: the Go-Live read-model exposes whether the loop is unfrozen (cutover.json metrics_confirmed).
    from fanops.studio.views import golive_status
    from fanops import cutover
    cfg = Config(root=tmp_path)
    assert golive_status(cfg).learning_validated is False     # no cutover.json yet
    cutover._save_state(cfg, {"metrics_confirmed": True})
    assert golive_status(cfg).learning_validated is True


def test_review_counts_tallies_buckets(tmp_path):
    # The Review live-poller's single source of truth: bucket tallies built from the SAME cards the
    # worklist renders. awaiting=editable (approve worklist), prepared=post-less clips, held=brand-risk;
    # 'recent' (shipped) is NOT a waiting count and is excluded.
    from fanops.studio.views import review_counts
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)                       # clip_1 queued, no posts -> prepared
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk"))
    led.add_clip(Clip(id="clip_edit", parent_id="mom_1", path="/e.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_edit", account="@a", account_id="1",
                      platform=Platform.instagram, caption="EDIT", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_recent", parent_id="clip_edit", account="@a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPED", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=2))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    counts = review_counts(cards)
    assert counts == {"awaiting": 1, "prepared": 1, "held": 1}  # recent excluded

def test_review_counts_empty_is_all_zero(tmp_path):
    from fanops.studio.views import review_counts
    assert review_counts([]) == {"awaiting": 0, "prepared": 0, "held": 0}


# ---- content-lifecycle Phase 3: day-bucketed Review (ingest day) + Posted (publish day) ----
def _lineage_day(led, *, sid, mid, cid, day):
    # a source with a fixed ingest day + a captioned clip ready for an awaiting post
    led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en",
                          created_at=(day + "T08:00:00Z") if day else None))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path=f"/c/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))

def test_group_review_editable_sorted_by_day(tmp_path):
    # editable cards from two ingest days come back day-sorted (newest first), card.day set; undated last.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _lineage_day(led, sid="src_old", mid="m_old", cid="c_old", day="2026-06-01")
    _lineage_day(led, sid="src_new", mid="m_new", cid="c_new", day="2026-06-05")
    _lineage_day(led, sid="src_undated", mid="m_un", cid="c_un", day=None)
    for cid in ("c_old", "c_new", "c_un"):
        led.add_post(Post(id=f"p_{cid}", parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                          scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    editable = [c for c in cards if c.bucket == "editable"]
    days = [c.day for c in editable]
    assert days == ["2026-06-05", "2026-06-01", "undated"]      # newest day first, undated last

def test_review_body_poller_count_unchanged(tmp_path):
    # H8 safety: the day-sort must NOT change the awaiting count (review_counts reads count, not order).
    from fanops.studio.views import review_counts
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _lineage_day(led, sid="src_a", mid="m_a", cid="c_a", day="2026-06-01")
    _lineage_day(led, sid="src_b", mid="m_b", cid="c_b", day="2026-06-05")
    for cid in ("c_a", "c_b"):
        led.add_post(Post(id=f"p_{cid}", parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                          scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    assert review_counts(cards)["awaiting"] == 2                # both editable, sort didn't drop one

def test_group_posted_by_day_publish_day(tmp_path):
    from fanops.studio.views import group_posted_by_day, PostedRow
    rows = [PostedRow(post_id="p1", clip_id="c", account="@a", platform="instagram", caption="x",
                      public_url=None, scheduled_time="2026-06-01T00:00:00Z", lift_score=None,
                      published_at="2026-06-05T10:00:00Z"),
            PostedRow(post_id="p2", clip_id="c", account="@a", platform="instagram", caption="y",
                      public_url=None, scheduled_time="2026-06-05T00:00:00Z", lift_score=None,
                      published_at="2026-06-05T20:00:00Z"),
            # no published_at -> falls back to scheduled_time day
            PostedRow(post_id="p3", clip_id="c", account="@a", platform="instagram", caption="z",
                      public_url=None, scheduled_time="2026-06-02T00:00:00Z", lift_score=None,
                      published_at=None),
            # neither aware time -> undated, sorts last
            PostedRow(post_id="p4", clip_id="c", account="@a", platform="instagram", caption="w",
                      public_url=None, scheduled_time=None, lift_score=None, published_at=None)]
    groups = group_posted_by_day(rows)
    days = [d for d, _ in groups]
    assert days == ["2026-06-05", "2026-06-02", "undated"]      # publish day groups, undated last
    by_day = dict(groups)
    assert [r.post_id for r in by_day["2026-06-05"]] == ["p1", "p2"]   # within-day order preserved
    assert [r.post_id for r in by_day["2026-06-02"]] == ["p3"]         # scheduled_time fallback
    assert [r.post_id for r in by_day["undated"]] == ["p4"]

def test_group_posted_by_day_naive_is_undated(tmp_path):
    from fanops.studio.views import group_posted_by_day, PostedRow
    rows = [PostedRow(post_id="pn", clip_id="c", account="@a", platform="instagram", caption="x",
                      public_url=None, scheduled_time="2026-06-05T00:00:00", lift_score=None,
                      published_at=None)]   # NAIVE scheduled_time -> undated (no local-tz guess)
    groups = group_posted_by_day(rows)
    assert [d for d, _ in groups] == ["undated"]

def test_posted_library_row_carries_published_at(tmp_path):
    # PostedRow must expose published_at so the grouper keys on the TRUE publish day.
    from fanops.studio.views import posted_library
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c.mp4", state=ClipState.published))
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, scheduled_time="2026-06-01T00:00:00Z",
                      published_at="2026-06-05T10:00:00Z"))
    rows = posted_library(led, cfg)
    assert rows[0].published_at == "2026-06-05T10:00:00Z"

def test_review_card_surfaces_removed_hook(tmp_path):
    # A clip whose moment had its hook STRIPPED (is_weak_hook dup/template) surfaces the removed hook on the
    # card, so Review can badge it + let the operator restore it. The clip still rendered clean.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.moments["mom_1"].hook_removed = "made it and lost everything"   # what the guard stripped
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.awaiting_approval))
    ed = [c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
          if c.bucket == "editable" and c.clip_id == "clip_1"][0]
    assert ed.hook_removed == "made it and lost everything"


# ---- P1: suggest_time helper + suggested_time on read-models (per-account operator scheduling) ----
def _post(account="@a", platform=Platform.instagram, parent_id="clip_1"):
    return Post(id="p", parent_id=parent_id, account=account, account_id="1", platform=platform,
                caption="x", state=PostState.awaiting_approval)

def test_suggest_time_is_deterministic_and_future(tmp_path):
    from fanops.studio.views import suggest_time
    cfg = Config(root=tmp_path)
    p = _post()
    a = suggest_time(cfg, p, now=NOW); b = suggest_time(cfg, p, now=NOW)
    assert a == b                                   # deterministic (content-addressed, no random)
    assert parse_iso(a) > NOW                       # strictly future

def test_suggest_time_index_zero_no_stagger(tmp_path):
    # two posts on the SAME surface (same account/platform/date) get index=0 each -> NOT 40 min apart.
    # surface_time's clip_id seed differs per parent_id, so they may differ slightly, but never by a
    # full _STEP_MIN (40 min) — that stagger only appears for index>0 (Reschedule-all).
    from fanops.studio.views import suggest_time
    cfg = Config(root=tmp_path)
    a = suggest_time(cfg, _post(parent_id="clip_a"), now=NOW)
    b = suggest_time(cfg, _post(parent_id="clip_b"), now=NOW)
    assert abs((parse_iso(a) - parse_iso(b)).total_seconds()) < 40 * 60   # no imposed 40-min stagger

def test_suggest_time_strictly_after_now_when_lead_zero(tmp_path, monkeypatch):
    # publish_lead_minutes==0 (default) + a degenerate seed (offset 0) could land == now; the +1s nudge
    # keeps it strictly future. Probe seeds until one yields raw == now, then assert the nudge fired.
    from fanops.studio.views import suggest_time
    from fanops.crosspost import surface_time
    monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)   # lead == 0
    cfg = Config(root=tmp_path)
    date_str = NOW.date().isoformat()
    for n in range(2000):                            # find a (parent_id) whose surface_time(index=0) == now
        pid = f"clip_{n}"
        raw = surface_time(NOW, "@a", "instagram", date_str, 0, clip_id=pid, lead_minutes=0)
        if parse_iso(raw) <= NOW:
            out = suggest_time(cfg, _post(parent_id=pid), now=NOW)
            assert parse_iso(out) > NOW              # anti-degenerate +1s nudge fired
            break
    else:
        # no degenerate seed in range — assert the helper is at least always strictly future
        assert parse_iso(suggest_time(cfg, _post(), now=NOW)) > NOW

def test_surfacepost_carries_suggested_time(tmp_path):
    # an awaiting (editable) surface carries a strictly-future suggestion on its read-model.
    from fanops.studio.views import _surface
    cfg = Config(root=tmp_path)
    s = _surface(_post(), persona=None, now=NOW, cfg=cfg)
    assert s.suggested_time is not None and parse_iso(s.suggested_time) > NOW

def test_schedulerow_carries_suggested_time(tmp_path):
    # a queued (editable) row carries the suggestion; a read-only past row carries None.
    from fanops.studio.views import schedule_rows
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pq", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="pp", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, scheduled_time=_z(NOW - timedelta(hours=1))))
    rows = {r.post_id: r for r in schedule_rows(led, cfg, now=NOW)}
    assert rows["pq"].suggested_time is not None and parse_iso(rows["pq"].suggested_time) > NOW
    assert rows["pp"].suggested_time is None         # read-only past row gets no suggestion

def test_suggested_time_with_broken_clip_lineage_still_renders(tmp_path):
    # the suggestion needs ONLY account/platform/parent_id (all on the Post) — a post whose parent clip
    # is missing from the ledger still builds the read-model with a suggestion; no crash.
    from fanops.studio.views import _surface
    cfg = Config(root=tmp_path)
    s = _surface(_post(parent_id="ghost_clip"), persona=None, now=NOW, cfg=cfg)
    assert s.suggested_time is not None and parse_iso(s.suggested_time) > NOW
