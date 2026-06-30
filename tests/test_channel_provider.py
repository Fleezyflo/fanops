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
                      caption="c", state=PostState.queued, media_urls=["https://x/v.mp4"],
                      scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://c"))   # CULM-4: a due time (no-schedule now parks)


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


def test_effective_provider_no_bridge_for_platform_global_does_not_serve(tmp_path, monkeypatch):
    # H2: the legacy FANOPS_POSTER bridge is platform-AWARE. postiz (this system's IG poster) must NOT
    # bridge a provider-less TikTok channel -> None (else it publishes to the wrong provider/integration
    # or burns the post). The TikTok channel must declare its own provider (e.g. zernio) instead.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    _accounts(tmp_path, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active"}])
    assert Accounts.load(Config(root=tmp_path)).effective_provider("@tk", Platform.tiktok) is None


def test_effective_provider_bridge_serves_its_own_platform(tmp_path, monkeypatch):
    # the platform-aware bridge still fires for a platform the global DOES serve (zernio->tiktok), so the
    # H2 narrowing never strands a channel the legacy global legitimately published.
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    _accounts(tmp_path, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active"}])
    assert Accounts.load(Config(root=tmp_path)).effective_provider("@tk", Platform.tiktok) == "zernio"


# ---- C1: is_live_backend keys off PER-CHANNEL readiness, not the retired global poster_backend ----
def test_is_live_backend_true_when_a_channel_is_ready_without_global_poster(tmp_path, monkeypatch):
    # go_live writes FANOPS_LIVE (NOT FANOPS_POSTER); a channel routed postiz w/ key is LIVE-READY, so the
    # learn/reconcile gate must be TRUE even though the global poster_backend is dryrun (C1).
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_API_KEY", "sk")
    _accounts(tmp_path, [{"handle": "@ig", "platforms": ["instagram"], "status": "active",
                          "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    assert Config(root=tmp_path).is_live_backend is True


def test_is_live_backend_false_when_live_but_no_ready_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")                     # live switch on, but no provider/key anywhere
    _accounts(tmp_path, [{"handle": "@ig", "platforms": ["instagram"], "status": "active",
                          "integrations": {"instagram": "ig_1"}}])
    assert Config(root=tmp_path).is_live_backend is False


def test_is_live_backend_legacy_global_unchanged(tmp_path, monkeypatch):
    # byte-identical legacy path: a live global poster WITH its key is live, no accounts.json required.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "sk")
    assert Config(root=tmp_path).is_live_backend is True


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
    assert res == {"due": 1, "published": 0, "no_provider": 1, "no_integration_id": 0}
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


# ---- H1: track/reconcile READ paths route via effective_provider, not `resolve_backend or global` ----
def _submitted(led, pid, handle, platform, sub, acct_id="x"):
    led.add_post(Post(id=pid, parent_id="c", account=handle, account_id=acct_id, platform=platform,
                      caption="c", state=PostState.submitted, submission_id=sub, public_url="dryrun://c"))


def test_metrics_routing_uses_effective_provider_skips_none(tmp_path, monkeypatch, mocker):
    # live, FANOPS_POSTER unset: a zernio-routed channel pulls metrics from the ZERNIO client; a channel
    # with no provider is SKIPPED (never the dryrun/global client -> never silently starves a live post).
    monkeypatch.setenv("FANOPS_LIVE", "1")
    _accounts(tmp_path, [{"handle": "@tk", "platforms": ["tiktok"], "status": "active",
                          "backends": {"tiktok": "zernio"}, "integrations": {"tiktok": "z1"}},
                         {"handle": "@ig", "platforms": ["instagram"], "status": "active",
                          "integrations": {"instagram": "i1"}}])   # no provider -> skipped
    cfg = Config(root=tmp_path)
    from fanops import track
    seen = []
    mocker.patch.object(track, "_metrics_client_for",
                        side_effect=lambda c, b, ids: (seen.append((b, tuple(ids))), (lambda w="30d": []))[1])
    posts = [Post(id="p1", parent_id="c", account="@tk", account_id="z1", platform=Platform.tiktok,
                  caption="c", state=PostState.published, submission_id="s1", public_url="dryrun://p1"),
             Post(id="p2", parent_id="c", account="@ig", account_id="i1", platform=Platform.instagram,
                  caption="c", state=PostState.published, submission_id="s2", public_url="dryrun://p2")]
    track._default_list_posts(cfg, posts=posts)()
    assert seen == [("zernio", ("s1",))]                  # ONLY the zernio channel; the provider-less IG post skipped


def test_reconcile_routing_uses_effective_provider_skips_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    _accounts(tmp_path, [{"handle": "@tk", "platforms": ["tiktok"], "status": "active",
                          "backends": {"tiktok": "zernio"}, "integrations": {"tiktok": "z1"}},
                         {"handle": "@ig", "platforms": ["instagram"], "status": "active",
                          "integrations": {"instagram": "i1"}}])   # no provider -> skipped
    cfg = Config(root=tmp_path)
    from fanops.reconcile import _reconcilable_routing
    with Ledger.transaction(cfg) as led:
        _submitted(led, "p1", "@tk", Platform.tiktok, "s1", acct_id="z1")
        _submitted(led, "p2", "@ig", Platform.instagram, "s2", acct_id="i1")
    assert _reconcilable_routing(cfg, Ledger.load(cfg)) == {"s1": "zernio"}   # p2 skipped, NOT dryrun-defaulted


# ---- H5: zernio has NO server idempotency key -> the queued-only publish filter is the SOLE double-POST guard ----
def test_needs_reconcile_post_is_never_republished(tmp_path, monkeypatch, mocker):
    # an ambiguous-live (needs_reconcile) post must NEVER be re-submitted by publish_due — a re-POST would
    # double-publish (zernio publishNow:true carries no idempotency key). publish_due iterates `queued` ONLY.
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    cfg = Config(root=tmp_path)
    _accounts(tmp_path, [{"handle": "@tk", "platforms": ["tiktok"], "status": "active",
                          "backends": {"tiktok": "zernio"}, "integrations": {"tiktok": "z1"}}])
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c", account="@tk", account_id="z1", platform=Platform.tiktok,
                          caption="c", state=PostState.needs_reconcile, submission_id="s1",
                          media_urls=["https://x/v.mp4"], scheduled_time="2020-01-01T00:00:00+00:00", public_url="dryrun://p1"))
    gp = mocker.patch("fanops.post.run.get_poster")
    publish_due(cfg)
    gp.assert_not_called()                                   # never re-submitted
    assert Ledger.load(cfg).posts["p1"].state is PostState.needs_reconcile
