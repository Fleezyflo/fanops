"""publish-out-of-lock: the live publish network must run OUTSIDE the ledger flock.

Pre-fix, advance() wrapped the WHOLE pass (incl. publish_due) in one Ledger.transaction, so a live
publish held the lock across every post's network round-trip — a concurrent Studio/daemon writer
blocked up to the 30s lock timeout (LockBusyError). The lock-probe poster below proves the property:
its publish() can itself acquire the ledger lock, which is only possible if the publish loop is NOT
holding it at network time. Post-fix, advance() commits its main txn FIRST, then publishes via the
per-post claim->network->finalize discipline (network lock-free)."""
import json
from fanops.config import Config
from fanops.ledger import Ledger, _file_lock
from fanops.models import Post, Clip, PostState, ClipState, Platform


def _persist_queued(cfg, pid="p1", cid="c1", when="2020-01-01T00:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="98432",
                          platform=Platform.instagram, caption="ship it",
                          # already-http media -> _ensure_media passes it through (no live upload to the fake
                          # POSTIZ_URL); the test proves the LOCK property, not the uploader.
                          media_urls=["https://h/v.mp4"],
                          scheduled_time=when, state=PostState.queued,
                          public_url="https://www.instagram.com/reel/AAA/"))


def test_advance_publishes_with_network_outside_the_lock(tmp_path, monkeypatch, mocker):
    # A poster whose publish() ACQUIRES the ledger lock proves the publish loop is lock-free at
    # network time. Pre-fix (publish inside advance's transaction) this acquire LockBusyError'd and
    # the post was marked failed; post-fix it acquires cleanly and the post publishes.
    # LIVE backend (postiz): the lock-free property is a LIVE-publish property. Post dryrun-boundary,
    # a dryrun (not-live) post never enters the distribution rail, so this test must be genuinely live
    # to exercise the claim->network->finalize path the lock-probe poster proves.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}]}))
    _persist_queued(cfg, pid="p1", cid="c1")
    import fanops.post.run as run
    acquired = {}

    class _LockProbePoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            # if the publish loop holds the ledger lock, this acquire raises LockBusyError (timeout)
            with _file_lock(cfg.lock_path, timeout=3):
                acquired[post_id] = True
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = f"probe_{post_id}"
            return led_

    mocker.patch.object(run, "get_poster", return_value=_LockProbePoster(cfg))
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert acquired.get("p1") is True                       # the network ran with the lock free
    assert led.posts["p1"].state is PostState.published     # and the post shipped


from fanops.pipeline import advance  # noqa: E402  (imported after the helpers for readability)
