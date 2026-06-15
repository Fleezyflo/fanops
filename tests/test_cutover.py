# tests/test_cutover.py — Phase 1: the live-cutover validation harness. All offline (injected
# network); the real auth/post/metrics steps are operator-run, never in CI.
import json
import pytest
from fanops.config import Config
from fanops.errors import CutoverError, BlotatoAuthError
from fanops import cutover


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


# ---- reconcile_fields (pure — the load-bearing _W vs live-row diff) ------------------------------
def test_reconcile_splits_scored_unweighted_absent():
    # live row returns saves+likes (both in _W) + bookmarks (live-only, _W ignores);
    # shares/retention/reach are weighted but absent here (dead weights to re-tune).
    rec = cutover.reconcile_fields({"saves": 5, "likes": 10, "bookmarks": 3})
    assert rec["scored"] == ["likes", "saves"]
    assert rec["present_unweighted"] == ["bookmarks"]
    assert rec["weighted_absent"] == ["reach", "retention", "shares"]

def test_reconcile_ignores_nonnumeric_values():
    rec = cutover.reconcile_fields({"saves": 5, "caption": "hi"})
    assert rec["scored"] == ["saves"] and "caption" not in rec["present_unweighted"]


# ---- build_cutover_payload / the hardcoded 2099 schedule ----------------------------------------
def test_cutover_payload_is_2099_twitter():
    p = cutover.build_cutover_payload("acct123")
    assert p["scheduledTime"] == "2099-01-01T00:00:00Z"
    assert p["post"]["accountId"] == "acct123" and p["post"]["content"]["platform"] == "twitter"


# ---- cutover_post guards (cannot fire accidentally) ---------------------------------------------
def test_post_refuses_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)         # default dryrun
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    with pytest.raises(CutoverError, match="dryrun"):
        cutover.cutover_post(Config(root=tmp_path), "acct", confirmed=True)

def test_post_refuses_without_confirm_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    with pytest.raises(CutoverError, match="refusing|THROWAWAY"):
        cutover.cutover_post(Config(root=tmp_path), "acct", confirmed=False)

def test_post_fires_and_saves_state_when_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    captured = {}
    def fake_post(url, **kw):
        captured["json"] = kw["json"]; return _R(201, {"postSubmissionId": "sub_LIVE_1"})
    out = cutover.cutover_post(cfg, "acct", confirmed=True, post=fake_post)
    assert out["submission_id"] == "sub_LIVE_1"
    assert captured["json"]["scheduledTime"] == "2099-01-01T00:00:00Z"
    state = json.loads(cfg.cutover_path.read_text())
    assert state["submission_id"] == "sub_LIVE_1"
    assert not cfg.ledger_path.exists()        # ISOLATION: the test post never entered the unit chain

def test_post_401_is_typed_auth_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    def fake_post(url, **kw): return _R(401, {"e": "denied key SENTINEL"}, text="denied key SENTINEL")
    with pytest.raises(BlotatoAuthError) as ei:
        cutover.cutover_post(Config(root=tmp_path), "acct", confirmed=True, post=fake_post)
    assert "SENTINEL" not in str(ei.value)


# ---- cutover_auth -------------------------------------------------------------------------------
def test_auth_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    out = cutover.cutover_auth(Config(root=tmp_path), get=lambda u, **kw: _R(200, []))
    assert out["ok"] is True and out["status_code"] == 200

def test_auth_401_typed(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    with pytest.raises(BlotatoAuthError):
        cutover.cutover_auth(Config(root=tmp_path), get=lambda u, **kw: _R(401, {}, text="x"))

def test_auth_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    with pytest.raises(CutoverError, match="BLOTATO_API_KEY"):
        cutover.cutover_auth(Config(root=tmp_path))


# ---- cutover_metrics ----------------------------------------------------------------------------
def test_metrics_reconciles_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    rows = [{"postSubmissionId": "sub_1", "metrics": {"saves": 9, "bookmarks": 2}}]
    out = cutover.cutover_metrics(cfg, "sub_1", list_posts=lambda w: rows)
    assert out["reconciliation"]["scored"] == ["saves"]
    assert out["reconciliation"]["present_unweighted"] == ["bookmarks"]
    state = json.loads(cfg.cutover_path.read_text())
    assert state["metrics_confirmed"] is True and state["metrics_row"]["saves"] == 9

def test_metrics_missing_row_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    with pytest.raises(CutoverError, match="no metrics row"):
        cutover.cutover_metrics(Config(root=tmp_path), "sub_X", list_posts=lambda w: [])


# ---- cutover_lift -------------------------------------------------------------------------------
def test_lift_computes_on_captured_row(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    cutover._save_state(cfg, {"metrics_row": {"saves": 10, "likes": 100}})
    out = cutover.cutover_lift(cfg, "sub_1")
    assert out["lift_score"] == 45.0          # 4.0*10 + 0.05*100

def test_lift_without_captured_row_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    with pytest.raises(CutoverError, match="metrics"):
        cutover.cutover_lift(Config(root=tmp_path), "sub_1")


# ---- CLI surface: the refuse path is a clean exit 2, not a traceback ----------------------------
def test_cli_cutover_post_refuses_dryrun_exit2(tmp_path, monkeypatch):
    from fanops.cli import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    assert main(["cutover", "post", "acct"]) == 2


# ---- M3: Postiz cutover — dispatch + the 4 steps (offline, injected network; the LIVE post is operator-run) ----
def _postiz_env(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)

def _integrations():
    from fanops.post.postiz import PostizIntegration
    return [PostizIntegration(id="ig_1", name="throwaway", platform="instagram")]

# Task 1 — dispatch by backend
def test_cutover_metrics_dispatches_postiz(tmp_path, monkeypatch):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    rows = [{"postSubmissionId": "pz1", "metrics": {"likes": 10, "shares": 2}, "_raw_labels": ["Likes", "Shares"]}]
    out = cutover.cutover_metrics(cfg, "pz1", list_posts=lambda w: rows)
    assert out["reconciliation"]["scored"] == ["likes", "shares"]
    state = json.loads(cfg.cutover_path.read_text())
    assert state["metrics_confirmed"] is True and state["backend"] == "postiz"

# Task 2 — Postiz auth
def test_postiz_auth_ok(tmp_path, monkeypatch, mocker):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_check_auth", return_value=True)
    out = cutover.cutover_auth(cfg)
    assert out["ok"] is True and out["backend"] == "postiz"

def test_postiz_auth_requires_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    with pytest.raises(CutoverError, match="POSTIZ_API_KEY"):
        cutover.cutover_auth(Config(root=tmp_path))

def test_postiz_auth_401_propagates(tmp_path, monkeypatch, mocker):
    from fanops.errors import PostizAuthError
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_check_auth", side_effect=PostizAuthError("401 — key withheld"))
    with pytest.raises(PostizAuthError):
        cutover.cutover_auth(cfg)

# Task 3 — Postiz post (confirmed 2099 throwaway; operator-selected integration; SECURITY surface)
def test_postiz_post_refuses_without_confirm(tmp_path, monkeypatch, mocker):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_list_integrations", return_value=_integrations())
    with pytest.raises(CutoverError, match="throwaway|confirm"):
        cutover.cutover_post(cfg, "ig_1", confirmed=False)

def test_postiz_post_refuses_unknown_integration(tmp_path, monkeypatch, mocker):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_list_integrations", return_value=_integrations())
    with pytest.raises(CutoverError, match="unknown postiz integration"):
        cutover.cutover_post(cfg, "NOT_MAPPED", confirmed=True)

def test_postiz_post_fires_and_saves_when_confirmed(tmp_path, monkeypatch, mocker):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_list_integrations", return_value=_integrations())
    captured = {}
    def fake_post(url, **kw): captured["json"] = kw["json"]; return _R(201, {"id": "pz_LIVE_1"})
    out = cutover.cutover_post(cfg, "ig_1", confirmed=True, post=fake_post)
    assert out["submission_id"] == "pz_LIVE_1"
    assert captured["json"]["date"] == "2099-01-01T00:00:00Z"                        # 2099 schedule, never near-now
    assert captured["json"]["posts"][0]["settings"]["__type"] == "instagram"          # platform DERIVED, not hardcoded
    state = json.loads(cfg.cutover_path.read_text())
    assert state["submission_id"] == "pz_LIVE_1" and state["backend"] == "postiz"
    assert not cfg.ledger_path.exists()                                               # ISOLATION: never the ledger

def test_postiz_post_401_redacted(tmp_path, monkeypatch, mocker):
    from fanops.errors import PostizAuthError
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.postiz.postiz_list_integrations", return_value=_integrations())
    def fake_post(url, **kw): return _R(401, {"e": "key SENTINEL"}, text="key SENTINEL")
    with pytest.raises(PostizAuthError) as ei:
        cutover.cutover_post(cfg, "ig_1", confirmed=True, post=fake_post)
    assert "SENTINEL" not in str(ei.value)

# Task 4 — Postiz metrics (real-label reconcile → confirmed field map)
def test_postiz_metrics_records_raw_labels_and_confirms(tmp_path, monkeypatch):
    _postiz_env(monkeypatch); cfg = Config(root=tmp_path)
    rows = [{"postSubmissionId": "pz1", "metrics": {"likes": 10, "shares": 2}, "_raw_labels": ["Likes", "Shares", "Saves"]}]
    out = cutover.cutover_metrics(cfg, "pz1", list_posts=lambda w: rows)
    assert out["postiz_labels"] == ["Likes", "Shares", "Saves"]                       # M3 records the RAW label set, no self-fetch
    state = json.loads(cfg.cutover_path.read_text())
    assert state["postiz_labels"] == ["Likes", "Shares", "Saves"] and state["label_map"]
    assert state["metrics_confirmed"] is True

def test_postiz_metrics_missing_row_says_postiz_not_blotato(tmp_path, monkeypatch):
    _postiz_env(monkeypatch)
    with pytest.raises(CutoverError, match="no metrics row") as ei:
        cutover.cutover_metrics(Config(root=tmp_path), "pzX", list_posts=lambda w: [])
    assert "Postiz" in str(ei.value) and "Blotato" not in str(ei.value)


def test_post_non_2xx_withholds_response_body(tmp_path, monkeypatch):
    # FIX 8: the only non-auth poster path that embedded the raw response body was
    # `raise CutoverError(f"blotato post {code}: {resp.text[:200]}")` — inconsistent with the
    # no-echo posture everywhere else (cutover_postiz withholds it). The body must NOT leak.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    def fake_post(url, **kw): return _R(500, {"e": "BODY_SENTINEL"}, text="BODY_SENTINEL details")
    with pytest.raises(CutoverError) as ei:
        cutover.cutover_post(Config(root=tmp_path), "acct", confirmed=True, post=fake_post)
    msg = str(ei.value)
    assert "BODY_SENTINEL" not in msg                         # raw body withheld
    assert "500" in msg and "withheld" in msg.lower()         # status kept, body explicitly withheld
