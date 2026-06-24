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

def test_run_ingest_with_batch_name_mints_batch_and_stamps_source(tmp_path, mocker):
    # A non-blank batch_name mints a named, account-targeted Batch in the SAME transaction; the catalogued
    # source carries its id and the detail reports the batch.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg, batch_name="  Launch week  ", target_accounts=["@a", "@a", ""])
    assert res.ok and res.detail["sources"] == 1
    led = Ledger.load(cfg)
    assert len(led.batches) == 1
    b = next(iter(led.batches.values()))
    assert b.name == "Launch week" and b.target_accounts == ["@a"]    # stripped + deduped + blank-dropped
    assert res.detail["batch"] == "Launch week" and res.detail["batch_id"] == b.id
    assert next(iter(led.sources.values())).batch_id == b.id          # source stamped under the batch

def test_run_ingest_blank_batch_name_is_byte_identical(tmp_path, mocker):
    # Blank batch_name => today's ungrouped ingest: no batch minted, source.batch_id None.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg, batch_name="   ")
    assert res.ok and "batch" not in res.detail
    led = Ledger.load(cfg)
    assert len(led.batches) == 0 and next(iter(led.sources.values())).batch_id is None

def _seed_accounts(cfg, handles):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "x", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def test_run_ingest_zero_target_bubbles_warning(tmp_path, mocker):
    # Face 1-fu (T4): a batch targeting a handle that is NOT active still mints (advisory, not fatal) but
    # surfaces detail["warnings"] — so the operator isn't left with a silent zero-post run downstream.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["@a"])
    res = actions.run_ingest(cfg, batch_name="Ghost run", target_accounts=["ghost"])
    assert res.ok and res.detail.get("warnings") and "ghost" in res.detail["warnings"][0]
    b = next(iter(Ledger.load(cfg).batches.values()))
    assert "ghost" in (b.error_reason or "")            # the advisory is persisted on the batch too

def test_run_ingest_on_target_no_warnings_key(tmp_path, mocker):
    # A batch targeting an ACTIVE handle carries no warning (no false positive).
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["@a", "@b"])
    res = actions.run_ingest(cfg, batch_name="Real", target_accounts=["@a"])
    assert res.ok and "warnings" not in res.detail

def test_run_ingest_single_account_mints_named_batch(tmp_path, mocker):
    # B1: with exactly ONE active account, a named batch with NO target is the []-ALL sentinel — never
    # flagged as zero-target (regression guard for T1's [] path on the production run_ingest path).
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["@solo"])
    res = actions.run_ingest(cfg, batch_name="Solo")
    assert res.ok and res.detail["batch"] == "Solo" and "warnings" not in res.detail
    b = next(iter(Ledger.load(cfg).batches.values()))
    assert b.target_accounts == [] and b.error_reason is None

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

def test_run_ingest_route_passes_batch_fields(tmp_path, mocker):
    # The route reads batch_name + the repeated target_accounts form fields and threads them to run_ingest.
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/run/ingest", data={"batch_name": "Launch", "target_accounts": ["@a", "@b"]})
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert len(led.batches) == 1
    b = next(iter(led.batches.values()))
    assert b.name == "Launch" and b.target_accounts == ["@a", "@b"]
    assert next(iter(led.sources.values())).batch_id == b.id

def test_run_advance_route(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().post("/run/advance")
    assert r.status_code == 200


# ---- views.run_next_step (S3: the Make tab's one "do this next" affordance) ----
def _st(**over):
    base = dict(sources=0, third_party=0, clips=0, posts=0, published=0, holds=0, pending_moments=0,
                pending_moment_hooks=0, pending_captions=0, backend="dryrun", accounts=[])
    base.update(over); return base


def test_run_next_step_add_when_no_footage():
    n = views.run_next_step(_st())
    assert n["key"] == "add" and isinstance(n["label"], str) and isinstance(n["hint"], str)


def test_run_next_step_counts_third_party_as_footage():
    assert views.run_next_step(_st(third_party=1))["key"] == "prepare"   # a link source is footage too -> past 'add'


def test_run_next_step_prepare_when_footage_no_output():
    assert views.run_next_step(_st(sources=2))["key"] == "prepare"


def test_run_next_step_gate_when_decisions_pending():
    n = views.run_next_step(_st(sources=2, pending_moments=3))
    assert n["key"] == "gate" and "3" in n["label"]
    assert "prepare" in n["hint"].lower()       # the gate->clip explanation: answer the gates, then Prepare again


def test_run_next_step_gate_counts_all_three_pending_kinds():
    assert views.run_next_step(_st(sources=1, pending_captions=1))["key"] == "gate"
    assert views.run_next_step(_st(sources=1, pending_moment_hooks=1))["key"] == "gate"


def test_run_next_step_gate_precedes_review():
    # gates block mid-pipeline clips; answering them comes BEFORE reviewing finished posts (ladder order)
    assert views.run_next_step(_st(sources=2, posts=4, pending_captions=1))["key"] == "gate"


def test_run_next_step_review_when_posts_ready():
    n = views.run_next_step(_st(sources=2, posts=4))
    assert n["key"] == "review" and "4" in n["label"]


def test_run_next_step_fail_open_on_empty_dict():
    n = views.run_next_step({})                  # missing keys -> safe 'add' default, never raises
    assert n["key"] == "add" and isinstance(n["label"], str)


def test_run_route_shows_next_step_banner(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    html = app.test_client().get("/run").data.decode()
    assert "run-next" in html and "Add a video" in html        # empty pipeline -> the 'add' banner


def test_run_route_gate_explanation_visible(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(views, "pipeline_status", lambda c: _st(sources=2, pending_moments=2))
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/run").data.decode()
    assert "run-next" in html and "run-next-gate" in html
    assert "prepare again" in html.lower()                     # the gate->clip link is spelled out


def test_run_next_banner_is_flag_independent(tmp_path, monkeypatch):
    # the banner is pipeline-STATE driven, never per-account-differentiation-flag gated -> OFF renders it too
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    html = app.test_client().get("/run").data.decode()
    assert "run-next" in html
