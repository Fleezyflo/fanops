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


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _mock_burn(mocker):
    # isolate from ffmpeg: write the out file (so Render.path exists) and report a real burn.
    def burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"V"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)

def _seed_clip(led, cfg, *, hooks_by_persona=None, m_hook=None, surfaces=("@a/instagram",), batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook, hooks_by_persona=hooks_by_persona or {}))
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
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "hook A", "@b": "hook B"},
               surfaces=("@a/instagram", "@b/instagram")); led.save()
    led = _crosspost(cfg)
    assert led.renders == {}                                          # NOTHING rendered at crosspost (slice 2)
    assert all(p.render_id is None and p.media_urls == [] for p in led.posts.values())
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    assert len(led.renders) == 2                                      # two distinct hooks -> two renders, AT APPROVAL
    posts = {p.account: p for p in led.posts.values()}
    for h in ("@a", "@b"):
        p = posts[h]
        assert p.state is PostState.queued                           # approved
        r = led.get_render(p.render_id)
        assert r is not None and p.media_urls == [f"file://{r.path}"]
        assert Path(r.path).exists()                                 # publish-needs-media: the burned file is on disk
    assert posts["@a"].render_id != posts["@b"].render_id
    assert led.get_render(posts["@a"].render_id).hook_text == "hook A"

def test_approve_dedups_same_hook_to_one_render(tmp_path, monkeypatch, mocker):
    # one account on TWO 9:16 platforms (ig+tiktok) with the SAME per-account hook -> ONE content-addressed
    # Render + ONE file at approval, two posts pointing at it (anti-explosion preserved past the mint move).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"],
                          "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "one hook"},
               surfaces=("@a/instagram", "@a/tiktok")); led.save()
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
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",), batch_id="batch_xy"); led.save()
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
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "hA", "@b": "hB"},
               surfaces=("@a/instagram", "@b/instagram")); led.save()
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
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    led = _crosspost(cfg)
    mocker.patch("fanops.overlay.burn_hook_only", return_value=False)   # burn fails AT APPROVAL (no libass)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_burn_failed" in log and "@a/instagram" in log

def test_approve_skips_variant_post_when_render_cannot_materialize(tmp_path, monkeypatch, mocker):
    # publish-needs-media guard: a variant post whose clip is gone at approval (corrupt / GC'd ledger) cannot
    # be rendered -> it is NOT promoted to queued (never a silent hookless ship); it stays awaiting + a breadcrumb.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    led = _crosspost(cfg)
    pid = next(iter(led.posts.values())).id
    drop = Ledger.load(cfg); drop.clips.pop("clip_1", None); drop.save()   # simulate a vanished clip
    approve_posts(cfg, [pid])
    led = Ledger.load(cfg)
    p = led.posts[pid]
    assert p.state is PostState.awaiting_approval                # NOT queued (no render -> not shippable)
    assert p.render_id is None and p.media_urls == []
    assert led.renders == {}
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "render_unavailable_skip_approve" in log

def test_off_mode_approval_mints_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, hooks_by_persona={"@a": "h"}, surfaces=("@a/instagram",)); led.save()
    led = _crosspost(cfg)
    approve_posts(cfg, [p.id for p in led.posts.values()])
    led = Ledger.load(cfg)
    assert led.renders == {}                                          # OFF firewall holds at approval too
    p = next(iter(led.posts.values()))
    assert p.state is PostState.queued and p.render_id is None and p.media_urls == []
