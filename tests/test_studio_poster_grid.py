# tests/test_studio_poster_grid.py — the black-box-grid fixes on /review and /publish:
#  (1) every <video> carries a non-empty poster= (the cached first-frame) so the box shows a frame;
#  (2) the grids are CAPPED (page size) with a visible "showing N of M" count + show-more affordance;
#  (3) /publish renders a wired "Publish now" button per card (operator asked for it).
import re
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.app import create_app
from fanops.studio import views

FAR = "2099-06-01T00:00:00Z"


def _accounts(cfg, n_handles=1):
    import json
    accts = [{"handle": f"@a{i}", "account_id": str(i), "platforms": ["instagram"], "status": "active"}
             for i in range(n_handles)]
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))


def _seed(cfg, n_clips=1, *, with_posts=True, state=PostState.queued):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/show.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    for i in range(n_clips):
        cid = f"clip_{i}"
        (cdir / f"{cid}.mp4").write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16,
                          state=ClipState.queued))
        if with_posts:
            led.add_post(Post(id=f"p{i}", parent_id=cid, account="@a0", account_id="0",
                              platform=Platform.instagram, caption="c", state=state,
                              scheduled_time=FAR, public_url="dryrun://0"))
    led.save()


def _client(cfg):
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def _videos(html: str):
    return re.findall(r"<video[^>]*>", html)


def _posters(html: str):
    return re.findall(r'<video[^>]*\bposter="([^"]+)"', html)


# ---- (1) poster attribute ----
def test_review_videos_all_have_nonempty_poster(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=3, state=PostState.awaiting_approval)
    html = _client(cfg).get("/review?view=list").data.decode()
    vids, posters = _videos(html), _posters(html)
    assert vids, "expected video elements on /review"
    assert len(posters) == len(vids), "every <video> must carry a poster="
    assert all(p.strip() for p in posters)
    assert all("/clip-thumb/" in p for p in posters)


def test_publish_scan_list_uses_lazy_thumbs_not_video_grid(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=3)
    html = _client(cfg).get("/publish").data.decode()
    assert _videos(html) == [], "publish uses scan-list thumbs, not per-card <video>"
    assert html.count("/clip-thumb/") >= 3
    assert 'loading="lazy"' in html


# ---- (2) cap / paginate ----
def test_review_grid_capped_with_visible_count(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, n_clips=views.GRID_PAGE_SIZE + 5, state=PostState.awaiting_approval)
    html = _client(cfg).get("/review").data.decode()
    cards = html.count('class="card clip-card"')
    assert cards <= views.GRID_PAGE_SIZE, "the grid must be capped to a page"
    assert str(views.GRID_PAGE_SIZE + 5) in html, "the TOTAL count must be visible (not silently truncated)"
    assert "show more" in html.lower()


def test_publish_grid_capped_with_visible_count(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, n_clips=views.GRID_PAGE_SIZE + 4)
    html = _client(cfg).get("/publish").data.decode()
    rows = html.count('class="publish-row"')
    assert rows <= views.GRID_PAGE_SIZE
    assert str(views.GRID_PAGE_SIZE + 4) in html
    assert "show more" in html.lower()


def test_review_show_more_offset_returns_remainder(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg)
    total = views.GRID_PAGE_SIZE + 6
    _seed(cfg, n_clips=total, state=PostState.awaiting_approval)
    html = _client(cfg).get(f"/review?view=list&offset={views.GRID_PAGE_SIZE}").data.decode()
    cards = html.count('class="card clip-card"')
    assert cards == total - views.GRID_PAGE_SIZE       # the remainder shows on the next page


def test_publish_show_more_offset_returns_remainder(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg)
    total = views.GRID_PAGE_SIZE + 3
    _seed(cfg, n_clips=total)
    html = _client(cfg).get(f"/publish?offset={views.GRID_PAGE_SIZE}").data.decode()
    rows = html.count('class="publish-row"')
    assert rows == total - views.GRID_PAGE_SIZE


def test_review_oversize_and_garbage_offset_never_500(tmp_path, monkeypatch):
    # a hand-typed offset beyond the total (or non-numeric) must clamp to an empty/first page, never 500.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=2)
    c = _client(cfg)
    assert c.get("/review?offset=9999").status_code == 200      # beyond total -> empty page, no crash
    assert c.get("/review?offset=-5").status_code == 200        # negative -> clamped to 0
    assert c.get("/review?offset=abc").status_code == 200       # garbage -> 0
    assert c.get("/publish?offset=9999").status_code == 200


# ---- (3) publish-now button on /publish ----
def test_publish_renders_publish_now_button_wired(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=1)
    html = _client(cfg).get("/publish").data.decode()
    assert "Publish now" in html
    assert "/publish/now/p0" in html
    assert "Mark posted" in html

def test_publish_hides_publish_now_when_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False); monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=1)
    html = _client(cfg).get("/publish").data.decode()
    assert "Publish now" not in html and "publish-guard" in html and "Mark posted" in html


def test_publish_now_button_dryrun_has_no_confirm_checkbox(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)   # dryrun
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=1)
    html = _client(cfg).get("/publish").data.decode()
    assert 'name="confirm"' not in html                  # no live confirm gate in dryrun


def test_publish_now_button_live_has_confirm_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n_clips=1)
    html = _client(cfg).get("/publish").data.decode()
    assert 'name="confirm"' in html                      # live backend -> the confirm checkbox gate appears
