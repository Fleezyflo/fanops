# tests/test_results_page.py — U10: one Results surface at /posted that keeps the shipped library
# unchanged, folds the Lift lens + a gate-honest "What's working" panel onto the same page, and
# 301-redirects /lift -> /posted. No new aggregation code: dim rankings flow ONLY through
# digest.aggregate_by_dim (single path); the panel shows the FULL per-value reach ranking (the rows the
# actuators read BEFORE the p4_min_reach_gap winner selection). learning_validated + p4_unlocked gate the
# panel per dim (frozen / collecting / ranked); the tag-exposure <details> stays and NO hashtag is
# performance-ranked anywhere.
import inspect
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.digest import aggregate_by_dim
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, LIFT_SCORE, Source, Moment
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _seed_published(cfg, *, pid, clip="clip_1", lift=None, account="a", hook=None, state=PostState.published,
                    metrics_extra=None, when="2026-06-01T00:00:00Z"):
    with Ledger.transaction(cfg) as led:
        if clip not in led.clips:
            mid = f"m_{clip}"
            if not led.moments.get(mid):
                led.add_source(Source(id="src_p", source_path="/s.mp4"))
                led.add_moment(Moment(id=mid, parent_id="src_p", content_token="0-7", start=0, end=7, reason="r", hook=hook))
            led.add_clip(Clip(id=clip, parent_id=mid, path=f"/c/{clip}.mp4", state=ClipState.published))
        elif hook is not None:
            c = led.clips[clip]; mom = led.moments.get(c.parent_id)
            if mom is not None and not (mom.hook or "").strip():
                led.moments[c.parent_id] = mom.model_copy(update={"hook": hook})
        metrics = {} if lift is None else {LIFT_SCORE: lift}
        metrics.update(metrics_extra or {})
        led.add_post(Post(id=pid, parent_id=clip, account=account, account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=state,
                          scheduled_time=when, public_url=f"https://insta/{pid}",
                          metrics=metrics))


def _validate(cfg):
    """Auto-cutover stamp: proves the live-metric field shape so learning_validated(cfg) is True."""
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})


def _dim_post(led, pid, *, dim, value, reach, state=PostState.analyzed):
    """An analyzed post with ONE stamped creative dim + a reach metric — the shape aggregate_by_dim reads."""
    led.add_post(Post(id=pid, parent_id="c_dim", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=state, metrics={"reach": reach}, public_url="dryrun://%s" % pid,
                      **{dim: value}))


# ── one merged Results page carries library + lift lens + what's-working + a legend ─────────────────
def test_posted_renders_library_lift_insights_legend(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p1", clip="clip_1", lift=0.5, hook="H", state=PostState.analyzed)
    html = _client(cfg).get("/posted").data.decode()
    assert "posted-scan-list" in html                    # the shipped library markers (unchanged)
    assert "posted-mode-banner" in html                  # the dryrun/live mode banner is preserved
    assert "Lift by variant" in html                     # the folded-in Lift lens heading
    assert "What's working" in html                      # the gate-honest insights panel
    assert "<details" in html                             # the collapsible metrics legend element


# ── /lift 301-redirects to /posted, preserving the account query verbatim ───────────────────────────
def test_lift_redirects_to_posted_preserving_account(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).get("/lift?account=@a")
    assert r.status_code == 301
    assert (r.headers.get("Location") or "").endswith("/posted?account=@a")


# ── MOL-50 degraded-lift lens still reads correctly once folded onto /posted ────────────────────────
def test_degraded_lift_lens_on_posted(tmp_path):
    # Ported from tests/test_studio_posted_results.py (uniform-degraded case) but asserted against /posted:
    # the table-level note + the quiet per-row marker render on the MERGED page (loud badge dropped).
    cfg = Config(root=tmp_path)
    for i in range(3):
        _seed_published(cfg, pid=f"d{i}", clip=f"clip_{i}", lift=0.1 * i, hook=f"H{i}",
                        state=PostState.analyzed,
                        metrics_extra={"lift_degraded": True, "lift_missing_keys": ["retention"]})
    html = _client(cfg).get("/posted").data.decode()
    assert "retention data missing" in html.lower()      # table-level note emitted once...
    assert 'class="badge degraded"' not in html          # ...loud per-row badge dropped...
    assert "degraded-quiet" in html                      # ...replaced by the quiet per-row marker (missing key)
    assert html.count("degraded-quiet") == 3             # one quiet marker per degraded row


# ── What's-working: honest "collecting" state under the P4 signal threshold ─────────────────────────
def test_whats_working_collecting_state(tmp_path):
    # learning is VALIDATED (plumbing proven) but a dim has <8 attributed posts per bucket -> the panel
    # must say "collecting" and show the honest "N of 8" progress copy for the best-filled bucket.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _validate(cfg)
    for i in range(3):                                    # 3 (< 8) analyzed posts on one clip_profile value
        _dim_post(led, f"cp{i}", dim="clip_profile", value="short", reach=100.0 + i)
    panel = views.whats_working_panel(led, cfg)
    length = next(r for r in panel if r.dim == "clip_profile")
    assert length.state == "collecting"
    assert length.values == []                            # nothing ranked while collecting
    assert "3 of 8" in (length.progress or "")           # honest numerator from aggregate_by_dim


# ── What's-working: ranked order is EXACTLY the aggregate_by_dim reach sort (single source) ──────────
def test_whats_working_ranked_matches_aggregate_by_dim(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _validate(cfg)
    # >=8 posts backing each of >=2 distinct clip_profile values, distinct mean reach -> p4_unlocked True.
    for i in range(8):
        _dim_post(led, f"sh{i}", dim="clip_profile", value="short", reach=1000.0)
    for i in range(8):
        _dim_post(led, f"lo{i}", dim="clip_profile", value="long", reach=100.0)
    panel = views.whats_working_panel(led, cfg)
    length = next(r for r in panel if r.dim == "clip_profile")
    assert length.state == "ranked"
    expected = sorted(aggregate_by_dim(led, "clip_profile").items(), key=lambda kv: -kv[1]["reach_mean"])
    assert [v for v, _row in length.values] == [v for v, _row in expected]
    assert length.values[0][0] == "short"                # the high-reach value leads the ranking


# ── no hashtag is performance-ranked anywhere; the tag-exposure block still renders ─────────────────
def test_no_hashtag_performance_on_page(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_p", source_path="/s.mp4"))
        led.add_moment(Moment(id="m_c", parent_id="src_p", content_token="0-7", start=0, end=7, reason="r", hook="H"))
        led.add_clip(Clip(id="clip_1", parent_id="m_c", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=PostState.analyzed,
                          scheduled_time="2026-06-01T00:00:00Z", public_url="https://insta/p1",
                          hashtags=["#fyp", "#viral"], metrics={LIFT_SCORE: 0.4, "reach": 500.0}))
    html = _client(cfg).get("/posted").data.decode()
    assert "tag-exposure" in html                         # the exposure <details> block is still present
    low = html.lower()
    # the page never ranks a hashtag BY performance (no reach/lift-per-tag ranking UI).
    assert "hashtag reach" not in low and "reach by tag" not in low and "tag reach" not in low
    assert "hashtag performance" not in low and "top hashtags" not in low


# ── single aggregation path: whats_working_panel has NO local reach re-aggregation ──────────────────
def test_no_duplicate_reach_aggregation():
    src = inspect.getsource(views.whats_working_panel)
    body = src.split('"""')[2] if src.count('"""') >= 2 else src   # drop the docstring (prose mentions reach)
    assert "aggregate_by_dim" in body                     # rankings flow through the ONE aggregator...
    # ...and the panel never RE-DERIVES reach: no assignment to a reach_mean local, no raw reach metric read
    # (a second aggregation loop would compute the sort key itself instead of reading aggregate_by_dim's row).
    assert "reach_mean =" not in body and "reach_mean=" not in body
    assert '.get("reach"' not in body and ".metrics" not in body
