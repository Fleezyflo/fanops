"""B04 lock-probe tests: H10 transcribe, M05 ingest stage, M04 reconcile liveness."""

from types import SimpleNamespace

from fanops.config import Config
from fanops.errors import ToolchainMissingError
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, SourceState
from fanops.reconcile import reconcile_due
from tests.conftest import ledger_lock_is_free


def test_h10_transcribe_cold_cache_never_shells_in_lock(tmp_path, mocker, monkeypatch):
    """H10: in_lock=True + cold cache → defer or typed miss; whisper never invoked under the flock."""
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    cfg = Config(root=tmp_path)
    vid = cfg.sources / "src_1.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"audio-bytes")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(vid), state=SourceState.catalogued))
    led.save()
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    from fanops.transcribe import transcribe_source
    with Ledger.transaction(cfg) as led:
        try:
            led = transcribe_source(led, cfg, "src_1", in_lock=True)
        except ToolchainMissingError:
            pass
    spy.assert_not_called()
    assert led.sources["src_1"].state is SourceState.catalogued


def test_m05_ingest_stage_hash_copy_lock_free_and_dedup(tmp_path, monkeypatch, mocker):
    """M05: sha256/copy2 lock-free; double-stage → one ledger row."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    drop = cfg.inbox / "clip.mp4"
    drop.write_bytes(b"same-bytes")

    def ffprobe(cmd, **_k):
        joined = " ".join(cmd)
        if "codec_type" in joined:
            return SimpleNamespace(returncode=0, stdout="video\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="1920\n1080\n10.0\n", stderr="")

    mocker.patch("fanops.media_probe.subprocess.run", side_effect=ffprobe)
    from fanops.ingest import stage_inbox_candidates, ingest_staged, _archive_staged
    assert ledger_lock_is_free(cfg)
    s1 = stage_inbox_candidates(cfg)
    assert ledger_lock_is_free(cfg)
    with Ledger.transaction(cfg) as led:
        led, c1 = ingest_staged(led, cfg, s1)
    _archive_staged(cfg, s1)
    assert c1.added == 1
    drop.write_bytes(b"same-bytes")
    s2 = stage_inbox_candidates(cfg)
    with Ledger.transaction(cfg) as led:
        led, c2 = ingest_staged(led, cfg, s2)
    _archive_staged(cfg, s2)
    assert c2.deduped == 1 and c2.added == 0
    assert len(Ledger.load(cfg).sources) == 1


def test_m04_reconcile_liveness_branches_lock_free_and_applied(tmp_path, monkeypatch, mocker):
    """M04: liveness enrichment runs lock-free; IG rests on Postiz confirmation only."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for pid in ("ig_rest", "ig_park", "ig_failopen"):
        led.add_post(Post(id=pid, parent_id="c", account="@cred", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id=f"sub_{pid}",
                          public_url="https://instagram.com/p/x"))
    led.save()
    seen = {"lock_free": []}

    def fake_get(url, **_k):
        seen["lock_free"].append(ledger_lock_is_free(cfg))
        pid = str(url).rstrip("/").rsplit("/", 1)[-1]
        body = {"status": "published", "url": f"https://instagram.com/p/{pid[-1]}"}
        return SimpleNamespace(status_code=200, text="{}", json=lambda: body)

    mocker.patch("fanops.post.metrics.zernio_read.requests.get", side_effect=fake_get)
    reconcile_due(cfg)
    assert seen["lock_free"] and all(seen["lock_free"])
    again = Ledger.load(cfg)
    for pid in ("ig_rest", "ig_park", "ig_failopen"):
        assert again.posts[pid].state is PostState.published
