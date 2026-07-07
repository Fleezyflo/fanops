# tests/test_review_selection_scope.py — MOL-82 (carried through P11/MOL-152): the review-matrix / account-lanes
# cast lookup must be SCOPED to its source and built ONCE per page render, NOT rescanned per moment. After the
# casting teardown the cast set IS Moment.affinities, and both views build a single source-scoped `_affinity_index`
# per render. These tests pin the per-render cost (one index build, independent of moment/account/ledger-history
# count) and the cast-state correctness (the affinity owners the views derive).
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState)
from fanops.studio import views
import fanops.studio.views_review as views_review

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")


def _accts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))


def _seed(cfg, *, moments=3, unrelated_sources=0, unrelated_moments_each=0):
    """Target source src1 with `moments` decided moments, @a cast on ALL of them + @b cast on the first (via
    Moment.affinities); both have a real post per moment so the matrix draws cells. `unrelated_sources` sources
    each carry `unrelated_moments_each` moments a source-scoped index must NEVER surface."""
    _accts(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
        mids = []
        for i in range(moments):
            mid = f"m{i}"; mids.append(mid)
            led.add_moment(Moment(id=mid, parent_id="src1", content_token=f"{i}-{i+5}", start=i * 10,
                                  end=i * 10 + 5, reason="r", state=MomentState.decided,
                                  affinities=["a", "b"] if i == 0 else ["a"]))
            led.add_clip(Clip(id=f"c{i}", parent_id=mid, path=f"/c{i}.mp4", state=ClipState.queued))
            led.add_post(Post(id=f"p_a_{i}", parent_id=f"c{i}", account="a", account_id="1",
                              platform=Platform.instagram, caption="A", state=PostState.awaiting_approval,
                              public_url=f"dryrun://p_a_{i}"))
            led.add_post(Post(id=f"p_b_{i}", parent_id=f"c{i}", account="b", account_id="2",
                              platform=Platform.instagram, caption="B", state=PostState.awaiting_approval,
                              public_url=f"dryrun://p_b_{i}"))
        # unrelated ledger history — the whole-map scan the scoped index must never grow with.
        for s in range(unrelated_sources):
            sid = f"other{s}"
            led.add_source(Source(id=sid, source_path=f"/other{s}.mp4", created_at=_z(NOW)))
            for k in range(unrelated_moments_each):
                led.add_moment(Moment(id=f"{sid}_m{k}", parent_id=sid, content_token=f"{k}-{k+5}",
                                      start=k * 10, end=k * 10 + 5, reason="r", state=MomentState.decided,
                                      affinities=[f"@ghost{s}_{k}"]))


def _counting_index(monkeypatch):
    """Wrap views_review._affinity_index with a call counter (list holds [count])."""
    calls = [0]
    orig = views_review._affinity_index
    def _wrapped(led, source_id):
        calls[0] += 1
        return orig(led, source_id)
    monkeypatch.setattr(views_review, "_affinity_index", _wrapped)
    return calls


# ── the scoped index helper (source-of-truth for both views) ─────────────────
def test_affinity_index_matches_cast_handles_for(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=5, unrelated_moments_each=4)
    led = Ledger.load(cfg)
    idx = views_review._affinity_index(led, "src1")
    for mid in ("m0", "m1", "m2"):
        assert idx.get(mid, []) == led.cast_handles_for("src1", mid)   # byte-identical, per moment


def test_affinity_index_scoped_to_source(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=5, unrelated_moments_each=4)
    led = Ledger.load(cfg)
    idx = views_review._affinity_index(led, "src1")
    assert set(idx) == {"m0", "m1", "m2"}   # only src1's moments; the 20 unrelated moments never appear


# ── review_matrix: one index build, not per-moment ───────────────────────────
def test_review_matrix_index_built_once(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg, moments=6, unrelated_sources=3, unrelated_moments_each=2)
    led = Ledger.load(cfg); accts = Accounts.load(cfg); calls = _counting_index(monkeypatch)
    views.review_matrix(led, accts, cfg, source_id="src1", now=NOW)
    assert calls[0] == 1   # 6 moments must NOT drive 6 index builds — one scoped index for the whole grid


def test_review_matrix_cast_handles_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=4, unrelated_moments_each=3)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    mv = views.review_matrix(led, accts, cfg, source_id="src1", now=NOW)
    by_mid = {r.moment_id: r for r in mv.rows}
    from fanops.studio.views_review import _display_handles, _handle_display_map
    _by_norm = _handle_display_map({a.handle: a for a in accts.accounts})
    for mid in ("m0", "m1", "m2"):
        assert by_mid[mid].affinities == _display_handles(led.cast_handles_for("src1", mid), _by_norm)
    # @a cast on all moments; @b only on m0.
    assert set(by_mid["m0"].affinities) == {"a", "b"} and by_mid["m1"].affinities == ["a"]


# ── account_lanes: one index build, not per (account × moment) ───────────────
def test_account_lanes_index_built_once(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg, moments=5, unrelated_sources=3, unrelated_moments_each=2)
    led = Ledger.load(cfg); accts = Accounts.load(cfg); calls = _counting_index(monkeypatch)
    views.account_lanes(led, accts, cfg, source_id="src1", now=NOW)
    assert calls[0] == 1   # one scoped index for every lane, never one per (account × moment)


def test_account_lanes_cast_state_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, moments=3, unrelated_sources=4, unrelated_moments_each=3)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    lv = views.account_lanes(led, accts, cfg, source_id="src1", now=NOW)
    lanes = {ln.account: ln for ln in lv.lanes}
    a_cast = {r.moment_id for r in lanes["a"].rows if r.is_cast}
    b_cast = {r.moment_id for r in lanes["b"].rows if r.is_cast}
    assert a_cast == {"m0", "m1", "m2"}      # @a cast on all
    assert b_cast == {"m0"}                    # @b cast only on m0 (Moment.affinities truth, unchanged)
