# tests/conftest.py — hermetic env baseline for the unit suite.
#
# Before Brief 02, Config.__init__ called load_dotenv(self.root / ".env"); a Config() built with
# the default cwd root (as several tests do) loaded the OPERATOR's live repo .env into os.environ.
# After Brief 02, Config() is side-effect-free; load_dotenv runs once in cli.main() at process entry.
# Tests that invoke cli.main() may still hit that path — the leak class is the same: repo .env vars
# persisting in os.environ and silently flipping later tests. This fixture closes that isolation bug.
#
# Strategy: strip the publish-mode/credential vars at the START of every test and restore the real
# process environment AFTER. Tests that want a live backend still set it explicitly via monkeypatch
# (and get their own clean teardown). The suite no longer depends on what FANOPS_POSTER happens to be
# in the repo .env.
import os
import pytest
from fanops.errors import LockBusyError
from fanops.ledger import Ledger
from fanops.settings import BOOL_ENV_FIELDS


def ledger_lock_is_free(cfg) -> bool:
    """True iff the ledger store write lock can be acquired right now (i.e. NOT held). Used inside an
    injected network/subprocess closure to assert the lock is NOT held during slow work."""
    try:
        with Ledger.load(cfg)._store.lock(timeout=0.01):
            pass
        return True
    except LockBusyError:
        return False

# Vars the repo .env can leak that change publish/auth behavior — neutralized per test so a live .env
# never poisons a unit test. (POSTIZ_URL/POSTIZ_API_KEY ride along so a leaked URL can't 'configure'
# a backend the test didn't ask for.)
# FANOPS_HOOK_JUDGE rides along: it DEFAULTS ON (v2), so a dev's repo .env carrying =off would
# silently flip the critic OFF for every test that doesn't set it explicitly (the inverse of the
# FANOPS_POSTER leak). Stripping it makes each test see the CODE default; opt-out tests set it via
# monkeypatch and get clean teardown.
# META_GRAPH_TOKEN/META_IG_USER_ID/META_GRAPH_URL ride along (insights / deferred Graph hashtag).
# FANOPS_IG_SCRAPE_USER/PASSWORD ride along: a scrape login leaking into the session makes
# refresh_store open a REAL instagrapi client (network/login, flaky/CI-breaking).
# Stripping them makes every test see the no-scrape abort path; measurement tests inject scrape_client.
# FANOPS_CONCURRENT_SOURCES/FANOPS_CONCURRENT_WORKERS ride along (parallel-source pipeline): the
# concurrency flag DEFAULTS OFF (the byte-identical contract), so an operator's repo .env carrying
# =1 leaking into the session would silently flip every test onto the pooled path (and the worker
# count along with it). Stripping them makes each test see the OFF default; the concurrent tests
# set them explicitly via monkeypatch and get clean teardown.
#
# EVERY registered boolean flag is scrubbed, and that half of the list is DERIVED, not typed: a flag
# whose leak flips a code default is exactly what `settings.BOOL_ENV_FIELDS` enumerates, so a newly
# registered flag is hermetic on the commit that registers it instead of on the commit where someone
# notices. `_NON_FLAG_LEAKY` below is the remainder — vars that are not registered boolean flags
# (credentials, tuning numbers, and the four read directly by config.py with no Settings field).
_NON_FLAG_LEAKY = ("FANOPS_ROOT", "FANOPS_POSTER", "BLOTATO_API_KEY", "POSTIZ_API_KEY", "POSTIZ_URL", "FANOPS_MEDIA_PUBLIC_BASE",
              "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "FANOPS_HOOK_JUDGE",
              "FANOPS_RESPONDER",   # llm-only validate-or-refuse switch — must not leak across tests/CI
              # LLM transport/model: the operator persists FANOPS_LLM_TRANSPORT=cursor (+ an optional
              # FANOPS_LLM_MODEL) to the repo .env — must not leak into the dispatch-default/AUTO tests.
              "FANOPS_LLM_TRANSPORT", "FANOPS_LLM_MODEL",
              "META_GRAPH_TOKEN", "META_IG_USER_ID", "FANOPS_CORPUS_TARGET", "FANOPS_HASHTAG_SCRAPE_TRY_CAP", "FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", "FANOPS_IG_SCRAPE_USER", "FANOPS_IG_SCRAPE_PASSWORD", "META_GRAPH_URL",
              "FANOPS_GC_KEEP_DAYS",   # content-lifecycle Phase 3: a repo .env value must not leak into the gc-window tests
              "FANOPS_CONCURRENT_SOURCES", "FANOPS_CONCURRENT_WORKERS",
              # FANOPS_CREATIVE_VARIATION is a persona/learning flag with NO Settings field (config.py reads
              # it directly), so BOOL_ENV_FIELDS cannot carry it — once the operator persists =1 to the repo
              # .env (the supported "system default") it must not leak into tests that assume the code default.
              "FANOPS_CREATIVE_VARIATION",
              # S03 source sharding: a repo .env value must not leak into tests that assume the code default.
              "FANOPS_SOURCE_SHARD_MIN",
              # bringup: the `fanops up` on-demand-script path override — a repo .env value must not leak
              # into the bring-up tests that assert the DEFAULT $HOME/postiz-selfhost path when unset.
              "FANOPS_POSTIZ_ONDEMAND",
              # FANOPS_AUTO_ADOPT is now a registered BoolEnv (Settings) -> auto-scrubbed via BOOL_ENV_FIELDS
              # (DEFAULTS ON; a repo .env =0/=1 must not leak into test_run_loop.py's `run --loop`).
              # MOL-732: the operator tz decides which CALENDAR DAY (and hour) a stamp buckets into —
              # timeutil.operator_local_day / publish_buckets. The operator's live .env DOES set it,
              # set it explicitly via monkeypatch (test_operator_timezone_cadence_window, test_studio_views,
              # test_bulk_approve_spread, test_studio_actions, test_home_rebuild) and are unaffected.
              "FANOPS_OPERATOR_TZ")
_LEAKY_ENV = tuple(dict.fromkeys(BOOL_ENV_FIELDS + _NON_FLAG_LEAKY))


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
def _no_real_publish_sleep(monkeypatch):
    """The publish throttle (post/run.py `_publish_throttle_wait`, ~60/postiz_publish_per_min s between posts)
    and the publish retry-backoff call a REAL time.sleep. Any test that drives the live publish path without
    stubbing it stalls the whole suite for many seconds (the 48%-stall footgun). Neutralize the WAIT globally
    here — the throttle LOGIC still runs (per_min unchanged), only the wall-clock sleep is a no-op. A test
    that wants to assert the wait duration re-patches run._sleep to capture it."""
    monkeypatch.setattr("fanops.post.run._sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _hermetic_publish_env():
    # `saved` covers the two force-set vars below too: both are registered bool flags, so BOOL_ENV_FIELDS
    # puts them in _LEAKY_ENV and the restore loop puts the operator's real value back. They used to need
    # their own save/restore pair because the hand-typed list happened not to name FANOPS_ISOLATE_VOCALS.
    saved = {k: os.environ.get(k) for k in _LEAKY_ENV}
    for k in _LEAKY_ENV:
        os.environ.pop(k, None)
    # Force vocal isolation OFF for the unit suite: it DEFAULTS ON (the music transcription fix), but
    # transcribe_source would then shell real `demucs` on fixture audio — slow + non-hermetic. Tests
    # that exercise the isolation wiring opt back in explicitly (and monkeypatch isolate_vocals).
    os.environ["FANOPS_ISOLATE_VOCALS"] = "0"
    # Force transcript-caption burn OFF for the unit suite: burn_subs DEFAULTS ON, but most clip tests
    # isolate reframe/fingerprint/hook wiring and must not write .ass files. Tests that exercise subs
    # opt back in explicitly (monkeypatch delenv/setenv burn_subs).
    os.environ["FANOPS_BURN_SUBS"] = "0"
    # FANOPS_RESPONDER is scrubbed above (it is in _LEAKY_ENV), so it resolves to the production default
    # 'llm'. Gates are answered ONLY by the LLM now (the no-op ManualResponder was retired) and 'manual'
    # is a HARD REFUSE, so the suite can no longer pin it to dodge the preflight — the LLM is MOCKED
    # instead by the _hermetic_llm fixture below (CLI present + default model raises a transient error).
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _hermetic_llm(monkeypatch):
    """Gates are answered ONLY by the LLM now (the manual responder was retired), so the unit suite can no
    longer pin FANOPS_RESPONDER=manual to dodge the preflight — that value is a HARD REFUSE. Instead the
    suite MOCKS the LLM two ways: (1) make the LLM CLI resolvable so doctor/preflight pass without a real
    install (no real binary is ever executed), and (2) make the responder's `claude -p` seam
    (fanops.responder.claude_json_meta) raise a TRANSIENT error so an incidental default answer_pending
    leaves every gate pending WITHOUT bumping the deterministic ceiling (the generic-Exception branch) —
    the same 'nothing gets answered' no-op the old ManualResponder gave, via the REAL llm code path.
    Tests that inject their own model bypass (2); tests that exercise the default model re-patch
    fanops.responder.claude_json_meta themselves (their patch wins, applied after this autouse fixture);
    tests that need the CLI genuinely ABSENT (preflight/doctor failure paths) monkeypatch shutil.which
    back to return None for the binary."""
    import shutil
    _real_which = shutil.which
    def _which(name, *a, **k):
        if name in ("claude", "cursor-agent"):
            return f"/usr/bin/{name}"                     # pretend the LLM CLI is installed — never actually run
        return _real_which(name, *a, **k)
    monkeypatch.setattr("shutil.which", _which)
    def _no_llm(*_a, **_k):
        raise RuntimeError("hermetic unit suite: no live LLM (inject a model, or patch claude_json_meta)")
    monkeypatch.setattr("fanops.responder.claude_json_meta", _no_llm)


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
