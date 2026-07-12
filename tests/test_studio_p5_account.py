# tests/test_studio_p5_account.py — P5: per-account cockpit. A pure read-model filter (?account=<handle>)
# + chips + Schedule account-grouping + Results time/metric breakdown columns. No mutation, no schema,
# no accounts.json/Post change; the account UNIVERSE is derived from the posts in each list, never from
# Accounts.active() (so a retired account's history stays filterable). The htmx-swap-after-mutation
# scope-preservation (R1) rides the account on the POST URL into request.args.
import json
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState,
                           Fmt, LIFT_SCORE)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed_accounts(cfg, handles=("a", "b")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def _lineage(led, *, cid="clip_1", mid="mom_1", sid="src_1"):
    led.add_source(Source(id=sid, source_path="/v/show.mp4", language="en"))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c/clip.mp4", aspect=Fmt.r9x16, state=ClipState.queued))


# ---- T1: accounts_in helper (dual row-shape) + Review account filter ----
def test_accounts_in_handles_dicts_and_dataclasses():
    from fanops.studio.views import accounts_in
    class _Row:
        def __init__(self, a): self.account = a
    mixed = [_Row("b"), {"account": "a"}, _Row("a"), {"account": "c"}]
    assert accounts_in(mixed) == ["a", "b", "c"]            # distinct, sorted, both shapes

def test_review_buckets_filters_to_one_account(tmp_path):
    from fanops.studio.views import review_buckets
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    # one clip fanned to @a + @b -> ONE card with two surfaces
    led.add_post(Post(id="p_a", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="a", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_b", parent_id="clip_1", account="b", account_id="1", platform=Platform.instagram,
                      caption="b", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account="a")
    ed = [c for c in cards if c.bucket == "editable"]
    assert len(ed) == 1                                        # card kept (a surface is @a)
    # the card itself still carries BOTH surfaces (the fan-out is one card); only cards with NO @a surface drop

def test_review_account_filter_drops_card_with_no_matching_surface(tmp_path):
    from fanops.studio.views import review_buckets
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_a", mid="m_a", sid="s_a")
    _lineage(led, cid="clip_b", mid="m_b", sid="s_b")
    led.add_post(Post(id="p_a", parent_id="clip_a", account="a", account_id="1", platform=Platform.instagram,
                      caption="a", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_b", parent_id="clip_b", account="b", account_id="1", platform=Platform.instagram,
                      caption="b", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account="a")
    ids = {c.clip_id for c in cards if c.bucket == "editable"}
    assert ids == {"clip_a"}                                   # @b-only card dropped

def test_review_account_filter_drops_postless_cards(tmp_path):
    from fanops.studio.views import review_buckets
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)   # clip_1: queued, NO posts -> prepared
    assert any(c.bucket == "prepared" for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW))  # present under None
    assert not review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account="a")             # post-less card has no surface -> dropped

def test_review_no_account_is_byte_identical(tmp_path):
    from fanops.studio.views import review_buckets
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_a", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="a", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    a = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    b = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account=None)
    assert [c.clip_id for c in a] == [c.clip_id for c in b]    # None path unchanged


# ---- T2: Schedule filter + account grouping ----
def _queued(led, pid, account, hours):
    led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=hours))))

def test_schedule_rows_filtered_by_account(tmp_path):
    from fanops.studio.views import schedule_rows
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _queued(led, "p_a", "a", 3); _queued(led, "p_b", "b", 4)
    rows = schedule_rows(led, cfg, now=NOW, account="a")
    assert {r.post_id for r in rows} == {"p_a"}

def test_schedule_rows_none_account_unchanged(tmp_path):
    from fanops.studio.views import schedule_rows
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _queued(led, "p_b", "b", 4); _queued(led, "p_a", "a", 1)
    base = [r.post_id for r in schedule_rows(led, cfg, now=NOW)]
    assert base == [r.post_id for r in schedule_rows(led, cfg, now=NOW, account=None)]  # identical
    assert base == ["p_a", "p_b"]                             # still pure time-sort (1h before 4h), NOT account-grouped

def test_schedule_grouped_account_order(tmp_path):
    # the grouped read sorts account-then-time so a running header can run; within an account, time order holds.
    from fanops.studio.views import schedule_rows, group_schedule_by_account
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _queued(led, "b_late", "b", 9); _queued(led, "a_late", "a", 5); _queued(led, "a_early", "a", 1)
    groups = group_schedule_by_account(schedule_rows(led, cfg, now=NOW))
    assert [g[0] for g in groups] == ["a", "b"]             # account-sorted headers
    assert [r.post_id for r in dict(groups)["a"]] == ["a_early", "a_late"]   # time order within account


# ---- T3: Posted filter + metric breakdown ----
def _published(led, pid, account, *, metrics, when="2026-06-01T00:00:00Z"):
    led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c.mp4", state=ClipState.published)) if "clip_1" not in led.clips else None
    led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, scheduled_time=when, metrics=metrics, public_url="dryrun://clip_1"))

def test_posted_library_filtered_by_account(tmp_path):
    from fanops.studio.views import posted_library
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _published(led, "p_a", "a", metrics={LIFT_SCORE: 1.0})
    _published(led, "p_b", "b", metrics={LIFT_SCORE: 2.0})
    assert {r.post_id for r in posted_library(led, cfg, account="a")} == {"p_a"}
    assert {r.post_id for r in posted_library(led, cfg)} == {"p_a", "p_b"}   # None = all

def test_posted_row_carries_metric_breakdown(tmp_path):
    from fanops.studio.views import posted_library
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _published(led, "p_a", "a", metrics={LIFT_SCORE: 1.0, "saves": 5, "shares": 2, "retention": 0.4, "reach": 1000})
    r = posted_library(led, cfg)[0]
    assert (r.saves, r.shares, r.retention, r.reach) == (5, 2, 0.4, 1000)

def test_posted_row_absent_metric_is_none(tmp_path):
    from fanops.studio.views import posted_library
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _published(led, "p_a", "a", metrics={LIFT_SCORE: 1.0})   # no saves/shares/retention/reach
    r = posted_library(led, cfg)[0]
    assert r.saves is None and r.shares is None and r.retention is None and r.reach is None

def test_posted_row_new_fields_default_none():
    # back-compat: constructing PostedRow with the EXISTING keyword set (ending published_at) still works.
    from fanops.studio.views import PostedRow
    r = PostedRow(post_id="p", clip_id="c", account="a", platform="instagram", caption="x",
                  public_url=None, scheduled_time=None, lift_score=None, published_at="2026-06-05T10:00:00Z")
    assert r.saves is None and r.shares is None and r.retention is None and r.reach is None


# ---- T4: Publish filter ----
def _manual(led, pid, account):
    led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time="2020-01-01T00:00:00Z", public_url="dryrun://clip_1"))

def test_publish_queue_filtered_by_account(tmp_path):
    from fanops.studio.views import publish_queue
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _lineage(led)
    _manual(led, "p_a", "a"); _manual(led, "p_b", "b"); led.save()
    assert {r["post_id"] for r in publish_queue(cfg, now=NOW, account="a")} == {"p_a"}

def test_publish_queue_none_account_unchanged(tmp_path):
    from fanops.studio.views import publish_queue
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _lineage(led)
    _manual(led, "p_a", "a"); _manual(led, "p_b", "b"); led.save()
    a = {r["post_id"] for r in publish_queue(cfg, now=NOW)}
    b = {r["post_id"] for r in publish_queue(cfg, now=NOW, account=None)}
    assert a == b == {"p_a", "p_b"}


# ---- T5: Results (lift) filter + P1 time column + P3 breakdown ----
def _variant(led, pid, account, hook, lift, *, metrics_extra=None, when="2026-06-01T00:00:00Z"):
    cid, mid = f"clip_{pid}", f"mom_{pid}"
    if not led.sources.get("src_1"):
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en"))
    if not led.moments.get(mid):
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped, hook=hook))
        led.add_clip(Clip(id=cid, parent_id=mid, path="/c/clip.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    m = {LIFT_SCORE: lift}; m.update(metrics_extra or {})
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed,
                      scheduled_time=when, metrics=m, public_url=f"dryrun://{pid}"))

def test_lift_rows_filtered_by_account(tmp_path):
    from fanops.studio.views import lift_rows
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _variant(led, "p_a", "a", "HA", 90.0); _variant(led, "p_b", "b", "HB", 80.0)
    rows = lift_rows(led, cfg, Accounts.load(cfg), account="a").variant_rows
    assert {r.account for r in rows} == {"a"} and len(rows) == 1

def test_lift_filter_keeps_empty_reason(tmp_path):
    # a filtered-to-empty view still returns a non-None, honest empty reason (filter BEFORE empty-reason).
    from fanops.studio.views import lift_rows
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _variant(led, "p_a", "a", "HA", 90.0)
    view = lift_rows(led, cfg, Accounts.load(cfg), account="b")   # no @b variants
    assert view.variant_rows == [] and view.variant_empty_reason is not None

def test_lift_amplify_candidates_filtered_by_account(tmp_path, monkeypatch, mocker):
    from fanops.studio.views import lift_rows
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _variant(led, "p_a", "a", "HA", 90.0); _variant(led, "p_b", "b", "HB", 80.0)
    mocker.patch("fanops.variant_amplify.amplify_candidates",
                 return_value=[{"post_id": "p_b", "winning_hook": "HB", "evidence": "streak"}])
    assert lift_rows(led, cfg, Accounts.load(cfg), account="a").amplify_rows == []   # @b candidate dropped
    assert len(lift_rows(led, cfg, Accounts.load(cfg), account="b").amplify_rows) == 1

def test_lift_row_carries_scheduled_time_and_metrics(tmp_path):
    from fanops.studio.views import lift_rows
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _variant(led, "p_a", "a", "HA", 90.0, when="2026-06-02T09:00:00Z",
             metrics_extra={"saves": 7, "shares": 3, "retention": 0.5, "reach": 5000})
    r = lift_rows(led, cfg, Accounts.load(cfg)).variant_rows[0]
    assert r.scheduled_time == "2026-06-02T09:00:00Z"
    assert (r.saves, r.shares, r.retention, r.reach) == (7, 3, 0.5, 5000)

def test_lift_row_new_fields_default_none():
    from fanops.studio.views import LiftRow
    r = LiftRow(variant_hook="H", account="a", platform="instagram", lift_score=1.0, loop_state="x")
    assert r.scheduled_time is None and r.saves is None and r.reach is None


# ================= T6/T7: route wiring, param preservation, chips, a11y =================
flask = pytest.importorskip("flask")

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_two_accounts_all_surfaces(cfg):
    # @a + @b each get their OWN clip (so Review's card-level filter cleanly drops the non-matching card) and
    # TWO awaiting posts (so approving one leaves the account in the chip universe), plus a queued (Schedule +
    # Publish), a published (Posted), and an analyzed variant (Results).
    _seed_accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en"))
        for acct in ("a", "b"):
            tag = acct.strip("@")
            led.add_moment(Moment(id=f"mom_{tag}", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped, hook=f"HOOK_{tag}"))
            led.add_clip(Clip(id=f"clip_{tag}", parent_id=f"mom_{tag}", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
            for n in ("", "2"):                          # aw_a + aw_a2 (approve one, the other keeps @a in the universe)
                led.add_post(Post(id=f"aw_{tag}{n}", parent_id=f"clip_{tag}", account=acct, account_id="1",
                                  platform=Platform.instagram, caption=f"await {tag}", state=PostState.awaiting_approval,
                                  scheduled_time=_z(NOW + timedelta(hours=3))))
            led.add_post(Post(id=f"q_{tag}", parent_id=f"clip_{tag}", account=acct, account_id="1",
                              platform=Platform.instagram, caption=f"queued {tag}", state=PostState.queued,
                              scheduled_time=_z(datetime.now(timezone.utc) + timedelta(hours=3))))
            led.add_post(Post(id=f"pub_{tag}", parent_id=f"clip_{tag}", account=acct, account_id="1",
                              platform=Platform.instagram, caption=f"posted {tag}", state=PostState.published,
                              scheduled_time="2026-06-01T00:00:00Z", public_url=f"https://insta/{tag}"))
            led.add_post(Post(id=f"var_{tag}", parent_id=f"clip_{tag}", account=acct, account_id="1",
                              platform=Platform.instagram, caption=f"variant {tag}", state=PostState.analyzed,
                              scheduled_time="2026-06-01T00:00:00Z", metrics={LIFT_SCORE: 50.0, "saves": 3}, public_url="dryrun://1"))

@pytest.mark.parametrize("path,present,absent", [
    ("/review?view=list&account=@a", b"await a", b"await b"),
    ("/schedule?account=@a", b"q_a", b"q_b"),
    ("/posted?account=@a", b"https://insta/a", b"https://insta/b"),
    ("/publish?account=@a", b"q_a", b"q_b"),
    ("/posted?account=@a", b"HOOK_a", b"HOOK_b"),   # U10: the Lift lens (variant hooks) is folded onto /posted
])
def test_surface_account_param_filters(tmp_path, path, present, absent):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    r = _client(cfg).get(path)
    assert r.status_code == 200 and present in r.data and absent not in r.data

def test_feed_sentinel_preserves_account_review(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    # > REVIEW_FEED_SLICE awaiting posts on @a so the lazy-load sentinel renders
    for i in range(30):
        led.add_clip(Clip(id=f"c{i}", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=f"p{i}", parent_id=f"c{i}", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    html = _client(cfg).get("/review?account=@a").data.decode()
    assert "feed-sentinel" in html and "account=a" in html and "Show more" not in html

def test_show_more_link_preserves_account_publish(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    for i in range(30):
        led.add_post(Post(id=f"p{i}", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.queued, scheduled_time="2020-01-01T00:00:00Z", public_url="dryrun://clip_1"))
    led.save()
    html = _client(cfg).get("/publish?account=@a").data.decode()
    assert "Show more" in html and "account=" in html

def test_approve_keeps_account_scope(tmp_path):
    # U6: POST /posts/approve?account=@a re-renders the @a feed (active switcher chip + only @a).
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    r = _client(cfg).post("/posts/approve?account=@a", data={"ids": ["aw_a"]})
    assert r.status_code == 200
    assert b"await b" not in r.data                          # the swapped panel stays scoped to @a
    assert b"chip active" in r.data and b">a <" in r.data    # switcher marks @a active (not aria-current)

def test_schedule_move_keeps_account_scope(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    new = _z(datetime.now(timezone.utc) + timedelta(days=2))
    r = _client(cfg).post("/schedule/move/q_a?account=@a", data={"new_time": new})
    assert r.status_code == 200 and b"q_b" not in r.data     # re-rendered bucket stays @a-scoped

def test_posted_repost_keeps_account_scope(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    r = _client(cfg).post("/posts/repost/pub_a?account=@a")
    assert r.status_code == 200 and b"https://insta/b" not in r.data   # re-rendered library stays @a-scoped

def test_account_filter_chips_rendered(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review?account=all").data.decode()
    assert ">All<" in html or ">All " in html                # the All chip is always rendered
    assert "account=" in html and "account=" in html # one chip per distinct account
    active = _client(cfg).get("/review?account=@a").data.decode()
    assert 'aria-current="page"' in active and "active" in active   # active chip styled + a11y

def test_unknown_account_param_is_safe(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    r = _client(cfg).get("/review?account=@nope")
    assert r.status_code == 200                              # never a 500
    assert b"await a" not in r.data and b"await b" not in r.data   # zero matching cards
    assert b">All<" in r.data or b"All" in r.data            # the All chip is still present (recoverable)

def test_empty_filter_message_is_account_aware(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review?account=@nope").data.decode()
    assert "nope" in html                                   # the empty block names the filter,
    assert "No footage yet" not in html                      # not the misleading default empty message

def test_blank_account_param_is_all(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    r = _client(cfg).get("/review?view=list&account=")                 # blank -> None (All), both accounts shown
    assert r.status_code == 200 and b"await a" in r.data and b"await b" in r.data

def test_schedule_all_view_renders_per_account_headers(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/schedule").data.decode()
    assert ">a<" in html and ">b<" in html
    one = _client(cfg).get("/schedule?account=@a").data.decode()
    assert ">@b<" not in one and "q_b" not in one                       # scoped, header suppressed

def test_review_live_strip_is_account_scoped(tmp_path):
    # HIGH (review fix): the live strip counts the SAME scope the body shows, so a filtered worklist's
    # banner is not pinned open by the unscoped poll. review_counts tallies editable CARDS (clips): @a has
    # ONE editable card (clip_a), @b has one -> @a-scoped count is 1, while the unscoped All view is 2.
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    scoped = _client(cfg).get("/review/live?account=@a").data.decode()
    assert "Awaiting <strong>1</strong>" in scoped              # @a-scoped (1 card), not the unscoped 2
    assert "account=a" in scoped                               # the 5s poll + 'load them' carry the scope forward
    allview = _client(cfg).get("/review/live").data.decode()
    assert "Awaiting <strong>2</strong>" in allview            # unscoped: both clips' editable cards
