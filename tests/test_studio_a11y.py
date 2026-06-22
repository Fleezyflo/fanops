# tests/test_studio_a11y.py — Phase 1 a11y baseline: skip-to-content link, a single page <h1> sourced
# from the title block, the brand demoted from <h1> to a link, and aria-live on the htmx swap targets.
import re
from fanops.config import Config
from fanops.studio.app import create_app


def _html(cfg, path="/"):
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get(path)
    assert r.status_code == 200, r.data[:300]
    return r.data.decode()


def test_skip_nav_link_targets_main_landmark(tmp_path):
    h = _html(Config(root=tmp_path))
    assert 'class="skip-nav' in h and 'href="#main"' in h     # skip link present...
    assert 'id="main"' in h                                   # ...and its target landmark exists


def test_brand_demoted_and_exactly_one_page_h1(tmp_path):
    h = _html(Config(root=tmp_path))
    assert '<h1 class="nav-brand"' not in h                   # brand is no longer the page heading
    assert 'class="nav-brand"' in h                           # but still rendered (as a link)
    assert h.count("<h1") == 1                                # exactly one <h1> per page (the title)


def test_aria_live_on_swap_targets(tmp_path):
    for path, ident in [("/review", "review-body"), ("/schedule", "schedule-body"), ("/run", "run-panel")]:
        h = _html(Config(root=tmp_path), path)
        assert re.search(r'id="%s"[^>]*aria-live="polite"' % ident, h), f"{ident} missing aria-live"
