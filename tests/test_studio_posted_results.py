# tests/test_studio_posted_results.py — S6: make Posted & Results legible. The surfaces showed a raw
# lift_score + a bare metric breakdown but never "this hook BEAT that hook" — a repost/crosspost is a
# disconnected row though it shares clip_id with its origin. lineage_stats() is a PURE in-place annotation
# over the already-built rows: group by clip_id (the durable join key), rank by lift desc within the group,
# stamp sibling_count / rank / delta_vs_best. metric_peaks()+bar_pct() drive a proportional micro-bar.
#
# OFF-firewall note (deliberate, mirrors S4's reasoning): lineage_stats reads ONLY clip_id + lift_score —
# neither of which FANOPS_CREATIVE_VARIATION changes for already-shipped posts. The honest invariant is
# therefore FLAG-INDEPENDENCE (same rows in -> same annotation out regardless of the flag), NOT "panel
# byte-identical": with CV off, multiple accounts on one shared clip ARE a real lineage and SHOULD read as
# one. The annotation is additive, display-only, mints nothing, gates nothing — learning stays frozen.
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, LIFT_SCORE
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _row(post_id, clip_id, lift, **over):
    """A bare PostedRow with the fields lineage_stats/metric_peaks read (no ledger needed for unit tests)."""
    return views.PostedRow(post_id=post_id, clip_id=clip_id, account=over.get("account", "@a"),
                           platform="instagram", caption="x", public_url=None, scheduled_time=None,
                           lift_score=lift, saves=over.get("saves"), shares=over.get("shares"),
                           retention=over.get("retention"), reach=over.get("reach"),
                           variant_hook=over.get("variant_hook"))


def _seed_published(cfg, *, pid, clip="clip_1", lift=None, account="@a", hook=None, state=PostState.published,
                    variant_key=None, metrics_extra=None, when="2026-06-01T00:00:00Z"):
    with Ledger.transaction(cfg) as led:
        if clip not in led.clips:
            led.add_clip(Clip(id=clip, parent_id="m1", path=f"/c/{clip}.mp4", state=ClipState.published))
        metrics = {} if lift is None else {LIFT_SCORE: lift}
        metrics.update(metrics_extra or {})
        led.add_post(Post(id=pid, parent_id=clip, account=account, account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=state,
                          scheduled_time=when, public_url=f"https://insta/{pid}",
                          variant_key=variant_key, variant_hook=hook, metrics=metrics))


# ── lineage_stats: rank siblings by lift within a clip ─────────────────────────────────────────────
def test_lineage_ranks_siblings_by_lift():
    rows = [_row("a", "clip_1", 0.9), _row("b", "clip_1", 0.5)]
    views.lineage_stats(rows)
    win = next(r for r in rows if r.post_id == "a"); lose = next(r for r in rows if r.post_id == "b")
    assert win.sibling_count == 2 and lose.sibling_count == 2
    assert win.rank == 1 and lose.rank == 2
    assert win.delta_vs_best == 0.0
    assert lose.delta_vs_best == pytest.approx(-0.4)


def test_winner_is_rank_one_delta_zero():
    rows = [_row("a", "c", 0.3), _row("b", "c", 0.7), _row("d", "c", 0.1)]
    views.lineage_stats(rows)
    best = next(r for r in rows if r.post_id == "b")
    assert best.rank == 1 and best.delta_vs_best == 0.0 and best.sibling_count == 3


def test_singleton_clip_has_no_winner_badge():
    rows = [_row("solo", "clip_solo", 0.5)]
    views.lineage_stats(rows)
    r = rows[0]
    assert r.sibling_count == 1                       # one post -> a lineage of one
    assert not (r.rank == 1 and r.sibling_count > 1)  # the star condition is FALSE -> no badge in the panel


def test_competition_ranking_ties_both_rank_one():
    rows = [_row("a", "c", 0.8), _row("b", "c", 0.8), _row("d", "c", 0.4)]
    views.lineage_stats(rows)
    a = next(r for r in rows if r.post_id == "a"); b = next(r for r in rows if r.post_id == "b")
    d = next(r for r in rows if r.post_id == "d")
    assert a.rank == 1 and b.rank == 1 and a.delta_vs_best == 0.0 and b.delta_vs_best == 0.0
    assert d.rank == 3                                # competition ranking skips rank 2 after the tie


def test_lineage_fail_open_on_none_lift():
    # an unmeasured sibling (lift None) still counts toward the lineage size but can't be RANKED
    rows = [_row("measured", "c", 0.6), _row("blank", "c", None)]
    views.lineage_stats(rows)
    m = next(r for r in rows if r.post_id == "measured"); b = next(r for r in rows if r.post_id == "blank")
    assert m.sibling_count == 2 and b.sibling_count == 2
    assert m.rank == 1 and m.delta_vs_best == 0.0
    assert b.rank is None and b.delta_vs_best is None


def test_lineage_ignores_falsy_clip_id():
    rows = [_row("a", "", 0.9), _row("b", None, 0.5)]
    views.lineage_stats(rows)
    assert all(r.sibling_count is None and r.rank is None for r in rows)  # untouched — no join key


def test_lineage_ranks_within_the_passed_set():
    # ranks within whatever filtered list is handed in (the route passes the account/batch-filtered rows)
    filtered = [_row("a", "c", 0.4), _row("b", "c", 0.9)]   # 'b' would NOT be top if a stronger sibling existed elsewhere
    views.lineage_stats(filtered)
    assert next(r for r in filtered if r.post_id == "b").rank == 1


def test_lineage_never_raises():
    class Weird: pass
    views.lineage_stats([Weird(), Weird()])              # missing every attr -> fail-open, no exception


# ── flag-independence (the honest OFF firewall) ────────────────────────────────────────────────────
def test_lineage_is_creative_variation_independent(monkeypatch):
    def annotate():
        rows = [_row("a", "c", 0.9), _row("b", "c", 0.5)]
        views.lineage_stats(rows)
        return [(r.post_id, r.sibling_count, r.rank, r.delta_vs_best) for r in rows]
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); on = annotate()
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); off = annotate()
    assert on == off                                     # reads clip_id+lift only — the flag cannot move it


# ── metric_peaks + bar_pct: the proportional micro-bar ─────────────────────────────────────────────
def test_metric_peaks_takes_column_max():
    rows = [_row("a", "c", 0.5, saves=10, shares=2, reach=None),
            _row("b", "c", 0.5, saves=40, shares=None, reach=100)]
    peaks = views.metric_peaks(rows)
    assert peaks["saves"] == 40 and peaks["shares"] == 2 and peaks["reach"] == 100
    assert peaks["retention"] is None                    # absent on every row -> None (renders no bar)


def test_bar_pct_proportional_and_clamped():
    assert views.bar_pct(20, 40) == 50
    assert views.bar_pct(40, 40) == 100
    assert views.bar_pct(80, 40) == 100                  # clamped to 100


def test_bar_pct_fail_safe():
    assert views.bar_pct(None, 40) == 0
    assert views.bar_pct(10, None) == 0
    assert views.bar_pct(10, 0) == 0                     # no divide-by-zero
    assert views.bar_pct("x", 40) == 0                   # non-numeric -> 0, never raises


# ── routes render the winner star, lineage chip, delta and micro-bars ──────────────────────────────
def test_posted_panel_renders_lineage_and_bars(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="win", clip="clip_1", lift=0.90, hook="A wins", metrics_extra={"saves": 50})
    _seed_published(cfg, pid="lose", clip="clip_1", lift=0.40, hook="B loses", metrics_extra={"saves": 10})
    html = _client(cfg).get("/posted").data.decode()
    assert "★" in html                                   # winner star (sibling_count>1 & rank 1)
    assert "lineage" in html.lower()                     # the "N in lineage" chip
    assert "vs best" in html.lower()                     # the muted delta line
    assert 'class="bar metric-bar"' in html              # proportional saves/… micro-bar reusing .bar


def test_lift_route_carries_clip_id_and_bars(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="v1", clip="clip_1", lift=0.80, hook="H1", state=PostState.analyzed,
                    variant_key="k1", metrics_extra={"saves": 30})
    _seed_published(cfg, pid="v2", clip="clip_1", lift=0.30, hook="H2", state=PostState.analyzed,
                    variant_key="k2", metrics_extra={"saves": 10})
    led = Ledger.load(cfg)
    view = views.lift_rows(led, cfg)
    assert all(getattr(r, "clip_id", None) == "clip_1" for r in view.variant_rows)  # LiftRow now carries it
    views.lineage_stats(view.variant_rows)
    assert any(r.rank == 1 and r.sibling_count == 2 for r in view.variant_rows)
    html = _client(cfg).get("/lift").data.decode()
    assert "★" in html and 'class="bar metric-bar"' in html


def test_singleton_posted_panel_has_no_star(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="only", clip="clip_solo", lift=0.55, hook="alone")
    html = _client(cfg).get("/posted").data.decode()
    assert "★" not in html                               # one post on the clip -> no winner badge


def test_posted_micro_bars_normalise_over_full_filtered_set_not_the_page(tmp_path):
    # S6 audit LOW: the micro-bar must be a STABLE reference across pages. metric_peaks reads the full
    # filtered set (like lineage_stats), NOT the visible slice — else the same saves=10 row reads 1% on
    # page 1 and 100% on page 2. Seed 25 distinct clips: the 24 newest carry saves=10, the oldest (off the
    # first page) carries saves=1000 -> the first page's bars scale to 1000, i.e. ~1%, never 100%.
    cfg = Config(root=tmp_path)
    for i in range(24):
        _seed_published(cfg, pid=f"p{i:02d}", clip=f"clip_{i:02d}", lift=0.5,
                        metrics_extra={"saves": 10}, when=f"2026-06-{i + 2:02d}T00:00:00Z")
    _seed_published(cfg, pid="whale", clip="clip_whale", lift=0.5,
                    metrics_extra={"saves": 1000}, when="2026-06-01T00:00:00Z")   # oldest -> page 2
    html = _client(cfg).get("/posted").data.decode()
    assert 'class="bar metric-bar" style="width: 1%"' in html          # 10/1000 -> normalised to the global peak
    assert 'class="bar metric-bar" style="width: 100%"' not in html    # would appear if peaks came from the page
