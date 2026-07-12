# tests/test_studio_posted_results.py — S6: make Posted & Results legible. The surfaces showed a raw
# lift_score + a bare metric breakdown but never "this hook BEAT that hook" — a repost/crosspost is a
# disconnected row though it shares clip_id with its origin. lineage_stats() is a PURE annotation over the
# already-built rows returning a NEW list (MOL-70 — the caller's rows are never mutated): group by clip_id
# (the durable join key), rank by lift desc within the group, stamp sibling_count / rank / delta_vs_best. metric_peaks()+bar_pct() drive a proportional micro-bar.
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
from fanops.models import Post, Platform, PostState, Clip, ClipState, LIFT_SCORE, Source, Moment
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _row(post_id, clip_id, lift, **over):
    """A bare PostedRow with the fields lineage_stats/metric_peaks read (no ledger needed for unit tests)."""
    return views.PostedRow(post_id=post_id, clip_id=clip_id, account=over.get("account", "a"),
                           platform="instagram", caption="x", public_url=None, scheduled_time=None,
                           lift_score=lift, saves=over.get("saves"), shares=over.get("shares"),
                           retention=over.get("retention"), reach=over.get("reach"),
                           variant_hook=over.get("variant_hook"))


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


# ── lineage_stats: rank siblings by lift within a clip ─────────────────────────────────────────────
def test_lineage_ranks_siblings_by_lift():
    rows = views.lineage_stats([_row("a", "clip_1", 0.9), _row("b", "clip_1", 0.5)])
    win = next(r for r in rows if r.post_id == "a"); lose = next(r for r in rows if r.post_id == "b")
    assert win.sibling_count == 2 and lose.sibling_count == 2
    assert win.rank == 1 and lose.rank == 2
    assert win.delta_vs_best == 0.0
    assert lose.delta_vs_best == pytest.approx(-0.4)


def test_winner_is_rank_one_delta_zero():
    rows = views.lineage_stats([_row("a", "c", 0.3), _row("b", "c", 0.7), _row("d", "c", 0.1)])
    best = next(r for r in rows if r.post_id == "b")
    assert best.rank == 1 and best.delta_vs_best == 0.0 and best.sibling_count == 3


def test_singleton_clip_has_no_winner_badge():
    rows = views.lineage_stats([_row("solo", "clip_solo", 0.5)])
    r = rows[0]
    assert r.sibling_count == 1                       # one post -> a lineage of one
    assert not (r.rank == 1 and r.sibling_count > 1)  # the star condition is FALSE -> no badge in the panel


def test_competition_ranking_ties_both_rank_one():
    rows = views.lineage_stats([_row("a", "c", 0.8), _row("b", "c", 0.8), _row("d", "c", 0.4)])
    a = next(r for r in rows if r.post_id == "a"); b = next(r for r in rows if r.post_id == "b")
    d = next(r for r in rows if r.post_id == "d")
    assert a.rank == 1 and b.rank == 1 and a.delta_vs_best == 0.0 and b.delta_vs_best == 0.0
    assert d.rank == 3                                # competition ranking skips rank 2 after the tie


def test_lineage_fail_open_on_none_lift():
    # an unmeasured sibling (lift None) still counts toward the lineage size but can't be RANKED
    rows = views.lineage_stats([_row("measured", "c", 0.6), _row("blank", "c", None)])
    m = next(r for r in rows if r.post_id == "measured"); b = next(r for r in rows if r.post_id == "blank")
    assert m.sibling_count == 2 and b.sibling_count == 2
    assert m.rank == 1 and m.delta_vs_best == 0.0
    assert b.rank is None and b.delta_vs_best is None


def test_lineage_ignores_falsy_clip_id():
    rows = views.lineage_stats([_row("a", "", 0.9), _row("b", None, 0.5)])
    assert all(r.sibling_count is None and r.rank is None for r in rows)  # untouched — no join key


def test_lineage_ranks_within_the_passed_set():
    # ranks within whatever filtered list is handed in (the route passes the account/batch-filtered rows)
    filtered = views.lineage_stats([_row("a", "c", 0.4), _row("b", "c", 0.9)])   # 'b' would NOT be top if a stronger sibling existed elsewhere
    assert next(r for r in filtered if r.post_id == "b").rank == 1


def test_lineage_never_raises():
    class Weird: pass
    views.lineage_stats([Weird(), Weird()])              # missing every attr -> fail-open, no exception


def test_lineage_returns_new_rows_originals_untouched():
    # MOL-70: lineage_stats must NOT mutate the caller-owned rows — it returns a NEW annotated list.
    originals = [_row("a", "clip_1", 0.9), _row("b", "clip_1", 0.5)]
    out = views.lineage_stats(originals)
    assert all(r.sibling_count is None and r.rank is None and r.delta_vs_best is None for r in originals)
    assert [r.post_id for r in out] == ["a", "b"]        # same order, same length as the input
    win, lose = out
    assert win.sibling_count == 2 and win.rank == 1 and win.delta_vs_best == 0.0
    assert lose.sibling_count == 2 and lose.rank == 2 and lose.delta_vs_best == pytest.approx(-0.4)
    assert all(o is not n for o, n in zip(originals, out))   # annotated rows are copies, never the caller's objects


# ── flag-independence (the honest OFF firewall) ────────────────────────────────────────────────────
def test_lineage_is_creative_variation_independent(monkeypatch):
    def annotate():
        rows = views.lineage_stats([_row("a", "c", 0.9), _row("b", "c", 0.5)])
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
    _seed_published(cfg, pid="win", clip="clip_1", lift=0.90, hook="SHARED", metrics_extra={"saves": 50})
    _seed_published(cfg, pid="lose", clip="clip_1", lift=0.40, hook="SHARED", metrics_extra={"saves": 10})
    html = _client(cfg).get("/posted").data.decode()
    assert "★" in html                                   # winner star (sibling_count>1 & rank 1)
    assert "lineage" in html.lower()                     # the "N in lineage" chip
    assert "vs best" in html.lower()                     # the muted delta line
    assert 'class="bar metric-bar"' in html              # proportional saves/… micro-bar reusing .bar


def test_lift_route_carries_clip_id_and_bars(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="v1", clip="clip_1", lift=0.80, hook="H1", state=PostState.analyzed,
                    metrics_extra={"saves": 30})
    _seed_published(cfg, pid="v2", clip="clip_1", lift=0.30, hook="H2", state=PostState.analyzed,
                    metrics_extra={"saves": 10})
    led = Ledger.load(cfg)
    view = views.lift_rows(led, cfg)
    assert all(getattr(r, "clip_id", None) == "clip_1" for r in view.variant_rows)  # LiftRow now carries it
    annotated = views.lineage_stats(view.variant_rows)
    assert any(r.rank == 1 and r.sibling_count == 2 for r in annotated)
    html = _client(cfg).get("/posted").data.decode()   # U10: the Lift lens is folded onto /posted
    assert "★" in html and 'class="bar metric-bar"' in html


# ── MOL-50: uniform DEGRADED dedup — table-level note + quiet per-row marker vs loud minority badge ──
def test_lift_all_degraded_emits_table_note_and_quiet_marker(tmp_path):
    cfg = Config(root=tmp_path)
    for i in range(3):
        _seed_published(cfg, pid=f"d{i}", clip=f"clip_{i}", lift=0.1 * i, hook=f"H{i}",
                        state=PostState.analyzed,
                        metrics_extra={"lift_degraded": True, "lift_missing_keys": ["retention"]})
    html = _client(cfg).get("/posted").data.decode()   # U10: the Lift lens is folded onto /posted
    assert "retention data missing" in html.lower()        # table-level note emitted once...
    assert 'class="badge degraded"' not in html            # ...loud per-row badge dropped...
    assert "degraded-quiet" in html                        # ...replaced by the quiet per-row marker
    assert html.count("degraded-quiet") == 3               # one quiet marker per degraded row


def test_lift_minority_degraded_keeps_loud_badge_no_note(tmp_path):
    # 1 of 4 degraded (25%) -> the exception: loud per-row badge stays, NO table-level note.
    _seed_published(cfg := Config(root=tmp_path), pid="deg", clip="clip_d", lift=0.1, hook="DEG",
                    state=PostState.analyzed,
                    metrics_extra={"lift_degraded": True, "lift_missing_keys": ["retention"]})
    for i in range(3):
        _seed_published(cfg, pid=f"ok{i}", clip=f"clip_ok{i}", lift=0.2 + i, hook=f"OK{i}",
                        state=PostState.analyzed)
    html = _client(cfg).get("/posted").data.decode()   # U10: the Lift lens is folded onto /posted
    assert "retention data missing" not in html.lower()    # no table-level note for a minority
    assert 'class="badge degraded"' in html                # the loud per-row badge stays (exception-signal)
    assert "degraded-quiet" not in html


def test_lift_number_uses_dominant_class(tmp_path):
    # MOL-50: the Lift number is the answer to "which variant won" -> it carries the dominant .lift-num class.
    _seed_published(cfg := Config(root=tmp_path), pid="v", clip="clip_1", lift=0.3, hook="H",
                    state=PostState.analyzed)
    html = _client(cfg).get("/posted").data.decode()   # U10: the Lift lens is folded onto /posted
    assert 'class="lift-num"' in html


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


# ── the live link is a clean, labeled affordance (click the post -> the real social post), not a raw URL
#    dump; and an HONEST 'pending' state (not a dead dash) when the loop hasn't captured public_url yet ──
def test_posted_link_is_a_labeled_affordance_not_a_raw_url(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p_live", lift=0.5)         # _seed_published sets public_url=https://insta/p_live
    html = _client(cfg).get("/posted").data.decode()
    assert 'href="https://insta/p_live"' in html         # still links to the real social post
    assert 'target="_blank"' in html and 'rel="noopener"' in html
    assert "View on instagram" in html                   # platform-labeled, clickable affordance
    assert ">https://insta/p_live<" not in html          # the raw URL is NO LONGER the visible link text


def test_posted_link_dryrun_row_labels_no_link_not_pending(tmp_path):
    """M5 — a published post WITHOUT a public_url is the dryrun signature (DryRunPoster->publish_post
    never sets public_url; only reconcile.py does, and only on a real provider response). The OLD
    contract conflated this with 'pending — link fills in later' and was the operator's verbatim
    'says posted when nothing is posted' bug. The NEW contract: dryrun rows label 'dryrun' (chip) +
    'no link' (the link cell), live-rows-without-URL still read 'pending ⟳'."""
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_np", parent_id="m1", path="/c/clip_np.mp4", state=ClipState.published))
        # R1: a published row MUST carry a public_url; the dryrun:// scheme is the M5 dryrun-signature
        # marker (channel chip labels 'dryrun'). The old contract (public_url=None) is now unconstructable.
        led.add_post(Post(id="p_nourl", parent_id="clip_np", account="a", account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", public_url="dryrun://p_nourl",
                          metrics={LIFT_SCORE: 0.5}))
    html = _client(cfg).get("/posted").data.decode()
    assert 'data-testid="posted-channel-chip"' in html       # M5: channel chip present
    assert ">dryrun<" in html                                # labels as dryrun (no real platform saw it)
    assert "no link" in html                                 # the honest dryrun placeholder, NOT 'pending'


# ── MOL-51: per-row action weights ranked deliberately (U8: repost actions folded into one menu) ──────
# Before MOL-51 the Posted list row had ~3 default-weight controls (Metrics disclosure, Post again,
# Crosspost) at IDENTICAL weight, the payoff "View on {platform}" quieter than them, and the Lift value
# as plain text regardless of magnitude. MOL-51 assigned MOL-44's 3 tiers: the infrequent utility controls
# + the Metrics disclosure → tertiary .ghost; the live permalink stays the accent-bright leading affordance;
# the Lift value reuses MOL-50's dominant .lift-num. U8 replaced the two per-row repost/crosspost forms with
# ONE <details> menu ("Post again ▾"), so the demoted tier is now the menu summary + the Metrics disclosure.
def test_posted_utility_menu_summary_is_ghost(tmp_path):
    # the row action menu ("Post again ▾") is the infrequent-utility affordance -> tertiary .ghost tier.
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p_live", lift=0.42)            # published + https:// url -> live delivery row
    html = _client(cfg).get("/posted").data.decode()
    import re as _re
    m = _re.search(r'<summary[^>]*>Post again[^<]*</summary>', html)
    assert m, "row action menu summary (Post again) not rendered on the live row"
    assert "ghost" in m.group(0), f"menu summary must be tertiary .ghost, got: {m.group(0)}"


def test_posted_metrics_disclosure_is_ghost(tmp_path):
    # the Metrics <summary> disclosure toggle is the same low-weight family as MOL-44's demoted toggles.
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p_live", lift=0.42)
    html = _client(cfg).get("/posted").data.decode()
    import re as _re
    m = _re.search(r'<summary[^>]*>Metrics</summary>', html)
    assert m, "Metrics disclosure summary not rendered"
    assert "ghost" in m.group(0), f"Metrics summary must carry the ghost class, got: {m.group(0)}"


def test_posted_lift_value_uses_dominant_class(tmp_path):
    # MOL-51 item 3: Posted's Lift value becomes the dominant per-row datum -> reuse MOL-50's .lift-num.
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p_live", lift=0.42)
    html = _client(cfg).get("/posted").data.decode()
    assert 'class="lift-num"' in html                        # the Lift number is bold mono --ink, not plain text


def test_posted_live_link_keeps_promoted_class(tmp_path):
    # MOL-51 item 2: the live permalink is the payoff link -> stays the leading .live-link accent affordance.
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="p_live", lift=0.42)
    html = _client(cfg).get("/posted").data.decode()
    import re as _re
    m = _re.search(r'<a class="live-link"[^>]*>View on instagram', html)
    assert m, f"live permalink must keep the promoted .live-link class as the leading affordance, html: {html[:200]}"
