# MOL-150 (P9): owner-moment render spec — no variant_hook / creative_variation / per-account re-resolve.
import json
from pathlib import Path
from types import SimpleNamespace
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt, PostState, Post
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips, render_spec
from fanops.studio.actions_approve import approve_posts


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _surf(handle):
    h = handle if handle.startswith("@") else f"@{handle.lstrip('@')}"
    return f"{h}/instagram"

def _clip_stub():
    return Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)

def _moment(**kw):
    base = dict(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                state=MomentState.clipped, hook="HOOK")
    base.update(kw)
    return Moment(**base)

def _seed_captioned_clip(led, cfg, moment, surfaces=None):
    if surfaces is None:
        surfaces = (_surf("@a"),)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(moment)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)


def test_render_reads_moment_cut_spec(tmp_path):
    cfg = Config(root=tmp_path)
    m = _moment(clip_profile="long", framing="top")
    clip = _clip_stub()
    rid, wants_cut, profile, top_bias = render_spec(cfg, clip=clip, hook="H", moment=m)
    assert wants_cut is True and profile == "long" and top_bias is True
    m2 = _moment(clip_profile=cfg.clip_profile, framing="center")
    _, wants2, prof2, top2 = render_spec(cfg, clip=clip, hook="H", moment=m2)
    assert wants2 is True and prof2 == cfg.clip_profile and top2 is False


def test_post_stamp_from_moment_not_account(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    monkeypatch.setattr("fanops.config.Config.resolve_clip_profile", lambda self, acct=None: "short")
    monkeypatch.setattr("fanops.config.Config.resolve_top_bias", lambda self, acct=None: True)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_captioned_clip(led, cfg, _moment(clip_profile="long", framing="top", hook="SHARED"))
    led.save()
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    p = next(iter(led.posts.values()))
    assert p.clip_profile == "long" and p.top_bias is True


def test_no_approval_reclip_stamp(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_captioned_clip(led, cfg, _moment(clip_profile="long", framing="top", hook="H"))
    led.save()
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z"); led.save()
    p = next(iter(led.posts.values()))
    assert p.clip_profile == "long"
    approve_posts(cfg, [p.id])
    p2 = Ledger.load(cfg).posts[p.id]
    assert p2.clip_profile == "long" and p2.state is PostState.queued


def test_post_has_no_variant_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    assert "variant_key" not in Post.model_fields
    assert "variant_hook" not in Post.model_fields


def test_no_creative_variation(tmp_path):
    cfg = Config(root=tmp_path)
    assert not hasattr(cfg, "creative_variation")


def test_moment_renders_once_per_aspect(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    calls = []
    def _render(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
        calls.append((moment_id, aspect))
        cid = f"clip_{aspect.value.replace(':', 'x')}"
        dst = cfg.clips / f"{cid}.mp4"; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(b"x")
        clip = Clip(id=cid, parent_id=moment_id, path=str(dst), aspect=aspect, state=ClipState.rendered)
        led.clips[cid] = clip
        return led, clip
    mocker.patch("fanops.crosspost.render_moment", side_effect=_render)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram", "twitter"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_captioned_clip(led, cfg, _moment(hook="H"),
                         surfaces=(_surf("@a"), f"{_surf('@a').split('/')[0]}/twitter"))
    led.save()
    crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(calls) == 1 and calls[0] == ("mom_1", Fmt.r16x9)


def test_supercut_branch_survives_render_fork_deletion(tmp_path, monkeypatch):
    from fanops import clip as clipmod
    calls = []
    def _sc(src, dst, spans, **kw):
        calls.append(1)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"x")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(clipmod, "render_supercut_reframed", _sc)
    monkeypatch.setattr(clipmod, "_supercut_span_entries", lambda *a, **k: ([], None))
    monkeypatch.setattr("fanops.overlay.ffmpeg_has_textfilter", lambda: False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(_moment(segments=[(0.0, 3.0), (10.0, 13.0)], hook="SUPER", start=0, end=13))
    ok, realized = clipmod.render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                                              hook="SUPER", out_path=str(tmp_path / "out.mp4"), top_bias=False)
    assert calls and ok is True and realized == 6.0
