# tests/test_studio_bulk_disabled.py — MOL-52: bulk-action buttons must reflect selection state.
# Stitches: the Approve/Release bulk buttons render `disabled` when their list is EMPTY (and enabled
# when non-empty). Review: the server-rendered action dock ships Approve/Reject `disabled` (0 selected
# on first paint, before review.js runs) — the JS re-enables on the first tick.
import re
import pytest
pytest.importorskip("flask")   # Studio is the optional [studio] extra
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import StitchPlan, Clip, ClipState, Fmt


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _btn(html, label):
    # the <button ...>label</button> whose text is `label` — return its opening tag for attribute checks
    m = re.search(r'<button\b([^>]*)>\s*' + re.escape(label), html)
    assert m, f"button labelled {label!r} not found in html"
    return m.group(1)


# ── Stitches: empty list → bulk button disabled ────────────────────────────────────────────────────
def test_stitches_empty_lists_disable_both_bulk_buttons(tmp_path):
    # a fresh ledger has neither suggestions nor drafts → both Approve and Release render disabled
    h = _client(Config(root=tmp_path)).get("/stitches").data.decode()
    assert "No suggestions awaiting approval" in h and "No drafts awaiting release" in h
    assert "disabled" in _btn(h, "Approve selected"), "Approve selected must be disabled on an empty suggestion list"
    assert "disabled" in _btn(h, "Release selected"), "Release selected must be disabled on an empty draft list"


def test_stitches_nonempty_suggestions_enable_approve(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_stitch_plan(StitchPlan(id="sp1", clip_id="clip_1", strategy_key="impact_cut"))
    h = _client(cfg).get("/stitches").data.decode()
    assert "disabled" not in _btn(h, "Approve selected"), "Approve must NOT be disabled when suggestions exist"
    # no drafts still present → Release stays disabled (independent lists)
    assert "disabled" in _btn(h, "Release selected"), "Release stays disabled when no drafts exist"


def test_stitches_nonempty_drafts_enable_release(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.clips["stitch_x"] = Clip(id="stitch_x", parent_id="m1", path="/x/stitch_x.mp4",
                                     aspect=Fmt.r9x16, state=ClipState.stitch_draft)
    h = _client(cfg).get("/stitches").data.decode()
    assert "disabled" not in _btn(h, "Release selected"), "Release must NOT be disabled when drafts exist"
    assert "disabled" in _btn(h, "Approve selected"), "Approve stays disabled when no suggestions exist"


# ── Review: server-rendered dock ships Approve/Reject disabled (0 selected on load) ─────────────────
def test_review_dock_bulk_buttons_disabled_on_first_paint(tmp_path):
    h = _client(Config(root=tmp_path)).get("/review").data.decode()
    assert "disabled" in _btn(h, "Approve selected"), "Review Approve must render disabled (0 selected on load)"
    assert "disabled" in _btn(h, "Reject selected"), "Review Reject must render disabled (0 selected on load)"
    # the select helpers (Select page / Clear) must NOT be disabled — they act on the list, not selection
    assert "disabled" not in _btn(h, "Select page"), "Select page helper must stay enabled"
