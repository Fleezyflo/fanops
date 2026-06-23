# tests/test_graph_cooccurrence.py
# M1+M2 — live hashtag DISCOVERY via Graph co-occurrence. harvest_cooccurring resolves each category
# SEED tag, reads its live top_media captions, and tallies the hashtags those currently-winning posts use
# ALONGSIDE the seed — the only Graph-native way to find tags the system has never named (IG has no
# "trending tags by topic" endpoint). discover_candidates ranks the harvest, drops known tags, and
# optionally measures the top-K within the 30/7-day budget. Same fail-soft/fail-closed discipline as
# sample_trends; the seed RESOLUTION spends one ig_hashtag_search slot, the top_media caption read is free.
from fanops.config import Config
from fanops.meta_graph import harvest_cooccurring, discover_candidates, budget_remaining, record_query


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _creds(monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")


def _router(media):
    """Fake Graph: ig_hashtag_search resolves any seed to a node id; top_media returns `media`."""
    def get(url, params=None, timeout=None):
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + (params or {}).get("q", "")}]})
        if "top_media" in url:
            return _Resp(200, {"data": media})
        return _Resp(404, None)
    return get


# --- harvest_cooccurring (M1) ------------------------------------------------------------------

def test_harvest_tallies_cooccurring_tags(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"caption": "fire verse #detroitrap #bars", "like_count": 100, "comments_count": 10},
             {"caption": "#detroitrap again", "like_count": 50, "comments_count": 0}]
    out = harvest_cooccurring(Config(root=tmp_path), ["#hiphop"], get=_router(media))
    assert out["#detroitrap"]["count"] == 2
    assert out["#detroitrap"]["host_engagement"] == 160.0      # (100+10) + (50+0)
    assert out["#bars"]["count"] == 1 and out["#bars"]["host_engagement"] == 110.0


def test_harvest_excludes_the_seed_itself(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"caption": "#hiphop #bars", "like_count": 10, "comments_count": 0}]
    out = harvest_cooccurring(Config(root=tmp_path), ["#hiphop"], get=_router(media))
    assert "#hiphop" not in out and "#bars" in out


def test_harvest_keeps_arabic_cooccurring_tag(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"caption": "خليها تكبر #اغاني", "like_count": 5, "comments_count": 1}]
    out = harvest_cooccurring(Config(root=tmp_path), ["#hiphop"], get=_router(media))
    assert "#اغاني" in out             # the Arabic-block tag survives the regex


def test_harvest_no_creds_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    assert harvest_cooccurring(Config(root=tmp_path), ["#hiphop"], get=_router([])) == {}


def test_harvest_budget_unreadable_failclosed(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("{ not json")
    called = {"n": 0}
    def get(url, params=None, timeout=None):
        called["n"] += 1; return _Resp(200, {"data": []})
    out = harvest_cooccurring(cfg, ["#hiphop"], get=get)
    assert out == {} and called["n"] == 0                      # refused BEFORE any Graph call


def test_harvest_spends_one_slot_per_seed(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    before = budget_remaining(cfg)
    media = [{"caption": "#x", "like_count": 1, "comments_count": 0}]
    harvest_cooccurring(cfg, ["#hiphop", "#rap"], get=_router(media))
    assert budget_remaining(cfg) == before - 2                 # two distinct seeds resolved = two slots


def test_harvest_caption_none_skipped(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"like_count": 5, "comments_count": 1}, {"caption": None, "like_count": 2, "comments_count": 0},
             {"caption": "#keep", "like_count": 3, "comments_count": 0}]
    out = harvest_cooccurring(Config(root=tmp_path), ["#hiphop"], get=_router(media))
    assert out == {"#keep": {"count": 1, "host_engagement": 3.0}}


def test_harvest_caps_distinct_tags(tmp_path, monkeypatch):
    # untrusted UGC guard: a pathological caption can't grow the tally dict without bound.
    _creds(monkeypatch)
    monkeypatch.setattr("fanops.meta_graph._HARVEST_CAP", 3)
    caption = " ".join(f"#t{i}" for i in range(10))
    media = [{"caption": caption, "like_count": 1, "comments_count": 0}]
    out = harvest_cooccurring(Config(root=tmp_path), ["#seed"], get=_router(media))
    assert len(out) == 3                                       # distinct co-tags bounded by _HARVEST_CAP


def test_harvest_duplicate_seed_deduped(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    before = budget_remaining(cfg)
    media = [{"caption": "#x", "like_count": 1, "comments_count": 0}]
    harvest_cooccurring(cfg, ["#hiphop", "#HipHop", "  #hiphop "], get=_router(media))
    assert budget_remaining(cfg) == before - 1                 # normalized-duplicate seeds resolve once


# --- discover_candidates (M2) ------------------------------------------------------------------

def test_discover_ranks_by_count_then_engagement(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"caption": "#a #b", "like_count": 10, "comments_count": 0},
             {"caption": "#a", "like_count": 5, "comments_count": 0}]
    out = discover_candidates(Config(root=tmp_path), ["#seed"], get=_router(media))
    assert [c["tag"] for c in out] == ["#a", "#b"]              # #a count 2 outranks #b count 1
    assert out[0]["count"] == 2 and all("measured_engagement" not in c for c in out)   # measure_k=0 default


def test_discover_drops_known_tags_normalized(tmp_path, monkeypatch):
    _creds(monkeypatch)
    media = [{"caption": "#a #b", "like_count": 1, "comments_count": 0}]
    out = discover_candidates(Config(root=tmp_path), ["#seed"], known={"A"}, get=_router(media))   # un-normalized
    tags = [c["tag"] for c in out]
    assert "#a" not in tags and "#b" in tags                    # known is normalized before exclusion


def test_discover_measure_k_zero_spends_only_harvest(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    before = budget_remaining(cfg)
    media = [{"caption": "#a #b", "like_count": 1, "comments_count": 0}]
    discover_candidates(cfg, ["#seed"], get=_router(media))
    assert budget_remaining(cfg) == before - 1                 # only the seed resolution, no measurement


def test_discover_measures_top_k(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    media = [{"caption": "#a #b #c", "like_count": 100, "comments_count": 0}]
    out = discover_candidates(cfg, ["#seed"], measure_k=2, get=_router(media))
    measured = [c for c in out if "measured_engagement" in c]
    assert len(measured) == 2                                  # the top 2 get a reach measurement
    assert measured[0]["measured_engagement"] == 100.0 and "sampled_at" in measured[0]


def test_discover_measure_respects_remaining_budget(tmp_path, monkeypatch):
    _creds(monkeypatch)
    cfg = Config(root=tmp_path)
    for i in range(28):                                        # 28 pre + 1 seed + 1 measure = the 30 cap
        record_query(cfg, f"#pre{i}")
    media = [{"caption": "#a #b #c", "like_count": 5, "comments_count": 0}]
    out = discover_candidates(cfg, ["#seed"], measure_k=3, get=_router(media))
    measured = [c for c in out if "measured_engagement" in c]
    assert len(measured) == 1                                  # only one slot remained after the seed


def test_discover_no_creds_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    assert discover_candidates(Config(root=tmp_path), ["#seed"], measure_k=2, get=_router([])) == []
