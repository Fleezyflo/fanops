"""Follow-up to Phase B (post-merge review, Important finding): the standalone CLI write commands
(track / reconcile / adjust / ingest / pull) did a LOCK-FREE Ledger.load -> mutate -> led.save(),
re-opening the exact lost-update window B4 closed for advance() — a concurrent advance under its
transaction could be clobbered last-writer-wins. These migrate them to Ledger.transaction, with the
HARD constraint that network / subprocess I/O stays OUTSIDE the lock (mirroring publish_due's
in_transaction split) so the up-to-30s Blotato calls never serialize behind the ledger write lock."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
import fanops.cli as cli
from tests.conftest import ledger_lock_is_free as _ledger_lock_is_free


# ---------------------------------------------------------------------------
# 1. Each write command takes the transaction (no more lock-free load->save).
# ---------------------------------------------------------------------------

def test_cmd_adjust_uses_a_transaction(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    Ledger.load(Config(root=tmp_path)).save()
    spy = mocker.spy(Ledger, "transaction")
    assert main_ok(["adjust"])
    assert spy.call_count >= 1, "cmd_adjust must mutate under Ledger.transaction, not a lock-free load+save"


def test_cmd_ingest_uses_a_transaction(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    Ledger.load(Config(root=tmp_path)).save()
    # no drops in the inbox -> ingest_drops is a no-op, but it must still go through a transaction
    spy = mocker.spy(Ledger, "transaction")
    assert main_ok(["ingest"])
    assert spy.call_count >= 1, "cmd_ingest must persist under Ledger.transaction"


def test_cmd_track_uses_a_transaction(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    Ledger.load(Config(root=tmp_path)).save()
    # inject a fetch so no real network; returns no rows (nothing to apply)
    mocker.patch("fanops.cli._default_list_posts", return_value=lambda window: [])
    spy = mocker.spy(Ledger, "transaction")
    assert main_ok(["track"])
    assert spy.call_count >= 1, "cmd_track must apply metrics under Ledger.transaction"


def test_cmd_reconcile_uses_a_transaction(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p"))
    led.save()
    # inject a status poll so no real network; report still in-progress (no state change needed)
    mocker.patch("fanops.reconcile._default_get_status", return_value=lambda sid: {"status": "in-progress"})
    spy = mocker.spy(Ledger, "transaction")
    assert main_ok(["reconcile"])
    assert spy.call_count >= 1, "cmd_reconcile must apply poll results under Ledger.transaction"


# ---------------------------------------------------------------------------
# 2. The network / poll call must run OUTSIDE the ledger lock (no serialization
#    of the slow Blotato call behind the flock).
# ---------------------------------------------------------------------------

def test_cmd_track_network_runs_outside_the_lock(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    seen = {}

    def fetching(window):
        seen["lock_free_during_fetch"] = _ledger_lock_is_free(cfg)   # must be True: lock not held
        return []

    mocker.patch("fanops.cli._default_list_posts", return_value=fetching)
    assert main_ok(["track"])
    assert seen.get("lock_free_during_fetch") is True, \
        "the metrics fetch held the ledger lock — network must be OUTSIDE the transaction"


def test_learn_pass_fetch_runs_outside_the_lock(tmp_path, monkeypatch, mocker):
    # ECC-review fix #1: the `run` post-loop learning pass fetched metrics (up to ~30s network)
    # INSIDE Ledger.transaction, holding the flock across the call and serializing any concurrent
    # advance/ingest behind it. The fetch must run OUTSIDE the lock (mirroring cmd_track).
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    seen = {}

    def fetching(window):
        seen["lock_free_during_fetch"] = _ledger_lock_is_free(cfg)   # must be True: lock not held
        return []

    mocker.patch("fanops.cli._default_list_posts", return_value=fetching)
    cli._learn_pass(cfg)
    assert seen.get("lock_free_during_fetch") is True, \
        "the learn-pass metrics fetch held the ledger lock — network must be OUTSIDE the transaction"


def test_cmd_map_media_uses_a_transaction(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    Ledger.load(Config(root=tmp_path)).save()
    mocker.patch("fanops.meta_graph.enumerate_scoped_media", return_value=[])
    spy = mocker.spy(Ledger, "transaction")
    assert main_ok(["map-media"])
    assert spy.call_count >= 1, "cmd_map_media must persist under Ledger.transaction, not a lock-free load+save"


def test_cmd_map_media_network_runs_outside_the_lock(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    seen = []

    def scoped(cfg_, handles, *, get=None):
        seen.append(_ledger_lock_is_free(cfg))
        return []

    mocker.patch("fanops.meta_graph.enumerate_scoped_media", side_effect=scoped)
    assert main_ok(["map-media"])
    assert seen and seen[0] is True, \
        "the media enumeration held the ledger lock — network must be OUTSIDE the transaction"


def test_cmd_reconcile_poll_runs_outside_the_lock(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p"))
    led.save()
    seen = {}

    def polling(sid):
        seen["lock_free_during_poll"] = _ledger_lock_is_free(cfg)
        return {"status": "in-progress"}

    mocker.patch("fanops.reconcile._default_get_status", return_value=polling)
    assert main_ok(["reconcile"])
    assert seen.get("lock_free_during_poll") is True, \
        "the status poll held the ledger lock — per-post network must be OUTSIDE the transaction"


# ---------------------------------------------------------------------------
# 3. Behavior preserved: track/reconcile still apply their results to the ledger.
# ---------------------------------------------------------------------------

def test_cmd_reconcile_still_promotes_published(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p"))
    led.save()
    mocker.patch("fanops.reconcile._default_get_status",
                 return_value=lambda sid: {"status": "published", "publicUrl": "https://x/p"})
    assert main_ok(["reconcile"])
    again = Ledger.load(cfg)
    assert again.posts["p"].state is PostState.published
    assert again.posts["p"].public_url == "https://x/p"


def test_cmd_reconcile_postiz_date_windows_each_post(tmp_path, monkeypatch, mocker):
    # P2 review fix: the explicit `fanops reconcile` verb must carry the date window for Postiz too.
    # _default_get_status(cfg, snapshot) lets the Postiz poll read each post's own scheduled_time and
    # pass a startDate/endDate window bracketing it on GET /public/v1/posts — else a future/old post is
    # permanently off the page and never reconciles (and the live server rejects the old display/date
    # with HTTP 400). Run the REAL Postiz dispatch (no _default_get_status mock); capture the params.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="postiz_9",
                      scheduled_time="2099-01-01T00:00:00Z", public_url="dryrun://p")); led.save()
    seen = {}
    class _Resp:
        status_code = 200; text = "{}"
        def json(self): return {"posts": [{"id": "postiz_9", "state": "PUBLISHED"}]}
    def fake_get(url, **kw):
        seen["params"] = kw.get("params"); return _Resp()
    mocker.patch("fanops.post.metrics.requests.get", side_effect=fake_get)
    assert main_ok(["reconcile"])
    p = seen.get("params") or {}
    assert "date" not in p and p["startDate"] <= "2099-01-01" <= p["endDate"]   # ISO window brackets the post's own time
    assert Ledger.load(cfg).posts["p"].state is PostState.published

def test_cmd_reconcile_postiz_without_key_skips_cleanly(tmp_path, monkeypatch, capsys):
    # P2 review fix: postiz WITHOUT a key must SKIP (return 0), not raise/exit. _default_get_status
    # builds PostizStatusClient -> _key raises PostizAuthError (an AuthError, NOT a RuntimeError), so
    # the widened `except (RuntimeError, AuthError)` is what keeps reconcile a clean no-op (like track).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    assert cli.cmd_reconcile(cfg) == 0
    assert "reconciled" in capsys.readouterr().out  # empty ledger -> clean no-op


def main_ok(argv) -> bool:
    rc = cli.main(argv)
    return rc == 0
