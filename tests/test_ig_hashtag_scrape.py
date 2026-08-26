# Unit: ig_hashtag_scrape resolve / measure / harvest / configured (no network).
from pathlib import Path
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



def test_open_client_picks_first_usable_user(tmp_path, monkeypatch):
    """MOL-857: preference order in FANOPS_IG_SCRAPE_USER; first with session|password wins."""
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "dead,live,other")
    monkeypatch.delenv("FANOPS_IG_SCRAPE_PASSWORD", raising=False)
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


def test_search_hashtags_feedback_required_does_not_fail_open():
    """FeedbackRequired was named in docs/cooldown copy but not _SESSION_DEAD_NAMES — search
    swallowed it to [] and the pass kept walking."""
    from fanops.ig_hashtag_scrape import scrape_session_dead

    class FeedbackRequired(Exception):
        pass

    class _C:
        def search_hashtags(self, query):
            raise FeedbackRequired("feedback_required")

    assert scrape_session_dead(FeedbackRequired("x")) is True
    try:
        search_hashtags_scrape(_C(), "#x")
        raise AssertionError("must not fail-open to []")
    except FeedbackRequired:
        pass


def test_search_hashtags_rate_limit_does_not_fail_open():
    from fanops.ig_hashtag_scrape import scrape_session_dead

    class RateLimitError(Exception):
        pass

    class _C:
        def search_hashtags(self, query):
            raise RateLimitError("rate_limit_error")

    assert scrape_session_dead(RateLimitError("x")) is True
    try:
        search_hashtags_scrape(_C(), "#x")
        raise AssertionError("must not fail-open to []")
    except RateLimitError:
        pass


def test_search_hashtags_scrape_unavailable_does_not_fail_open():
    from fanops.ig_hashtag_scrape import ScrapeUnavailable

    class _C:
        def search_hashtags(self, query):
            raise ScrapeUnavailable("safari scrape not ready")

    try:
        search_hashtags_scrape(_C(), "#x")
        raise AssertionError("must not fail-open to []")
    except ScrapeUnavailable:
        pass


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


def test_open_client_unattended_envelope_probes_without_dump(tmp_path, monkeypatch):
    """Unattended success: load envelope → probe ok → no dump_settings."""
    from pathlib import Path
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    def _boom(_u):
        raise AssertionError("unattended must not read scrape password")
    monkeypatch.setattr(igs, "scrape_password_for", _boom)
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    original = '{"keep": "envelope"}'
    sess.write_text(original)
    seen = {"search": 0, "login": 0, "dump": 0}

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"overwritten": true}')
    c = open_client(cfg, client_factory=_Live)
    assert c is not None
    assert seen == {"search": 1, "login": 0, "dump": 0}
    assert sess.read_text() == original



def test_open_client_unattended_dead_dump_no_profile_sid_leaves_envelope(tmp_path, monkeypatch):
    """Unattended dead dump + no profile sid → ScrapeUnavailable; envelope byte-identical."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import LoginRequired as _LR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
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
        assert "scrape session" in str(e)
    assert seen == {"login": 0, "account_info": 0, "dump": 0, "search": 1}
    assert sess.read_text() == original


def test_ht4_runtime_has_no_cookie_inject_symbols():
    """HT4: cookie inject helpers must not exist — only envelope-based open_client remains."""
    import fanops.ig_hashtag_scrape as igs
    src = Path(igs.__file__).read_text()
    for needle in ("import browser_cookie3", "def _inject_sessionid", "def _profile_auth_for",
                   "def profile_instagram_cookies"):
        assert needle not in src, f"{needle} must be deleted from ig_hashtag_scrape"



def test_open_client_unattended_dead_profile_sid_leaves_envelope(tmp_path, monkeypatch):
    """Unattended profile sid + LoginRequired probe → ScrapeUnavailable; envelope unchanged."""
    from pathlib import Path
    import fanops.ig_hashtag_scrape as igs
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client, scrape_session_path
    from instagrapi.exceptions import LoginRequired as _LR
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
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



def test_scrape_login_promote_writes_envelope_from_session_file(tmp_path, monkeypatch):
    """scrape-login promote writes envelope from on-disk session probe (no cookie inject)."""
    from pathlib import Path
    from fanops.ig_hashtag_scrape import open_client, scrape_session_path
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    cfg = Config(root=tmp_path)
    sess = scrape_session_path(cfg, "u")
    sess.parent.mkdir(parents=True, exist_ok=True)
    sess.write_text("{}")
    seen = {"search": 0, "login": 0, "dump": 0}

    class _Live:
        def load_settings(self, _p): pass
        def search_hashtags(self, _q):
            seen["search"] += 1
            return []
        def login(self, *_a, **_k):
            seen["login"] += 1
        def dump_settings(self, p):
            seen["dump"] += 1
            Path(p).write_text('{"promoted": true}')
    c = open_client(cfg, client_factory=_Live, allow_reauth=True, user="u")
    assert c is not None
    assert seen["search"] == 1
    assert seen["login"] == 0
    assert seen["dump"] == 1
    assert '"promoted": true' in sess.read_text()



def test_scrape_launch_argv_is_safari_never_google_chrome(tmp_path, monkeypatch):
    """Scrape launch is Safari. Google Chrome must stay the operator's browser."""
    import fanops.ig_hashtag_scrape as igs
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "perca.late")
    cfg = Config(root=tmp_path)
    argv = igs.scrape_chrome_launch_argv(cfg, "perca.late")
    assert argv is not None
    joined = " ".join(argv)
    assert "Safari" in argv
    assert argv[-1] == "perca"
    assert "Google Chrome" not in joined
    assert "9222" not in joined and "9223" not in joined
    assert "remote-debugging" not in joined
    assert "Application Support/Google/Chrome" not in joined
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: ("sid", "1"))
    from fanops.fanops_hashtags import cmd_hashtags_scrape_login
    monkeypatch.setattr(igs, "open_client", lambda *_a, **_k: object())
    assert cmd_hashtags_scrape_login(cfg) == 0
    assert igs.scrape_chrome_profile_dir(cfg, "perca.late").is_dir()


def test_scrape_login_no_profile_sid_does_not_promote(tmp_path, monkeypatch):
    """Wait timeout → no open_client, no dump, no password."""
    import fanops.ig_hashtag_scrape as igs
    from fanops.fanops_hashtags import cmd_hashtags_scrape_login
    monkeypatch.setenv("FANOPS_IG_SCRAPE_USER", "u")
    monkeypatch.setenv("FANOPS_IG_SCRAPE_PASSWORD", "p")
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(igs, "ensure_scrape_chrome", lambda *_a, **_k: True)
    monkeypatch.setattr(igs, "wait_for_scrape_profile_auth", lambda *_a, **_k: None)
    opened = []
    monkeypatch.setattr(igs, "open_client", lambda *_a, **_k: opened.append(1))
    assert cmd_hashtags_scrape_login(cfg) == 2
    assert opened == []
    from fanops.ig_hashtag_scrape import scrape_session_path
    assert not scrape_session_path(cfg, "u").exists()


def test_wait_for_scrape_profile_auth_returns_when_sid_appears(tmp_path, monkeypatch):
    import fanops.ig_hashtag_scrape as igs
    import fanops.ig_web_scrape as web
    cfg = Config(root=tmp_path)
    hits = {"n": 0}

    def _auth(*_a, **_k):
        hits["n"] += 1
        return ("safari", "u") if hits["n"] >= 2 else None
    monkeypatch.setattr(web, "safari_profile_auth", _auth)
    slept = []
    got = igs.wait_for_scrape_profile_auth(
        cfg, "u", timeout_s=5, sleep=lambda s: slept.append(s),
        clock=lambda: 0 if hits["n"] < 2 else 5)
    assert got == ("safari", "u")
    assert slept
