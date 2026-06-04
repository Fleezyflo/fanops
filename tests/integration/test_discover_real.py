import shutil, subprocess
import pytest
import fanops.discover as discover
from fanops.config import Config

@pytest.mark.integration
def test_discover_real_thumbnails_then_intake_to_inbox(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe required for the real discovery render")
    bank = tmp_path / "bank"; bank.mkdir()
    for name, color in [("keep.mp4", "navy"), ("skip.mp4", "darkgreen")]:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s=720x1280:d=3", str(bank / name), "-y"], check=True)
    (bank / "tax return.mp4").write_bytes(b"not real but PII-named")   # excluded by name
    cfg = Config(root=tmp_path)
    summary = discover.discover(cfg, [bank])
    assert summary["new"] == 2                                # PII-named excluded
    thumbs = list(cfg.review.glob("*.jpg"))
    assert len(thumbs) == 2 and all(t.stat().st_size > 0 for t in thumbs)   # REAL viewable jpgs
    # operator approves keep.mp4
    from fanops.ingest import sha256_of
    keep_eid = sha256_of(bank / "keep.mp4")[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{keep_eid}.jpg").rename(cfg.review / "approved" / f"{keep_eid}.jpg")
    assert discover.intake(cfg)["intaken"] == 1
    assert (cfg.inbox / "keep.mp4").exists() and not (cfg.inbox / "skip.mp4").exists()
