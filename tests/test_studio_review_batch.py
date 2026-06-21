# tests/test_studio_review_batch.py — Face 4: account-first, batch-grouped Review. ReviewCard carries the
# REAL Batch (Face 1's denormalized Post.batch_id), a pure first-appearance grouper (None -> 'Ungrouped'
# LAST), a clip dedup across editable+recent, and collapsible <details> batch sections. No schema/migration
# (read-model only); the None/unbatched path renders byte-identical. Mirrors test_studio_p5_account.py.
import json
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import (Source, Moment, Clip, Post, Batch, Platform, PostState, ClipState,
                           MomentState, Fmt)
from fanops.studio.views import ReviewCard, review_buckets, group_review_by_batch

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed_accounts(cfg, handles=("@a", "@b")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def _lineage(led, *, cid="clip_1", mid="mom_1", sid="src_1", batch_id=None):
    led.add_source(Source(id=sid, source_path="/v/show.mp4", language="en", batch_id=batch_id))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c/clip.mp4", aspect=Fmt.r9x16, state=ClipState.queued))

def _await(led, pid, cid, account, *, batch_id=None, hours=3):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, batch_id=batch_id,
                      scheduled_time=_z(NOW + timedelta(hours=hours))))


# ---- T1: ReviewCard batch fields (= Post.batch_id, title = Batch.name) ----
def test_review_card_carries_batch_field(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch Week", target_accounts=["@a"]))
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x")
    card = next(c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW) if c.bucket == "editable")
    assert card.batch_id == "batch_x"               # = Post.batch_id (NOT Source.id)
    assert card.batch_title == "Launch Week"        # = Batch.name via led.get_batch

def test_review_card_unbatched_batch_field_none(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a")              # no batch_id stamped
    card = next(c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW) if c.bucket == "editable")
    assert card.batch_id is None and card.batch_title is None   # byte-identical: nothing batched

def test_review_card_batch_fields_default_none():
    # back-compat: constructing ReviewCard with the EXISTING keyword set still works (None defaults).
    rc = ReviewCard(clip_id="c", preview_url="", source_name="", label="", moment_window="", reason="",
                    language=None, subtitles_burned=False, held=False, held_reason=None,
                    transcript_excerpt=None, surfaces=[], bucket="editable")
    assert rc.batch_id is None and rc.batch_title is None


# ---- T2: dedup a clip across editable + recent ----
def test_clip_in_editable_and_recent_dedups_to_editable(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_aw", "clip_1", "@a")             # awaiting -> editable card
    led.add_post(Post(id="p_pub", parent_id="clip_1", account="@a", account_id="1",   # same clip, shipped
                      platform=Platform.instagram, caption="c", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=1))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    for_clip = [c for c in cards if c.clip_id == "clip_1"]
    assert [c.bucket for c in for_clip] == ["editable"]   # ONE card; the recent dup is dropped (two <video> fix)


# ---- T3: group_review_by_batch (first-appearance order, None -> 'Ungrouped' LAST) ----
def _rc(cid, batch_id=None, batch_title=None):
    return ReviewCard(clip_id=cid, preview_url="", source_name="", label="", moment_window="", reason="",
                      language=None, subtitles_burned=False, held=False, held_reason=None,
                      transcript_excerpt=None, surfaces=[], bucket="editable",
                      batch_id=batch_id, batch_title=batch_title)

def test_group_review_by_batch_first_appearance_ungrouped_last():
    cards = [_rc("c1", "bx", "Launch"), _rc("c2", None), _rc("c3", "bx", "Launch"),
             _rc("c4", "by", "Promo"), _rc("c5", None)]
    groups = group_review_by_batch(cards)
    assert [(g[0], g[1]) for g in groups] == [("bx", "Launch"), ("by", "Promo"), (None, "Ungrouped")]
    by_id = {g[0]: g[2] for g in groups}
    assert [c.clip_id for c in by_id["bx"]] == ["c1", "c3"]      # within-batch INPUT order preserved
    assert [c.clip_id for c in by_id[None]] == ["c2", "c5"]      # all unbatched collect in one group

def test_group_review_by_batch_all_unbatched_single_group():
    groups = group_review_by_batch([_rc("c1"), _rc("c2")])
    assert len(groups) == 1 and groups[0][0] is None and groups[0][1] == "Ungrouped"

def test_group_review_by_batch_empty():
    assert group_review_by_batch([]) == []


# ---- T4: collapsible batch <details> sections in the rendered Review ----
flask = pytest.importorskip("flask")

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_review_renders_collapsible_batch_section(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch Week", target_accounts=["@a"]))
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x"); led.save()
    html = _client(cfg).get("/review").data.decode()
    assert "<details" in html and "Launch Week" in html         # batch name in a collapsible <summary>

def test_review_unbatched_renders_ungrouped_section(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); led.save()
    html = _client(cfg).get("/review").data.decode()
    assert "Ungrouped" in html and b"c" in _client(cfg).get("/review").data   # card still renders


# ---- Face 4 follow-up: B3 header / B4 excluded / C3 affinity / B2 filter ----
def _editable(led, cfg):
    return next(c for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW) if c.bucket == "editable")

def test_header_card_carries_targets_state_created(tmp_path):   # B3
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch", target_accounts=["@a"], created_at="2026-06-22T00:00:00Z"))
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x")
    card = _editable(led, cfg)
    assert card.batch_targets == ["@a"] and card.batch_state == "open" and card.batch_created == "2026-06-22T00:00:00Z"

def test_header_unbatched_fields_empty(tmp_path):               # B3 byte-identity
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a")
    card = _editable(led, cfg)
    assert card.batch_targets == [] and card.batch_state is None and card.batch_created is None and card.batch_excluded == 0

def test_excluded_counts_active_accounts_outside_target(tmp_path):   # B4
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch", target_accounts=["@a"]))   # active {@a,@b}, target {@a} -> 1 excluded
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x")
    assert _editable(led, cfg).batch_excluded == 1

def test_excluded_all_sentinel_is_zero(tmp_path):              # B4 ALL-sentinel
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_all")
    led.add_batch(Batch(id="batch_all", name="Everyone", target_accounts=[]))   # [] == ALL -> excludes nobody
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_all")
    assert _editable(led, cfg).batch_excluded == 0

def test_affinity_from_moment(tmp_path):                       # C3
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    led.moments["mom_1"].affinities = ["@a", "@b"]
    _await(led, "p_a", "clip_1", "@a")
    assert _editable(led, cfg).affinities == ["@a", "@b"]

def test_affinity_default_empty_when_uncast(tmp_path):         # C3 byte-identity (casting OFF)
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a")
    assert _editable(led, cfg).affinities == []

def test_batch_filter_keeps_only_that_batch(tmp_path):         # B2
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, cid="clip_x", mid="mom_x", sid="src_x", batch_id="bx")
    _lineage(led, cid="clip_y", mid="mom_y", sid="src_y", batch_id="by")
    led.add_batch(Batch(id="bx", name="X", target_accounts=[])); led.add_batch(Batch(id="by", name="Y", target_accounts=[]))
    _await(led, "p_x", "clip_x", "@a", batch_id="bx"); _await(led, "p_y", "clip_y", "@a", batch_id="by")
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW, batch="bx")
    assert {c.clip_id for c in cards} == {"clip_x"}            # only bx's card; by dropped

def test_batch_filter_composes_with_account(tmp_path):         # B2 + P5
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, cid="clip_x", mid="mom_x", sid="src_x", batch_id="bx")
    led.add_batch(Batch(id="bx", name="X", target_accounts=[]))
    _await(led, "p_xa", "clip_x", "@a", batch_id="bx"); _await(led, "p_xb", "clip_x", "@b", batch_id="bx")
    assert {c.clip_id for c in review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account="@a", batch="bx")} == {"clip_x"}
    assert review_buckets(led, Accounts.load(cfg), cfg, now=NOW, account="@a", batch="by") == []   # wrong batch -> none

def test_route_batch_filter_scope_preserved_and_header_rendered(tmp_path):   # B2 R1 + B3 render
    cfg = Config(root=tmp_path); _seed_accounts(cfg, ("@a", "@b")); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch", target_accounts=["@a"], created_at="2026-06-22T00:00:00Z"))
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x"); led.save()
    html = _client(cfg).get("/review?batch=batch_x").data.decode()
    assert "batch=batch_x" in html                 # POST/pagination URLs carry the batch scope (R1)
    assert "Launch" in html and "→ @a" in html and "1 account(s) excluded" in html   # B3 header + B4 line

def test_route_unknown_batch_is_recoverable(tmp_path):        # B2 stale id
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg)
    _lineage(led, batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="Launch", target_accounts=["@a"]))
    _await(led, "p_a", "clip_1", "@a", batch_id="batch_x"); led.save()
    r = _client(cfg).get("/review?batch=ghost")
    assert r.status_code == 200 and b"show all batches" in r.data   # recoverable, never a 404

def test_route_unbatched_has_no_batch_param_or_excluded(tmp_path):   # nonregression / byte-identity
    cfg = Config(root=tmp_path); _seed_accounts(cfg); led = Ledger.load(cfg); _lineage(led)
    _await(led, "p_a", "clip_1", "@a"); led.save()
    html = _client(cfg).get("/review").data.decode()
    assert "batch=" not in html and "excluded by batch target" not in html and "show all batches" not in html
