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
_LEAKY_ENV = ("FANOPS_POSTER", "BLOTATO_API_KEY", "POSTIZ_API_KEY", "POSTIZ_URL", "FANOPS_MEDIA_PUBLIC_BASE",
              "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "FANOPS_HOOK_JUDGE",
              "FANOPS_RESPONDER",   # defaults to llm when `claude` is on PATH — must not leak across tests/CI
              "META_GRAPH_TOKEN", "META_IG_USER_ID", "FANOPS_HASHTAG_TRENDS", "META_GRAPH_URL",
              "FANOPS_GC_KEEP_DAYS",   # content-lifecycle Phase 3: a repo .env value must not leak into the gc-window tests
              "FANOPS_CONCURRENT_SOURCES", "FANOPS_CONCURRENT_WORKERS",
              # persona/learning behavior flags (default OFF): once the operator persists e.g.
              # FANOPS_CREATIVE_VARIATION=1 to the repo .env (the supported "system default"), it must not
              # leak into tests that assume the code default — same class as FANOPS_HOOK_JUDGE above.
              "FANOPS_CREATIVE_VARIATION", "FANOPS_VARIANT_LEARNING", "FANOPS_P4_DIM_BIAS",
              # Account-First Studio casting (Face 3): a repo .env value must not leak into tests that assume
              # the code default (same class as FANOPS_CREATIVE_VARIATION above).
              "FANOPS_ACCOUNT_CASTING")


def pytest_configure(config):
    # #13: studio tests use pytest.importorskip("flask"), so a flask-less interpreter SKIPS them — fine
    # LOCALLY, but it silently false-greens the whole studio surface for anyone running bare `pytest`
    # without the [studio] extra. CI (and a strict local run) sets FANOPS_REQUIRE_STUDIO=1, which turns a
    # missing flask into a hard SESSION ABORT — the same skip→fail intent as FANOPS_REQUIRE_E2E for the
    # real-tooling suite, implemented here as a collection-time precondition (not a per-test guard).
    if os.getenv("FANOPS_REQUIRE_STUDIO") == "1":
        try:
            import flask  # noqa: F401
        except ImportError:
            pytest.exit("FANOPS_REQUIRE_STUDIO=1 but flask is absent — run: pip install -e '.[dev,studio]'", returncode=1)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    from tests._require_e2e import require_e2e, integration_skip_failure_longrepr, skip_reason_from_report
    outcome = yield
    rep = outcome.get_result()
    if not require_e2e():
        return
    if "integration" not in item.keywords:
        return
    if not rep.skipped or getattr(rep, "wasxfail", None):
        return
    reason = skip_reason_from_report(rep)
    rep.outcome = "failed"
    rep.longrepr = integration_skip_failure_longrepr(call.when, reason)


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


# ── VCR: source external API shapes from the REAL call, never a guess ──────────────────────────────
# pytest-recording/VCR.py records the verbatim request+response of every external HTTP call into a
# cassette (tests/cassettes/), then replays it. The recorded body IS the contract — Meta/Postiz/Zernio's
# actual shape, not a hand-written classifier. First capture: `pytest --record-mode=once <test>` (hits the
# live endpoint once). Thereafter tests replay the cassette offline. Secrets NEVER hit disk: the token
# (access_token query param + Authorization header) and R2 creds are scrubbed to DUMMY before the cassette
# is written, so cassettes are safe to commit and version alongside the code they pin.
@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_query_parameters": [("access_token", "DUMMY"), ("input_token", "DUMMY")],
        "filter_headers": [("authorization", "DUMMY"), ("Authorization", "DUMMY")],
        "filter_post_data_parameters": [("access_token", "DUMMY")],
        # MATCH on method + path ONLY — deliberately NOT on the query string. The token rides the query
        # (access_token/input_token); it is scrubbed to DUMMY on write, but a replay in CI carries a
        # DIFFERENT (or absent) token, so matching on query would never match the recorded DUMMY and every
        # replay would miss (the CI `unit` failure: "Matchers failed: query"). Path+method uniquely
        # identifies each recorded Graph edge here, so this is exact without leaking the secret into the key.
        "match_on": ["method", "path"],
        # record_mode is NOT pinned here — it is driven by the CLI `--record-mode` flag (default "none"
        # via pytest-recording, so a missing cassette is an error, not a silent live call). Pinning it to
        # "none" here would (a) override --record-mode=once so recording never happens, and (b) on a
        # replay-miss VCR raises with the UNFILTERED request URI — leaking the real token into the error
        # (filter_query_parameters only scrubs what is WRITTEN to a cassette, not a miss-error). Leaving it
        # unpinned lets `--record-mode=once` actually record, and normal runs still default to none.
        "decode_compressed_response": True,
    }
