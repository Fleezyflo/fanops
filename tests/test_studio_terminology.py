# tests/test_studio_terminology.py — S9: the seven insider terms that drive the IA (moment, cast, lever, batch,
# surface, variant, integration) are defined inline, ONCE at first use per surface, via a native keyboard-
# accessible <details> disclosure (no JS). views.TERM_DEFS is the frozen source of truth; term_def() is fail-soft;
# the _term.html macro renders it; term_def is a Jinja GLOBAL (jinja_env.globals) so the context-isolated
# {% from %} macro can resolve it. cast and variant ride OFF-gated blocks, so they vanish when casting /
# creative_variation are off.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.studio import views


def _accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _active(handle="a"):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active"}


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


# ── TERM_DEFS / term_def: the frozen glossary, fail-soft ───────────────────────────────────────────
def test_term_defs_covers_the_seven_terms():
    assert set(views.TERM_DEFS) == {"moment", "cast", "lever", "batch", "surface", "variant", "integration"}
    assert all(isinstance(v, str) and v for v in views.TERM_DEFS.values())   # every def is non-empty prose


def test_term_def_returns_the_canonical_definition():
    assert views.term_def("moment") == "a worth-clipping window in the source video"
    assert views.term_def("integration") == "the Postiz channel a handle+platform publishes through"


def test_term_def_is_fail_soft_on_unknown_or_nonstring():
    assert views.term_def("not-a-term") is None     # unknown key -> None, never KeyError
    assert views.term_def(None) is None             # non-string -> None, never raises
    assert views.term_def(123) is None


# ── the macro: a focusable inline PHRASING element, keyboard-accessible, NO JavaScript ─────────────
def test_term_macro_renders_keyboard_accessible_span(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert '<span class="term" data-term="lever"' in html       # PHRASING content (a <details> here is flow
                                                                # content and the parser ejects it, tearing the line)
    assert 'tabindex="0"' in html and "term-def" in html        # focusable (keyboard/click), reveals the definition
    assert "<details class=\"term\"" not in html                # never the flow-content disclosure
    assert "ⓘ" in html                                          # the inline marker


# ── placed ONCE at first use per surface ───────────────────────────────────────────────────────────
def test_personas_defines_lever_once(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert html.count('data-term="lever"') == 1


def test_gates_defines_moment_once(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/gates").get_data(as_text=True)
    assert html.count('data-term="moment"') == 1               # the intro defines it, once


def test_review_defines_moment_and_surface_at_most_once(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/review").get_data(as_text=True)
    assert html.count('data-term="moment"') == 1
    assert html.count('data-term="surface"') == 1


def test_home_defines_batch_once(tmp_path):
    from fanops.batches import create_batch
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    led = Ledger.load(cfg)
    create_batch(led, name="B1", target_accounts=["a"], now_iso="2026-06-22T00:00:00.000001Z"); led.save()
    html = _client(cfg).get("/").get_data(as_text=True)
    assert html.count('data-term="batch"') == 1


# ── cast / variant ride OFF-gated blocks: absent under OFF, present under ON ────────────────────────
def test_cast_and_variant_absent_when_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0"); monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/review").get_data(as_text=True)
    assert 'data-term="cast"' not in html       # casting OFF -> the cast-state explainer (and its term) is gone
    assert 'data-term="variant"' not in html    # creative_variation OFF -> the variant explainer is gone


def test_cast_and_variant_present_when_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1"); monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/review").get_data(as_text=True)
    assert html.count('data-term="cast"') == 1
    assert html.count('data-term="variant"') == 1


# ── no surface 500s with the glossary wired in ─────────────────────────────────────────────────────
def test_all_surfaces_200_with_terms(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    c = _client(cfg)
    for path in ("/", "/gates", "/review", "/personas", "/golive", "/schedule", "/posted", "/lift"):
        assert c.get(path).status_code == 200, path
