# tests/test_publish_post.py — publish_post(cfg, post_id): ship ONE queued post NOW, ignoring its
# (future) schedule, scoped to just that post. The "Publish now" engine behind the Studio button.
# Reuses publish_due's per-post claim->network->finalize core (_publish_one) with the network OUTSIDE
# the ledger flock; returns the final post-state value (or None when nothing was claimable). Setup
# persists to disk (self-loading path) and assertions reload from disk.
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform, Moment, MomentState
from fanops.post.run import publish_post, publish_due


def _mom1(led):
    """Materialize `mom_1`, the moment every clip here names. It was never added, so each clip had a
    DANGLING parent — a shape production cannot build. Harmless while every retirement predicate failed
    OPEN on a missing row; publish_due now asks Ledger.can_promote, which fails CLOSED. add_moment is
    setdefault, so repeat calls are a no-op."""
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                          state=MomentState.clipped))


def _queued(led, cfg, pid="p1", cid="clip_1", when="2999-01-01T00:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="98432",
                      platform=Platform.instagram, caption="ship it", post_type="post",
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
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    with Ledger.transaction(cfg) as led:
        led.posts["p1"] = led.posts["p1"].model_copy(update={"state": PostState.published})                         # already published on disk
        led.posts["p1"].public_url = "https://www.instagram.com/reel/AAA/"   # R1: a published row carries a permalink
    assert publish_post(cfg, "p1") is None                                  # claim sees non-queued -> no-op
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._b = {} if body is None else body
        self.text = text
    def json(self):
        return self._b


def _wire_postiz(mocker, *, on_post=None):
    def _get(url, **kw):
        if "integrations" in str(url):
            return _R(200, [{"id": "98432", "name": "a", "identifier": "instagram-standalone"},
                            {"id": "98", "name": "a", "identifier": "instagram-standalone"}])
        return _R(200, {"posts": []})
    def _post(url, **kw):
        if on_post is not None:
            return on_post(url, **kw)
        if "/upload" in str(url):
            return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
        return _R(201, {"id": "postiz_1"})
    mocker.patch("requests.get", side_effect=_get)
    mocker.patch("requests.post", side_effect=_post)


def test_publish_post_propagates_fatal_auth(tmp_path, monkeypatch, mocker):
    # LIVE: a bad key must HALT (raise), not silently mark the post failed — same contract as publish_due.
    from fanops.errors import PostizAuthError
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].media_urls = ["https://uploads.postiz.com/v.mp4"]
    _wire_postiz(mocker, on_post=lambda url, **kw: _R(401, {}, text="unauthorized"))
    with pytest.raises(PostizAuthError):
        publish_post(cfg, "p1")


def test_empty_integration_id_is_skipped_not_posted(tmp_path, monkeypatch, mocker):
    # CULM-1: a live post whose channel resolves to an EMPTY integration id must NOT be POSTed
    # (it would ship integration:{id:""} -> a silent dead post). It stays queued + breadcrumbs.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as lg: lg.posts["p1"].account_id = ""           # never-mapped channel reached queued
    def boom(url, **kw):
        raise AssertionError(f"must not POST when integration id is empty: {url}")
    mocker.patch("requests.post", side_effect=boom)
    mocker.patch("requests.get", side_effect=boom)
    out = publish_due(cfg, now="2000-01-02T00:00:00Z")
    assert out["no_integration_id"] == 1 and out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # stays queued, re-driveable

def test_submission_started_at_is_not_network_determined(tmp_path, monkeypatch):
    # MOL-709: the stamp is written in the CLAIM txn, so it must NOT ride _NET_POST_FIELDS — that union is
    # the set finalize merges from the THROWAWAY network ledger, and anything in it can be rewritten by a
    # late network phase. Same exclusion, same reason, as created_at (test_zernio_idempotency test_13).
    import fanops.post.run as run
    assert "submission_started_at" not in run._NET_POST_FIELDS

def test_timeless_queued_post_does_not_auto_publish(tmp_path, monkeypatch):
    # CULM-4: a queued post with NO scheduled_time must NOT auto-publish via publish_due (defense-in-depth
    # on no-auto-publish). It parks (stays queued); publish_post (manual) is unaffected.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                          # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when=None)                            # queued but NO scheduled_time
    out = publish_due(cfg, now="2030-01-01T00:00:00Z")
    assert out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # parked, never published


def test_variant_render_uploaded_once_across_two_publishes(tmp_path, monkeypatch, mocker):
    # CULM-2: a per-account render's file must be uploaded at most ONCE (cached on Render.media_url).
    # Leftover dryrun:// is not a permalink.
    from fanops.models import Render
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    rid = "render_x"; vf = cfg.clips / "v.mp4"; vf.parent.mkdir(parents=True, exist_ok=True); vf.write_bytes(b"V")
    led.add_render(Render(id=rid, clip_id="c1", account="a", surface_key="a|instagram", path=str(vf)))
    _mom1(led)
    led.add_clip(Clip(id="c1", parent_id="mom_1", path=str(vf), state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="98", platform=Platform.instagram,
                      caption="x", post_type="post", state=PostState.queued, scheduled_time="2000-01-01T00:00:00Z",
                      render_id=rid, media_urls=[f"file://{vf}"], public_url="dryrun://p1"))
    led.save()
    class _R:
        def __init__(self, code, body=None, text=""):
            self.status_code = code; self._b = {} if body is None else body; self.text = text
        def json(self): return self._b
    uploads = {"n": 0}
    def _get(url, **kw):
        if "integrations" in str(url):
            return _R(200, [{"id": "98", "name": "a", "identifier": "instagram-standalone"}])
        return _R(200, {"posts": []})
    def _post(url, **kw):
        if "/upload" in str(url):
            uploads["n"] += 1
            return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
        return _R(201, {"id": "postiz_1"})
    mocker.patch("requests.get", side_effect=_get)
    mocker.patch("requests.post", side_effect=_post)
    out = publish_post(cfg, "p1")
    assert out != "published"
    assert Ledger.load(cfg).posts["p1"].state is not PostState.published
    assert Ledger.load(cfg).renders[rid].media_url == "img1|https://uploads.postiz.com/v.mp4"
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"] = lg.posts["p1"].model_copy(update={"state": PostState.queued, "submission_id": None})
        lg.posts["p1"].media_urls = [f"file://{vf}"]
    publish_post(cfg, "p1")
    assert uploads["n"] == 1


def test_real_id_post_is_refused_at_the_claim_never_stranded(tmp_path, monkeypatch, mocker):
    # RC-1 / S03 INVARIANT. A post that ALREADY carries a real submission_id has been POSTed; re-POSTing
    # the SAME post id is the double-POST we forbid (MOL-115). The refusal MUST happen in the CLAIM, where
    # declining is a clean no-op that leaves the post `queued`. Before S03 the refusal lived one phase
    # LATER (the network phase), so the claim had already committed `submitting` and NOTHING un-claimed it
    # — the post stranded `submitting`, claimed-but-never-published. This test pins the outcome, not the
    # phrasing: the poster is NEVER called (no double-POST), and the post is left `queued` (not stranded,
    # not published). (Reposting CONTENT freely is `repost_post`, which mints a NEW id — a different path.)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as lg: lg.posts["p1"].submission_id = "blotato_1"
    sent = {"n": 0}
    def boom(url, **kw):
        sent["n"] += 1
        raise AssertionError(f"must not POST when submission_id exists: {url}")
    mocker.patch("requests.post", side_effect=boom)
    mocker.patch("requests.get", side_effect=boom)
    assert publish_post(cfg, "p1") is None                # refused at the claim — nothing published
    assert sent["n"] == 0                                 # the network POST was NEVER attempted
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued   # left queued, NOT stranded `submitting`
    assert "skip_resubmit_existing_id" in cfg.log_path.read_text()

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

def _corrupt_ledger(cfg):
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_bytes(b"this is not a sqlite database")


def test_produce_one_ledger_load_failure_logs_error_not_warn(tmp_path, monkeypatch):
    # #9 (M1): a ledger-load failure inside _produce_one HALTS artifact production for that source and
    # must log at outcome `error` (log.py is level-less; `error` is the outcome alerting keys on), NOT the
    # `warn` it used to. Drive the fault via a corrupt ledger file, not a Ledger.load patch.
    from fanops.produce import _produce_one
    from fanops.models import Fmt
    cfg = Config(root=tmp_path)
    _corrupt_ledger(cfg)
    seen: list[tuple] = []
    def spy(stage, unit, outcome, **f): seen.append((stage, unit, outcome, f))
    res = _produce_one(cfg, "src_x", {Fmt.r9x16}, log=spy)
    assert res.error_reason and "invalid" in res.error_reason.lower()           # fail-open, reason stamped
    load_rows = [r for r in seen if r[0] == "produce" and r[1] == "src_x"]
    assert load_rows and all(r[2] == "error" for r in load_rows)                # the load-failure row is `error`, not `warn`

def test_run_all_ledger_load_failure_logs_error_not_warn(tmp_path, monkeypatch):
    # #9 (M1): the SECOND ledger-load site — run_all's own load — halts the whole producer pass and must
    # ALSO log `error`. Fixing only _produce_one half-fixes the register finding.
    from fanops.produce import run_all
    from fanops.models import Fmt
    cfg = Config(root=tmp_path)
    _corrupt_ledger(cfg)
    seen: list[tuple] = []
    def spy(stage, unit, outcome, **f): seen.append((stage, unit, outcome, f))
    run_all(cfg, {Fmt.r9x16}, spy)                                               # NEVER raises (returns early on load fail)
    load_rows = [r for r in seen if r[0] == "produce" and r[1] == "-"]
    assert load_rows and all(r[2] == "error" for r in load_rows)                # run_all's load-failure row is `error`

def test_publish_backend_fallback_logs_when_it_fires(tmp_path, monkeypatch):
    # #10 (M2): publish_backend_for_post falls back to `cfg.poster_backend or "dryrun"` when Accounts
    # resolution raises. The SAFE value is unchanged; the only gap was no breadcrumb. Prove the fallback
    # value AND that it now leaves a trace. Drive via a corrupt accounts.json, not an Accounts.load patch.
    from fanops.post.compress import publish_backend_for_post
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text("{")
    post = Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram, caption="x")
    assert publish_backend_for_post(cfg, post) == "dryrun"                      # safe value byte-identical (no poster_backend set)
    assert "backend_fallback" in cfg.log_path.read_text()                       # breadcrumb landed

def test_publish_backend_no_log_on_happy_path(tmp_path):
    # #10 (M2): silence when the fallback does NOT fire — a clean resolve emits NO breadcrumb (manufactured
    # noise is a half-fix too).
    from fanops.post.compress import publish_backend_for_post
    from fanops.accounts import add_account, set_backend
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active"); set_backend(cfg, "@tt", "tiktok", "zernio")
    post = Post(id="p", parent_id="c", account="tt", account_id="1", platform=Platform.tiktok, caption="x")
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
    post = Post(id="p", parent_id="c", account="a", account_id="frozen_id", platform=Platform.instagram, caption="x")
    assert _resolve_publish_account_id(_Boom(), post, cfg=cfg) is None          # safe value: None -> frozen id stands
    assert "account_id_fallback" in cfg.log_path.read_text()                    # breadcrumb landed

def test_resolve_publish_account_id_no_log_on_happy_path(tmp_path):
    # #10 (M2): a clean resolve returns the id and emits NO breadcrumb.
    from fanops.post.run import _resolve_publish_account_id
    cfg = Config(root=tmp_path)
    class _Ok:
        def resolve_account_id(self, handle, platform=None): return "live_id"
    post = Post(id="p", parent_id="c", account="a", account_id="frozen_id", platform=Platform.instagram, caption="x")
    assert _resolve_publish_account_id(_Ok(), post, cfg=cfg) == "live_id"       # resolved cleanly
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "account_id_fallback" not in log                                    # NOT logged on the happy path
