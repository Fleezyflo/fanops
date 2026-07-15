"""RC-3b / S07 — the publish PRODUCER and the reconcile CONSUMER of `submitting` share ONE backend
capability (`accounts.channel_provider_if_ready`), so it is impossible to mint a `submitting` post under
a backend configuration for which reconciliation (`cfg.is_live_backend`) will not run.

Before S07 the producer claimed whenever a provider merely RESOLVED (`effective_provider != None`), while
the consumer additionally required CREDS (`is_live_backend` -> `live_ready_channels` -> `backend_has_creds`).
A live provider without a key therefore minted a `submitting` post that reconcile — disabled by that very
same missing key — would never resolve: stranded forever. `test_credless_live_provider_never_mints_submitting`
is the regression: it FAILS on pre-S07 `main` (post reaches `submitting`) and PASSES after (refused, `queued`).
"""
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
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)   # never start a real stack
    yield


def _accounts(tmp_path, backend, platform="instagram"):
    p = Config(root=tmp_path).accounts_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"accounts": [
        {"handle": "@h", "account_id": "h1", "platforms": [platform], "status": "active",
         "backends": ({platform: backend} if backend is not None else {})}]}))   # None -> no explicit provider


def _queued(cfg, platform=Platform.instagram):
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c", account="h", account_id="h1", platform=platform,
                          caption="c", state=PostState.queued, media_urls=["https://x/v.mp4"],
                          scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://c"))


def _park_poster(monkeypatch):
    """A poster that parks in needs_reconcile (the normal fresh-Postiz outcome) — proves the post was CLAIMED
    without a real network call."""
    import fanops.post.run as run
    class _P:
        def publish(self, led, pid):
            led.posts[pid] = led.posts[pid].model_copy(update={"state": PostState.needs_reconcile})
            return led
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: _P())
    monkeypatch.setattr(run, "_ensure_media", lambda *a, **k: None, raising=False)


# ── 1. permitted to BOTH publish and reconcile ──────────────────────────────────────────────
def test_live_ready_channel_publishes_and_is_reconcilable(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path); _queued(cfg)
    _park_poster(monkeypatch)
    res = publish_due(cfg)
    assert res["not_live_ready"] == 0
    assert Ledger.load(cfg).posts["p1"].state is not PostState.queued        # CLAIMED (producer ran)
    assert cfg.is_live_backend is True                                       # reconcile ENABLED (consumer runs)


# ── 2. permitted for NEITHER (not live) ─────────────────────────────────────────────────────
def test_not_live_neither_publishes_nor_reconciles(tmp_path):
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path); _queued(cfg)     # FANOPS_LIVE unset -> dryrun
    res = publish_due(cfg)
    assert res["published"] == 0 and res["not_distributed"] >= 1              # dryrun preview, never claimed
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued
    assert cfg.is_live_backend is False


# ── 3. dry-run backend on a LIVE system → preview, never a claim ─────────────────────────────
def test_explicit_dryrun_channel_while_live_previews_never_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _accounts(tmp_path, "dryrun"); cfg = Config(root=tmp_path); _queued(cfg)     # channel explicitly dryrun
    res = publish_due(cfg)
    assert res["not_distributed"] >= 1 and res["published"] == 0              # dryrun path preserved (test #3)
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued
    assert Accounts.load(cfg).channel_provider_if_ready("h", Platform.instagram) is None


# ── 4. unknown backend → dropped at load (S02) → refused by BOTH ─────────────────────────────
def test_unknown_backend_refused_by_both(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _accounts(tmp_path, "bogus"); cfg = Config(root=tmp_path); _queued(cfg)      # unknown provider (hand-edit)
    accts = Accounts.load(cfg)
    assert accts.accounts[0].backends == {}                                  # S02: unrecoverable value DROPPED at load...
    assert any("bogus" in s for s in accts.skipped_rows)                     # ...loudly (surfaced via validate() -> doctor)
    res = publish_due(cfg)
    assert res["no_provider"] == 1                                           # dropped -> no provider -> producer refuses
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued            # never submitting
    assert accts.channel_provider_if_ready("h", Platform.instagram) is None  # consumer excludes it too — symmetric


# ── 5. recoverable backend value (trailing space) → NORMALIZED at load (S02) → admitted SYMMETRICALLY ──
def test_normalized_backend_value_admitted_symmetrically(tmp_path, monkeypatch):
    # S02 now normalizes a RECOVERABLE hand-edit at load ('postiz ' -> 'postiz', the same strip+lower
    # set_backend applies on the write path), so the typo becomes a WORKING channel — and the shared
    # predicate admits it SYMMETRICALLY (producer claims IFF consumer reconciles), exactly as for a clean
    # 'postiz'. (Pre-S02 the value stayed malformed and BOTH refused; the RC-3b parity holds either way —
    # that is the point. An UNRECOVERABLE typo is instead dropped + refused: see test 4.)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _accounts(tmp_path, "postiz "); cfg = Config(root=tmp_path); _queued(cfg)
    _park_poster(monkeypatch)                                                 # once admitted, avoid a real network call
    accts = Accounts.load(cfg)
    assert accts.accounts[0].backends == {"instagram": "postiz"}             # S02 repaired the trailing space at load
    assert accts.channel_provider_if_ready("h", Platform.instagram) == "postiz"   # consumer admits
    res = publish_due(cfg)
    assert res["not_live_ready"] == 0
    assert Ledger.load(cfg).posts["p1"].state is not PostState.queued        # producer claimed -> symmetric admit
    assert cfg.is_live_backend is True                                        # consumer ON too — symmetric


# ── 6. THE REGRESSION: a cred-less live provider disabled reconcile but previously allowed publishing ──
def test_credless_live_provider_never_mints_submitting(tmp_path, monkeypatch):
    # backends[ig]=postiz but NO POSTIZ_API_KEY: effective_provider RESOLVES 'postiz' (so the OLD producer
    # claimed -> submitting), yet is_live_backend is False (no creds -> reconcile OFF). The post would strand
    # `submitting` forever. FAILS on pre-S07 main (reaches submitting); PASSES after (refused, stays queued).
    monkeypatch.setenv("FANOPS_LIVE", "1")                                    # live switch ON, key ABSENT
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path); _queued(cfg)
    assert cfg.is_live_backend is False                                       # the consumer is DISABLED...
    res = publish_due(cfg)
    assert res["not_live_ready"] == 1                                         # ...so the producer REFUSES
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued            # NEVER submitting -> no strand


# ── 7. the invariant via publish_post (Studio "Publish now") too ─────────────────────────────
def test_publish_post_refuses_when_reconciler_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")                                    # cred-less -> reconcile off
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path); _queued(cfg)
    assert cfg.is_live_backend is False
    assert publish_post(cfg, "p1") is None                                    # refused
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued            # never submitting


# ── 8. an existing `submitting` post stays visible and is NOT silently rewritten ─────────────
def test_existing_submitting_post_not_rewritten_by_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")                                    # cred-less config
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c", account="h", account_id="h1", platform=Platform.instagram,
                          caption="c", state=PostState.submitting, submission_id="fanops_x",
                          scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://c"))
    publish_due(cfg)                                                          # iterates queued only
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.submitting and p.submission_id == "fanops_x"  # untouched


# ── 9. the refusal introduces NO publish/retry (double-post) path ────────────────────────────
def test_gate_introduces_no_publish_or_retry_path(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_LIVE", "1")                                    # cred-less -> refused at claim
    _accounts(tmp_path, "postiz"); cfg = Config(root=tmp_path); _queued(cfg)
    gp = mocker.patch("fanops.post.run.get_poster")
    publish_due(cfg)
    gp.assert_not_called()                                                    # no poster -> no double-post, no retry


# ── 10. SHARED-PREDICATE PARITY — future edits cannot split producer and consumer again ──────
@pytest.mark.parametrize("backend,platform,key,ready", [
    ("postiz", "instagram", "POSTIZ_API_KEY", True),    # live + creds  -> both publish AND reconcile
    ("postiz", "instagram", None, False),               # live, no key  -> NEITHER
    ("zernio", "tiktok", "ZERNIO_API_KEY", True),
    ("dryrun", "instagram", None, False),               # dryrun        -> never ready
    ("bogus", "instagram", None, False),                # unknown       -> never ready
])
def test_producer_consumer_share_one_capability(tmp_path, monkeypatch, backend, platform, key, ready):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    if key:
        monkeypatch.setenv(key, "k")
    _accounts(tmp_path, backend, platform=platform)
    cfg = Config(root=tmp_path); accts = Accounts.load(cfg); plat = Platform(platform)

    admitted = accts.channel_provider_if_ready("h", plat) is not None
    assert admitted is ready                                                  # the ONE predicate

    # the CONSUMER chain is exactly its aggregate:
    in_live_ready = any(h == "h" and p == platform for (h, p, _) in accts.live_ready_channels())
    assert in_live_ready is ready
    assert cfg.is_live_backend is ready                                       # reconcile runs IFF the predicate admits

    # the PRODUCER claims a `submitting` post IFF the same predicate admits — never for a config the
    # consumer refuses. This is the parity a future edit cannot split without turning this red.
    _queued(cfg, platform=plat)
    _park_poster(monkeypatch)
    publish_due(cfg)
    claimed = Ledger.load(cfg).posts["p1"].state is not PostState.queued
    assert claimed is ready


# ── EXHAUSTIVE PARITY — THE permanent RC-3b regression: producer-claim == consumer-reconcile ──────────
# The invariant PROVEN, not assumed. For EVERY (is_live × backend × postiz-key × zernio-key × platform)
# the producer's ACTUAL claim decision — does a queued post enter `submitting`? (run the real publish_due) —
# equals the consumer's ACTUAL gate — `cfg.is_live_backend`, exactly what `_reconcile_safe` reads. Not
# "looks equivalent": both sides are executed, on all 96 backend states. A future edit that lets publishing
# claim a channel reconcile will not run for — OR that disables reconcile for a channel publishing still
# claims — turns exactly one cell red. FANOPS_POSTER is left UNSET so a single channel's readiness (not a
# legacy global bridge) is the sole determinant, making the per-channel producer and the aggregate
# `is_live_backend` coincide.
@pytest.mark.parametrize("is_live", [False, True])
@pytest.mark.parametrize("backend", ["postiz", "zernio", "dryrun", "bogus", "postiz ", None])
@pytest.mark.parametrize("postiz_key", [False, True])
@pytest.mark.parametrize("zernio_key", [False, True])
@pytest.mark.parametrize("platform", ["instagram", "tiktok"])
def test_exhaustive_producer_consumer_parity(tmp_path, monkeypatch, is_live, backend, postiz_key, zernio_key, platform):
    if is_live:
        monkeypatch.setenv("FANOPS_LIVE", "1")
    if postiz_key:
        monkeypatch.setenv("POSTIZ_API_KEY", "k")
    if zernio_key:
        monkeypatch.setenv("ZERNIO_API_KEY", "k")
    plat = Platform(platform)
    _accounts(tmp_path, backend, platform=platform)          # single channel — S02 normalizes at load ('postiz '->'postiz', 'bogus' dropped), so producer + consumer read the SAME resolved backend; parity holds across the resulting states
    cfg = Config(root=tmp_path)

    # CONSUMER permission — the EXACT predicate `_reconcile_safe` gates reconcile on.
    consumer_reconcile = cfg.is_live_backend

    # PRODUCER permission — run the REAL producer; did the queued post enter `submitting` (leave queued)?
    _queued(cfg, platform=plat)
    _park_poster(monkeypatch)                                # a live-ready channel claims -> parks needs_reconcile
    publish_due(cfg)
    producer_claim = Ledger.load(cfg).posts["p1"].state is not PostState.queued

    assert producer_claim == consumer_reconcile, (
        f"RC-3b PARITY BROKEN: is_live={is_live} backend={backend!r} postiz_key={postiz_key} "
        f"zernio_key={zernio_key} platform={platform} -> producer_claim={producer_claim}, "
        f"consumer_reconcile={consumer_reconcile}. A post may enter `submitting` IFF reconcile will run.")
