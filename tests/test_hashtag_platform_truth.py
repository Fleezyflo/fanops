# tests/test_hashtag_platform_truth.py
# The contract of the two-layer platform-truth hashtag architecture. Each test is REFUTE-FORM: it fails on
# the pre-rebuild code (invented likes+comments metric under a `reach` key, the fictional 30/7-day local
# budget, unmeasured corpus promotion, hand-ranked frozen pools).
#
# Layer A (network via instagrapi): persona description -> terms -> anchor tags -> ONE medias_top fetch
#   per tag that yields BOTH the verbatim platform `like_count` and the co-occurring tags -> the cache.
# Layer B (zero network): corpus = top corpus_target of the persona's aligned pool by like_count.
import json
from fanops.config import Config
from fanops.models import Platform
from fanops.hashtags import METRIC_FIELD, load_measurements, ranked_tags, vet_hashtags, vet_hashtags_traced
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

def test_metric_is_the_verbatim_platform_like_count_never_a_sum(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 10, "comments_count": 90}]}
    refresh_store(cfg, scrape_client=_client(media))
    m = load_measurements(cfg)
    assert m["#hiphop"][METRIC_FIELD] == 10
    flat = json.dumps(json.loads(cfg.hashtags_path.read_text()))
    assert '"reach"' not in flat and '"confidence"' not in flat
    assert METRIC_FIELD == "like_count"


def test_first_media_carrying_a_like_count_wins_not_the_first_media(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "comments_count": 8},
                         {"caption": "", "like_count": 777, "comments_count": 2},
                         {"caption": "", "like_count": 1, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 777


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
        media[f"#c{i}"] = [{"caption": "", "like_count": 100 + i, "comments_count": 0}]
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
    assert before[METRIC_FIELD] == 100.0
    stamp0 = before["measured_at"]

    client = _FakeClient(
        media_by_tag={"#hiphop": [{"caption": "", "like_count": 250}]},
        refuse_tags={"#lyricism", "lyricism"})
    # seed hiphop id so resolve is skipped; lyricism is novel and will refuse on info
    out = refresh_store(cfg, scrape_client=client, now=t1)
    after = load_measurements(cfg)["#hiphop"]
    assert after[METRIC_FIELD] == 250.0, "platform's new like_count must land"
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
             "#bars": [{"caption": "", "like_count": 4, "comments_count": 0}]}
    client = _client(media)
    refresh_store(cfg, scrape_client=client)
    assert "poisontag" not in client.info_calls and "poisontag" not in client.media_calls
    derive_corpus(cfg, pid)
    assert "#poisontag" not in Personas.load(cfg).get(pid).hashtag_corpus


def test_persona_terms_include_surface_not_niche_only(tmp_path):
    """F-2: niche + levers + voice unigrams; no adjacent-voice glue; corpus-blind."""
    from fanops.personas import Persona
    per = Persona(id="x", name="Craft Curator", voice="syrian rapper craft",
                  niche=["Lyricism", "songwriting", "lyricism", "#MusicReview"],
                  content_focus=["punchlines"], hook_angle="curiosity",
                  hashtag_corpus=["#neverseenhere"])
    terms = persona_terms(per)
    assert terms[:3] == ["lyricism", "songwriting", "musicreview"]  # niche first
    assert "punchlines" in terms and "curiosity" in terms
    assert "syrian" in terms and "rapper" in terms and "craft" in terms  # voice unigrams
    assert "syrianrapper" not in terms and "rappercraft" not in terms     # no pairwise glue
    assert "craftcurator" not in terms and "hiphop" not in terms
    assert not any("neverseenhere" in t for t in terms)
    assert terms == persona_terms(per)


def test_persona_terms_disjoint_voices_diverge(tmp_path):
    from fanops.personas import Persona
    a = Persona(id="a", name="A", voice="lyric craft musicianship", niche=["hiphop"])
    b = Persona(id="b", name="B", voice="unhinged viral chaos drama", niche=["hiphop"])
    ta, tb = set(persona_terms(a)), set(persona_terms(b))
    assert "hiphop" in ta & tb
    assert ta - {"hiphop"} != tb - {"hiphop"}
    assert "musicianship" in ta and "musicianship" not in tb
    assert "unhinged" in tb and "unhinged" not in ta


# ---------------------------------------------------------------- 4. corpus is derived + evidence-only

def test_unmeasured_candidate_never_enters_a_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "10")
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#measured #unmeasured", "like_count": 500, "comments_count": 0}],
             "#measured": [{"caption": "", "like_count": 400, "comments_count": 0}],
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
             "#bars": [{"caption": "", "like_count": 400, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    import fanops.ig_hashtag_scrape as igs
    def explode(*a, **k): raise AssertionError("derive_corpus must not touch the network")
    monkeypatch.setattr(igs, "resolve_hashtag_scrape", explode)
    monkeypatch.setattr(igs, "measure_and_harvest_scrape", explode)
    assert derive_corpus(cfg, pid)["changed"] is True


def test_corpus_is_ranked_by_the_platform_field(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#low #high", "like_count": 500, "comments_count": 0}],
             "#low": [{"caption": "", "like_count": 1, "comments_count": 9999}],
             "#high": [{"caption": "", "like_count": 900, "comments_count": 0}]}
    refresh_store(cfg, scrape_client=_client(media))
    derive_corpus(cfg, pid)
    corpus = Personas.load(cfg).get(pid).hashtag_corpus
    assert corpus.index("#high") < corpus.index("#low")


def test_unreachable_platform_holds_a_derived_corpus(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "", "like_count": 400, "comments_count": 0}]}
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
             "#bars": [{"caption": "", "like_count": 400, "comments_count": 0}]}
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
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 42
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
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 42
    assert out["unresolved"], "a dead pass must surface refusals, not swallow them"
