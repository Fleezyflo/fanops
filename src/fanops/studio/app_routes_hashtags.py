"""Hashtags route group for the Studio (U11): source locks (ship menu), then Layer A/B discovery,
then rotation health. GET is network-INERT. Read-only. register_hashtags_routes(app, cfg)
registers under the ORIGINAL endpoint name (url_for byte-identical); create_app calls it AFTER personas
routes (so the 'edit →' link's personas_view endpoint is already registered). No nav change here — the
rail entry is U13's job (base.html)."""
from __future__ import annotations
from flask import render_template, url_for
from fanops.ledger import Ledger
from fanops.studio import views_hashtags


def register_hashtags_routes(app, cfg):
    def _page():
        # One lock-free ledger read feeds rotation health; edit_href points the corpora rows at U9/Personas.
        return views_hashtags.hashtags_page(cfg, led=Ledger.load(cfg), edit_href=url_for("personas_view"))

    @app.get("/hashtags")
    def hashtags_view():
        return render_template("hashtags.html", page=_page(), result=None, tab="hashtags")
