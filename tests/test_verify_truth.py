"""T4 — the STATE TRANSITION enforces identity-of-truth (not a read-only verb nobody runs).

A post may only REST in published/analyzed if its identity is CONFIRMED:
  IG      -> Postiz status==published + a real releaseURL (media_id arrives at promotion from releaseId)
  TikTok  -> a live-verified public_url (T8 oEmbed author==handle); for T4 the gate requires at
             least a non-empty safe_public_url AND a real (non fanops_) submission_id.
An UNCONFIRMED post QUARANTINES to a visible parked state (needs_reconcile) with a clear error_reason
rather than resting — FAIL CLOSED (unknown/unresolvable identity = NOT confirmed = parked). The park is
STABLE across passes (idempotent): a post awaiting its verifier lands in the SAME parked state each pass,
it never flips published<->parked every tick (that would be thrash, not a park).

Enforcement points:
  - reconcile_posts published branch: IG rests on Postiz confirmation; TikTok REST-gate + oEmbed.
  - Authored-post feed-match enrichment was deleted (MOL-775); it is not a liveness source.
"""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts


def _post(led, pid, state, *, platform=Platform.instagram, sub=None, url=None, media_id=None,
          post_type=None, account="a", published_at=None, error_reason=None):
    # a terminal-with-URL state needs a public_url to satisfy the R1 model invariant; callers pass a real
    # https url when the test is about the rest-gate, else a synthetic dryrun:// only to construct the row.
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    if url is None and state in _POST_TERMINAL_REQUIRES_URL:
        url = f"dryrun://{pid}"
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1", platform=platform,
                      caption="x", state=state, submission_id=sub, public_url=url, media_id=media_id,
                      post_type=post_type, published_at=published_at, error_reason=error_reason))


# ------------------------------------- IG: reconcile liveness stands on Postiz-confirmation --------
def test_reconcile_promotes_postiz_confirmed_ig_even_without_media_id(tmp_path):
    # THE 6-STUCK-POSTS FIX, reconcile side. An IG post parked in needs_reconcile whose Postiz get_status
    # returns status==published + a real releaseURL RESTS published — even though media_id is None.
    # Liveness authority is Postiz's published-confirmation, NOT a Graph media_id match.
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


def test_reconcile_promotes_fresh_ig_post_on_postiz_confirmation(tmp_path):
    # A FRESH IG post (no unverified sentinel, no media_id yet) MUST still promote to published on a valid
    # Postiz-confirmed URL. media_id is stamped at promotion from releaseId when the backend supplies it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "fresh", PostState.needs_reconcile, sub="postiz_fresh",
          url="https://www.instagram.com/reel/FRESH/", media_id=None, error_reason=None)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://www.instagram.com/reel/FRESH/"})
    assert led.posts["fresh"].state is PostState.published


# ---------------------------------------------------------------- TikTok: URL + real id gate (T4) ----
def test_tiktok_fake_token_quarantines(tmp_path):
    # TikTok with a fanops_ (fake) submission_id is not a GET key (I4) — never confirmed, never rested.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tt", PostState.needs_reconcile, platform=Platform.tiktok, sub="fanops_fake", account="tt")
    polled = []
    led = reconcile_posts(led, cfg, get_status=lambda sid: polled.append(sid) or {
        "status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7"})
    p = led.posts["tt"]
    assert polled == []
    assert p.state is PostState.needs_reconcile          # fake token -> never rests published
    assert p.state is not PostState.published


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
    polled = []
    def gs(sid):
        polled.append(sid)
        return {"status": "published", "publicUrl": "https://www.tiktok.com/@tt/video/7"}
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 1
    first = led.posts["tt"]
    led = reconcile_posts(led, cfg, get_status=gs)       # pass 2
    second = led.posts["tt"]
    assert polled == []
    assert first.state is PostState.needs_reconcile and second.state is PostState.needs_reconcile
    assert second.error_reason == first.error_reason     # identical -> stable, not thrash
