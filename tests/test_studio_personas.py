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

def test_create_persona_is_the_clean_lever_set(tmp_path):
    # create takes the five clean levers only (voice + content_focus/selection_scope/hook_angle); tag_lean and genre are
    # NOT create params — hashtags come from the card corpus, genre is set via the Research control.
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", content_focus=["punchlines"],
                          selection_scope="controversy_seeking", hook_angle="curiosity")
    assert r.ok
    p = core.Personas.load(cfg).get(r.detail["created"])
    assert p.voice == "champions craft" and p.content_focus == ["punchlines"] and p.hook_angle == "curiosity"
    assert p.intake == {}                          # genre is set later via Research, not collected at create


def test_create_persona_blank_name_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="   ")
    assert r.ok is False and r.error


def test_edit_persona_updates_fields(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z", voice="old")
    r = sp.edit_persona(cfg, pid, name="Z2", voice="new", content_focus=["hype"], hook_angle="fomo")
    assert r.ok
    p = core.Personas.load(cfg).get(pid)
    assert p.name == "Z2" and p.voice == "new" and p.content_focus == ["hype"] and p.hook_angle == "fomo"


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
    pid = core.add_persona(cfg, name="P1", voice="v1")
    r = sp.connect_account(cfg, "a", pid)
    assert r.ok
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["persona_id"] == pid
    # and the link hydrates the account on next load
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "v1"


def test_connect_unknown_account_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    pid = core.add_persona(cfg, name="P1")
    r = sp.connect_account(cfg, "nope", pid)
    assert r.ok is False and r.error


def test_connect_unknown_persona_is_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    r = sp.connect_account(cfg, "a", "ghost")          # refuse linking to a persona that doesn't exist
    assert r.ok is False and r.error


def test_disconnect_account_with_blank(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}])
    assert sp.connect_account(cfg, "a", "").ok          # blank persona_id clears the link
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0].get("persona_id") in (None, "")


def test_run_migration_action(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@mark", "platforms": ["instagram"], "status": "active",
                          "persona": "music blogger"}])
    r = sp.run_migration(cfg)
    assert r.ok and r.detail["created"] == ["mark"]
    assert Accounts.load(cfg).accounts[0].persona_id == "mark"


# --- read-model --------------------------------------------------------------------------------

def test_personas_page_read_model(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid},
                         {"handle": "@b", "platforms": ["instagram"], "status": "active"}])
    page = views.personas_page(cfg)
    card = next(c for c in page.personas if c.id == pid)
    assert card.voice == "v1" and card.corpus == ["#detroitrap"] and card.linked_handles == ["a"]
    links = {lk.handle: lk.persona_id for lk in page.accounts}
    assert links["a"] == pid and links["b"] is None


def test_personas_page_surfaces_live_graph_reach(tmp_path):
    # WS5 / MOL-59: the card's reach annotation + ★ come from the LIVE Graph-reach store (refresh_store), NOT
    # own-post reach. A curated tag with a MEASURED Graph reach value is flagged most-active + shows the number.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#detroitrap", "#hiphop"], "reach": {"#detroitrap": 4200}}))
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert "#detroitrap" in card.reach_tags                 # measured reach -> flagged most-active (★)
    assert card.reach_means.get("#detroitrap") == 4200.0    # the LIVE Graph reach, surfaced honestly


def test_personas_page_star_gated_on_measured_reach_not_store_presence(tmp_path):
    # MOL-59: a tag merely PRESENT in the store (a seed) with an EMPTY reach map is NOT most-active — the ★
    # asserts a live-reach fact, so with zero measurements ZERO tags star. This is today's live state.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#detroitrap", "#hiphop"], "reach": {}}))
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == []                            # store-present but unmeasured -> no ★
    # and the rendered panel shows no star for it
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert "#detroitrap" in html and "★" not in html


def test_personas_page_star_only_on_measured_tags(tmp_path):
    # MOL-59: with reach covering SOME corpus tags, ONLY the measured ones star.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    core.add_corpus_tag(cfg, pid, "#hiphop")
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#detroitrap", "#hiphop"], "reach": {"#detroitrap": 4200}}))
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == ["#detroitrap"]               # measured only; #hiphop present-in-store but unmeasured


def test_personas_page_no_store_no_stars(tmp_path):
    # MOL-59: no store at all -> no measurements -> no ★ (pins the existing fail-open).
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == []


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


def test_edit_drawer_is_the_clean_five_lever_set(tmp_path):
    # The edit sidebar collapses to DISTINCT, non-overlapping levers: voice, content_focus, selection_scope, hook_angle
    # (the corpus is managed on the card). Everything that repeated another field is gone from the drawer AND
    # the compose-preview path: tag_lean (corpus owns hashtags), the 3 directive overrides (voice + structured
    # levers cover them), genre (-> Research), framing (-> smart framing), hook_tone/clip_profile/clip_count/brief.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    drawer = _client(cfg).get(f"/personas/drawer/{pid}").get_data(as_text=True)
    for keep in ('name="voice"', 'name="content_focus"', 'name="selection_scope"', 'name="hook_angle"'):
        assert keep in drawer, f"missing lever {keep}"
    for gone in ('name="tag_lean"', 'name="casting_directive"', 'name="hook_directive"',
                 'name="caption_directive"', 'name="genre"', 'name="framing"', 'name="clip_count"',
                 'name="hook_tone"', 'name="clip_profile"', 'name="brief"'):
        assert gone not in drawer, f"removed control still in the edit drawer: {gone}"


def test_genre_moves_from_editor_to_research(tmp_path):
    # genre is not a clip lever — it seeds hashtag RESEARCH, so its input lives with the Research control.
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="P1", voice="v1")
    page = _client(cfg).get("/personas").get_data(as_text=True)
    assert 'name="genre"' in page and "/personas/research" in page     # genre settable in the research area


def test_persona_forms_drop_dead_intake_fields(tmp_path):
    # The inert intake inputs (language / reference accounts / notes) are gone from BOTH the add form and the
    # edit drawer; only the functional Genre field remains. language is source-derived; refs + notes fed nothing.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    add_form = _client(cfg).get("/personas").get_data(as_text=True)
    drawer = _client(cfg).get(f"/personas/drawer/{pid}").get_data(as_text=True)
    for body in (add_form, drawer):
        assert 'name="language"' not in body       # inert (source-derived) — removed
        assert 'name="refs"' not in body           # reference accounts — removed
        assert 'name="notes"' not in body          # memo nothing read — removed


def test_post_add_persona_route(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/personas/add", data={"name": "New One", "voice": "a voice"})
    assert r.status_code == 200
    assert any(p.name == "New One" for p in core.Personas.load(cfg).all())


def test_corpus_tag_with_quote_is_json_escaped_in_attribute(tmp_path):
    # _norm keeps a double-quote (a hand-edit could land one); the hx-vals attribute must JSON-escape it
    # via tojson so a crafted tag can NEVER break out of the attribute (defense-in-depth — ecc:python-review).
    # R4: `add_corpus_tag` now REFUSES this tag structurally (hashtag_hygiene), so that route is closed — but
    # the threat this test names is a HAND-EDIT of personas.json, which bypasses the write boundary entirely.
    # The escaping defense therefore still matters; seed the file directly, i.e. via the vector that remains.
    import json as _json
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _raw = _json.loads(cfg.personas_path.read_text())
    for _d in _raw["personas"]:
        if _d["id"] == pid: _d["hashtag_corpus"] = ['#a"b']
    cfg.personas_path.write_text(_json.dumps(_raw))
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
    sp.connect_account(cfg, "linked", pid)
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert "persona-accounts" in html and "linked" in html          # the driven handle shows on the card
    assert "persona-assign" in html and "free" in html              # the unassigned account is assignable inline
    assert "Connect accounts" not in html                            # the orphan page-foot section is removed


def test_persona_card_action_tiers_assign_over_hashtag_utils(tmp_path):
    # MOL-60 (kept, U9-updated selectors): on a persona card the CONSEQUENTIAL, rare Assign (rewires which
    # voice drives a real account, stealing it from another persona) must out-weigh the FREQUENT, low-stakes
    # hashtag utilities (Force refresh now / Add / Check reach — two of which only PROPOSE, never mutate). Per
    # the MOL-44 3-tier system: Assign stays secondary (base .button = fill+border), the three hashtag tools
    # drop to .ghost. U9: the card's single .primary is now the zone-2 inline Save (was "Tune this voice").
    import re
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@free", "platforms": ["tiktok"], "status": "active"}])
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _client(cfg).get("/personas").get_data(as_text=True)

    def _btn(label):  # the <button ...>LABEL</button> opening tag whose text is exactly `label`
        m = re.search(r'<button\b[^>]*>' + re.escape(label) + r'</button>', html)
        assert m, f"{label!r} button not found in rendered personas panel"
        return m.group(0)

    # the three hashtag utilities are demoted to the tertiary ghost tier (Research is relabelled Force refresh now)
    assert 'class="ghost"' in _btn("Force refresh now")
    assert 'class="ghost"' in _btn("Add")
    # "Check reach" may wrap across whitespace in the template; match on its title as the anchor
    check = re.search(r'<button\b[^>]*title="look up this tag[^"]*"[^>]*>', html)
    assert check and 'class="ghost"' in check.group(0), "Check reach must be a ghost button"

    # Assign stays secondary — NOT demoted to ghost, NOT promoted to primary
    assign = _btn("Assign")
    assert "ghost" not in assign, "Assign must out-weigh the hashtag utils (not ghost)"
    assert "primary" not in assign, "Assign must not become a second primary (one per view)"

    # the card's single primary is now the zone-2 inline Save (the drawer-trigger primary is gone)
    assert 'class="primary">Save</button>' in html
    assert "Tune this voice" not in html
