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
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt, Render, RenderState
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
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="EDIT ME", hashtags=["#x"],
                      state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    # an already-approved (queued) post has LEFT Review for the Schedule -> must NOT appear in editable
    led.add_post(Post(id="p_appr", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="APPROVED", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=1))))
    # recent published post (within 24h) on a DISTINCT clip — Face 4 dedups a clip out of 'recent' when it
    # is ALSO in the editable worklist, so the recent bucket needs its own shipped-only clip to cover it.
    led.add_clip(Clip(id="clip_recent", parent_id="mom_1", path="/clips/clip_recent.mp4", aspect=Fmt.r9x16,
                      state=ClipState.published))
    led.add_post(Post(id="p_recent", parent_id="clip_recent", account="a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPED", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=2)), public_url="dryrun://p_recent"))
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
    # Face 4 dedup: clip_1 is in the editable worklist, so it is NOT also rendered as a recent card (one <video>)
    assert not [c for c in by_bucket.get("recent", []) if c.clip_id == "clip_1"]
    # recent bucket holds the DISTINCT shipped-only clip, read-only
    rc = [c for c in by_bucket.get("recent", []) if c.clip_id == "clip_recent"][0]
    assert all(not s.editable for s in rc.surfaces)
    assert any(s.post_id == "p_recent" for s in rc.surfaces)

def test_review_buckets_variant_media_url_is_post_scoped(tmp_path):
    # media_url is always /media/<post_id> (route resolves variant vs base); not the clip path.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_v", parent_id="clip_1", account="a", account_id="1",
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
    led.add_post(Post(id="p1", parent_id="clip_posted", account="a", account_id="1", platform=Platform.instagram,
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
    led.add_post(Post(id="p_q", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    assert [c.bucket for c in cards if c.clip_id == "clip_1"] == ["held"]

from fanops.studio.views import schedule_rows

def test_schedule_rows_sorted_with_recent_and_imminent_flags(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_far", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="far", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=5))))
    led.add_post(Post(id="p_soon", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="soon", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=1))))
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="imm", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.add_post(Post(id="p_done", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="done", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=1)), public_url="dryrun://p_done"))
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
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued, public_url="dryrun://p1"))
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
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued, public_url="dryrun://p1"))
    reason = lift_rows(led, cfg, Accounts.load(cfg)).variant_empty_reason
    assert "postiz" in reason.lower() and "POSTIZ_API_KEY" in reason   # no key value rendered, just the env var name

def _lift_post(led, pid, hook, lift, *, degraded=False):
    cid, mid = f"clip_{pid}", f"mom_{pid}"
    if not led.sources.get("src_1"):
        led.add_source(Source(id="src_1", source_path="/videos/show.mp4", language="en"))
    led.add_moment(Moment(id=mid, parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id=cid, parent_id=mid, path=f"/clips/{cid}.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))
    m = {"lift_score": lift}
    if degraded: m |= {"lift_degraded": True, "lift_missing_keys": ["saves", "retention"]}
    led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics=m, public_url=f"dryrun://{pid}"))

def test_lift_analyzed_but_no_hook(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 50.0}, public_url="dryrun://p1"))
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert "hook" in (view.variant_empty_reason or "").lower()

def test_lift_ranks_variants_by_lift_score(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg)
    _lift_post(led, "p_lo", "CALM", 10.0)
    _lift_post(led, "p_hi", "HYPE", 90.0)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_empty_reason is None
    assert [r.variant_hook for r in view.variant_rows] == ["HYPE", "CALM"]
    assert view.variant_rows[0].lift_score == 90.0
    assert isinstance(view.variant_rows[0].loop_state, str) and view.variant_rows[0].loop_state

def test_lift_row_carries_degraded_marker(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _lift_post(led, "p_deg", "HYPE", 50.0, degraded=True)
    _lift_post(led, "p_ok", "CALM", 40.0, degraded=False)
    rows = {r.variant_hook: r for r in lift_rows(led, cfg, Accounts.load(cfg)).variant_rows}
    assert rows["HYPE"].lift_degraded is True and "saves" in (rows["HYPE"].lift_missing or [])
    assert rows["CALM"].lift_degraded is False

def _deg_post(led, pid, hook, lift, degraded):
    _lift_post(led, pid, hook, lift, degraded=degraded)

def test_lift_all_degraded_reports_table_level_fact(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    for i in range(4): _deg_post(led, f"p{i}", f"H{i}", 10.0 * i, degraded=True)   # 4/4 degraded
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.degraded_count == 4 and view.degraded_total == 4
    assert view.degraded_mostly is True                    # 100% > 50% -> table-level

def test_lift_majority_degraded_is_mostly(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    for i in range(3): _deg_post(led, f"p{i}", f"H{i}", 10.0 * i, degraded=True)    # 3 degraded
    _deg_post(led, "pok", "OK", 99.0, degraded=False)                               # 1 clean -> 3/4 = 75%
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.degraded_count == 3 and view.degraded_total == 4
    assert view.degraded_mostly is True                    # 75% > 50% -> table-level

def test_lift_minority_degraded_is_not_mostly(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _deg_post(led, "pdeg", "DEG", 10.0, degraded=True)                              # 1 degraded
    for i in range(3): _deg_post(led, f"pok{i}", f"OK{i}", 20.0 + i, degraded=False)  # 3 clean -> 1/4 = 25%
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.degraded_count == 1 and view.degraded_total == 4
    assert view.degraded_mostly is False                   # 25% <= 50% -> loud per-row badge stays

def test_lift_no_degraded_is_not_mostly(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    for i in range(3): _deg_post(led, f"pok{i}", f"OK{i}", 10.0 + i, degraded=False)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.degraded_count == 0 and view.degraded_mostly is False

def test_lift_empty_view_degraded_summary_safe(tmp_path):
    # no analyzed variant posts -> the summary fields must be well-defined (no note, not mostly)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert view.degraded_count == 0 and view.degraded_total == 0 and view.degraded_mostly is False

def test_lift_amplify_section_present_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.amplify_present is True
    assert view.amplify_rows == [] and view.amplify_empty_reason is not None


# ── T-15 (MOL-102): Δ-vs-account-MEDIAN annotator (additive sibling of lineage_stats' Δ-vs-best) ──────
from fanops.studio.views_results import LiftRow, account_median_deltas

def _lr(account, lift, hook="h"):
    return LiftRow(variant_hook=hook, account=account, platform="instagram", lift_score=lift, loop_state="s")

def test_account_median_deltas_stamps_delta_vs_group_median():
    # median of [10, 30, 50] = 30 -> deltas -20 / 0 / +20 for the same account.
    rows = [_lr("a", 10.0), _lr("a", 30.0), _lr("a", 50.0)]
    account_median_deltas(rows)
    assert [r.delta_vs_account_median for r in rows] == [-20.0, 0.0, 20.0]

def test_account_median_deltas_even_count_uses_mean_of_middle_two():
    # statistics.median of [10, 20, 30, 40] = 25 -> matches stdlib semantics, not a pick-one.
    rows = [_lr("a", 10.0), _lr("a", 20.0), _lr("a", 30.0), _lr("a", 40.0)]
    account_median_deltas(rows)
    assert [r.delta_vs_account_median for r in rows] == [-15.0, -5.0, 5.0, 15.0]

def test_account_median_deltas_single_row_account_stays_none():
    # a median vs a single data point is degenerate -> no chip (mirrors lineage_stats' measured guard).
    rows = [_lr("solo", 42.0)]
    account_median_deltas(rows)
    assert rows[0].delta_vs_account_median is None

def test_account_median_deltas_groups_are_per_account():
    # @a has 2 rows (median 15), @b has 1 row -> @b stays None, @a is deltated against ITS own median.
    a1, a2, b1 = _lr("a", 10.0), _lr("a", 20.0), _lr("b", 99.0)
    account_median_deltas([a1, a2, b1])
    assert [a1.delta_vs_account_median, a2.delta_vs_account_median] == [-5.0, 5.0]
    assert b1.delta_vs_account_median is None

def test_account_median_deltas_excludes_unmeasured_rows_from_median_and_delta():
    # a None lift_score is excluded from the median AND never stamped; a group needs >=2 MEASURED rows.
    measured1, measured2 = _lr("a", 10.0), _lr("a", 30.0)
    unmeasured = LiftRow(variant_hook="u", account="a", platform="instagram", lift_score=None, loop_state="s")  # type: ignore[arg-type]
    account_median_deltas([measured1, unmeasured, measured2])
    assert [measured1.delta_vs_account_median, measured2.delta_vs_account_median] == [-10.0, 10.0]  # median 20
    assert unmeasured.delta_vs_account_median is None

def test_account_median_deltas_one_measured_plus_unmeasured_stays_none():
    # only ONE measured row in the group (the other is None) -> degenerate, no delta.
    measured = _lr("a", 50.0)
    unmeasured = LiftRow(variant_hook="u", account="a", platform="instagram", lift_score=None, loop_state="s")  # type: ignore[arg-type]
    account_median_deltas([measured, unmeasured])
    assert measured.delta_vs_account_median is None

def test_account_median_deltas_fail_open_on_bad_input():
    # mirrors lineage_stats' blanket fail-open: a non-iterable / attribute-less arg must not raise.
    account_median_deltas(None)          # no exception
    account_median_deltas([object()])    # rows without .account/.lift_score are skipped, not fatal

def test_lift_row_carries_account_median_delta_field():
    # the additive field exists and defaults None WITHOUT disturbing the existing delta_vs_best.
    r = _lr("a", 5.0)
    assert r.delta_vs_account_median is None and r.delta_vs_best is None

def test_lift_page_renders_delta_arrow_glyphs(tmp_path):
    # end-to-end: /lift shows a green ▲ for the above-median row and a red ▼ for the below-median one.
    # The ledger is PERSISTED (the /lift route does a fresh Ledger.load), so seed via a transaction.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/videos/show.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="big drop", state=MomentState.clipped, hook="SHARED"))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/clips/clip_1.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued))
        for i, (pid, lift) in enumerate([("p_lo", 10.0), ("p_mid", 30.0), ("p_hi", 50.0)]):
            led.add_post(Post(id=pid, parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                              caption="x", state=PostState.analyzed, metrics={"lift_score": lift},
                              public_url="dryrun://%s" % pid))
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    h = app.test_client().get("/lift").data.decode()
    assert "delta-arrow" in h and "delta-up" in h and "delta-down" in h   # median 30 -> HYPE +20 up, CALM -20 down
    assert "▲" in h and "▼" in h


def test_lift_compound_row_demotes_delta_vs_best_when_arrow_shows(tmp_path):
    # MOL-111 item 1: a row with BOTH the T-15 delta-arrow (vs account median) AND a lineage Δ-vs-best
    # must not render two parallel visible comparatives. The T-15 arrow stays visible; the Δ-vs-best
    # figure is demoted into a title= attribute (no visible "vs best" text on that row).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/videos/show.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="big drop", state=MomentState.clipped, hook="SHARED"))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/clips/clip_1.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued))
        for i, (pid, lift) in enumerate([("p_lo", 10.0), ("p_mid", 30.0), ("p_hi", 50.0)]):
            led.add_post(Post(id=pid, parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                              caption="x", state=PostState.analyzed, metrics={"lift_score": lift},
                              public_url="dryrun://%s" % pid))
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    h = app.test_client().get("/lift").data.decode()
    assert "delta-arrow" in h                                  # the T-15 arrow still renders (visible comparative)
    assert "delta-best" not in h                               # the Δ-vs-best figure is NOT a visible cell span
    assert "vs best" in h                                      # ...but its value survives, in a title= attribute


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
    led.add_post(Post(id="p_edit", parent_id="clip_edit", account="a", account_id="1",
                      platform=Platform.instagram, caption="EDIT", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_recent", parent_id="clip_edit", account="a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPED", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=2)), public_url="dryrun://p_recent"))
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
        led.add_post(Post(id=f"p_{cid}", parent_id=cid, account="a", account_id="1",
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
        led.add_post(Post(id=f"p_{cid}", parent_id=cid, account="a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                          scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    assert review_counts(cards)["awaiting"] == 2                # both editable, sort didn't drop one

def test_group_posted_by_day_publish_day(tmp_path):
    from fanops.studio.views import group_posted_by_day, PostedRow
    rows = [PostedRow(post_id="p1", clip_id="c", account="a", platform="instagram", caption="x",
                      public_url=None, scheduled_time="2026-06-01T00:00:00Z", lift_score=None,
                      published_at="2026-06-05T10:00:00Z"),
            PostedRow(post_id="p2", clip_id="c", account="a", platform="instagram", caption="y",
                      public_url=None, scheduled_time="2026-06-05T00:00:00Z", lift_score=None,
                      published_at="2026-06-05T20:00:00Z"),
            # no published_at -> falls back to scheduled_time day
            PostedRow(post_id="p3", clip_id="c", account="a", platform="instagram", caption="z",
                      public_url=None, scheduled_time="2026-06-02T00:00:00Z", lift_score=None,
                      published_at=None),
            # neither aware time -> undated, sorts last
            PostedRow(post_id="p4", clip_id="c", account="a", platform="instagram", caption="w",
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
    rows = [PostedRow(post_id="pn", clip_id="c", account="a", platform="instagram", caption="x",
                      public_url=None, scheduled_time="2026-06-05T00:00:00", lift_score=None,
                      published_at=None)]   # NAIVE scheduled_time -> undated (no local-tz guess)
    groups = group_posted_by_day(rows)
    assert [d for d, _ in groups] == ["undated"]

def test_group_posted_by_day_operator_tz_shifts_day(tmp_path, monkeypatch):
    # MOL-83: a 23:30Z ts falls on 2026-01-02 in Asia/Dubai (+04) but 2026-01-01 in UTC.
    # With cfg carrying that operator_tz, the row groups under the LOCAL day, not the UTC day.
    from fanops.studio.views import group_posted_by_day, PostedRow
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "Asia/Dubai")
    cfg = Config(root=tmp_path)
    rows = [PostedRow(post_id="p1", clip_id="c", account="a", platform="instagram", caption="x",
                      public_url=None, scheduled_time=None, lift_score=None,
                      published_at="2026-01-01T23:30:00Z")]
    assert [d for d, _ in group_posted_by_day(rows)] == ["2026-01-01"]          # cfg omitted -> UTC day (unchanged)
    assert [d for d, _ in group_posted_by_day(rows, cfg=cfg)] == ["2026-01-02"] # cfg -> operator-local day

def test_posted_library_row_carries_published_at(tmp_path):
    # PostedRow must expose published_at so the grouper keys on the TRUE publish day.
    from fanops.studio.views import posted_library
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c.mp4", state=ClipState.published))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, scheduled_time="2026-06-01T00:00:00Z",
                      published_at="2026-06-05T10:00:00Z", public_url="dryrun://p1"))
    rows = posted_library(led, cfg)
    assert rows[0].published_at == "2026-06-05T10:00:00Z"

def test_review_card_surfaces_removed_hook(tmp_path):
    # A clip whose moment had its hook STRIPPED (is_weak_hook dup/template) surfaces the removed hook on the
    # card, so Review can badge it + let the operator restore it. The clip still rendered clean.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.moments["mom_1"].hook_removed = "made it and lost everything"   # what the guard stripped
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, public_url="dryrun://p_edit"))
    ed = [c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
          if c.bucket == "editable" and c.clip_id == "clip_1"][0]
    assert ed.hook_removed == "made it and lost everything"


# ---- P1: suggest_time helper + suggested_time on read-models (per-account operator scheduling) ----
def _post(account="a", platform=Platform.instagram, parent_id="clip_1"):
    return Post(id="p", parent_id=parent_id, account=account, account_id="1", platform=platform,
                caption="x", state=PostState.awaiting_approval, public_url="dryrun://p")

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
        raw = surface_time(NOW, "a", "instagram", date_str, 0, clip_id=pid, lead_minutes=0)
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
    s = _surface(_post(), persona=None, now=NOW, cfg=cfg, led=Ledger.load(cfg))
    assert s.suggested_time is not None and parse_iso(s.suggested_time) > NOW

def test_schedulerow_carries_suggested_time(tmp_path):
    # a queued (editable) row carries the suggestion; a read-only past row carries None.
    from fanops.studio.views import schedule_rows
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pq", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="pp", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, scheduled_time=_z(NOW - timedelta(hours=1)),
                      public_url="dryrun://pp"))
    rows = {r.post_id: r for r in schedule_rows(led, cfg, now=NOW)}
    assert rows["pq"].suggested_time is not None and parse_iso(rows["pq"].suggested_time) > NOW
    assert rows["pp"].suggested_time is None         # read-only past row gets no suggestion

def test_suggested_time_with_broken_clip_lineage_still_renders(tmp_path):
    # the suggestion needs ONLY account/platform/parent_id (all on the Post) — a post whose parent clip
    # is missing from the ledger still builds the read-model with a suggestion; no crash.
    from fanops.studio.views import _surface
    cfg = Config(root=tmp_path)
    s = _surface(_post(parent_id="ghost_clip"), persona=None, now=NOW, cfg=cfg, led=Ledger.load(cfg))
    assert s.suggested_time is not None and parse_iso(s.suggested_time) > NOW


# ---- Sprint 0: ops truth (home failed/live_trackable + spine recovery CTA) ----
from fanops.studio.views import build_spine

def _spine_counts(**kw):
    base = {"sources": 0, "batches": 0, "awaiting": 0, "scheduled": 0, "posted": 0, "failed": 0, "live_trackable": 0}
    base.update(kw); return base

def test_build_spine_failed_blocks_caught_up():
    spine = build_spine(counts=_spine_counts(sources=2, posted=3, live_trackable=2, failed=9), has_accounts=True, here=None)
    assert spine.next_endpoint == "posted"
    assert "9" in spine.next_label and "failed" in spine.next_label.lower()

def test_build_spine_caught_up_only_when_live_trackable_and_clean():
    spine = build_spine(counts=_spine_counts(sources=2, live_trackable=4, failed=0), has_accounts=True, here=None)
    assert spine.next_endpoint is None
    assert "caught up" in spine.next_label.lower()

# ---- Face 2: home_status / golive_accounts / home_batches (status home + batch entry + per-account metrics) ----
from fanops.studio.views import home_status, golive_accounts, home_batches, golive_status
from fanops.batches import create_batch

def _seed_home(cfg):
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_source(Source(id="src_tp", source_path="/v/tp.mp4", language="en", origin_kind="third_party"))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, public_url="dryrun://p1"))
    led.add_post(Post(id="p2", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, public_url="dryrun://p2"))
    led.add_post(Post(id="p3", parent_id="clip_1", account="b", account_id="2", platform=Platform.instagram,
                      caption="x", state=PostState.published, public_url="dryrun://p3"))
    led.save(); return led

def test_home_status_counts(tmp_path):
    cfg = Config(root=tmp_path); _seed_home(cfg)
    st = home_status(cfg)
    assert st.counts["sources"] == 1                                  # native only (the third_party src excluded)
    assert st.counts["awaiting"] == 1 and st.counts["scheduled"] == 1 and st.counts["posted"] == 1
    assert st.mode == "dryrun" and st.is_live is False

def test_home_status_failed_and_live_trackable(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="plive", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="live", state=PostState.published, public_url="https://www.instagram.com/reel/abc/"))
    led.add_post(Post(id="pdry", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="dry", state=PostState.published, public_url="dryrun://pdry"))
    led.add_post(Post(id="pfail", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="fail", state=PostState.failed, error_reason="postiz 429"))
    led.add_post(Post(id="pinfl", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="wait", state=PostState.needs_reconcile, submission_id="cmqzabc"))
    led.save()
    c = home_status(cfg).counts
    assert c["failed"] == 1
    assert c["live_trackable"] == 1
    assert c["inflight"] == 1
    assert c["posted"] == 2

def test_home_awaiting_counts_moments_not_posts(tmp_path):
    # Root fix: Home 'Awaiting' is the MOMENT count (size of the Review approve-worklist), NOT the raw
    # awaiting-POST count — a clip fans out to many per-account surface posts, so counting posts overstates
    # the worklist (the live '57 posts vs 17 moments' bug). awaiting_posts retains the raw count for the tip.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@c", "account_id": "3", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    for i, h in enumerate(["a", "b", "c"]):            # 3 awaiting SURFACE posts, ONE moment (clip_1)
        led.add_post(Post(id=f"pa{i}", parent_id="clip_1", account=h, account_id=str(i + 1),
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, public_url="dryrun://clip_1"))
    led.save()
    st = home_status(cfg)
    assert st.counts["awaiting"] == 1                     # ONE moment, not three posts
    assert st.counts["awaiting_posts"] == 3              # raw surface count retained (Home tooltip)

def test_home_awaiting_matches_review_worklist(tmp_path):
    # Single source of truth: Home's awaiting count == the Review tab's editable-card count, by construction.
    from fanops.studio.views_review import review_buckets as _rb, review_counts as _rc
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="pa", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, public_url="dryrun://pa"))
    led.add_post(Post(id="pb", parent_id="clip_1", account="b", account_id="2", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, public_url="dryrun://pb"))
    led.save()
    now = datetime.now(timezone.utc)
    review_awaiting = _rc(_rb(led, Accounts.load(cfg), cfg, now=now))["awaiting"]
    assert home_status(cfg).counts["awaiting"] == review_awaiting    # one definition, cannot drift

def test_home_status_by_account(tmp_path):
    cfg = Config(root=tmp_path); _seed_home(cfg)
    assert home_status(cfg).by_account == {"a": 2, "b": 1}          # on-disk post facts, never fabricated

def test_home_status_batches_count(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    create_batch(led, name="Launch", target_accounts=["a"], now_iso="2026-06-22T00:00:00.000001Z"); led.save()
    assert home_status(cfg).counts["batches"] == 1

def test_home_status_fail_open(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed_home(cfg)
    def _boom(c): raise RuntimeError("torn")
    monkeypatch.setattr(Ledger, "load", _boom)
    st = home_status(cfg)
    assert st.counts == {"sources": 0, "batches": None, "awaiting": 0, "awaiting_posts": 0, "scheduled": 0,
                         "inflight": 0, "due_soon": 0, "live_today": 0, "live_trackable": 0, "failed": 0, "posted": 0}
    assert st.by_account == {}                                        # zeroed shell, never a 500

def test_golive_accounts_parity_with_golive_status(tmp_path):
    cfg = Config(root=tmp_path); _seed_home(cfg)
    assert golive_accounts(cfg) == golive_status(cfg).accounts        # shared helper = single source of truth

def test_home_batches_counts_posts_born(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    b = create_batch(led, name="Launch", target_accounts=["a"], now_iso="2026-06-22T00:00:00.000001Z")
    led.add_post(Post(id="pb", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, batch_id=b.id, public_url="dryrun://pb")); led.save()
    hb = home_batches(cfg)
    assert len(hb) == 1 and hb[0].posts_born == 1 and hb[0].is_zero_result is False

def test_home_batches_flags_zero_result(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    ghost = create_batch(led, name="Ghost", target_accounts=["ghost"], now_iso="2026-06-22T00:00:00.000001Z")
    led.add_source(Source(id="s_ghost", source_path="/v.mp4", batch_id=ghost.id))   # sources > 0, 0 posts -> true zero-result
    create_batch(led, name="All", target_accounts=[], now_iso="2026-06-22T00:00:00.000002Z"); led.save()  # [] ALL-sentinel
    by_name = {h.name: h for h in home_batches(cfg)}
    assert by_name["Ghost"].is_zero_result is True and by_name["Ghost"].is_emptied is False
    assert by_name["Ghost"].sources_in_batch == 1
    assert by_name["All"].is_zero_result is False

def test_home_batches_flags_emptied_shell(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    create_batch(led, name="Shell", target_accounts=["ghost"], now_iso="2026-06-22T00:00:00.000001Z")   # batch only, 0 sources
    led.save()
    hb = home_batches(cfg)[0]
    assert hb.is_emptied is True and hb.is_zero_result is False and hb.sources_in_batch == 0

def test_home_batches_fail_open(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    def _boom(c): raise RuntimeError("torn")
    monkeypatch.setattr(Ledger, "load", _boom)
    assert home_batches(cfg) == []


# ---------------------------------------------------------------- M3a: per-account length/cut/framing on the card ----
def test_review_surface_carries_per_account_length_cut_and_framing(tmp_path):
    # M3a "review at scale": each Review surface shows the account's clip LENGTH band, whether it is a real
    # per-account CUT (M2b/M2c), and its pinned FRAMING — so the operator SEES the per-account differentiation.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [
        {"handle": "@long", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "clip_profile": "long", "framing": "top"},
        {"handle": "@a", "account_id": "2", "platforms": ["instagram"], "status": "active"},
    ])
    led = Ledger.load(cfg); _lineage(led)
    # @long: a REAL per-account cut (render is_account_cut=True), post stamped its own length profile
    led.add_render(Render(id="r_long", clip_id="clip_1", account="long", surface_key="long/instagram",
                          hook_text="H", path="/clips/r_long.mp4", state=RenderState.rendered, is_account_cut=True))
    led.add_post(Post(id="p_long", parent_id="clip_1", account="long", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                      render_id="r_long", clip_profile="long", scheduled_time=_z(NOW + timedelta(hours=3))))
    # @a: no render (shared-clip burn), the GLOBAL length, no pinned framing
    led.add_post(Post(id="p_a", parent_id="clip_1", account="a", account_id="2",
                      platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                      clip_profile="talk", scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    sp = {s.post_id: s for c in cards for s in c.surfaces}
    assert sp["p_long"].length_label == "28–45s"        # long band, en dash + 's' (mirrors moment_window)
    assert sp["p_long"].is_account_cut is True           # the render records a genuine per-account cut
    assert sp["p_long"].framing == "top"                 # the account's pinned crop, surfaced for the operator
    assert sp["p_a"].length_label == "12–22s"            # global talk band
    assert sp["p_a"].is_account_cut is False             # shared-clip burn, not a per-account cut
    assert sp["p_a"].framing is None                     # no pinned framing -> nothing shown

def test_review_surface_length_label_absent_when_no_profile(tmp_path):
    # defensive: a legacy post with no clip_profile -> no length label (band_for is not guessed from None)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_x", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                      clip_profile=None, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    sp = [s for c in cards for s in c.surfaces if s.post_id == "p_x"][0]
    assert sp.length_label is None and sp.is_account_cut is False


# ---- Production redesign: classify_post_delivery + schedule inflight lane ----
from fanops.studio.views_results import classify_post_delivery, schedule_lanes
from fanops.models import PostState as PS


def test_classify_post_delivery_states(tmp_path):
    p_await = Post(id="pa", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                   caption="x", state=PS.awaiting_approval)
    p_q = Post(id="pq", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
               caption="x", state=PS.queued)
    p_inf = Post(id="pi", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                 caption="x", state=PS.needs_reconcile, submission_id="cmqz_real_123")
    p_live = Post(id="pl", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                  caption="x", state=PS.published, public_url="https://instagram.com/reel/abc/")
    p_dry = Post(id="pd", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                 caption="x", state=PS.published, public_url="dryrun://pd")
    assert classify_post_delivery(p_await) == "awaiting"
    assert classify_post_delivery(p_q) == "queued"
    assert classify_post_delivery(p_inf) == "inflight"
    assert classify_post_delivery(p_live) == "live"
    assert classify_post_delivery(p_dry) == "dryrun"


def test_schedule_rows_inflight_lane(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_inf", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="inf", state=PS.needs_reconcile,
                      submission_id="cmqz_abc", scheduled_time=_z(NOW + timedelta(hours=1))))
    led.add_post(Post(id="p_q", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="q", state=PS.queued,
                      scheduled_time=_z(NOW + timedelta(hours=2))))
    rows = schedule_rows(led, cfg, now=NOW)
    lanes = schedule_lanes(rows)
    assert len(lanes.inflight) == 1 and lanes.inflight[0].post_id == "p_inf"
    assert lanes.inflight[0].lane == "inflight"
    assert len(lanes.upcoming) == 1


def test_posted_library_delivery_filter(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_post(Post(id="pl", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="live", state=PS.published, public_url="https://instagram.com/x/"))
    led.add_post(Post(id="pd", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="dry", state=PS.published, public_url="dryrun://pd"))
    from fanops.studio.views_results import posted_library
    assert len(posted_library(led, cfg, delivery="live")) == 1
    assert posted_library(led, cfg, delivery="live")[0].post_id == "pl"
    assert len(posted_library(led, cfg, delivery="dryrun")) == 1


# ---- Sprint 1: failure classification + recovery cockpit ----
from fanops.studio.views_results import classify_failure, failure_rollup


def _fail_post(pid, reason):
    return Post(id=pid, parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                caption="x", state=PS.failed, error_reason=reason)


def test_classify_failure_buckets():
    assert classify_failure(_fail_post("r1", "postiz 429 too many requests")) == "rate_limit"
    assert classify_failure(_fail_post("r2", "zernio upload 413 entity too large")) == "oversize"
    assert classify_failure(_fail_post("r3", "postiz 400 bad media url")) == "bad_payload"
    assert classify_failure(_fail_post("r4", "reconcile poll error: connection refused")) == "poll_error"
    assert classify_failure(_fail_post("r5", "publish failed: zernio.com Read timed out (read timeout=30)")) == "transient"
    assert classify_failure(_fail_post("r6", "something weird")) == "unknown"


def test_failure_rollup_counts_failed_posts(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("f429", "postiz 429"))
    led.add_post(_fail_post("f413", "zernio 413"))
    led.add_post(_fail_post("f400", "postiz 400"))
    led.save()
    roll = failure_rollup(led)
    assert roll["total"] == 3
    assert roll["buckets"]["rate_limit"] == 1
    assert roll["buckets"]["oversize"] == 1
    assert roll["buckets"]["bad_payload"] == 1


def test_posted_library_stamps_failure_kind(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("fx", "postiz 429"))
    from fanops.studio.views_results import posted_library
    row = posted_library(led, cfg, delivery="failed")[0]
    assert row.failure_kind == "rate_limit"


def test_posted_library_failure_kind_filter(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(_fail_post("a", "postiz 429"))
    led.add_post(_fail_post("b", "zernio 413"))
    from fanops.studio.views_results import posted_library
    assert len(posted_library(led, cfg, delivery="failed", failure_kind="rate_limit")) == 1
    assert posted_library(led, cfg, delivery="failed", failure_kind="rate_limit")[0].post_id == "a"


def test_delivery_audit_counts_buckets(tmp_path):
    from fanops.studio.views_results import delivery_audit
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="pl", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="live", state=PS.published, public_url="https://instagram.com/x/"))
    led.add_post(Post(id="pf", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="fail", state=PS.failed, error_reason="postiz 429"))
    led.add_post(Post(id="pi", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="wait", state=PS.needs_reconcile, submission_id="cmqzabc"))
    aud = delivery_audit(led)
    assert aud["live_trackable"] == 1 and aud["inflight"] == 1
    assert aud["buckets"]["rate_limit"] == 1 and aud["failed"] == 1
