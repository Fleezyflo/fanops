"""Lock scrape fetches inside FanOps Chrome, not instagrapi / exported cookies."""
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


def test_open_web_session_refuses_without_live_chrome(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: False)
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


def test_cdp_port_is_fanops_owned_not_system_devtools():
    from fanops.ig_hashtag_scrape import scrape_cdp_port
    from fanops.ig_web_scrape import _Ws
    for user in ("markmakmouly", "perca.late", "cisumwolfhom"):
        port = scrape_cdp_port(user)
        assert 9331 <= port <= 9399
        assert port not in (9222, 9223)
    for url in ("ws://127.0.0.1:9222/devtools/page/x", "ws://10.0.0.1:9331/devtools/page/x"):
        try:
            _Ws.connect(url)
            raise AssertionError(url)
        except RuntimeError:
            pass
