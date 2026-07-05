"""Personas route group for the Studio: the Personas tab (voice/lean/corpus levers, the persona drawer,
live compose preview) plus the persona<->account link and corpus research/recommend actions.
register_personas_routes(app, cfg) registers them under their ORIGINAL endpoint names (url_for byte-identical);
create_app calls it. Constants come from app (loaded before create_app runs, so cycle-free)."""
from __future__ import annotations
from flask import render_template, request
from fanops.studio import personas as studio_personas, views
from fanops.studio.app import _LEVER_EFFECTS, _LEVERS, _LEVER_REF


def register_personas_routes(app, cfg):
    @app.get("/personas")
    def personas_view():
        # First-class personas (voice/corpus/intake/levers) — list, add via intake, edit, connect to
        # accounts. nav_account is injected globally but the page is account-agnostic (it lists ALL).
        return render_template("personas.html", page=views.personas_page(cfg),
                               levers=_LEVERS, effects=_LEVER_EFFECTS, lever_ref=_LEVER_REF, result=None, tab="personas")

    def _personas_panel(result=None):
        # Re-render the panel with FRESH personas_page after an action (htmx swaps #personas-panel).
        return render_template("_personas_panel.html", page=views.personas_page(cfg),
                               levers=_LEVERS, effects=_LEVER_EFFECTS, lever_ref=_LEVER_REF, result=result, tab="personas")

    @app.get("/personas/drawer/<pid>")
    def do_personas_drawer(pid):
        # Slice 3: render the focused persona's levers as a slide-out DRAWER body (htmx swaps it into the
        # body-level #persona-drawer mount). Levers are visible here — no nested collapse. Save/Delete reuse
        # /personas/edit + /personas/delete (re-render #personas-panel). Fail-open: an unknown id renders a
        # clean "not found" dialog (p=None), never a 404/500 (htmx would swap an error page into the mount).
        card = next((c for c in views.personas_page(cfg).personas if c.id == pid), None)
        return render_template("_persona_drawer.html", p=card,
                               levers=_LEVERS, effects=_LEVER_EFFECTS)

    @app.post("/personas/compose")
    def do_personas_compose():
        # LIVE TRANSLATION: recompute what the in-progress (unsaved) persona compiles to from the posted form
        # values and render the compose panel. Transient — preview_compose NEVER persists. htmx swaps the
        # per-form #persona-compose-<id> target on every lever change.
        return render_template("_persona_compose.html", result=studio_personas.preview_compose(cfg, request.form))

    @app.post("/personas/add")
    def do_personas_add():
        return _personas_panel(studio_personas.create_persona(
            cfg, request.form.get("name", ""), request.form.get("voice", ""),
            content_focus=request.form.getlist("content_focus"), selection_scope=request.form.get("selection_scope", ""),
            hook_angle=request.form.get("hook_angle", "")))

    @app.post("/personas/edit")
    def do_personas_edit():
        return _personas_panel(studio_personas.edit_persona(
            cfg, request.form.get("id", ""), request.form.get("name", ""), request.form.get("voice", ""),
            content_focus=request.form.getlist("content_focus"), selection_scope=request.form.get("selection_scope", ""),
            hook_angle=request.form.get("hook_angle", "")))

    @app.post("/personas/delete")
    def do_personas_delete():
        return _personas_panel(studio_personas.delete_persona(cfg, request.form.get("id", "")))

    @app.post("/personas/corpus/add")
    def do_personas_corpus_add():
        return _personas_panel(studio_personas.add_corpus_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/corpus/remove")
    def do_personas_corpus_remove():
        return _personas_panel(studio_personas.remove_corpus_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/research")
    def do_personas_research():
        # B3: propose the reach-best hashtags this persona lacks (bootstrap research) -> the panel renders
        # them with one-click Add. Grounded in the reach store; instant + budget-free.
        return _personas_panel(studio_personas.research_corpus(cfg, request.form.get("id", ""), request.form.get("genre", "")))

    @app.post("/personas/recommend")
    def do_personas_recommend():
        # B2: look up a candidate tag's live Graph metrics (engagement) so the operator can decide before
        # adding it to the corpus. The panel renders the metrics + an Add button; no add happens here.
        return _personas_panel(studio_personas.recommend_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/connect")
    def do_personas_connect():
        # Connect/disconnect ONE account to a persona (blank persona_id disconnects). Re-render the panel.
        return _personas_panel(studio_personas.connect_account(cfg, request.form.get("handle", ""), request.form.get("persona_id", "")))

    @app.post("/personas/migrate")
    def do_personas_migrate():
        # One-click: lift inline account persona strings into first-class Persona records + link (idempotent).
        return _personas_panel(studio_personas.run_migration(cfg))
