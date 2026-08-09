# tests/test_hashtag_platform_truth.py
# The contract of the two-layer platform-truth hashtag architecture. Each test is REFUTE-FORM: it fails on
# the pre-rebuild code (invented likes+comments metric under a `reach` key, the fictional 30/7-day local
# budget, unmeasured corpus promotion, hand-ranked frozen pools).
#
# Layer A (network via instagrapi): persona description -> terms -> anchor tags -> ONE medias_top fetch
#   per tag that yields Top-grid medians (play_count preferred, like_count fallback) + co-tags -> the cache.
# Layer B (zero network): corpus = top corpus_target of the persona's aligned pool by that visibility metric.
import json
from fanops.config import Config
from fanops.models import Platform
from fanops.hashtags import METRIC_FIELD, _metric, load_measurements, ranked_tags, vet_hashtags, vet_hashtags_traced
from fanops.fanops_hashtags import refresh_store
from fanops.personas import Personas, add_persona, apply_auto_corpus
from fanops.persona_research import persona_terms, derive_corpus
from fanops.ig_hashtag_scrape import ScrapeRefused
from hashtag_scrape_fakes import _FakeClient


def _persona(cfg, pid="craft", name="Craft Curator", voice="syrian rapper craft", niche=None):
    add_persona(cfg, name=name, voice=voice, niche=list(niche) if niche is not None else ["hiphop"], id=pid)
    return pid


def _link_active(cfg, pid, handle="markmakmouly"):
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    return Accounts.load(cfg)


def _client(media_by_tag=None, metric_by_tag=None, **kw):
    return _FakeClient(metric_by_tag=metric_by_tag, media_by_tag=media_by_tag, **kw)


# ---------------------------------------------------------------- 1. verbatim platform field

def test_metric_is_platform_fields_never_a_sum_or_invented_reach(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 10, "comments_count": 90}]}
    refresh_store(cfg, scrape_client=_client(media))
    m = load_measurements(cfg)
    assert m["#hiphop"]["like_count"] == 10
    assert _metric(m["#hiphop"]) == 10
    flat = json.dumps(json.loads(cfg.hashtags_path.read_text()))
    assert '"reach"' not in flat and '"confidence"' not in flat
    assert METRIC_FIELD == "play_count"   # preferred rank key; like_count still admits


def test_top_grid_median_not_first_media_wins(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "comments_count": 8},
                         {"caption": "", "like_count": 777, "comments_count": 2},
                         {"caption": "", "like_count": 1, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["like_count"] == 389.0   # median of [777, 1]
    assert _metric(rec) == 389.0


def test_play_count_beats_like_count_when_both_present(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [
        {"caption": "", "like_count": 9999, "play_count": 100},
        {"caption": "", "like_count": 1, "play_count": 300},
        {"caption": "", "like_count": 50, "play_count": 200}]}
    refresh_store(cfg, scrape_client=_client(media))
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["play_count"] == 200.0 and rec["like_count"] == 50.0
    assert _metric(rec) == 200.0
    assert rec[METRIC_FIELD] == 200.0


def test_media_count_persisted_from_hashtag_info(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    from hashtag_scrape_fakes import _FakeClient
    client = _FakeClient(media_by_tag={"#hiphop": [{"caption": "", "like_count": 10}]},
                         media_count_by_tag={"#hiphop": 50000})
    refresh_store(cfg, scrape_client=client)
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["media_count"] == 50000.0
    assert rec["media_count_at"] == rec["measured_at"], "volume carries its own age stamp (MOL-691)"


# ------------------------------------------- 1b. volume backfill + Reels trend evidence (MOL-691)

def test_cached_id_without_volume_is_backfilled_not_stranded(tmp_path, monkeypatch):
    """The live defect: 131/300 records had no media_count because a cached graph_id skipped the ONLY
    call that serves volume. A second pass must acquire it."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 10}]}
    refresh_store(cfg, scrape_client=_client(media))                       # no volume served
    assert "media_count" not in load_measurements(cfg)["#hiphop"]
    client = _client(media, media_count_by_tag={"#hiphop": 20_923_125})
    refresh_store(cfg, scrape_client=client)
    assert "hiphop" in client.info_calls, "missing volume MUST re-resolve"
    assert load_measurements(cfg)["#hiphop"]["media_count"] == 20_923_125.0


def test_known_volume_is_not_re_resolved_inside_seven_days_then_is_after(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 10}]}
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    refresh_store(cfg, scrape_client=_client(media, media_count_by_tag={"#hiphop": 100}), now=t0)
    near = _client(media, media_count_by_tag={"#hiphop": 100})
    refresh_store(cfg, scrape_client=near, now=t0 + timedelta(days=29, hours=23))
    assert "hiphop" not in near.info_calls and "hiphop" not in near.media_calls, \
        "sub-30d volume+measure must stay off-queue (no fetch split to skip)"
    stale = _client(media, media_count_by_tag={"#hiphop": 175})
    refresh_store(cfg, scrape_client=stale, now=t0 + timedelta(days=30, hours=1))
    assert "hiphop" in stale.info_calls, "volume older than 30d must refresh"
    assert load_measurements(cfg)["#hiphop"]["media_count"] == 175.0


def test_top_sample_is_twenty_seven_not_nine(tmp_path, monkeypatch):
    """9 was Instagram's default grid page, not a measurement floor — too thin to take a max over."""
    from fanops.hashtags import TOP_SAMPLE_N
    from fanops.persona_research import TOP_GRID_N
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    client = _client({"#hiphop": [{"caption": "", "like_count": 10}]})
    refresh_store(cfg, scrape_client=client)
    assert TOP_SAMPLE_N == 27 and client.amounts and set(client.amounts) == {27}
    assert TOP_GRID_N == TOP_SAMPLE_N, "the density denominator must BE the real sample"


def test_recent_reel_play_max_persisted_and_stale_viral_reel_excluded(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [
        {"caption": "", "play_count": 5_000, "product_type": "clips", "taken_at": now - timedelta(days=1)},
        {"caption": "", "play_count": 9_000, "product_type": "clips", "taken_at": now - timedelta(days=6)},
        {"caption": "", "play_count": 99_000_000, "product_type": "clips", "taken_at": now - timedelta(days=400)},
        {"caption": "", "play_count": 7_000_000, "product_type": "feed", "taken_at": now},
        {"caption": "", "play_count": 8_000, "product_type": "clips"},          # undated -> not current
    ]}
    refresh_store(cfg, scrape_client=_client(media), now=now)
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["current_top_reel_play_max_7d"] == 9_000.0, "max over RECENT reels only"
    assert rec["top_reel_sample_n"] == 2.0, "sample count is the honest denominator of that max"


def test_reel_less_sample_preserves_prior_trend_evidence(tmp_path, monkeypatch):
    """A transiently photo-only grid is not proof that a tag has no Reels — never erase bought evidence."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    hot = {"#hiphop": [{"caption": "", "play_count": 4_242, "product_type": "clips",
                        "taken_at": now - timedelta(days=2)}]}
    refresh_store(cfg, scrape_client=_client(hot), now=now)
    assert load_measurements(cfg)["#hiphop"]["current_top_reel_play_max_7d"] == 4_242.0
    refresh_store(cfg, scrape_client=_client({"#hiphop": [{"caption": "", "like_count": 12}]}),
                  now=now + timedelta(days=31))
    assert load_measurements(cfg)["#hiphop"]["current_top_reel_play_max_7d"] == 4_242.0


def test_every_contract_field_survives_the_whole_file_rewrite(tmp_path, monkeypatch):
    """refresh_store seeds from load_measurements and rewrites hashtags.json WHOLE, so a field the
    reader drops is written in pass N and gone in pass N+1. Reader contract must cover the writer."""
    from datetime import datetime, timedelta, timezone
    from fanops.hashtags import RECORD_NUM_FIELDS, RECORD_STR_FIELDS
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 11, "play_count": 4_000,
                          "product_type": "clips", "taken_at": now - timedelta(days=1)}]}
    refresh_store(cfg, scrape_client=_client(media, media_count_by_tag={"#hiphop": 900}), now=now)
    first = load_measurements(cfg)["#hiphop"]
    for f in RECORD_NUM_FIELDS + RECORD_STR_FIELDS:
        assert f in first, f"{f} must be persisted AND retained by the reader"
    # A pass that re-measures plays but serves no Reel row and no volume must not strip either.
    refresh_store(cfg, scrape_client=_client({"#hiphop": [{"caption": "", "like_count": 11,
                                                           "play_count": 4_000}]}),
                  now=now + timedelta(days=31))
    second = load_measurements(cfg)["#hiphop"]
    for f in RECORD_NUM_FIELDS + RECORD_STR_FIELDS:
        assert f in second, f"{f} was stripped by the whole-file rewrite"


def test_a_tag_with_no_like_count_anywhere_is_unmeasured_and_absent(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "comments_count": 3}]}
    refresh_store(cfg, scrape_client=_client(media))
    assert "#hiphop" not in load_measurements(cfg)


# ---------------------------------------------------------------- 2. no fictional local budget

def test_no_local_budget_caps_a_pass(tmp_path, monkeypatch):
    """No Meta hashtag_budget.json. Scrape try/cotag caps are env-tunable — raise them to prove discovery."""
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "200")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", "100")
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    cotags = " ".join(f"#c{i}" for i in range(100))
    media = {"#hiphop": [{"caption": cotags, "like_count": 500, "comments_count": 0}]}
    for i in range(100):
        # inbound: niche on the candidate Top (not outbound-from-anchor-Top)
        media[f"#c{i}"] = [{"caption": "clip #hiphop", "like_count": 100 + i, "comments_count": 0}]
    refresh_store(cfg, scrape_client=_client(media))
    m = load_measurements(cfg)
    assert len([t for t in m if t.startswith("#c")]) == 100
    assert not (cfg.control / "hashtag_budget.json").exists()


def test_budget_bookkeeping_is_gone_from_the_module(tmp_path):
    import fanops.meta_graph as mg
    import fanops.config as cf
    for dead in ("_BUDGET_LIMIT", "_BUDGET_WINDOW_DAYS", "_read_queries", "budget_remaining", "record_query",
                 "sample_trends", "trend_score", "_trend_score_status", "tag_metrics", "discover_candidates"):
        assert not hasattr(mg, dead), f"{dead} must be deleted"
    assert not hasattr(Config(root=tmp_path), "hashtag_budget_path")
    assert not hasattr(cf.Config, "hashtag_trends")


def test_graph_id_is_cached_so_a_known_tag_never_spends_another_search(tmp_path, monkeypatch):
    """MOL-856: a due remesure always spends hashtag_info + medias_top together.

    Fresh tags stay off-queue (MOL-855). Once due, volume stamp freshness must NOT skip hashtag_info —
    one visit refreshes volume and visibility. Missing volume still backfills via the volume tier (MOL-691)."""
    from datetime import datetime, timezone, timedelta
    import json
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, name="Hiphop", voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 5, "comments_count": 0}]}
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    t_due = t0 + timedelta(days=31)
    refresh_store(cfg, scrape_client=_client(media, media_count_by_tag={"#hiphop": 500}), now=t0)
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["#hiphop"]["measured_at"] = (t_due - timedelta(days=31)).isoformat()
    blob["#hiphop"]["media_count_at"] = (t_due - timedelta(days=1)).isoformat()  # volume stamp still <30d
    blob["last_complete_pass"] = (t_due - timedelta(days=2)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    rec = load_measurements(cfg)["#hiphop"]
    assert rec["graph_id"] == "id-hiphop" and rec["media_count"] == 500.0
    client = _client(media, media_count_by_tag={"#hiphop": 777})
    refresh_store(cfg, scrape_client=client, now=t_due)
    assert "hiphop" in client.info_calls, "due remesure must still call hashtag_info (MOL-856)"
    assert "hiphop" in client.media_calls, "≥30d-stale tag must re-measure"
    assert load_measurements(cfg)["#hiphop"]["media_count"] == 777.0


def test_cached_tag_metric_moves_when_due_not_every_pass(tmp_path, monkeypatch):
    """MOL-855 supersedes 'every cached tag every pass': fresh volume+measure stays off-queue until due.

    Unmeasured anchors still run; a cached tag remesures only after the 30d due floor."""
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["lyricism", "hiphop"]); _link_active(cfg, pid)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    t_near = t0 + timedelta(days=13)
    t_due = t0 + timedelta(days=31)
    media0 = {"#hiphop": [{"caption": "", "like_count": 100, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media0, media_count_by_tag={"#hiphop": 7}), now=t0)
    before = load_measurements(cfg)["#hiphop"]
    assert before["like_count"] == 100.0 and _metric(before) == 100.0
    stamp0 = before["measured_at"]

    near = _FakeClient(
        media_by_tag={"#hiphop": [{"caption": "", "like_count": 250}]},
        media_count_by_tag={"#hiphop": 7},
        refuse_tags={"#lyricism", "lyricism"})
    out_near = refresh_store(cfg, scrape_client=near, now=t_near)
    assert "hiphop" not in near.media_calls, "sub-30d measured tag must not remesure"
    assert load_measurements(cfg)["#hiphop"]["measured_at"] == stamp0
    refused = [u for u in (out_near.get("unresolved") or []) if u.get("tag") == "#lyricism"]
    assert refused and refused[0].get("code") == 18 and refused[0].get("reason") == "refused"
    assert out_near.get("throttled") is False, "code 18 must not abort the pass as ScrapeThrottled"

    # Age measured_at into remesure while leaving media_count_at <30d — MOL-856 still spends hashtag_info.
    import json
    blob = json.loads(cfg.hashtags_path.read_text())
    blob["#hiphop"]["measured_at"] = (t_due - timedelta(days=31)).isoformat()
    blob["#hiphop"]["media_count_at"] = (t_due - timedelta(days=1)).isoformat()
    blob["last_complete_pass"] = (t_due - timedelta(days=2)).isoformat()
    cfg.hashtags_path.write_text(json.dumps(blob))
    due = _FakeClient(
        media_by_tag={"#hiphop": [{"caption": "", "like_count": 250}]},
        media_count_by_tag={"#hiphop": 42},
        refuse_tags={"#lyricism", "lyricism"})
    out = refresh_store(cfg, scrape_client=due, now=t_due)
    after = load_measurements(cfg)["#hiphop"]
    assert after["like_count"] == 250.0 and _metric(after) == 250.0, "platform's new like_count must land when due"
    assert after["measured_at"] != stamp0 and after["measured_at"].startswith("2026-08-01T12:00")
    assert "hiphop" in due.info_calls, "due remesure must refresh volume via hashtag_info (MOL-856)"
    assert "hiphop" in due.media_calls, "≥30d-due tag must re-measure"
    assert after["media_count"] == 42.0
    assert out.get("throttled") is False


# ---------------------------------------------------------------- 3. discovery roots in the DESCRIPTION

def test_terms_come_from_the_description_never_from_the_corpus(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="poison-proof prose", niche=["hiphop", "bars"])
    _link_active(cfg, pid)
    apply_auto_corpus(cfg, pid, tags=["#poisontag"], meta={})
    media = {"#hiphop": [{"caption": "#bars", "like_count": 5, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 4, "comments_count": 0}]}
    client = _client(media)
    refresh_store(cfg, scrape_client=client)
    assert "poisontag" not in client.info_calls and "poisontag" not in client.media_calls
    derive_corpus(cfg, pid)
    assert "#poisontag" not in Personas.load(cfg).get(pid).hashtag_corpus


def test_persona_terms_drop_junk_voice_filler(tmp_path):
    """Voice filler must never become Layer A anchors — niche only (MOL-637)."""
    from fanops.personas import Persona
    per = Persona(id="x", name="X", voice="within high angle hook gaps menu lyricism",
                  niche=["hiphop"])
    terms = set(persona_terms(per))
    assert terms == {"hiphop"}
    for junk in ("within", "high", "angle", "hook", "gaps", "menu", "lyricism"):
        assert junk not in terms


def test_persona_terms_niche_only(tmp_path):
    """MOL-637: persona_terms returns declared niche ONLY — not voice/levers/corpus."""
    from fanops.personas import Persona
    per = Persona(id="x", name="Craft Curator", voice="syrian rapper craft",
                  niche=["Lyricism", "songwriting", "lyricism", "#MusicReview"],
                  cut_policy=["punchlines"], hook_angle="curiosity", intensity="high",
                  hashtag_corpus=["#neverseenhere"])
    terms = persona_terms(per)
    assert terms == ["lyricism", "songwriting", "musicreview"]
    assert "punchlines" not in terms
    assert "curiosity" not in terms and "high" not in terms
    assert "syrian" not in terms and "rapper" not in terms and "craft" not in terms
    assert "craftcurator" not in terms and "hiphop" not in terms
    assert not any("neverseenhere" in t for t in terms)
    assert terms == persona_terms(per)


def test_outbound_one_hit_never_enters_aligned_pool(tmp_path, monkeypatch):
    """MOL-643: tag seen once on a niche Top (outbound) must NOT enter the corpus pool — even at huge plays."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="x", niche=["undergroundmusic"]); _link_active(cfg, pid)
    media = {"#undergroundmusic": [{"caption": "tour #nashville", "like_count": 10, "play_count": 100}],
             # Measured after enqueue, but ITS Top does not mention the niche → no inbound.
             "#nashville": [{"caption": "country vibes only", "like_count": 5000, "play_count": 90000}]}
    refresh_store(cfg, scrape_client=_client(media))
    m = load_measurements(cfg)
    assert "#nashville" not in m                               # evicted: non-anchor, empty live from
    from fanops.persona_research import _aligned_pool
    from fanops.personas import Personas
    pool_tags = {t for t, _, _ in _aligned_pool(Personas.load(cfg).get(pid), m)}
    assert "#nashville" not in pool_tags
    assert "#undergroundmusic" in pool_tags                    # anchors still admit


def test_inbound_cotag_attributes_measured_tag_to_anchor(tmp_path, monkeypatch):
    """Inbound: the niche appearing on the candidate's own Top writes `from`, which is what the pool's
    relatedness bar reads (MOL-665). Two Top rows carry the anchor, so hits>=2 admits — this no longer
    rides the deleted magnet soft lane (MOL-692), and #fyp's own huge numbers buy it nothing."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="x", niche=["rapbeef"]); _link_active(cfg, pid)
    media = {"#rapbeef": [{"caption": "noise #fyp", "like_count": 10}],
             "#fyp": [{"caption": "chaos #rapbeef #drama", "like_count": 9000},
                      {"caption": "more #rapbeef", "like_count": 9000}]}
    refresh_store(cfg, scrape_client=_client(media, media_count_by_tag={"#rapbeef": 50_000, "#fyp": 90_000}))
    rec = load_measurements(cfg)["#fyp"]
    assert rec.get("from", {}).get("#rapbeef", 0) >= 2
    from fanops.persona_research import _aligned_pool
    from fanops.personas import Personas
    pool_tags = {t for t, _, _ in _aligned_pool(Personas.load(cfg).get(pid), load_measurements(cfg))}
    assert "#fyp" in pool_tags


def test_dead_seed_from_edges_pruned_on_write(tmp_path, monkeypatch):
    """Legacy punchlines-only attribution must not survive a refresh write (remesure pollution)."""
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "id-hiphop", "like_count": 10.0,
                    "measured_at": now.isoformat(), "from": {}},
        "#rtlmatin": {"graph_id": "id-rtlmatin", "play_count": 311845.0, "like_count": 4952.0,
                      "measured_at": now.isoformat(), "from": {"#punchlines": 4}},
        "last_complete_pass": now.isoformat()}))
    refresh_store(cfg, scrape_client=_client({"#hiphop": [{"caption": "", "like_count": 12}]}), now=now)
    m = load_measurements(cfg)
    assert "#hiphop" in m and "#rtlmatin" not in m


def test_persona_terms_disjoint_niches_diverge(tmp_path):
    """MOL-637: different niches diverge; voice prose is ignored for Layer A."""
    from fanops.personas import Persona
    a = Persona(id="a", name="A", voice="lyric craft musicianship", niche=["hiphop", "lyricism"])
    b = Persona(id="b", name="B", voice="unhinged viral chaos drama", niche=["hiphop", "drama"])
    ta, tb = set(persona_terms(a)), set(persona_terms(b))
    assert "hiphop" in ta & tb
    assert ta - {"hiphop"} != tb - {"hiphop"}
    assert "lyricism" in ta and "lyricism" not in tb
    assert "drama" in tb and "drama" not in ta
    assert "musicianship" not in ta and "unhinged" not in tb


# ---------------------------------------------------------------- 4. corpus is derived + evidence-only

def test_unmeasured_candidate_never_enters_a_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "10")
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#measured #unmeasured", "like_count": 500, "comments_count": 0}],
             "#measured": [{"caption": "bars #hiphop", "like_count": 400, "comments_count": 0},
                           {"caption": "more #hiphop", "like_count": 300, "comments_count": 0}],
             "#unmeasured": [{"caption": "", "comments_count": 9}]}
    refresh_store(cfg, scrape_client=_client(
        media, media_count_by_tag={"#hiphop": 4_000_000, "#measured": 50_000, "#unmeasured": 50_000}))
    derive_corpus(cfg, pid)
    per = Personas.load(cfg).get(pid)
    assert "#measured" in per.hashtag_corpus
    assert "#unmeasured" not in per.hashtag_corpus
    assert len(per.hashtag_corpus) < 10


def test_derivation_is_zero_network(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 400, "comments_count": 0},
                       {"caption": "y #hiphop", "like_count": 350, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    # Layer A write path already re-derived corpora — corpus must be non-empty before the second call.
    assert Personas.load(cfg).get(pid).hashtag_corpus
    import fanops.ig_hashtag_scrape as igs
    def explode(*a, **k): raise AssertionError("derive_corpus must not touch the network")
    monkeypatch.setattr(igs, "resolve_hashtag_scrape", explode)
    monkeypatch.setattr(igs, "measure_and_harvest_scrape", explode)
    # Second derive is idempotent (changed=False) and must never touch the network.
    assert derive_corpus(cfg, pid)["changed"] is False


def test_corpus_is_ranked_by_tag_size_not_by_post_medians(tmp_path, monkeypatch):
    """End-to-end through a real Layer A pass: `#smallbutloud` has ~900x the Top-grid median plays of
    `#bignichetag`, and must still rank BELOW it because it carries ~78x fewer posts (MOL-692)."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#smallbutloud #bignichetag", "like_count": 500}],
             "#smallbutloud": [{"caption": "x #hiphop", "play_count": 900_000},
                               {"caption": "y #hiphop", "play_count": 900_000}],
             "#bignichetag": [{"caption": "x #hiphop", "play_count": 1_000},
                              {"caption": "y #hiphop", "play_count": 1_000}]}
    # Both stay UNDER CATEGORY_MEDIA_FLOOR so the single-root relatedness they have is enough to admit —
    # this test is about ORDER, not the category admission bar (proved separately above).
    refresh_store(cfg, scrape_client=_client(
        media, media_count_by_tag={"#hiphop": 10, "#smallbutloud": 52_228, "#bignichetag": 4_095_289}))
    derive_corpus(cfg, pid)
    corpus = Personas.load(cfg).get(pid).hashtag_corpus
    assert corpus.index("#bignichetag") < corpus.index("#smallbutloud")


def _cat_rec(metric, *, frm, media_count=None):
    """A fresh cache record: verbatim play_count (legacy median — NOT the rank since MOL-692), inbound
    `from`, and the optional `media_count` volume that IS the rank."""
    from datetime import datetime, timezone
    r = {"graph_id": "id", "play_count": float(metric),
         "measured_at": datetime.now(timezone.utc).isoformat(), "from": dict(frm)}
    if media_count is not None:
        r["media_count"] = float(media_count)
    return r


def test_category_scale_tag_needs_multi_root_relatedness(tmp_path):
    """MOL-685: platform-scale post volume must not buy a corpus seat off ONE anchor's Top.
    Refutes the MOL-665 bar, under which a single-root whale (#bars, 6.9M posts) admitted."""
    from fanops.personas import Persona
    from fanops.persona_research import CATEGORY_MEDIA_FLOOR, _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap", "arabicdrill"])
    whale = CATEGORY_MEDIA_FLOOR + 1
    cache = {"#bars": _cat_rec(9611, frm={"#syrianrap": 4}, media_count=whale),
             "#remix": _cat_rec(10697, frm={"#syrianrap": 2, "#arabicdrill": 2}, media_count=whale),
             "#nichetag": _cat_rec(120, frm={"#syrianrap": 2}, media_count=50_000)}
    tags = {t for t, _v, _s in _aligned_pool(per, cache)}
    assert "#bars" not in tags                    # single-root whale refused, however many plays
    assert "#remix" in tags                       # multi-root whale still admits — no ban list
    assert "#nichetag" in tags                    # niche-scale relatedness bar unchanged


def test_category_scale_tag_that_earned_its_seat_ranks_BY_SIZE(tmp_path):
    """MOL-692 inverts MOL-685's rank tier. The category ADMISSION bar stays (test above), but an admitted
    large tag must now lead on volume. The old demotion sent every whale behind every niche tag, which
    capped the corpus just under CATEGORY_MEDIA_FLOOR — the opposite of ranking on scale."""
    from fanops.personas import Persona
    from fanops.persona_research import CATEGORY_MEDIA_FLOOR, _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap", "arabicdrill"])
    whale = CATEGORY_MEDIA_FLOOR + 1
    cache = {"#remix": _cat_rec(120, frm={"#syrianrap": 3, "#arabicdrill": 2}, media_count=whale),
             "#nichetag": _cat_rec(999999, frm={"#syrianrap": 2}, media_count=50_000)}
    pool = _aligned_pool(per, cache)
    order = [t for t, _v, _s in pool]
    assert order.index("#remix") < order.index("#nichetag"), "the bigger admitted tag leads"
    # ...and it leads DESPITE the niche tag having ~8000x the Top-grid median plays.
    assert dict((t, v) for t, v, _s in pool)["#remix"] == float(whale)   # verbatim media_count, no blend


def test_bigger_tag_outranks_smaller_even_when_the_smaller_trends_harder(tmp_path):
    """The user-facing contract: media_count is the HIGHER signal, 7d Top-Reel max only the tie-break."""
    from fanops.personas import Persona
    from fanops.persona_research import _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap", "arabicdrill"])
    big = _cat_rec(10, frm={"#syrianrap": 2, "#arabicdrill": 2}, media_count=20_000_000)
    small = _cat_rec(10, frm={"#syrianrap": 2}, media_count=1_000_000)
    small["current_top_reel_play_max_7d"] = 50_000_000.0        # trending hard, still smaller
    order = [t for t, _v, _s in _aligned_pool(per, {"#big": big, "#small": small})]
    assert order == ["#big", "#small"]


def test_equal_size_breaks_by_recent_reel_max(tmp_path):
    from fanops.personas import Persona
    from fanops.persona_research import _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap"])
    cold = _cat_rec(10, frm={"#syrianrap": 2}, media_count=1_000_000)
    hot = _cat_rec(10, frm={"#syrianrap": 2}, media_count=1_000_000)
    hot["current_top_reel_play_max_7d"] = 900_000.0
    order = [t for t, _v, _s in _aligned_pool(per, {"#cold": cold, "#hot": hot})]
    assert order == ["#hot", "#cold"]


def test_volumeless_tags_rank_after_every_sized_tag(tmp_path):
    """MOL-714: unmeasured non-niche is refused (not ranked last). Sized related tag still admits."""
    from fanops.personas import Persona
    from fanops.persona_research import _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap"])
    sized = _cat_rec(1, frm={"#syrianrap": 2}, media_count=1_000)
    novol = _cat_rec(99_999_999, frm={"#syrianrap": 2})          # no media_count at all
    pool = _aligned_pool(per, {"#sized": sized, "#novol": novol})
    order = [t for t, _v, _s in pool]
    assert order == ["#sized"]
    assert "#novol" not in order


def test_generic_magnet_needs_real_relatedness_now(tmp_path):
    """MOL-692 deletes the magnet soft lane: #love used to enter on ONE inbound hit plus a high Top-grid
    median — the very number we no longer rank on. It is category-scale, so it needs multi-root."""
    from fanops.personas import Persona
    from fanops.persona_research import CATEGORY_MEDIA_FLOOR, _aligned_pool, _is_candidate
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap", "arabicdrill"])
    anchors = {"#syrianrap", "#arabicdrill"}
    one_hit = _cat_rec(6009, frm={"#syrianrap": 1}, media_count=CATEGORY_MEDIA_FLOOR * 40)
    assert not _is_candidate("#love", one_hit, anchors)
    assert "#love" not in {t for t, _v, _s in _aligned_pool(per, {"#love": one_hit})}
    earned = _cat_rec(6009, frm={"#syrianrap": 2, "#arabicdrill": 2},
                      media_count=CATEGORY_MEDIA_FLOOR * 40)
    assert _is_candidate("#love", earned, anchors), "no ban list — measured relatedness still admits"


def test_size_only_record_is_admissible_evidence(tmp_path):
    """A row Instagram gave volume but no usable Top median must not be invisible to size-first rank."""
    from datetime import datetime, timezone
    from fanops.hashtags import has_evidence
    from fanops.personas import Persona
    from fanops.persona_research import _aligned_pool
    per = Persona(id="craft", name="Craft", voice="x", niche=["syrianrap"])
    rec = {"graph_id": "id", "media_count": 800_000.0, "from": {"#syrianrap": 2},
           "measured_at": datetime.now(timezone.utc).isoformat()}
    assert has_evidence(rec) and not has_evidence({"graph_id": "id", "reach": 12345})
    assert "#sizeonly" in {t for t, _v, _s in _aligned_pool(per, {"#sizeonly": rec})}


def test_unreachable_platform_holds_a_derived_corpus(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 400, "comments_count": 0},
                       {"caption": "y #hiphop", "like_count": 350, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media)); derive_corpus(cfg, pid)
    before = list(Personas.load(cfg).get(pid).hashtag_corpus)
    cfg.hashtags_path.unlink()
    assert derive_corpus(cfg, pid)["changed"] is False
    assert Personas.load(cfg).get(pid).hashtag_corpus == before


# ---------------------------------------------------------------- 5. pre-derivation tags do not survive

def test_pre_derivation_tags_do_not_survive_a_derivation(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop")
    _link_active(cfg, pid)
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d["id"] == pid:
            d["hashtag_corpus"] = ["#taylorswift"]
            d["hashtag_corpus_meta"] = {"#taylorswift": {"source": "pinned", "reach": None, "added": "x"}}
    cfg.personas_path.write_text(json.dumps(raw))
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 400, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    derive_corpus(cfg, pid)
    per = Personas.load(cfg).get(pid)
    assert "#taylorswift" not in per.hashtag_corpus
    assert per.hashtag_corpus
    from fanops.accounts import Accounts
    acc = Accounts.load(cfg).accounts[0]
    assert "#taylorswift" not in list(getattr(acc, "hashtag_corpus", []) or [])


# ---------------------------------------------------------------- 6. selection is cache-sourced

def test_every_shipped_non_corpus_tag_traces_to_the_measurement_cache(tmp_path):
    cfg = Config(root=tmp_path)
    store = ["#alpha", "#beta", "#gamma"]
    out, sources = vet_hashtags_traced(None, Platform.instagram, None, store=store, corpus=["#own"], cfg=cfg)
    assert out
    assert set(sources.values()) <= {"corpus", "graph-reach", "region", "content"}
    assert "genre-floor" not in sources.values()
    for t, src in sources.items():
        if src == "graph-reach":
            assert t in store


def test_cold_cache_ships_short_not_invented(tmp_path):
    cfg = Config(root=tmp_path)
    assert vet_hashtags(None, Platform.instagram, None, store=None, corpus=None, cfg=cfg) == []
    assert vet_hashtags(None, Platform.tiktok, None, store=None, corpus=None, cfg=cfg) == []


def test_frozen_reach_pools_are_gone(tmp_path):
    import fanops.hashtags as h
    for dead in ("_MEGA", "_RELEVANCE", "_GOSSIP_MEGA", "_GOSSIP_RELEVANCE", "_NICHE_POOLS", "_RANK",
                 "VETTED", "niche_floor", "_normalize_genre", "_composition", "vetted_menu",
                 "load_store", "load_store_reach", "load_store_evidence",
                 "_DISCOVERY", "_DISCOVERY_DEFAULT"):
        assert not hasattr(h, dead), f"{dead} is a manufactured-reach artifact and must be deleted"


def test_model_cannot_ship_a_tag_outside_the_cache_or_corpus(tmp_path):
    cfg = Config(root=tmp_path)
    out = vet_hashtags(["#invented", "#alpha"], Platform.instagram, None,
                       store=["#alpha", "#beta"], corpus=["#own"], cfg=cfg)
    assert "#invented" not in out and "#alpha" in out


def test_ranked_tags_orders_by_size_then_trend_then_tag(tmp_path):
    """The menu order `vet_hashtags` inherits wholesale (MOL-692)."""
    from fanops.hashtags import SIZE_FIELD, TREND_FIELD
    m = {"#small": {SIZE_FIELD: 52_228, METRIC_FIELD: 45_355},      # loud but tiny -> last of the sized
         "#huge": {SIZE_FIELD: 20_923_125, METRIC_FIELD: 1_272},    # quiet but enormous -> first
         "#tie_cold": {SIZE_FIELD: 1_000_000},
         "#tie_hot": {SIZE_FIELD: 1_000_000, TREND_FIELD: 900_000},
         "#novolume": {METRIC_FIELD: 99_999_999}}                   # plays cannot fake volume -> last
    assert ranked_tags(m) == ["#huge", "#tie_hot", "#tie_cold", "#small", "#novolume"]


# ---------------------------------------------------------------- 7. refusals are the only governor

def test_throttle_stops_the_pass_with_evidence_intact(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop", "bars"]); _link_active(cfg, pid)
    # Measure hiphop once, then throttle on the next medias_top (bars or co-tag).
    client = _FakeClient(metric_by_tag={"#hiphop": 42, "#bars": 7}, throttle_after=1)
    refresh_store(cfg, scrape_client=client)
    assert _metric(load_measurements(cfg)["#hiphop"]) == 42
    assert client._media_n >= 1


def test_ordinary_refusal_is_recorded_and_the_pass_continues(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop", "bars"]); _link_active(cfg, pid)
    client = _FakeClient(metric_by_tag={"#hiphop": 7}, refuse_tags={"#bars", "bars"})
    out = refresh_store(cfg, scrape_client=client)
    m = load_measurements(cfg)
    assert "#hiphop" in m and "#bars" not in m
    assert out["tried"] >= 2
    refused = [u for u in out["unresolved"] if u.get("reason") == "refused" and u.get("tag") == "#bars"]
    assert refused and refused[0]["code"] == 18
    assert not (cfg.control / "hashtag_budget.json").exists()


def test_accrual_never_clobbers_on_a_dead_pass(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 42, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    out = refresh_store(cfg, scrape_client=_FakeClient(refuse=ScrapeRefused("down", code=1)))
    assert _metric(load_measurements(cfg)["#hiphop"]) == 42
    assert out["unresolved"], "a dead pass must surface refusals, not swallow them"


# ------------------------------------------- 8. the Layer B safety net is INPUT-DRIVEN (MOL-694)

def _seed_cache(cfg, rows):
    """Write hashtags.json directly — a COLD Layer A (no pass ever ran, no scrape session)."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat()
    blob = {}
    for tag, (media_count, frm) in rows.items():
        rec = {"graph_id": "id" + tag, "play_count": 100.0, "media_count": float(media_count),
               "media_count_at": stamp, "measured_at": stamp}
        if frm:
            rec["from"] = dict(frm)
        blob[tag] = rec
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps(blob))


def _count_derives(monkeypatch):
    import fanops.persona_research as pr
    calls = {"n": 0}
    real = pr.derive_corpus
    def counted(*a, **k):
        calls["n"] += 1; return real(*a, **k)
    monkeypatch.setattr(pr, "derive_corpus", counted)
    return calls


def test_unchanged_control_files_skip_the_derive_round(tmp_path, monkeypatch):
    """MOL-694: the tick re-derived EVERY persona every 12h off the marker's mtime, even when neither
    personas.json nor hashtags.json had moved. The marker now carries a content fingerprint of both, so
    an unchanged pair is `reason=unchanged` with ZERO derive_corpus calls."""
    from fanops.persona_research import refresh_corpora_if_due
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    _seed_cache(cfg, {"#hiphop": (10_000, None)})
    calls = _count_derives(monkeypatch)
    first = refresh_corpora_if_due(cfg)
    assert first["refreshed"] is True and calls["n"] == 1
    assert Personas.load(cfg).get(pid).hashtag_corpus == ["#hiphop"]
    marker = cfg.control / ".corpora_refresh.json"
    assert isinstance(json.loads(marker.read_text()).get("inputs_fp"), str)
    again = refresh_corpora_if_due(cfg)
    assert again["refreshed"] is False and again["reason"] == "unchanged"
    assert calls["n"] == 1, "an unchanged input pair must not re-derive, however old the marker"


def test_niche_change_re_derives_even_with_a_cold_layer_a(tmp_path, monkeypatch):
    """The safety net is KEPT, not deleted: an operator niche edit moves the fingerprint, so the corpus
    follows the new niche on the next tick even though Layer A never ran (no scrape session)."""
    from fanops.persona_research import refresh_corpora_if_due
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    _seed_cache(cfg, {"#hiphop": (10_000, None), "#newroot": (20_000, None)})
    assert refresh_corpora_if_due(cfg)["refreshed"] is True
    assert Personas.load(cfg).get(pid).hashtag_corpus == ["#hiphop"]
    assert refresh_corpora_if_due(cfg)["reason"] == "unchanged"
    raw = json.loads(cfg.personas_path.read_text())
    for row in raw["personas"]:
        if row["id"] == pid:
            row["niche"] = ["newroot"]
    cfg.personas_path.write_text(json.dumps(raw))
    calls = _count_derives(monkeypatch)
    out = refresh_corpora_if_due(cfg)
    assert out["refreshed"] is True and calls["n"] == 1
    assert Personas.load(cfg).get(pid).hashtag_corpus == ["#newroot"]


def test_new_measurements_move_the_fingerprint(tmp_path, monkeypatch):
    """A Layer A write is an input change: the tick that follows derives, then falls silent again."""
    from fanops.persona_research import refresh_corpora_if_due
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    _seed_cache(cfg, {"#hiphop": (10_000, None)})
    assert refresh_corpora_if_due(cfg)["refreshed"] is True
    assert refresh_corpora_if_due(cfg)["reason"] == "unchanged"
    _seed_cache(cfg, {"#hiphop": (10_000, None), "#bars": (99_000, {"#hiphop": 3})})
    calls = _count_derives(monkeypatch)
    assert refresh_corpora_if_due(cfg)["refreshed"] is True and calls["n"] == 1
    assert "#bars" in Personas.load(cfg).get(pid).hashtag_corpus
    assert refresh_corpora_if_due(cfg)["reason"] == "unchanged"
    assert calls["n"] == 1


def test_a_failed_derive_leaves_the_persona_due(tmp_path, monkeypatch):
    """No fingerprint is stamped when a persona's derive fails open — a swallowed miss must not buy
    permanent silence (the MOL-693 lesson, applied to the corpora marker)."""
    import fanops.persona_research as pr
    from fanops.persona_research import refresh_corpora_if_due
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    _seed_cache(cfg, {"#hiphop": (10_000, None)})
    calls = {"n": 0}
    def boom(*a, **k):
        calls["n"] += 1; raise RuntimeError("derive exploded")
    monkeypatch.setattr(pr, "derive_corpus", boom)
    out = refresh_corpora_if_due(cfg)
    assert out["refreshed"] is True and out.get("failed") == 1 and calls["n"] == 1
    assert "inputs_fp" not in json.loads((cfg.control / ".corpora_refresh.json").read_text())
    assert refresh_corpora_if_due(cfg)["refreshed"] is True   # still DUE — no fingerprint bought silence
    assert calls["n"] == 2
