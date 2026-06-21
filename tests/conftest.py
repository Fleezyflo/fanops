# tests/conftest.py — hermetic env baseline for the unit suite.
#
# Config.__init__ calls load_dotenv(self.root / ".env"); a Config() built with the default cwd root
# (as several tests do) loads the OPERATOR's live repo .env into os.environ — currently
# FANOPS_POSTER=postiz + POSTIZ_URL/POSTIZ_API_KEY. load_dotenv does NOT override an already-set var
# but DOES set new ones, so once any test triggers that load the leaked vars PERSIST for the rest of
# the process and silently flip later tests "live" (the cutover/publish auth tests that assume dryrun
# / an unset key then DON'T RAISE). That is an order-dependent failure that depends on the .env state
# on disk — exactly the isolation bug this fixture closes.
#
# Strategy: strip the publish-mode/credential vars at the START of every test and restore the real
# process environment AFTER. Tests that want a live backend still set it explicitly via monkeypatch
# (and get their own clean teardown). The suite no longer depends on what FANOPS_POSTER happens to be
# in the repo .env.
import os
import pytest

# Vars the repo .env can leak that change publish/auth behavior — neutralized per test so a live .env
# never poisons a unit test. (POSTIZ_URL/POSTIZ_API_KEY ride along so a leaked URL can't 'configure'
# a backend the test didn't ask for.)
# FANOPS_HOOK_JUDGE rides along: it DEFAULTS ON (v2), so a dev's repo .env carrying =off would
# silently flip the critic OFF for every test that doesn't set it explicitly (the inverse of the
# FANOPS_POSTER leak). Stripping it makes each test see the CODE default; opt-out tests set it via
# monkeypatch and get clean teardown.
# META_GRAPH_TOKEN/META_IG_USER_ID/FANOPS_HASHTAG_TRENDS/META_GRAPH_URL ride along (M4): once the
# operator wires live trends into the repo .env, a token + FANOPS_HASHTAG_TRENDS=1 leaking into the
# session makes refresh_store fire a REAL ig_hashtag_search over the network (60s timeout, flaky/CI-
# breaking). Stripping them makes every test see the OFF default; the trend tests set them + inject a
# mock `get` explicitly.
# FANOPS_CONCURRENT_SOURCES/FANOPS_CONCURRENT_WORKERS ride along (parallel-source pipeline): the
# concurrency flag DEFAULTS OFF (the byte-identical contract), so an operator's repo .env carrying
# =1 leaking into the session would silently flip every test onto the pooled path (and the worker
# count along with it). Stripping them makes each test see the OFF default; the concurrent tests
# set them explicitly via monkeypatch and get clean teardown.
_LEAKY_ENV = ("FANOPS_POSTER", "BLOTATO_API_KEY", "POSTIZ_API_KEY", "POSTIZ_URL", "FANOPS_HOOK_JUDGE",
              "META_GRAPH_TOKEN", "META_IG_USER_ID", "FANOPS_HASHTAG_TRENDS", "META_GRAPH_URL",
              "FANOPS_GC_KEEP_DAYS",   # content-lifecycle Phase 3: a repo .env value must not leak into the gc-window tests
              "FANOPS_CONCURRENT_SOURCES", "FANOPS_CONCURRENT_WORKERS",
              # persona/learning behavior flags (default OFF): once the operator persists e.g.
              # FANOPS_CREATIVE_VARIATION=1 to the repo .env (the supported "system default"), it must not
              # leak into tests that assume the code default — same class as FANOPS_HOOK_JUDGE above.
              "FANOPS_CREATIVE_VARIATION", "FANOPS_VARIANT_LEARNING", "FANOPS_P4_DIM_BIAS")


@pytest.fixture(autouse=True)
def _hermetic_publish_env():
    saved = {k: os.environ.get(k) for k in _LEAKY_ENV}
    for k in _LEAKY_ENV:
        os.environ.pop(k, None)
    # Force vocal isolation OFF for the unit suite: it DEFAULTS ON (the music transcription fix), but
    # transcribe_source would then shell real `demucs` on fixture audio — slow + non-hermetic. Tests
    # that exercise the isolation wiring opt back in explicitly (and monkeypatch isolate_vocals).
    iso_saved = os.environ.get("FANOPS_ISOLATE_VOCALS")
    os.environ["FANOPS_ISOLATE_VOCALS"] = "0"
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if iso_saved is None:
            os.environ.pop("FANOPS_ISOLATE_VOCALS", None)
        else:
            os.environ["FANOPS_ISOLATE_VOCALS"] = iso_saved
