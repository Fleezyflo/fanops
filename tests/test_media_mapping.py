# tests/test_media_mapping.py
# Leg 2 Identify read-half + map-media import path. Authored-post feed-match deleted in MOL-775 —
# media_id/post_type arrive at publish. Pure-fixture (injected `get=`), no real network.
from fanops.config import Config
from fanops.models import Post, PostState, Platform
from fanops.ledger import Ledger
from fanops import meta_graph

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
    return Post(id=pid, parent_id="clip1", account="a", account_id="acc1", platform=plat,
                caption="c", state=state, public_url=url, published_at=published_at,
                submission_id=f"real_{pid}")


def _led(cfg, posts):
    led = Ledger(cfg)
    for p in posts:
        led.add_post(p)
    return led


# ---- list_user_media (the read half of identify) -------------------------------------------------

def test_list_user_media_paginates(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    page1 = _Resp(200, {"data": [{"id": "M1", "permalink": "https://www.instagram.com/reel/AAA/",
                                  "media_product_type": "REELS", "timestamp": "2026-06-30T10:00:00+0000"}],
                        "paging": {"next": f"{cfg.meta_graph_url}/ig-123/media?after=CUR"}})
    page2 = _Resp(200, {"data": [{"id": "M2", "permalink": "https://www.instagram.com/reel/BBB/",
                                  "media_product_type": "REELS", "timestamp": "2026-06-29T10:00:00+0000"}]})
    media = meta_graph.list_user_media(cfg, get=_media_get([page1, page2]))
    assert {m["id"] for m in media} == {"M1", "M2"}          # both pages walked via paging.next


def test_list_user_media_http_500_invents_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.list_user_media(cfg, get=_media_get([_Resp(500, None)])) == []


def test_pull_metrics_default_path_does_not_stamp_media_id(tmp_path, monkeypatch):
    # MOL-790/775: media_id arrives at promotion; a default pull with an empty fetch must not
    # fabricate one. list_posts is the production fetch seam (not a fanops.* setattr).
    from fanops.track import pull_metrics
    cfg = _cfg(tmp_path, monkeypatch)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    pull_metrics(led, cfg, list_posts=lambda w: [])
    assert led.posts["p1"].media_id is None


def test_cmd_map_media_no_creds_invents_nothing(tmp_path, monkeypatch, capsys):
    # `fanops map-media` with no Graph creds: authored media_id stays None (nothing fabricated).
    from fanops.cli import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("p1", "https://www.instagram.com/reel/AAA/")])
    led.save()
    assert main(["map-media"]) == 0
    assert "media mapped" in capsys.readouterr().out
    assert Ledger.load(cfg).posts["p1"].media_id is None
