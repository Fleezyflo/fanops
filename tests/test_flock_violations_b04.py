"""B04 lock-probe tests: H10 transcribe, M03 media resolve, M05 ingest stage, M04 reconcile liveness."""

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, SourceState
from fanops.reconcile import _GATE_FAILOPEN, _GATE_PARK, _GATE_REST, reconcile_due
from tests.conftest import ledger_lock_is_free


def test_h10_transcribe_cold_cache_never_shells_in_lock(tmp_path, mocker, monkeypatch):
    """H10: in_lock=True + cold cache → defer, whisper never invoked under the flock."""
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
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
        led = transcribe_source(led, cfg, "src_1", in_lock=True)
    spy.assert_not_called()
    assert led.sources["src_1"].state is SourceState.catalogued


def test_m03_learn_pass_media_enumeration_lock_free(tmp_path, monkeypatch, mocker):
    """M03: enumerate_scoped_media runs outside the ledger flock in _learn_pass."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="sub_p",
                      public_url="https://instagram.com/p/abc"))
    led.save()
    seen = {}

    def scoped(cfg_, handles, *, get=None):
        seen["lock_free"] = ledger_lock_is_free(cfg)
        return []

    mocker.patch("fanops.meta_graph.enumerate_scoped_media", side_effect=scoped)
    mocker.patch("fanops.cli._default_list_posts", return_value=lambda window: [])
    import fanops.cli as cli
    cli._learn_pass(cfg)
    assert seen.get("lock_free") is True


def test_m05_ingest_stage_hash_copy_lock_free_and_dedup(tmp_path, monkeypatch, mocker):
    """M05: sha256/copy2 lock-free; double-stage → one ledger row."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    drop = cfg.inbox / "clip.mp4"
    drop.write_bytes(b"same-bytes")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 10.0))
    seen = {"sha": [], "copy": []}

    def track_sha(p):
        seen["sha"].append(ledger_lock_is_free(cfg))
        import hashlib
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()

    import shutil
    real_copy2 = shutil.copy2

    def track_copy(src, dst):
        seen["copy"].append(ledger_lock_is_free(cfg))
        return real_copy2(src, dst)

    mocker.patch("fanops.ingest.sha256_of", side_effect=track_sha)
    mocker.patch("fanops.ingest.shutil.copy2", side_effect=track_copy)
    from fanops.ingest import stage_inbox_candidates, ingest_staged, _archive_staged
    s1 = stage_inbox_candidates(cfg)
    assert seen["sha"] and all(seen["sha"])
    assert seen["copy"] and all(seen["copy"])
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
    """M04: IG rest / park / fail_open liveness computed lock-free; verdict applied in txn."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for pid, plat in (("ig_rest", Platform.instagram), ("ig_park", Platform.instagram),
                      ("ig_failopen", Platform.instagram)):
        led.add_post(Post(id=pid, parent_id="c", account="@cred", account_id="1", platform=plat,
                          caption="x", state=PostState.needs_reconcile, submission_id=f"sub_{pid}",
                          public_url="https://instagram.com/p/x"))
    led.save()
    seen = {"lock_free": []}

    def poll(sid):
        seen["lock_free"].append(ledger_lock_is_free(cfg))
        pid = sid.replace("sub_", "")
        return {"status": "published", "publicUrl": f"https://instagram.com/p/{pid[-1]}",
                "releaseId": f"mid_{pid}"}

    mocker.patch("fanops.reconcile._default_get_status", return_value=poll)
    mocker.patch("fanops.meta_graph.credentialed_ig_handles", return_value=["@cred"])

    def fake_ig_verdict(cfg, post, media_id, cred_ig, confirm, graph_get):
        if post.id == "ig_rest":
            return _GATE_REST
        if post.id == "ig_park":
            return _GATE_PARK
        return _GATE_FAILOPEN

    mocker.patch("fanops.reconcile._ig_rest_verdict", side_effect=fake_ig_verdict)
    reconcile_due(cfg)
    assert seen["lock_free"] and all(seen["lock_free"])
    again = Ledger.load(cfg)
    assert again.posts["ig_rest"].state is PostState.published
    assert again.posts["ig_park"].state is PostState.needs_reconcile
    assert again.posts["ig_failopen"].state is PostState.needs_reconcile
