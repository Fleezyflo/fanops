# Unit: ig_hashtag_scrape resolve / measure / harvest / configured (no network).
from fanops.config import Config
from fanops.ig_hashtag_scrape import (ScrapeUnavailable,
                                       measure_and_harvest_scrape, resolve_hashtag_scrape,
                                       scrape_configured, search_hashtags_scrape)
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
    from fanops.ig_hashtag_scrape import scrape_session_path
    _sess = scrape_session_path(cfg, "u")
    _sess.parent.mkdir(parents=True, exist_ok=True)
    _sess.write_text("{}")
    assert scrape_configured(cfg) is True



def test_scrape_configured_any_of_comma_users(tmp_path, monkeypatch):
    """MOL-857: configured when ANY listed user has session or password."""
    from fanops.ig_hashtag_scrape import scrape_session_path
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "a,b,perca.late")
    cfg = Config(root=tmp_path)
    assert scrape_configured(cfg) is False
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD_B", "secret-b")
    assert scrape_configured(cfg) is True
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD_B", raising=False)
    legacy = cfg.control / "ig_scrape_session.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{}")
    assert scrape_configured(cfg) is True   # legacy session → perca.late
    assert scrape_session_path(cfg, "perca.late") == legacy


def _stub_profile_auth(monkeypatch, sid="profile-sid", ds="1"):
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setattr(igs, "_profile_auth_for", lambda *_a, **_k: (sid, ds))


def test_open_client_picks_first_usable_user(tmp_path, monkeypatch):
    """MOL-857: preference order in FANOPS_IG_SCRAPE_USER; first with session|password wins."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "dead,live,other")
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    _stub_profile_auth(monkeypatch)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "live")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = []
    class _Ok:
        def load_settings(self, p): seen.append(p)
        def search_hashtags(self, _q): return []
        def account_info(self): raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k): raise AssertionError("valid session must not login")
        def dump_settings(self, p): seen.append(("dump", p))
    open_client(cfg, client_factory=_Ok)
    assert str(sess) in seen[0]
    assert not any(isinstance(x, tuple) and x and x[0] == "dump" for x in seen)


def test_open_client_unattended_prefers_session_over_earlier_password(tmp_path, monkeypatch):
    """Unattended open_client must not stall on a password-only earlier user when a later session exists."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "pwonly,sessuser")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD_PWONLY", "x")
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
    _stub_profile_auth(monkeypatch)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "sessuser")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = []
    class _Ok:
        def load_settings(self, p): seen.append(p)
        def search_hashtags(self, _q): return []
        def account_info(self): raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k): raise AssertionError("must not login")
        def dump_settings(self, _p): seen.append("dump")
    open_client(cfg, client_factory=_Ok)
    assert str(sess) in seen[0]
    assert "dump" not in seen


def test_scrape_password_for_prefers_per_user_env(monkeypatch):
    from fanops.ig_hashtag_scrape import scrape_password_for
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "shared")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD_PERCA_LATE", "specific")
    assert scrape_password_for("perca.late") == "specific"
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD_PERCA_LATE", raising=False)
    assert scrape_password_for("perca.late") == "shared"


def test_resolve_hashtag_scrape_returns_id(tmp_path):
    c = _FakeClient({"#hiphop": 1})
    assert resolve_hashtag_scrape(c, "#HipHop") == ("id-hiphop", None)
    assert "hiphop" in c.info_calls


def test_resolve_passes_platform_throttle_through(tmp_path):
    """Bare hashtag_info — platform exception is the caller exception (identity)."""
    class PleaseWaitFewMinutes(Exception): pass
    c = _FakeClient({})
    def boom(name): raise PleaseWaitFewMinutes("please_wait")
    c.hashtag_info = boom
    try:
        resolve_hashtag_scrape(c, "#a"); assert False
    except PleaseWaitFewMinutes as e:
        assert "please_wait" in str(e)


def test_resolve_passes_platform_refuse_through(tmp_path):
    """Bare hashtag_info — non-throttle platform error is not retyped."""
    class ClientNotFoundError(Exception): pass
    c = _FakeClient({})
    def boom(name): raise ClientNotFoundError("not found")
    c.hashtag_info = boom
    try:
        resolve_hashtag_scrape(c, "#a"); assert False
    except ClientNotFoundError as e:
        assert "not found" in str(e)


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


def test_measure_asks_for_the_full_top_sample(tmp_path):
    from fanops.hashtags import TOP_SAMPLE_N
    c = _FakeClient(media_by_tag={"#hiphop": [_Media(10, "")]})
    measure_and_harvest_scrape(c, "#hiphop")
    assert c.amounts == [TOP_SAMPLE_N] and TOP_SAMPLE_N == 27


def test_recent_reel_max_ignores_feed_rows_and_old_reels(tmp_path):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    c = _FakeClient(media_by_tag={"#hiphop": [
        _Media(1, "", play_count=300, product_type="clips", taken_at=now - timedelta(days=2)),
        _Media(1, "", play_count=900, product_type="clips", taken_at=now - timedelta(hours=1)),
        _Media(1, "", play_count=10**9, product_type="clips", taken_at=now - timedelta(days=99)),
        _Media(1, "", play_count=10**8, product_type="feed", taken_at=now),
    ]})
    metric, _ = measure_and_harvest_scrape(c, "#hiphop", now=now)
    assert metric["current_top_reel_play_max_7d"] == 900.0
    assert metric["top_reel_sample_n"] == 2.0


def test_no_recent_reel_means_no_trend_fields_not_a_zero(tmp_path):
    c = _FakeClient(media_by_tag={"#hiphop": [_Media(10, "", play_count=5)]})
    metric, _ = measure_and_harvest_scrape(c, "#hiphop")
    assert "current_top_reel_play_max_7d" not in metric and "top_reel_sample_n" not in metric


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


def test_search_hashtags_scrape_maps_incomplete_hits():
    class _Hit:
        def __init__(self, name, id=None, media_count=None):
            self.name = name
            self.id = id
            self.media_count = media_count

    class _C:
        def search_hashtags(self, query):
            assert query == "music"
            return [_Hit("alpha"), _Hit("beta", id="1"), _Hit(None),
                    _Hit("gamma", id="2", media_count=9)]

    rows = search_hashtags_scrape(_C(), "#Music")
    assert {r["name"] for r in rows} == {"alpha", "beta", "gamma"}
    alpha = next(r for r in rows if r["name"] == "alpha")
    assert "id" not in alpha and "media_count" not in alpha
    assert all("play_count" not in r for r in rows)


def test_search_hashtags_scrape_fail_open_on_client_error():
    class _Boom:
        def search_hashtags(self, query):
            raise RuntimeError("network")

    assert search_hashtags_scrape(_Boom(), "music") == []


def test_open_client_unattended_profile_sid_probes_without_dump(tmp_path, monkeypatch):
    """Unattended success: load envelope + profile sid → probe ok → no dump_settings."""
    from pathlib import Path
    from types import SimpleNamespace
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    _stub_profile_auth(monkeypatch, sid="profile-sid", ds="1")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "envelope"}'
    sess.write_text(original)
    seen = {"search": 0, "login": 0, "account_info": 0, "dump": 0}

    class _Jar:
        def set(self, *a, **k):
            seen.setdefault("cookies", []).append(a[0] if a else None)

    class _Live:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1", "sessionid": "old"}
            self.private = SimpleNamespace(cookies=_Jar(), headers={})
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def account_info(self):
            seen["account_info"] += 1
            raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k):
            seen["login"] += 1
            raise AssertionError("unattended must not login")
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"overwritten": true}')
        def inject_sessionid_to_public(self):
            seen["injected"] = True
    c = open_client(cfg, client_factory=_Live)
    assert c is not None
    assert seen["search"] == 1
    assert seen["login"] == 0
    assert seen["account_info"] == 0
    assert seen["dump"] == 0
    assert seen.get("injected") is True
    assert c.authorization_data.get("sessionid") == "profile-sid"
    assert sess.read_text() == original


def test_open_client_unattended_dead_dump_no_profile_sid_leaves_envelope(tmp_path, monkeypatch):
    """Unattended dead dump + no profile sid → ScrapeUnavailable; envelope byte-identical."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import LoginRequired as _LR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    monkeypatch.setattr(igs, "_profile_auth_for", lambda *_a, **_k: None)
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "dead-dump"}'
    sess.write_text(original)
    seen = {"login": 0, "account_info": 0, "dump": 0, "search": 0}

    class _Stale:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            raise _LR("login_required")
        def account_info(self): seen["account_info"] += 1
        def login(self, *_a, **_k): seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            from pathlib import Path
            Path(p).write_text('{"overwritten": true}')
    try:
        open_client(cfg, client_factory=_Stale)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "scrape session dead" in str(e)
    assert seen == {"login": 0, "account_info": 0, "dump": 0, "search": 0}
    assert sess.read_text() == original


def test_open_client_never_reads_system_chrome(tmp_path, monkeypatch):
    """Unattended never walks system Chrome; _browser_sessionid_for must not exist."""
    import sys
    from pathlib import Path
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_chrome_profile_dir, scrape_session_path
    assert not hasattr(igs, "_browser_sessionid_for")
    assert not hasattr(igs, "_try_browser_session_restore")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    cookie = scrape_chrome_profile_dir(cfg, "u") / "Default" / "Network" / "Cookies"
    cookie.parent.mkdir(parents=True, exist_ok=True)
    cookie.write_bytes(b"x")
    seen_files: list[str] = []

    class _Cookie:
        def __init__(self, name, value, domain):
            self.name, self.value, self.domain = name, value, domain

    class _Chrome:
        def __init__(self, **kw):
            path = str(kw.get("cookie_file") or "")
            seen_files.append(path)
            home_chrome = str(Path.home() / "Library/Application Support/Google/Chrome")
            if home_chrome in path or "9222" in path or "9223" in path:
                raise AssertionError(f"must not read system Chrome: {path}")
            self._hits = [
                _Cookie("sessionid", "profile-sid", ".instagram.com"),
                _Cookie("ds_user_id", "1", ".instagram.com"),
            ]
        def __iter__(self):
            return iter(self._hits)

    fake = type(sys)("browser_cookie3")
    fake.chrome = _Chrome
    fake.BrowserCookieError = OSError
    monkeypatch.setitem(sys.modules, "browser_cookie3", fake)
    real_glob = Path.glob

    def guarded_glob(self, pattern):
        s = str(self)
        if "Application Support/Google/Chrome" in s:
            raise AssertionError(f"must not glob system Chrome: {self}/{pattern}")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", guarded_glob)

    class _Live:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1"}
        def load_settings(self, _p): pass
        def search_hashtags(self, _q): return []
        def account_info(self): raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k): raise AssertionError("unattended must not login")
        def dump_settings(self, _p): raise AssertionError("unattended must not dump")
    open_client(cfg, client_factory=_Live)
    assert seen_files
    root = str(scrape_chrome_profile_dir(cfg, "u"))
    assert all(root in p for p in seen_files)


def test_open_client_unattended_dead_profile_sid_leaves_envelope(tmp_path, monkeypatch):
    """Unattended profile sid + LoginRequired probe → ScrapeUnavailable; envelope unchanged."""
    from pathlib import Path
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import LoginRequired as _LR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    _stub_profile_auth(monkeypatch, sid="dead-sid", ds="1")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "envelope"}'
    sess.write_text(original)
    seen = {"dump": 0, "login": 0}

    class _Dead:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1"}
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            raise _LR("login_required")
        def account_info(self): raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"overwritten": true}')
    try:
        open_client(cfg, client_factory=_Dead)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "scrape session dead" in str(e)
    assert seen == {"dump": 0, "login": 0}
    assert sess.read_text() == original


def test_open_client_unattended_ds_mismatch_refuses_no_write(tmp_path, monkeypatch):
    """Profile sid with wrong ds_user_id → refuse, no write."""
    from pathlib import Path
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    _stub_profile_auth(monkeypatch, sid="profile-sid", ds="999")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "envelope"}'
    sess.write_text(original)
    seen = {"search": 0, "dump": 0, "login": 0}

    class _Env:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1", "sessionid": "old"}
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            raise AssertionError("ds mismatch must refuse before probe")
        def account_info(self): raise AssertionError("unattended must not probe account_info")
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"overwritten": true}')
    try:
        open_client(cfg, client_factory=_Env)
        raise AssertionError("expected ScrapeUnavailable")
    except ScrapeUnavailable as e:
        assert "ds_user_id" in str(e)
    assert seen == {"search": 0, "dump": 0, "login": 0}
    assert sess.read_text() == original


def test_scrape_login_promote_writes_envelope_from_profile_sid(tmp_path, monkeypatch):
    """scrape-login promote writes envelope (fake client + fake profile sid)."""
    from pathlib import Path
    from types import SimpleNamespace
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    _stub_profile_auth(monkeypatch, sid="profile-sid", ds="1")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = {"search": 0, "login": 0, "dump": 0}

    class _Jar:
        def set(self, *a, **k): pass

    class _Live:
        def __init__(self):
            self.authorization_data = {"ds_user_id": "1"}
            self.private = SimpleNamespace(cookies=_Jar(), headers={})
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"promoted": true}')
        def inject_sessionid_to_public(self):
            seen["injected"] = True
    c = open_client(cfg, client_factory=_Live, allow_reauth=True, user="u")
    assert c is not None
    assert seen["search"] == 1
    assert seen["login"] == 0
    assert seen["dump"] == 1
    assert seen.get("injected") is True
    assert '"promoted": true' in sess.read_text()


def test_scrape_chrome_launch_argv_uses_fanops_profile_not_devtools(tmp_path, monkeypatch):
    """Operator Chrome argv is that user's FanOps profile; no DevTools port."""
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "perca.late")
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(igs, "_chrome_executable", lambda: "/fake/chrome")
    argv = igs.scrape_chrome_launch_argv(cfg, "perca.late")
    assert argv is not None
    joined = " ".join(argv)
    assert argv[0] == "/fake/chrome"
    assert f"--user-data-dir={igs.scrape_chrome_profile_dir(cfg, 'perca.late')}" in argv
    assert "9222" not in joined and "9223" not in joined
    assert "remote-debugging" not in joined
    assert "Application Support/Google/Chrome" not in joined
    launched = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **_k: launched.append(list(argv)) or object())
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: ("sid", "1"))
    from fanops.fanops_hashtags import cmd_hashtags_scrape_login
    monkeypatch.setattr(igs, "open_client", lambda *_a, **_k: object())
    assert cmd_hashtags_scrape_login(cfg) == 0
    assert igs.scrape_chrome_profile_dir(cfg, "perca.late").is_dir()
    assert launched
    assert launched[0] == argv


def test_scrape_login_no_profile_sid_does_not_promote(tmp_path, monkeypatch):
    """Wait timeout → no open_client, no dump, no password."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import cmd_hashtags_scrape_login
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(igs, "launch_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: None)
    opened = []
    monkeypatch.setattr(igs, "open_client", lambda *_a, **_k: opened.append(1))
    assert cmd_hashtags_scrape_login(cfg) == 2
    assert opened == []
    from fanops.ig_hashtag_scrape import scrape_session_path
    assert not scrape_session_path(cfg, "u").exists()


def test_wait_for_scrape_profile_auth_returns_when_sid_appears(tmp_path, monkeypatch):
    import fanops.ig_hashtag_scrape as igs
    cfg = Config(root=tmp_path)
    hits = {"n": 0}

    def _auth(*_a, **_k):
        hits["n"] += 1
        return ("sid", "1") if hits["n"] >= 2 else None
    monkeypatch.setattr(igs, "_profile_auth_for", _auth)
    slept = []
    got = igs.wait_for_scrape_profile_auth(
        cfg, "u", timeout_s=5, sleep=lambda s: slept.append(s),
        clock=lambda: 0 if hits["n"] < 2 else 5)
    assert got == ("sid", "1")
    assert slept
