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
