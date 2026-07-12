"""Live-library route group for the Studio (ledger-rebuild MOL-27 + M4 wipe surface MOL-33): the read-only
"viewed there, not authored here" surface over led.imported_media, PLUS the operator wipe (fall-away of
unbacked rows) behind a read-only preview + a typed confirm. register_live_routes(app, cfg) registers the
wipe routes; the GET now 301s into the folded /library?view=live lens (U13). create_app calls it. The
library view is a pure ledger read (the M2 projection / M3 insights fill the data); the wipe is
snapshot-first + code-gated."""
from __future__ import annotations
from flask import redirect, render_template, request, url_for
from fanops.ledger import Ledger
from fanops.studio import views, actions_wipe


def register_live_routes(app, cfg):
    def _page(*, preview=None, result=None):
        # U13: the wipe POSTs re-render on the FOLDED surface — library.html under the live lens. It carries
        # the same live-library context the /library?view=live GET builds, so htmx swaps the #wipe-panel
        # fragment out of the folded page exactly as before (the standalone live-library page is retired).
        led = Ledger.load(cfg)
        return render_template("library.html", view="live", catalog=views.library_catalog(cfg),
                               rows=views.live_library(led, cfg), scope=views.live_library_scope(cfg),
                               tab="library", preview=preview, wipe_result=result,
                               confirm_word=actions_wipe.CONFIRM_WORD)

    @app.get("/live-library")
    def live_library():
        # U13: Live library folded into the one Library surface at /library?view=live. 301 (permanent) so
        # bookmarks/links move; ALL other query args (incl. ?account=) ride through url_for verbatim. A stray
        # incoming ?view= is dropped so it can never collide with the view=live we force (url_for would 500 on
        # a duplicate kwarg).
        args = {k: v for k, v in request.args.items() if k != "view"}
        return redirect(url_for("library", view="live", **args), code=301)

    @app.post("/live-library/wipe/preview")
    def do_wipe_preview():
        # READ-ONLY: compute the would-remove id-set + counts and render them; the typed-confirm form is
        # shown only AFTER this preview (the destructive step is gated behind seeing what it removes).
        return _page(preview=actions_wipe.preview_wipe(cfg))

    @app.post("/live-library/wipe/confirm")
    def do_wipe_confirm():
        # GATED on the typed word AND the preview token (MOL-71): confirm_wipe checks the word, then re-verifies
        # the token against a fresh preview (preview-ran gate), snapshots first, then executes.
        res = actions_wipe.confirm_wipe(cfg, typed=request.form.get("confirm_text", ""),
                                        token=request.form.get("preview_token", ""))
        # re-render with a fresh preview so the operator sees the now-updated would-remove set (empty on success).
        return _page(preview=actions_wipe.preview_wipe(cfg), result=res)
