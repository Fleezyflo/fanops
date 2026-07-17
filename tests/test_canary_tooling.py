# tests/test_canary_tooling.py — offline proofs for the isolated canary tooling (src/fanops/canary.py).
# Hermetic UNIT tests: render + probe are stubbed (no ffmpeg), no network/LLM/agent, no pipeline call.
# Covers the approved matrix + the revision-round hardening: single-lineage mint (0 Posts/0 Renders),
# EXACT idempotency, terminal-after-discard, content-addressed identity, reserved-account ONE-SHOT allowlist,
# filesystem containment, ATOMIC render finalization, discard identity-binding + TOCTOU, run-authenticated
# cancellation, finite/bounded time validation, and a read-only NON-DISCLOSIVE baseline capture/compare.
import json
import argparse
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Batch, Post, SourceState, MomentState, ClipState,
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
    # `realized` tracks the last requested clip window so the stubbed probe of a rendered clip returns a
    # duration WITHIN the Phase-4 strict-probe tolerance of the window (the source probes at a fixed 30s).
    calls = {"single": 0, "supercut": 0, "realized": 4.0}
    def _probe(path):
        if "clip" in Path(str(path)).name:                  # a rendered clip or its .part temp
            return (1080, 1920, calls["realized"])
        return (1080, 1920, 30.0)                            # the source media
    def _single(src, dst, cs, ce, aspect, *, src_w, src_h):
        calls["single"] += 1; calls["realized"] = round(ce - cs, 3)
        Path(dst).write_bytes(b"RENDERED-SINGLE"); return type("R", (), {"returncode": 0})()
    def _super(src, dst, spans, aspect, *, src_w, src_h):
        calls["supercut"] += 1; calls["realized"] = round(sum(e - s for s, e in spans), 3)
        Path(dst).write_bytes(b"RENDERED-SUPER"); return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(canary, "_do_probe", _probe)
    monkeypatch.setattr(canary, "_do_render_single", _single)
    monkeypatch.setattr(canary, "_do_render_supercut", _super)
    return calls


def _prep(cfg, media, **kw):
    kw.setdefault("start", "0"); kw.setdefault("end", "4"); kw.setdefault("caption", "canary caption")
    return canary.prepare_canary_lineage(cfg, media_path=media, **kw)


def _prepare_and_mint_post(cfg, tmp_path, *, state=PostState.awaiting_approval, submission_id="fanops_tok",
                           reconcile_candidate_id=None, public_url=None, account="fanops_canary",
                           account_id="tiktok-integ-999"):
    """Prepare a REAL canary lineage (with a run record on disk) then mint a Post against its clip/batch — so
    the Phase-6 run-authentication finds a genuine authenticated run to bind the post to."""
    res = _prep(cfg, _media(tmp_path))
    assert res.ok, res.error
    cid, bid = res.detail["clip_id"], res.detail["batch_id"]
    if public_url is None:
        public_url = "https://www.tiktok.com/@x/video/1" if state in (PostState.published, PostState.analyzed) else ""
    with Ledger.transaction(cfg) as led:
        led.posts["post_canary"] = Post(id="post_canary", parent_id=cid, account=account,
                                        account_id=account_id, platform=Platform.tiktok, caption="c",
                                        state=state, submission_id=submission_id,
                                        reconcile_candidate_id=reconcile_candidate_id,
                                        public_url=public_url, batch_id=bid)
    return "post_canary"


def _seed_valid_canary_lineage(cfg, *, media_sha256, start, end, caption="other-run", handle="fanops_canary"):
    """Hand-seed a second, fully-valid canary-shaped lineage (bypassing the account guard) so a Phase-1
    regression can prove a tampered record cannot select ANOTHER valid canary lineage's rows."""
    cn = canary._canonical_run_name(media_sha256=media_sha256, start=start, end=end, segments=None,
                                    caption=caption, hashtags=[], hook=None, run_label=None)
    run_id = canary._run_id_from_name(cn); fp = canary._sha256_text(cn)
    ids = canary._lineage_ids(run_id=run_id, media_sha256=media_sha256, start=start, end=end, segments=None)
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id=ids["batch_id"], name="seed", target_accounts=[handle], state=BatchState.open))
        led.add_source(Source(id=ids["source_id"], state=SourceState.moments_decided, source_path="/seed/media.mp4",
                             sha256=media_sha256, batch_id=ids["batch_id"]))
        led.add_moment(Moment(id=ids["moment_id"], parent_id=ids["source_id"], state=MomentState.clipped,
                             start=start, end=end, reason="seed", affinities=[handle], content_token=fp))
        led.add_clip(Clip(id=ids["clip_id"], parent_id=ids["moment_id"], state=ClipState.queued,
                         path="/seed/clip.mp4", aspect=Fmt.r9x16))
    return ids


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


def test_hook_is_persisted_on_the_moment(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = canary.prepare_canary_lineage(cfg, media_path=_media(tmp_path), start="0", end="4",
                                      caption="c", hook="POV the drop hits")
    assert r.ok, r.error
    assert next(iter(Ledger.load(cfg).moments.values())).hook == "POV the drop hits"


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
    # plan-only on each: minting BOTH would (correctly) trip the one-shot account guard. The full sha256 still
    # flows through the real prepare() entry-point into the derived run id. _media makes each subdir.
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


# ---------- idempotency + terminal-after-discard (Phase 3) ----------

def test_identical_prepare_is_idempotent_noop(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r1 = _prep(cfg, media); r2 = _prep(cfg, media)
    assert r1.ok and r2.ok
    assert r1.detail["created"] is True and r2.detail["created"] is False and r2.detail["idempotent"] is True
    assert len(Ledger.load(cfg).sources) == 1                # not duplicated


def test_idempotent_refuses_full_but_mismatched_source_state(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r1 = _prep(cfg, media); sid = r1.detail["source_id"]
    with Ledger.transaction(cfg) as led:                      # a full lineage whose source state was mutated
        led.set_source_state(sid, SourceState.catalogued)
    r2 = _prep(cfg, media)
    assert not r2.ok and "MISMATCH" in r2.error and "moments_decided" in r2.error


def test_idempotent_refuses_tampered_clip_bytes(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r1 = _prep(cfg, media)
    clip = Path(r1.detail["run_dir"]) / "clip.mp4"
    clip.write_bytes(b"TAMPERED-DIFFERENT-BYTES")            # probe-valid (stub) but a different sha256
    r2 = _prep(cfg, media)
    assert not r2.ok and "MISMATCH" in r2.error and "clip" in r2.error.lower()


def test_partial_lineage_refuses_idempotent_claim(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    plan = _prep(cfg, media, plan_only=True)                  # derive the run's content-addressed ids
    with Ledger.transaction(cfg) as led:                      # seed ONLY the source -> a PARTIAL lineage
        led.add_source(Source(id=plan.detail["source_id"], state=SourceState.moments_decided, source_path="/x"))
    r = _prep(cfg, media)
    assert not r.ok and "PARTIAL" in r.error                 # not falsely reported as idempotent


def test_run_record_written_after_adoption_and_recovers_if_deleted(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    r = _prep(cfg, media); assert r.ok
    rec = Path(r.detail["run_dir"]) / "canary-run.json"
    assert rec.exists()                                      # record published only AFTER adoption
    rec.unlink()                                             # simulate a crash in the commit->write gap
    r2 = _prep(cfg, media)                                   # idempotent re-prepare recovers it
    assert r2.ok and r2.detail["idempotent"] and rec.exists()


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


# ---------- reserved-account contract (Phase 7: one-shot) ----------

def test_prepare_refuses_active_account(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg, status="active")
    assert not _prep(cfg, _media(tmp_path)).ok


def test_prepare_refuses_non_reserved_handle_without_denylist(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
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
    assert not _prep(cfg2, _media(tmp_path)).ok


def test_any_canary_post_blocks_a_new_run_even_retired(tmp_path):
    # Phase 7: a minted canary run is ONE-SHOT. ANY Post on the handle/integration — even a retired one — blocks
    # a fresh run (no cancel->new-run reuse; that would change the account-history isolation contract).
    cfg = Config(root=tmp_path); _seed(cfg)
    ids = {"moment_id": "m_new", "batch_id": "b_new"}
    with Ledger.transaction(cfg) as led:
        led.posts["pc"] = Post(id="pc", parent_id="c", account="fanops_canary", account_id="i",
                               platform=Platform.tiktok, caption="c", state=PostState.retired,
                               submission_id="fanops_x", error_reason="canary_cancelled: probe done")
    integ, err = canary._validate_canary_account(cfg, "fanops_canary", Ledger.load(cfg), ids)
    assert integ is None and err is not None and "one-shot" in err.lower()


def test_prepare_over_cap_duration_refused(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path), start="0", end="100000")
    assert not r.ok and "cap" in r.error


def test_prepare_invalid_media_refused_before_mutation(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, str(tmp_path / "nope.mp4"))
    assert not r.ok
    assert Ledger.load(cfg).sources == {} and not (Path(cfg.base) / "canary").exists()


# ---------- Phase 8: finite + bounded time validation ----------

def test_canon_rejects_nonfinite_values():
    with pytest.raises(ValueError):
        canary._canon({"x": float("inf")})
    with pytest.raises(ValueError):
        canary._canon({"x": float("nan")})


@pytest.mark.parametrize("start,end", [("nan", "4"), ("inf", "4"), ("0", "nan"), ("-1", "4")])
def test_prepare_refuses_nonfinite_or_negative_time(tmp_path, stub_render, start, end):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path), start=start, end=end)
    assert not r.ok and ("finite" in r.error or "non-negative" in r.error)
    assert Ledger.load(cfg).sources == {}


def test_prepare_refuses_overlapping_segments(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = canary.prepare_canary_lineage(cfg, media_path=_media(tmp_path), start="0", end=None,
                                      segments=[(0.0, 5.0), (3.0, 8.0)], caption="x")
    assert not r.ok and ("ascending" in r.error or "overlap" in r.error)


def test_prepare_refuses_reversed_segment(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = canary.prepare_canary_lineage(cfg, media_path=_media(tmp_path), start="0", end=None,
                                      segments=[(5.0, 2.0)], caption="x")
    assert not r.ok and "end > start" in r.error


def test_prepare_refuses_window_beyond_source_duration(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)                   # stub source duration is 30s
    r = _prep(cfg, _media(tmp_path), start="0", end="40")
    assert not r.ok and "source" in r.error
    assert Ledger.load(cfg).sources == {}


def test_prepare_refuses_negative_segment_start(tmp_path, stub_render):
    # a negative first-segment start would render a DIFFERENT window that can still pass the duration tolerance
    cfg = Config(root=tmp_path); _seed(cfg)
    r = canary.prepare_canary_lineage(cfg, media_path=_media(tmp_path), start="0", end=None,
                                      segments=[(-1.0, 2.0)], caption="x")
    assert not r.ok and "non-negative" in r.error
    assert Ledger.load(cfg).sources == {}


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


# ---------- Phase 4: atomic render finalization ----------

def test_render_leaves_no_temp_and_stamps_clip_sha(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = _prep(cfg, _media(tmp_path))
    assert res.ok and canary._HEX64_RE.match(res.detail["clip_sha256"])
    run_dir = Path(res.detail["run_dir"])
    assert (run_dir / "clip.mp4").exists()
    assert list(run_dir.glob("clip.*.part.mp4")) == []       # the unique temp was atomically consumed


def test_render_failure_zero_ledger_adoption(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    monkeypatch.setattr(canary, "_do_render_single", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg boom")))
    r = _prep(cfg, media)
    assert not r.ok and "render failed" in r.error
    assert Ledger.load(cfg).sources == {}                    # no adoption
    assert list((Path(cfg.base) / "canary").rglob("clip.*.part.mp4")) == []   # temp cleaned up


def test_zero_duration_render_refused(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    def _probe(path):
        if "clip" in Path(str(path)).name: return (1080, 1920, 0.0)   # rendered clip probes to ZERO duration
        return (1080, 1920, 30.0)
    monkeypatch.setattr(canary, "_do_probe", _probe)
    r = _prep(cfg, _media(tmp_path))
    assert not r.ok and "validation" in r.error and "duration" in r.error
    assert Ledger.load(cfg).sources == {}


def test_truncated_empty_render_refused(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    monkeypatch.setattr(canary, "_do_render_single",
                        lambda src, dst, *a, **k: (Path(dst).write_bytes(b""), type("R", (), {"returncode": 0})())[1])
    r = _prep(cfg, _media(tmp_path))
    assert not r.ok and "validation" in r.error              # empty file -> strict probe rejects
    assert Ledger.load(cfg).sources == {}


def test_probe_exception_on_clip_refuses_adoption(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    def _probe(path):                                        # media probes fine; ANY clip artifact does not
        if "clip" in Path(str(path)).name: raise RuntimeError("corrupt clip")
        return (1080, 1920, 30.0)
    monkeypatch.setattr(canary, "_do_probe", _probe)
    r = _prep(cfg, _media(tmp_path))
    assert not r.ok and "validation" in r.error             # a clip we cannot probe is NEVER adopted (fail-closed)
    assert Ledger.load(cfg).sources == {}


def test_crash_after_render_rerun_reuses_valid_final_and_sweeps_orphan_temp(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    # crash AFTER render (clip.mp4 promoted) but BEFORE adoption commits
    orig_tx = Ledger.transaction
    monkeypatch.setattr(Ledger, "transaction", staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))))
    with pytest.raises(RuntimeError):
        _prep(cfg, media)
    monkeypatch.setattr(Ledger, "transaction", orig_tx)      # recover
    # plant a crash-orphan temp next to the valid final
    plan = _prep(cfg, media, plan_only=True)
    orphan = Path(plan.detail["run_dir"]) / "clip.ORPHAN.part.mp4"; orphan.write_bytes(b"junk")
    calls_before = stub_render["single"]
    r = _prep(cfg, media)                                    # rerun reuses the validated final, sweeps the temp
    assert r.ok and r.detail["created"] is True
    assert stub_render["single"] == calls_before             # render NOT re-invoked (valid final reused)
    assert not orphan.exists()                               # orphan temp swept
    assert len(Ledger.load(cfg).clips) == 1


def test_orphan_mismatched_fingerprint_refused(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    plan = _prep(cfg, media, plan_only=True)
    run_dir = Path(plan.detail["run_dir"]); run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "canary-run.json").write_text(json.dumps({"fingerprint": "deadbeef-mismatch"}))
    r = _prep(cfg, media)
    assert not r.ok and "MISMATCH" in r.error.upper()


# ---------- cancellation (Phase 6: run-authenticated) ----------

def test_cancel_retires_awaiting_and_persists_reason(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path)
    r = canary.cancel_canary_post(cfg, pid, reason="probe done")
    assert r.ok, r.error
    assert r.detail["state"] == "retired"
    p = Ledger.load(cfg).posts[pid]
    assert p.state is PostState.retired and p.error_reason.startswith("canary_cancelled:")
    assert p.submission_id == "fanops_tok"                   # birth identity preserved


@pytest.mark.parametrize("state", [PostState.submitting, PostState.submitted, PostState.needs_reconcile,
                                   PostState.published, PostState.analyzed, PostState.failed, PostState.rejected,
                                   PostState.retired])
def test_cancel_refuses_non_precancel_states(tmp_path, stub_render, state):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path, state=state)
    assert not canary.cancel_canary_post(cfg, pid, reason="x").ok


def test_cancel_refuses_real_submission_id_even_if_state_ok(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path, submission_id="REAL_provider_id_123")
    r = canary.cancel_canary_post(cfg, pid, reason="x")
    assert not r.ok and "submission_id" in r.error


def test_cancel_refuses_reconcile_candidate(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path, reconcile_candidate_id="cand_999")
    assert not canary.cancel_canary_post(cfg, pid, reason="x").ok


def test_cancel_refuses_wrong_integration_id(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path, account_id="SOME-OTHER-INTEG")
    r = canary.cancel_canary_post(cfg, pid, reason="x")
    assert not r.ok and "integration" in r.error


def test_cancel_refuses_canary_post_without_an_authenticated_run(tmp_path):
    # Phase 6 negative: a hand-inserted Post + Batch(target=[canary]) with NO real run record is NOT cancellable.
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="batch_fake", name="c", target_accounts=["fanops_canary"], state=BatchState.open))
        led.posts["p"] = Post(id="p", parent_id="clip_fake", account="fanops_canary", account_id="tiktok-integ-999",
                              platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                              submission_id="fanops_t", batch_id="batch_fake")
    r = canary.cancel_canary_post(cfg, "p", reason="x")
    assert not r.ok and "authenticated canary run" in r.error
    assert Ledger.load(cfg).posts["p"].state is PostState.awaiting_approval   # untouched


def test_cancel_refuses_non_canary_account(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="b", name="c", target_accounts=["someoneelse"], state=BatchState.open))
        led.posts["p"] = Post(id="p", parent_id="c", account="markmakmouly", account_id="i",
                              platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                              submission_id="fanops_t", batch_id="b")
    assert not canary.cancel_canary_post(cfg, "p", reason="x").ok


def test_cancel_audit_failure_leaves_post_retired_with_warning(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path)
    def _boom(*a, **k): raise RuntimeError("audit disk full")
    monkeypatch.setattr(canary, "write_audit", _boom)
    r = canary.cancel_canary_post(cfg, pid, reason="x")
    assert r.ok and r.detail["audit_warning"]                # success + visible warning
    assert Ledger.load(cfg).posts[pid].state is PostState.retired   # NOT rolled back


def test_retired_canary_post_cannot_publish_requeue_or_remint(tmp_path, stub_render):
    # exercise the ACTUAL guarded entry points, not just state membership: the real publish path
    # (publish_due, which also runs the daemon transient-failed requeue) and the crosspost re-mint seed predicate.
    from fanops.post.run import publish_due
    from fanops.crosspost import _seed_clips
    cfg = Config(root=tmp_path); _seed(cfg)
    pid = _prepare_and_mint_post(cfg, tmp_path)
    cid = Ledger.load(cfg).posts[pid].parent_id
    canary.cancel_canary_post(cfg, pid, reason="x")
    res = publish_due(cfg)                                    # dryrun (cfg not live) -> no network; retired post invisible
    assert res["due"] == 0 and res["published"] == 0
    led = Ledger.load(cfg)
    assert led.posts[pid].state is PostState.retired         # untouched by publish + transient-failed requeue
    assert led.posts[pid].submission_id == "fanops_tok"      # not re-driven
    assert cid not in [c.id for c in _seed_clips(led)]       # the crosspost re-mint predicate never re-seeds it
    assert led.posts_in_state(PostState.queued) == [] and led.posts_in_state(PostState.failed) == []


# ---------- discard (Phase 1 identity binding + Phase 2 TOCTOU) ----------

def test_discard_refuses_after_post_exists(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg); media = _media(tmp_path)
    res = _prep(cfg, media); run_id = res.detail["run_id"]
    with Ledger.transaction(cfg) as led:
        cid = res.detail["clip_id"]
        led.posts["p"] = Post(id="p", parent_id=cid, account="fanops_canary", account_id="i",
                              platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                              submission_id="fanops_t", batch_id=res.detail["batch_id"])
    r = canary.discard_canary(cfg, run_id)
    assert not r.ok and "pre-mint" in r.error


def test_discard_only_touches_canary_lineage(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_prod", state=SourceState.moments_decided, source_path="/p"))
        led.add_clip(Clip(id="clip_prod", parent_id="m_prod", path="/p", state=ClipState.captioned))
    res = _prep(cfg, _media(tmp_path)); run_id = res.detail["run_id"]
    d = canary.discard_canary(cfg, run_id); assert d.ok, d.error
    led = Ledger.load(cfg)
    assert led.sources["src_prod"].state is SourceState.moments_decided     # untouched
    assert led.clips["clip_prod"].state is ClipState.captioned
    assert "sources" in d.detail["map_digests_changed"]                     # canary source retired => sources map changed


def test_discard_refuses_a_tampered_record_pointing_at_a_foreign_row(tmp_path, stub_render):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_prod", state=SourceState.moments_decided, source_path="/p"))
    r = _prep(cfg, _media(tmp_path)); run_id = r.detail["run_id"]
    rec_path = Path(r.detail["run_dir"]) / "canary-run.json"
    rec = json.loads(rec_path.read_text()); rec["source_id"] = "src_prod"   # tamper: aim at the foreign row
    rec_path.write_text(json.dumps(rec))
    d = canary.discard_canary(cfg, run_id)
    assert not d.ok and "refusing" in d.error and "foreign lineage" in d.error
    assert Ledger.load(cfg).sources["src_prod"].state is SourceState.moments_decided  # untouched


def test_discard_refuses_record_swapped_to_another_valid_canary_lineage(tmp_path, stub_render):
    # Phase 1 regression: even when the swapped-in ids form a VALID, parent-linked canary chain (which the old
    # parent-chain-only check would have accepted), the recompute-and-bind check refuses and retires nothing.
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path)); run_id = r.detail["run_id"]
    seeded = _seed_valid_canary_lineage(cfg, media_sha256="b" * 64, start=0.0, end=3.0)
    rec_path = Path(r.detail["run_dir"]) / "canary-run.json"; rec = json.loads(rec_path.read_text())
    for k in ("source_id", "moment_id", "clip_id", "batch_id"):
        rec[k] = seeded[k]                                    # swap ALL four ids to the other valid canary lineage
    rec_path.write_text(json.dumps(rec))
    d = canary.discard_canary(cfg, run_id)
    assert not d.ok and "foreign lineage" in d.error
    led = Ledger.load(cfg)
    assert led.sources[seeded["source_id"]].state is SourceState.moments_decided   # seeded lineage untouched
    assert led.clips[seeded["clip_id"]].state is ClipState.queued
    assert led.sources[r.detail["source_id"]].state is SourceState.moments_decided  # real lineage still live


def test_discard_refuses_when_a_post_is_minted_before_the_transaction(tmp_path, stub_render, monkeypatch):
    # Phase 2 TOCTOU: a Post that commits AFTER the pre-checks but BEFORE the discard transaction is seen inside
    # the transaction and refuses discard, leaving the lineage AND the filesystem untouched.
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path)); run_id = r.detail["run_id"]
    cid, bid = r.detail["clip_id"], r.detail["batch_id"]
    orig_audit = canary._audit_has_mint_evidence
    def _inject(cfg_, **kw):                                  # runs immediately before the discard transaction
        with Ledger.transaction(cfg_) as led:
            led.posts["late"] = Post(id="late", parent_id=cid, account="fanops_canary", account_id="i",
                                     platform=Platform.tiktok, caption="c", state=PostState.awaiting_approval,
                                     submission_id="fanops_t", batch_id=bid)
        return orig_audit(cfg_, **kw)                         # (returns False on a clean audit log)
    monkeypatch.setattr(canary, "_audit_has_mint_evidence", _inject)
    d = canary.discard_canary(cfg, run_id)
    assert not d.ok and "pre-mint" in d.error
    led = Ledger.load(cfg)
    assert led.sources[r.detail["source_id"]].state is SourceState.moments_decided   # NOT retired
    assert (Path(r.detail["run_dir"]) / "canary-run.json").exists()                  # filesystem untouched


def test_discard_refuses_when_audit_log_is_unreadable(tmp_path, stub_render, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _prep(cfg, _media(tmp_path)); run_id = r.detail["run_id"]
    (cfg.control / "studio_audit.log").write_text("x")
    orig = Path.read_text
    def _boom(self, *a, **k):
        if self.name == "studio_audit.log": raise OSError("perm denied")
        return orig(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", _boom)
    d = canary.discard_canary(cfg, run_id)
    assert not d.ok and "audit" in d.error.lower()          # fail CLOSED: can't rule out evidence -> refuse


# ---------- baseline capture / compare (Phase 5: non-disclosive + strict) ----------

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


def test_baseline_emits_no_raw_url_or_token_from_any_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    secret_url = "https://cdn.secret.example/upload?sig=SUPERSECRETSIG42"
    secret_perma = "https://www.tiktok.com/@canary/video/SECRETVIDEOID"
    _seed_posts(cfg, [Post(id="post_a", parent_id="c", account="x", account_id="INTEG-SECRET-1",
                           platform=Platform.tiktok, caption="caption https://leak.example/x", state=PostState.published,
                           submission_id="REAL-SUBMISSION-SECRET", public_url=secret_perma,
                           media_urls=[secret_url])])
    out = tmp_path / "b.json"; assert canary.capture_canary_baseline(cfg, output=str(out)).ok
    text = out.read_text()
    for leak in (secret_url, secret_perma, "REAL-SUBMISSION-SECRET", "INTEG-SECRET-1", "leak.example"):
        assert leak not in text, leak                        # NOTHING URL/token-bearing appears verbatim


def test_capture_has_no_accepted_flag():
    import inspect
    sig = inspect.signature(canary.capture_canary_baseline)
    assert "accepted" not in sig.parameters                  # capture cannot self-accept


def test_compare_detects_raw_and_separates_layers(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    _seed_posts(cfg, [Post(id="post_a", parent_id="c", account="x", account_id="i", platform=Platform.tiktok,
                           caption="hi", state=PostState.awaiting_approval, scheduled_time="2026-01-01T00:00:00Z")])
    base = tmp_path / "b.json"; canary.capture_canary_baseline(cfg, output=str(base))
    with Ledger.transaction(cfg) as led:
        led.posts["post_a"] = led.posts["post_a"].model_copy(update={"scheduled_time": "2026-02-02T00:00:00Z"})
    c = canary.compare_canary_baseline(cfg, baseline=str(base))
    assert c.ok and c.detail["mismatch"] and c.detail["scheduling_changed"] == ["post_a"]
    assert c.detail["safety_critical_changed"] == {}
    with Ledger.transaction(cfg) as led:
        del led.posts["post_a"]
        led.posts["post_b"] = Post(id="post_b", parent_id="c", account="x", account_id="i",
                                   platform=Platform.tiktok, caption="new", state=PostState.awaiting_approval)
    c2 = canary.compare_canary_baseline(cfg, baseline=str(base))
    assert c2.detail["added"] == ["post_b"] and c2.detail["removed"] == ["post_a"] and c2.detail["mismatch"]


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


def test_compare_rejects_malformed_or_null_baseline(tmp_path):
    # Phase 5 REVERSAL of the prior null-tolerance: a null/malformed layer is an ERROR, never a clean comparison.
    cfg = Config(root=tmp_path); _seed(cfg)
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"format_version": "1", "status": "candidate",
                               "canonicalization": {"json": "x", "row_order": "x", "aggregate": "x", "hash": "sha256"},
                               "schema_version": 11, "post_count": 0, "state_distribution": {}, "repo_commit": "x",
                               "digests": None, "per_post_manifest": None, "per_post_layers": None, "frozen_incident": None}))
    r = canary.compare_canary_baseline(cfg, baseline=str(bad))
    assert not r.ok and "invalid baseline" in r.error


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


# ---------- CLI segment parsing (Phase 8 finite guard) ----------

def test_parse_segments_rejects_malformed_or_nonfinite_input(tmp_path):
    from fanops.cli import _parse_segments
    assert _parse_segments("0-2,5-7") == [(0.0, 2.0), (5.0, 7.0)]
    for bad in ("not-a-range", "1", "a-b", "0-", "nan-1", "0-inf"):
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_segments(bad)
