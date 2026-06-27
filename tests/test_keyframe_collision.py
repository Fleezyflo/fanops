# tests/test_keyframe_collision.py — WS4 (audit c0-f2 / c2-f1): smart-framing extracted keyframes into ONE
# shared dir (cfg.agent_io/framing/tmp) with filenames keyed only on (rounded-start, index) — so two sources
# whose cut windows share a start map to the IDENTICAL path. Under FANOPS_CONCURRENT_SOURCES the prewarm runs
# subject_focus in a thread pool, so worker A's ffmpeg -y overwrites B's frame mid-read, or A's finally-unlink
# removes the file B is reading -> a face detected for the wrong source's frame (silently wrong crop) or a
# None read (silent centered-crop fallback). The fix keys the tmp dir on (source, window) so the collision
# domain is per-call, not global — the bad path can no longer be constructed (safe to default the flag on).
from types import SimpleNamespace
from fanops.config import Config
import fanops.framing as framing


def test_keyframe_tmp_dir_is_unique_per_source(tmp_path, monkeypatch):
    seen = []
    def fake_extract(video_path, start, end, *, count, out_dir, **kw):
        seen.append(str(out_dir)); return []      # no frames -> fail-open to None; we only assert the dir
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", fake_extract)
    monkeypatch.setattr(framing, "_cv2", lambda: object())     # force the detection branch (pretend cv2 present)
    cfg = Config(root=tmp_path)
    framing.subject_focus(cfg, SimpleNamespace(id="src_a", source_path="/a.mp4"), start=0.0, end=7.0)
    framing.subject_focus(cfg, SimpleNamespace(id="src_b", source_path="/b.mp4"), start=0.0, end=7.0)  # SAME window
    assert len(seen) == 2
    assert seen[0] != seen[1], "two sources sharing a window get the SAME tmp dir -> concurrent clobber/unlink race"
    assert "src_a" in seen[0] and "src_b" in seen[1]           # the dir is keyed on the source


def test_keyframe_tmp_dir_is_unique_per_window(tmp_path, monkeypatch):
    seen = []
    def fake_extract(video_path, start, end, *, count, out_dir, **kw):
        seen.append(str(out_dir)); return []
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", fake_extract)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    cfg = Config(root=tmp_path)
    src = SimpleNamespace(id="src_a", source_path="/a.mp4")
    framing.subject_focus(cfg, src, start=0.0, end=7.0)
    framing.subject_focus(cfg, src, start=0.0, end=12.0)       # same source+start, DIFFERENT window end
    assert seen[0] != seen[1], "same source, different windows must not share a tmp dir"
