# C10 checklist gaps: schedule dialog must survive an htmx panel swap, and Posted
# "Show more" must keep the filters that built the page and must not link past the last page.
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parents[1] / "src/fanops/studio/static/schedule.js"
_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_schedule_dialog_listeners_survive_panel_swap():
    """#schedule-body is replaced by htmx outerHTML. A listener captured on that
    node is discarded with it, so Schedule… and calendar drag die after the first
    in-place update. Listeners stay on document and re-find the panel per event."""
    js = _JS.read_text()
    assert "body.addEventListener" not in js
    assert re.search(r"^const body = document\.getElementById\('schedule-body'\)", js, re.M) is None
    for ev in ("click", "submit", "dragstart", "dragover", "drop", "dragend"):
        assert f"document.addEventListener('{ev}'" in js
    assert "function panel()" in js
    assert "const body = panel()" in js


def test_posted_show_more_keeps_filters_and_stops_on_last_page(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import (
        Clip, ClipState, ErrorKind, Moment, MomentState, Platform, Post, PostState, Source,
    )
    from fanops.studio import views
    from fanops.studio.app import create_app

    real = views.paginate
    monkeypatch.setattr(views, "paginate", lambda rows, offset, page_size=24: real(rows, offset, page_size=2))
    cfg = Config(root=tmp_path)

    def _fn(led):
        led.add_source(Source(id="src_a", source_path="/a.mp4"))
        led.add_source(Source(id="src_b", source_path="/b.mp4"))
        led.add_moment(Moment(id="ma", parent_id="src_a", start=0, end=5, reason="a", state=MomentState.decided))
        led.add_moment(Moment(id="mb", parent_id="src_b", start=0, end=5, reason="b", state=MomentState.decided))
        led.add_clip(Clip(id="ca", parent_id="ma", path="/ca.mp4", state=ClipState.analyzed))
        led.add_clip(Clip(id="cb", parent_id="mb", path="/cb.mp4", state=ClipState.analyzed))
        for i, hours in enumerate((72, 48, 24), start=1):
            led.add_post(Post(
                id=f"fa{i}", parent_id="ca", account="a", account_id="ig1", platform=Platform.instagram,
                state=PostState.failed, caption=f"fail-{i}", error_kind=ErrorKind.rate_limit,
                scheduled_time=_z(_NOW - timedelta(hours=hours)),
            ))
        led.add_post(Post(
            id="fb", parent_id="cb", account="a", account_id="ig1", platform=Platform.instagram,
            state=PostState.failed, caption="other-source", error_kind=ErrorKind.rate_limit,
            scheduled_time=_z(_NOW - timedelta(hours=1)),
        ))

    with Ledger.transaction(cfg) as led:
        _fn(led)
    app = create_app(cfg)
    app.config.update(TESTING=True)
    client = app.test_client()
    q = "/posted?source=src_a&delivery=failed&failure=rate_limit"
    first = client.get(q)
    assert first.status_code == 200
    html = first.data.decode()
    assert "other-source" not in html
    m = re.search(r'href="([^"]+)">Show more', html)
    assert m, html
    href = m.group(1)
    assert "source=src_a" in href
    assert "delivery=failed" in href
    assert "failure=rate_limit" in href
    assert "offset=2" in href
    last = client.get(q + "&offset=2")
    assert last.status_code == 200
    last_html = last.data.decode()
    assert "fail-1" in last_html
    assert ">Show more<" not in last_html
