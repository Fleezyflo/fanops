# tests/test_fanops_hashtags.py
# Hashtag store builder — the ONLY judge of a hashtag is its LIVE Meta Graph reach (operator 2026-06-27).
# refresh_store harvests co-occurring candidates from the niche seeds, measures their Graph reach within the
# 30/7-day budget, ranks by reach, writes 00_control/hashtags.json. NO ledger, NO learn-doctor gate (the store
# is independent of any published post). Own-post-reach attribution (tag_reach_means/rank_tags_by_reach) and
# the doctor gate were DELETED — a post's outcome attributes to the hook/clip/account, never to the hashtag.
import inspect
import json
from fanops.config import Config
from fanops.models import Platform
from fanops.hashtags import load_store, vetted_menu, vet_hashtags
from fanops.fanops_hashtags import refresh_store


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph_router(reach_by_tag, *, cooccur=""):
    """A fake Meta Graph `get`: ig_hashtag_search resolves '#tag'->'id-tag'; {hid}/top_media returns the
    co-occurring caption (for the harvest) + like/comments = the tag's reach (for the measurement)."""
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            return _Resp(200, {"data": [{"caption": cooccur, "like_count": reach_by_tag.get(tag, 0),
                                         "comments_count": 0}]})
        return _Resp(404, None)
    return get


def test_refresh_store_takes_no_ledger_and_no_doctor_gate(tmp_path, monkeypatch):
    # The own-reach model is gone: refresh_store's signature carries NO `led`, and it writes WITHOUT any
    # learn-doctor verdict on disk (the store does not depend on a published post).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    assert "led" not in inspect.signature(refresh_store).parameters
    cfg = Config(root=tmp_path)
    assert not (cfg.control / "learn_doctor.json").exists()     # no doctor verdict anywhere
    out = refresh_store(cfg)
    assert out["written"] is True and cfg.hashtags_path.exists()  # still writes — no gate


def test_refresh_store_ranks_by_live_graph_reach(tmp_path, monkeypatch):
    # The store is ranked by LIVE Graph reach: a higher-reach co-occurring tag leads, regardless of any post.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    from fanops import personas as P
    P.add_persona(cfg, name="Curator", id="curator")
    P.add_corpus_tag(cfg, "curator", "#seed")               # the niche seed the harvest reads from
    get = _graph_router({"#beta": 900, "#alpha": 100}, cooccur="#alpha #beta")
    out = refresh_store(cfg, get=get)
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert store[0] == "#beta"                              # highest LIVE Graph reach leads
    assert store.index("#beta") < store.index("#alpha")     # reach order, not insertion order
    assert out["measured"] >= 2


def test_refresh_store_fail_open_without_creds_writes_frozen_floor(tmp_path, monkeypatch):
    # No Meta creds -> harvest/measure no-op -> the frozen reach-ranked seed stands (never empty, never raises).
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    out = refresh_store(cfg)
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["written"] is True and out["measured"] == 0
    assert store[0] == vetted_menu()[0]                     # frozen floor order, byte-stable
    assert "#hiphop" in store


def test_load_store_reads_tags_or_none(tmp_path):
    cfg = Config(root=tmp_path)
    assert load_store(cfg) is None                          # absent -> None (fall back to frozen)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#x", "#y"]}))
    assert load_store(cfg) == ["#x", "#y"]
    cfg.hashtags_path.write_text("{ corrupt")
    assert load_store(cfg) is None                          # corrupt -> None, never raises


def test_refresh_store_flag_off_writes_frozen_floor_even_with_creds(tmp_path, monkeypatch):
    # FANOPS_HASHTAG_TRENDS=0 is the operator escape hatch: the store is the frozen reach floor only — no Graph
    # harvest/measure — even when Meta creds are present. Keeps the flag meaningful (not a dead switch).
    monkeypatch.setenv("FANOPS_HASHTAG_TRENDS", "0")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    out = refresh_store(cfg, get=_graph_router({"#beta": 900}, cooccur="#beta"))   # router present but must NOT be used
    store = json.loads(cfg.hashtags_path.read_text())["tags"]
    assert out["measured"] == 0 and out["harvested"] == 0       # Graph sampling skipped
    assert store == vetted_menu()                               # frozen floor verbatim


def test_load_store_reach_reads_graph_reach_map_or_empty(tmp_path):
    # WS5: refresh_store persists {"reach": {tag: graph reach}} for the Studio surface; load_store_reach reads
    # it normalized, fail-open to {} when absent / no reach key / corrupt.
    from fanops.hashtags import load_store_reach
    cfg = Config(root=tmp_path)
    assert load_store_reach(cfg) == {}                      # absent -> {}
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"]}))     # no reach key -> {}
    assert load_store_reach(cfg) == {}
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"], "reach": {"#A": 1200, "#b": "x"}}))
    assert load_store_reach(cfg) == {"#a": 1200.0}          # normalized key; non-numeric dropped


def test_vetted_menu_uses_store_when_given_else_frozen():
    assert vetted_menu(store=["#a", "#b"]) == ["#a", "#b"]  # dynamic store drives the menu
    assert "#hiphop" in vetted_menu()                       # no store -> frozen pools


def test_vet_hashtags_store_aware_and_byte_identical_without_store():
    base = vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en")
    assert vet_hashtags(["#hiphop", "#garbageword"], Platform.instagram, "en", store=None) == base
    out = vet_hashtags(["#mytrend", "#garbageword"], Platform.instagram, "en", store=["#mytrend", "#second"])
    assert "#mytrend" in out and "#garbageword" not in out
    assert len(out) <= 4


# --- `hashtags discover` REPORTS fresh per-persona tags, NEVER writes the caption menu. Auto-absorbing
# unvetted discoveries into the menu was DROPPED (an engagement floor admits spam + bypasses the operator
# curation gate). Curation stays operator-gated in the Studio: discover -> operator ACCEPTS into a corpus.
def test_cmd_hashtags_discover_reports_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    from fanops import personas as P
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Curator", id="curator")
    monkeypatch.setattr("fanops.personas.discover_corpus",
                        lambda c, pid, **k: [{"tag": "#detroitrap", "count": 9}])
    rc = cmd_hashtags_discover(cfg)
    out = capsys.readouterr().out
    assert rc == 0 and "#detroitrap" in out and "curator" in out
    assert not cfg.hashtags_path.exists()                   # discovery NEVER writes the caption menu


def test_cmd_hashtags_discover_no_personas(tmp_path, capsys):
    from fanops.fanops_hashtags import cmd_hashtags_discover
    rc = cmd_hashtags_discover(Config(root=tmp_path))
    assert rc == 0 and "no personas" in capsys.readouterr().out.lower()


# --- WS2: the run loop refreshes the Graph-reach store on a throttle (constant update), fail-open.
def test_refresh_store_if_due_throttles_and_fail_open(tmp_path, monkeypatch):
    import os
    from fanops.fanops_hashtags import refresh_store_if_due
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    assert refresh_store_if_due(cfg)["refreshed"] is False       # no Meta creds -> clean no-op
    assert not cfg.hashtags_path.exists()
    monkeypatch.setenv("META_GRAPH_TOKEN", "t"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    assert refresh_store_if_due(cfg)["refreshed"] is True        # no store yet -> writes (fail-open frozen floor)
    assert cfg.hashtags_path.exists()
    assert refresh_store_if_due(cfg, max_age_s=43200)["refreshed"] is False   # just written -> fresh -> throttled
    old = cfg.hashtags_path.stat().st_mtime - 100000
    os.utime(cfg.hashtags_path, (old, old))
    assert refresh_store_if_due(cfg, max_age_s=10)["refreshed"] is True       # stale -> refresh again
