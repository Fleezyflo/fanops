# tests/test_studio_run.py — Studio as pipeline DRIVER: ingest/advance/pull from the browser through
# the same lock-safe paths the CLI uses, so the operator never needs the terminal.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.studio import views, actions


def _src_in_inbox(cfg, mocker, name="a.mp4"):
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / name).write_bytes(b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))


# ---- actions.run_ingest ----
def test_run_ingest_catalogues_inbox(tmp_path, mocker):
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg)
    assert res.ok and res.detail["sources"] == 1
    assert len(Ledger.load(cfg).sources) == 1

def test_run_ingest_wraps_toolchain_error(tmp_path, mocker):
    # ffprobe absent -> ingest raises ToolchainMissingError; Studio must surface a clean error, not 500.
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / "a.mp4").write_bytes(b"V")
    def absent(cmd, **kw): raise FileNotFoundError(2, "no", cmd[0])
    mocker.patch("fanops.ingest.subprocess.run", side_effect=absent)
    res = actions.run_ingest(cfg)
    assert not res.ok and "ffprobe" in (res.error or "")


# ---- actions.run_advance ----
def test_run_advance_returns_summary(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.run_advance(cfg)
    assert res.ok and "sources" in res.detail and "awaiting" in res.detail

def test_run_advance_live_backend_requires_confirm(tmp_path, monkeypatch):
    # Track C: a pass on a LIVE backend publishes to real accounts — the Run button must require an
    # explicit confirm, never fire on a stray click.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    res = actions.run_advance(Config(root=tmp_path), confirmed=False)
    assert not res.ok and "confirm" in (res.error or "").lower()

def test_run_advance_dryrun_needs_no_confirm(tmp_path, monkeypatch):
    # dryrun publishes nothing, so no confirm gate — the offline flow stays one click.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert actions.run_advance(Config(root=tmp_path), confirmed=False).ok

def test_run_advance_blocks_on_invalid_accounts(tmp_path):
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts":
        [{"handle": "@x", "account_id": "", "platforms": ["instagram"], "status": "active"}]}))
    res = actions.run_advance(cfg)
    assert not res.ok and "account" in (res.error or "").lower()


def test_run_advance_postiz_auth_names_postiz_key(tmp_path, monkeypatch):
    # ecc holistic audit GAP 2: a PostizAuthError on a postiz backend must surface FATAL + POSTIZ_API_KEY,
    # not degrade to a generic "advance failed" via the BlotatoAuthError-only arm.
    from fanops.errors import PostizAuthError
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    def boom(c, *, base_time): raise PostizAuthError("Postiz 401 (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_advance(Config(root=tmp_path), confirmed=True)
    assert not res.ok and "FATAL" in res.error and "POSTIZ_API_KEY" in res.error

def test_run_advance_surfaces_fatal_auth(tmp_path, monkeypatch):
    # ecc:python-review HIGH: a fatal BlotatoAuthError (bad key) must surface as FATAL, not be demoted
    # to a soft "advance failed" by the broad except. advance's own txn already rolled back.
    from fanops.errors import BlotatoAuthError
    def boom(c, *, base_time): raise BlotatoAuthError("Blotato 401 unauthorized (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_advance(Config(root=tmp_path))
    assert not res.ok and "FATAL" in res.error and "BLOTATO_API_KEY" in res.error


# ---- actions.run_prepare (auto-prepare: answer gates via the responder + advance until stable) ----
def test_run_prepare_answers_gates_and_advances(tmp_path, monkeypatch):
    # The review-first behavior (milestone 1): ONE action answers every moment/caption gate via the
    # responder AND advances — so the operator NEVER hand-writes a caption. (run_advance does a bare
    # advance that CREATES gates and leaves them pending in the Gates tab — the manual headache.)
    cfg = Config(root=tmp_path)
    calls = {"answer": 0, "advance": 0}
    class FakeResp:
        def answer_pending(self, c):
            calls["answer"] += 1; return 1
    monkeypatch.setattr("fanops.responder.get_responder", lambda c: FakeResp())
    def fake_advance(c, *, base_time):
        calls["advance"] += 1
        return {"sources": 0, "awaiting": {"moments": 0, "captions": 0}}
    monkeypatch.setattr("fanops.pipeline.advance", fake_advance)
    res = actions.run_prepare(cfg)
    assert res.ok
    assert calls["answer"] >= 1 and calls["advance"] >= 1        # answered gates AND advanced
    assert res.detail["awaiting"] == {"moments": 0, "captions": 0}

def test_run_prepare_loops_until_no_gate_remains(tmp_path, monkeypatch):
    # First advance still shows a pending gate; the responder answers it; the loop runs again until clear.
    cfg = Config(root=tmp_path)
    seq = iter([{"sources": 1, "awaiting": {"moments": 1, "captions": 0}},
                {"sources": 1, "awaiting": {"moments": 0, "captions": 0}}])
    monkeypatch.setattr("fanops.pipeline.advance", lambda c, *, base_time: next(seq))
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 1})())
    res = actions.run_prepare(cfg)
    assert res.ok and res.detail["awaiting"]["moments"] == 0

def test_run_prepare_cap_hit_in_llm_mode_surfaces_incomplete(tmp_path, monkeypatch):
    # If the responder never drains the gates (malformed answers / gates regenerating), the 10-pass
    # cap is hit with gates still pending. In llm mode that's a FAILURE to surface, not a green
    # "prepared" the operator would wrongly trust (ecc audit: code+python MEDIUM).
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.pipeline.advance",
                        lambda c, *, base_time: {"sources": 1, "awaiting": {"moments": 1, "captions": 0}})
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    res = actions.run_prepare(cfg)
    assert res.ok is False and "did not finish" in res.error
    assert res.detail["awaiting"]["moments"] == 1               # the last summary is still attached

def test_run_prepare_manual_mode_leaves_gates_ok(tmp_path, monkeypatch):
    # In MANUAL mode the responder writes nothing, so gates remaining after the loop is EXPECTED
    # (they wait in the Gates tab) — still ok=True, not a failure.
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)       # manual
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.pipeline.advance",
                        lambda c, *, base_time: {"sources": 1, "awaiting": {"moments": 1, "captions": 0}})
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    res = actions.run_prepare(cfg)
    assert res.ok is True                                        # manual: gates pending is normal

def test_run_prepare_live_backend_requires_confirm(tmp_path, monkeypatch):
    # A prepare pass crossposts/publishes due posts on a live backend -> same confirm guard as advance.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    res = actions.run_prepare(Config(root=tmp_path), confirmed=False)
    assert not res.ok and "confirm" in (res.error or "").lower()

def test_run_prepare_surfaces_fatal_auth(tmp_path, monkeypatch):
    # A fatal auth failure during a prepare pass surfaces FATAL + the right key, not a soft "failed".
    from fanops.errors import BlotatoAuthError
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    def boom(c, *, base_time): raise BlotatoAuthError("Blotato 401 (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_prepare(Config(root=tmp_path))
    assert not res.ok and "FATAL" in res.error and "BLOTATO_API_KEY" in res.error

def test_run_prepare_route(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    monkeypatch.setattr("fanops.pipeline.advance",
                        lambda c, *, base_time: {"sources": 0, "awaiting": {"moments": 0, "captions": 0}})
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().post("/run/prepare")
    assert r.status_code == 200

def test_run_route_shows_prepare_button(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().get("/run")
    assert b"Prepare" in r.data


# ---- actions.run_pull ----
def test_run_pull_rejects_non_http_url(tmp_path):
    res = actions.run_pull(Config(root=tmp_path), "not-a-url")
    assert not res.ok and "http" in (res.error or "").lower()


# ---- views.pipeline_status ----
def test_pipeline_status_counts(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))
    st = views.pipeline_status(cfg)
    assert st["sources"] == 1 and "pending_moments" in st and st["backend"] == "dryrun"


# ---- Flask wiring ----
def test_run_route_renders(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().get("/run")
    assert r.status_code == 200 and b"Ingest" in r.data

def test_run_ingest_route_drives_ingest(tmp_path, mocker):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/run/ingest")
    assert r.status_code == 200
    assert len(Ledger.load(cfg).sources) == 1

def test_run_advance_route(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().post("/run/advance")
    assert r.status_code == 200
