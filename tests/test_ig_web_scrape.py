"""Lock scrape fetches inside Safari, never Google Chrome."""
import json
from types import SimpleNamespace

from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, measure_and_harvest_scrape, search_hashtags_scrape
from fanops.ig_web_scrape import IgWebSession, LoginRequired, open_web_session
from fanops.source_tags import _iter_lock_clients


def test_web_search_exact_name():
    def fetch(method, url, body=None):
        assert method == "GET"
        assert "/tags/music/info/" in url
        return {"name": "music", "id": "9", "media_count": 3, "status": "ok"}
    sess = IgWebSession("u", fetch=fetch)
    hits = search_hashtags_scrape(sess, "music")
    assert [h["name"] for h in hits] == ["music"]


def test_web_search_invented_name_is_empty():
    sess = IgWebSession("u", fetch=lambda *_a, **_k: {"name": "nope", "media_count": 0, "status": "ok"})
    assert search_hashtags_scrape(sess, "nope") == []


def test_web_403_is_login_required():
    def fetch(method, url, body=None):
        raise LoginRequired("web 403")
    sess = IgWebSession("u", fetch=fetch)
    try:
        sess.search_hashtags("music")
        raise AssertionError("expected LoginRequired")
    except LoginRequired:
        pass


def test_web_measure_reads_play_and_like():
    payload = {
        "sections": [{
            "layout_content": {
                "medias": [{
                    "media": {
                        "pk": "1",
                        "like_count": 10,
                        "play_count": 100,
                        "product_type": "clips",
                        "taken_at": 1_700_000_000,
                        "caption": {"text": "#music #live"},
                    }
                }]
            }
        }]
    }
    sess = IgWebSession("u", fetch=lambda *_a, **_k: payload)
    metrics, cotags = measure_and_harvest_scrape(sess, "#music")
    assert metrics is not None
    assert metrics["like_count"] == 10
    assert metrics["play_count"] == 100
    assert "#music" in cotags or "#live" in cotags


def test_safari_logged_in_does_not_hit_tags_api(monkeypatch):
    """Login check is the existing tab (login URL / login form). #music/info is a
    private API call and was fired once per unfinished source — that logged
    the accounts out. sessionid is HttpOnly so document.cookie is not a signal."""
    import fanops.ig_hashtag_scrape as igs
    import fanops.ig_web_scrape as iws
    seen = []

    def ev(expr, user=None):
        seen.append(expr)
        assert "/tags/" not in expr
        assert "music" not in expr
        assert "sessionid" not in expr
        return "ok"

    monkeypatch.setattr(igs, "safari_eval", ev)
    assert iws.safari_logged_in("markmakmouly") is True
    assert seen and all("/tags/" not in e for e in seen)
    monkeypatch.setattr(igs, "safari_eval", lambda *_a, **_k: "login")
    assert iws.safari_logged_in("markmakmouly") is False


def test_ensure_scrape_safari_unattended_does_not_navigate(tmp_path, monkeypatch):
    """Unattended tick must not activate Safari or reload instagram.com."""
    import fanops.ig_hashtag_scrape as igs
    opened = []
    monkeypatch.setattr(igs, "_enable_safari_apple_events", lambda: None)
    monkeypatch.setattr(igs, "stop_scrape_chrome", lambda *_a, **_k: None)
    monkeypatch.setattr(igs, "scrape_users", lambda _cfg: ["u"])
    monkeypatch.setattr(igs, "safari_eval",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no instagram tab")))
    monkeypatch.setattr(igs, "safari_open_instagram", lambda u: opened.append(u))
    cfg = Config(root=tmp_path)
    assert igs.ensure_scrape_safari(cfg, "u", navigate=False) is False
    assert opened == []


def test_open_web_session_refuses_without_safari(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setattr(igs, "ensure_scrape_safari", lambda *_a, **_k: False)
    cfg = Config(root=tmp_path)
    try:
        open_web_session(cfg, "u")
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "profile" in str(e)


def test_lock_walk_uses_unfrozen_users(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,wolf")
    cfg = Config(root=tmp_path)
    seen = []

    def opener(_cfg, user=None, **_k):
        seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    clients = list(_iter_lock_clients(cfg, client=None, open_client_fn=opener))
    assert seen == ["mark", "wolf"]
    assert [c._fanops_scrape_user for c in clients] == ["mark", "wolf"]


def test_scrape_launch_never_names_google_chrome():
    from pathlib import Path

    from fanops.config import Config
    from fanops.ig_hashtag_scrape import scrape_chrome_launch_argv, safari_profile_name
    argv = scrape_chrome_launch_argv(Config(root=Path("/tmp")), "perca.late")
    joined = " ".join(argv or [])
    assert "Google Chrome" not in joined
    assert "Safari" in joined
    assert safari_profile_name("cisumwolfhom") == "Personal"
    assert safari_profile_name("markmakmouly") == "mark"
    assert safari_profile_name("perca.late") == "perca"


def test_web_hashtag_info_returns_id_and_media_count():
    def fetch(method, url, body=None):
        assert method == "GET"
        assert "/tags/music/info/" in url
        return {"name": "music", "id": "9", "media_count": 3, "status": "ok"}
    sess = IgWebSession("u", fetch=fetch)
    info = sess.hashtag_info("music")
    assert info.id == "9"
    assert info.media_count == 3


def test_resolve_hashtag_scrape_uses_web_hashtag_info():
    from fanops.ig_hashtag_scrape import resolve_hashtag_scrape
    sess = IgWebSession("u", fetch=lambda *_a, **_k: {
        "name": "music", "id": "9", "media_count": 3, "status": "ok"})
    hid, mc = resolve_hashtag_scrape(sess, "#music")
    assert hid == "9" and mc == 3.0


def test_open_web_session_passes_user_keyword(tmp_path, monkeypatch):
    """#1029 profile map: open_web_session(cfg, user=u) binds that Safari profile."""
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "markmakmouly,cisumwolfhom")
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setattr(igs, "ensure_scrape_safari", lambda *_a, **_k: True)
    monkeypatch.setattr("fanops.ig_web_scrape.safari_logged_in", lambda _u: True)
    cfg = Config(root=tmp_path)
    sess = open_web_session(cfg, user="cisumwolfhom", fetch=lambda *_a, **_k: {})
    assert sess._fanops_scrape_user == "cisumwolfhom"


def _ok_xhr(*_a, **_k):
    return json.dumps({"status": 200, "url": "https://www.instagram.com/api/v1/tags/music/info/",
                       "text": json.dumps({"ok": True})})


def test_igweb_json_paces_safari_xhr(tmp_path, monkeypatch):
    """instagrapi delay_range: first XHR has no wait; the next waits [lo,hi] since the last.
    Injected _fetch does not sleep."""
    import fanops.ig_web_scrape as iws
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "2,2")
    iws._LAST_REQUEST_MONO.clear()
    sleeps = []
    monkeypatch.setattr(iws.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(iws, "_safari_xhr", _ok_xhr)
    cfg = Config(root=tmp_path)
    live = IgWebSession("u", safari=True, cfg=cfg)
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert sleeps == []
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert len(sleeps) == 1 and sleeps[0] >= 1.9
    sleeps.clear()
    injected = IgWebSession("u", fetch=lambda *_a, **_k: {"ok": True})
    injected._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert sleeps == []
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert sleeps == []


def test_igweb_json_charges_each_live_xhr(tmp_path, monkeypatch):
    """instagrapi counts every request. Live _json +1 used; injected _fetch does not."""
    import json
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _cooldown_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    iws._LAST_REQUEST_MONO.clear()
    monkeypatch.setattr(iws.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(iws, "_safari_xhr", _ok_xhr)
    cfg = Config(root=tmp_path)
    live = IgWebSession("u", safari=True, cfg=cfg)
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    live._json("POST", "https://www.instagram.com/api/v1/tags/music/sections/", body="x")
    rec = json.loads(_cooldown_path(cfg).read_text())["accounts"]["u"]
    assert rec["used"] == 2
    assert rec.get("last_request_at")
    injected = IgWebSession("u", fetch=lambda *_a, **_k: {"ok": True}, cfg=cfg)
    injected._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    used2 = json.loads(_cooldown_path(cfg).read_text())["accounts"]["u"]["used"]
    assert used2 == 2


def test_safari_fetch_skips_network_when_frozen(tmp_path, monkeypatch):
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _persist_cooldown
    from fanops.ig_hashtag_scrape import ScrapeUnavailable
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path)
    _persist_cooldown(cfg, datetime(2026, 8, 19, tzinfo=timezone.utc),
                      reason="operator_hold", delay_s=7 * 24 * 3600, user="u")
    hit = []
    monkeypatch.setattr(iws, "_safari_xhr", lambda *_a, **_k: hit.append(1) or _ok_xhr())
    try:
        iws._safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                          user="u", cfg=cfg)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable:
        pass
    assert hit == []


def test_safari_fetch_429_freezes(tmp_path, monkeypatch):
    import json
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from datetime import datetime, timezone
    iws._LAST_REQUEST_MONO.clear()
    monkeypatch.setattr(iws.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(iws, "_safari_xhr", lambda *_a, **_k: json.dumps({
        "status": 429, "url": "https://www.instagram.com/api/v1/tags/music/info/", "text": "{}",
    }))
    cfg = Config(root=tmp_path)
    try:
        iws._safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                          user="u", cfg=cfg)
        raise AssertionError("expected WebThrottled")
    except iws.WebThrottled:
        pass
    rec = _account_rec(_load_cooldown_blob(cfg), "u")
    assert _is_frozen(rec, datetime.now(timezone.utc))
    assert rec.get("reason") == "WebThrottled"
    assert rec.get("used") == 1
