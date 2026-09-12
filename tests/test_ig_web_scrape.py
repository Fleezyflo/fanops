"""Lock scrape fetches inside Safari, never Google Chrome."""
import json
import subprocess
from types import SimpleNamespace

from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, measure_and_harvest_scrape, search_hashtags_scrape
from fanops.ig_safari_shell import (
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    SentryBlock,
    WebThrottled,
    _body_stop,
    _text_stop,
    safari_fetch,
    safari_logged_in,
    safari_xhr,
)
from fanops.ig_web_scrape import IgWebSession, _collect_medias, _looks_like_media, open_web_session
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
    sess = IgWebSession("u", fetch=lambda *_a, **_k: {
        "name": "nope", "id": "9", "media_count": 99, "status": "ok"})
    assert search_hashtags_scrape(sess, "music") == []


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


def test_collect_medias_and_looks_like_media():
    blob = {
        "sections": [{
            "layout_content": {
                "medias": [{
                    "media": {
                        "pk": "1",
                        "like_count": 10,
                        "play_count": 100,
                        "taken_at": 1_700_000_000,
                    }
                }]
            }
        }]
    }
    found = _collect_medias(blob)
    assert len(found) == 1
    assert found[0].like_count == 10
    assert found[0].play_count == 100
    assert _looks_like_media({"pk": "1", "like_count": 1}) is True
    assert _looks_like_media({"pk": "1"}) is False
    assert _collect_medias({"status": "ok"}) == []


def test_body_stop_named_signals_and_missing_tag():
    assert isinstance(_body_stop({
        "status": "fail", "message": "login_required", "require_login": True,
    }), LoginRequired)
    assert isinstance(_body_stop({
        "status": "fail", "message": "Please wait a few minutes before you try again.",
    }), PleaseWaitFewMinutes)
    assert isinstance(_body_stop({
        "status": "fail", "message": "feedback_required", "spam": True,
    }), FeedbackRequired)
    assert isinstance(_body_stop({
        "status": "fail", "error_type": "rate_limit_error",
    }), RateLimitError)
    assert isinstance(_body_stop({
        "status": "fail", "error_type": "sentry_block",
    }), SentryBlock)
    assert isinstance(_body_stop({
        "status": "fail", "error_title": "You've Been Logged Out", "logout_reason": 8,
    }), LoginRequired)
    assert _body_stop({"status": "fail", "message": "Invalid hashtag"}) is None


def test_text_stop_html_login_and_garbage():
    html = "<!DOCTYPE html><html><form><input name=\"username\"></form></html>"
    assert isinstance(_text_stop(html), LoginRequired)
    assert isinstance(_text_stop("not-json"), WebThrottled)


def _osascript_out(monkeypatch, result, seen=None):
    def fake_co(cmd, *a, **k):
        if seen is not None:
            seen.append({"cmd": list(cmd), "input": k.get("input") or ""})
        text = result if isinstance(result, str) else result()
        return text if text.endswith("\n") else text + "\n"
    monkeypatch.setattr("subprocess.check_output", fake_co)


def test_safari_logged_in_does_not_hit_tags_api(monkeypatch):
    """Login check is the existing tab (login URL / login form). #music/info is a
    private API call and was fired once per unfinished source — that logged
    the accounts out. sessionid is HttpOnly so document.cookie is not a signal."""
    seen = []
    _osascript_out(monkeypatch, "ok", seen)
    assert safari_logged_in("markmakmouly") is True
    blob = " ".join(str(item) for rec in seen for item in (rec["cmd"], rec["input"]))
    assert "/tags/" not in blob
    assert "music" not in blob
    assert "sessionid" not in blob
    _osascript_out(monkeypatch, "login")
    assert safari_logged_in("markmakmouly") is False


def test_safari_xhr_sends_www_claim(monkeypatch):
    """IG web /api/v1/tags needs X-IG-WWW-Claim from sessionStorage www-claim-v2.
    Missing claim is login_required on a live feed."""
    seen = []
    _osascript_out(monkeypatch, '{"status":200,"url":"","text":"{}"}', seen)
    safari_xhr("GET", "https://www.instagram.com/api/v1/tags/music/info/", user="u")
    assert seen
    expr = " ".join(str(x) for x in seen[0]["cmd"])
    assert "www-claim-v2" in expr
    assert "X-IG-WWW-Claim" in expr


def test_file_menu_profile_window_helpers_are_gone():
    """Unattended does not File-menu restore Safari windows (#1182)."""
    import fanops.ig_hashtag_scrape as igs
    assert not hasattr(igs, "safari_open_profile_window")
    assert not hasattr(igs, "safari_profile_window_open")


def test_safari_open_instagram_does_not_reload_live_tab(monkeypatch):
    """Reloading a tab that already has instagram.com is the session-kill."""
    import fanops.ig_hashtag_scrape as igs
    seen = []
    _osascript_out(monkeypatch, "2", seen)
    igs.safari_open_instagram("markmakmouly")
    blob = "\n".join(rec["input"] for rec in seen)
    assert "set URL of current tab" not in blob


def test_safari_set_instagram_skips_existing_tab(monkeypatch):
    import fanops.ig_hashtag_scrape as igs
    seen = []
    _osascript_out(monkeypatch, "have", seen)
    igs._safari_set_instagram_if_missing("markmakmouly")
    assert seen
    script = seen[0]["input"]
    assert "return \"have\"" in script
    assert "set URL of current tab of w" in script


def test_open_web_session_refuses_without_safari(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")

    def fake_co(cmd, *a, **k):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="no instagram tab")

    monkeypatch.setattr("subprocess.check_output", fake_co)
    cfg = Config(root=tmp_path)
    try:
        open_web_session(cfg, "u")
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "profile" in str(e) or "session" in str(e)


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


def test_scrape_launch_never_names_google_chrome(tmp_path, monkeypatch):
    from fanops.ig_hashtag_scrape import launch_scrape_chrome, safari_open_instagram, safari_profile_name
    osa = []

    def fake_co(cmd, *a, **k):
        osa.append(k.get("input") or "")
        return "2\n"

    monkeypatch.setattr("subprocess.check_output", fake_co)
    cfg = Config(root=tmp_path)
    safari_open_instagram("perca.late")
    assert launch_scrape_chrome(cfg, "perca.late") is True
    blob = "\n".join(osa)
    assert "Safari" in blob
    assert "Google Chrome" not in blob
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
    cfg = Config(root=tmp_path)
    sess = open_web_session(cfg, user="cisumwolfhom", fetch=lambda *_a, **_k: {})
    assert sess._fanops_scrape_user == "cisumwolfhom"


def _ok_xhr_text():
    return json.dumps({"status": 200, "url": "https://www.instagram.com/api/v1/tags/music/info/",
                       "text": json.dumps({"ok": True})})


def _xhr_json(http, payload, text=None):
    return json.dumps({
        "status": http,
        "url": "https://www.instagram.com/api/v1/tags/music/info/",
        "text": text if text is not None else json.dumps(payload),
    })


def test_igweb_json_paces_safari_xhr(tmp_path, monkeypatch):
    """instagrapi delay_range: first XHR has no wait; the next waits [lo,hi] since the last.
    Injected _fetch does not sleep."""
    import time as _time
    import fanops.ig_web_scrape as iws
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "2,2")
    iws._LAST_REQUEST_MONO.clear()
    _osascript_out(monkeypatch, _ok_xhr_text())
    cfg = Config(root=tmp_path)
    live = IgWebSession("u", safari=True, cfg=cfg)
    t0 = _time.monotonic()
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert _time.monotonic() - t0 >= 1.9
    injected = IgWebSession("u", fetch=lambda *_a, **_k: {"ok": True})
    t1 = _time.monotonic()
    injected._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert _time.monotonic() - t1 < 0.5
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    t2 = _time.monotonic()
    live._json("GET", "https://www.instagram.com/api/v1/tags/music/info/")
    assert _time.monotonic() - t2 < 0.5


def test_igweb_json_charges_each_live_xhr(tmp_path, monkeypatch):
    """instagrapi counts every request. Live _json +1 used; injected _fetch does not."""
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _cooldown_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    _osascript_out(monkeypatch, _ok_xhr_text())
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
    """Pinned now — not Aug-19+7d wall clock (expires mid-CI on 2026-08-26)."""
    from fanops.fanops_hashtags import _persist_cooldown
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    _persist_cooldown(cfg, datetime(2099, 1, 1, tzinfo=timezone.utc),
                      reason="operator_hold", delay_s=7 * 24 * 3600, user="u")
    hit = []

    def fake_co(cmd, *a, **k):
        hit.append(1)
        return _ok_xhr_text() + "\n"

    monkeypatch.setattr("subprocess.check_output", fake_co)
    try:
        safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                     user="u", cfg=cfg)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable:
        pass
    assert hit == []


def _assert_fetch_freezes(tmp_path, monkeypatch, xhr_raw, exc_cls, reason):
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from datetime import datetime, timezone
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    _osascript_out(monkeypatch, xhr_raw)
    cfg = Config(root=tmp_path)
    try:
        safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                     user="u", cfg=cfg)
        raise AssertionError(f"expected {exc_cls.__name__}")
    except exc_cls:
        pass
    rec = _account_rec(_load_cooldown_blob(cfg), "u")
    assert _is_frozen(rec, datetime.now(timezone.utc))
    assert rec.get("reason") == reason


def test_safari_fetch_429_freezes(tmp_path, monkeypatch):
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from datetime import datetime, timezone
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    _osascript_out(monkeypatch, json.dumps({
        "status": 429, "url": "https://www.instagram.com/api/v1/tags/music/info/", "text": "{}",
    }))
    cfg = Config(root=tmp_path)
    try:
        safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                     user="u", cfg=cfg)
        raise AssertionError("expected WebThrottled")
    except WebThrottled:
        pass
    rec = _account_rec(_load_cooldown_blob(cfg), "u")
    assert _is_frozen(rec, datetime.now(timezone.utc))
    assert rec.get("reason") == "WebThrottled"
    assert rec.get("used") == 1


def test_safari_fetch_200_please_wait_freezes(tmp_path, monkeypatch):
    """instagrapi PleaseWaitFewMinutes is a 200 body. HTTP-status-only freeze never fired."""
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail",
        "message": "Please wait a few minutes before you try again.",
    }), PleaseWaitFewMinutes, "PleaseWaitFewMinutes")


def test_safari_fetch_200_feedback_required_freezes(tmp_path, monkeypatch):
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail",
        "message": "feedback_required",
        "spam": True,
        "feedback_title": "We restrict certain activity",
        "feedback_message": "This action was blocked.",
    }), FeedbackRequired, "FeedbackRequired")


def test_safari_fetch_200_login_required_body_freezes(tmp_path, monkeypatch):
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail", "message": "login_required", "require_login": True,
    }), LoginRequired, "auth_death")


def test_safari_fetch_200_missing_tag_does_not_freeze(tmp_path, monkeypatch):
    """status=fail for a missing hashtag is not an account freeze."""
    import fanops.ig_web_scrape as iws
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from datetime import datetime, timezone
    monkeypatch.setenv("FANOPS_HASHTAG_SCRAPE_DELAY", "0")
    iws._LAST_REQUEST_MONO.clear()
    _osascript_out(monkeypatch, _xhr_json(200, {
        "status": "fail", "message": "Invalid hashtag",
    }))
    cfg = Config(root=tmp_path)
    out = safari_fetch("GET", "https://www.instagram.com/api/v1/tags/nope/info/",
                       user="u", cfg=cfg)
    assert out.get("message") == "Invalid hashtag"
    rec = _account_rec(_load_cooldown_blob(cfg), "u")
    assert not _is_frozen(rec, datetime.now(timezone.utc))
    assert rec.get("used") == 1


def test_safari_fetch_200_rate_limit_error_freezes(tmp_path, monkeypatch):
    """instagrapi RateLimitError is error_type on HTTP 200. Text-only please-wait missed it."""
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail", "error_type": "rate_limit_error",
    }), RateLimitError, "RateLimitError")


def test_safari_fetch_200_sentry_block_freezes(tmp_path, monkeypatch):
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail", "error_type": "sentry_block",
    }), SentryBlock, "SentryBlock")


def test_safari_fetch_200_logged_out_title_freezes(tmp_path, monkeypatch):
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, {
        "status": "fail", "error_title": "You've Been Logged Out", "logout_reason": 8,
    }), LoginRequired, "auth_death")


def test_safari_fetch_200_html_login_freezes(tmp_path, monkeypatch):
    """Session death as HTML, not JSON — RuntimeError used to fail-open the lock walk."""
    html = "<!DOCTYPE html><html><form><input name=\"username\"></form></html>"
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, None, text=html),
                         LoginRequired, "auth_death")


def test_safari_fetch_200_non_json_freezes(tmp_path, monkeypatch):
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(200, None, text="not-json"),
                         WebThrottled, "WebThrottled")


def test_safari_fetch_400_unclassified_freezes(tmp_path, monkeypatch):
    """Unclassified 4xx was RuntimeError — lock search fail-opened and kept walking."""
    _assert_fetch_freezes(tmp_path, monkeypatch, _xhr_json(400, {
        "status": "fail", "message": "counter get error",
    }), WebThrottled, "WebThrottled")


def test_safari_fetch_skips_when_day_budget_exhausted(tmp_path, monkeypatch):
    """HT3: scrape_user_blocked must gate day budget — no XHR when used >= day budget."""
    from fanops.fanops_hashtags import (_SCRAPE_DAY_BUDGET, _cooldown_path, _utc_day,
                                       scrape_user_blocked)
    from fanops.controlio import write_json_atomic
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path)
    now = datetime.now(timezone.utc)
    write_json_atomic(_cooldown_path(cfg), {
        "accounts": {"u": {"day": _utc_day(now), "used": _SCRAPE_DAY_BUDGET}}})
    assert scrape_user_blocked(cfg, "u", now) is True
    hit = []

    def fake_co(cmd, *a, **k):
        hit.append(1)
        return _ok_xhr_text() + "\n"

    monkeypatch.setattr("subprocess.check_output", fake_co)
    try:
        safari_fetch("GET", "https://www.instagram.com/api/v1/tags/music/info/",
                     user="u", cfg=cfg)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "budget" in str(e).lower() or "frozen" in str(e).lower()
    assert hit == []
