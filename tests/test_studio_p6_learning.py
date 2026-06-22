# tests/test_studio_p6_learning.py — UI Phase 6: expose the learning-loop levers + legibility.
# The A/B learning strategies (VARIANT_LEARNING + amplify / UCB / transfer) were env-only flags with no
# UI; the Library never showed filename/duration/resolution; the Gates transcript was mouse-only. This
# adds: four default-OFF Go-Live toggles (intent flags — the apply paths stay learning_validated-frozen),
# library legibility, and keyboard-operable transcript segments.
#
# Env isolation (the os.environ-leak guard): every learning flag golive dual-writes is delenv'd in _clean
# AND restored to baseline by the autouse fixture, so a toggle never leaks FANOPS_VARIANT_* into a later
# test (which would silently flip another suite's default-OFF assertions).
import os
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source
from fanops.studio import golive, views

_FLAGS = ("FANOPS_VARIANT_LEARNING", "FANOPS_VARIANT_AMPLIFY", "FANOPS_VARIANT_UCB", "FANOPS_VARIANT_TRANSFER")
_BASELINE = {k: os.environ.get(k) for k in _FLAGS}

@pytest.fixture(autouse=True)
def _restore_learning_env():
    yield
    for k, v in _BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _FLAGS:
        monkeypatch.delenv(k, raising=False)     # clean start + registers the key for teardown-restore
    return Config(root=tmp_path)

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# ── learning toggles: dual-write (.env durable + os.environ in-process), both directions ────────────
@pytest.mark.parametrize("setter,flag,prop", [
    ("set_variant_learning", "FANOPS_VARIANT_LEARNING", "variant_learning"),
    ("set_variant_amplify", "FANOPS_VARIANT_AMPLIFY", "variant_amplify"),
    ("set_variant_ucb", "FANOPS_VARIANT_UCB", "variant_ucb"),
    ("set_variant_transfer", "FANOPS_VARIANT_TRANSFER", "variant_transfer"),
])
def test_learning_toggle_dual_writes_both_directions(tmp_path, monkeypatch, setter, flag, prop):
    cfg = _clean(monkeypatch, tmp_path)
    assert getattr(cfg, prop) is False                       # default OFF
    assert getattr(golive, setter)(cfg, True).ok is True
    assert f"{flag}=1" in (tmp_path / ".env").read_text()    # durable
    assert getattr(cfg, prop) is True                        # in-process (reads os.environ live)
    assert getattr(golive, setter)(cfg, False).ok is True
    assert getattr(cfg, prop) is False                       # flipped back off


def test_golive_status_reflects_learning_flags(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    s0 = views.golive_status(cfg)
    assert (s0.variant_learning, s0.variant_amplify, s0.variant_ucb, s0.variant_transfer) == (False, False, False, False)
    golive.set_variant_learning(cfg, True); golive.set_variant_amplify(cfg, True)
    golive.set_variant_ucb(cfg, True); golive.set_variant_transfer(cfg, True)
    s1 = views.golive_status(cfg)
    assert (s1.variant_learning, s1.variant_amplify, s1.variant_ucb, s1.variant_transfer) == (True, True, True, True)


def test_golive_panel_renders_advanced_learning_toggles(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    html = _client(cfg).get("/golive").data
    assert b"Advanced learning" in html
    # one toggle form per learning strategy (url_for renders the route PATH, not the function name)
    assert b"/golive/learning" in html and b"/golive/amplify" in html
    assert b"/golive/ucb" in html and b"/golive/transfer" in html


# ── Library legibility: filename + duration + resolution ────────────────────────────────────────────
def test_library_panel_renders_filename_duration_resolution(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_native_1", source_path="/footage/showtime_raw.mp4", language="en",
                              origin_kind="native", duration=92.5, width=1920, height=1080))
    html = _client(cfg).get("/library").data
    assert b"showtime_raw.mp4" in html        # filename, not the opaque content id
    assert b"1920" in html and b"1080" in html  # resolution
    assert b"92" in html                       # duration seconds


# ── Gates: transcript segments are keyboard-operable (not mouse-only) ───────────────────────────────
def test_gates_transcript_segments_keyboard_operable(tmp_path, monkeypatch):
    from fanops.agentstep import write_request
    cfg = _clean(monkeypatch, tmp_path)
    write_request(cfg, kind="moments", key="s1", payload={
        "source_id": "s1", "duration": 10.0,
        "transcript": [{"start": 0.0, "end": 2.0, "text": "yo"}],
        "signal_peaks": [{"t": 1.0, "score": 0.9}], "language": "en"})
    html = _client(cfg).get("/gates").data.decode()
    # a .seg must be focusable + announce as an actionable control, so keyboard users get the click-to-fill
    assert 'class="seg"' in html
    assert 'tabindex="0"' in html and 'role="button"' in html
