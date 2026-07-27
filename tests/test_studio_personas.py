# tests/test_studio_personas.py
# A2 — the Studio "Personas" page: personas become editable/addable/connectable IN THE BROWSER (no
# personas.json hand-edit). The action layer (fanops.studio.personas) wraps the A1 core writers and
# returns ActionResult (ok/error/detail), never raising into a 500; the read-model (views.personas_page)
# powers the page; the routes htmx-swap the panel. Mirrors the Go-Live action/route conventions.
import json
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.hashtags import METRIC_FIELD
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
    # create takes the clean levers only (voice + content_focus/selection_scope/hook_angle); tag_lean and
    # genre are NOT create params — the corpus is DERIVED, the niche is set via the Save-niche control.
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", content_focus=["punchlines"],
                          selection_scope="controversy_seeking", hook_angle="curiosity")
    assert r.ok
    p = core.Personas.load(cfg).get(r.detail["created"])
    assert p.voice == "champions craft" and p.content_focus == ["punchlines"] and p.hook_angle == "curiosity"
    assert p.intake == {}                          # the niche is set later, not collected at create
    assert p.hashtag_corpus == []                  # a fresh persona ships NO corpus — derivation fills it


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


def test_set_niche_saves_the_declared_terms(tmp_path):
    # `Persona.niche` is the declared subject list persona_terms will search on — the operator's most
    # direct lever over which hashtags get discovered and measured. Comma/newline split; an inner space
    # is ONE entry and is refused at the store with the tag_defect string (no silent join/split).
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Z")
    assert sp.set_niche(cfg, pid, "lyricism, songwriting").ok
    assert core.Personas.load(cfg).get(pid).niche == ["lyricism", "songwriting"]
    bad = sp.set_niche(cfg, pid, "hip hop")
    assert bad.ok is False and "malformed" in (bad.error or "")
    assert core.Personas.load(cfg).get(pid).niche == ["lyricism", "songwriting"]  # refusal left prior intact
    assert sp.set_niche(cfg, pid, "").ok                # blank CLEARS it (the form is authoritative) — A-13 inverts this
    assert core.Personas.load(cfg).get(pid).niche == []


def test_set_niche_unknown_persona_is_a_clean_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = sp.set_niche(cfg, "ghost", "rap")
    assert r.ok is False and r.error
    assert sp.set_niche(cfg, "", "rap").ok is False     # no persona selected -> clean error, never a 500


def test_corpus_curation_actions_are_gone(tmp_path):
    # The corpus is DERIVED wholesale from platform evidence, so there is nothing to add, remove,
    # research or recommend. These must be absent, not merely unused.
    for dead in ("add_corpus_tag", "remove_corpus_tag", "recommend_tag", "research_corpus"):
        assert not hasattr(sp, dead), f"{dead} is a curation action and must be deleted"


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

def _cache(cfg, values):
    """The platform measurement cache in its real shape: {tag: {graph_id, like_count, measured_at}}."""
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        t: {"graph_id": "id-" + t.lstrip("#"), METRIC_FIELD: float(v),
            "measured_at": "2026-07-20T00:00:00+00:00"} for t, v in values.items()}))


def test_personas_page_read_model(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap"], meta={})
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid},
                         {"handle": "@b", "platforms": ["instagram"], "status": "active"}])
    page = views.personas_page(cfg)
    card = next(c for c in page.personas if c.id == pid)
    assert card.voice == "v1" and card.corpus == ["#detroitrap"] and card.linked_handles == ["a"]
    links = {lk.handle: lk.persona_id for lk in page.accounts}
    assert links["a"] == pid and links["b"] is None


def test_personas_page_surfaces_the_platform_metric(tmp_path):
    # The card's number + ★ come from the platform MEASUREMENT CACHE, never own-post reach, and it is
    # Meta's own like_count — not a "reach" figure we computed.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap"], meta={})
    _cache(cfg, {"#detroitrap": 4200, "#hiphop": 900})
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert "#detroitrap" in card.reach_tags                 # measured -> flagged most-active (★)
    assert card.reach_means.get("#detroitrap") == 4200.0    # Meta's own like_count, surfaced honestly


def test_personas_page_star_is_gated_on_a_real_measurement(tmp_path):
    # The ★ asserts a live-platform fact: a corpus tag the cache has no record for does NOT star, and a
    # LEGACY record (the invented likes+comments sum under a `reach` key) is not a measurement either.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap"], meta={})
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"#detroitrap": {
        "graph_id": "id-detroitrap", "reach": 4200, "measured_at": "2026-07-20T00:00:00+00:00"}}))
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == []                            # no platform field -> unmeasured -> no ★
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert "#detroitrap" in html and "★" not in html


def test_personas_page_star_only_on_measured_tags(tmp_path):
    # with the cache covering SOME corpus tags, ONLY the measured ones star.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap", "#hiphop"], meta={})
    _cache(cfg, {"#detroitrap": 4200})
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == ["#detroitrap"]               # #hiphop is in the corpus but unmeasured


def test_personas_page_no_cache_no_stars(tmp_path):
    # no cache at all -> no measurements -> no ★ (pins the existing fail-open).
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap"], meta={})
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.reach_tags == []


def test_personas_page_renders_no_retired_block(tmp_path):
    # A-11 deleted the deprecated-corpus record: the card has no "Retired" section to render any more.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d["id"] == pid: d["hashtag_corpus"] = ["#taylorswift"]
    cfg.personas_path.write_text(json.dumps(raw))
    html = _client(cfg).get("/personas").get_data(as_text=True)
    assert "Retired (" not in html


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


def test_niche_lives_in_the_editable_zone(tmp_path):
    # niche is not a clip lever — it is the declared subject list the next measurement pass searches on,
    # so its input sits in zone 2 ("What you can change"). The proposal routes it used to sit next to are gone.
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="P1", voice="v1")
    page = _client(cfg).get("/personas").get_data(as_text=True)
    assert 'name="niche"' in page and "/personas/niche" in page       # the niche is settable on the card
    assert 'name="genre"' not in page
    for gone in ("/personas/research", "/personas/recommend", "/personas/corpus/add", "/personas/corpus/remove"):
        assert gone not in page, f"a curation route survived in the panel: {gone}"


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


def test_corpus_tag_with_quote_is_escaped_when_rendered(tmp_path):
    # _norm keeps a double-quote (a hand-edit could land one) and the corpus is now rendered as chip TEXT,
    # so Jinja autoescape is the defense. The threat this test names is a HAND-EDIT of personas.json,
    # which bypasses every write boundary — seed the file directly, i.e. via the vector that remains.
    import json as _json
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _raw = _json.loads(cfg.personas_path.read_text())
    for _d in _raw["personas"]:
        if _d["id"] == pid: _d["hashtag_corpus"] = ['#a"b']
    cfg.personas_path.write_text(_json.dumps(_raw))
    r = _client(cfg).get("/personas")
    assert r.status_code == 200
    assert b"#a&#34;b" in r.data                 # autoescape neutralized the inner quote
    assert b'#a"b' not in r.data                 # the raw, un-escaped (breakout) form must NOT appear


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


def test_persona_card_action_tiers_assign_over_the_niche_util(tmp_path):
    # MOL-60 (kept, repointed): on a persona card the CONSEQUENTIAL, rare Assign (rewires which voice
    # drives a real account, stealing it from another persona) must out-weigh the FREQUENT, low-stakes
    # hashtag utility. With the corpus DERIVED there is exactly one such utility left — Save niche — and
    # it sits in the tertiary ghost tier. The card's single .primary is the zone-2 inline Save.
    import re
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@free", "platforms": ["tiktok"], "status": "active"}])
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _client(cfg).get("/personas").get_data(as_text=True)

    def _btn(label):  # the <button ...>LABEL</button> opening tag whose text is exactly `label`
        m = re.search(r'<button\b[^>]*>' + re.escape(label) + r'</button>', html)
        assert m, f"{label!r} button not found in rendered personas panel"
        return m.group(0)

    assert 'class="ghost"' in _btn("Save niche")
    # the retired curation buttons must not come back with the routes deleted underneath them
    for gone in ("Force refresh now", "Check reach", "Tune this voice"):
        assert gone not in html, f"a deleted curation control still renders: {gone}"

    # Assign stays secondary — NOT demoted to ghost, NOT promoted to primary
    assign = _btn("Assign")
    assert "ghost" not in assign, "Assign must out-weigh the hashtag utils (not ghost)"
    assert "primary" not in assign, "Assign must not become a second primary (one per view)"

    # the card's single primary is the zone-2 inline Save
    assert 'class="primary">Save</button>' in html
