import json, os, shutil, subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.pipeline import advance
from fanops.agentstep import request_path, response_path, latest_request_id
from fanops.models import MomentDecision, CaptionSet
from fanops.transcribe import _cached_models, _resolve_model, real_transcript_signal

pytestmark = pytest.mark.integration

# Whisper model this test pins itself to (see _make_spoken_sample / the monkeypatch below).
# `tiny` is the smallest checkpoint and the one cached in the dev/CI image; pinning it in-test
# means the golden path no longer depends on the operator remembering `FANOPS_WHISPER_MODEL=tiny`
# on the command line — forgetting it would let the default `turbo` try a >1GB download that
# fails on offline / air-gapped / TLS-proxied hosts, silently erroring the source and failing
# this test with a cryptic `assert 0 == 1` instead of a clear skip.
_PINNED_WHISPER_MODEL = "tiny"

def _skip_or_fail(reason: str) -> None:
    """AUDIT H10: locally, a missing real toolchain is a clean SKIP (the real tool is genuinely
    absent — a skip beats a cryptic failure). But the whole point of this "not just mocks" test is
    lost if EVERY environment skips it, so a regression in the real ffmpeg/whisper/clip path would
    never be caught. In CI we set FANOPS_REQUIRE_E2E=1, which turns these skips into FAILURES — the
    CI image is responsible for installing the toolchain, and if the E2E didn't actually run, that
    is a CI failure, not a silent pass."""
    if os.getenv("FANOPS_REQUIRE_E2E") == "1":
        pytest.fail(f"FANOPS_REQUIRE_E2E=1 but the real-tooling E2E could not run: {reason}")
    pytest.skip(reason)

def _have(*bins): return all(shutil.which(b) for b in bins)

def _whisper_model_runnable(model: str) -> bool:
    """True iff `model` can actually transcribe here without a network fetch: its checkpoint is
    cached, or the resolver can fall back to one that is. On a fresh host with no cached
    checkpoint at all, whisper would have to download — which fails offline — so we skip
    (real-tooling test, real tool genuinely unavailable) rather than fail."""
    return _resolve_model(model) in _cached_models()

def _make_spoken_sample(dst: Path) -> bool:
    """Render a short clip with REAL speech so whisper has something to transcribe."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    wav = dst.with_suffix(".wav")
    if shutil.which("say"):            # macOS
        subprocess.run(["say", "-o", str(wav), "--data-format=LEF32@22050",
                        "they slept on me. not anymore."], check=False)
    elif shutil.which("espeak"):
        subprocess.run(["espeak", "-w", str(wav), "they slept on me. not anymore."], check=False)
    else:
        return False
    if not wav.exists():
        return False
    # wide source so the 9:16 crop path is exercised
    # Full 6s video (audio is shorter; trailing video is silent) so an in-bounds moment pick
    # validates. -shortest would clamp the clip to the ~1.6s TTS and make a 4s pick out-of-bounds.
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=6:size=1280x720:rate=30",
                    "-i", str(wav), "-c:v", "libx264", "-c:a", "aac", "-t", "6", str(dst)],
                   check=False, capture_output=True)
    return dst.exists()

def test_real_transcript_drives_moment_and_real_clip_renders(tmp_path, monkeypatch):
    if not _have("ffmpeg", "ffprobe", "whisper"):
        _skip_or_fail("needs ffmpeg + whisper on PATH")
    # This is the legacy `whisper` CLI golden path (gated on the `whisper` binary, pinned to a cached
    # `tiny` checkpoint). Pin the engine to the CLI so the proof is deterministic even where the [asr]
    # extra (faster-whisper) is installed — otherwise transcribe would divert to the fw large-v3 runner,
    # ignore FANOPS_WHISPER_MODEL, and attempt a >1GB download. The fw+large-v3 default is proven on real
    # data by the operator-run full re-transcribe, not gated into CI behind a heavy model.
    monkeypatch.setattr("fanops.transcribe._fw_available", lambda: False)
    # Pin the model in-test so the golden path is self-contained: `advance()` -> transcribe
    # reads FANOPS_WHISPER_MODEL, and this guarantees `tiny` regardless of the caller's env.
    monkeypatch.setenv("FANOPS_WHISPER_MODEL", _PINNED_WHISPER_MODEL)
    if not _whisper_model_runnable(_PINNED_WHISPER_MODEL):
        _skip_or_fail(f"no cached whisper checkpoint for '{_PINNED_WHISPER_MODEL}' "
                      "(would require a network download that fails offline)")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@mohflow.edits", "account_id": "999", "platforms": ["instagram", "tiktok"],
         "status": "active"}]}))
    if not _make_spoken_sample(cfg.inbox / "sample.mp4"):
        _skip_or_fail("no TTS available to synthesize speech")

    # pass 1: real whisper + real signals + real request
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["awaiting"]["moments"] == 1
    src_id = next(iter(Ledger.load(cfg).sources))
    req = json.loads(request_path(cfg, "moments", src_id).read_text())
    # THE KEY ASSERTION v1 could not make: REAL whisper ran on REAL audio and produced a REAL,
    # substantive transcript (not a fake/empty/stub one). We assert that CONTRACT — structure +
    # substance — via real_transcript_signal, NOT a single literal token. The old check
    # (`"slept" in joined`) over-specified one word that survived macOS `say` but not the Linux
    # CI's espeak vocoder (whisper-tiny hears espeak's sample as "Nice lap, Tommy, not anymore.").
    # See tests/test_e2e_transcript_assertion.py for the per-vocoder RED/GREEN proof.
    assert real_transcript_signal(req["transcript"]), \
        f"expected a real, substantive whisper transcript, got: {req['transcript']}"
    # Robust content anchor: "anymore" is the distinctive tail BOTH `say` and espeak reproduce
    # (verified against both engines' actual run output) — a content check that isn't vocoder-fragile.
    joined = " ".join(seg["text"].lower() for seg in req["transcript"])
    assert "anymore" in joined, f"expected the spoken tail in the transcript, got: {req['transcript']}"

    # answer via the LLM responder with a fake model (still proves the responder path)
    rid = latest_request_id(cfg, "moments", src_id)
    response_path(cfg, "moments", src_id).write_text(MomentDecision(
        source_id=src_id, request_id=rid,
        picks=[{"start": 0.0, "end": 4.0, "reason": "the line", "transcript_excerpt": "they slept on me"}]
    ).model_dump_json())

    # pass 2: real ffmpeg cut + reframe -> request captions
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["clips"] >= 1
    led = Ledger.load(cfg)
    clip = next(iter(led.clips.values()))
    # the rendered file is a real, vertical mp4
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", clip.path],
                         capture_output=True, text=True)
    assert "1080,1920" in out.stdout.replace(" ", "")

    # answer captions for both surfaces, publish in dryrun (past base => due)
    clip_id = clip.id
    rid2 = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid2, items=[
        {"surface": "@mohflow.edits/instagram", "caption": "no warning. just impact."},
        {"surface": "@mohflow.edits/tiktok", "caption": "wait for it."}]).model_dump_json())
    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    # post-approval gate: the 2 posts are born awaiting_approval -> an unattended advance publishes none.
    assert s["posts"] == 2 and s["published"] == 0
    # operator approves both, then the next pass publishes them in dryrun.
    with Ledger.transaction(cfg) as led:
        for pid in list(led.posts): led.approve_post(pid, now_iso="2020-01-01T00:00:00Z")
    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    assert s["published"] == 2
