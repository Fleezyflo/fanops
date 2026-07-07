# tests/test_meta_graph_contract.py — the Meta Graph SHAPE CONTRACT, sourced from the real API.
#
# These tests are the answer to "stop guessing external shapes." Each records the VERBATIM Graph
# response into tests/cassettes/ (via pytest-recording/VCR.py) and asserts against what Graph ACTUALLY
# returns — not a hand-written classifier. To (re)capture the real shapes once against the live API:
#
#     source .venv/bin/activate
#     META_GRAPH_TOKEN=... META_IG_USER_ID=... \
#       python -m pytest tests/test_meta_graph_contract.py --record-mode=once -q
#
# The token is scrubbed to DUMMY before the cassette is written (see conftest.vcr_config), so the
# committed cassette carries the real response SHAPE with no secret. Thereafter the suite replays offline
# (record_mode "none"): if Meta changes a shape, the replay drifts from the code's expectation and these
# tests go red — which is exactly the drift we currently discover only in production.
#
# WHY THIS EXISTS (the bug that motivated it): the daemon logged `insights_blocked_scope` and doctor said
# "grant instagram_manage_insights" — but a direct probe proved the token HAS the scope and Graph returns
# full insights (HTTP 200). The false block came from `_is_scope_error` mapping ANY OAuthException/
# GraphMethodException to "missing scope". The `does_not_exist` cassette below captures the REAL body of
# the failing call (a `GraphMethodException` / code 100 / subcode 33 "does not exist" on a non-IG id) so
# the classifier's contract is pinned to reality: a does-not-exist error is NOT a scope refusal.
import pytest
from fanops.config import Config
from fanops.meta_graph import resolve_meta_creds, insights_metrics_for, _is_scope_error
import requests

# Replay matches on method+path only (see conftest.vcr_config match_on) so these pass with NO token,
# which is CI's condition — the token rides the query and is scrubbed to DUMMY, never in the match key.
pytestmark = pytest.mark.vcr


def _cfg() -> Config:
    # Config(root) — NOT Config.load() (there is no such classmethod). root/.env is operator truth
    # (config.py:92-94) and the .env lives at the REPO ROOT, so the default cwd root is correct; the
    # data dir is root/"MohFlow-FanOps" (self.base), NOT the root itself.
    return Config()


@pytest.mark.vcr
def test_media_insights_real_shape_is_a_data_list_of_named_values():
    """CONTRACT: a valid /{media}/insights call returns {"data": [{"name","values":[{"value":N}]}...]}.
    Recorded from a real published REELS media. Pins the exact keys media_insights() parses."""
    cfg = _cfg()
    creds = resolve_meta_creds(cfg)
    metrics = insights_metrics_for("REELS")
    assert metrics, "REELS must resolve to a non-empty metric set (else no request is ever built)"
    # A REAL published REELS media id (from the ledger) so the recording captures Graph's true 200 shape.
    # A placeholder id would 400 on record AND, on a replay-miss, VCR echoes the UNFILTERED request URI —
    # leaking the token into the error. A real id records a clean 200; thereafter this replays offline.
    media_id = "17915984967201344"
    resp = requests.get(f"{cfg.meta_graph_url}/{media_id}/insights",
                        params={"metric": ",".join(metrics), "access_token": creds.token}, timeout=20)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("data"), list) and body["data"], "insights success => non-empty data list"
    row = body["data"][0]
    assert "name" in row and isinstance(row.get("values"), list), "each row: a name + a values list"
    assert "value" in row["values"][0], "each values[0] carries the metric value"
    # and the classifier must NOT read a 200 success as a scope error
    assert _is_scope_error(body) is False


@pytest.mark.vcr
def test_does_not_exist_error_is_NOT_a_scope_error():
    """THE motivating bug, pinned to the real wire: a request for a non-Instagram id (e.g. a Postiz id)
    returns a `GraphMethodException` (code 100, subcode 33, "...does not exist..."). This is NOT a missing
    scope — `_is_scope_error` returning True on it is the false-block. This test records that real body and
    asserts the correct classification. It is EXPECTED-RED against the current `_is_scope_error` (which
    over-matches GraphMethodException); fixing the classifier to read the real error is the follow-on."""
    cfg = _cfg()
    creds = resolve_meta_creds(cfg)
    resp = requests.get(f"{cfg.meta_graph_url}/cmr0pc4qe0002oa74c714sk8n/insights",
                        params={"metric": "reach,views", "access_token": creds.token}, timeout=20)
    assert resp.status_code == 400
    body = resp.json()
    err = body["error"]
    # The real Graph shape for a bad object id — pinned so we never guess it again:
    assert err["type"] == "GraphMethodException"
    assert err["code"] == 100
    assert "does not exist" in err["message"]
    # A does-not-exist is a BAD-ID (transient/data) failure, categorically not a permission refusal.
    assert _is_scope_error(body) is False, (
        "REGRESSION TARGET: _is_scope_error must not map a GraphMethodException 'does not exist' to "
        "'missing instagram_manage_insights'. That false-positive is what froze IG insights.")


@pytest.mark.vcr
def test_debug_token_real_shape():
    """CONTRACT: /debug_token returns {"data": {"is_valid": bool, "scopes": [...], "expires_at": int}}.
    Pins the shape debug_token_expiry() and the scope-audit read against reality — including the presence
    of `instagram_manage_insights` in the granted scopes (the ground truth doctor should trust)."""
    cfg = _cfg()
    creds = resolve_meta_creds(cfg)
    resp = requests.get(f"{cfg.meta_graph_url}/debug_token",
                        params={"input_token": creds.token, "access_token": creds.token}, timeout=20)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("is_valid"), bool)
    assert isinstance(data.get("scopes"), list), "debug_token exposes the granted scope list"
    assert "expires_at" in data, "expires_at drives the near-expiry preflight"
