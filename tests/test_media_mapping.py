# tests/test_media_mapping.py
# Leg 2 Task 1 (Identify): resolve each published/analyzed IG post's Graph `media_id` from the live
# /{ig_user_id}/media list, matched by permalink. Pure-fixture (injected `get=`), no real network.
# Covers: permalink match stamps media_id; trailing-slash / scheme normalization; ambiguous permalink
# broken by timestamp<->published_at; an unmatched post is breadcrumbed (never a fabricated id); the
# resolver is fail-open ([] media list -> nobody stamped, no crash); a non-IG post is left alone.
from fanops.config import Config
from fanops.models import Post, PostState, Platform
from fanops.ledger import Ledger
from fanops import meta_graph, reconcile

_TOKEN = "SECRET-meta-token-xyz"


def _cfg(tmp_path, monkeypatch, *, token=_TOKEN, ig="ig-123"):
    monkeypatch.setenv("META_GRAPH_TOKEN", token) if token else monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.setenv("META_IG_USER_ID", ig) if ig else monkeypatch.delenv("META_IG_USER_ID", raising=False)
    return Config(root=tmp_path)


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _media_get(pages):
    """A fake requests.get returning `/media` pages in sequence (each a _Resp). Records call urls."""
    seq = list(pages)
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        if "/media" in url and seq:
            return seq.pop(0)
        return _Resp(404, None)
    get.calls = calls
    return get


def _post(pid, url, *, plat=Platform.instagram, state=PostState.published, published_at=None):
    return Post(id=pid, parent_id="clip1", account="@a", account_id="acc1", platform=plat,
                caption="c", state=state, public_url=url, published_at=published_at,
                submission_id=f"real_{pid}")


def _led(cfg, posts):
    led = Ledger(cfg)
    for p in posts:
        led.add_post(p)
    return led


# ---- list_user_media (the read half of identify) -------------------------------------------------

def test_list_user_media_paginates_and_fails_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    page1 = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                  "media_product_type": "REELS", "timestamp": "2026-06-30T10:00:00+0000"}],
                        "paging": {"next": "https://graph.facebook.com/v20.0/ig-123/media?after=CUR"}})
    page2 = _Resp(200, {"data": [{"id": "M2", "permalink": "https://www.instagram.com/reel/BBB/",
                                  "media_product_type": "REELS", "timestamp": "2026-06-29T10:00:00+0000"}]})
    media = meta_graph.list_user_media(cfg, get=_media_get([page1, page2]))
    ids = {m["id"] for m in media}
    assert ids == {"M1", "M2"}                       # both pages walked via paging.next
    # fail-open: a transport failure yields [] rather than raising
    assert meta_graph.list_user_media(cfg, get=_media_get([_Resp(500, None)])) == []


# ---- resolve_media_ids (the match + stamp) -------------------------------------------------------

def test_resolve_stamps_media_id_on_permalink_match(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                 "media_product_type": "REELS"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id == "M1"


def test_resolve_stamps_the_real_product_type(tmp_path, monkeypatch):
    # The insights request is DERIVED from product_type, so resolve must stamp the media's REAL
    # media_product_type (from the live media record), not leave the client to guess REELS. A FEED post
    # must be stamped FEED so the client sends the feed metric set (no reels-only avg-watch -> no 400).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/p/AAA/")])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/p/AAA/",
                                 "media_product_type": "FEED"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id == "M1"
    assert led.posts["p1"].product_type == "FEED"    # the real type, stamped alongside media_id


def test_resolve_normalizes_trailing_slash_and_scheme(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # post stored WITHOUT trailing slash; media permalink WITH it (+ differing case host) -> still matches
    led = _led(cfg, [_post("p1", "https://instagram.com/reel/AAA")])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                 "media_product_type": "REELS"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id == "M1"


def test_resolve_disambiguates_by_timestamp(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # two live media share the SAME normalized permalink (pathological but possible on re-share); the
    # post's published_at picks the nearer media timestamp rather than guessing the first.
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/",
                           published_at="2026-06-29T10:05:00Z")])
    page = _Resp(200, {"data": [
        {"id": "M_old", "permalink": "https://www.instagram.com/reel/AAA/",
         "media_product_type": "REELS", "timestamp": "2026-06-20T10:00:00+0000"},
        {"id": "M_near", "permalink": "https://www.instagram.com/reel/AAA/",
         "media_product_type": "REELS", "timestamp": "2026-06-29T10:00:00+0000"},
    ]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id == "M_near"      # nearest published_at wins, not first-seen


def test_resolve_breadcrumbs_unmatched_rests_never_fabricates(tmp_path, monkeypatch):
    # IG-liveness fix (the single-credential feed reality): an ENUMERATED-but-unmatched IG post is NOT
    # quarantined — its account's media simply isn't enumerable under the one global Graph credential, so
    # the Graph match is unavailable ENRICHMENT, not a liveness failure. The post RESTS in its terminal-
    # positive state (liveness stands on the Postiz-confirmed releaseURL), media_id stays None (never
    # fabricated), and only a NON-fatal enrichment breadcrumb is set.
    from fanops.reconcile import _IG_MEDIA_ENRICH_UNRESOLVED, _UNVERIFIED_PREFIX
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/ZZZ/")])   # _post defaults state=published
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                 "media_product_type": "REELS"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id is None          # no match -> NOT fabricated
    assert led.posts["p1"].state is PostState.published                 # RESTS (never demoted)
    assert led.posts["p1"].error_reason == _IG_MEDIA_ENRICH_UNRESOLVED  # non-fatal enrichment note
    assert not (led.posts["p1"].error_reason or "").startswith(_UNVERIFIED_PREFIX)   # NOT a quarantine sentinel


def test_resolve_leaves_non_ig_posts_alone(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("tk", "https://www.tiktok.com/@a/video/123", plat=Platform.tiktok)])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.tiktok.com/@a/video/123",
                                 "media_product_type": "REELS"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["tk"].media_id is None          # TikTok is not an IG media; never stamped from Graph


def test_resolve_skips_when_no_media_id_field_on_model(tmp_path, monkeypatch):
    # Model contract: a post that already carries a media_id AND product_type is not re-resolved (idempotent,
    # no wasted call). (A media_id-bearing row with product_type=None IS re-targeted — see the M2 test below.)
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post("p1", "https://www.instagram.com/reel/AAA/")
    p = p.model_copy(update={"media_id": "PRESET", "product_type": "REELS"})
    led = _led(cfg, [p])
    getter = _media_get([_Resp(200, {"data": []})])
    reconcile.resolve_media_ids(led, cfg, get=getter)
    assert led.posts["p1"].media_id == "PRESET"      # untouched


def test_resolve_backstamps_product_type_on_a_media_id_bearing_row(tmp_path, monkeypatch):
    # M2 residual (LIVE post_4eb7c0802e79): a row stamped with media_id by an EARLIER pass (before
    # product_type was carried) has media_id set but product_type=None. The pre-M2 target filter was
    # `media_id is None`, so such a row was NEVER re-visited -> product_type stayed None -> the insights
    # request derived [] -> empty `metric=` -> 400 -> false block. resolve_media_ids must ALSO target a
    # media_id-bearing row whose product_type is None, re-match its permalink, and back-stamp the real type
    # so the row heals on the next pass and real metrics flow.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post("p1", "https://www.instagram.com/reel/AAA/")
    p = p.model_copy(update={"media_id": "M1", "product_type": None})   # resolved id, type not yet carried
    led = _led(cfg, [p])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                 "media_product_type": "REELS"}]})
    reconcile.resolve_media_ids(led, cfg, get=_media_get([page]))
    assert led.posts["p1"].media_id == "M1"          # media_id preserved (not re-fabricated)
    assert led.posts["p1"].product_type == "REELS"   # the real type back-stamped -> request no longer empty


def test_resolve_empty_media_list_does_not_false_breadcrumb(tmp_path, monkeypatch):
    # No creds / transport failure -> list_user_media returns []; an unmatched post must stay CLEAN
    # (re-resolvable), NOT get a false "unmatched" breadcrumb — we never actually looked.
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    reconcile.resolve_media_ids(led, cfg, get=_media_get([_Resp(500, None)]))
    assert led.posts["p1"].media_id is None
    assert led.posts["p1"].error_reason is None      # empty list != unmatched; no breadcrumb


def test_pull_metrics_resolves_media_ids_in_the_pull_path(tmp_path, monkeypatch):
    # The automatic pull path (what the daemon runs) MUST self-resolve new posts' media_ids, else the
    # sole-source insights read can never reach them. pull_metrics threads a `resolve_media=` hook.
    from fanops.track import pull_metrics
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    page = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                 "media_product_type": "REELS"}]})
    getter = _media_get([page])
    # inject the media getter into the resolve step; list_posts stubbed empty (no metric rows this test)
    pull_metrics(led, cfg, list_posts=lambda w: [],
                 resolve_media=lambda ledg, conf: reconcile.resolve_media_ids(ledg, conf, get=getter))
    assert led.posts["p1"].media_id == "M1"          # resolved as a side effect of the pull


def test_cmd_map_media_is_read_only_and_fail_open(tmp_path, monkeypatch, capsys):
    # `fanops map-media` on a default (no-creds) env: fail-open, exit 0, stamps nothing, no crash/network.
    from fanops.cli import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    led.save()
    assert main(["map-media"]) == 0
    assert "media mapped" in capsys.readouterr().out
    assert Ledger.load(cfg).posts["p1"].media_id is None    # no creds -> nothing fabricated
