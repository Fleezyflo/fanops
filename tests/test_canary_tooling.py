# tests/test_canary_tooling.py — offline proofs for the isolated canary tooling (src/fanops/canary.py).
# Hermetic UNIT tests: render + probe are stubbed (no ffmpeg), no network/LLM/agent, no pipeline call.
# Covers the approved matrix: single-lineage mint (0 Posts/0 Renders), idempotency + terminal-after-discard,
# content-addressed identity, reserved-account allowlist, filesystem containment, cancellation guards +
# audit-failure, discard, and read-only baseline capture/compare.
import json
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Clip, Batch, Post, SourceState, MomentState, ClipState,
                           BatchState, PostState, Platform, Fmt)
from fanops import canary


# ---------- fixtures / helpers ----------

def _seed(cfg, *, status="planned", integ="tiktok-integ-999", backend="zernio",
          persona_id="canary-persona", platforms=None, extra_accounts=None, personas=None):
    cfg.control.mkdir(parents=True, exist_ok=True)
    acct = {"handle": "fanops_canary", "account_id": "", "platforms": platforms or ["tiktok"],
            "status": status, "integrations": {"tiktok": integ} if integ else {},
            "backends": {"tiktok": backend} if backend else {}, "persona_id": persona_id}
    accounts = [acct] + list(extra_accounts or [])
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))
    per = personas if personas is not None else [{"id": "canary-persona", "name": "Canary", "voice": "neutral test voice"}]
    cfg.personas_path.write_text(json.dumps({"personas": per}))


def _media(tmp_path, data=b"canary-media-bytes"):
    p = Path(tmp_path) / "in.mp4"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return str(p)


@pytest.fixture
def stub_render(monkeypatch):
    calls = {"single": 0, "supercut": 0}
    def _probe(path): return (1080, 1920, 30.0)
    def _single(src, dst, cs, ce, aspect, *, src_w, src_h):
        calls["single"] += 1; Path(dst).write_bytes(b"RENDERED"); return type("R", (), {"returncode": 0})()
    def _super(src, dst, spans, aspect, *, src_w, src_h):
        calls["supercut"] += 1; Path(dst).write_bytes(b"RENDERED"); return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(canary, "_do_probe", _probe)
    monkeypatch.setattr(canary, "_do_render_single", _single)
    monkeypatch.setattr(canary, "_do_render_supercut", _super)
    return calls


def _prep(cfg, media, **kw):
    kw.setdefault("start", "0"); kw.setdefault("end", "4"); kw.setdefault("caption", "canary caption")
    return canary.prepare_canary_lineage(cfg, media_path=media, **kw)


# ---------- prepare: lineage shape ----------

def test_prepare_creates_one_source_moment_clip_batch_zero_posts_zero_renders(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = _prep(cfg, media)
    assert res.ok, res.error
    led = Ledger.load(cfg)
    assert len(led.sources) == 1 and len(led.moments) == 1 and len(led.clips) == 1 and len(led.batches) == 1
    assert len(led.posts) == 0
    assert len(getattr(led, "renders", {})) == 0            # BC1: NO Render entity
    s = next(iter(led.sources.values())); m = next(iter(led.moments.values()))
    c = next(iter(led.clips.values())); b = next(iter(led.batches.values()))
    assert s.state is SourceState.moments_decided
    assert m.state is MomentState.clipped and m.affinities == ["fanops_canary"]
    assert c.state is ClipState.queued and c.aspect is Fmt.r9x16
    assert b.state is BatchState.open and b.target_accounts == ["fanops_canary"]
    assert c.meta_captions.get("fanops_canary/tiktok", {}).get("caption") == "canary caption"


def test_prepare_clip_queued_not_globally_seedable_but_reusable(tmp_path, stub_render):
    from fanops.crosspost import _seed_clips, _REUSABLE_CLIP_STATES
    cfg = Config(root=tmp_path); _seed(cfg); _prep(cfg, _media(tmp_path))
    led = Ledger.load(cfg); c = next(iter(led.clips.values()))
    assert c.state is ClipState.queued
    assert _seed_clips(led) == []                            # queued clip is NOT a crosspost seed
    assert ClipState.queued in _REUSABLE_CLIP_STATES         # ...but IS reusable by the scoped mint


def test_prepare_plan_only_no_filesystem_or_ledger_artifact(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = _prep(cfg, media, plan_only=True)
    assert res.ok and res.detail["plan_only"] and res.detail["created"] is False
    assert Ledger.load(cfg).sources == {}
    assert not (Path(cfg.base) / "canary").exists()          # zero fs mutation


def test_prepare_segments_use_supercut_and_set_moment_segments(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = canary.prepare_canary_lineage(cfg, media_path=media, start="0", end=None,
                                        segments=[(0.0, 2.0), (5.0, 7.0)], caption="x")
    assert res.ok, res.error
    assert stub_render["supercut"] == 1 and stub_render["single"] == 0
    m = next(iter(Ledger.load(cfg).moments.values()))
    assert [tuple(s) for s in m.segments] == [(0.0, 2.0), (5.0, 7.0)]


# ---------- identity ----------

def test_run_identity_deterministic_and_input_sensitive(tmp_path):
    n = canary._canonical_run_name(media_sha256="a" * 64, start=0.0, end=4.0, segments=None,
                                   caption="c", hashtags=["x"], hook=None, run_label=None)
    rid = canary._run_id_from_name(n)
    assert canary._RUN_ID_RE.match(rid)
    assert rid == canary._run_id_from_name(n)                # deterministic
    for chg in [{"caption": "c2"}, {"hook": "h"}, {"hashtags": ["y"]}, {"start": 1.0}, {"end": 5.0},
                {"segments": [[0.0, 1.0]]}, {"run_label": "z"}, {"media_sha256": "b" * 64}]:
        base = dict(media_sha256="a" * 64, start=0.0, end=4.0, segments=None, caption="c",
                    hashtags=["x"], hook=None, run_label=None)
        base.update(chg)
        assert canary._run_id_from_name(canary._canonical_run_name(**base)) != rid, chg


def test_full_media_sha256_participates_in_identity(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    # plan-only on each: minting BOTH would (correctly) trip the single-live-canary-lineage account guard.
    # The full sha256 still flows through the real prepare() entry-point into the derived run id. _media
    # makes each subdir.
    r1 = _prep(cfg, _media(tmp_path / "a", b"AAAA"), plan_only=True)
    r2 = _prep(cfg, _media(tmp_path / "b", b"BBBB"), plan_only=True)
    assert r1.ok and r2.ok, (r1.error, r2.error)
    assert r1.detail["run_id"] != r2.detail["run_id"]
    assert r1.detail["media_sha256"] != r2.detail["media_sha256"]


def test_delimiter_like_input_cannot_alias_another_run(tmp_path):
    a = canary._run_id_from_name(canary._canonical_run_name(
        media_sha256="a" * 64, start=0.0, end=4.0, segments=None, caption="a|b", hashtags=[], hook=None, run_label=None))
    b = canary._run_id_from_name(canary._canonical_run_name(
        media_sha256="a" * 64, start=0.0, end=4.0, segments=None, caption="a", hashtags=[], hook="b", run_label=None))
    assert a != b


def test_concrete_pinned_namespace():
    assert str(canary.CANARY_RUN_NAMESPACE) == "a1c9e6d2-7b34-5f81-9e0a-2d6f4c8b1e73"
    assert canary.CANARY_RUN_ID_VERSION == "1" and canary.BASELINE_FORMAT_VERSION == "1"


# ---------- idempotency + terminal-after-discard ----------

def test_identical_prepare_is_idempotent_noop(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r1 = _prep(cfg, media); r2 = _prep(cfg, media)
    assert r1.ok and r2.ok
    assert r1.detail["created"] is True and r2.detail["created"] is False and r2.detail["idempotent"] is True
    assert len(Ledger.load(cfg).sources) == 1                # not duplicated


def test_rerun_after_discard_is_terminal(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r1 = _prep(cfg, media); run_id = r1.detail["run_id"]
    d = canary.discard_canary(cfg, run_id); assert d.ok, d.error
    r2 = _prep(cfg, media)
    assert not r2.ok and "TERMINAL" in r2.error             # refuses; does not resurrect
    led = Ledger.load(cfg)
    assert next(iter(led.sources.values())).state is SourceState.retired
    assert next(iter(led.clips.values())).state is ClipState.retired
    assert next(iter(led.batches.values())).state is BatchState.closed


# ---------- reserved-account contract ----------

def test_prepare_refuses_active_account(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg, status="active")
    assert not _prep(cfg, _media(tmp_path)).ok


def test_prepare_refuses_non_reserved_handle_without_denylist(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    # a brand-new production-looking handle is refused purely by the allowlist (no hardcoded denylist)
    r = _prep(cfg, _media(tmp_path), handle="brand_new_production_acct")
    assert not r.ok and "fanops_canary" in r.error


def test_prepare_refuses_duplicate_integration_id(tmp_path, stub_render):
    other = {"handle": "otheracct", "account_id": "", "platforms": ["tiktok"], "status": "active",
             "integrations": {"tiktok": "tiktok-integ-999"}, "backends": {"tiktok": "zernio"}}
    cfg = Config(root=tmp_path); _seed(cfg, extra_accounts=[other])
    r = _prep(cfg, _media(tmp_path))
    assert not r.ok and "unique" in r.error


def test_prepare_refuses_shared_persona(tmp_path, stub_render):
    other = {"handle": "otheracct", "account_id": "x", "platforms": ["instagram"], "status": "active",
             "persona_id": "canary-persona"}
    cfg = Config(root=tmp_path); _seed(cfg, extra_accounts=[other])
    r = _prep(cfg, _media(tmp_path))
    assert not r.ok and "dedicated" in r.error


def test_prepare_refuses_wrong_backend_or_missing_integration(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg, backend="postiz")
    assert not _prep(cfg, _media(tmp_path)).ok
    cfg2 = Config(root=tmp_path / "b"); _seed(cfg2, integ=None)
    assert not _prep(cfg2, _media(tmp_path / "b2" if False else tmp_path)).ok


def test_prepare_over_cap_duration_refused(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path), start="0", end="100000")
    assert not r.ok and "cap" in r.error


def test_prepare_invalid_media_refused_before_mutation(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, str(tmp_path / "nope.mp4"))
    assert not r.ok
    assert Ledger.load(cfg).sources == {} and not (Path(cfg.base) / "canary").exists()


# ---------- filesystem containment / run label ----------

@pytest.mark.parametrize("label", ["../escape", ".", "..", "a/b", "UPPER", "with space", "x" * 65])
def test_unsafe_run_label_refused(tmp_path, stub_render, label):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path), run_label=label)
    assert not r.ok and "run-label" in r.error


def test_run_dir_is_generated_hex_never_user_input(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = _prep(cfg, _media(tmp_path), run_label="mylabel")
    run_dir = Path(res.detail["run_dir"])
    assert canary._RUN_ID_RE.match(run_dir.name) and "mylabel" not in run_dir.name


def test_containment_helper_rejects_escape(tmp_path):
    root = tmp_path / "canary"; root.mkdir()
    with pytest.raises(ValueError):
        canary._assert_contained(root, root / ".." / "outside")
    with pytest.raises(ValueError):
        canary._assert_contained(root, root)                 # root itself is not a strict descendant


def test_discard_cleanup_never_escapes_run_dir(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = _prep(cfg, media); run_id = res.detail["run_id"]
    outside = Path(cfg.base) / "canary" / "SENTINEL_OUTSIDE.txt"; outside.write_text("keep")
    d = canary.discard_canary(cfg, run_id); assert d.ok
    assert not (Path(cfg.base) / "canary" / run_id).exists() # run dir gone
    assert outside.exists()                                  # sibling outside the run dir untouched


# ---------- crash recovery ----------

def test_render_failure_zero_ledger_adoption(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    monkeypatch.setattr(canary, "_do_render_single", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg boom")))
    r = _prep(cfg, media)
    assert not r.ok and "render failed" in r.error
    assert Ledger.load(cfg).sources == {}                    # no adoption


def test_crash_after_render_rerun_adopts_identical(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    # simulate crash AFTER render, BEFORE adoption: render writes the clip, then adoption raises
    orig_tx = Ledger.transaction
    monkeypatch.setattr(Ledger, "transaction", staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))))
    with pytest.raises(RuntimeError):
        _prep(cfg, media)
    monkeypatch.setattr(Ledger, "transaction", orig_tx)      # recover
    calls_before = stub_render["single"]
    r = _prep(cfg, media)                                    # rerun reuses the validated render
    assert r.ok and r.detail["created"] is True
    assert stub_render["single"] == calls_before             # render NOT re-invoked (orphan reused)
    assert len(Ledger.load(cfg).clips) == 1


def test_orphan_mismatched_fingerprint_refused(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    plan = _prep(cfg, media, plan_only=True)
    run_dir = Path(plan.detail["run_dir"]); run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "canary-run.json").write_text(json.dumps({"fingerprint": "deadbeef-mismatch"}))
    r = _prep(cfg, media)
    assert not r.ok and "MISMATCH" in r.error.upper()


# ---------- cancellation ----------

def _mint_awaiting_post(cfg, *, state=PostState.awaiting_approval, submission_id="fanops_tok",
                        reconcile_candidate_id=None, public_url=None, batch_target="fanops_canary"):
    # published/analyzed carry the R1 invariant (a non-empty public_url); supply one for those states only.
    if public_url is None:
        public_url = "https://www.tiktok.com/@x/video/1" if state in (PostState.published, PostState.analyzed) else ""
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="batch_canary", name="c", target_accounts=[batch_target], state=BatchState.open))
        led.add_clip(Clip(id="clip_x", parent_id="m_x", path="/x", state=ClipState.queued))
        led.posts["post_canary"] = Post(id="post_canary", parent_id="clip_x", account="fanops_canary",
                                        account_id="ii", platform=Platform.tiktok, caption="c",
                                        state=state, submission_id=submission_id,
                                        reconcile_candidate_id=reconcile_candidate_id,
                                        public_url=public_url, batch_id="batch_canary")
    return "post_canary"


def test_cancel_retires_awaiting_and_persists_reason(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg)
    r = canary.cancel_canary_post(cfg, pid, reason="probe done")
    assert r.ok and r.detail["state"] == "retired"
    p = Ledger.load(cfg).posts[pid]
    assert p.state is PostState.retired and p.error_reason.startswith("canary_cancelled:")
    assert p.submission_id == "fanops_tok"                   # birth identity preserved


@pytest.mark.parametrize("state", [PostState.submitting, PostState.submitted, PostState.needs_reconcile,
                                   PostState.published, PostState.analyzed, PostState.failed, PostState.rejected,
                                   PostState.retired])
def test_cancel_refuses_non_precancel_states(tmp_path, state):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg, state=state)
    assert not canary.cancel_canary_post(cfg, pid, reason="x").ok


def test_cancel_refuses_real_submission_id_even_if_state_ok(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg, submission_id="REAL_provider_id_123")
    r = canary.cancel_canary_post(cfg, pid, reason="x")
    assert not r.ok and "submission_id" in r.error


def test_cancel_refuses_reconcile_candidate(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg, reconcile_candidate_id="cand_999")
    assert not canary.cancel_canary_post(cfg, pid, reason="x").ok


def test_cancel_refuses_non_canary_account(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="b", name="c", target_accounts=["someoneelse"], state=BatchState.open))
        led.posts["p"] = Post(id="p", parent_id="c", account="markmakmouly", account_id="i",
                              platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                              submission_id="fanops_t", batch_id="b")
    assert not canary.cancel_canary_post(cfg, "p", reason="x").ok


def test_cancel_audit_failure_leaves_post_retired_with_warning(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg)
    def _boom(*a, **k): raise RuntimeError("audit disk full")
    monkeypatch.setattr(canary, "write_audit", _boom)
    r = canary.cancel_canary_post(cfg, pid, reason="x")
    assert r.ok and r.detail["audit_warning"]                # success + visible warning
    assert Ledger.load(cfg).posts[pid].state is PostState.retired   # NOT rolled back


def test_retired_canary_post_cannot_publish_requeue_or_remint(tmp_path):
    from fanops.crosspost import _REUSABLE_CLIP_STATES  # noqa: F401
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _mint_awaiting_post(cfg)
    canary.cancel_canary_post(cfg, pid, reason="x")
    led = Ledger.load(cfg)
    # publish_due iterates queued only; requeue selects failed only; crosspost pops rejected/failed only.
    assert led.posts[pid].state is PostState.retired
    assert led.posts_in_state(PostState.queued) == []
    assert led.posts_in_state(PostState.failed) == []
    assert led.posts[pid].state not in (PostState.rejected, PostState.failed)


# ---------- discard ----------

def test_discard_refuses_after_post_exists(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = _prep(cfg, media); run_id = res.detail["run_id"]
    # a Post now references the canary clip -> discard is pre-mint only
    with Ledger.transaction(cfg) as led:
        cid = res.detail["clip_id"]
        led.posts["p"] = Post(id="p", parent_id=cid, account="fanops_canary", account_id="i",
                              platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                              submission_id="fanops_t", batch_id=res.detail["batch_id"])
    r = canary.discard_canary(cfg, run_id)
    assert not r.ok and "pre-mint" in r.error


def test_discard_only_touches_canary_lineage(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    # a foreign production lineage that must remain byte-identical
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_prod", state=SourceState.moments_decided, source_path="/p"))
        led.add_clip(Clip(id="clip_prod", parent_id="m_prod", path="/p", state=ClipState.captioned))
    res = _prep(cfg, _media(tmp_path)); run_id = res.detail["run_id"]
    d = canary.discard_canary(cfg, run_id); assert d.ok, d.error
    led = Ledger.load(cfg)
    assert led.sources["src_prod"].state is SourceState.moments_decided     # untouched
    assert led.clips["clip_prod"].state is ClipState.captioned
    # the foreign maps' digest is unaffected by the canary discard for the production rows
    assert "sources" in d.detail["map_digests_changed"]                     # canary source retired => sources map changed


# ---------- baseline capture / compare ----------

def _seed_posts(cfg, posts):
    with Ledger.transaction(cfg) as led:
        for p in posts:
            led.posts[p.id] = p


def test_baseline_capture_is_candidate_and_deterministic(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    _seed_posts(cfg, [Post(id="post_a", parent_id="c", account="x", account_id="i",
                           platform=Platform.tiktok, caption="hi", state=PostState.awaiting_approval)])
    out1 = tmp_path / "b1.json"; out2 = tmp_path / "b2.json"
    r1 = canary.capture_canary_baseline(cfg, output=str(out1))
    canary.capture_canary_baseline(cfg, output=str(out2))
    assert r1.ok and r1.detail["status"] == "candidate"
    m1 = json.loads(out1.read_text()); m2 = json.loads(out2.read_text())
    assert m1["status"] == "candidate" and m1["format_version"] == "1"
    assert m1["digests"] == m2["digests"]                    # deterministic
    dump = json.dumps(m1["per_post_layers"])
    assert "hi" not in dump                                  # the raw caption text is redacted...
    assert "caption_sha256" in dump                          # ...and replaced by its sha256


def test_capture_has_no_accepted_flag():
    import inspect
    sig = inspect.signature(canary.capture_canary_baseline)
    assert "accepted" not in sig.parameters                  # capture cannot self-accept


def test_compare_detects_raw_and_separates_layers(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    _seed_posts(cfg, [Post(id="post_a", parent_id="c", account="x", account_id="i", platform=Platform.tiktok,
                           caption="hi", state=PostState.awaiting_approval, scheduled_time="2026-01-01T00:00:00Z")])
    base = tmp_path / "b.json"; canary.capture_canary_baseline(cfg, output=str(base))
    # (a) schedule-only change -> scheduling_changed, NOT safety_critical
    with Ledger.transaction(cfg) as led:
        led.posts["post_a"] = led.posts["post_a"].model_copy(update={"scheduled_time": "2026-02-02T00:00:00Z"})
    c = canary.compare_canary_baseline(cfg, baseline=str(base))
    assert c.ok and c.detail["mismatch"] and c.detail["scheduling_changed"] == ["post_a"]
    assert c.detail["safety_critical_changed"] == {}
    # (b) add + remove ids
    with Ledger.transaction(cfg) as led:
        del led.posts["post_a"]
        led.posts["post_b"] = Post(id="post_b", parent_id="c", account="x", account_id="i",
                                   platform=Platform.tiktok, caption="new", state=PostState.awaiting_approval)
    c2 = canary.compare_canary_baseline(cfg, baseline=str(base))
    assert c2.detail["added"] == ["post_b"] and c2.detail["removed"] == ["post_a"]


def test_compare_safety_field_named(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    _seed_posts(cfg, [Post(id="post_a", parent_id="c", account="x", account_id="i", platform=Platform.tiktok,
                           caption="hi", state=PostState.awaiting_approval)])
    base = tmp_path / "b.json"; canary.capture_canary_baseline(cfg, output=str(base))
    with Ledger.transaction(cfg) as led:
        led.posts["post_a"] = led.posts["post_a"].model_copy(update={"state": PostState.queued})
    c = canary.compare_canary_baseline(cfg, baseline=str(base))
    assert "state" in c.detail["safety_critical_changed"]["post_a"]


def test_compare_rejects_wrong_format_version(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps({"format_version": "999"}))
    assert not canary.compare_canary_baseline(cfg, baseline=str(bad)).ok


# ---------- no forbidden calls ----------

def test_prepare_invokes_no_pipeline_publish_or_network(tmp_path, stub_render, monkeypatch):
    import fanops.pipeline, fanops.crosspost, fanops.post.run, fanops.reconcile, fanops.studio.actions
    spies = {}
    for mod, name in [(fanops.pipeline, "advance"), (fanops.crosspost, "crosspost_clips"),
                      (fanops.post.run, "publish_due"), (fanops.post.run, "publish_post"),
                      (fanops.reconcile, "reconcile_due"), (fanops.studio.actions, "crosspost_to_account")]:
        spy = pytest.importorskip("unittest.mock").MagicMock(side_effect=AssertionError(f"{name} called"))
        monkeypatch.setattr(mod, name, spy); spies[name] = spy
    cfg = Config(root=tmp_path); _seed(cfg)
    assert _prep(cfg, _media(tmp_path)).ok
    for spy in spies.values():
        spy.assert_not_called()
