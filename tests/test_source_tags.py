# tests/test_source_tags.py
"""HV1-SCRAPE-COMPLETE: Safari scrape completes the lock; Graph ranks, never vetoes."""
from __future__ import annotations
import inspect
import json
from types import SimpleNamespace

from fanops.caption import request_captions
from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, search_hashtags_scrape
from fanops.source_tags import (SOURCE_TAG_LOCKS_NAME, ensure_source_lock, load_source_tag_locks,
                                lock_ready_sources, source_tag_locks_path)
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
    """Graph absence must not withhold researched_at after scrape finishes."""
    cfg = _cfg(tmp_path)
    seen = {"research": 0}

    def research(_s, _e):
        seen["research"] += 1
        return ["music"]

    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})
    ensure_source_lock(cfg, _src(), client=client, research_fn=research)
    assert seen["research"] == 1
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#music"]
    assert rec["pile"] == ["#music"]


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


def test_ingest_141_request_12_sidecar_ships_subset(tmp_path):
    from fanops.agentstep import latest_request_id, request_path, response_path
    from fanops.caption import ingest_captions
    from fanops.ledger import Ledger
    from fanops.models import CaptionItem, CaptionSet, Clip, ClipState, Moment, MomentState, Platform, Source
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    lock = [f"#lock{i:02d}" for i in range(12)]
    pile = [f"#req{i:03d}" for i in range(141)]
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_1": {"pile": pile, "lock": lock, "researched_at": "2026-08-18T00:00:00Z"},
    }))
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    req_path = request_path(cfg, "captions", "clip_1")
    req = json.loads(req_path.read_text())
    req["surfaces"][0]["hashtag_store"] = pile
    req_path.write_text(json.dumps(req))
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x",
                    hashtags=lock[:6] + pile[:8])]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    shipped = led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"]
    assert shipped == lock[:4]
    assert set(shipped) <= set(lock)
    assert not any(t.startswith("#req") for t in shipped)


def test_caption_menu_is_not_80_pile():
    from fanops.caption import ingest_captions
    req = inspect.getsource(request_captions)
    assert "_per_account_hashtag_stores" not in req
    assert "content_tag_candidates" not in req
    assert "ensure_source_lock" not in req
    regen = inspect.getsource(regenerate_caption)
    assert "_per_account_hashtag_stores" not in regen
    assert "content_tag_candidates" not in regen
    assert "ensure_source_lock" not in regen
    assert "ship_from_lock" in inspect.getsource(ingest_captions)
    assert "ship_from_lock" in regen
    assert "vet_hashtags_traced" not in inspect.getsource(ingest_captions)
    assert "vet_hashtags_traced" not in regen
    import fanops.source_tags as st
    st_src = inspect.getsource(st)
    assert "content_tag_candidates" not in st_src
    assert "_aligned_pool" not in st_src
    assert "_per_account_hashtag_stores" not in st_src
    assert "fail_open(\"source_tags.ensure\")" not in st_src
    assert "sleep" not in st_src
    assert "_SCRAPE_DAY_BUDGET" not in st_src


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


def test_ensure_source_lock_login_required_stops_the_tick(tmp_path, monkeypatch):
    """LoginRequired freezes that user and ends the walk. Same-tick rotate onto the
    next account is the burst instagrapi says to stop."""
    from instagrapi.exceptions import LoginRequired
    from fanops.ig_hashtag_scrape import scrape_session_path

    cfg = _cfg(tmp_path)

    class _Dead:
        def search_hashtags(self, query):
            raise LoginRequired("login_required")

    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        return _Dead()

    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b")
    for u in ("a", "b"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: ["music"], open_client_fn=opener,
                       **_ok_graph())
    assert not source_tag_locks_path(cfg).exists()
    assert seen == ["a"]


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


def _freeze_accounts(cfg, accounts: dict) -> None:
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import _cooldown_path
    write_json_atomic(_cooldown_path(cfg), {"accounts": accounts})


def test_lock_walk_skips_loginrequired_freeze(tmp_path, monkeypatch):
    """Lock walk uses healthy peers only — a LoginRequired freeze is not retried."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import _healthy_scrape_users
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.source_tags import _iter_lock_clients

    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,cisum,perca.late,wolf,ghost")
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    until = "2099-01-01T00:00:00+00:00"
    for u in ("mark", "cisum", "perca.late", "wolf"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    _freeze_accounts(cfg, {
        "mark": {"until": until, "streak": 1, "reason": "LoginRequired"},
        "cisum": {"until": until, "streak": 2, "reason": "throttle"},
        "perca.late": {"until": until, "streak": 1, "reason": "checkpoint"},
        "wolf": {"until": until, "streak": 1, "reason": "ClientLoginRequired"},
        "ghost": {"until": until, "streak": 1, "reason": "ClientLoginRequired"},
    })
    assert _healthy_scrape_users(cfg, now) == []
    assert _healthy_scrape_users(cfg, now, require_budget_room=False) == []
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise ScrapeUnavailable("all frozen")

    opened = [getattr(c, "_fanops_scrape_user", None)
              for c in _iter_lock_clients(cfg, client=None, open_client_fn=opener)]
    assert opened == []
    assert seen == []  # empty blocked list does not fall through to opener(cfg)


def test_lock_walk_unfrozen_peer_not_loginrequired_freeze(tmp_path, monkeypatch):
    """One unfrozen session is walked; a LoginRequired-frozen peer is not."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import _healthy_scrape_users
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.source_tags import _iter_lock_clients

    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,cisum")
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    for u in ("mark", "cisum"):
        p = scrape_session_path(cfg, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    _freeze_accounts(cfg, {
        "mark": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                 "reason": "LoginRequired"},
    })
    assert _healthy_scrape_users(cfg, now, require_budget_room=False) == ["cisum"]
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    opened = [getattr(c, "_fanops_scrape_user", None)
              for c in _iter_lock_clients(cfg, client=None, open_client_fn=opener)]
    assert opened == ["cisum"]
    assert seen == ["cisum"]


def _write_whisper(cfg, stem, text="hello world"):
    p = cfg.agent_io / "transcripts"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{stem}.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 0, "end": 2, "text": text}],
    }))


def test_lock_ready_sources_no_whisper_errors_no_stamp(tmp_path):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.add_source(Source(id="src_2", source_path=str(tmp_path / "b.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "b")
    lock_ready_sources(cfg, client=_SearchClient({"music": [_Hit("music")]},
                                                 media_by_tag={"#music": [_Media(1, "", play_count=8)]}),
                       research_fn=lambda *_a: ["music"], **_ok_graph())
    table = load_source_tag_locks(cfg)
    assert "src_1" not in table
    assert "src_2" in table
    assert table["src_2"]["researched_at"]
    assert "no_transcript" in cfg.log_path.read_text()


def test_lock_ready_no_seat_opens_safari_once_not_per_source(tmp_path, monkeypatch):
    """No Instagram seat: probe users once, log no_scrape once, do not walk every source.

    Live 2026-08-19: lock_ready_sources logged no_scrape on ~30 unfinished sources in 37s.
    Each source called open_web_session → #music XHR. That is what logged the Safari
    sessions out. A missing seat is one tick fact, not N probes."""
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,wolf")
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    for i, stem in enumerate(("a", "b", "c"), start=1):
        led.add_source(Source(id=f"src_{i}", source_path=str(tmp_path / f"{stem}.mp4"),
                              state=SourceState.catalogued))
        _write_whisper(cfg, stem)
    led.save()
    seen: list[str | None] = []

    def opener(_cfg, user=None, **_k):
        seen.append(user)
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")

    lock_ready_sources(cfg, open_client_fn=opener,
                       research_fn=lambda *_a: (_ for _ in ()).throw(AssertionError("no LLM without a seat")))
    assert seen == ["mark", "wolf"]


def test_lock_ready_sources_at_most_one(tmp_path):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.add_source(Source(id="src_2", source_path=str(tmp_path / "b.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "a")
    _write_whisper(cfg, "b")
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})
    lock_ready_sources(cfg, client=client, research_fn=lambda *_a: ["music"], **_ok_graph())
    table = load_source_tag_locks(cfg)
    assert set(table) == {"src_1"}
    assert table["src_1"]["researched_at"]
    lock_ready_sources(cfg, client=client, research_fn=lambda *_a: ["music"], **_ok_graph())
    table = load_source_tag_locks(cfg)
    assert set(table) == {"src_1", "src_2"}


def test_lock_ready_sources_skips_pending_without_error_loop(tmp_path):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_p", source_path=str(tmp_path / "p.mp4"),
                          state=SourceState.pending))
    led.add_source(Source(id="src_1", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "a")
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})
    lock_ready_sources(cfg, client=client, research_fn=lambda *_a: ["music"], **_ok_graph())
    table = load_source_tag_locks(cfg)
    assert "src_p" not in table and "src_1" in table
    assert "src_p" not in cfg.log_path.read_text()


def test_advance_calls_lock_ready_after_produce():
    from fanops.pipeline import advance
    src = inspect.getsource(advance)
    assert src.index("produce.run_all") < src.index("lock_ready_sources")


def test_graph_refused_writes_nothing(tmp_path):
    from fanops.meta_graph import GraphRefused
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})

    def resolve(_cfg, tag):
        raise GraphRefused("ig_hashtag_search", message="app not approved")

    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: ["music"],
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#music"]


def _count_graph():
    resolves: list[str] = []
    measures: list[str] = []

    def resolve(_cfg, tag):
        resolves.append(tag)
        n = tag if str(tag).startswith("#") else f"#{tag}"
        return f"gid-{n}"

    def measure(_cfg, hid):
        measures.append(hid)
        return 10.0, {}

    return resolves, measures, {"resolve_fn": resolve, "measure_fn": measure}


def test_three_verified_names_six_graph_http(tmp_path):
    cfg = _cfg(tmp_path)
    names = ["a", "b", "c"]
    client = _SearchClient({n: [_Hit(n)] for n in names},
                           media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names})
    resolves, measures, graph = _count_graph()
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: names, **graph)
    assert len(resolves) == 3 and len(measures) == 3
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["lock"] == ["#a", "#b", "#c"]
    assert rec["researched_at"]


def test_stop_graph_at_fifteen_dual_qualify(tmp_path):
    cfg = _cfg(tmp_path)
    names = [f"t{i}" for i in range(16)]
    client = _SearchClient(
        {n: [_Hit(n)] for n in names},
        media_by_tag={f"#{n}": [_Media(1, "", play_count=i + 1)] for i, n in enumerate(names)},
    )
    resolves, measures, graph = _count_graph()
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: names, **graph)
    assert [_norm_tag(t) for t in resolves] == [f"#t{i}" for i in range(15)]
    assert len(measures) == 15
    assert load_source_tag_locks(cfg)["src_1"]["lock"] == [f"#t{i}" for i in range(15)]


def _norm_tag(tag):
    from fanops.hashtags import _norm
    return _norm(tag)


def test_second_source_overlapping_tags_zero_extra_search(tmp_path):
    cfg = _cfg(tmp_path)
    names = ["a", "b", "c"]
    media = {f"#{n}": [_Media(1, "", play_count=8)] for n in names}
    client = _SearchClient({n: [_Hit(n)] for n in names}, media_by_tag=media)
    resolves, measures, graph = _count_graph()
    ensure_source_lock(cfg, _src(sid="src_1"), client=client, research_fn=lambda *_a: names, **graph)
    n_search, n_measure = len(resolves), len(measures)
    assert n_search == 3
    ensure_source_lock(cfg, _src(sid="src_2"), client=client, research_fn=lambda *_a: names, **graph)
    assert len(resolves) == n_search
    assert len(measures) == n_measure
    rec = load_source_tag_locks(cfg)["src_2"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#a", "#b", "#c"]


def test_code_18_mid_pile_keeps_in_progress_no_rellm(tmp_path):
    from fanops.meta_graph import GraphQuotaExhausted
    cfg = _cfg(tmp_path)
    names = ["a", "b", "c"]
    client = _SearchClient({n: [_Hit(n)] for n in names},
                           media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names})
    calls = {"n": 0}
    seen = []

    def research(_s, _e):
        calls["n"] += 1
        return names

    def resolve(_c, tag):
        seen.append(tag)
        from fanops.hashtags import _norm
        if _norm(tag) == "#c":
            raise GraphQuotaExhausted("ig_hashtag_search", code=18, subcode=2207034,
                                      message="resource limits")
        return f"gid-{tag}"

    ensure_source_lock(cfg, _src(), client=client, research_fn=research,
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["pile"] == ["#a", "#b", "#c"]
    assert rec["lock"] == ["#a", "#b", "#c"]
    assert calls["n"] == 1
    first_seen = list(seen)
    ensure_source_lock(cfg, _src(), client=client, research_fn=research,
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    assert calls["n"] == 1
    assert seen == first_seen
    rec2 = load_source_tag_locks(cfg)["src_1"]
    assert rec2["researched_at"] == rec["researched_at"]


def test_lock_ready_after_quota_on_a_tries_b(tmp_path):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    from fanops.meta_graph import GraphQuotaExhausted
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_a", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.add_source(Source(id="src_b", source_path=str(tmp_path / "b.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "a")
    _write_whisper(cfg, "b")
    names_a = ["a1", "a2", "a3"]
    names_b = ["a1", "a2"]
    client = _SearchClient({n: [_Hit(n)] for n in names_a},
                           media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names_a})
    attempted = []

    def research(source, _e):
        attempted.append(source.id)
        return names_a if source.id == "src_a" else names_b

    def resolve(_c, tag):
        from fanops.hashtags import _norm
        if _norm(tag) == "#a3":
            raise GraphQuotaExhausted("ig_hashtag_search", code=18, subcode=2207034,
                                      message="resource limits")
        return f"gid-{tag}"

    lock_ready_sources(cfg, client=client, research_fn=research, resolve_fn=resolve,
                       measure_fn=lambda *_a: (10.0, {}))
    assert attempted == ["src_a"]
    rec_a = load_source_tag_locks(cfg)["src_a"]
    assert rec_a["researched_at"]
    assert rec_a["lock"] == ["#a1", "#a2", "#a3"]
    lock_ready_sources(cfg, client=client, research_fn=research, resolve_fn=resolve,
                       measure_fn=lambda *_a: (10.0, {}))
    assert attempted == ["src_a", "src_b"]
    table = load_source_tag_locks(cfg)
    assert table["src_a"]["researched_at"]
    assert table["src_b"]["researched_at"]
    assert table["src_b"]["lock"] == ["#a1", "#a2"]


def test_scrape_only_writes_researched_at_and_lock(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: ["music"])
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#music"]
    assert rec["pile"] == ["#music"]


def test_graph_quota_after_scrape_still_stamps(tmp_path):
    from fanops.meta_graph import GraphQuotaExhausted
    cfg = _cfg(tmp_path)
    names = ["a", "b"]
    client = _SearchClient({n: [_Hit(n)] for n in names},
                           media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names})

    def resolve(_c, tag):
        raise GraphQuotaExhausted("ig_hashtag_search", code=18, subcode=2207034,
                                  message="resource limits")

    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: names,
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#a", "#b"]


def _leftover_quota_sidecar(cfg, sid="src_1", names=("#a", "#b"), measured=None):
    names = list(names)
    measured = list(names) if measured is None else list(measured)
    source_tag_locks_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    source_tag_locks_path(cfg).write_text(json.dumps({
        sid: {
            "pile": names,
            "verified": names,
            "measurements": {n: {"play_count": 10.0} for n in measured},
            "remaining": names,
            "lock": [],
            "quota_exhausted_at": "2026-08-19T13:15:49.118016Z",
        }
    }))


def test_leftover_quota_row_stamps_without_safari_or_graph(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    _freeze_accounts(cfg, {"u": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                                 "reason": "LoginRequired"}})
    _leftover_quota_sidecar(cfg)
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise AssertionError("finished scrape must not open Safari")

    def resolve(_c, tag):
        raise AssertionError("Graph must not gate leftover lock")

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: (_ for _ in ()).throw(
        AssertionError("must not re-LLM a leftover pile")), open_client_fn=opener,
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#a", "#b"]
    assert "quota_exhausted_at" not in rec
    assert seen == []


def test_leftover_incomplete_without_safari_does_not_stamp(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    _freeze_accounts(cfg, {"u": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                                 "reason": "LoginRequired"}})
    _leftover_quota_sidecar(cfg, names=("#a", "#b"), measured=("#a",))
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: ["a", "b"], open_client_fn=opener,
                       **_ok_graph())
    rec = load_source_tag_locks(cfg).get("src_1") or {}
    assert not rec.get("researched_at")
    assert rec.get("quota_exhausted_at")
    assert seen == []


def test_lock_ready_stamps_leftover_quota_row_before_next_source(tmp_path):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_a", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.add_source(Source(id="src_b", source_path=str(tmp_path / "b.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "a")
    _write_whisper(cfg, "b")
    _leftover_quota_sidecar(cfg, sid="src_a", names=("#a",))
    attempted = []

    def research(source, _e):
        attempted.append(source.id)
        return ["music"]

    def resolve(_c, tag):
        raise AssertionError("Graph must not skip leftover A")

    lock_ready_sources(cfg, client=_SearchClient({"music": [_Hit("music")]},
                                                 media_by_tag={"#music": [_Media(1, "", play_count=8)]}),
                       research_fn=research, resolve_fn=resolve,
                       measure_fn=lambda *_a: (10.0, {}))
    table = load_source_tag_locks(cfg)
    assert table["src_a"]["researched_at"]
    assert table["src_a"]["lock"] == ["#a"]
    assert "src_b" not in table
    assert attempted == []


def test_lock_ready_skips_unfinished_leftover_to_stamp_complete(tmp_path, monkeypatch):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    _freeze_accounts(cfg, {"u": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                                 "reason": "LoginRequired"}})
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_a", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.catalogued))
    led.add_source(Source(id="src_b", source_path=str(tmp_path / "b.mp4"),
                          state=SourceState.catalogued))
    led.save()
    _write_whisper(cfg, "a")
    _write_whisper(cfg, "b")
    source_tag_locks_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    source_tag_locks_path(cfg).write_text(json.dumps({
        "src_a": {
            "pile": ["#t0", "#t1", "#t2"],
            "verified": ["#t0"],
            "measurements": {"#t0": {"play_count": 8.0}},
            "remaining": ["#t1", "#t2"],
            "lock": [],
        },
        "src_b": {
            "pile": ["#b"],
            "verified": ["#b"],
            "measurements": {"#b": {"play_count": 10.0}},
            "remaining": ["#b"],
            "lock": [],
            "quota_exhausted_at": "2026-08-19T13:15:49.118016Z",
        },
    }))
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise AssertionError("no safari")

    lock_ready_sources(cfg, research_fn=lambda *_a: (_ for _ in ()).throw(AssertionError("llm")),
                       open_client_fn=opener,
                       resolve_fn=lambda *_a: (_ for _ in ()).throw(AssertionError("graph")),
                       measure_fn=lambda *_a: (10.0, {}))
    table = load_source_tag_locks(cfg)
    assert not table["src_a"].get("researched_at")
    assert table["src_b"]["researched_at"]
    assert table["src_b"]["lock"] == ["#b"]
    assert seen == []


def test_empty_scrape_pass_stamps_empty_lock(tmp_path):
    cfg = _cfg(tmp_path)
    client = _SearchClient({"music": [_Hit("incomplete"), _Hit("full")]})
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: ["music"])
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == []
    assert rec["pile"] == ["#music"]


def test_graph_refused_does_not_wipe_scrape_admits(tmp_path):
    from fanops.meta_graph import GraphRefused
    cfg = _cfg(tmp_path)
    client = _SearchClient(
        {"plays": [_Hit("plays")], "likes": [_Hit("likes")]},
        media_by_tag={
            "#plays": [_Media(1, "", play_count=9)],
            "#likes": [_Media(40, "")],
        },
    )

    def resolve(_cfg, tag):
        raise GraphRefused("ig_hashtag_search", message="app not approved")

    ensure_source_lock(cfg, _src(), client=client,
                       research_fn=lambda *_a: ["plays", "likes"],
                       resolve_fn=resolve, measure_fn=lambda *_a: (10.0, {}))
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#plays", "#likes"]


def test_request_captions_noops_without_researched_at(tmp_path):
    from fanops.agentstep import request_path
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState, Moment, MomentState, Platform, Source
    cfg = _cfg(tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    assert not request_path(cfg, "captions", "clip_1").exists()
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_1": {"pile": ["#x"], "lock": [], "researched_at": "2026-08-18T00:00:00Z"},
    }))
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    assert request_path(cfg, "captions", "clip_1").exists()


def _cooldown_used(cfg, user):
    from fanops.fanops_hashtags import _cooldown_path
    p = _cooldown_path(cfg)
    if not p.exists():
        return 0
    raw = json.loads(p.read_text())
    rec = (raw.get("accounts") or {}).get(user) or {}
    return int(rec.get("used") or 0)


def test_injected_client_still_finishes_the_pile(tmp_path, monkeypatch):
    """Tests/manual inject a client — finish the pile. Tick (client is None) is one tag."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    client = _SearchClient({"music": [_Hit("music")]},
                           media_by_tag={"#music": [_Media(1, "", play_count=8)]})
    client._fanops_scrape_user = "u"
    ensure_source_lock(cfg, _src(), client=client, research_fn=lambda *_a: ["music"],
                       **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#music"]
    assert _cooldown_used(cfg, "u") == 0


def test_unattended_lock_walks_one_tag(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    names = ["t0", "t1", "t2"]
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        cli = _SearchClient({n: [_Hit(n)] for n in names},
                            media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names})
        cli._fanops_scrape_user = user
        return cli

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: names, open_client_fn=opener,
                       **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    assert not rec.get("researched_at")
    assert rec.get("verified") == ["#t0"]
    assert rec.get("remaining") == ["#t1", "#t2"]
    assert seen == ["u"]
    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: names, open_client_fn=opener,
                       **_ok_graph())
    rec2 = load_source_tag_locks(cfg)["src_1"]
    assert rec2.get("verified") == ["#t0", "#t1"]
    assert not rec2.get("researched_at")
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines() if line.strip()]
    unfinished = [r for r in recs if r.get("outcome") == "scrape_unfinished"]
    assert unfinished
    assert all(r.get("level") != "error" for r in unfinished)
    assert not any(r.get("err") == "scrape_unfinished" for r in recs)


def test_all_peers_at_cap_skips_stamp(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP", "1")
    names = ["t0", "t1", "t2"]
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        cli = _SearchClient({n: [_Hit(n)] for n in names},
                            media_by_tag={f"#{n}": [_Media(1, "", play_count=8)] for n in names})
        cli._fanops_scrape_user = user
        return cli

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: names, open_client_fn=opener,
                       **_ok_graph())
    rec = load_source_tag_locks(cfg).get("src_1") or {}
    assert not rec.get("researched_at")
    assert rec.get("verified") == ["#t0"]
    assert rec.get("remaining") == ["#t1", "#t2"]
    assert seen == ["u"]
    _freeze_accounts(cfg, {"u": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                                 "reason": "LoginRequired"}})
    seen.clear()

    def dead(_cfg, user=None):
        seen.append(user)
        raise AssertionError("unfinished scrape must not stamp without a seat")

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: names, open_client_fn=dead,
                       **_ok_graph())
    rec2 = load_source_tag_locks(cfg)["src_1"]
    assert not rec2.get("researched_at")
    assert rec2.get("remaining") == ["#t1", "#t2"]
    assert seen == []


def _write_meas(cfg, tags):
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        t: {"graph_id": f"g-{t}", "measured_at": "2026-08-01T00:00:00Z",
            "play_count": play, "like_count": 2.0}
        for t, play in tags.items()
    }))


def _seed_source_with_tags(cfg, sid, tags, *, title="a session"):
    from fanops.ledger import Ledger
    from fanops.models import (Clip, ClipState, Moment, MomentState, Platform, Post,
                               Source, SourceState)
    led = Ledger.load(cfg)
    led.add_source(Source(id=sid, source_path=str(cfg.root / f"{sid}.mp4"),
                          title=title, state=SourceState.catalogued))
    led.add_moment(Moment(id=f"mom_{sid}", parent_id=sid, content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    led.add_clip(Clip(id=f"clip_{sid}", parent_id=f"mom_{sid}", path="/c.mp4",
                      state=ClipState.rendered,
                      meta_captions={"a/instagram": {"caption": "x", "hashtags": list(tags)}}))
    led.add_post(Post(id=f"post_{sid}", parent_id=f"clip_{sid}", account="a",
                      account_id="id", platform=Platform.instagram, caption="x",
                      hashtags=list(tags)))
    led.save()
    return Ledger.load(cfg)


def test_hydrate_used_measured_stamps_without_safari(tmp_path):
    from fanops.caption import posted_text_for
    from fanops.ledger import Ledger
    from fanops.source_tags import hydrate_locks_from_known
    cfg = _cfg(tmp_path)
    _write_meas(cfg, {"#hiphop": 90.0, "#lyrics": 50.0, "#rap": 999.0})
    led = _seed_source_with_tags(cfg, "src_1", ["#lyrics", "#hiphop", "#storeonly"])
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise AssertionError("used∩measured must not open Safari")

    n = hydrate_locks_from_known(cfg, led)
    rec = load_source_tag_locks(cfg)["src_1"]
    assert n == 1
    assert rec["researched_at"]
    assert rec["lock"][0] == "#hiphop"
    assert "#lyrics" in rec["lock"]
    assert "#storeonly" in rec["lock"]
    assert "#rap" not in rec["lock"]
    lock_ready_sources(cfg, open_client_fn=opener, research_fn=lambda *_a: ["music"])
    assert seen == []
    post = Ledger.load(cfg).posts["post_src_1"]
    text = posted_text_for(cfg, Ledger.load(cfg), post)
    assert "#hiphop" in text and "#lyrics" in text and "#storeonly" in text


def test_hydrate_store_tag_not_on_source_stays_out(tmp_path):
    from fanops.source_tags import hydrate_locks_from_known
    cfg = _cfg(tmp_path)
    _write_meas(cfg, {"#hiphop": 90.0, "#rap": 999.0})
    led = _seed_source_with_tags(cfg, "src_1", ["#hiphop"])
    hydrate_locks_from_known(cfg, led)
    assert load_source_tag_locks(cfg)["src_1"]["lock"] == ["#hiphop"]


def test_hydrate_merges_used_into_completed_lock(tmp_path):
    from fanops.source_tags import hydrate_locks_from_known
    cfg = _cfg(tmp_path)
    _write_meas(cfg, {"#hiphop": 90.0})
    led = _seed_source_with_tags(cfg, "src_1", ["#hiphop"])
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_1": {"pile": ["#jussiesmollett"], "lock": ["#jussiesmollett"],
                  "researched_at": "2026-08-19T00:00:00Z"},
    }))
    hydrate_locks_from_known(cfg, led)
    lock = load_source_tag_locks(cfg)["src_1"]["lock"]
    assert lock[0] == "#hiphop"
    assert "#jussiesmollett" in lock


def test_pile_cache_hit_stamps_without_safari(tmp_path):
    cfg = _cfg(tmp_path)
    source_tag_locks_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    source_tag_locks_path(cfg).write_text(json.dumps({
        "src_1": {"pile": ["#music"], "verified": [], "remaining": ["#music"], "lock": []},
    }))
    _write_meas(cfg, {"#music": 8.0})
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise AssertionError("cache hit must not open Safari")

    ensure_source_lock(cfg, _src(), research_fn=lambda *_a: (_ for _ in ()).throw(
        AssertionError("must not re-LLM")), open_client_fn=opener, **_ok_graph())
    rec = load_source_tag_locks(cfg)["src_1"]
    assert rec["researched_at"]
    assert rec["lock"] == ["#music"]
    assert seen == []


def test_lock_ready_hydrates_every_used_source_in_one_tick(tmp_path):
    from fanops.source_tags import hydrate_locks_from_known
    cfg = _cfg(tmp_path)
    _write_meas(cfg, {"#hiphop": 90.0, "#lyrics": 50.0})
    _seed_source_with_tags(cfg, "src_a", ["#hiphop"])
    _seed_source_with_tags(cfg, "src_b", ["#lyrics"])
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        raise AssertionError("hydrate must stamp both without Safari")

    lock_ready_sources(cfg, open_client_fn=opener, research_fn=lambda *_a: ["music"])
    table = load_source_tag_locks(cfg)
    assert table["src_a"]["lock"] == ["#hiphop"]
    assert table["src_b"]["lock"] == ["#lyrics"]
    assert table["src_a"]["researched_at"] and table["src_b"]["researched_at"]
    assert seen == []
    assert hydrate_locks_from_known(cfg, __import__("fanops.ledger", fromlist=["Ledger"]).Ledger.load(cfg)) == 0
