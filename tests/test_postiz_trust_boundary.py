# tests/test_postiz_trust_boundary.py — Plan R5 (defects D12-D15): be HONEST about external state.
# D12: go_live's success ActionResult explains FANOPS_POSTER is a legacy bridge (per-channel accounts.json
#      routing is the source of truth) so the operator who reads .env doesn't panic at FANOPS_POSTER=dryrun.
# D13: a typed Postiz health probe (past nginx to the backend) + a Studio banner that fires ONLY when the
#      probe is unhealthy AND a channel actually routes to postiz. D14: the mastra_ai_spans workaround doc.
# D15 (Task 6): FANOPS_LIVE=1 with a typo'd FANOPS_POSTER and no live per-channel backend is SILENTLY
#      accepted today — is_live shows LIVE while every publish halts in queued. Render a HALF-LIVE warning
#      (never the plain LIVE banner) + a doctor finding; a genuinely-live config is byte-identical to today.
# ALL HTTP is mocked (never a real Postiz/Meta endpoint); env via monkeypatch (raising=False); accounts via tmp_path.
import json
import os
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source
from fanops.studio import golive
from fanops.studio import views
from fanops.studio import views_common
from fanops.post.postiz import postiz_health_probe, postiz_check_auth


class _R:
    """Minimal mock requests.Response (mirrors tests/test_postiz.py::_R)."""
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


# os.environ-leak guard: go_live DIRECT-writes FANOPS_LIVE; restore the baseline so a flip never leaks
# into a later test (delenv of an already-absent key registers no restoration — the documented gotcha).
_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "FANOPS_RESPONDER")
_BASELINE = {k: os.environ.get(k) for k in _KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    # The Postiz health banner caches the probe ~30s keyed by postiz_url (process-local). Two tests here
    # share the same URL with opposite mocked backend states, so clear the cache per test for isolation
    # (the TTL is a production concern, not a test one).
    views_common._postiz_health_cache.clear()
    yield
    views_common._postiz_health_cache.clear()
    for k, v in _BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def _seed_due_postiz_post(cfg, *, pid="due_p1", when="2020-01-01T12:00:00Z", account="@ig", account_id="1"):
    with Ledger.transaction(cfg) as led:
        if not led.sources:
            led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
            led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
            led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id=account_id,
                          platform=Platform.instagram, caption="fire", state=PostState.queued,
                          scheduled_time=when, public_url="dryrun://clip_1"))


# ------------------------------------------------------------------ D12: routing_source detail ----
def test_go_live_success_detail_includes_legacy_poster_note(tmp_path, monkeypatch):
    # A live channel via the legacy FANOPS_POSTER bridge; go_live succeeds and its success detail must
    # name FANOPS_POSTER as a legacy bridge and point at accounts.json as the routing source of truth.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is True
    rs = res.detail.get("routing_source")
    assert rs and "FANOPS_POSTER" in rs and "legacy" in rs.lower()
    assert "accounts.json" in rs                                   # names the real source of truth


# ------------------------------------------------------------------ D13a: typed health probe ----
def test_postiz_health_probe_returns_false_when_api_returns_502(tmp_path, monkeypatch, mocker):
    # The nginx-only container health lie: nginx is up (a real HTTP response comes back) but the Node
    # backend is crash-looping -> 502. The probe must report unhealthy AND carry the status code + a hint.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    healthy, status_code, hint = postiz_health_probe(cfg)
    assert healthy is False
    assert status_code == 502
    assert hint                                                   # a non-empty operator hint (the WHY)


def test_postiz_health_probe_returns_true_on_200(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(200, []))
    healthy, status_code, hint = postiz_health_probe(cfg)
    assert healthy is True
    assert status_code == 200


def test_postiz_check_auth_still_a_bool_wrapper(tmp_path, monkeypatch, mocker):
    # Backward compat: postiz_check_auth stays a bool (.healthy) wrapper over the new typed probe.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(200, []))
    assert postiz_check_auth(cfg) is True
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    assert postiz_check_auth(cfg) is False


def test_postiz_health_probe_401_reports_unhealthy_with_status(tmp_path, monkeypatch, mocker):
    # A 401 is a real backend answer (auth fault, not a crash) — the probe reports unhealthy + 401 rather
    # than raising, so the banner surface never 500s. (postiz_check_auth keeps its raise-on-401 contract.)
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(401, text="unauthorized"))
    healthy, status_code, hint = postiz_health_probe(cfg)
    assert healthy is False and status_code == 401
    with pytest.raises(PostizAuthError):
        postiz_check_auth(cfg)                                    # legacy contract preserved


# ------------------------------------------------------------------ D13b: Studio Postiz-down banner ----
def test_postiz_health_for_banner_absent_when_no_channel_routes_to_postiz(tmp_path, monkeypatch, mocker):
    # Postiz is down, but NO channel routes to postiz (a pure-Zernio deployment) — the banner must NOT show
    # (a Postiz outage is irrelevant to a deployment that doesn't publish through it).
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    banner = views_common.postiz_health_for_banner(cfg)
    assert banner["show"] is False


def test_studio_renders_postiz_down_banner_when_unhealthy(tmp_path, monkeypatch, mocker):
    # Postiz is down (502) AND a due postiz post is waiting -> real stall: danger banner names the status code
    # and points at docs/POSTIZ_OPS.md. Assert on the read-model that base.html renders (build_system_strip).
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    _seed_due_postiz_post(cfg)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    strip = views.build_system_strip(cfg)
    pd = strip.get("postiz_down")
    assert pd and pd.get("show") is True and pd.get("danger") is True
    assert "502" in str(pd.get("status"))
    assert "stalled" in pd.get("hint", "").lower()
    assert "POSTIZ_OPS.md" in pd.get("hint", "")


# ------------------------------------------------------------------ MOL-124: idle-by-design vs real stall ----
def test_postiz_banner_muted_idle_when_down_and_no_due_postiz_posts(tmp_path, monkeypatch, mocker):
    # Reaper-stopped Postiz is expected cold state — probe down with zero due postiz posts must NOT cry wolf.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    banner = views_common.postiz_health_for_banner(cfg)
    assert banner.get("danger") is not True
    assert "stalled" not in (banner.get("hint") or "").lower()


def test_postiz_banner_danger_when_down_and_due_postiz_post(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    _seed_due_postiz_post(cfg)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    banner = views_common.postiz_health_for_banner(cfg)
    assert banner.get("show") is True and banner.get("danger") is True
    assert "stalled" in (banner.get("hint") or "").lower()


def test_postiz_down_banner_absent_when_healthy(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(200, []))
    strip = views.build_system_strip(cfg)
    assert strip.get("postiz_down", {}).get("show") is False


# ------------------------------------------------------------------ D14: POSTIZ_OPS.md ----
def _docs_dir() -> Path:
    # repo root = two levels up from this test file (tests/ -> repo).
    return Path(__file__).resolve().parent.parent / "docs"


def test_docs_postiz_ops_md_exists_and_documents_workaround():
    p = _docs_dir() / "POSTIZ_OPS.md"
    assert p.exists(), f"{p} must exist"
    body = p.read_text()
    assert "DROP TABLE mastra_ai_spans CASCADE" in body           # the exact workaround command
    assert "MASTRA_STORAGE_PG_ALTER_TABLE_FAILED" in body         # the diagnostic grep symptom
    assert "docker restart postiz" in body                        # the restart step
    assert "nginx" in body.lower()                                # why the container's "healthy" lies


# ------------------------------------------------------------------ D15 (Task 6): half-live coherence ----
def test_live_route_exists_true_for_genuine_live_channel(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    assert cfg.live_route_exists is True


def test_live_route_exists_false_for_typod_poster_and_no_backend(tmp_path, monkeypatch):
    # FANOPS_LIVE=1 + a typo'd FANOPS_POSTER (resolves to dryrun via W4) + no live per-channel backend.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postizz")   # typo -> dryrun
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    assert cfg.is_live is True                                    # the flag is set...
    assert cfg.live_route_exists is False                         # ...but nothing actually routes live


def test_half_live_config_renders_warning_not_live_banner(tmp_path, monkeypatch):
    # The core D15 assertion: FANOPS_LIVE=1, typo'd FANOPS_POSTER, no live backend -> the mode surface is
    # the HALF-LIVE WARNING state, NOT the plain LIVE banner.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postizz")   # typo -> dryrun
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    strip = views.build_system_strip(cfg)
    assert strip.get("half_live") is True                        # the distinct warning state
    hint = strip.get("half_live_hint", "")
    assert "postizz" in hint                                     # names the raw ignored value
    assert "nothing routes live" in hint.lower()


def test_genuine_live_config_is_not_half_live(tmp_path, monkeypatch):
    # A genuinely-live config renders LIVE byte-identically to today: is_live True, half_live absent/False.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    strip = views.build_system_strip(cfg)
    assert strip["is_live"] is True
    assert strip.get("half_live") is False


def test_dryrun_config_is_not_half_live(tmp_path, monkeypatch):
    # Not-live is never half-live (half-live is strictly is_live AND no live route).
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    strip = views.build_system_strip(cfg)
    assert strip["is_live"] is False
    assert strip.get("half_live") is False


def test_doctor_flags_half_live_config(tmp_path, monkeypatch):
    from fanops.doctor import doctor_report
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postizz")   # typo -> dryrun
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    rep = doctor_report(cfg)
    labels = [c["label"] for c in rep["checks"]]
    # a coherence check exists and FAILS (ok False) for the half-live config
    coh = [c for c in rep["checks"] if "live" in c["label"].lower() and "route" in c["label"].lower()]
    assert coh, f"expected a live-route coherence check; got {labels}"
    assert coh[0]["ok"] is False
    assert coh[0]["hint"]                                         # names the fix


def test_doctor_does_not_flag_genuine_live(tmp_path, monkeypatch):
    from fanops.doctor import doctor_report
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    rep = doctor_report(cfg)
    coh = [c for c in rep["checks"] if "live" in c["label"].lower() and "route" in c["label"].lower()]
    assert coh and coh[0]["ok"] is True                          # genuine live passes the coherence check
