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
    assert load_measurements(cfg)["#hiphop"]["media_count"] == 50000.0


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
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, name="Hiphop", voice="hiphop", niche=["hiphop"]); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 5, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    assert load_measurements(cfg)["#hiphop"]["graph_id"] == "id-hiphop"
    client = _client(media)
    refresh_store(cfg, scrape_client=client)
    assert "hiphop" not in client.info_calls, "a cached graph_id must not re-resolve"
    assert "hiphop" in client.media_calls, "but it must still re-measure"


def test_cached_tag_metric_and_stamp_move_on_every_pass(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["lyricism", "hiphop"]); _link_active(cfg, pid)
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=13)
    media0 = {"#hiphop": [{"caption": "", "like_count": 100, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media0), now=t0)
    before = load_measurements(cfg)["#hiphop"]
    assert before["like_count"] == 100.0 and _metric(before) == 100.0
    stamp0 = before["measured_at"]

    client = _FakeClient(
        media_by_tag={"#hiphop": [{"caption": "", "like_count": 250}]},
        refuse_tags={"#lyricism", "lyricism"})
    # seed hiphop id so resolve is skipped; lyricism is novel and will refuse on info
    out = refresh_store(cfg, scrape_client=client, now=t1)
    after = load_measurements(cfg)["#hiphop"]
    assert after["like_count"] == 250.0 and _metric(after) == 250.0, "platform's new like_count must land"
    assert after["measured_at"] != stamp0 and after["measured_at"].startswith("2026-07-02T01:00")
    assert "hiphop" not in client.info_calls, "cached #hiphop must not re-resolve"
    assert "hiphop" in client.media_calls, "but it must still re-measure"
    refused = [u for u in (out.get("unresolved") or []) if u.get("tag") == "#lyricism"]
    assert refused and refused[0].get("code") == 18 and refused[0].get("reason") == "refused"
    assert out.get("throttled") is False, "code 18 must not abort the pass as ScrapeThrottled"


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
                  content_focus=["punchlines"], hook_angle="curiosity", intensity="high",
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
    """Inbound: niche on the candidate's own Top writes from. Pool needs relatedness (MOL-665);
    #fyp is a magnet with high metric → soft lane admits one-hit."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="x", niche=["rapbeef"]); _link_active(cfg, pid)
    media = {"#rapbeef": [{"caption": "noise #fyp", "like_count": 10}],
             "#fyp": [{"caption": "chaos #rapbeef #drama", "like_count": 9000}]}
    refresh_store(cfg, scrape_client=_client(media))
    rec = load_measurements(cfg)["#fyp"]
    assert rec.get("from", {}).get("#rapbeef", 0) >= 1
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
    refresh_store(cfg, scrape_client=_client(media))
    derive_corpus(cfg, pid)
    per = Personas.load(cfg).get(pid)
    assert "#measured" in per.hashtag_corpus
    assert "#unmeasured" not in per.hashtag_corpus
    assert len(per.hashtag_corpus) < 10


def test_derivation_is_zero_network(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 400, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    # Layer A write path already re-derived corpora — corpus must be non-empty before the second call.
    assert Personas.load(cfg).get(pid).hashtag_corpus
    import fanops.ig_hashtag_scrape as igs
    def explode(*a, **k): raise AssertionError("derive_corpus must not touch the network")
    monkeypatch.setattr(igs, "resolve_hashtag_scrape", explode)
    monkeypatch.setattr(igs, "measure_and_harvest_scrape", explode)
    # Second derive is idempotent (changed=False) and must never touch the network.
    assert derive_corpus(cfg, pid)["changed"] is False


def test_corpus_is_ranked_by_the_platform_field(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#low #high", "like_count": 500, "comments_count": 0}],
             "#low": [{"caption": "x #hiphop", "like_count": 1, "comments_count": 9999}],
             "#high": [{"caption": "x #hiphop", "like_count": 900, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    derive_corpus(cfg, pid)
    corpus = Personas.load(cfg).get(pid).hashtag_corpus
    assert corpus.index("#high") < corpus.index("#low")


def test_unreachable_platform_holds_a_derived_corpus(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "x #hiphop", "like_count": 400, "comments_count": 0}]}
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


def test_ranked_tags_orders_by_platform_field_desc(tmp_path):
    m = {"#a": {METRIC_FIELD: 10}, "#b": {METRIC_FIELD: 900}, "#c": {METRIC_FIELD: 900}}
    assert ranked_tags(m) == ["#b", "#c", "#a"]


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
