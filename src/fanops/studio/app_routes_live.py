"""Live-library route group for the Studio (ledger-rebuild MOL-27): the read-only "viewed there, not
authored here" surface over led.imported_media. register_live_routes(app, cfg) registers it under the
`live_library` endpoint (GET /live-library); create_app calls it. A pure ledger read — no mutation, no
Graph call (the M2 projection / M3 insights fill the data via `fanops map-media` or the daemon)."""
from __future__ import annotations
from flask import render_template
from fanops.ledger import Ledger
from fanops.studio import views


def register_live_routes(app, cfg):
    @app.get("/live-library")
    def live_library():
        led = Ledger.load(cfg)
        rows = views.live_library(led, cfg)
        return render_template("live_library.html", rows=rows, scope=views.live_library_scope(cfg), tab="live")
