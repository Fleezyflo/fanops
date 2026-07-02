"""Live-library route group for the Studio (ledger-rebuild MOL-27 + M4 wipe surface MOL-33): the read-only
"viewed there, not authored here" surface over led.imported_media, PLUS the operator wipe (fall-away of
unbacked rows) behind a read-only preview + a typed confirm. register_live_routes(app, cfg) registers them
under `live_library` (GET /live-library) + the wipe routes; create_app calls it. The library view is a pure
ledger read (the M2 projection / M3 insights fill the data); the wipe is snapshot-first + code-gated."""
from __future__ import annotations
from flask import render_template, request
from fanops.ledger import Ledger
from fanops.studio import views, actions_wipe


def register_live_routes(app, cfg):
    def _page(*, preview=None, result=None):
        led = Ledger.load(cfg)
        return render_template("live_library.html", rows=views.live_library(led, cfg),
                               scope=views.live_library_scope(cfg), tab="live",
                               preview=preview, wipe_result=result, confirm_word=actions_wipe.CONFIRM_WORD)

    @app.get("/live-library")
    def live_library():
        return _page()

    @app.post("/live-library/wipe/preview")
    def do_wipe_preview():
        # READ-ONLY: compute the would-remove id-set + counts and render them; the typed-confirm form is
        # shown only AFTER this preview (the destructive step is gated behind seeing what it removes).
        return _page(preview=actions_wipe.preview_wipe(cfg))

    @app.post("/live-library/wipe/confirm")
    def do_wipe_confirm():
        # GATED on the typed word (actions_wipe.confirm_wipe checks it, snapshots first, then executes).
        res = actions_wipe.confirm_wipe(cfg, typed=request.form.get("confirm_text", ""))
        # re-render with a fresh preview so the operator sees the now-updated would-remove set (empty on success).
        return _page(preview=actions_wipe.preview_wipe(cfg), result=res)
