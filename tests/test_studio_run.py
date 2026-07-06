# tests/test_studio_run.py — Studio as pipeline DRIVER: ingest/advance/pull from the browser through
# the same lock-safe paths the CLI uses, so the operator never needs the terminal.
import json
import os
from types import SimpleNamespace
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

def test_run_ingest_surfaces_skipped_count_on_copy_failure(tmp_path, mocker):
    # silent_ingest_failure_on_copy_enospc (high): a copy failure (ENOSPC/perms) leaves the file in the
    # inbox and bumps counts.skipped, but run_ingest dropped `skipped` from the detail dict — the operator
    # saw "Done" while the file silently jammed the inbox and re-failed every pass. The count must reach the
    # action detail (like `excluded` already does) so the skip is VISIBLE, not silent.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    mocker.patch("fanops.ingest.shutil.copy2", side_effect=OSError(28, "No space left on device"))
    res = actions.run_ingest(cfg)
    assert res.ok                                            # a per-file skip is NOT a pass failure
    assert res.detail.get("skipped") == 1                   # the copy-failed file is surfaced, not silent
    assert res.detail["added"] == 0                         # nothing was catalogued

def test_run_ingest_with_batch_name_mints_batch_and_stamps_source(tmp_path, mocker):
    # A non-blank batch_name mints a named, account-targeted Batch in the SAME transaction; the catalogued
    # source carries its id and the detail reports the batch.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg, batch_name="  Launch week  ", target_accounts=["a", "a", ""])
    assert res.ok and res.detail["sources"] == 1
    led = Ledger.load(cfg)
    assert len(led.batches) == 1
    b = next(iter(led.batches.values()))
    assert b.name == "Launch week" and b.target_accounts == ["a"]    # stripped + deduped + blank-dropped
    assert res.detail["batch"] == "Launch week" and res.detail["batch_id"] == b.id
    assert next(iter(led.sources.values())).batch_id == b.id          # source stamped under the batch

def test_run_ingest_blank_batch_name_falls_back_to_drop_batch(tmp_path, mocker):
    # ROOT CONTRACT (supersedes earlier "no batch => None"): a blank batch_name leaves run_ingest's
    # `batch` detail unset (no operator-named batch surfaced), but ingest_drops still resolves the day's
    # auto drop-batch and stamps it onto the new Source — so the Studio Review "Ungrouped" group can
    # never be constructed from this path. Detailed contract in tests/test_ingest_auto_batch.py.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg, batch_name="   ")
    assert res.ok and "batch" not in res.detail              # no operator-named batch surfaced
    led = Ledger.load(cfg)
    src = next(iter(led.sources.values()))
    assert src.batch_id is not None and led.get_batch(src.batch_id).name.startswith("drop-")

def _seed_accounts(cfg, handles):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "x", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def test_run_ingest_zero_target_bubbles_warning(tmp_path, mocker):
    # Face 1-fu (T4): a batch targeting a handle that is NOT active still mints (advisory, not fatal) but
    # surfaces detail["warnings"] — so the operator isn't left with a silent zero-post run downstream.
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["a"])
    res = actions.run_ingest(cfg, batch_name="Ghost run", target_accounts=["ghost"])
    assert res.ok and res.detail.get("warnings") and "ghost" in res.detail["warnings"][0]
    b = next(iter(Ledger.load(cfg).batches.values()))
    assert "ghost" in (b.error_reason or "")            # the advisory is persisted on the batch too

def test_run_ingest_on_target_no_warnings_key(tmp_path, mocker):
    # A batch targeting an ACTIVE handle carries no warning (no false positive).
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["a", "b"])
    res = actions.run_ingest(cfg, batch_name="Real", target_accounts=["a"])
    assert res.ok and "warnings" not in res.detail

def test_run_ingest_single_account_mints_named_batch(tmp_path, mocker):
    # B1: with exactly ONE active account, a named batch with NO target is the []-ALL sentinel — never
    # flagged as zero-target (regression guard for T1's [] path on the production run_ingest path).
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker); _seed_accounts(cfg, ["solo"])
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


# ---- WS-I1 Task 2 (ING-6/12): a URL pull catalogues ONLY its staged download, never inbox residue ----
def test_run_pull_ingests_only_staged_download_not_inbox_residue(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    cfg.inbox.mkdir(parents=True, exist_ok=True); (cfg.inbox / "manual.mp4").write_bytes(b"MANUAL")
    def fake_ytdlp(cmd, **kw):
        from fanops.ingest import _pull_stage
        (_pull_stage(cfg) / "pulled.mp4").write_bytes(b"PULLED")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    res = actions.run_pull(cfg, "https://example.com/v")
    assert res.ok
    led = Ledger.load(cfg)
    assert len(led.sources) == 1                                    # ONLY the pulled file
    assert next(iter(led.sources.values())).source_origin == "url"
    assert (cfg.inbox / "manual.mp4").exists()                      # the manual drop is left for a native pass


# ---- WS-I1 Task 4 (ING-2): report the this-pass delta, not the cumulative total ----
def test_run_ingest_reports_added_delta_not_cumulative(tmp_path, mocker):
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker, name="a.mp4")
    r1 = actions.run_ingest(cfg)
    assert r1.detail["added"] == 1 and r1.detail["sources"] == 1
    _src_in_inbox(cfg, mocker, name="b.mp4")
    (cfg.inbox / "b.mp4").write_bytes(b"DIFFERENT")                  # ensure distinct sha
    r2 = actions.run_ingest(cfg)
    assert r2.detail["added"] == 1 and r2.detail["sources"] == 2
    r3 = actions.run_ingest(cfg)                                     # inbox now drained → nothing new
    assert r3.detail["added"] == 0 and r3.detail["sources"] == 2    # honest "Added 0", not 2


# ---- WS-I1 Task 5 (ING-3/5): no orphan batch; deterministic id; native PII count ----
def test_run_ingest_empty_inbox_mints_no_batch(tmp_path, mocker):
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)   # exists but empty
    res = actions.run_ingest(cfg, batch_name="Ghost batch", target_accounts=["a"])
    assert res.ok and res.detail["added"] == 0
    assert "batch" not in res.detail and res.detail.get("batch_skipped")        # no orphan; operator told why
    assert len(Ledger.load(cfg).batches) == 0

def test_run_ingest_real_drop_mints_one_batch_with_matching_id(tmp_path, mocker):
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    res = actions.run_ingest(cfg, batch_name="Real batch")
    assert res.ok and res.detail["added"] == 1 and res.detail["batch"] == "Real batch"
    led = Ledger.load(cfg); assert len(led.batches) == 1
    b = next(iter(led.batches.values()))
    assert next(iter(led.sources.values())).batch_id == b.id        # ids match (no silent orphan stamp)

def test_run_ingest_surfaces_native_pii_count(tmp_path, mocker):
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / "passport scan.mp4").write_bytes(b"S"); (cfg.inbox / "perf.mp4").write_bytes(b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    res = actions.run_ingest(cfg)
    assert res.detail["added"] == 1 and res.detail["excluded"] == 1


# ---- actions.run_advance ----
def test_run_advance_returns_summary(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.run_advance(cfg)
    assert res.ok and "sources" in res.detail and "awaiting" in res.detail

def test_run_advance_live_backend_requires_confirm(tmp_path, monkeypatch):
    # Track C: a pass on a LIVE backend publishes to real accounts — the Run button must require an
    # explicit confirm, never fire on a stray click.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
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
    # not degrade to a generic "advance failed" via the PostizAuthError-only arm.
    from fanops.errors import PostizAuthError
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    def boom(c, *, base_time): raise PostizAuthError("Postiz 401 (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_advance(Config(root=tmp_path), confirmed=True)
    assert not res.ok and "FATAL" in res.error and "POSTIZ_API_KEY" in res.error

def test_run_advance_surfaces_fatal_auth(tmp_path, monkeypatch):
    # ecc:python-review HIGH: a fatal PostizAuthError (bad key) must surface as FATAL, not be demoted
    # to a soft "advance failed" by the broad except. advance's own txn already rolled back.
    from fanops.errors import PostizAuthError
    def boom(c, *, base_time): raise PostizAuthError("Postiz 401 unauthorized (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_advance(Config(root=tmp_path))
    assert not res.ok and "FATAL" in res.error and "POSTIZ_API_KEY" in res.error


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
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")             # explicit manual
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.pipeline.advance",
                        lambda c, *, base_time: {"sources": 1, "awaiting": {"moments": 1, "captions": 0}})
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    res = actions.run_prepare(cfg)
    assert res.ok is True                                        # manual: gates pending is normal

def test_run_prepare_live_backend_requires_confirm(tmp_path, monkeypatch):
    # A prepare pass crossposts/publishes due posts on a live backend -> same confirm guard as advance.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    res = actions.run_prepare(Config(root=tmp_path), confirmed=False)
    assert not res.ok and "confirm" in (res.error or "").lower()

def test_run_prepare_surfaces_fatal_auth(tmp_path, monkeypatch):
    # A fatal auth failure during a prepare pass surfaces FATAL + the right key, not a soft "failed".
    from fanops.errors import PostizAuthError
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    def boom(c, *, base_time): raise PostizAuthError("Postiz 401 (body withheld)")
    monkeypatch.setattr("fanops.pipeline.advance", boom)
    res = actions.run_prepare(Config(root=tmp_path))
    assert not res.ok and "FATAL" in res.error and "POSTIZ_API_KEY" in res.error

def test_run_prepare_route(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    monkeypatch.setattr("fanops.responder.get_responder",
                        lambda c: type("R", (), {"answer_pending": lambda s, c: 0})())
    monkeypatch.setattr("fanops.pipeline.advance",
                        lambda c, *, base_time: {"sources": 0, "awaiting": {"moments": 0, "captions": 0}})
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().post("/run/prepare")
    assert r.status_code == 200

def test_run_route_shows_primary_make_button(tmp_path):
    # The Make page's dominant action — relabelled from "Prepare everything" to plain "Make clips" in
    # the 3-stage console rewrite. The button still posts to do_run_prepare (same backend verb), the
    # surface label is what changed; this pins the label so a future swap can't silently drop it.
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().get("/run")
    assert b"Make clips" in r.data


# ---- actions.run_pull ----
def test_run_pull_rejects_non_http_url(tmp_path):
    res = actions.run_pull(Config(root=tmp_path), "not-a-url")
    assert not res.ok and "http" in (res.error or "").lower()


def test_run_pull_does_not_mislabel_a_pre_existing_drop_as_url(tmp_path, mocker):
    # audit c0-f1 / ING-6: the Studio URL-ingest path catalogues ONLY its isolated .pull stage, so a manual drop
    # already in the inbox is never scanned by the pull — it CANNOT be mislabeled "url" (it waits for a native pass).
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    (cfg.inbox).mkdir(parents=True, exist_ok=True); (cfg.inbox / "drop.mp4").write_bytes(b"DROPPED")
    def fake_ytdlp(cmd, **kw):
        from fanops.ingest import _pull_stage
        (_pull_stage(cfg) / "pulled.mp4").write_bytes(b"PULLED")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    res = actions.run_pull(cfg, "https://example.com/v")
    assert res.ok
    led = Ledger.load(cfg)
    assert {s.source_origin for s in led.sources.values()} == {"url"}   # only the staged pull is catalogued
    assert (cfg.inbox / "drop.mp4").exists()                            # the manual drop is left for a native pass


# ---- views.pipeline_status ----
def test_pipeline_status_counts(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))
    st = views.pipeline_status(cfg)
    assert st["sources"] == 1 and "pending_moments" in st and st["backend"] == "dryrun"


def test_pipeline_status_awaiting_counts_moments_not_posts(tmp_path):
    # The Make tab's "Next: N ready" must speak the SAME unit as Home/Review (MOMENTS), not the raw awaiting-post
    # count — a clip fans out to many surface posts, so counting posts made Make say "57" next to "Clips ready 17".
    from fanops.models import Moment, Clip, Post, Platform, PostState, MomentState, Fmt, ClipState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))
        led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        for i in range(3):                       # 3 awaiting SURFACE posts on ONE clip/moment
            led.add_post(Post(id=f"p{i}", parent_id="c1", account=f"@a{i}", account_id=str(i),
                              platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, public_url="dryrun://c1"))
    assert views.pipeline_status(cfg)["awaiting"] == 1      # ONE moment, not three posts


# ---- Flask wiring ----
def test_run_route_renders(tmp_path):
    # Smoke-test the route + a stable rewrite-survivable string. The legacy "Ingest" button was
    # retired with PR #231 (ingest_drops auto-batches inbox drops on the next Make pass, so the
    # "Ingest added videos" escape hatch is structurally unnecessary). Pin a string that names the
    # Make page itself — "Add footage" is the stage-① card heading and survives copy tweaks.
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().get("/run")
    assert r.status_code == 200 and b"Add footage" in r.data

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
    r = app.test_client().post("/run/ingest", data={"batch_name": "Launch", "target_accounts": ["a", "b"]})
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert len(led.batches) == 1
    b = next(iter(led.batches.values()))
    assert b.name == "Launch" and b.target_accounts == ["a", "b"]
    assert next(iter(led.sources.values())).batch_id == b.id

def test_run_advance_route(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().post("/run/advance")
    assert r.status_code == 200


# ---- views.run_next_step (S3: the Make tab's one "do this next" affordance) ----
def _st(**over):
    base = dict(sources=0, third_party=0, clips=0, posts=0, awaiting=0, published=0, holds=0, pending_moments=0,
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
    n = views.run_next_step(_st(sources=2, awaiting=4, pending_captions=1))
    assert n["key"] == "gate" and "4 clip(s) are also waiting" in n["hint"]   # gate hint also flags review work


def test_run_next_step_review_counts_only_actionable_awaiting():
    n = views.run_next_step(_st(sources=2, awaiting=4))
    assert n["key"] == "review" and "4" in n["label"]


def test_run_next_step_prepare_when_all_posts_shipped():
    # audit MEDIUM: posts exist but ALL are published (awaiting==0) -> the next move is 'run a pass', NOT a false
    # "N post(s) ready" (the old len(posts) count made 'review' fire forever after the first post was ever minted).
    n = views.run_next_step(_st(sources=2, posts=50, published=50, awaiting=0))
    assert n["key"] == "prepare"


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
    # patch BEFORE create_app: the route closures reference views.pipeline_status at CALL time (late binding), so
    # patching the module attr here means the live route uses this stub on the request — exercises real wiring.
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


# ── WS-D1 Phase 3: ingest event-kick (de-lazify — drive immediately, not after a daemon interval) ──
import pytest
from fanops.studio import actions_run


@pytest.fixture(autouse=True)
def _no_real_run_spawn(monkeypatch):
    # The event-kick spawns a DETACHED `fanops run`; never let a TEST spawn a real one. Neutralize the
    # spawn module-wide so every run_ingest test stays hermetic; the kick tests below override with their
    # own Popen mock to assert.
    monkeypatch.setattr(actions_run.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=424242))   # a spawned proc has a .pid (the debounce reads it)


def test_run_ingest_kicks_prepare_when_footage_added(tmp_path, mocker):
    cfg = Config(root=tmp_path); _src_in_inbox(cfg, mocker)
    kick = mocker.patch("fanops.studio.actions_run.kick_prepare")
    res = actions.run_ingest(cfg)
    assert res.ok and res.detail["added"] == 1
    kick.assert_called_once()                              # a fresh drop drives immediately (no interval wait)


def test_run_ingest_no_kick_when_nothing_added(tmp_path, mocker):
    cfg = Config(root=tmp_path)                            # empty inbox -> added 0
    kick = mocker.patch("fanops.studio.actions_run.kick_prepare")
    res = actions.run_ingest(cfg)
    assert res.ok and res.detail["added"] == 0
    kick.assert_not_called()                               # no new footage -> no wasted run


def test_kick_prepare_spawns_detached_run_then_debounces(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    popen = mocker.patch("fanops.studio.actions_run.subprocess.Popen")
    popen.return_value.pid = os.getpid()                   # a REAL, alive pid -> the debounce is liveness-based
    assert actions_run.kick_prepare(cfg) is True           # no recent kick -> spawn
    assert popen.call_count == 1 and popen.call_args[0][0][1] == "run"   # spawns `fanops run`
    assert actions_run.kick_prepare(cfg) is False          # prior kick still ALIVE (this pid) -> debounced
    assert popen.call_count == 1                           # ...no second spawn

def test_kick_prepare_respawns_when_prior_run_finished(tmp_path, mocker):
    # kick-prepare-debounce-race (high): the debounce used a fixed 300s TTL stamped after spawn, so a run that
    # FINISHED early still blocked the next ingest-kick for up to a daemon interval. Now the debounce is tied to
    # the spawned process's LIVENESS — once the prior run's pid is dead, a fresh ingest kicks IMMEDIATELY.
    cfg = Config(root=tmp_path)
    popen = mocker.patch("fanops.studio.actions_run.subprocess.Popen")
    popen.return_value.pid = 424242
    mocker.patch("fanops.studio.actions_run.os.kill", side_effect=ProcessLookupError)   # prior run has FINISHED
    assert actions_run.kick_prepare(cfg) is True           # first kick -> spawn
    assert actions_run.kick_prepare(cfg) is True           # prior run dead -> respawn NOW (not blocked by the TTL)
    assert popen.call_count == 2


def test_kick_prepare_is_fail_open_on_spawn_error(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.studio.actions_run.subprocess.Popen", side_effect=OSError("boom"))
    assert actions_run.kick_prepare(cfg) is False          # swallowed -> ingest never breaks
