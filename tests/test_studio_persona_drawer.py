# tests/test_studio_persona_drawer.py — Slice 3: the persona editor as a slide-out DRAWER.
# The levers move OUT of a buried per-card collapsed <details>Edit</details> into a focus-trapped modal
# drawer (role=dialog) so a persona's levers are always one click away and visible. The drawer body is an
# htmx fragment swapped into a body-level #persona-drawer mount; Save/Delete reuse the proven /personas/edit
# + /personas/delete routes (re-render #personas-panel); drawer.js owns focus-trap/ESC/inert/focus-return.
import pytest
pytest.importorskip("flask")
import json
from fanops.config import Config
from fanops import personas as core


def _seed_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_panel_edit_is_a_drawer_trigger_not_nested_details(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Curator", voice="champions craft", tag_lean="tasteful")
    html = _client(cfg).get("/personas").data.decode()
    # the card's Edit is now an htmx drawer trigger — never a second nested expand
    assert f'/personas/drawer/{pid}' in html
    assert 'hx-target="#persona-drawer"' in html
    assert '<details class="persona-edit">' not in html and '<summary>Edit</summary>' not in html


def test_personas_page_mounts_drawer_backdrop_and_js(tmp_path):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="x")
    html = _client(cfg).get("/personas").data.decode()
    assert 'id="persona-drawer"' in html                 # the slide-out mount (body-level, outside .workspace)
    assert 'drawer-backdrop' in html                     # the click-to-dismiss scrim
    assert 'drawer.js' in html                           # the a11y focus-trap/ESC/inert script


def test_drawer_route_renders_levers_visible(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Curator", voice="champions craft", tag_lean="tasteful")
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert 'role="dialog"' in html and 'aria-modal="true"' in html
    assert 'id="persona-drawer-heading"' in html         # the labelled, focusable heading drawer.js focuses
    # the levers are PRESENT and not gated behind a second <details> collapse
    assert 'name="content_focus"' in html and 'name="energy"' in html and 'name="clip_profile"' in html
    assert '<details' not in html                         # nothing in the drawer is hidden behind an expand
    assert "Curator" in html


def test_drawer_route_has_compose_preview_mount(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Curator", voice="x")
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert f'id="persona-compose-{pid}"' in html          # the live "compiles to" preview target
    assert '/personas/compose' in html                    # the levers post to the compose preview


def test_drawer_save_targets_panel_and_has_close_and_delete(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Curator", voice="x")
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert '/personas/edit' in html and 'hx-target="#personas-panel"' in html   # Save reuses the proven path
    assert '/personas/delete' in html                     # Delete present
    assert 'data-drawer-close' in html                    # an explicit Close affordance drawer.js wires


def test_drawer_unknown_persona_is_clean_not_500(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).get("/personas/drawer/does-not-exist")
    assert r.status_code == 200                            # fail-open: a clean fragment, never a 500
    html = r.data.decode()
    # the not-found body is STILL a proper modal dialog (drawer.js opens + focus-traps it) — never a role-less region
    assert 'role="dialog"' in html and 'aria-modal="true"' in html and "not found" in html.lower()


def test_drawer_edit_persists_via_existing_route(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="old", tag_lean="bold")
    r = _client(cfg).post("/personas/edit", data={
        "id": pid, "name": "Z2", "voice": "new voice", "tag_lean": "underground",
        "genre": "rap", "language": "ar", "refs": "", "notes": ""})
    assert r.status_code == 200
    p = core.Personas.load(cfg).get(pid)
    assert p.name == "Z2" and p.voice == "new voice" and p.tag_lean == "underground"


def test_persona_with_no_levers_still_renders_drawer(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Bare")               # no levers set
    html = _client(cfg).get(f"/personas/drawer/{pid}").data.decode()
    assert 'role="dialog"' in html and f'id="persona-compose-{pid}"' in html   # renders, compose mount present
    assert "Bare" in html


def test_compose_panel_shows_produces_prose(tmp_path):
    # S7: the live "compiles to" panel leads with a plain "Produces: …" sentence so the operator reads the
    # OUTPUT (length/framing/hook/hashtags), not just the engineer-facing directive rows.
    cfg = Config(root=tmp_path)
    html = _client(cfg).post("/personas/compose", data={
        "voice": "a devoted fan", "clip_profile": "short", "framing": "top",
        "hook_angle": "curiosity", "tag_lean": "tasteful"}).get_data(as_text=True)
    assert "produces-line" in html                       # the styled lead sentence is rendered
    assert "curiosity hooks" in html and "top-framed" in html

def test_compose_panel_empty_levers_keeps_the_grid_no_produces_line(tmp_path):
    # an unconfigured persona still gets the live panel (the affordance), just no Produces lead (nothing to say).
    cfg = Config(root=tmp_path)
    html = _client(cfg).post("/personas/compose", data={"voice": "v"}).get_data(as_text=True)
    assert "compose-grid" in html                         # the panel still renders
    assert "produces-line" not in html                    # but no produces sentence when no dimension is set


def test_drawer_lever_fields_persist_via_edit(tmp_path):
    # the WHOLE POINT of the drawer is surfacing the levers — prove a full lever submission round-trips and saves.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Lever Test")
    r = _client(cfg).post("/personas/edit", data={
        "id": pid, "name": "Lever Test", "voice": "v", "tag_lean": "",
        "genre": "", "language": "", "refs": "", "notes": "",
        "content_focus": ["storytelling", "emotional"], "energy": "low",
        "clip_profile": "long", "framing": "center", "hook_angle": "curiosity", "hook_tone": "restrained"})
    assert r.status_code == 200
    p = core.Personas.load(cfg).get(pid)
    assert set(p.content_focus) == {"storytelling", "emotional"}
    assert p.energy == "low" and p.clip_profile == "long" and p.framing == "center"
    assert p.hook_angle == "curiosity" and p.hook_tone == "restrained"
