# tests/test_effective_persona.py — S05: drawer-only "Effective persona" read projection.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.accounts import link_persona
from fanops import personas as core
from fanops.studio import views
from fanops.persona_directives import casting_directive, hook_directive, caption_directive
from fanops.studio.app import _LEVER_EFFECTS


def _seed_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_persona_card_directives_match_compiler_verbatim(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="devoted fan", content_focus=["punchlines"],
                           selection_scope="controversy_seeking", hook_angle="curiosity")
    p = core.Personas.load(cfg).get(pid)
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.instruction == str(casting_directive(p))
    assert card.hook_text == str(hook_directive(p))
    assert card.caption_text == caption_directive(p)


def test_lever_detail_rows_joins_option_effect(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", content_focus=["punchlines"], hook_angle="curiosity")
    p = core.Personas.load(cfg).get(pid)
    from fanops.personas import lever_catalog, manifest
    cat = lever_catalog()
    rows = views._lever_detail_rows(cfg, p, manifest(cfg, p), cat, _LEVER_EFFECTS)
    hook_row = next(r for r in rows if r["key"] == "hook_angle")
    assert hook_row["option_effect"] == _LEVER_EFFECTS["hook_angle"]["curiosity"]
    foc_row = next(r for r in rows if r["key"] == "content_focus")
    assert _LEVER_EFFECTS["content_focus"]["punchlines"] in foc_row["option_effect"]


def test_account_provenance_persona_derived_clip_profile(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}])
    pid = core.add_persona(cfg, name="P", voice="v", content_focus=["punchlines"])
    link_persona(cfg, "@a", pid)
    p = core.Personas.load(cfg).get(pid)
    prov = views._account_provenance(cfg, p, ["a"])
    prof = next(f for f in prov[0]["fields"] if f["name"] == "clip_profile")
    assert prof["source"] == "persona" and prof["value"] == "short"


def test_account_provenance_account_only_clip_profile(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "clip_profile": "long"}])
    pid = core.add_persona(cfg, name="P", voice="v")
    link_persona(cfg, "@a", pid)
    p = core.Personas.load(cfg).get(pid)
    prov = views._account_provenance(cfg, p, ["a"])
    prof = next(f for f in prov[0]["fields"] if f["name"] == "clip_profile")
    assert prof["source"] == "account" and prof["value"] == "long"


def test_drawer_unlinked_persona_shows_drives_no_accounts(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Lonely", voice="x")
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert "Drives no accounts" in html


def test_drawer_failopen_when_provenance_raises(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="x")

    def _boom(*a, **k):
        raise RuntimeError("prov fail")

    monkeypatch.setattr(views, "_account_provenance", _boom)
    r = _client(cfg).get(f"/personas/drawer/{pid}")
    assert r.status_code == 200
    html = r.data.decode()
    assert 'class="effective-persona"' in html


def test_drawer_smoke_effective_persona_section(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="devoted fan", content_focus=["punchlines"], hook_angle="curiosity")
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert 'class="effective-persona"' in html
    assert "<pre" in html and "Pick" in html
    assert "lever-detail" in html or "effective-levers" in html
