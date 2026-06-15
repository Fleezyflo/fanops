# tests/test_vocals.py
import subprocess
from pathlib import Path
import fanops.vocals as vocals
from fanops.vocals import demucs_cmd, isolate_vocals

def test_demucs_cmd_shape():
    cmd = demucs_cmd("/s/x.mp4", "/out")
    assert cmd[0] == "demucs"
    assert "--two-stems=vocals" in cmd        # only split vocals vs the rest (faster than 4-stem)
    assert "--mp3" in cmd                       # write via lameenc, NOT torchaudio.save (torchcodec)
    assert "-o" in cmd and "/out" in cmd
    assert cmd[-1] == "/s/x.mp4"

def test_isolate_vocals_returns_vocals_path_on_success(tmp_path, mocker):
    # demucs writes <out>/<model>/<stem>/vocals.mp3 -> isolate_vocals returns THAT path
    src = tmp_path / "src_1.mp4"; src.write_bytes(b"VID")
    out = tmp_path / "work"
    def fake_run(cmd, **kw):
        stem = Path(cmd[-1]).stem
        d = out / "htdemucs" / stem; d.mkdir(parents=True, exist_ok=True)
        (d / "vocals.mp3").write_bytes(b"VOCALS")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.vocals.subprocess.run", side_effect=fake_run)
    got = isolate_vocals(str(src), str(out))
    assert got.endswith("htdemucs/src_1/vocals.mp3") and Path(got).exists()

def test_isolate_vocals_failopen_when_demucs_absent(tmp_path, mocker):
    # demucs not installed -> subprocess.run raises FileNotFoundError BEFORE the process starts.
    # isolate_vocals must return the ORIGINAL audio (transcription degrades to today's behavior).
    src = tmp_path / "src_1.mp4"; src.write_bytes(b"VID")
    mocker.patch("fanops.vocals.subprocess.run",
                 side_effect=FileNotFoundError(2, "No such file", "demucs"))
    assert isolate_vocals(str(src), str(tmp_path / "w")) == str(src)

def test_isolate_vocals_failopen_on_nonzero_and_timeout(tmp_path, mocker):
    src = tmp_path / "src_1.mp4"; src.write_bytes(b"VID")
    # nonzero rc (e.g. model download blocked) -> original path
    class R: returncode = 1; stderr = "boom"; stdout = ""
    mocker.patch("fanops.vocals.subprocess.run", return_value=R())
    assert isolate_vocals(str(src), str(tmp_path / "w1")) == str(src)
    # hung -> killed at the bound -> original path, never a raise
    mocker.patch("fanops.vocals.subprocess.run",
                 side_effect=subprocess.TimeoutExpired("demucs", 1800.0))
    assert isolate_vocals(str(src), str(tmp_path / "w2")) == str(src)

def test_isolate_vocals_failopen_when_stem_missing(tmp_path, mocker):
    # rc 0 but no vocals.mp3 written (schema drift) -> fail-open to original
    src = tmp_path / "src_1.mp4"; src.write_bytes(b"VID")
    class R: returncode = 0; stderr = ""; stdout = ""
    mocker.patch("fanops.vocals.subprocess.run", return_value=R())
    assert isolate_vocals(str(src), str(tmp_path / "w")) == str(src)

def test_demucs_env_sets_certifi_bundle(monkeypatch):
    # the macOS SSL cert fix: demucs fetches its model over https; point SSL_CERT_FILE at certifi
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    env = vocals._demucs_env()
    import certifi
    assert env.get("SSL_CERT_FILE") == certifi.where()
    assert env.get("REQUESTS_CA_BUNDLE") == certifi.where()
