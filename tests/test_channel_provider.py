"""M3a — per-channel provider as the publish source of truth. `accounts.effective_provider` resolves a
channel to its provider: the explicit accounts.json `backends` entry, else a BACK-COMPAT bridge to the
legacy global FANOPS_POSTER (so the running deployment never goes dark), else None. The publish path
(`run._post_provider`) gates on `cfg.is_live` (dryrun posts nothing — even an explicitly-routed channel)
and SKIPS a live post whose channel has no provider (breadcrumb, never global-defaults a new deployment,
never fails). BYTE-IDENTICAL in the current world (FANOPS_LIVE unset): the no-provider enforcement only
bites once go_live writes FANOPS_LIVE (M3b)."""
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.accounts import Accounts
from fanops.post.run import publish_due, publish_post


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for k in ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "BLOTATO_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield


def _accounts(tmp_path, rows):
    p = Config(root=tmp_path).accounts_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"accounts": rows}))


def _queued(led, pid, handle, platform, acct_id="x"):
    led.add_post(Post(id=pid, parent_id="c", account=handle, account_id=acct_id, platform=platform,
                      caption="c", state=PostState.queued, media_urls=["https://x/v.mp4"]))


# ---------------------------------------------------------------- effective_provider ----
def test_effective_provider_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    _accounts(tmp_path, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"],
                          "status": "active", "backends": {"tiktok": "zernio"}}])
    assert Accounts.load(Config(root=tmp_path)).effective_provider("@tk", Platform.tiktok) == "zernio"


def test_effective_provider_bridges_legacy_global_when_live(tmp_path, monkeypatch):
    # the LIVE deployment: a channel with no explicit provider keeps publishing via the legacy global.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    _accounts(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    assert Accounts.load(Config(root=tmp_path)).effective_provider("@ig", Platform.instagram) == "postiz"


def test_effective_provider_none_when_global_not_live(tmp_path, monkeypatch):
    # the NEW deployment: FANOPS_POSTER unset (dryrun) -> no bridge -> a channel MUST declare a provider.
    _accounts(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    assert Accounts.load(Config(root=tmp_path)).effective_provider("@ig", Platform.instagram) is None


# ---------------------------------------------------------------- publish gating ----
def test_publish_due_skips_live_channel_with_no_provider(tmp_path, monkeypatch, mocker):
    # FANOPS_LIVE=1 but the channel has no provider and no legacy live global -> SKIP (breadcrumb), the post
    # stays queued, NOT failed, and the poster is never even constructed.
    monkeypatch.setenv("FANOPS_LIVE", "1")                     # live switch on, but no global provider
    cfg = Config(root=tmp_path)
    _accounts(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        _queued(led, "p1", "@ig", Platform.instagram)
    gp = mocker.patch("fanops.post.run.get_poster")
    res = publish_due(cfg)
    gp.assert_not_called()                                    # never tried to publish
    assert res == {"due": 1, "published": 0, "no_provider": 1}
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued   # left queued (not failed) — waits for a provider


def test_publish_due_dryrun_posts_nothing_even_with_explicit_provider(tmp_path, monkeypatch, mocker):
    # the footgun fix: in dryrun (not live), an explicitly-routed channel must NOT publish — the global
    # on/off switch governs ALL channels. The post goes through the dryrun poster (posts nothing).
    monkeypatch.setenv("FANOPS_LIVE", "0"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    cfg = Config(root=tmp_path)
    _accounts(tmp_path, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"],
                          "status": "active", "backends": {"tiktok": "zernio"}}])
    with Ledger.transaction(cfg) as led:
        _queued(led, "p1", "@tk", Platform.tiktok, acct_id="a")
    seen = {}
    class _Fake:
        def __init__(self, backend): self.backend = backend
        def publish(self, led, pid): seen[pid] = self.backend; led.posts[pid].state = PostState.submitted; return led
    mocker.patch("fanops.post.run.get_poster", side_effect=lambda c, backend=None: _Fake(backend))
    publish_due(cfg)
    assert seen["p1"] == "dryrun"                             # NOT zernio — dryrun governs even an overridden channel


def test_publish_post_no_provider_returns_none(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path)
    _accounts(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        _queued(led, "p1", "@ig", Platform.instagram)
    gp = mocker.patch("fanops.post.run.get_poster")
    assert publish_post(cfg, "p1") is None
    gp.assert_not_called()
