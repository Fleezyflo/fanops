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
_LEAKY_ENV = ("FANOPS_POSTER", "BLOTATO_API_KEY", "POSTIZ_API_KEY", "POSTIZ_URL")


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
