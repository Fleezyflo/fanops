"""M2 public_url scheme-guard: a public_url captured from a backend (Postiz releaseURL / Blotato
publicUrl) must be a valid https:// permalink or nothing — never a malformed/non-http string persisted
and later surfaced as a dead 'live URL'. The guard runs at the automated CAPTURE points (reconcile +
the postiz publish-time permalink); operator-supplied URLs (`fanops resolve --url`, Studio mark-posted)
are the operator's explicit intent and stay untouched."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts


def _parked(led, pid="p"):
    led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="s"))


def test_safe_public_url_accepts_only_well_formed_https():
    from fanops.text import safe_public_url
    assert safe_public_url("https://www.instagram.com/p/abc/") == "https://www.instagram.com/p/abc/"
    assert safe_public_url("  https://x.com/p  ") == "https://x.com/p"   # trimmed
    assert safe_public_url("http://x.com/p") is None        # http rejected (public permalinks are https)
    assert safe_public_url("javascript:alert(1)") is None   # non-web scheme
    assert safe_public_url("ftp://x.com/p") is None
    assert safe_public_url("not-a-url") is None
    assert safe_public_url("https://") is None              # scheme but no host
    assert safe_public_url("https://evil\n.com/p") is None   # embedded newline -> malformed/injected
    assert safe_public_url("https://x.com/a b") is None      # internal whitespace
    assert safe_public_url("") is None
    assert safe_public_url(None) is None


def test_reconcile_drops_a_non_https_public_url(tmp_path):
    # the audit target: a malformed publicUrl from the backend must NOT be persisted on the post.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _parked(led)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "javascript:alert(1)"})
    assert led.posts["p"].state is PostState.published      # state still records the publish
    assert led.posts["p"].public_url is None                # but the malformed url was rejected


def test_reconcile_keeps_a_valid_https_public_url(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _parked(led)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "https://insta/p/abc"})
    assert led.posts["p"].public_url == "https://insta/p/abc"


def test_reconcile_bad_url_does_not_clobber_an_existing_valid_url(tmp_path):
    # a later poll returning a malformed url must not erase a previously-captured good permalink.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="s",
                      public_url="https://insta/p/good"))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "not-a-url"})
    assert led.posts["p"].public_url == "https://insta/p/good"   # kept the good one
