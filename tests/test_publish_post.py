# tests/test_publish_post.py — publish_post(cfg, post_id): ship ONE queued post NOW, ignoring its
# (future) schedule, scoped to just that post. The "Publish now" engine behind the Studio button.
# Reuses publish_due's per-post claim->network->finalize core (_publish_one) with the network OUTSIDE
# the ledger flock; returns the final post-state value (or None when nothing was claimable). Setup
# persists to disk (self-loading path) and assertions reload from disk.
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_post, publish_due


def _queued(led, cfg, pid="p1", cid="clip_1", when="2999-01-01T00:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="98432",
                      platform=Platform.instagram, caption="ship it",
                      scheduled_time=when, state=PostState.queued))
    led.save()


def test_publish_post_dryrun_writes_preview_and_holds_queued(tmp_path, monkeypatch):
    # dryrun-boundary M2: on a NOT-live system, clicking Publish now writes the would-send preview and
    # HOLDS the post `queued` — there is no backend to distribute to, so it never enters distribution
    # (never a phantom `published`). The schedule is still ignored (a 2999 post is "publish-now" eligible).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                      # dryrun
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2999-01-01T00:00:00Z")      # NOT due by schedule
    assert publish_post(cfg, "p1") == "queued"                              # held at the boundary, not published
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.queued
    assert p.submission_id is None and p.public_url is None                 # no fabricated artifacts
    assert (cfg.scheduled / "p1.json").exists()                            # preview WAS written

def test_publish_post_is_scoped_to_the_target(tmp_path, monkeypatch):
    # other queued posts are UNTOUCHED — Publish now acts only on the clicked piece, not the batch.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2999-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="c2", when="2020-01-01T00:00:00Z")      # already due, but NOT clicked
    publish_post(cfg, "p1")
    led = Ledger.load(cfg)
    # dryrun holds p1 queued (boundary) — but the point of THIS test is scoping: p1's preview is written,
    # p2's is not. Only the clicked post was acted on.
    assert (cfg.scheduled / "p1.json").exists()
    assert not (cfg.scheduled / "p2.json").exists()                         # untouched — never processed

def test_publish_post_unknown_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    assert publish_post(cfg, "nope") is None                                # no such post -> no raise, no change
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_publish_post_non_queued_is_noop(tmp_path, monkeypatch):
    # LIVE: a non-queued post is a no-op at _publish_one's CLAIM step. (Must be live — on a dryrun
    # system the M2 boundary short-circuits before the claim; this pins the claim guard itself.)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    with Ledger.transaction(cfg) as led:
        led.posts["p1"].state = PostState.published                         # already published on disk
        led.posts["p1"].public_url = "https://www.instagram.com/reel/AAA/"   # R1: a published row carries a permalink
    assert publish_post(cfg, "p1") is None                                  # claim sees non-queued -> no-op
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_publish_post_propagates_fatal_auth(tmp_path, monkeypatch):
    # LIVE: a bad key must HALT (raise), not silently mark the post failed — same contract as publish_due.
    # (Must be live — a dryrun system never invokes the poster now that M2 boundary-skips it.)
    import fanops.post.run as run
    from fanops.errors import PostizAuthError
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    class BoomPoster:
        def publish(self, led, post_id): raise PostizAuthError("401 unauthorized")
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: BoomPoster())
    monkeypatch.setattr(run, "_ensure_media", lambda *a, **kw: None, raising=False)
    with pytest.raises(PostizAuthError):
        publish_post(cfg, "p1")


def test_empty_integration_id_is_skipped_not_posted(tmp_path, monkeypatch):
    # CULM-1: a live post whose channel resolves to an EMPTY integration id must NOT be POSTed
    # (it would ship integration:{id:""} -> a silent dead post). It stays queued + breadcrumbs.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as lg: lg.posts["p1"].account_id = ""           # never-mapped channel reached queued
    monkeypatch.setattr("fanops.post.run.get_poster",
                        lambda cfg, backend=None: (_ for _ in ()).throw(AssertionError("must not POST")))
    out = publish_due(cfg, now="2000-01-02T00:00:00Z")
    assert out["no_integration_id"] == 1 and out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # stays queued, re-driveable

def test_timeless_queued_post_does_not_auto_publish(tmp_path, monkeypatch):
    # CULM-4: a queued post with NO scheduled_time must NOT auto-publish via publish_due (defense-in-depth
    # on no-auto-publish). It parks (stays queued); publish_post (manual) is unaffected.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                          # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when=None)                            # queued but NO scheduled_time
    out = publish_due(cfg, now="2030-01-01T00:00:00Z")
    assert out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # parked, never published


def test_variant_render_uploaded_once_across_two_publishes(tmp_path, monkeypatch):
    # CULM-2: a per-account render's file must be uploaded at most ONCE (cached on Render.media_url),
    # not re-uploaded every approve->publish cycle (approval re-points media_urls to file://<render>).
    from fanops.models import Render
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    rid = "render_x"; vf = cfg.clips / "v.mp4"; vf.parent.mkdir(parents=True, exist_ok=True); vf.write_bytes(b"V")
    led.add_render(Render(id=rid, clip_id="c1", account="@a", surface_key="@a|instagram", path=str(vf)))
    led.add_clip(Clip(id="c1", parent_id="mom_1", path=str(vf), state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time="2000-01-01T00:00:00Z",
                      render_id=rid, media_urls=[f"file://{vf}"], public_url="dryrun://p1"))
    led.save()
    calls = {"n": 0}
    def up(cfg, backend=None):
        def _u(c, pth, **kw): calls["n"] += 1; return "https://cdn/v.mp4"
        return _u
    monkeypatch.setattr("fanops.post.get_media_uploader", up)        # ensure_render_media (media.py) path
    monkeypatch.setattr("fanops.post.run.get_media_uploader", up)    # the legacy run.py direct-upload path
    class FakePoster:
        def publish(self, led, pid): led.posts[pid].state = PostState.submitted; return led
    monkeypatch.setattr("fanops.post.run.get_poster", lambda cfg, backend=None: FakePoster())
    assert publish_post(cfg, "p1") == "published"
    assert Ledger.load(cfg).renders[rid].media_url == "https://cdn/v.mp4"   # cached on the Render
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.queued; lg.posts["p1"].media_urls = [f"file://{vf}"]   # simulate a re-approval re-stamp
    publish_post(cfg, "p1")
    assert calls["n"] == 1                                            # uploaded ONCE total, not per cycle


def test_republish_of_real_id_post_warns(tmp_path, monkeypatch):
    # XC-7: re-publishing a post that already carries a REAL submission_id may double-post (repost-freely
    # OK, but the claim must breadcrumb it). LIVE — the claim breadcrumb is inside _publish_one, which a
    # dryrun system no longer reaches (M2 boundary-skips before the claim).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    import fanops.post.run as run
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as lg: lg.posts["p1"].submission_id = "blotato_1"
    class _OkPoster:
        def publish(self, led, post_id):
            led.posts[post_id].state = PostState.submitted
            led.posts[post_id].public_url = "https://www.instagram.com/reel/AAA/"
            return led
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: _OkPoster())
    monkeypatch.setattr(run, "_ensure_media", lambda *a, **kw: None, raising=False)
    publish_post(cfg, "p1")
    assert "republish_with_real_id" in cfg.log_path.read_text()

def test_publish_records_the_integration_id_it_used(tmp_path, monkeypatch):
    # XC-5 (characterization): the post carries the integration id it is addressed to. On a dryrun
    # system Publish-now holds it `queued` at the boundary (M2) but the id is preserved on the record.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                          # dryrun
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")          # account_id="98432"
    assert publish_post(cfg, "p1") == "queued"                                  # held at the boundary (dryrun)
    assert Ledger.load(cfg).posts["p1"].account_id == "98432"                   # the addressed id is preserved


# --- Degradation honesty (PRD .claude/prds/degradation-honesty.prd.md) ---
# Every fallback/degradation leaves a trace at the right level; the safe value each path lands on is
# BYTE-IDENTICAL (these tests prove the trace, not a behavior change).

def test_produce_one_ledger_load_failure_logs_error_not_warn(tmp_path, monkeypatch):
    # #9 (M1): a ledger-load failure inside _produce_one HALTS artifact production for that source and
    # must log at outcome `error` (log.py is level-less; `error` is the outcome alerting keys on), NOT the
    # `warn` it used to. Assert via the injected `log` spy the site already accepts.
    from fanops.produce import _produce_one
    from fanops.models import Fmt
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.produce.Ledger.load", staticmethod(lambda c: (_ for _ in ()).throw(RuntimeError("disk gone"))))
    seen: list[tuple] = []
    def spy(stage, unit, outcome, **f): seen.append((stage, unit, outcome, f))
    res = _produce_one(cfg, "src_x", {Fmt.r9x16}, log=spy)
    assert res.error_reason and "disk gone" in res.error_reason                 # safe value: still fail-open, reason stamped
    load_rows = [r for r in seen if r[0] == "produce" and r[1] == "src_x"]
    assert load_rows and all(r[2] == "error" for r in load_rows)                # the load-failure row is `error`, not `warn`

def test_run_all_ledger_load_failure_logs_error_not_warn(tmp_path, monkeypatch):
    # #9 (M1): the SECOND ledger-load site — run_all's own load — halts the whole producer pass and must
    # ALSO log `error`. Fixing only _produce_one half-fixes the register finding.
    from fanops.produce import run_all
    from fanops.models import Fmt
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.produce.Ledger.load", staticmethod(lambda c: (_ for _ in ()).throw(RuntimeError("disk gone"))))
    seen: list[tuple] = []
    def spy(stage, unit, outcome, **f): seen.append((stage, unit, outcome, f))
    run_all(cfg, {Fmt.r9x16}, spy)                                               # NEVER raises (returns early on load fail)
    load_rows = [r for r in seen if r[0] == "produce" and r[1] == "-"]
    assert load_rows and all(r[2] == "error" for r in load_rows)                # run_all's load-failure row is `error`

def test_publish_backend_fallback_logs_when_it_fires(tmp_path, monkeypatch):
    # #10 (M2): publish_backend_for_post falls back to `cfg.poster_backend or "dryrun"` when Accounts
    # resolution raises. The SAFE value is unchanged; the only gap was no breadcrumb. Prove the fallback
    # value AND that it now leaves a trace.
    from fanops.post.compress import publish_backend_for_post
    cfg = Config(root=tmp_path)
    monkeypatch.setattr("fanops.accounts.Accounts.load", staticmethod(lambda c: (_ for _ in ()).throw(RuntimeError("accounts corrupt"))))
    post = Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram, caption="x")
    assert publish_backend_for_post(cfg, post) == "dryrun"                      # safe value byte-identical (no poster_backend set)
    assert "backend_fallback" in cfg.log_path.read_text()                       # breadcrumb landed

def test_publish_backend_no_log_on_happy_path(tmp_path):
    # #10 (M2): silence when the fallback does NOT fire — a clean resolve emits NO breadcrumb (manufactured
    # noise is a half-fix too).
    from fanops.post.compress import publish_backend_for_post
    from fanops.accounts import add_account, set_backend
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active"); set_backend(cfg, "@tt", "tiktok", "zernio")
    post = Post(id="p", parent_id="c", account="@tt", account_id="1", platform=Platform.tiktok, caption="x")
    assert publish_backend_for_post(cfg, post) == "zernio"                      # resolved cleanly, no fallback
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "backend_fallback" not in log                                       # NOT logged on the happy path

def test_resolve_publish_account_id_fallback_logs_when_it_fires(tmp_path):
    # #10 (M2): _resolve_publish_account_id returns None (the frozen post.account_id then stands) when the
    # per-channel lookup raises. Safe value (None) unchanged; breadcrumb it when the frozen-id fallback fires.
    from fanops.post.run import _resolve_publish_account_id
    cfg = Config(root=tmp_path)
    class _Boom:
        def resolve_account_id(self, handle, platform=None): raise RuntimeError("no mapping")
    post = Post(id="p", parent_id="c", account="@a", account_id="frozen_id", platform=Platform.instagram, caption="x")
    assert _resolve_publish_account_id(_Boom(), post, cfg=cfg) is None          # safe value: None -> frozen id stands
    assert "account_id_fallback" in cfg.log_path.read_text()                    # breadcrumb landed

def test_resolve_publish_account_id_no_log_on_happy_path(tmp_path):
    # #10 (M2): a clean resolve returns the id and emits NO breadcrumb.
    from fanops.post.run import _resolve_publish_account_id
    cfg = Config(root=tmp_path)
    class _Ok:
        def resolve_account_id(self, handle, platform=None): return "live_id"
    post = Post(id="p", parent_id="c", account="@a", account_id="frozen_id", platform=Platform.instagram, caption="x")
    assert _resolve_publish_account_id(_Ok(), post, cfg=cfg) == "live_id"       # resolved cleanly
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "account_id_fallback" not in log                                    # NOT logged on the happy path
