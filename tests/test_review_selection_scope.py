# tests/test_review_selection_scope.py — MOL-82: the review-matrix / account-lanes account-selection lookup
# must be SCOPED to its source (built ONCE per page render), NOT rescanned over the whole ledger-wide
# account_selections map per moment (review_matrix) or per (account × moment) (account_lanes). These tests pin
# the per-render cost: selections_of_source is called a BOUNDED number of times per view render, independent of
# the moment count, the account count, AND the unrelated-source count in the ledger. Behavior stays identical:
# the cast handles the views derive are byte-identical to the pre-fix cast_handles_for path.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger, selection_index_for_source
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState,
                           AccountSelection, SelectionMethod, account_selection_id)
from fanops.studio import views

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")


def _accts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))


def _seed(cfg, *, moments=3, unrelated_sources=0, unrelated_sels_each=0):
    """Target source src1 with `moments` decided moments, @a cast on ALL of them + @b cast on the first;
    both have a real post per moment so the matrix draws cells. `unrelated_sources` sources each carry
    `unrelated_sels_each` AccountSelections that a source-scoped lookup must NEVER examine."""
    _accts(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
        mids = []
        for i in range(moments):
            mid = f"m{i}"; mids.append(mid)
            led.add_moment(Moment(id=mid, parent_id="src1", content_token=f"{i}-{i+5}", start=i * 10,
                                  end=i * 10 + 5, reason="r", state=MomentState.decided))
            led.add_clip(Clip(id=f"c{i}", parent_id=mid, path=f"/c{i}.mp4", state=ClipState.queued))
            led.add_post(Post(id=f"p_a_{i}", parent_id=f"c{i}", account="a", account_id="1",
                              platform=Platform.instagram, caption="A", state=PostState.awaiting_approval,
                              public_url=f"dryrun://p_a_{i}"))
            led.add_post(Post(id=f"p_b_{i}", parent_id=f"c{i}", account="b", account_id="2",
                              platform=Platform.instagram, caption="B", state=PostState.awaiting_approval,
                              public_url=f"dryrun://p_b_{i}"))
        led.add_account_selection(AccountSelection(id=account_selection_id("src1", "a"), source_id="src1",
                                                   account="a", moment_ids=list(mids), method=SelectionMethod.llm))
        led.add_account_selection(AccountSelection(id=account_selection_id("src1", "b"), source_id="src1",
                                                   account="b", moment_ids=[mids[0]], method=SelectionMethod.llm))
        # unrelated ledger history — the whole-map scan the fix must stop paying for on every cell.
        for s in range(unrelated_sources):
            sid = f"other{s}"
            led.add_source(Source(id=sid, source_path=f"/other{s}.mp4", created_at=_z(NOW)))
            for k in range(unrelated_sels_each):
                acct = f"@ghost{s}_{k}"
                led.add_account_selection(AccountSelection(id=account_selection_id(sid, acct), source_id=sid,
                                                           account=acct, moment_ids=["x"], method=SelectionMethod.llm))


def _counting_ledger(cfg):
    """Load the ledger and wrap selections_of_source with a call counter (list holds [count])."""
    led = Ledger.load(cfg); calls = [0]
    orig = led.selections_of_source
    def _wrapped(source_id):
        calls[0] += 1
        return orig(source_id)
    led.selections_of_source = _wrapped   # type: ignore[method-assign]
    return led, calls


# ── the scoped index helper (source-of-truth for both views) ─────────────────
def test_selection_index_matches_cast_handles_for(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=5, unrelated_sels_each=4)
    led = Ledger.load(cfg)
    idx = selection_index_for_source(led, "src1")
    for mid in ("m0", "m1", "m2"):
        assert idx.get(mid, []) == led.cast_handles_for("src1", mid)   # byte-identical, per moment


def test_selection_index_scans_source_once(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=4, unrelated_sources=3, unrelated_sels_each=2)
    led, calls = _counting_ledger(cfg)
    selection_index_for_source(led, "src1")
    assert calls[0] == 1   # ONE whole-map scan builds the entire index, not one per moment


# ── review_matrix: bounded, not per-moment ───────────────────────────────────
def test_review_matrix_scan_count_independent_of_moments(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=6, unrelated_sources=3, unrelated_sels_each=2)
    led, calls = _counting_ledger(cfg); accts = Accounts.load(cfg)
    views.review_matrix(led, accts, cfg, source_id="src1", now=NOW)
    assert calls[0] == 1   # 6 moments must NOT drive 6 scans — one scoped index for the whole grid


def test_review_matrix_scan_count_independent_of_ledger_history(tmp_path):
    # the failure scenario: a small source rendered against a large historical account_selections map.
    small = Config(root=tmp_path / "small"); _seed(small, moments=3, unrelated_sources=0)
    big = Config(root=tmp_path / "big"); _seed(big, moments=3, unrelated_sources=200, unrelated_sels_each=5)
    ls, cs = _counting_ledger(small); lb, cb = _counting_ledger(big)
    views.review_matrix(ls, Accounts.load(small), small, source_id="src1", now=NOW)
    views.review_matrix(lb, Accounts.load(big), big, source_id="src1", now=NOW)
    assert cs[0] == cb[0]   # per-render scan count does not grow with unrelated ledger history


def test_review_matrix_cast_handles_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=4, unrelated_sels_each=3)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    mv = views.review_matrix(led, accts, cfg, source_id="src1", now=NOW)
    by_mid = {r.moment_id: r for r in mv.rows}
    # row.affinities is the DISPLAY-mapped cast set (the pre-fix path was _display_handles(cast_handles_for(...)));
    # the scoped index must produce the byte-identical result — so compare against that same projection.
    from fanops.studio.views_review import _display_handles, _handle_display_map
    _by_norm = _handle_display_map({a.handle: a for a in accts.accounts})
    for mid in ("m0", "m1", "m2"):
        assert by_mid[mid].affinities == _display_handles(led.cast_handles_for("src1", mid), _by_norm)
    # @a cast on all moments; @b only on m0 (display handles preserve the '@' — accounts.json keys carry it).
    assert set(by_mid["m0"].affinities) == {"a", "b"} and by_mid["m1"].affinities == ["a"]


# ── account_lanes: bounded, not per (account × moment) ────────────────────────
def test_account_lanes_scan_count_bounded(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=5, unrelated_sources=3, unrelated_sels_each=2)
    led, calls = _counting_ledger(cfg); accts = Accounts.load(cfg)
    views.account_lanes(led, accts, cfg, source_id="src1", now=NOW)
    # pre-fix: one scan per (account × moment) in the nested loop + a few direct scans → scales with H×M.
    # post-fix: a small CONSTANT (the direct universe/has-chosen reads + one scoped index), never H×M.
    assert calls[0] <= 4


def test_account_lanes_scan_count_independent_of_ledger_history(tmp_path):
    small = Config(root=tmp_path / "small"); _seed(small, moments=4, unrelated_sources=0)
    big = Config(root=tmp_path / "big"); _seed(big, moments=4, unrelated_sources=200, unrelated_sels_each=5)
    ls, cs = _counting_ledger(small); lb, cb = _counting_ledger(big)
    views.account_lanes(ls, Accounts.load(small), small, source_id="src1", now=NOW)
    views.account_lanes(lb, Accounts.load(big), big, source_id="src1", now=NOW)
    assert cs[0] == cb[0]


def test_account_lanes_cast_state_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=4, unrelated_sels_each=3)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    lv = views.account_lanes(led, accts, cfg, source_id="src1", now=NOW)
    lanes = {ln.account: ln for ln in lv.lanes}
    a_cast = {r.moment_id for r in lanes["a"].rows if r.is_cast}
    b_cast = {r.moment_id for r in lanes["b"].rows if r.is_cast}
    assert a_cast == {"m0", "m1", "m2"}      # @a cast on all
    assert b_cast == {"m0"}                    # @b cast only on m0 (durable AccountSelection truth, unchanged)
