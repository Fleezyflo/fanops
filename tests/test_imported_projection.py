# tests/test_imported_projection.py — ledger-rebuild M2: the INVERSE projection.
# The forward direction (list_user_media + resolve_media_ids: match a live media to a ledger post by
# permalink, enrich it) ALREADY SHIPPED and is NOT retested here. The NEW work: iterate the live media
# inventory -> a live media that matches NO ledger post becomes an ImportedMedia record ("viewed there,
# not authored here"). Idempotent (re-run UPSERTS by media_id, never duplicates). Scoped to the
# CREDENTIALED handle (META_IG_USER_ID is single-handle). Pure-fixture (injected `get=`), no real network.
from fanops.config import Config
from fanops.models import Post, PostState, Platform
from fanops.ledger import Ledger
from fanops import reconcile

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
    seq = list(pages)
    def get(url, params=None, timeout=None):
        if "/media" in url and seq:
            return seq.pop(0)
        return _Resp(404, None)
    return get


def _post(pid, url, *, plat=Platform.instagram, state=PostState.published):
    return Post(id=pid, parent_id="clip1", account="@a", account_id="acc1", platform=plat,
                caption="c", state=state, public_url=url, submission_id=f"real_{pid}")


def _led(cfg, posts):
    led = Ledger(cfg)
    for p in posts:
        led.add_post(p)
    return led


def _page(media):
    return _Resp(200, {"data": media})


def test_unmatched_live_media_becomes_imported(tmp_path, monkeypatch):
    # A live media whose permalink matches NO ledger post is IMPORTED as an ImportedMedia record — the
    # "viewed there, not authored here" case (the whole point of the projection).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])                                   # empty ledger — every live media is unmatched
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/", "media_product_type": "REELS",
         "timestamp": "2026-06-30T10:00:00+0000"}])]))
    assert "M1" in led.imported_media
    im = led.imported_media["M1"]
    assert im.permalink == "https://www.instagram.com/reel/AAA/"
    assert im.product_type == "REELS"
    assert im.timestamp == "2026-06-30T10:00:00+0000"


def test_live_media_matching_a_post_is_NOT_imported(tmp_path, monkeypatch):
    # A live media whose permalink matches an EXISTING ledger post is "authored here" — it must NOT become
    # an ImportedMedia (that post is the authoritative record; import would duplicate meaning).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/", "media_product_type": "REELS"}])]))
    assert led.imported_media == {}                       # matched -> authored here -> not imported


def test_match_normalizes_scheme_and_trailing_slash(tmp_path, monkeypatch):
    # The authored-here skip uses the SAME normalization as resolve (host www-strip + trailing-slash), so a
    # post stored without the slash still shadows a live media WITH it (no spurious import of our own post).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://instagram.com/reel/AAA")])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/", "media_product_type": "REELS"}])]))
    assert led.imported_media == {}


def test_projection_is_idempotent_upsert_no_duplicate(tmp_path, monkeypatch):
    # Re-running the projection over the SAME live media UPSERTS by media_id — never a second row. A later
    # pull with fresher fields (a new product_type) OVERWRITES (the latest live snapshot wins).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": None}])]))
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS"}])]))
    assert list(led.imported_media) == ["M1"]             # exactly ONE row (no dup)
    assert led.imported_media["M1"].product_type == "REELS"   # latest snapshot won


def test_projection_preserves_metrics_on_reimport(tmp_path, monkeypatch):
    # An UPSERT must not clobber accumulated insights: a re-import of a media whose ImportedMedia already
    # carries metrics/metrics_series (filled by M3) keeps them (the live /media list has no insights — the
    # projection updates identity fields, it does NOT erase the metrics the insights read landed).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS"}])]))
    # simulate M3 having filled metrics on the imported row
    led.imported_media["M1"] = led.imported_media["M1"].model_copy(
        update={"metrics": {"reach": 500}, "metrics_series": [{"offset": "P1D", "reach": 500}]})
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS"}])]))
    assert led.imported_media["M1"].metrics == {"reach": 500}          # metrics survived the re-import
    assert led.imported_media["M1"].metrics_series[0]["reach"] == 500


def test_projection_fail_open_no_creds(tmp_path, monkeypatch):
    # No creds -> list_user_media returns [] -> nothing imported, no crash (mirrors resolve_media_ids).
    cfg = _cfg(tmp_path, monkeypatch, token=None, ig=None)
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS"}])]))
    assert led.imported_media == {}


def test_projection_fail_open_empty_media(tmp_path, monkeypatch):
    # Creds present but the live media list is empty (or a transport failure) -> nothing imported, no crash.
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([])]))
    assert led.imported_media == {}


def test_imported_media_carries_credentialed_handle_scope(tmp_path, monkeypatch):
    # META_IG_USER_ID is single-handle: the projection enumerates ONE handle's media, so each ImportedMedia
    # is stamped with that credentialed handle (the scope label the Live library + wipe preview must show).
    cfg = _cfg(tmp_path, monkeypatch, ig="ig-777")
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS"}])]))
    assert led.imported_media["M1"].account == "ig-777"                # the credentialed handle scope
    assert led.imported_media["M1"].imported_at is not None            # audit stamp set


def test_projection_captures_caption_when_present(tmp_path, monkeypatch):
    # When the live /media record carries a caption, mirror it (display-only). Absent -> None (no crash).
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [])
    reconcile.project_imported_media(led, cfg, get=_media_get([_page([
        {"id": "M1", "permalink": "https://ig/reel/AAA/", "media_product_type": "REELS",
         "caption": "live caption text"}])]))
    assert led.imported_media["M1"].caption == "live caption text"
