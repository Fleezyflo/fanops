# tests/test_render_on_approval.py — slice 2 (burn on approval): the per-account RENDER is NO LONGER minted at
# crosspost. crosspost records only the INTENT (Post.variant_hook); ffmpeg runs when the operator APPROVES the
# surface, so ONLY approved surfaces ever render (the operator's anti-explosion ask — no "100 videos per run").
# On approval each approved variant post gets its content-addressed Render minted (dedup preserved), its
# render_id + media_urls pointed at the burned file, and is promoted awaiting->queued — so a queued post ALWAYS
# carries its media (publish never falls back to the master for a variant). OFF / hookless approval mints
# nothing (the creative_variation firewall holds at approval too).
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt, PostState
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips
from fanops.studio.actions_approve import approve_posts, approve_clip
from fanops.studio.actions_common import RENDER_PENDING_REASON
from fanops.studio.views_review import review_matrix


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _mock_burn(mocker):
    # isolate from ffmpeg: write the out file (so Render.path exists) and report a real burn.
    def burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"V"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)

def _seed_clip(led, cfg, *, m_hook=None, surfaces=("a/instagram",), batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _crosspost(cfg):
    # crosspost mutates + returns an in-memory ledger; the real pipeline persists it. PERSIST here so the
    # approve actions (which open their own on-disk Ledger.transaction) see the minted posts.
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save(); return led


# ---- the render materializes AT APPROVAL, not at crosspost ----
def test_approve_materializes_render_and_points_post(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "clip_profile": "short"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="shared hook",
               surfaces=("a/instagram", "b/instagram")); led.save()
    led = _crosspost(cfg)
    assert led.renders == {}                                          # NOTHING rendered at crosspost (slice 2)
    assert all(p.render_id is None and p.media_urls == [] for p in led.posts.values())
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    assert len(led.renders) == 2                                      # two distinct hooks -> two renders, AT APPROVAL
    posts = {p.account: p for p in led.posts.values()}
    for h in ("a", "b"):
        p = posts[h]
        assert p.state is PostState.queued                           # approved
        r = led.get_render(p.render_id)
        assert r is not None and p.media_urls == [f"file://{r.path}"]
        assert Path(r.path).exists()                                 # publish-needs-media: the burned file is on disk
    assert posts["a"].render_id != posts["b"].render_id
    assert led.get_render(posts["a"].render_id).hook_text == "shared hook"

def test_approve_dedups_same_hook_to_one_render(tmp_path, monkeypatch, mocker):
    # one account on TWO 9:16 platforms (ig+tiktok) with the SAME per-account hook -> ONE content-addressed
    # Render + ONE file at approval, two posts pointing at it (anti-explosion preserved past the mint move).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"],
                          "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="one hook",
               surfaces=("a/instagram", "a/tiktok")); led.save()
    led = _crosspost(cfg)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    assert len(led.renders) == 1                                      # same hook+aspect -> ONE render at approval
    rids = {p.render_id for p in led.posts.values()}
    assert len(rids) == 1 and None not in rids

def test_approve_files_render_under_batch_source_dirs(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",), batch_id="batch_xy"); led.save()
    led = _crosspost(cfg)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    r = next(iter(led.renders.values()))
    rp = Path(r.path)
    assert cfg.clips in rp.parents and "batch_xy" in rp.parts and "src_1" in rp.parts and rp.exists()
    assert r.batch_id == "batch_xy" and r.source_id == "src_1"

def test_approve_clip_bulk_materializes_all_surfaces(tmp_path, monkeypatch, mocker):
    # the bulk path (approve_clip -> _approve_matching) must materialize EVERY approved surface's render, not
    # just the explicit-id approve_posts batch.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "clip_profile": "short"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="shared hook",
               surfaces=("a/instagram", "b/instagram")); led.save()
    _crosspost(cfg)
    approve_clip(cfg, "clip_1")                                       # bulk: every surface of the clip
    led = Ledger.load(cfg)
    assert len(led.renders) == 2
    assert all(p.state is PostState.queued and p.render_id for p in led.posts.values())


# ---- failure + firewall at approval ----
def test_approve_burn_failure_emits_breadcrumb(tmp_path, monkeypatch, mocker):
    # the burn now runs at approval; a failed burn (no libass / nothing burnable) leaves the SAME hook_burn_failed
    # breadcrumb the mint used to, so a hookless ship is never silent.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    led = _crosspost(cfg)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=False)   # burn fails AT APPROVAL (no libass)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_burn_failed" in log and "a/instagram" in log

def test_approve_skips_variant_post_when_render_cannot_materialize(tmp_path, monkeypatch, mocker):
    # publish-needs-media guard: a variant post whose clip is gone at approval (corrupt / GC'd ledger) cannot
    # be rendered -> it is NOT promoted to queued (never a silent hookless ship); it stays awaiting + a breadcrumb.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    led = _crosspost(cfg)
    pid = next(iter(led.posts.values())).id
    drop = Ledger.load(cfg); drop.clips.pop("clip_1", None); drop.save()   # simulate a vanished clip
    approve_posts(cfg, [pid])
    led = Ledger.load(cfg)
    p = led.posts[pid]
    assert p.state is PostState.awaiting_approval                # NOT queued (no render -> not shippable)
    assert p.render_id is None and p.media_urls == []
    assert led.renders == {}
    assert p.error_reason == RENDER_PENDING_REASON               # #4: durable marker, not only a log line
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "render_unavailable_skip_approve" in log

def test_warm_miss_skips_approve_and_never_burns_under_the_flock(tmp_path, monkeypatch, mocker):
    # M1 (audit): the in-lock adopt must NEVER run ffmpeg while holding the ledger flock. When the off-flock
    # warm produced no usable render for a post (warm failed, or the hook changed since the snapshot), the
    # approve leaves it un-materialized + a breadcrumb and the NEXT warm pass renders it — it does NOT burn
    # under the lock to force it through.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    led = _crosspost(cfg)
    pid = next(iter(led.posts.values())).id
    # the off-flock warm produces NO usable plan (simulate an ffmpeg/transport failure in the warm pass)
    spy = mocker.patch("fanops.studio.actions_approve.render_account_file", side_effect=RuntimeError("warm boom"))
    approve_posts(cfg, [pid])
    led = Ledger.load(cfg)
    p = led.posts[pid]
    assert p.state is PostState.awaiting_approval                # NOT queued — never burned under the flock to force it
    assert p.render_id is None and p.media_urls == [] and led.renders == {}
    assert spy.call_count == 1                                   # called ONCE (the off-flock warm); the in-lock adopt did NOT re-call it
    assert p.error_reason == RENDER_PENDING_REASON               # #4: durable marker so Review can flag it
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "render_unavailable_skip_approve" in log

def test_warm_miss_surfaces_render_pending_in_review_matrix(tmp_path, monkeypatch, mocker):
    # #4: a warm-miss post carries a durable marker, so the Review matrix shows a 'render pending' cell
    # (not just a log line) — the operator sees exactly which surface needs a re-approve.
    from datetime import datetime, timezone
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    _crosspost(cfg)
    pid = next(iter(Ledger.load(cfg).posts.values())).id
    mocker.patch("fanops.studio.actions_approve.render_account_file", side_effect=RuntimeError("warm boom"))
    approve_posts(cfg, [pid])
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    mv = review_matrix(led, accts, cfg, source_id="src_1", now=datetime(2026, 6, 2, tzinfo=timezone.utc))
    cell = next(c for r in mv.rows for c in r.cells.values() if c)
    assert cell.render_pending is True                           # the warm-miss surface is flagged in the grid

def test_off_mode_approval_mints_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    led = _crosspost(cfg)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    assert led.renders == {}                                          # OFF firewall holds at approval too
    p = next(iter(led.posts.values()))
    assert p.state is PostState.queued and p.render_id is None and p.media_urls == []


def test_realized_cut_over_platform_cap_is_not_queued(tmp_path, monkeypatch, mocker):
    # CULM-5: a per-account CUT can widen past the platform cap (IG 90s) even when the moment window fit.
    # The realized length is known only at approval; an over-cap realized cut must NOT reach queued.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="hook A", surfaces=("a/instagram",)); led.save()
    led = _crosspost(cfg)
    p = next(pp for pp in led.posts.values() if pp.account == "a")
    from fanops.crosspost import account_render_spec
    from fanops.models import Render, RenderState
    from fanops.studio.actions_approve import _acct_for
    accts = Accounts.load(cfg); clip = led.clips["clip_1"]
    rid, *_ = account_render_spec(cfg, clip=clip, hook=p.variant_hook, acct=_acct_for(accts, "a"))
    vf = cfg.clips / "over.mp4"; vf.write_bytes(b"V")
    led.add_render(Render(id=rid, clip_id="clip_1", account="a", surface_key="a|instagram",
                          hook_text=p.variant_hook, path=str(vf), state=RenderState.rendered,
                          is_account_cut=True, cut_seconds=120.0))   # IG cap is 90 -> over-cap
    led.save()
    approve_posts(cfg, [p.id])
    p2 = Ledger.load(cfg).posts[p.id]
    assert p2.state is PostState.awaiting_approval                  # NOT queued
    assert "exceeds" in (p2.error_reason or "")                    # the over-cap reason survives (not RENDER_PENDING)
