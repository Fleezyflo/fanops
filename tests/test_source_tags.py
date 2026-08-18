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


def _freeze_accounts(cfg, accounts: dict) -> None:
    from fanops.controlio import write_json_atomic
    from fanops.fanops_hashtags import _cooldown_path
    write_json_atomic(_cooldown_path(cfg), {"accounts": accounts})


def test_lock_walk_includes_loginrequired_freeze_excludes_throttle(tmp_path, monkeypatch):
    """Lock walk retries a LoginRequired dump; throttle / checkpoint stay skipped."""
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import _healthy_scrape_users
    from fanops.ig_hashtag_scrape import scrape_session_path
    from fanops.source_tags import _iter_lock_clients, _lock_walk_users

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
    assert _lock_walk_users(cfg, now) == ["mark", "wolf"]
    assert _healthy_scrape_users(cfg, now) == []
    assert _healthy_scrape_users(cfg, now, require_budget_room=False) == []
    seen = []

    def opener(_cfg, user=None):
        seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    opened = [getattr(c, "_fanops_scrape_user", None)
              for c in _iter_lock_clients(cfg, client=None, open_client_fn=opener)]
    assert opened == ["mark", "wolf"]
    assert seen == ["mark", "wolf"]


def test_lock_walk_loginrequired_freeze_uses_chrome_open(tmp_path, monkeypatch):
    """Lock walk open of a LoginRequired-frozen dump uses unattended Chrome inject (#1023)."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    from fanops.source_tags import _iter_lock_clients
    from instagrapi.exceptions import LoginRequired as _LR

    cfg = _cfg(tmp_path)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    monkeypatch.setattr(igs, "_browser_sessionid_for", lambda _ds: "chrome-sid")

    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")

    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    sess = scrape_session_path(cfg, "mark")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    _freeze_accounts(cfg, {
        "mark": {"until": "2099-01-01T00:00:00+00:00", "streak": 1,
                 "reason": "LoginRequired"},
    })
    seen = {"search": 0, "login": 0, "account_info": 0, "users": []}

    class _Jar:
        def set(self, *a, **k):
            seen.setdefault("cookies", []).append(a[0] if a else None)

        def clear(self):
            seen["cleared"] = True

    class _StaleThenLive:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1", "sessionid": "old"}
            self.private = SimpleNamespace(cookies=_Jar(), headers={})

        def load_settings(self, _p):
            pass

        def search_hashtags(self, _q):
            seen["search"] += 1
            if seen["search"] == 1:
                raise _LR("login_required")

        def account_info(self):
            seen["account_info"] += 1
            raise AssertionError("unattended must not probe account_info")

        def login(self, *_a, **_k):
            seen["login"] += 1

        def dump_settings(self, _p):
            seen["dump"] = True

        def inject_sessionid_to_public(self):
            seen["injected"] = True

    def opener(_cfg, user=None):
        seen["users"].append(user)
        return open_client(_cfg, user=user, client_factory=_StaleThenLive)

    clients = list(_iter_lock_clients(cfg, client=None, open_client_fn=opener))
    assert seen["users"] == ["mark"]
    assert len(clients) == 1
    assert seen["search"] == 2
    assert seen["login"] == 0
    assert seen["account_info"] == 0
    assert seen.get("injected") is True
    assert seen.get("dump") is True
    assert clients[0].authorization_data.get("sessionid") == "chrome-sid"
    assert getattr(clients[0], "_fanops_scrape_user", None) == "mark"


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
    lock_ready_sources(cfg, client=_SearchClient({"music": [_Hit("music")]}),
                       research_fn=lambda *_a: ["music"], **_ok_graph())
    assert not source_tag_locks_path(cfg).exists()
    assert "no_transcript" in cfg.log_path.read_text()


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
    assert not source_tag_locks_path(cfg).exists()
