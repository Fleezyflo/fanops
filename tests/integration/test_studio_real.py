# tests/integration/test_studio_real.py — CREATE
import json, shutil, subprocess
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.clip import ffmpeg_clip_cmd
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="needs real ffmpeg/ffprobe")
def test_review_serves_real_h264_aac_mp4(tmp_path):
    # 1) make a real source with ffmpeg's test sources, then render a base clip via the SAME
    #    ffmpeg_clip_cmd the pipeline uses (asserting the H.264/AAC/+faststart codec invariant).
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(src)], check=True, capture_output=True)
    clip_path = tmp_path / "clip_1.mp4"
    cmd = ffmpeg_clip_cmd(str(src), str(clip_path), 0.0, 2.0, Fmt.r9x16.value, src_w=320, src_h=240)
    assert "-c:v" in cmd and "libx264" in cmd and "+faststart" in cmd   # codec invariant pinned
    subprocess.run(cmd, check=True, capture_output=True)
    assert clip_path.exists() and clip_path.stat().st_size > 0

    # 2) queue a post over that real clip
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(src), language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-2", start=0, end=2,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(clip_path), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="real", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()

    # 3) Studio serves the real bytes and ffprobe confirms H.264 video + AAC audio
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/media/p1")
    assert r.status_code == 200 and r.data == clip_path.read_bytes()

    out = tmp_path / "served.mp4"; out.write_bytes(r.data)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                            "-of", "json", str(out)], check=True, capture_output=True, text=True)
    codecs = {s["codec_name"] for s in json.loads(probe.stdout)["streams"]}
    assert "h264" in codecs and "aac" in codecs
