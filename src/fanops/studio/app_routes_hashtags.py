"""Hashtags route group for the Studio (U11): the /hashtags observatory — corpora-at-a-glance, the reach store,
the Meta budget meter, cross-account rotation health, and the operator's global ban lane. GET renders the whole
page (budget-INERT — zero Graph calls); the two POSTs mutate the ban list via studio/hashtags then re-render the
#hashtags-panel partial (htmx swap). register_hashtags_routes(app, cfg) registers them under their ORIGINAL
endpoint names (url_for byte-identical); create_app calls it AFTER personas routes (so the 'edit →' link's
personas_view endpoint is already registered). No nav change here — the rail entry is U13's job (base.html)."""
from __future__ import annotations
from flask import render_template, request, url_for
from fanops.ledger import Ledger
from fanops.studio import hashtags as studio_hashtags, views_hashtags


def register_hashtags_routes(app, cfg):
    def _page():
        # One lock-free ledger read feeds rotation health; edit_href points the corpora rows at U9/Personas.
        return views_hashtags.hashtags_page(cfg, led=Ledger.load(cfg), edit_href=url_for("personas_view"))

    @app.get("/hashtags")
    def hashtags_view():
        return render_template("hashtags.html", page=_page(), result=None, tab="hashtags")

    def _hashtags_panel(result=None):
        # Re-render the panel with a FRESH read-model after a ban mutation (htmx swaps #hashtags-panel).
        return render_template("_hashtags_panel.html", page=_page(), result=result, tab="hashtags")

    @app.post("/hashtags/ban/add")
    def do_hashtags_ban_add():
        return _hashtags_panel(studio_hashtags.add_ban(cfg, request.form.get("tag", "")))

    @app.post("/hashtags/ban/remove")
    def do_hashtags_ban_remove():
        return _hashtags_panel(studio_hashtags.remove_ban(cfg, request.form.get("tag", "")))
