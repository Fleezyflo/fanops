"""reconcile-out-of-lock (M1): a live reconcile pass must run its per-post status POLLS (network)
OUTSIDE the ledger write lock — only the apply belongs inside a tight transaction. Pre-fix, advance() ran
reconcile_posts INSIDE its main Ledger.transaction, so each GET held the lock across the network (the
same contention class #89 removed from publish). The lock-probe poller below proves the property: its
GET can acquire the ledger store lock, which is only possible if the poll loop is NOT holding it."""
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.pipeline import advance


class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._b = {} if body is None else body
        self.text = text
        self.headers = {}
    def json(self):
        return self._b


def _persist_parked(cfg, pid="p1", cid="c1"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="98432",
                          platform=Platform.instagram, caption="x", submission_id="zernio_sid_1",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.submitting, public_url="dryrun://98432"))


def test_advance_reconciles_with_polls_outside_the_lock(tmp_path, monkeypatch, mocker):
    # live (zernio) backend + key => advance reconciles. The poller acquires the ledger lock; pre-fix (poll
    # inside the main txn) this LockBusyError'd and the post was parked with a poll-error reason —
    # post-fix it acquires cleanly and the post reconciles to published.
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}]}))
    _persist_parked(cfg, pid="p1", cid="c1")
    acquired = {}
    url = "https://www.instagram.com/reel/abc/"

    def _get(u, **kw):
        s = str(u)
        if "zernio_sid_1" in s:
            with Ledger.load(cfg)._store.lock(timeout=3):       # only succeeds if the poll loop holds no lock
                acquired["zernio_sid_1"] = True
            return _R(200, {"status": "published", "publicUrl": url})
        return _R(200, {"posts": []})

    mocker.patch("fanops.post.metrics.requests.get", side_effect=_get)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert acquired.get("zernio_sid_1") is True           # the poll ran with the lock free
    assert led.posts["p1"].state is PostState.published   # and the post reconciled
    assert led.posts["p1"].public_url == url
    assert not (led.posts["p1"].public_url or "").startswith("dryrun://")
