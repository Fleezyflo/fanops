# tests/test_studio_nav.py — Slice 1 (sidebar cockpit nav). FIRST nav coverage: the old top-bar +
# "Advanced" dropdown was never tested (no test references nav-more/nav-primary/"Advanced"). These pin
# the new left-rail IA: every surface reachable in one click, grouped, a11y-correct, account-spine intact.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

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
    led.add_post(Post(id="p_base", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram, caption="BASE", state=PostState.awaiting_approval))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

# every full-page surface (route → label fragment in the rail). The whole point: NONE is hidden.
FULL_PAGES = ["/", "/run", "/review", "/publish", "/lift", "/posted", "/candidates", "/library",
              "/stitches", "/schedule", "/gates", "/personas", "/golive"]
RAIL_GROUPS = [b"Overview", b"Workflow", b"Insights", b"Setup"]

def test_rail_landmark_present(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b'class="rail"' in html and b'id="rail-nav"' in html and b'aria-label="Studio navigation"' in html

def test_no_advanced_dropdown(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b"nav-more" not in html and b">Advanced<" not in html  # the buried dropdown is GONE

def test_every_surface_linked_one_click(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    for path in FULL_PAGES:
        assert f'href="{path}"' in html, f"{path} not reachable from the rail"

def test_rail_groups_labelled(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    for g in RAIL_GROUPS:
        assert g in html, f"rail group {g!r} missing"

def test_active_link_has_aria_current(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/personas").data.decode()
    # the active rail link carries BOTH the .active class and aria-current=page (programmatic state, not colour-only)
    assert 'href="/personas" class="rail-link active" aria-current="page"' in html

def test_inactive_link_has_no_aria_current(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/personas").data.decode()
    assert 'href="/schedule" class="rail-link"' in html and 'aria-current="page"' in html  # exactly one active

def test_account_spine_threads_rail(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?account=@a").data
    assert b"/personas?account=@a" in html and b"Filtering" in html  # the @a filter rides every rail link (@ is RFC-legal in a query, left unencoded)

def test_full_pages_carry_rail(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    c = _client(cfg)
    for path in FULL_PAGES:
        r = c.get(path)
        assert r.status_code == 200 and b'class="rail"' in r.data, f"{path} missing the rail"

def test_skiplink_targets_main_content(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b'href="#main-content"' in html and b'id="main-content"' in html  # skip past sticky chrome

def test_brand_and_skip_survive(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b"nav-brand" in html and b"skip-nav" in html
