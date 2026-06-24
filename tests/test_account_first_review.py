# tests/test_account_first_review.py — Phase 4: Account-First Review & approval at scale. Adds a source/state
# filter + per-account/per-batch progress counts, SCOPE-STABLE htmx mutations (offset rides the URL so an
# approve on page N re-renders page N), an account-first PIVOT view (?view=account) with a true ultra-compact
# (zero-<video>) mode + fallback badges (⚠ shared-cut / ⚠ shared-hook, read Phase-3 Render provenance fail-open),
# and account×batch×source scoped bulk approve. Read-model + routes + templates only; no schema/migration; the
# moment-first view + every existing approve action stay byte-identical; htmx mutations stay HTTP 200; no
# auto-publish (posts stay awaiting_approval). Mirrors the seed harness in test_studio_review_batch.py /
# test_studio_p5_account.py.
import json
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import (Source, Moment, Clip, Post, Batch, Render, HookSource, Platform, PostState,
                           ClipState, MomentState, Fmt)
from fanops.studio.views import (SurfacePost, review_buckets, review_progress,
                                 account_pivot_rows, group_review_by_account_surface, source_universe)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_accounts(cfg, handles=("@a", "@b", "@c")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))


def _lineage(led, *, cid="clip_1", mid="mom_1", sid="src_1", path="/v/show.mp4", batch_id=None):
    led.add_source(Source(id=sid, source_path=path, language="en", batch_id=batch_id))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c/clip.mp4", aspect=Fmt.r9x16, state=ClipState.queued))


def _await(led, pid, cid, account, *, batch_id=None, render_id=None, hours=3):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, batch_id=batch_id, render_id=render_id,
                      scheduled_time=_z(NOW + timedelta(hours=hours))))


def _client(cfg):
    pytest.importorskip("flask")
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


# ============================ Task 1 — ?source= / ?state= arg readers ============================
def test_source_arg_blank_is_none(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led); led.save()
    c = _client(cfg)
    assert c.get("/review").status_code == 200          # absent ?source= -> unfiltered, never 500
    assert c.get("/review?source=").status_code == 200  # blank -> None, never 500

def test_state_arg_unknown_maps_to_none(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led); led.save()
    c = _client(cfg)
    # an unknown ?state= must map to None (the unfiltered view), never 500.
    assert c.get("/review?state=bogus").status_code == 200
    assert c.get("/review?state=awaiting").status_code == 200


# ============================ Task 2 — source/state filters narrow review_buckets ============================
def test_source_filter_narrows_to_one_source(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_a", mid="m_a", sid="s_1", path="/v/show1.mp4")
    _lineage(led, cid="clip_b", mid="m_b", sid="s_2", path="/v/show2.mp4")
    _await(led, "p_a", "clip_a", "@a")
    _await(led, "p_b", "clip_b", "@a")
    only_s1 = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, source="s_1")
    assert {c.clip_id for c in only_s1 if c.bucket == "editable"} == {"clip_a"}   # source s_1 -> clip_a only
    # source_key is the moment's parent (the stable source id), NOT the basename
    card_a = next(c for c in only_s1 if c.clip_id == "clip_a")
    assert card_a.source_key == "s_1"

def test_source_filter_none_is_byte_identical(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_a", mid="m_a", sid="s_1")
    _lineage(led, cid="clip_b", mid="m_b", sid="s_2")
    _await(led, "p_a", "clip_a", "@a"); _await(led, "p_b", "clip_b", "@a")
    base = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    same = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, source=None)
    assert [c.clip_id for c in base] == [c.clip_id for c in same]   # None default -> byte-identical card list

def test_state_filter_awaiting_keeps_only_editable(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_ed", mid="m_ed", sid="s_ed")
    _await(led, "p_ed", "clip_ed", "@a")                       # editable (awaiting)
    led.add_clip(Clip(id="clip_held", parent_id="m_ed", path="/c/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued, held=True, held_reason="risk"))   # held bucket
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, state="awaiting")
    assert {c.bucket for c in cards} == {"editable"}           # state=awaiting drops held/prepared/recent


# ============================ Task 5 — progress counts ============================
def test_review_progress_counts_buckets(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_ed", mid="m_ed", sid="s_ed")
    _await(led, "p_ed", "clip_ed", "@a")                                       # editable -> awaiting
    led.add_clip(Clip(id="clip_held", parent_id="m_ed", path="/c/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued, held=True, held_reason="risk"))  # held
    led.add_clip(Clip(id="clip_prep", parent_id="m_ed", path="/c/p.mp4", aspect=Fmt.r9x16,
                      state=ClipState.rendered))                               # prepared (post-less, preparable)
    prog = review_progress(review_buckets(led, Accounts.load(cfg), cfg, now=NOW))
    assert prog["awaiting"] == 1 and prog["held"] == 1 and prog["prepared"] == 1
    assert set(prog) == {"awaiting", "approved", "held", "prepared"}            # approved key present (0 here)


# ============================ Task 4/5 — account pivot rows + grouper ============================
def test_account_pivot_rows_only_selected_account(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); _await(led, "p_b", "clip_1", "@b"); _await(led, "p_c", "clip_1", "@c")
    rows = account_pivot_rows(led, Accounts.load(cfg), cfg, now=NOW, account="@a")
    assert [r.account for r in rows] == ["@a"]                  # ONLY @a's surface, the @b/@c fan-out dropped
    assert all(isinstance(r, SurfacePost) for r in rows)

def test_account_pivot_rows_no_account_is_empty(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a")
    assert account_pivot_rows(led, Accounts.load(cfg), cfg, now=NOW, account=None) == []   # no account -> no pivot

def test_group_review_by_account_surface_groups_by_day(tmp_path):
    # pure grouper: flat SurfacePost rows grouped by day, first-appearance order
    rows = [SurfacePost(post_id="p1", account="@a", platform="instagram", persona=None, caption="c",
                        hashtags=[], scheduled_time=None, media_url="/m/p1", state="awaiting_approval",
                        imminent=False, editable=True, day="2025-06-06"),
            SurfacePost(post_id="p2", account="@a", platform="instagram", persona=None, caption="c",
                        hashtags=[], scheduled_time=None, media_url="/m/p2", state="awaiting_approval",
                        imminent=False, editable=True, day="2025-06-06"),
            SurfacePost(post_id="p3", account="@a", platform="instagram", persona=None, caption="c",
                        hashtags=[], scheduled_time=None, media_url="/m/p3", state="awaiting_approval",
                        imminent=False, editable=True, day="2025-06-05")]
    groups = group_review_by_account_surface(rows)
    assert [d for d, _ in groups] == ["2025-06-06", "2025-06-05"]          # first-appearance day order
    assert [r.post_id for r in groups[0][1]] == ["p1", "p2"]


# ============================ Task 1 GOTCHA — source_universe helper ============================
def test_source_universe_lists_distinct_sources(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_a", mid="m_a", sid="s_1", path="/v/show1.mp4")
    _lineage(led, cid="clip_b", mid="m_b", sid="s_2", path="/v/show2.mp4")
    _await(led, "p_a", "clip_a", "@a"); _await(led, "p_b", "clip_b", "@a")
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    uni = source_universe(cards)
    assert {k for k, _ in uni} == {"s_1", "s_2"}                # keyed on the stable source id
    assert dict(uni)["s_1"] == "show1.mp4"                      # labelled by basename


# ============================ Task 6 — fallback badges, fail-open ============================
def test_surface_shared_hook_badge_when_field_set(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    # a render that FELL BACK to the shared moment hook -> hook_source=shared_fallback -> the ⚠ shared-hook signal
    r = Render(id="r1", clip_id="clip_1", account="@a", surface_key="@a|instagram",
               hook_text="shared", path="/c/r1.mp4", is_account_cut=False, hook_source=HookSource.shared_fallback)
    led.add_render(r)
    _await(led, "p_a", "clip_1", "@a", render_id="r1")
    card = next(c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW) if c.bucket == "editable")
    s = card.surfaces[0]
    assert s.hook_source == "shared_fallback"                   # P3 provenance surfaced on the SurfacePost
    assert s.is_account_cut is False                            # shared cut -> the ⚠ shared-cut signal

def test_surface_no_render_is_fail_open(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a")                         # no render_id -> no Render
    card = next(c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW) if c.bucket == "editable")
    s = card.surfaces[0]
    assert s.hook_source is None and s.is_account_cut is False  # absent provenance -> dark, no error


# ============================ Task 8 — scoped bulk approve (account × batch × source) ============================
def test_approve_account_scoped_by_source(tmp_path):
    from fanops.studio.actions import approve_account
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    led.add_batch(Batch(id="b1", name="Launch", target_accounts=["@a"]))
    _lineage(led, cid="clip_a", mid="m_a", sid="s_1")
    _lineage(led, cid="clip_b", mid="m_b", sid="s_2")
    _await(led, "p_a1", "clip_a", "@a", batch_id="b1")          # @a, batch b1, source s_1  -> APPROVE
    _await(led, "p_a2", "clip_b", "@a", batch_id="b1")          # @a, batch b1, source s_2  -> stay
    _await(led, "p_b1", "clip_a", "@b", batch_id="b1")          # @b -> stay
    led.save()
    res = approve_account(cfg, "@a", batch="b1", source="s_1")
    assert res.ok and res.detail["approved"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p_a1"].state is PostState.queued          # only the matching surface approved
    assert led.posts["p_a2"].state is PostState.awaiting_approval   # other source untouched
    assert led.posts["p_b1"].state is PostState.awaiting_approval   # other account untouched

def test_approve_account_scoped_source_idempotent(tmp_path):
    from fanops.studio.actions import approve_account
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, cid="clip_a", mid="m_a", sid="s_1")
    _await(led, "p_a1", "clip_a", "@a")
    led.save()
    assert approve_account(cfg, "@a", source="s_1").detail["approved"] == 1
    assert approve_account(cfg, "@a", source="s_1").detail["approved"] == 0   # re-run = 0 newly approved

def test_approve_account_dangling_lineage_not_over_approved(tmp_path):
    from fanops.studio.actions import approve_account
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    # a post whose clip has NO moment/source lineage must NOT match a source-scoped approve
    led.add_post(Post(id="p_orphan", parent_id="clip_gone", account="@a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    assert approve_account(cfg, "@a", source="s_1").detail["approved"] == 0   # dangling -> no source match


# ============================ HTTP — pivot view + offset + ultra-compact ============================
def test_pivot_view_renders_only_one_account(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); _await(led, "p_b", "clip_1", "@b"); led.save()
    c = _client(cfg)
    body = c.get("/review?view=account&account=@a").get_data(as_text=True)
    assert "p_a" in body and "p_b" not in body                 # only @a's surface is in the pivot

def test_pivot_view_no_account_does_not_500(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); led.save()
    c = _client(cfg)
    assert c.get("/review?view=account").status_code == 200    # view=account w/o ?account= falls back, never 500

def test_ultra_compact_omits_video_keeps_checkboxes(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); led.save()
    c = _client(cfg)
    body = c.get("/review?view=account&account=@a&compact=ultra").get_data(as_text=True)
    assert "<video" not in body                                # ZERO <video> in ultra-compact
    assert 'name="ids"' in body                                # bulk-approve checkboxes still present

def test_approve_on_page_n_stays_on_page_n(tmp_path):
    # seed > 2 pages of awaiting cards (GRID_PAGE_SIZE=24); approve at offset=24 must re-render offset 24
    cfg = Config(root=tmp_path); _seed_accounts(cfg, handles=("@a",)); led = Ledger.load(cfg)
    for i in range(60):
        _lineage(led, cid=f"clip_{i}", mid=f"m_{i}", sid=f"s_{i}")
        _await(led, f"p_{i}", f"clip_{i}", "@a")
    led.save()
    c = _client(cfg)
    # approve a non-existent id (no-op, still 200) carrying offset=24 -> the swap stays on page 2
    resp = c.post("/posts/approve?offset=24", data={"ids": "nope"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Showing 25–" in body                               # offset 24 preserved (1-based -> "25")

def test_review_default_is_byte_identical(tmp_path):
    # OFF firewall / default-render: the plain /review body must be unchanged by the new args being absent
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); led.save()
    c = _client(cfg)
    plain = c.get("/review").get_data(as_text=True)
    # a moment-first default render shows the moment card path (the per-account <video> switcher), not the pivot
    assert "review-body" in plain
    assert "account-pivot" not in plain                        # the pivot template is NOT rendered by default
