# Unit: ig_hashtag_scrape resolve / measure / harvest / configured (no network).
from fanops.config import Config
from fanops.ig_hashtag_scrape import (ScrapeRefused, ScrapeThrottled, ScrapeUnavailable,
                                       measure_and_harvest_scrape, resolve_hashtag_scrape,
                                       scrape_configured)
from hashtag_scrape_fakes import _FakeClient, _Media


def test_scrape_configured_needs_user_and_session_or_password(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg = Config(root=tmp_path)
    assert scrape_configured(cfg) is False
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    assert scrape_configured(cfg) is False
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    assert scrape_configured(cfg) is True
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    cfg.ig_scrape_session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ig_scrape_session_path.write_text("{}")
    assert scrape_configured(cfg) is True


def test_resolve_hashtag_scrape_returns_id(tmp_path):
    c = _FakeClient({"#hiphop": 1})
    assert resolve_hashtag_scrape(c, "#HipHop") == ("id-hiphop", None)
    assert "hiphop" in c.info_calls


def test_resolve_maps_throttle(tmp_path):
    class Boom(Exception): pass
    c = _FakeClient({})
    def boom(name): raise Boom("PleaseWaitFewMinutes")
    c.hashtag_info = boom
    try:
        resolve_hashtag_scrape(c, "#a"); assert False
    except ScrapeThrottled:
        pass


def test_resolve_maps_refuse(tmp_path):
    class Boom(Exception): pass
    c = _FakeClient({})
    def boom(name): raise Boom("not found")
    c.hashtag_info = boom
    try:
        resolve_hashtag_scrape(c, "#a"); assert False
    except ScrapeRefused as e:
        assert "not found" in e.message


def test_measure_median_like_and_harvest(tmp_path):
    c = _FakeClient(media_by_tag={"#hiphop": [
        _Media(None, "#alpha"), _Media(777, "#alpha #beta"), _Media(1, "")]})
    metric, cotags = measure_and_harvest_scrape(c, "#hiphop")
    assert metric == {"like_count": 389.0}   # median of [777, 1]
    assert cotags["#alpha"] == 2 and cotags["#beta"] == 1


def test_measure_play_count_median_preferred(tmp_path):
    c = _FakeClient(media_by_tag={"#hiphop": [
        _Media(10, "", play_count=1000), _Media(90, "", play_count=2000), _Media(50, "", play_count=3000)]})
    metric, _ = measure_and_harvest_scrape(c, "#hiphop")
    assert metric["play_count"] == 2000.0
    assert metric["like_count"] == 50.0


def test_resolve_returns_media_count(tmp_path):
    c = _FakeClient({"#hiphop": 1}, media_count_by_tag={"#hiphop": 12345})
    assert resolve_hashtag_scrape(c, "#hiphop") == ("id-hiphop", 12345.0)


def test_measure_unmeasured_on_empty(tmp_path):
    c = _FakeClient({})
    assert measure_and_harvest_scrape(c, "#x") == (None, {})


def test_open_client_unavailable_without_user(tmp_path, monkeypatch):
    from fanops.ig_hashtag_scrape import open_client
    monkeypatch.delenv("FANOPS_IG_SCRAPE_USER", raising=False)
    cfg = Config(root=tmp_path)
    try:
        open_client(cfg); assert False
    except ScrapeUnavailable as e:
        assert "FANOPS_IG_SCRAPE_USER" in str(e)
