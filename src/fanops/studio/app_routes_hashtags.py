"""Hashtags route group: source locks (ship menu), measurement cache, duplicate-line warning.
GET is network-inert. Read-only."""
from __future__ import annotations
from flask import render_template, url_for
from fanops.ledger import Ledger
from fanops.studio import views_hashtags


def register_hashtags_routes(app, cfg):
    def _page():
        return views_hashtags.hashtags_page(cfg, led=Ledger.load(cfg), edit_href=url_for("personas_view"))

    @app.get("/hashtags")
    def hashtags_view():
        return render_template("hashtags.html", page=_page(), result=None, tab="hashtags")
