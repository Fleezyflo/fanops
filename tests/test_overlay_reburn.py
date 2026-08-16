# tests/test_overlay_reburn.py
"""MOL-969: prove ass-only, stage then replace, refuse center fail-open. CI has no ffmpeg — mock render_reframed."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from fanops import clip as clipmod
from fanops import framing
from fanops import overlay
from fanops import overlay_reburn as ob
from fanops import reframe
from fanops import reframe_apply as ra
from fanops.config import Config
from fanops.ids import child_id
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source
from fanops.reframe import ReframePaths

_STATS = {"fps": 4.0, "frames": [[[0.5, 0.5, 0.3, 0.42, 0.9]]]}
_FOCUS = (0.61, 0.44, 0.30, 0.38)
_STACK_FOCUS = (0.2, 0.4, 0.3, 0.38, 0.2, 0.8, 0.4, 0.3, 0.38, 0.2)
_OLD_ASS = "OLD-ASS-TEXT"
_NEW_ASS = "NEW-ASS-TEXT"


def _stub_framing(monkeypatch, *, ct="talk", detect=_STATS, focus=None, track=None, sal=None):
    """Stub framing._resolve's return — prove enumerates that crop vs centered. CI has no YuNet."""
    res = type("R", (), {"focus": focus, "track": track, "content_type": ct})()
    monkeypatch.setattr(framing, "_resolve", lambda *a, **k: res)


def _stamp(cfg, led, cid, *, focus=None, track=None, content_type=None, top_bias=None, ass=_OLD_ASS):
    m, src = led.moments["mom_1"], led.sources["src_1"]
    from fanops.bands import band_for
    band = band_for(clipmod._moment_profile(m, cfg))
    cs, ce = clipmod.fit_window(m.start, m.end, src.duration, lo=band.lo, hi=band.hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration)
    tb = clipmod._moment_top_bias(m, cfg) if top_bias is None else top_bias
    p = clipmod._render_fingerprint_payload(
        src.source_path, cs, ce, Fmt.r9x16.value, 1920, 1080, ass,
        top_bias=tb, focus=focus, track=track, content_type=content_type)
    (cfg.clips / f"{cid}.render.json").write_text(json.dumps({"fp": clipmod.fingerprint_of_payload(p)}))
    return p


def _corpus(tmp_path, monkeypatch, *, hook="wait for the last line", framing_pin=None, segments=None,
            clip_id=None, media_url=None, posts=None, focus=None, track=None, content_type=None,
            top_bias=None, stamp_fp=True, extra_posts=()):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    monkeypatch.setattr(ob, "_build_ass_text", lambda *a, **k: (_NEW_ASS, False))
    prod = tmp_path / "prod"
    cfg = Config(root=prod)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    media = cfg.sources / "s.mp4"
    media.write_bytes(b"\x00" * 64)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(media), width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10.0, end=28.0,
                          reason="r", state=MomentState.clipped, hook=hook, framing=framing_pin,
                          segments=segments or []))
    cid = clip_id or child_id("clip", "mom_1", Fmt.r9x16.value)
    led.add_clip(Clip(id=cid, parent_id="mom_1", state=ClipState.rendered,
                      path=str(cfg.clips / f"{cid}.mp4"), aspect=Fmt.r9x16, media_url=media_url))
    if posts is None:
        posts = [Post(id="p_await", parent_id=cid, account="a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.awaiting_approval)]
    for p in list(posts) + list(extra_posts):
        led.add_post(p)
    led.save()
    (cfg.clips / f"{cid}.mp4").write_bytes(b"OLD-PIXELS")
    (cfg.clips / f"{cid}.ass").write_text(_OLD_ASS)
    if stamp_fp:
        _stamp(cfg, led, cid, focus=focus, track=track, content_type=content_type, top_bias=top_bias)
    return ReframePaths.build(prod, tmp_path / "scratch"), cid, cfg


def _classify(paths, cid):
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    return ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])


def _good_probe(*_a, **_k):
    return {"format": {"duration": "4.0"},
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}]}


def _stub_render(monkeypatch, out_bytes=b"NEW-PIXELS", rc=0, calls=None):
    def fake(src_path, dst, cs, ce, aspect, **kw):
        if calls is not None:
            calls.append(kw)
        if rc == 0:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(out_bytes)
        return type("R", (), {"returncode": rc})()
    monkeypatch.setattr(clipmod, "render_reframed", fake)
    monkeypatch.setattr(ra, "ffprobe_json", _good_probe)
    monkeypatch.setattr(ra, "decodes", lambda *_a, **_k: True)


class _FakeLed:
    def __init__(self, cid="clip_a", media_url=None):
        c = type("C", (), {})()
        c.id, c.parent_id, c.media_url = cid, "m1", media_url
        self.clips, self.posts = {cid: c}, {}


def test_awaiting_ass_only_eligible(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    row = _classify(paths, cid)
    assert row["classification"] == "eligible"
    assert row["payload_delta"] == ["ass"]
    assert row["payload_new"]["ass"] == _NEW_ASS
    assert row["payload_old"]["ass"] == _OLD_ASS


def test_queued_sibling_skip(tmp_path, monkeypatch):
    _stub_framing(monkeypatch)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.add_post(Post(id="p_q", parent_id=cid, account="b", account_id="2",
                      platform=Platform.instagram, caption="c", state=PostState.queued))
    led.save()
    row = _classify(paths, cid)
    assert row["classification"] == "live_or_queued_sibling"


def test_published_plus_awaiting_skip(tmp_path, monkeypatch):
    _stub_framing(monkeypatch)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.add_post(Post(id="p_pub", parent_id=cid, account="b", account_id="2",
                      platform=Platform.instagram, caption="c", state=PostState.published,
                      public_url="https://instagram.com/p/x"))
    led.save()
    row = _classify(paths, cid)
    assert row["classification"] == "live_or_queued_sibling"


def test_file_url_does_not_skip_https_does(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch, media_url="file:///tmp/x.mp4")
    assert _classify(paths, cid)["classification"] == "eligible"
    led = Ledger.load(paths.production_cfg)
    led.clips[cid] = led.clips[cid].model_copy(update={"media_url": "https://cdn/x.mp4"})
    led.save()
    assert _classify(paths, cid)["classification"] == "hosted_http"
    led.clips[cid] = led.clips[cid].model_copy(update={"media_url": None})
    led.posts["p_await"] = led.posts["p_await"].model_copy(update={"media_urls": ["https://cdn/p.mp4"]})
    led.save()
    assert _classify(paths, cid)["classification"] == "hosted_http"
    led.posts["p_await"] = led.posts["p_await"].model_copy(update={"media_urls": ["file:///tmp/p.mp4"]})
    led.save()
    assert _classify(paths, cid)["classification"] == "eligible"


def test_supercut_stitch_render_id_skip(tmp_path, monkeypatch):
    _stub_framing(monkeypatch)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch, segments=[(10.0, 16.0), (20.0, 26.0)])
    assert _classify(paths, cid)["classification"] == "supercut"
    paths, cid, _cfg = _corpus(tmp_path / "st", monkeypatch, clip_id="stitch_not_content")
    assert _classify(paths, cid)["classification"] == "stitch"
    paths, cid, _cfg = _corpus(tmp_path / "rid", monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.posts["p_await"] = led.posts["p_await"].model_copy(update={"render_id": "rnd_1"})
    led.save()
    assert _classify(paths, cid)["classification"] == "render_id"


def test_current_framing_match_when_centered_reconstruct_would_fail(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=_FOCUS)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch, focus=_FOCUS, content_type="talk")
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    rec = reframe.reconstruct(paths, paths.scratch_cfg, led, led.clips[cid],
                              paths.read_stored_fingerprint(cid))
    assert rec.proved is False
    row = ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])
    assert row["classification"] == "eligible"
    assert row["payload_old"].get("focus") == [round(v, 3) for v in _FOCUS]
    assert row["payload_delta"] == ["ass"]


def test_top_bias_axis_required_to_prove(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch, top_bias=True)
    assert _classify(paths, cid)["classification"] == "eligible"
    monkeypatch.setattr(ob, "_top_bias_candidates", lambda m, cfg: [(False, "top_bias:cfg=off")])
    assert _classify(paths, cid)["classification"] == "unreconstructable"


def test_framing_keys_in_delta_skip(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    monkeypatch.setattr(ob, "_payload_with_new_ass",
                        lambda old, ass: {**old, "ass": ass, "focus": [0.1, 0.2]})
    assert _classify(paths, cid)["classification"] == "extra_delta_keys"


def test_stack_fail_open_not_committed(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=_STACK_FOCUS, ct=framing.RENDER_STACK_PAIR)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch, focus=_STACK_FOCUS,
                              content_type=framing.RENDER_STACK_PAIR)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    class_row = ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])
    assert class_row["classification"] == "eligible"
    calls = []
    _stub_render(monkeypatch, calls=calls)
    dirs = ob.RunDirs.build(cfg, "or_test")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    out = ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_test")
    assert out["status"] == "STACK_PAIR_REFUSED"
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"
    assert calls == []
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == class_row["fp_stored"]


def test_ct_stack_pair_never_fed_to_ffmpeg_clip_cmd(tmp_path, monkeypatch):
    fed = []
    real = clipmod.ffmpeg_clip_cmd

    def spy(*a, **k):
        fed.append(k.get("content_type"))
        return real(*a, **k)
    monkeypatch.setattr(clipmod, "ffmpeg_clip_cmd", spy)
    _stub_framing(monkeypatch, focus=_STACK_FOCUS, ct=framing.RENDER_STACK_PAIR)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch, focus=_STACK_FOCUS,
                              content_type=framing.RENDER_STACK_PAIR)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    class_row = ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])
    dirs = ob.RunDirs.build(cfg, "or_ff")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    _stub_render(monkeypatch)
    ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_ff")
    assert framing.RENDER_STACK_PAIR not in fed
    assert "stack-pair" not in fed


def test_scratch_seed_includes_detect_track_not_vstart_only(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    fr = cfg.agent_io / "framing"
    fr.mkdir(parents=True, exist_ok=True)
    (fr / "src_1.detect.json").write_text(json.dumps({"v": 1, "windows": {}}))
    (fr / "src_1.track.json").write_text(json.dumps({"v": 1}))
    _classify(paths, cid)
    scratch_fr = paths.scratch_cfg.agent_io / "framing"
    assert (scratch_fr / "src_1.detect.json").exists()
    assert (scratch_fr / "src_1.track.json").exists()
    assert (scratch_fr / "src_1.detect.json").read_text() == (fr / "src_1.detect.json").read_text()


def test_backup_rollback_restores_ass(tmp_path, monkeypatch):
    _stub_framing(monkeypatch, focus=None)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    class_row = ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])
    assert class_row["classification"] == "eligible"
    _stub_render(monkeypatch)
    dirs = ob.RunDirs.build(cfg, "or_rb")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    before_ass = Path(row["ass_path"]).read_text()
    out = ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_rb")
    assert out["status"] == "MIGRATED"
    assert Path(row["ass_path"]).read_text() == _NEW_ASS
    assert (dirs.backups / f"{cid}.ass").read_text() == before_ass
    rb = ob.rollback_clip(dirs, row)
    assert rb["status"] == "ROLLED_BACK"
    assert Path(row["ass_path"]).read_text() == before_ass
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


def test_burn_hook_only_never_called(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(overlay, "burn_hook_only", lambda *a, **k: called.append(1) or True)
    _stub_framing(monkeypatch, focus=None)
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    class_row = ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])
    _stub_render(monkeypatch)
    dirs = ob.RunDirs.build(cfg, "or_bh")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_bh")
    assert called == []


def test_overlay_keys_is_ass_only():
    assert ob.OVERLAY_KEYS == {"ass"}
    assert "ass" not in reframe.APPROVED_FRAMING_KEYS


def test_cli_argparse_and_dispatch_and_pause(tmp_path, monkeypatch):
    from fanops.cli import cmd_overlay_reburn, main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_ROOT", str(tmp_path))
    assert main(["overlay-reburn"]) == 0
    paused = []
    monkeypatch.setattr("fanops.cli.cmd_pause", lambda cfg, on: paused.append(on) or 0)
    monkeypatch.setattr(ob, "run_apply", lambda cfg, limit=None, scratch=None: {"aborted": False})
    cfg = Config(root=tmp_path)
    assert cmd_overlay_reburn(cfg, Namespace(apply=True, limit=None, scratch=None, dry_run=False)) == 0
    assert paused == [True]
