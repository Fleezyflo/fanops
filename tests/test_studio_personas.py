# tests/test_studio_personas.py
# A2 — the Studio "Personas" page: personas become editable/addable/connectable IN THE BROWSER (no
# personas.json hand-edit). The action layer (fanops.studio.personas) wraps the A1 core writers and
# returns ActionResult (ok/error/detail), never raising into a 500; the read-model (views.personas_page)
# powers the page; the routes htmx-swap the panel. Mirrors the Go-Live action/route conventions.
import json
from fanops.config import Config
from fanops.accounts import Accounts
from fanops import personas as core
from fanops.studio import personas as sp
from fanops.studio import views


def _seed_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


# --- action layer ------------------------------------------------------------------------------

def test_create_persona_captures_genre_only(tmp_path):
    # Intake collapsed to its ONE functional field: genre seeds hashtag research. language / reference_accounts
    # / notes were inert (language is derived from the SOURCE transcript, not the persona; refs + notes fed
    # nothing) — they are removed, not merely hidden, so they can no longer be collected into a void.
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", tag_lean="tasteful", genre="hip hop")
    assert r.ok
    p = core.Personas.load(cfg).get(r.detail["created"])
    assert p.voice == "champions craft" and p.tag_lean == "tasteful"
    assert p.intake == {"genre": "hip hop"}        # genre only — no language / reference_accounts / notes keys


def test_create_persona_bad_lean_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="X", tag_lean="spicy")
    assert r.ok is False and r.error                  # no raise -> the panel renders the ✗


def test_create_persona_blank_name_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="   ")
    assert r.ok is False and r.error


def test_edit_persona_updates_fields(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="old", tag_lean="bold")
    r = sp.edit_persona(cfg, pid, name="Z2", voice="new", tag_lean="underground", genre="rap")
    assert r.ok
    p = core.Personas.load(cfg).get(pid)
    assert p.name == "Z2" and p.voice == "new" and p.tag_lean == "underground"
    assert p.intake == {"genre": "rap"}            # intake is genre-only now


def test_delete_persona_action(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Gone")
    assert sp.delete_persona(cfg, pid).ok
    assert core.Personas.load(cfg).get(pid) is None


def test_delete_unknown_persona_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = sp.delete_persona(cfg, "ghost")
    assert r.ok is False and r.error


def test_corpus_add_then_remove(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z")
    assert sp.add_corpus_tag(cfg, pid, "DetroitRap").ok
    assert core.Personas.load(cfg).get(pid).hashtag_corpus == ["#detroitrap"]
    assert sp.remove_corpus_tag(cfg, pid, "#detroitrap").ok
    assert core.Personas.load(cfg).get(pid).hashtag_corpus == []


def test_connect_account_links_persona(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    pid = core.add_persona(cfg, name="P1", voice="v1", tag_lean="tasteful")
    r = sp.connect_account(cfg, "@a", pid)
    assert r.ok
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["persona_id"] == pid
    # and the link hydrates the account on next load
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "v1" and a.tag_lean == "tasteful"


def test_connect_unknown_account_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    pid = core.add_persona(cfg, name="P1")
    r = sp.connect_account(cfg, "@nope", pid)
    assert r.ok is False and r.error


def test_connect_unknown_persona_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    r = sp.connect_account(cfg, "@a", "ghost")          # refuse linking to a persona that doesn't exist
    assert r.ok is False and r.error


def test_disconnect_account_with_blank(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}])
    assert sp.connect_account(cfg, "@a", "").ok          # blank persona_id clears the link
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0].get("persona_id") in (None, "")


def test_run_migration_action(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@mark", "platforms": ["instagram"], "status": "active",
                          "persona": "music blogger", "tag_lean": "tasteful"}])
    r = sp.run_migration(cfg)
    assert r.ok and r.detail["created"] == ["mark"]
    assert Accounts.load(cfg).accounts[0].persona_id == "mark"


# --- read-model --------------------------------------------------------------------------------

def test_personas_page_read_model(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1", tag_lean="bold")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid},
                         {"handle": "@b", "platforms": ["instagram"], "status": "active"}])
    page = views.personas_page(cfg)
    card = next(c for c in page.personas if c.id == pid)
    assert card.voice == "v1" and card.corpus == ["#detroitrap"] and card.linked_handles == ["@a"]
    links = {lk.handle: lk.persona_id for lk in page.accounts}
    assert links["@a"] == pid and links["@b"] is None


def test_personas_page_failopen_on_corrupt(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text("{ not json")
    page = views.personas_page(cfg)                      # corrupt file -> empty page, never 500
    assert page.personas == [] and page.accounts == []


# --- routes ------------------------------------------------------------------------------------

def test_personas_route_renders(tmp_path):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="champions craft")
    r = _client(cfg).get("/personas")
    assert r.status_code == 200 and b"Curator" in r.data


def test_persona_forms_drop_dead_intake_fields(tmp_path):
    # The inert intake inputs (language / reference accounts / notes) are gone from BOTH the add form and the
    # edit drawer; only the functional Genre field remains. language is source-derived; refs + notes fed nothing.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    add_form = _client(cfg).get("/personas").get_data(as_text=True)
    drawer = _client(cfg).get(f"/personas/drawer/{pid}").get_data(as_text=True)
    for body in (add_form, drawer):
        assert 'name="genre"' in body              # the one functional intake field stays
        assert 'name="language"' not in body       # inert (source-derived) — removed
        assert 'name="refs"' not in body           # reference accounts — removed
        assert 'name="notes"' not in body          # memo nothing read — removed


def test_post_add_persona_route(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/personas/add", data={"name": "New One", "voice": "a voice", "tag_lean": "bold"})
    assert r.status_code == 200
    assert any(p.name == "New One" for p in core.Personas.load(cfg).all())


def test_corpus_tag_with_quote_is_json_escaped_in_attribute(tmp_path):
    # _norm keeps a double-quote (a hand-edit could land one); the hx-vals attribute must JSON-escape it
    # via tojson so a crafted tag can NEVER break out of the attribute (defense-in-depth — ecc:python-review).
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, '#a"b')
    r = _client(cfg).get("/personas")
    assert r.status_code == 200
    assert b'#a\\"b' in r.data                  # tojson escaped the inner quote
    assert b'"tag": "#a"b"' not in r.data        # the raw, un-escaped (breakout) form must NOT appear


def test_post_connect_route_links(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    pid = core.add_persona(cfg, name="P1", voice="v1")
    r = _client(cfg).post("/personas/connect", data={"handle": "@a", "persona_id": pid})
    assert r.status_code == 200
    assert Accounts.load(cfg).accounts[0].persona_id == pid


def test_account_assignment_is_folded_into_each_card(tmp_path):
    # Clarity: account assignment lives WITH the voice it drives — a driven handle is one click to unlink and
    # an unassigned account is offered in the card's assign dropdown. The orphan page-foot "Connect accounts"
    # dropdown stack is gone (it duplicated the card head's "drives" and forced an account-centric mental model).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@linked", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@free", "platforms": ["tiktok"], "status": "active"}])
    pid = core.add_persona(cfg, name="Curator", voice="champions craft")
    sp.connect_account(cfg, "@linked", pid)
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert "persona-accounts" in html and "@linked" in html          # the driven handle shows on the card
    assert "persona-assign" in html and "@free" in html              # the unassigned account is assignable inline
    assert "Connect accounts" not in html                            # the orphan page-foot section is removed
