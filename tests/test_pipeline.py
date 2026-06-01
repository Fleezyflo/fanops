import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.pipeline import advance

def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def _ff(mocker):
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        if cmd[0] == "whisper":
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] in ("ffmpeg",) and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 16.0")
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n20.0\n"
            return R()
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)

def test_advance_stops_at_gate_then_continues(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    _put(cfg.inbox / "raw.mp4", b"V")
    _ff(mocker)
    from fanops.models import MomentDecision, MomentPick, CaptionSet, CaptionItem
    from fanops.agentstep import response_path, latest_request_id

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["sources"] == 1 and s["awaiting"]["moments"] == 1 and s["posts"] == 0

    src_id = next(iter(Ledger.load(cfg).sources))
    rid = latest_request_id(cfg, "moments", src_id)
    response_path(cfg, "moments", src_id).write_text(MomentDecision(
        source_id=src_id, request_id=rid,
        picks=[MomentPick(start=14.0, end=18.0, reason="punchline",
                          transcript_excerpt="they slept on me")]).model_dump_json())

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["moments"] == 1 and s["clips"] >= 1 and s["awaiting"]["captions"] == 1

    led = Ledger.load(cfg); clip_id = next(iter(led.clips))
    rid2 = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="@a/tiktok", caption="wait for it.")]).model_dump_json())

    s = advance(cfg, base_time="2020-01-01T00:00:00Z")   # base in the PAST so posts are due
    assert s["posts"] == 2 and s["published"] == 2
    # AUDIT C1: needs_reconcile is an actionable parked state (ambiguous publish — may be live).
    # It must be visible in the advance() summary the unattended operator sees, not only the
    # digest. The dryrun backend never produces it, so the count is 0, but the KEY must exist.
    assert s["needs_reconcile"] == 0
    assert len(list(cfg.scheduled.glob("*.json"))) == 2

def test_one_bad_source_does_not_wedge_the_pass(tmp_path, monkeypatch, mocker):
    # FIX F03: a source whose whisper crashes goes to error; others still advance.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    _put(cfg.inbox / "good.mp4", b"G"); _put(cfg.inbox / "bad.mp4", b"B")
    call = {"n": 0}
    def fake(cmd, **kw):
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in " ".join(cmd) else "1920\n1080\n20.0\n"
            return R()
        if cmd[0] == "whisper":
            call["n"] += 1
            if call["n"] == 1:                      # first source: whisper raises
                raise OSError("whisper exploded")
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language":"en","segments":[{"start":0,"end":2,"text":"hi"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffmpeg":
            class R: returncode=0; stdout=""; stderr="silence_end: 1.0 | silence_duration: 0.5"
            return R()
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    states = sorted(x.state.value for x in led.sources.values())
    assert "error" in states                         # the bad one quarantined
    assert any(v in states for v in ("moments_requested", "signalled", "transcribed"))  # good one progressed
