# tests/test_nav_consolidation.py — U13 nav consolidation. Pins the 10-entry rail (Blocked/Lift/Live
# library left the rail; Hashtags joined Setup), the FANOPS_SHOW_EXTRAS gate on Footage+Stitches, the
# /live-library -> /library?view=live 301 fold (with the wipe POSTs still rendering on the folded surface),
# and the Add & run blocked-gates badge. Deep-link routes (/gates 200, /lift 301) stay reachable off-rail.
import json
import re
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt,
                           SourceState, ImportedMedia)
from fanops.agentstep import write_request
from fanops.studio import views


def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_base", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram, caption="BASE", state=PostState.awaiting_approval, public_url="dryrun://p_base"))
    led.save()


def _seed_caption_gate(cfg, *, sid="src_g", clip_id="clip_g"):
    # The canonical pending-gate seed (mirrors tests/test_pipeline_status.py): a moments_decided source with
    # a captions_requested clip + a written caption request -> pending_captions -> blocked_gates > 0.
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path="/g.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="mg", parent_id=sid, state=MomentState.decided,
                              content_token="tok", start=0.0, end=5.0, reason="pick"))
        led.add_clip(Clip(id=clip_id, parent_id="mg", state=ClipState.captions_requested,
                          path="/g/clip.mp4", duration=5.0))
    write_request(cfg, kind="captions", key=clip_id, payload={"clip_id": clip_id})


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# The 10 default rail entries, in target order: (href, label). Footage+Stitches are extras (behind SHOW_EXTRAS).
DEFAULT_RAIL = [
    ("/", "Home"), ("/run", "Add &amp; run"), ("/review", "Review"), ("/schedule", "Schedule"),
    ("/posted", "Results"), ("/publish", "Manual publish"),
    ("/library", "Library"),
    ("/personas", "Personas"), ("/hashtags", "Hashtags"), ("/golive/connect", "Go Live"),
]


def _rail_links(html):
    # every rail-link anchor's (href, inner text) in document order — the rail is the only place rail-link is used.
    return [(m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
            for m in re.finditer(r'<a href="([^"]+)" class="rail-link[^"]*"[^>]*>(.*?)</a>', html, re.S)]


def test_default_rail_is_ten_entries_in_target_order(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    links = _rail_links(html)
    assert len(links) == 10, f"expected 10 rail links, got {len(links)}: {links}"
    # href + label match, in order. got_label keeps HTML entities (&amp;) and drops the badge <span> (the
    # _rail_links regex strips inner tags), so a startswith on the escaped label ignores the Add&run chip.
    for (got_href, got_label), (want_href, want_label) in zip(links, DEFAULT_RAIL):
        assert got_href == want_href, f"order/href mismatch: {links}"
        assert got_label.startswith(want_label), f"label mismatch at {want_href}: {got_label!r} !~ {want_label!r}"


def test_blocked_lift_livelibrary_left_the_rail(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    hrefs = [h for h, _ in _rail_links(html)]
    assert "/gates" not in hrefs          # Blocked link removed (page still live; badge deep-links to it)
    assert "/lift" not in hrefs           # Lift folded into Results by U10 — no rail link
    assert "/live-library" not in hrefs   # Live library folded into /library?view=live


def test_show_extras_reveals_footage_and_stitches(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SHOW_EXTRAS", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    links = _rail_links(html)
    hrefs = [h for h, _ in links]
    assert len(links) == 12, f"SHOW_EXTRAS should add Footage+Stitches -> 12, got {len(links)}"
    assert "/candidates" in hrefs and "/stitches" in hrefs   # the two extras appear under Library


def test_extras_hidden_by_default(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)      # FANOPS_SHOW_EXTRAS unset (conftest strips it) -> default OFF
    html = _client(cfg).get("/").data.decode()
    hrefs = [h for h, _ in _rail_links(html)]
    assert "/candidates" not in hrefs and "/stitches" not in hrefs


def test_show_extras_config_default_off(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    assert cfg.show_extras is False                       # unset -> OFF
    monkeypatch.setenv("FANOPS_SHOW_EXTRAS", "1")
    assert cfg.show_extras is True
    monkeypatch.setenv("FANOPS_SHOW_EXTRAS", "off")
    assert cfg.show_extras is False                       # explicit off-word


# ---- deep-link routes stay reachable off-rail ----
def test_live_library_301s_to_library_live_lens(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).get("/live-library")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/library?view=live") or "/library?view=live" in r.headers["Location"]


def test_live_library_301_preserves_account(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).get("/live-library?account=@a")
    assert r.status_code == 301
    loc = r.headers["Location"]
    assert "view=live" in loc and "account=%40a" in loc.replace("@", "%40")


def test_gates_page_still_200(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).get("/gates")
    assert r.status_code == 200            # the /gates deep link (badge target) is intact


def test_lift_still_301(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).get("/lift")
    assert r.status_code == 301            # U10's redirect is untouched


# ---- Add & run blocked-gates badge ----
def test_badge_absent_when_no_blocked_gates(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert views.build_system_strip(cfg)["blocked_gates"] == 0
    html = _client(cfg).get("/").data.decode()
    assert 'data-testid="rail-badge"' not in html      # no chip when nothing is blocked


def test_badge_present_with_count_when_gates_blocked(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg); _seed_caption_gate(cfg)
    n = views.build_system_strip(cfg)["blocked_gates"]
    assert n > 0
    html = _client(cfg).get("/").data.decode()
    # the badge renders on the Add & run link with the blocked count
    m = re.search(r'<a href="/run"[^>]*class="rail-link[^"]*"[^>]*>Add &amp; run<span class="rail-badge"[^>]*>(\d+)</span></a>', html)
    assert m, f"badge not found on Add & run link; blocked={n}"
    assert int(m.group(1)) == n


def test_badge_links_to_gates_via_system_strip(tmp_path):
    # The blocked count deep-links to /gates from the system strip (the badge's semantic target).
    cfg = Config(root=tmp_path); _seed(cfg); _seed_caption_gate(cfg)
    html = _client(cfg).get("/").data.decode()
    assert 'href="/gates"' in html                     # the strip alert links to the (unrailed) gates page


# ---- folded live lens content parity ----
def test_library_live_lens_matches_live_library_read_model(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_imported_media(ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS",
                                             account="ig-777", metrics={"reach": 1200}))
        led.add_imported_media(ImportedMedia(media_id="M2", permalink="https://ig/p/B/", product_type="FEED"))
    want = {r.media_id for r in views.live_library(Ledger.load(cfg), cfg)}
    html = _client(cfg).get("/library?view=live").data.decode()
    # the folded lens renders the same live media set (permalinks are the visible identity)
    assert "https://ig/reel/A/" in html and "https://ig/p/B/" in html
    assert want == {"M1", "M2"}
    # and the "viewed there, not authored here" framing carried over from the retired page
    assert "not authored here" in html.lower()


def test_library_default_lens_is_asset_catalog(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/library").data.decode()
    assert "Asset library" in html                     # default (no ?view=) is the unchanged catalog
    assert 'data-testid="lens-live"' in html           # the lens switcher is present


# ---- wipe POSTs survive the fold (no dead-template error) ----
def test_wipe_preview_renders_on_folded_surface(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_imported_media(ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS"))
    r = _client(cfg).post("/live-library/wipe/preview")
    assert r.status_code == 200                        # the wipe POST still 200s (renders library.html, not the deleted page)
    body = r.get_data(as_text=True)
    assert "wipe-panel" in body                        # the #wipe-panel fragment htmx swaps is present


def test_wipe_confirm_renders_on_folded_surface(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_imported_media(ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS"))
    # a bare confirm (no valid token) must still render — the gate rejects it, it does NOT 500 on a missing template
    r = _client(cfg).post("/live-library/wipe/confirm", data={"confirm_text": "REMOVE"})
    assert r.status_code == 200
    assert "wipe-panel" in r.get_data(as_text=True)
