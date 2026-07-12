"""M4b — the Go-Live discover/adopt UI routes. `/golive/discover` lists every channel the connected
schedulers (Postiz + Zernio) already hold; `/golive/adopt` creates + maps the operator-ticked rows (and
routes them to their scheduler when confirmed). The backend (golive.discover_channels/adopt_channels) is
unit-tested in test_golive_discover.py; THESE prove the Flask wiring + the indexed form parsing.

os.environ-leak guard: the routes read POSTIZ/ZERNIO keys via the env; restore the baseline after each
test so a setenv never leaks into a later test (pytest-os-environ-leak-guard)."""
import json
import os
import re
import types
import pytest
from fanops.config import Config
from fanops.studio import golive

_ENV_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def _chan(cid, name, platform):
    # discover_channels only reads .id/.name/.platform off each provider-listed row.
    return types.SimpleNamespace(id=cid, name=name, platform=platform)


def test_discover_route_lists_connected_channels(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations", lambda c: [_chan("ig_1", "Mark", "instagram")])
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts", lambda c: [_chan("z_1", "llllllll", "tiktok")])
    r = _client(cfg).post("/golive/discover")
    assert r.status_code == 200
    body = r.data.decode()
    assert "llllllll" in body and "z_1" in body          # the Zernio channel is surfaced for adoption
    assert "ig_1" in body                                 # the Postiz channel too
    assert "Adopt" in body                                # the adopt form rendered


def test_discover_route_refused_without_a_connected_provider(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])   # neither POSTIZ nor ZERNIO key
    r = _client(cfg).post("/golive/discover")
    assert r.status_code == 200                           # htmx-swap-safe even on refusal
    assert b"connect Postiz or Zernio" in r.data


def test_adopt_route_creates_and_maps_ticked_row(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    r = _client(cfg).post("/golive/adopt", data={
        "adopt": "0", "provider__0": "zernio", "id__0": "z_1",
        "platform__0": "tiktok", "handle__0": "newtt", "persona__0": "bold burner"})
    assert r.status_code == 200
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "newtt")
    assert a["integrations"]["tiktok"] == "z_1"           # mapped
    assert a["persona"] == "bold burner"                  # persona seeded on creation
    assert "tiktok" not in a.get("backends", {})          # no confirm -> mapped but NOT routed (can't publish)


def test_adopt_route_routes_when_confirmed_with_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")            # creds present -> confirm can route
    r = _client(cfg).post("/golive/adopt", data={
        "adopt": "0", "provider__0": "zernio", "id__0": "z_1",
        "platform__0": "tiktok", "handle__0": "newtt", "confirm": "1"})
    assert r.status_code == 200
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "newtt")
    assert a["integrations"]["tiktok"] == "z_1"
    assert a["backends"]["tiktok"] == "zernio"            # confirmed + creds -> routed


def test_adopt_route_ignores_unticked_rows(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    # the row's hidden fields are present but the checkbox value 'adopt' is NOT submitted -> nothing adopted
    r = _client(cfg).post("/golive/adopt", data={
        "provider__0": "zernio", "id__0": "z_1", "platform__0": "tiktok", "handle__0": "skip"})
    assert r.status_code == 200
    assert json.loads(cfg.accounts_path.read_text())["accounts"] == []


def test_golive_health_route_renders_dependency_strip(tmp_path, monkeypatch):
    # Issue 1: /golive/health renders the live dependency strip. system_health is mocked (hermetic — no
    # real Docker/network); a DOWN dependency must be shown, not hidden.
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "daemon up"),
        health.DepHealth("postiz", False, "unreachable"),
        health.DepHealth("zernio", True, "reachable")])
    r = _client(cfg).get("/golive/health")
    assert r.status_code == 200
    body = r.data.decode()
    assert "docker" in body and "postiz" in body and "zernio" in body
    assert "unreachable" in body                          # the down dependency is surfaced, not buried


def test_golive_health_failing_dep_row_carries_loud_class(tmp_path, monkeypatch):
    # MOL-48: a failing system dependency must be visually LOUD, not styled identically to a passing
    # row. The failing <li> keeps class "err" (the CSS Tier-1 hook); prove the render still emits it
    # for a down dep so the studio.css .checks li.err solid-fill treatment lands on it.
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "daemon up"),
        health.DepHealth("postiz", False, "unreachable"),
        health.DepHealth("zernio", True, "reachable")])
    body = _client(cfg).get("/golive/health").data.decode()
    # the postiz row is the failing one — it must carry the loud err class.
    assert re.search(r'class="[^"]*\berr\b[^"]*"[^>]*>[^<]*postiz', body), \
        "failing postiz dependency row must carry the loud 'err' class"


def test_golive_health_emits_banner_alert_when_a_dependency_is_down(tmp_path, monkeypatch):
    # MOL-48 fix 2 + S10: a down Postiz with a DUE postiz-routed post is a real stall — promote to the
    # dep-alert banner. Parked-idle Postiz (reaper-stopped, zero due) must NOT raise this alert (see
    # test_truth_surfaces.py); here we seed the stall path so the banner contract stays covered.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "1", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="due_p1", parent_id="clip_1", account="ig", account_id="1", platform=Platform.instagram,
                          caption="fire", state=PostState.queued, scheduled_time="2020-01-01T12:00:00Z",
                          public_url="dryrun://clip_1"))
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "daemon up"),
        health.DepHealth("postiz", False, "unreachable"),
        health.DepHealth("zernio", True, "reachable")])
    body = _client(cfg).get("/golive/health").data.decode()
    assert "dep-alert" in body, "a real Postiz stall must raise a banner-level alert above the strip"
    assert "postiz" in body.split("dep-alert", 1)[1][:200], "the alert must name the down dependency (postiz)"
    assert "cannot ship" in body.lower()


def test_golive_health_no_banner_when_all_deps_up(tmp_path, monkeypatch):
    # the banner is failure-only: all-green must NOT raise it (no false alarm).
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "daemon up"),
        health.DepHealth("postiz", True, "reachable"),
        health.DepHealth("zernio", True, "reachable")])
    body = _client(cfg).get("/golive/health").data.decode()
    assert "dep-alert" not in body, "all-green health must not raise a dependency alert banner"
