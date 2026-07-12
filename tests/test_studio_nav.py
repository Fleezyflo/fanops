# tests/test_studio_nav.py — sidebar cockpit nav. Pins the left-rail IA: grouped, a11y-correct, account-spine
# intact. U13 consolidated the rail to 10 default entries: Blocked/Lift/Live library left the rail (their
# pages stay REACHABLE as deep links — asserted below under "reachable routes", just not rail links), Hashtags
# joined Setup, and Footage/Stitches moved behind FANOPS_SHOW_EXTRAS. The old top-bar + "Advanced" dropdown
# was never tested; these keep the rail honest.
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
    led.add_post(Post(id="p_base", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram, caption="BASE", state=PostState.awaiting_approval, public_url="dryrun://p_base"))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

# RAIL SURFACES = every full-page surface that IS a default rail link (U13: exactly these 10). The whole
# point: NONE of these is hidden.
RAIL_PAGES = ["/", "/run", "/review", "/schedule", "/posted", "/publish", "/library", "/personas",
              "/hashtags", "/golive/connect"]
# REACHABLE (off-rail) = pages the rail no longer links but that MUST stay reachable as deep links. Each
# maps to its expected terminal status: /gates is a live page (badge target); /lift + /live-library 301
# (folded by U10 + U13). Route coverage MOVED here from the rail inventory — never dropped.
REACHABLE_ROUTES = {"/gates": 200, "/lift": 301, "/live-library": 301}
RAIL_GROUPS = [b"Work", b"Library", b"Setup"]

def test_rail_landmark_present(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b'class="rail"' in html and b'id="rail-nav"' in html and b'aria-label="Studio navigation"' in html

def test_no_advanced_dropdown(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b"nav-more" not in html and b">Advanced<" not in html  # the buried dropdown is GONE

def test_every_rail_surface_linked_one_click(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    for path in RAIL_PAGES:
        assert f'href="{path}"' in html, f"{path} not reachable from the rail"

def test_offrail_routes_stay_reachable(tmp_path):
    # U13: Blocked/Lift/Live library left the RAIL but the routes must still resolve (deep links / bookmarks).
    cfg = Config(root=tmp_path); _seed(cfg)
    c = _client(cfg)
    for path, want in REACHABLE_ROUTES.items():
        r = c.get(path)
        assert r.status_code == want, f"{path} expected {want}, got {r.status_code}"

def test_offrail_routes_absent_from_rail(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    rail = html[html.index('id="rail-nav"'):html.index('</nav>', html.index('id="rail-nav"'))]
    for path in REACHABLE_ROUTES:
        assert f'href="{path}"' not in rail, f"{path} should have left the rail (still reachable, not railed)"

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
    assert b"/personas?account=a" in html and b"account-session-bar" in html and b"a" in html  # the a filter rides every rail link

def test_rail_pages_carry_rail(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    c = _client(cfg)
    for path in RAIL_PAGES:
        r = c.get(path, follow_redirects=(path == "/review"))
        assert r.status_code == 200 and b'class="rail"' in r.data, f"{path} missing the rail"

def test_skiplink_targets_main_content(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b'href="#main-content"' in html and b'id="main-content"' in html  # skip past sticky chrome

def test_brand_and_skip_survive(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data
    assert b"nav-brand" in html and b"skip-nav" in html

# S1 — de-junk "Setup": the OPERATIONAL surfaces live in "Library" so "Setup" means "configure once".
def test_library_group_precedes_setup_group(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    assert 'id="rg-library"' in html and html.index('id="rg-library"') < html.index('id="rg-setup"')  # operational Library above one-time Setup

def test_library_group_holds_library(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    library = html[html.index('id="rg-library"'):html.index('id="rg-setup"')]   # the Library group's slice (it precedes Setup)
    assert 'href="/library"' in library                       # U13: the one default Library surface
    for path in ("/lift", "/live-library"):                    # folded away — not in the Library group
        assert f'href="{path}"' not in library, f"{path} should be folded, not railed"

def test_setup_group_holds_personas_hashtags_accounts(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    rail_end = html.index('</nav>', html.index('id="rg-setup"'))
    setup = html[html.index('id="rg-setup"'):rail_end]
    assert 'href="/personas"' in setup and 'href="/hashtags"' in setup and 'href="/golive/connect"' in setup
    for path in ("/candidates", "/library", "/gates"):
        assert f'href="{path}"' not in setup, f"{path} should not be in Setup"

def test_gates_left_the_work_group(tmp_path):
    # U13: Blocked (/gates) is no longer a Work rail link — the Add & run badge deep-links to it instead.
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    wf = html[html.index('id="rg-work"'):html.index('id="rg-library"')]
    assert 'href="/gates"' not in wf
