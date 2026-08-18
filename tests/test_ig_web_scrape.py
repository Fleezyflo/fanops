"""Lock scrape uses FanOps Chrome *web* cookies, not instagrapi private API."""
from types import SimpleNamespace

from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, measure_and_harvest_scrape, search_hashtags_scrape
from fanops.ig_web_scrape import IgWebSession, LoginRequired, open_web_session
from fanops.source_tags import _iter_lock_clients


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_web_search_exact_name(monkeypatch):
    sess = IgWebSession(
        "u",
        {"sessionid": "x" * 40, "csrftoken": "t", "ds_user_id": "1"},
        get=lambda *_a, **_k: _Resp({
            "hashtags": [{"hashtag": {"name": "music", "id": "9", "media_count": 3}}],
        }),
    )
    hits = search_hashtags_scrape(sess, "music")
    assert [h["name"] for h in hits] == ["music"]


def test_web_403_is_login_required():
    def _forbid(*_a, **_k):
        return _Resp({}, status=403)
    sess = IgWebSession("u", {"sessionid": "x" * 40}, get=_forbid)
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
    sess = IgWebSession(
        "u",
        {"sessionid": "x" * 40, "csrftoken": "t"},
        post=lambda *_a, **_k: _Resp(payload),
    )
    metrics, cotags = measure_and_harvest_scrape(sess, "#music")
    assert metrics is not None
    assert metrics["like_count"] == 10
    assert metrics["play_count"] == 100
    assert "#music" in cotags or "#live" in cotags


def test_open_web_session_refuses_without_profile_sid(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setattr(igs, "profile_instagram_cookies", lambda *_a, **_k: {})
    cfg = Config(root=tmp_path)
    try:
        open_web_session(cfg, "u")
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "profile" in str(e)


def test_lock_walk_uses_web_opener(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "mark,wolf")
    import fanops.ig_web_scrape as web
    monkeypatch.setattr(web, "profile_instagram_cookies",
                        lambda _c, u: {"sessionid": "s"} if u == "mark" else {})
    cfg = Config(root=tmp_path)
    seen = []

    def opener(_cfg, user=None, **_k):
        seen.append(user)
        return SimpleNamespace(_fanops_scrape_user=user)

    clients = list(_iter_lock_clients(cfg, client=None, open_client_fn=opener))
    assert seen == ["mark"]
    assert [c._fanops_scrape_user for c in clients] == ["mark"]
