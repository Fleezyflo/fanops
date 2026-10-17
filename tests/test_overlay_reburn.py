# tests/test_overlay_reburn.py
"""MOL-969: ass-only recut — real classify_clip guards + real apply_clip/rollback.

CI has no ffmpeg: hermetic subprocess.run stubs (clip/reframe_apply share stdlib subprocess).
No fanops.* SUT patches (framing._resolve, render_reframed, ffmpeg_has_textfilter, cmd_pause).
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from fanops import clip as clipmod
from fanops import framing
from fanops import overlay
from fanops import overlay_reburn as ob
from fanops import reframe
from fanops.config import Config
from fanops.ids import child_id
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source
from fanops.pipeline_run import paused
from fanops.reframe import ReframePaths
from fanops.render_fingerprint import fingerprint_of_payload

_OLD_ASS = "OLD-ASS-TEXT"
_PROBE = json.dumps({
    "format": {"duration": "4.0"},
    "streams": [
        {"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1",
         "codec_name": "h264"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"},
    ],
})


def _corpus(tmp_path, monkeypatch, *, hook="wait for the last line", segments=None,
            clip_id=None, media_url=None, posts=None, extra_posts=(), stamp_fp=True):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "0")
    prod = tmp_path / "prod"
    cfg = Config(root=prod)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    media = cfg.sources / "s.mp4"
    media.write_bytes(b"\x00" * 64)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(media), width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10.0, end=28.0,
                          reason="r", state=MomentState.clipped, hook=hook, framing=None,
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
        _stamp(cfg, led, cid)
    return ReframePaths.build(prod, tmp_path / "scratch"), cid, cfg


def _stamp(cfg, led, cid, *, ass=_OLD_ASS):
    m, src = led.moments["mom_1"], led.sources["src_1"]
    dur = src.duration or 0.0
    hi = dur if dur > 0 else float("inf")
    cs, ce = clipmod.fit_window(m.start, m.end, dur, lo=0.0, hi=hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration)
    tb = clipmod._moment_top_bias(m, cfg)
    p = clipmod._render_fingerprint_payload(
        src.source_path, cs, ce, Fmt.r9x16.value, 1920, 1080, ass,
        top_bias=tb, focus=None, track=None, content_type=None)
    (cfg.clips / f"{cid}.render.json").write_text(json.dumps({"fp": fingerprint_of_payload(p)}))
    return p


def _classify(paths, cid):
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    return ob.classify_clip(paths, paths.scratch_cfg, led, led.clips[cid])


def _hermetic_apply(mocker, *, out_bytes=b"NEW-PIXELS"):
    """ffmpeg/ffprobe at the OS edge. Shared stdlib subprocess — covers clip + reframe_apply."""
    overlay._TEXTFILTER_CACHE = None
    captured: list = []

    def run(cmd, **kw):
        captured.append(list(cmd))
        if cmd and cmd[0] == "ffprobe":
            class R:
                returncode = 0
                stdout = _PROBE
                stderr = ""
            return R()
        if cmd and cmd[0] == "ffmpeg":
            if "-filters" in cmd:
                class R:
                    returncode = 0
                    stdout = "Filters:\n subtitles\n drawtext\n"
                    stderr = ""
                return R()
            dest = cmd[-1]
            if dest != "-" and not str(dest).startswith("-"):
                p = Path(dest)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(out_bytes)
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()
        raise AssertionError(f"unexpected cmd {cmd!r}")

    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    return captured


class _FakeLed:
    def __init__(self, cid="clip_a", media_url=None):
        c = type("C", (), {})()
        c.id, c.parent_id, c.media_url = cid, "m1", media_url
        self.clips, self.posts = {cid: c}, {}


def _class_row_for_apply(payload_old, ass_new):
    payload_new = dict(payload_old)
    payload_new["ass"] = ass_new
    return {
        "fp_stored": fingerprint_of_payload(payload_old),
        "fp_new": fingerprint_of_payload(payload_new),
        "payload_old": payload_old,
        "payload_new": payload_new,
        "payload_delta": ["ass"],
    }


def test_queued_sibling_skip(tmp_path, monkeypatch):
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.add_post(Post(id="p_q", parent_id=cid, account="b", account_id="2",
                      platform=Platform.instagram, caption="c", state=PostState.queued))
    led.save()
    row = _classify(paths, cid)
    assert row["classification"] == "live_or_queued_sibling"


def test_published_plus_awaiting_skip(tmp_path, monkeypatch):
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.add_post(Post(id="p_pub", parent_id=cid, account="b", account_id="2",
                      platform=Platform.instagram, caption="c", state=PostState.published,
                      public_url="https://instagram.com/p/x"))
    led.save()
    row = _classify(paths, cid)
    assert row["classification"] == "live_or_queued_sibling"


def test_file_url_does_not_skip_https_does(tmp_path, monkeypatch):
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch, media_url="file:///tmp/x.mp4")
    assert _classify(paths, cid)["classification"] != "hosted_http"
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
    assert _classify(paths, cid)["classification"] != "hosted_http"


def test_supercut_stitch_render_id_skip(tmp_path, monkeypatch):
    paths, cid, _cfg = _corpus(tmp_path, monkeypatch, segments=[(10.0, 16.0), (20.0, 26.0)])
    assert _classify(paths, cid)["classification"] == "supercut"
    paths, cid, _cfg = _corpus(tmp_path / "st", monkeypatch, clip_id="stitch_not_content")
    assert _classify(paths, cid)["classification"] == "stitch"
    paths, cid, _cfg = _corpus(tmp_path / "rid", monkeypatch)
    led = Ledger.load(paths.production_cfg)
    led.posts["p_await"] = led.posts["p_await"].model_copy(update={"render_id": "rnd_1"})
    led.save()
    assert _classify(paths, cid)["classification"] == "render_id"


def test_scratch_seed_includes_detect_track_not_vstart_only(tmp_path, monkeypatch):
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    fr = cfg.agent_io / "framing"
    fr.mkdir(parents=True, exist_ok=True)
    (fr / "src_1.detect.json").write_text(json.dumps({"v": 1, "windows": {}}))
    (fr / "src_1.track.json").write_text(json.dumps({"v": 1}))
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    ob._seed_scratch_detect_track(paths, paths.scratch_cfg, led.sources["src_1"])
    scratch_fr = paths.scratch_cfg.agent_io / "framing"
    assert (scratch_fr / "src_1.detect.json").exists()
    assert (scratch_fr / "src_1.track.json").exists()
    assert (scratch_fr / "src_1.detect.json").read_text() == (fr / "src_1.detect.json").read_text()


def test_stack_fail_open_not_committed(tmp_path, monkeypatch, mocker):
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    captured = _hermetic_apply(mocker)
    payload_old = _stamp(cfg, led, cid)
    class_row = _class_row_for_apply(payload_old, "NEW-ASS")
    dirs = ob.RunDirs.build(cfg, "or_test")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    row["payload_old"] = {**payload_old, "ct": framing.RENDER_STACK_PAIR,
                          "focus": [0.2, 0.4, 0.3, 0.38]}
    out = ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_test")
    assert out["status"] == "STACK_PAIR_REFUSED"
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"
    assert not any(c and c[0] == "ffmpeg" and "-i" in c for c in captured)
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == class_row["fp_stored"]


def test_ct_stack_pair_never_fed_to_ffmpeg(tmp_path, monkeypatch, mocker):
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    captured = _hermetic_apply(mocker)
    payload_old = _stamp(cfg, led, cid)
    class_row = _class_row_for_apply(payload_old, "NEW-ASS")
    dirs = ob.RunDirs.build(cfg, "or_ff")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    row["payload_old"] = {**payload_old, "ct": framing.RENDER_STACK_PAIR,
                          "focus": [0.2, 0.4, 0.3, 0.38]}
    ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_ff")
    joined = [" ".join(map(str, c)) for c in captured if c and c[0] == "ffmpeg" and "-i" in c]
    assert not any("stack-pair" in j for j in joined)
    assert not joined


def test_backup_rollback_restores_ass(tmp_path, monkeypatch, mocker):
    paths, cid, cfg = _corpus(tmp_path, monkeypatch)
    reframe.snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    _hermetic_apply(mocker)
    payload_old = _stamp(cfg, led, cid)
    ass_new = overlay.build_ass([], hook="wait for the last line", clip_start=payload_old["cs"],
                                clip_end=payload_old["ce"], width=1080, height=1920)
    assert ass_new and ass_new != _OLD_ASS
    class_row = _class_row_for_apply(payload_old, ass_new)
    dirs = ob.RunDirs.build(cfg, "or_rb")
    dirs.mkdirs()
    row = ob._plan_row(paths, led.clips[cid], class_row)
    before_ass = Path(row["ass_path"]).read_text()
    out = ob.apply_clip(paths, dirs, _FakeLed(cid), row, run_id="or_rb")
    assert out["status"] == "MIGRATED"
    assert Path(row["ass_path"]).read_text() == ass_new
    assert (dirs.backups / f"{cid}.ass").read_text() == before_ass
    rb = ob.rollback_clip(dirs, row)
    assert rb["status"] == "ROLLED_BACK"
    assert Path(row["ass_path"]).read_text() == before_ass
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


def test_overlay_keys_is_ass_only():
    assert ob.OVERLAY_KEYS == {"ass"}
    assert "ass" not in reframe.APPROVED_FRAMING_KEYS


def test_cli_dry_run_and_apply_pauses(tmp_path, monkeypatch):
    from fanops.cli import cmd_overlay_reburn, main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_ROOT", str(tmp_path))
    assert main(["overlay-reburn"]) == 0
    cfg = Config(root=tmp_path)
    assert cmd_overlay_reburn(cfg, Namespace(apply=True, limit=None, scratch=None, dry_run=False)) == 0
    assert paused(cfg)
