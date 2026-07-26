# tests/test_persona_legibility.py
# U9 — Persona Page Legibility: each persona card is THREE labelled zones (identity → editable → derived)
# so the operator can tell, structurally, what they're looking at, what they can change vs what is derived,
# and how each editable knob affects future work. Template/IA + read-projection only — no lever registry,
# model, hydration, or route changes. The card now edits IN PLACE (the same do_personas_edit route + the
# lever_fields macro the drawer uses); the drawer route/template/JS stay but the card no longer triggers it.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops import personas as core
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def _panel(cfg):
    return _client(cfg).get("/personas").get_data(as_text=True)


def test_card_renders_three_zones_in_order(tmp_path):
    # The card answers, top to bottom: what this is / what you can change / what the system derives. Assert the
    # three eyebrow headings render in that sequence for the persona's card.
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _panel(cfg)
    i1 = html.find("What this is")
    i2 = html.find("What you can change")
    i3 = html.find("Derived — updates itself")
    assert -1 < i1 < i2 < i3, f"zones out of order: {i1}, {i2}, {i3}"


def test_zone2_editable_inventory_matches_registry(tmp_path):
    # Every editable lever (LEVER_REGISTRY, minus the tags corpus which lives in zone 3) plus name/voice has a
    # real form control in zone 2; and zone 3 carries NO editable lever inputs (it is read-only + corpus tools).
    from fanops.persona_levers import LEVER_REGISTRY
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _panel(cfg)
    zone2 = html.split("What you can change", 1)[1].split("Derived — updates itself", 1)[0]
    assert 'name="name"' in zone2 and 'name="voice"' in zone2
    # the editor levers (content_focus/selection_scope/hook_angle) are the editable non-tag registry keys —
    # the corpus (kind=tags) and the GLOBAL clip_profile band are not per-persona card inputs.
    editable = [lv["key"] for lv in LEVER_REGISTRY if lv["kind"] in ("multi", "select") and lv["key"] != "clip_profile"]
    for key in editable:
        assert f'name="{key}"' in zone2, f"editable lever {key} missing a control in zone 2"
    # zone 3 is derived/read-only — it must not repeat a lever INPUT
    zone3 = html.split("Derived — updates itself", 1)[1].split("</article>", 1)[0]
    assert 'name="selection_scope"' not in zone3 and 'name="hook_angle"' not in zone3 and 'name="content_focus"' not in zone3


def test_blank_clears_hint_renders(tmp_path):
    # Zone 2 documents the authoritative save behavior (an unchecked/blank lever CLEARS it, studio/personas.py).
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _panel(cfg)
    zone2 = html.split("What you can change", 1)[1].split("Derived — updates itself", 1)[0]
    assert "Leaving scope or hook angle blank clears it on save." in zone2


def test_corpus_chips_carry_the_measurement_and_its_anchor(tmp_path):
    # Derivation meta drives the chip: each tag shows Meta's own value and the ANCHOR tag whose top posts
    # surfaced it ("via #hiphop") — the honest "why is this here". A tag with NO meta entry degrades to a
    # PLAIN chip rather than inventing a number.
    from fanops.hashtags import METRIC_FIELD
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1")
    core.apply_auto_corpus(cfg, pid, tags=["#derivedtag", "#barenometa"], meta={
        "#derivedtag": {METRIC_FIELD: 1500.0, "measured_at": "2026-07-01T00:00:00+00:00", "from": "#hiphop"}})
    # the read-model carries the value + anchor per tag
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    by_tag = {r["tag"]: r for r in card.corpus_tags}
    assert by_tag["#derivedtag"]["value"] == 1500.0 and by_tag["#derivedtag"]["from"] == "#hiphop"
    assert by_tag["#barenometa"]["value"] is None and by_tag["#barenometa"]["from"] is None
    # and the rendered chips: the measured tag shows its number + anchor, the bare one shows neither
    html = _panel(cfg)
    corpus = html.split('class="persona-corpus"', 1)[1]
    assert "1500" in corpus and "via #hiphop" in corpus
    bare = corpus.split("#barenometa", 1)[1][:80]
    assert "corpus-prov" not in bare and "reach-n" not in bare, "a meta-less tag must render a plain chip"


def test_edit_one_lever_round_trip(tmp_path):
    # Editing ONE lever (hook_angle) via the inline zone-2 form must leave every OTHER on-disk persona field
    # byte-identical (name/voice/content_focus/selection_scope + the corpus/meta) — no collateral mutation.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Keep", voice="the voice")
    # seed a full lever set + a corpus so there is real state to preserve, THROUGH the same edit route
    r0 = _client(cfg).post("/personas/edit", data={
        "id": pid, "name": "Keep", "voice": "the voice",
        "content_focus": ["punchlines"], "selection_scope": "subject_locked", "hook_angle": "curiosity"})
    assert r0.status_code == 200
    core.apply_auto_corpus(cfg, pid, tags=["#keeper"], meta={})
    before = json.loads(cfg.personas_path.read_text())["personas"][0]
    # change ONLY hook_angle, re-posting the same other values (the form is authoritative)
    r = _client(cfg).post("/personas/edit", data={
        "id": pid, "name": "Keep", "voice": "the voice",
        "content_focus": ["punchlines"], "selection_scope": "subject_locked", "hook_angle": "fomo"})
    assert r.status_code == 200
    after = json.loads(cfg.personas_path.read_text())["personas"][0]
    assert after["hook_angle"] == "fomo"                          # the one edit landed
    for k in ("name", "voice", "content_focus", "selection_scope", "hashtag_corpus", "hashtag_corpus_meta"):
        assert after[k] == before[k], f"{k} changed on a one-lever edit: {before[k]!r} -> {after[k]!r}"


def test_derived_zone_offers_the_niche_lever_not_a_curation_lane(tmp_path):
    # Zone 3 is DERIVED output. The one editable thing beside the corpus is the NICHE, because that is what
    # the next measurement pass searches on — the add/remove/research proposal lane is gone with the
    # curation model it belonged to.
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Curator", voice="champions craft")
    html = _panel(cfg)
    zone3 = html.split("Derived — updates itself", 1)[1].split("</article>", 1)[0]
    assert 'name="genre"' in zone3 and "Save niche" in zone3
    for gone in ("Force refresh now", "Check reach", "/personas/research", "/personas/corpus/add"):
        assert gone not in zone3, f"a retired curation control still renders in the derived zone: {gone}"
