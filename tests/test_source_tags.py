# tests/test_source_tags.py
"""HV1-INGEST producer: research → exact-name pile → dual scrape+Graph lock. Fake client, no network."""
from __future__ import annotations
import inspect
import json
from types import SimpleNamespace

from fanops.caption import request_captions
from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, search_hashtags_scrape
from fanops.source_tags import (SOURCE_TAG_LOCKS_NAME, ensure_source_lock, load_source_tag_locks,
                                source_tag_locks_path)
from fanops.studio.actions import regenerate_caption
from hashtag_scrape_fakes import _FakeClient, _Media


class _Hit:
    def __init__(self, name, id=None, media_count=None):
        self.name = name
        self.id = id
        self.media_count = media_count


class _SearchClient(_FakeClient):
    def __init__(self, search_by_query, **kw):
        super().__init__(**kw)
        self.search_by_query = dict(search_by_query)
        self.search_calls: list[str] = []

    def search_hashtags(self, query):
        self.search_calls.append(query)
        return list(self.search_by_query.get(query, []))


def _src(sid="src_1", title="a session", language="en", transcript=None):
    return SimpleNamespace(id=sid, title=title, language=language, transcript=transcript)


def _cfg(tmp_path):
    return Config(root=tmp_path)


def _ok_graph(metric=10.0, missing=()):
    miss = {f"#{n.lstrip('#')}" for n in missing}

    def resolve(_cfg, tag):
        n = tag if str(tag).startswith("#") else f"#{tag}"
        if n in miss:
            return None
        return f"gid-{n}"

    def measure(_cfg, hid):
        return metric, {}

    return {"resolve_fn": resolve, "measure_fn": measure}


def test_siblings_not_on_pile(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient({
        "music": [_Hit("incomplete"), _Hit("full", id="ig-1", media_count=99), _Hit(None)],
    })
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda _s, _e: ["music"],
                       **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["pile"] == ["#music"]
    assert "#incomplete" not in rec["pile"]
    assert "#full" not in rec["pile"]
    assert rec["lock"] == []
    assert rec["researched_at"]
    assert not cfg.hashtags_path.exists()


def test_lock_is_dual_qualify_llm_order_capped_fifteen(tmp_path):
    cfg = _cfg(tmp_path)
    names = [f"t{i}" for i in range(16)]
    search = {n: [_Hit(n)] for n in names}
    search["bigfolder"] = [_Hit("bigfolder")]
    search["highplay"] = [_Hit("highplay")]
    media = {f"#{n}": [_Media(1, "", play_count=i + 1)] for i, n in enumerate(names)}
    media["#bigfolder"] = [_Media(1, "", play_count=10)]
    media["#highplay"] = [_Media(1, "", play_count=9000)]
    client = _SearchClient(
        search,
        media_by_tag=media,
        media_count_by_tag={"#bigfolder": 1_500_000, "#highplay": 50_000},
    )
    ensure_source_lock(cfg, _src(), client=client,
                       research_fn=lambda _s, _e: names + ["bigfolder", "highplay"],
                       **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    lock = rec["lock"]
    assert rec["pile"][:16] == [f"#t{i}" for i in range(16)]
    assert "#bigfolder" in rec["pile"] and "#highplay" in rec["pile"]
    assert len(lock) == 15
    assert lock == [f"#t{i}" for i in range(15)]
    assert "#t15" not in lock
    assert "#highplay" not in lock


def test_high_media_low_play_keeps_llm_order_when_dual(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient(
        {"bigfolder": [_Hit("bigfolder")], "highplay": [_Hit("highplay")]},
        media_by_tag={
            "#bigfolder": [_Media(1, "", play_count=10)],
            "#highplay": [_Media(1, "", play_count=9000)],
        },
        media_count_by_tag={"#bigfolder": 1_500_000, "#highplay": 50_000},
    )
    ensure_source_lock(cfg, _src(), client=client,
                       research_fn=lambda _s, _e: ["bigfolder", "highplay"],
                       **_ok_graph())
    assert load_source_tag_locks(cfg)["src_1"]["lock"] == ["#bigfolder", "#highplay"]


def test_dual_qualify_drops_scrape_only_and_graph_only(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient(
        {"plays": [_Hit("plays")], "likes": [_Hit("likes")], "graphonly": [_Hit("graphonly")],
         "both": [_Hit("both")]},
        media_by_tag={
            "#plays": [_Media(1, "", play_count=9)],
            "#likes": [_Media(40, "")],
            "#graphonly": [],
            "#both": [_Media(1, "", play_count=3)],
        },
    )
    ensure_source_lock(cfg, _src(), client=client,
                       research_fn=lambda _s, _e: ["plays", "likes", "graphonly", "both"],
                       **_ok_graph(missing=("graphonly",)))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["pile"] == ["#plays", "#likes", "#graphonly", "#both"]
    assert rec["lock"] == ["#plays", "#likes", "#both"]


def test_second_call_is_noop_when_researched_at_present(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("music", id="1")]})
    calls = {"n": 0}

    def research(_s, _e):
        calls["n"] += 1
        return ["music"]

    ensure_source_lock(cfg, _src(), client=client, research_fn=research, **_ok_graph())
    first = json.loads(source_tag_locks_path(cfg).read_text())
    client.search_calls.clear()
    ensure_source_lock(cfg, _src(), client=client, research_fn=research, **_ok_graph())
    assert calls["n"] == 1
    assert client.search_calls == []
    assert json.loads(source_tag_locks_path(cfg).read_text()) == first


def test_scrape_unavailable_leaves_sidecar_absent(tmp_path):
    cfg = _cfg(tmp_path)
    seen = {"research": 0}

    def research(_s, _e):
        seen["research"] += 1
        return ["music"]

    def boom(_cfg):
        raise ScrapeUnavailable("no scrape session")

    ensure_source_lock(cfg, _src(), research_fn=research, open_client_fn=boom, **_ok_graph())
    assert seen["research"] == 0
    assert not source_tag_locks_path(cfg).exists()
    assert load_source_tag_locks(cfg) == {}
    assert SOURCE_TAG_LOCKS_NAME == "source_tag_locks.json"


def test_no_graph_leaves_sidecar_absent(tmp_path):
    cfg = _cfg(tmp_path)
    seen = {"research": 0}

    def research(_s, _e):
        seen["research"] += 1
        return ["music"]

    client = _SearchClient({"music": [_Hit("music")]})
    ensure_source_lock(cfg, _src(), client=client, research_fn=research)
    assert seen["research"] == 0
    assert not source_tag_locks_path(cfg).exists()
    assert client.search_calls == []


def test_injected_no_client_leaves_sidecar_absent(tmp_path):
    cfg = _cfg(tmp_path)
    ensure_source_lock(cfg, _src(), client=None, research_fn=lambda *_a: ["music"],
                       open_client_fn=lambda _cfg: None, **_ok_graph())
    assert not source_tag_locks_path(cfg).exists()


def test_research_empty_or_raise_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("music")]})

    def boom(_s, _e):
        raise RuntimeError("llm down")

    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: [], **_ok_graph())
    ensure_source_lock(cfg, _src(sid="src_2"), client=client, research_fn=boom, **_ok_graph())
    ensure_source_lock(cfg, _src(sid="src_3"), client=client, research_fn=lambda *_a: None,
                       **_ok_graph())
    assert not source_tag_locks_path(cfg).exists()
    assert client.search_calls == []


def test_caption_menu_is_not_80_pile():
    req = inspect.getsource(request_captions)
    assert "_per_account_hashtag_stores" not in req
    assert "content_tag_candidates" not in req
    regen = inspect.getsource(regenerate_caption)
    assert "_per_account_hashtag_stores" not in regen
    assert "content_tag_candidates" not in regen
    import fanops.source_tags as st
    assert "content_tag_candidates" not in inspect.getsource(st)
    assert "fail_open(\"source_tags.ensure\")" not in inspect.getsource(st)


def test_search_hashtags_scrape_maps_incomplete_hits():
    class _C:
        def search_hashtags(self, query):
            assert query == "music"
            return [_Hit("alpha"), _Hit("beta", id="1"), _Hit(None), _Hit("gamma", id="2", media_count=9)]

    rows = search_hashtags_scrape(_C(), "#Music")
    assert {r["name"] for r in rows} == {"alpha", "beta", "gamma"}
    alpha = next(r for r in rows if r["name"] == "alpha")
    assert "id" not in alpha and "media_count" not in alpha
    assert all("play_count" not in r for r in rows)


def test_search_hashtags_scrape_fail_open():
    class _Boom:
        def search_hashtags(self, query):
            raise RuntimeError("network")

    assert search_hashtags_scrape(_Boom(), "music") == []


def test_search_hashtags_scrape_reraises_login_required():
    from instagrapi.exceptions import LoginRequired

    class _Dead:
        def search_hashtags(self, query):
            raise LoginRequired("login_required")

    try:
        search_hashtags_scrape(_Dead(), "music")
    except LoginRequired:
        return
    assert False, "LoginRequired must not fail-open to []"


def test_search_hashtags_scrape_reraises_challenge_subclass():
    from fanops.ig_hashtag_scrape import scrape_session_dead, search_hashtags_scrape

    class ChallengeError(Exception):
        pass

    class ChallengeRedirection(ChallengeError):
        pass

    assert scrape_session_dead(ChallengeRedirection("redir"))

    class _Dead:
        def search_hashtags(self, query):
            raise ChallengeRedirection("redir")

    try:
        search_hashtags_scrape(_Dead(), "music")
    except ChallengeRedirection:
        return
    assert False, "Challenge subclass must not fail-open to []"


def test_ensure_source_lock_rotates_off_dead_session(tmp_path, monkeypatch):
    """A LoginRequired dump must not persist an empty lock — try the next client."""
    from instagrapi.exceptions import LoginRequired
    from fanops.ig_hashtag_scrape import scrape_session_path

    cfg = _cfg(tmp_path)
    live = _SearchClient({"music": [_Hit("music", id="1", media_count=2)]},
                         media_by_tag={"#music": [_Media(1, "", play_count=8)]})

    class _Dead:
        def search_hashtags(self, query):
            raise LoginRequired("login_required")

    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        return _Dead() if user == "a" else live

    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    for u in ("a", "b"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: ["music"], open_client_fn=opener,
                       **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["pile"] == ["#music"]
    assert rec["lock"] == ["#music"]
    assert seen == ["a", "b"]


def test_ensure_source_lock_all_dead_writes_nothing(tmp_path, monkeypatch):
    from instagrapi.exceptions import LoginRequired
    from fanops.ig_hashtag_scrape import scrape_session_path

    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    for u in ("a", "b"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")

    class _Dead:
        def search_hashtags(self, query):
            raise LoginRequired("login_required")

    def opener(_cfg, user=None):
        return _Dead()

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: ["music"], open_client_fn=opener,
                       **_ok_graph())
    assert not source_tag_locks_path(cfg).exists()


def test_graph_refused_writes_nothing(tmp_path):
    from fanops.meta_graph import GraphRefused
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})

    def resolve(_cfg, tag):
        raise GraphRefused("ig_hashtag_search", message="app not approved")

    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: ["music"],
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    assert not source_tag_locks_path(cfg).exists()
