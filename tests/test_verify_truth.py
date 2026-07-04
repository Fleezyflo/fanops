"""T4 — the STATE TRANSITION enforces identity-of-truth (not a read-only verb nobody runs).

A post may only REST in published/analyzed if its identity is CONFIRMED:
  IG      -> a matched Graph media_id is stamped (resolve_media_ids matched the permalink)
  TikTok  -> a live-verified public_url (T8 oEmbed author==handle); for T4 the gate requires at
             least a non-empty safe_public_url AND a real (non fanops_) submission_id.
An UNCONFIRMED post QUARANTINES to a visible parked state (needs_reconcile) with a clear error_reason
rather than resting — FAIL CLOSED (unknown/unresolvable identity = NOT confirmed = parked). The park is
STABLE across passes (idempotent): a post awaiting its verifier lands in the SAME parked state each pass,
it never flips published<->parked every tick (that would be thrash, not a park).

The two coordinated enforcement points:
  - resolve_media_ids (targets published/analyzed): an IG post whose permalink was ENUMERATED-but-unmatched
    has its media_id PROVEN absent -> it is QUARANTINED out of published/analyzed to needs_reconcile
    (was: breadcrumb only, left resting). This is the phantom-IG-URL fix (6 reels rested `analyzed` on a
    `media_id_unmatched` breadcrumb nobody actioned).
  - reconcile_posts published branch: the TikTok rest-gate + the guard that a re-polled IG phantom
    (unverified sentinel, still no media_id) is NOT re-promoted to published (else it would thrash with
    resolve_media_ids parking it back).
"""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts, resolve_media_ids, _UNVERIFIED_PREFIX


def _post(led, pid, state, *, platform=Platform.instagram, sub=None, url=None, media_id=None,
          product_type=None, account="@a", published_at=None, error_reason=None):
    # a terminal-with-URL state needs a public_url to satisfy the R1 model invariant; callers pass a real
    # https url when the test is about the rest-gate, else a synthetic dryrun:// only to construct the row.
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    if url is None and state in _POST_TERMINAL_REQUIRES_URL:
        url = f"dryrun://{pid}"
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1", platform=platform,
                      caption="x", state=state, submission_id=sub, public_url=url, media_id=media_id,
                      product_type=product_type, published_at=published_at, error_reason=error_reason))


# ---------------------------------------------------------------- IG: unmatched permalink quarantines ----
def _enumerate_returns(media):
    # patch meta_graph.enumerate_scoped_media -> a fixed list of (handle, media) pairs (the shape
    # resolve_media_ids consumes). credentialed_ig_handles -> [] so the fan-out falls to the single [None].
    def _patch(mocker):
        mocker.patch("fanops.meta_graph.credentialed_ig_handles", return_value=[])
        mocker.patch("fanops.meta_graph.enumerate_scoped_media", return_value=[(None, m) for m in media])
    return _patch


def test_ig_public_url_without_media_id_quarantines_when_enumerated_unmatched(tmp_path, mocker):
    # The phantom shape: an IG post rests `analyzed` with a public_url but NO media_id. resolve_media_ids
    # enumerates live media, the permalink is NOT among them -> media_id is PROVEN absent -> the post is
    # QUARANTINED to needs_reconcile (out of the terminal-positive rest), carrying a clear error_reason.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "phantom", PostState.analyzed, url="https://www.instagram.com/reel/PHANTOM/")
    _enumerate_returns([{"id": "M_other", "permalink": "https://www.instagram.com/reel/OTHER/",
                         "media_product_type": "REELS"}])(mocker)
    led = resolve_media_ids(led, cfg)
    p = led.posts["phantom"]
    assert p.state is PostState.needs_reconcile          # quarantined OUT of analyzed (no longer rests)
    assert p.media_id is None                            # never fabricated
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)
    assert "media_id" in (p.error_reason or "")


def test_ig_phantom_quarantine_is_stable_across_two_passes(tmp_path, mocker):
    # Idempotent / non-thrash: once quarantined to needs_reconcile the post is NO LONGER in the
    # published/analyzed target set, so a SECOND resolve_media_ids pass leaves it in the SAME parked state
    # (same error_reason) — it does not flip back to analyzed and re-park (that would be thrash).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "phantom", PostState.analyzed, url="https://www.instagram.com/reel/PHANTOM/")
    patch = _enumerate_returns([{"id": "M_other", "permalink": "https://www.instagram.com/reel/OTHER/",
                                 "media_product_type": "REELS"}])
    patch(mocker)
    led = resolve_media_ids(led, cfg)                    # pass 1: quarantine
    first = led.posts["phantom"]
    assert first.state is PostState.needs_reconcile
    led = resolve_media_ids(led, cfg)                    # pass 2: must be a no-op (stable park)
    second = led.posts["phantom"]
    assert second.state is PostState.needs_reconcile     # SAME parked state
    assert second.error_reason == first.error_reason     # byte-identical reason -> no re-stamp churn


def test_ig_with_media_id_rests(tmp_path, mocker):
    # The matched post: its permalink IS among the enumerated live media -> media_id stamped -> it RESTS
    # in a terminal-positive state (published/analyzed), never quarantined.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "real", PostState.published, url="https://www.instagram.com/reel/REAL/")
    _enumerate_returns([{"id": "M_real", "permalink": "https://www.instagram.com/reel/REAL/",
                         "media_product_type": "REELS"}])(mocker)
    led = resolve_media_ids(led, cfg)
    p = led.posts["real"]
    assert p.state is PostState.published                # rests (confirmed by a matched media_id)
    assert p.media_id == "M_real"


def test_ig_not_enumerable_does_not_quarantine(tmp_path, mocker):
    # Preserve resolve_media_ids' fail-open posture: if live media can't be ENUMERATED at all (no creds /
    # transport), the post stays re-resolvable (published) — we did NOT prove media_id absent, so we must
    # NOT quarantine (that would be a false alarm on a transient outage). The NEW fail-closed behavior is
    # specifically about a PROVEN-absent identity, not an un-checkable one.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pending", PostState.published, url="https://www.instagram.com/reel/PENDING/")
    mocker.patch("fanops.meta_graph.credentialed_ig_handles", return_value=[])
    mocker.patch("fanops.meta_graph.enumerate_scoped_media", return_value=[])   # couldn't enumerate
    led = resolve_media_ids(led, cfg)
    p = led.posts["pending"]
    assert p.state is PostState.published                # still resting, re-resolvable next pass
    assert p.error_reason is None                        # no false quarantine breadcrumb


# ---------------------------------------------------------------- IG: re-poll of a quarantined phantom ----
def test_reconcile_does_not_repromote_quarantined_ig_phantom(tmp_path):
    # Non-thrash across the two functions: after resolve_media_ids parks a phantom to needs_reconcile, the
    # next reconcile_due re-polls it (it's reconcilable) and the backend still reports "published" + the SAME
    # url. reconcile_posts must NOT re-promote it to published (that would thrash with resolve_media_ids
    # re-parking it) — an IG post carrying the unverified sentinel with STILL no media_id stays parked.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "phantom", PostState.needs_reconcile, sub="fanops_tok",
          url="https://www.instagram.com/reel/PHANTOM/", media_id=None,
          error_reason=_UNVERIFIED_PREFIX + " IG media_id not matched")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.instagram.com/reel/PHANTOM/"})
    p = led.posts["phantom"]
    assert p.state is PostState.needs_reconcile          # NOT re-promoted -> stable
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)


def test_reconcile_promotes_fresh_ig_post_so_media_id_can_resolve(tmp_path):
    # A FRESH IG post (no unverified sentinel, no media_id yet) MUST still promote to published on a valid
    # URL — resolve_media_ids only targets published/analyzed, so refusing here would create a chicken-and-egg
    # where media_id can never be stamped. The IG identity-of-truth (media_id) is enforced at resolve_media_ids
    # AFTER this promotion, not by refusing the promotion outright.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "fresh", PostState.needs_reconcile, sub="fanops_tok",
          url="https://www.instagram.com/reel/FRESH/", media_id=None, error_reason=None)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.instagram.com/reel/FRESH/"})
    assert led.posts["fresh"].state is PostState.published


# ---------------------------------------------------------------- TikTok: URL + real id gate (T4) ----
def test_tiktok_fake_token_quarantines(tmp_path):
    # TikTok with a fanops_ (fake) submission_id can NEVER attribute a real post -> NOT confirmed -> the post
    # QUARANTINES (needs_reconcile) even though the backend claims published with a url. FAIL CLOSED.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="fanops_fake", account="@tt")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7"})
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile          # fake token -> never rests published
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)


def test_tiktok_real_id_but_no_url_quarantines(tmp_path, monkeypatch, mocker):
    # Zernio returns {status:published, publicUrl:None} — claims published, gives NO url. A TikTok post with
    # a real submission_id but no captured url is NOT confirmed (no live-verifiable permalink) -> parked. The
    # T8 analytics fallback runs (real key set) but the /analytics body carries no url either -> stays parked.
    # requests.get is mocked so the fallback + any verify are network-free and deterministic.
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="zreal_1", account="@tt")
    class _OE:
        def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
        def json(s): return s._b
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_OE(200, {"platformAnalytics": [{"platform": "tiktok", "playCount": 9000}]}))  # no url
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": None})
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile          # no url anywhere -> parked, not rested
    assert p.state is not PostState.published


def test_tiktok_real_id_and_url_rests_when_oembed_verifies(tmp_path, mocker):
    # A TikTok post with a real submission_id AND a public_url that oEmbed-verifies to the ZERNIO-REPORTED tiktok
    # username RESTS published — the full confirmed shape (real id + live-verified url + author==reported username).
    # The oEmbed getter is patched at the module level so the REST-gate's live verify runs against the fake, no
    # network. (Pre-T8 this asserted real-id+url alone; T8 added oEmbed; the username fix keys the compare off the
    # username Zernio reports — surfaced in the status dict as tiktokUsername — not the internal handle.)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="zreal_1", account="@tt")
    class _OE:
        def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
        def json(s): return s._b
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_OE(200, {"author_unique_id": "tt", "author_url": "https://www.tiktok.com/@tt"}))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7", "tiktokUsername": "tt"})
    assert led.posts["tt"].state is PostState.published


def test_tiktok_park_is_stable_across_two_passes(tmp_path):
    # INTERIM proof (the T4<->T8 window): a TikTok post that can't yet be confirmed parks STABLY — two
    # reconcile passes leave it in the SAME needs_reconcile state with the SAME error_reason. That proves the
    # interim SIT-PARKED is a deterministic park, NOT a published<->parked thrash every tick.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="fanops_fake", account="@tt")
    def gs(sid): return {"status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7"}
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 1
    first = led.posts["tt"]
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 2
    second = led.posts["tt"]
    assert first.state is PostState.needs_reconcile and second.state is PostState.needs_reconcile
    assert second.error_reason == first.error_reason     # identical -> stable, not thrash


# ---------------------------------------------------------------- prod-shaped ledger fixture ----
def test_prod_shaped_ledger_only_phantoms_park_matched_rests(tmp_path, mocker):
    # ACCEPT: on the current-prod-shaped ledger (several phantom IG reels resting `analyzed` on a
    # media_id_unmatched shape + ONE genuinely matched post), resolve_media_ids parks EXACTLY the phantoms
    # and the matched post RESTS.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(1, 4):
        _post(led, f"ph{i}", PostState.analyzed, url=f"https://www.instagram.com/reel/PH{i}/", media_id=None)
    _post(led, "ok", PostState.analyzed, url="https://www.instagram.com/reel/OK/", media_id="M_ok",
          product_type="REELS")   # already matched -> resolve_media_ids skips it (fully resolved)
    # live media contains ONLY the matched permalink; the phantoms are absent -> proven-unmatched.
    _enumerate_returns([{"id": "M_ok", "permalink": "https://www.instagram.com/reel/OK/",
                         "media_product_type": "REELS"}])(mocker)
    led = resolve_media_ids(led, cfg)
    for i in range(1, 4):
        assert led.posts[f"ph{i}"].state is PostState.needs_reconcile, f"phantom ph{i} must park"
        assert (led.posts[f"ph{i}"].error_reason or "").startswith(_UNVERIFIED_PREFIX)
    assert led.posts["ok"].state is PostState.analyzed   # the matched post rests untouched
    assert led.posts["ok"].media_id == "M_ok"
