# tests/test_hashtag_platform_truth.py
# The contract of the two-layer platform-truth hashtag architecture. Each test is REFUTE-FORM: it fails on
# the pre-rebuild code (invented likes+comments metric under a `reach` key, the fictional 30/7-day local
# budget, unmeasured corpus promotion, hand-ranked frozen pools).
#
# Layer A (network): persona description -> terms -> anchor tags -> ONE top_media fetch per tag that yields
#   BOTH the verbatim platform `like_count` and the co-occurring tags -> the measurement cache.
# Layer B (zero network): corpus = top corpus_target of the persona's aligned pool by like_count.
import json
from fanops.config import Config
from fanops.models import Platform
from fanops.hashtags import METRIC_FIELD, load_measurements, ranked_tags, vet_hashtags, vet_hashtags_traced
from fanops.fanops_hashtags import refresh_store
from fanops.personas import Personas, add_persona, apply_auto_corpus, deprecate_legacy_corpus
from fanops.persona_research import persona_terms, derive_corpus


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph(media_by_tag, *, calls=None, search_status=200):
    """Fake Graph. ig_hashtag_search resolves '<q>' -> 'id-<q>'; '{hid}/top_media' returns the tag's
    scripted media list. `calls` (a list) records every request URL so a test can prove what was spent."""
    def get(url, params=None, timeout=None):
        p = params or {}
        if calls is not None: calls.append(url)
        if "ig_hashtag_search" in url:
            if search_status != 200: return _Resp(search_status, {"error": {"code": 1}})
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            return _Resp(200, {"data": list(media_by_tag.get(tag, []))})
        return _Resp(404, None)
    return get


def _live(monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "u")


def _persona(cfg, pid="craft", name="Craft Curator", voice="syrian rapper craft", genre="hiphop"):
    add_persona(cfg, name=name, voice=voice, intake={"genre": genre}, id=pid)
    return pid


def _link_active(cfg, pid, handle="markmakmouly"):
    """An ACTIVE account carrying the persona — _posting_persona_ids narrows discovery to these."""
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    return Accounts.load(cfg)


# ---------------------------------------------------------------- 1. verbatim platform field

def test_metric_is_the_verbatim_platform_like_count_never_a_sum(tmp_path, monkeypatch):
    """The stored number is Meta's own `like_count` on ONE media item — never likes+comments, never an
    average. Refutes the pre-rebuild `_trend_score_status` total (which would store 100 here)."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 10, "comments_count": 90}]}
    refresh_store(cfg, get=_graph(media))
    m = load_measurements(cfg)
    assert m["#hiphop"][METRIC_FIELD] == 10
    flat = json.dumps(json.loads(cfg.hashtags_path.read_text()))
    assert '"reach"' not in flat and '"confidence"' not in flat, "invented metric fields must not be written"
    assert METRIC_FIELD == "like_count"


def test_first_media_carrying_a_like_count_wins_not_the_first_media(tmp_path, monkeypatch):
    """Meta hides like_count on some posts (probed live). The rule is 'first item in Meta's own order that
    CARRIES one' — so a leading like-less item is skipped, not treated as zero and not summed past."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "comments_count": 8},          # likes hidden -> skip
                         {"caption": "", "like_count": 777, "comments_count": 2},
                         {"caption": "", "like_count": 1, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media))
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 777


def test_a_tag_with_no_like_count_anywhere_is_unmeasured_and_absent(tmp_path, monkeypatch):
    """No platform value => no record. The cache holds measured tags ONLY (no zero-filling, no padding)."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "comments_count": 3}]}
    refresh_store(cfg, get=_graph(media))
    assert "#hiphop" not in load_measurements(cfg)


# ---------------------------------------------------------------- 2. no fictional local budget

def test_no_local_budget_caps_a_pass(tmp_path, monkeypatch):
    """100 resolvable candidates are ALL measured in one pass. Refutes _BUDGET_LIMIT (30/pass) and the
    'already searched this window' skip-set — both disproven live (1,500+ searches accepted in one go)."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    cotags = " ".join(f"#c{i}" for i in range(100))
    media = {"#hiphop": [{"caption": cotags, "like_count": 500, "comments_count": 0}]}
    for i in range(100):
        media[f"#c{i}"] = [{"caption": "", "like_count": 100 + i, "comments_count": 0}]
    refresh_store(cfg, get=_graph(media))
    m = load_measurements(cfg)
    assert len([t for t in m if t.startswith("#c")]) == 100
    assert not (cfg.control / "hashtag_budget.json").exists(), "the local meter must not be reborn"


def test_budget_bookkeeping_is_gone_from_the_module(tmp_path):
    """The fiction is deleted, not disabled: the names cannot come back by flipping a flag."""
    import fanops.meta_graph as mg
    import fanops.config as cf
    for dead in ("_BUDGET_LIMIT", "_BUDGET_WINDOW_DAYS", "_read_queries", "budget_remaining", "record_query",
                 "sample_trends", "trend_score", "_trend_score_status", "tag_metrics", "discover_candidates"):
        assert not hasattr(mg, dead), f"{dead} must be deleted"
    assert not hasattr(Config(root=tmp_path), "hashtag_budget_path")
    assert not hasattr(cf.Config, "hashtag_trends")


def test_graph_id_is_cached_so_a_known_tag_never_spends_another_search(tmp_path, monkeypatch):
    """Re-measurement goes straight to {id}/top_media. The search endpoint funds ONLY novel tags.
    (One-word persona so every anchor resolves and lands in the cache — an anchor that never resolves
    is legitimately re-searched next pass, which is a different behaviour from re-searching a known one.)"""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, name="Hiphop", voice="hiphop", genre="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 5, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media))
    assert load_measurements(cfg)["#hiphop"]["graph_id"] == "id-hiphop"
    calls: list = []
    refresh_store(cfg, get=_graph(media, calls=calls))
    assert not any("ig_hashtag_search" in u for u in calls), "a cached graph_id must not re-search"
    assert any("/top_media" in u for u in calls), "but it must still re-measure"


# ---------------------------------------------------------------- 3. discovery roots in the DESCRIPTION

def test_terms_come_from_the_description_never_from_the_corpus(tmp_path, monkeypatch):
    """The corpus is OUTPUT, never input — this is the cut that kills the corpus->store->corpus echo.
    A poison tag sitting in the corpus is never searched and never re-enters."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop bars", genre="rap")
    _link_active(cfg, pid)
    apply_auto_corpus(cfg, pid, tags=["#poisontag"], meta={})
    calls: list = []
    media = {"#hiphop": [{"caption": "#bars", "like_count": 5, "comments_count": 0}],
             "#bars": [{"caption": "", "like_count": 4, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media, calls=calls))
    assert not any("poisontag" in u for u in calls), "the corpus must never seed a search"
    derive_corpus(cfg, pid)
    assert "#poisontag" not in Personas.load(cfg).get(pid).hashtag_corpus, "and it cannot survive derivation"


def test_persona_terms_are_pure_and_description_derived(tmp_path):
    from fanops.personas import Persona
    per = Persona(id="x", name="Craft Curator", voice="syrian rapper craft", intake={"genre": "hiphop"},
                  hashtag_corpus=["#neverseenhere"])
    terms = persona_terms(per)
    assert "hiphop" in terms and "syrian" in terms and "rapper" in terms
    assert "craftcurator" in terms, "the whole-name concatenation is how IG tags are written"
    assert not any("neverseenhere" in t for t in terms), "the corpus is never a term source"
    assert terms == persona_terms(per), "pure"


# ---------------------------------------------------------------- 4. corpus is derived + evidence-only

def test_unmeasured_candidate_never_enters_a_corpus(tmp_path, monkeypatch):
    """Refutes the live `reach: null` auto-fills — a tag with no platform measurement cannot occupy a slot,
    and the corpus stays honestly SHORT rather than padded."""
    _live(monkeypatch)
    monkeypatch.setenv("FANOPS_CORPUS_TARGET", "10")
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#measured #unmeasured", "like_count": 500, "comments_count": 0}],
             "#measured": [{"caption": "", "like_count": 400, "comments_count": 0}],
             "#unmeasured": [{"caption": "", "comments_count": 9}]}      # no like_count anywhere
    refresh_store(cfg, get=_graph(media))
    derive_corpus(cfg, pid)
    per = Personas.load(cfg).get(pid)
    assert "#measured" in per.hashtag_corpus
    assert "#unmeasured" not in per.hashtag_corpus
    assert len(per.hashtag_corpus) < 10, "short, not padded"


def test_derivation_is_zero_network(tmp_path, monkeypatch):
    """Layer B is a pure function of the cache + the persona. A Graph call from here is a bug."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "", "like_count": 400, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media))
    import fanops.meta_graph as mg
    def explode(*a, **k): raise AssertionError("derive_corpus must not touch the network")
    monkeypatch.setattr(mg, "_default_get", explode)
    monkeypatch.setattr(mg, "resolve_hashtag", explode)
    assert derive_corpus(cfg, pid)["changed"] is True


def test_corpus_is_ranked_by_the_platform_field(tmp_path, monkeypatch):
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#low #high", "like_count": 500, "comments_count": 0}],
             "#low": [{"caption": "", "like_count": 1, "comments_count": 9999}],
             "#high": [{"caption": "", "like_count": 900, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media))
    derive_corpus(cfg, pid)
    corpus = Personas.load(cfg).get(pid).hashtag_corpus
    assert corpus.index("#high") < corpus.index("#low"), "comments must not influence rank"


def test_unreachable_platform_holds_a_derived_corpus(tmp_path, monkeypatch):
    """Outage fail-safe (NOT preservation): an already-derived corpus survives a dead pass unchanged."""
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "#bars", "like_count": 500, "comments_count": 0}],
             "#bars": [{"caption": "", "like_count": 400, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media)); derive_corpus(cfg, pid)
    before = list(Personas.load(cfg).get(pid).hashtag_corpus)
    cfg.hashtags_path.unlink()                                  # cache gone == nothing measurable
    assert derive_corpus(cfg, pid)["changed"] is False
    assert Personas.load(cfg).get(pid).hashtag_corpus == before


# ---------------------------------------------------------------- 5. deprecation cutover

def test_first_pass_deprecates_every_pre_derivation_corpus_tag(tmp_path):
    """The existing corpus has NO authority. Legacy tags (r4 pins included) are MOVED to a visible
    hashtag_corpus_deprecated field and stop shipping immediately — not silently kept, not ignored."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop")
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d["id"] == pid:
            d["hashtag_corpus"] = ["#bars", "#lyrics", "#hiphopmusic"]
            d["hashtag_corpus_meta"] = {t: {"source": "pinned", "reach": None, "added": "20260716T130424Z"}
                                        for t in d["hashtag_corpus"]}
    cfg.personas_path.write_text(json.dumps(raw))
    moved = deprecate_legacy_corpus(cfg, pid)
    assert set(moved) == {"#bars", "#lyrics", "#hiphopmusic"}
    per = Personas.load(cfg).get(pid)
    assert per.hashtag_corpus == []
    assert set(per.hashtag_corpus_deprecated) == {"#bars", "#lyrics", "#hiphopmusic"}
    assert deprecate_legacy_corpus(cfg, pid) == [], "idempotent"


def test_deprecated_tags_do_not_ship_and_do_not_resurrect(tmp_path, monkeypatch):
    """After cutover the old tags are invisible to hydration/selection, and a later derivation only brings
    a tag back through fresh platform evidence."""
    _live(monkeypatch)
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
    refresh_store(cfg, get=_graph(media))
    derive_corpus(cfg, pid)
    per = Personas.load(cfg).get(pid)
    assert "#taylorswift" not in per.hashtag_corpus
    assert "#taylorswift" in per.hashtag_corpus_deprecated
    from fanops.accounts import Accounts
    acc = Accounts.load(cfg).accounts[0]
    assert "#taylorswift" not in list(getattr(acc, "hashtag_corpus", []) or [])


# ---------------------------------------------------------------- 6. selection is cache-sourced

def test_every_shipped_non_corpus_tag_traces_to_the_measurement_cache(tmp_path):
    """Refutes the hand-ranked frozen pools: with a cache present, backfill comes from measured tags, and
    no tag is admitted on a June-2026 hand-research reach claim."""
    cfg = Config(root=tmp_path)
    store = ["#alpha", "#beta", "#gamma"]
    out, sources = vet_hashtags_traced(None, Platform.instagram, None, store=store, corpus=["#own"], cfg=cfg)
    assert out
    assert set(sources.values()) <= {"corpus", "graph-reach", "discovery", "region", "content"}
    assert "genre-floor" not in sources.values()
    for t, src in sources.items():
        if src == "graph-reach":
            assert t in store


def test_cold_cache_ships_short_not_invented(tmp_path):
    """No measurements => a short line carrying only the platform discovery slot. Honest beats padded."""
    cfg = Config(root=tmp_path)
    assert vet_hashtags(None, Platform.instagram, None, store=None, corpus=None, cfg=cfg) == ["#reels"]
    assert vet_hashtags(None, Platform.tiktok, None, store=None, corpus=None, cfg=cfg) == ["#fyp"]


def test_frozen_reach_pools_are_gone(tmp_path):
    import fanops.hashtags as h
    for dead in ("_MEGA", "_RELEVANCE", "_GOSSIP_MEGA", "_GOSSIP_RELEVANCE", "_NICHE_POOLS", "_RANK",
                 "VETTED", "niche_floor", "_normalize_genre", "_composition", "vetted_menu",
                 "load_store", "load_store_reach", "load_store_evidence"):
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

def test_throttle_backs_off_then_stops_the_pass_with_evidence_intact(tmp_path, monkeypatch):
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop bars"); _link_active(cfg, pid)
    import fanops.meta_graph as mg
    slept: list = []
    monkeypatch.setattr(mg, "_sleep", lambda s: slept.append(s))
    media = [{"caption": "", "like_count": 42, "comments_count": 0}]
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            if p.get("q") == "hiphop":
                return _Resp(200, {"data": [{"id": "id-hiphop"}]})
            return _Resp(400, {"error": {"code": 4, "message": "rate limit"}})   # throttle
        if url.endswith("/top_media"):
            return _Resp(200, {"data": list(media)})
        return _Resp(404, None)
    refresh_store(cfg, get=get)
    assert slept, "a throttle must back off before giving up"
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 42, "evidence accrued before the stop persists"


def test_ordinary_refusal_skips_the_tag_and_the_pass_continues(tmp_path, monkeypatch):
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop bars"); _link_active(cfg, pid)
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            if p.get("q") == "bars":
                return _Resp(400, {"error": {"code": 18, "message": "search window"}})
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = url.rsplit("/", 2)[-2].replace("id-", "")
            if tag == "hiphop":
                return _Resp(200, {"data": [{"caption": "", "like_count": 7, "comments_count": 0}]})
        return _Resp(404, None)
    refresh_store(cfg, get=get)
    m = load_measurements(cfg)
    assert "#hiphop" in m and "#bars" not in m
    assert not (cfg.control / "hashtag_budget.json").exists()


def test_accrual_never_clobbers_on_a_dead_pass(tmp_path, monkeypatch):
    _live(monkeypatch)
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, voice="hiphop"); _link_active(cfg, pid)
    media = {"#hiphop": [{"caption": "", "like_count": 42, "comments_count": 0}]}
    refresh_store(cfg, get=_graph(media))
    refresh_store(cfg, get=lambda url, params=None, timeout=None: _Resp(500, None))
    assert load_measurements(cfg)["#hiphop"][METRIC_FIELD] == 42
