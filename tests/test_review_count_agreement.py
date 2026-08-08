# tests/test_review_count_agreement.py — T2.7 (Unit: same-page-count-agreement). ONE Review render used to
# show three different "awaiting" numbers over three different sets: the progress line over the full scoped
# set, the bucket <h3> over the 24-card PAGE SLICE, and the live strip over an account-only scope that ignored
# batch/source/state. Plus an unscoped failure rollup rendered beside a scoped list on the Posted page.
# These tests pin the three agreements: header = pre-pagination bucket total (slice named only when it
# differs), strip scope = body scope, failure rollup scope = library scope.
import json
import re
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio.views_common import GRID_PAGE_SIZE
from fanops.studio.views_results import failure_rollup
from fanops.studio.views_review import review_buckets, review_counts, review_progress

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _seed_accounts(cfg, handles=("a", "b")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))


def _source(led, sid):
    led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en"))


def _clip(led, cid, *, sid="src_1", held=False):
    """One clip on its own moment under `sid` — the lineage review_buckets/can_promote walk."""
    mid = f"mom_{cid}"
    led.add_moment(Moment(id=mid, parent_id=sid, content_token=f"{cid}-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path=f"/c/{cid}.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued, held=held))


def _awaiting(led, pid, cid, account, *, batch_id=None):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, batch_id=batch_id,
                      scheduled_time=_z(NOW + timedelta(hours=3))))


def _failed(led, pid, cid, account, reason, *, batch_id=None):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.failed, error_reason=reason, batch_id=batch_id,
                      scheduled_time=_z(NOW - timedelta(hours=3))))


# ============ (a) the bucket header counts the FULL scoped bucket, not the page slice ============

def test_bucket_header_shows_full_total_and_names_the_slice(tmp_path):
    # 40 editable cards vs a 24-card page: the <h3> used to read "(24)" beside a progress line saying 40.
    n = 40
    assert n > GRID_PAGE_SIZE, "fixture must overflow one page for this test to mean anything"
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _source(led, "src_1")
        for i in range(n):
            _clip(led, f"clip_{i}"); _awaiting(led, f"p_{i}", f"clip_{i}", "a")
    html = _client(cfg).get("/review?account=all").data.decode()
    assert f"Awaiting approval ({n})" in html                  # the FULL scoped bucket, pre-pagination
    assert f"showing {GRID_PAGE_SIZE}" in html                 # ...and the slice is named, not hidden
    assert f"Awaiting approval ({GRID_PAGE_SIZE})" not in html  # the old slice-only header is gone


def test_bucket_header_omits_the_slice_marker_when_nothing_is_paginated(tmp_path):
    # Under one page there is no second number to explain — the header stays exactly as it was.
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _source(led, "src_1")
        for i in range(3):
            _clip(led, f"clip_{i}"); _awaiting(led, f"p_{i}", f"clip_{i}", "a")
    html = _client(cfg).get("/review?account=all").data.decode()
    assert "Awaiting approval (3)" in html
    assert "· showing" not in html


def test_every_bucket_header_reports_its_own_total(tmp_path):
    # One entry per bucket key the template iterates. Kept under one page ON PURPOSE: a bucket with no card
    # in the slice renders no header at all (pre-existing pagination behaviour, untouched here), so a
    # multi-bucket assertion only means something while everything fits.
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _source(led, "src_1")
        for i in range(10):
            _clip(led, f"clip_{i}"); _awaiting(led, f"p_{i}", f"clip_{i}", "a")
        _clip(led, "clip_held", held=True)                     # -> the 'held' bucket, post-less
    cards = review_buckets(Ledger.load(cfg), Accounts.load(cfg), cfg, now=NOW)
    expected = {"editable": 10, "prepared": 0, "held": 1, "recent": 0}
    assert {k: sum(1 for c in cards if c.bucket == k) for k in expected} == expected
    assert len(cards) <= GRID_PAGE_SIZE                        # the premise the header assertions rest on
    html = _client(cfg).get("/review?account=all").data.decode()
    assert "Awaiting approval (10)" in html and "Held for review (1)" in html


# ============ (b) the live strip reports the same scope as the body it sits above ============

def _strip_awaiting(html):
    m = re.search(r"Awaiting <strong>(\d+)</strong>", html)
    assert m is not None, "live strip did not render an awaiting count"
    return int(m.group(1))


def _seed_two_batches(cfg):
    """@a carries 3 editable cards, only ONE of them in batch bx — so an unscoped count cannot pass as scoped."""
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _source(led, "src_1"); _source(led, "src_2")
        _clip(led, "clip_bx", sid="src_1"); _awaiting(led, "p_bx", "clip_bx", "a", batch_id="bx")
        _clip(led, "clip_by", sid="src_1"); _awaiting(led, "p_by", "clip_by", "a", batch_id="by")
        _clip(led, "clip_s2", sid="src_2"); _awaiting(led, "p_s2", "clip_s2", "a", batch_id="by")


def test_live_strip_counts_agree_with_the_body_under_a_batch_filter(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    c = _client(cfg)
    strip = c.get("/review/live?account=a&batch=bx").data.decode()
    body = c.get("/review?account=a&batch=bx").data.decode()
    assert _strip_awaiting(strip) == 1                          # the batch's ONE card, not the account's 3
    assert 'data-awaiting="1"' in body                          # ...which is what the body reports too
    assert _strip_awaiting(c.get("/review/live?account=a").data.decode()) == 3   # unfiltered still counts all 3


def test_live_strip_counts_agree_with_the_body_under_a_source_filter(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    c = _client(cfg)
    assert _strip_awaiting(c.get("/review/live?account=a&source=src_2").data.decode()) == 1
    assert 'data-awaiting="1"' in c.get("/review?account=a&source=src_2").data.decode()


def test_live_strip_scope_matches_review_progress(tmp_path):
    # The progress line and the strip are two renders of the same scoped set — they must not diverge.
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    led = Ledger.load(cfg); accounts = Accounts.load(cfg)
    scoped = review_buckets(led, accounts, cfg, now=NOW, account="a", batch="bx")
    assert review_counts(scoped)["awaiting"] == review_progress(scoped)["awaiting"] == 1
    strip = _client(cfg).get("/review/live?account=a&batch=bx").data.decode()
    assert _strip_awaiting(strip) == review_progress(scoped)["awaiting"]


def test_no_phantom_new_work_banner_under_a_filter(tmp_path):
    # The reported symptom: an account-only poll beside a filtered body made counts.awaiting > shown, so the
    # strip advertised new work that did not exist. shown = the body's data-awaiting for the same URL.
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    strip = _client(cfg).get("/review/live?account=a&batch=bx&shown=1").data.decode()
    assert "load them" not in strip


def test_poll_and_refresh_urls_carry_the_active_filters(tmp_path):
    # Both hx-gets must re-state the scope: the poll so its next tick counts the same set, the refresh so
    # loading the body does not silently clear the operator's filter.
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    strip = _client(cfg).get("/review/live?account=a&batch=bx&source=src_1&state=awaiting&shown=0").data.decode()
    for url in re.findall(r'hx-get="([^"]+)"', strip):
        assert "batch=bx" in url and "source=src_1" in url and "state=awaiting" in url, url


def test_unfiltered_poll_url_is_unchanged(tmp_path):
    # None-valued args are dropped by url_for, so an unfiltered strip renders byte-identically to before.
    cfg = Config(root=tmp_path); _seed_two_batches(cfg)
    strip = _client(cfg).get("/review/live?account=a").data.decode()
    assert "batch=" not in strip and "source=" not in strip and "state=" not in strip


# ============ (c) the failure rollup is scoped to the same set as the list beside it ============

def _seed_failures(cfg):
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _source(led, "src_1"); _source(led, "src_2")
        _clip(led, "clip_1", sid="src_1"); _clip(led, "clip_2", sid="src_2")
        _failed(led, "f_a1", "clip_1", "a", "postiz 429", batch_id="bx")
        _failed(led, "f_a2", "clip_1", "a", "zernio 413", batch_id="by")
        _failed(led, "f_a3", "clip_2", "a", "postiz 400", batch_id="bx")
        _failed(led, "f_b1", "clip_1", "b", "postiz 429", batch_id="bx")


def test_failure_rollup_scopes_never_exceed_the_unscoped_total(tmp_path):
    cfg = Config(root=tmp_path); _seed_failures(cfg)
    led = Ledger.load(cfg)
    whole = failure_rollup(led)
    assert whole["total"] == 4
    for scoped in (failure_rollup(led, account="a"), failure_rollup(led, batch="bx"),
                   failure_rollup(led, source="src_1")):
        assert scoped["total"] <= whole["total"]


def test_failure_rollup_scoped_tallies_match_a_hand_count(tmp_path):
    cfg = Config(root=tmp_path); _seed_failures(cfg)
    led = Ledger.load(cfg)
    by_account = failure_rollup(led, account="a")
    assert by_account["total"] == 3
    assert by_account["buckets"] == {"rate_limit": 1, "oversize": 1, "bad_payload": 1,
                                     "transient": 0, "unknown": 0}
    assert failure_rollup(led, batch="bx")["total"] == 3        # f_a1, f_a3, f_b1
    assert failure_rollup(led, source="src_2")["total"] == 1     # f_a3 only (clip_2 -> src_2)
    combined = failure_rollup(led, account="a", batch="bx", source="src_1")
    assert combined["total"] == 1 and combined["buckets"]["rate_limit"] == 1   # f_a1 alone


def test_failure_rollup_kwargless_call_is_unchanged(tmp_path):
    # The three existing callers pass no kwargs; all-None defaults must keep them whole-ledger.
    cfg = Config(root=tmp_path); _seed_failures(cfg)
    led = Ledger.load(cfg)
    assert failure_rollup(led) == failure_rollup(led, account=None, batch=None, source=None)
    assert failure_rollup(led)["total"] == len([p for p in led.posts.values()
                                                if p.state is PostState.failed])


def test_failure_rollup_scope_matches_the_posted_library_it_sits_beside(tmp_path):
    # The whole point: the chip total and the row count under it answer the same question.
    from fanops.studio.views_results import posted_library
    cfg = Config(root=tmp_path); _seed_failures(cfg)
    led = Ledger.load(cfg)
    rows = posted_library(led, cfg, account="a", batch="bx", delivery="failed")
    assert failure_rollup(led, account="a", batch="bx")["total"] == len(rows)


def test_posted_failure_chips_carry_the_source_filter(tmp_path):
    # A scoped count must link to the scoped page — otherwise clicking "All failed (1)" lands on a page
    # whose own total reads 4.
    cfg = Config(root=tmp_path); _seed_failures(cfg)
    html = _client(cfg).get("/posted?delivery=failed&source=src_2").data.decode()
    nav = re.search(r'<nav class="failure-chips".*?</nav>', html, re.S)
    assert nav is not None, "no failure chips rendered"
    chips = re.findall(r'href="([^"]+)"', nav.group(0))
    assert chips and all("source=src_2" in h for h in chips), chips
    assert "All failed (1)" in html                              # scoped to src_2, not the whole ledger's 4
