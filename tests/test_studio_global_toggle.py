# tests/test_studio_global_toggle.py — Face 4 spine: ONE account filter governs every tab. The active
# ?account= is injected globally (context_processor) so base.html's nav links carry it across tabs
# (cross-tab persistence) and the header shows a clearable "Filtering @x" indicator. Implemented as nav-level
# propagation (NOT a chip-row relocation) so the existing per-surface chip rows + R1 htmx-swap scope
# preservation + live counts are untouched. None (no filter) -> url_for drops the param -> byte-identical nav.
import json
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in ("@a", "@b")]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="aw_a", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()

flask = pytest.importorskip("flask")

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def test_nav_links_carry_account_when_filtered(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?account=@a").data
    # the primary nav links carry the filter so switching tabs preserves the account scope (cross-tab spine)
    assert b"/schedule?account=" in html and b"/publish?account=" in html and b"/posted?account=" in html
    assert b"account-session-bar" in html and b"@a" in html and b"Clear filter" in html       # account session bar shows scope + clear

def test_nav_links_bare_when_unfiltered(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review").data
    assert b'href="/schedule"' in html                     # nav link bare -> byte-identical when nothing filtered
    assert b"account-session-bar" not in html                       # no session bar with no active filter

@pytest.mark.parametrize("path", ["/review", "/schedule", "/posted", "/publish", "/lift", "/run"])
def test_spine_indicator_on_every_tab(tmp_path, path):
    # the global filter is visible (and recoverable) from EVERY tab — even tabs that don't themselves filter,
    # so the operator always sees + can clear the active account selection.
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get(path + "?account=@a").data
    assert html and b"account-session-bar" in html and b"@a" in html and b"Clear filter" in html

def test_clear_link_targets_current_path(tmp_path):
    # "Clear" drops the filter on the CURRENT tab (request.endpoint, no query) -> always recoverable.
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/schedule?account=@a").data.decode()
    assert 'href="/schedule"' in html                      # clear link returns to the unfiltered current tab
