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
from fanops.reconcile import (reconcile_posts, resolve_media_ids, _UNVERIFIED_PREFIX,
                              _IG_MEDIA_ENRICH_UNRESOLVED)


def _post(led, pid, state, *, platform=Platform.instagram, sub=None, url=None, media_id=None,
          product_type=None, account="a", published_at=None, error_reason=None):
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


def test_ig_enumerated_unmatched_rests_with_enrichment_breadcrumb(tmp_path, mocker):
    # THE 6-STUCK-POSTS FIX. An IG post reached `analyzed` on a Postiz-confirmed releaseURL, but its
    # permalink is NOT in the (single-credential) enumerated Graph media — perca.late/cisumwolfhom reels
    # can never appear in markmakmouly's feed. That is an ENRICHMENT miss (this account's media isn't
    # enumerable), NOT a liveness failure: the post STAYS RESTING (analyzed), media_id stays None, and only
    # a NON-fatal enrichment breadcrumb is set. Liveness stands on Postiz; the Graph match is opportunistic.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "stuck", PostState.analyzed, url="https://www.instagram.com/reel/STUCK/")
    _enumerate_returns([{"id": "M_other", "permalink": "https://www.instagram.com/reel/OTHER/",
                         "media_product_type": "REELS"}])(mocker)
    led = resolve_media_ids(led, cfg)
    p = led.posts["stuck"]
    assert p.state is PostState.analyzed                 # RESTS — never knocked out of the terminal-positive state
    assert p.media_id is None                            # enrichment unavailable -> never fabricated
    assert p.error_reason == _IG_MEDIA_ENRICH_UNRESOLVED # a NON-fatal enrichment note, NOT the quarantine sentinel
    assert not (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)   # NOT a quarantine reason


def test_ig_enrichment_breadcrumb_is_stable_across_two_passes(tmp_path, mocker):
    # Idempotent / non-thrash: an unmatched-but-resting post keeps the SAME state + SAME enrichment reason
    # across two resolve_media_ids passes (it stays in the published/analyzed target set because media_id is
    # still None, but the second pass re-stamps the identical breadcrumb -> no churn, no demotion).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "stuck", PostState.analyzed, url="https://www.instagram.com/reel/STUCK/")
    patch = _enumerate_returns([{"id": "M_other", "permalink": "https://www.instagram.com/reel/OTHER/",
                                 "media_product_type": "REELS"}])
    patch(mocker)
    led = resolve_media_ids(led, cfg)                    # pass 1
    first = led.posts["stuck"]
    assert first.state is PostState.analyzed
    led = resolve_media_ids(led, cfg)                    # pass 2: still resting, same note
    second = led.posts["stuck"]
    assert second.state is PostState.analyzed            # SAME resting state (never demoted)
    assert second.error_reason == first.error_reason     # identical enrichment reason -> no churn


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


# ------------------------------------- IG: reconcile liveness now stands on Postiz-confirmation --------
def test_reconcile_promotes_postiz_confirmed_ig_even_without_media_id(tmp_path):
    # THE 6-STUCK-POSTS FIX, reconcile side. An IG post parked in needs_reconcile whose Postiz get_status
    # returns status==published + a real releaseURL RESTS published — even though its permalink is not
    # matchable in the single-credential Graph feed (media_id stays None). Liveness authority is Postiz's
    # published-confirmation, NOT a Graph media_id match. media_id enrichment stays opportunistic.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "confirmed", PostState.needs_reconcile, sub="postiz_real_1",
          url="https://www.instagram.com/reel/CONFIRMED/", media_id=None, error_reason=None)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.instagram.com/reel/CONFIRMED/"})
    p = led.posts["confirmed"]
    assert p.state is PostState.published                # rests on the Postiz-confirmed releaseURL
    assert p.media_id is None                            # Graph enrichment absent -> never fabricated
    assert p.error_reason is None                        # a clean promotion (no stale reason survives)


def test_reconcile_parks_ig_not_confirmed_by_postiz(tmp_path):
    # PHANTOM PROTECTION intact (the hole must NOT reopen): an IG post whose Postiz get_status does NOT
    # confirm published — here status 'unknown' (the row is absent / not published) — does NOT rest. A
    # stored public_url alone is never liveness proof; only a Postiz published-confirmation is.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "phantom", PostState.needs_reconcile, sub="postiz_real_1",
          url="https://www.instagram.com/reel/PHANTOM/", media_id=None, error_reason=None)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "unknown"})
    p = led.posts["phantom"]
    assert p.state is PostState.needs_reconcile          # NOT rested — Postiz never confirmed it published
    assert p.state is not PostState.published


def test_reconcile_parks_ig_published_but_no_releaseurl(tmp_path):
    # PHANTOM PROTECTION, the no-releaseURL shape: Postiz get_status returns status==published but NO
    # publicUrl (releaseURL absent -> get_status omits it), and the post has no prior url. That is NOT a
    # confirmed liveness signal (a published row must carry a real releaseURL) -> parked, never a ghost row.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "norel", PostState.needs_reconcile, sub="postiz_real_1", media_id=None, error_reason=None)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": None})
    p = led.posts["norel"]
    assert p.state is PostState.needs_reconcile          # published-with-no-url stays parked (R1 fail-closed)
    assert p.state is not PostState.published


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
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="fanops_fake", account="tt")
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
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="zreal_1", account="tt")
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
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="zreal_1", account="tt")
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
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="fanops_fake", account="tt")
    def gs(sid): return {"status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7"}
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 1
    first = led.posts["tt"]
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 2
    second = led.posts["tt"]
    assert first.state is PostState.needs_reconcile and second.state is PostState.needs_reconcile
    assert second.error_reason == first.error_reason     # identical -> stable, not thrash


# ---------------------------------------------------------------- prod-shaped ledger fixture ----
def test_prod_shaped_ledger_unmatched_rest_matched_stamps(tmp_path, mocker):
    # ACCEPT (the real 6-stuck shape): on a prod-shaped ledger — several IG reels from OTHER accounts
    # resting `analyzed` whose permalinks aren't in the single enumerable feed + ONE genuinely matched
    # post — resolve_media_ids leaves the unmatched ones RESTING (enrichment breadcrumb, media_id None)
    # and stamps the matched one. NOTHING is demoted out of the terminal-positive state.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(1, 4):
        _post(led, f"other{i}", PostState.analyzed, url=f"https://www.instagram.com/reel/OTH{i}/", media_id=None)
    _post(led, "ok", PostState.analyzed, url="https://www.instagram.com/reel/OK/", media_id="M_ok",
          product_type="REELS")   # already matched -> resolve_media_ids skips it (fully resolved)
    # live media contains ONLY the matched permalink (markmakmouly's); the others belong to feeds we can't enumerate.
    _enumerate_returns([{"id": "M_ok", "permalink": "https://www.instagram.com/reel/OK/",
                         "media_product_type": "REELS"}])(mocker)
    led = resolve_media_ids(led, cfg)
    for i in range(1, 4):
        assert led.posts[f"other{i}"].state is PostState.analyzed, f"unmatched other{i} must REST"
        assert led.posts[f"other{i}"].media_id is None                        # never fabricated
        assert led.posts[f"other{i}"].error_reason == _IG_MEDIA_ENRICH_UNRESOLVED   # non-fatal enrichment note
    assert led.posts["ok"].state is PostState.analyzed   # the matched post rests untouched
    assert led.posts["ok"].media_id == "M_ok"
