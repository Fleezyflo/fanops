"""Make/Run route group for the Studio: the ingestion + pipeline driver (ingest / pull / upload+auto-
ingest / advance / prepare), the third-party asset library, and the oversize-upload (413) handler.
register_run_routes(app, cfg) registers them under their ORIGINAL endpoint names (url_for byte-identical);
create_app calls it. The 413 errorhandler re-renders the Run panel at HTTP 200 so htmx 2.x swaps it
(a non-2xx body is dropped); _run_panel is defined before it in the same scope, so the closure resolves."""
from __future__ import annotations
from flask import render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from fanops.studio import actions, views


def register_run_routes(app, cfg):

    def _run_handoff(result=None):
        h = views.review_handoff(cfg)
        bid = ((result or {}).detail or {}).get("batch_id") if result and result.ok else None
        if bid:
            h = {**h, "batch": bid}
        return h
    @app.get("/run")
    def run_panel():
        # The pipeline DRIVER: ingest/pull/advance from the browser so the operator never needs the
        # terminal. Read-only status; the actions below go through the same lock-safe paths as the CLI.
        return render_template("run.html", status=views.pipeline_status(cfg),
                               review_handoff=_run_handoff(), tab="run")

    @app.get("/run/status")
    def run_status():
        # The Make tab's self-polling status counts — so a background run's progress shows live without
        # the operator clicking anything (swaps only #run-status, never the upload/add-link forms).
        return render_template("_run_status.html", status=views.pipeline_status(cfg))

    def _run_panel(result):
        # Re-render the panel partial with FRESH status after an action (htmx swaps #run-panel), so the
        # counts update in place — drop files, click ingest, watch sources tick up, no page reload.
        return render_template("_run_panel.html", status=views.pipeline_status(cfg), result=result,
                                   review_handoff=_run_handoff(result), tab="run")

    @app.post("/run/ingest")
    def do_run_ingest():
        return _run_panel(actions.run_ingest(cfg, batch_name=request.form.get("batch_name", ""),
                                             target_accounts=request.form.getlist("target_accounts"),
                                             burn_subs=(False if request.form.get("no_subs") else None)))

    @app.post("/run/pull")
    def do_run_pull():
        return _run_panel(actions.run_pull(cfg, request.form.get("url", "")))

    @app.post("/run/upload")
    def do_run_upload():
        # Stream operator-uploaded raw video into 01_inbox AND catalogue it in one click (M5 auto-ingest)
        # — the browser replacement for a Finder drag + Ingest. save_uploads owns validation + atomic
        # os.replace; save_uploads_and_ingest chains the ingest pass; the panel re-renders with fresh
        # counts (htmx outerHTML). The manual "Ingest inbox" button stays for a re-ingest / failed retry.
        # "no_subs" checkbox (e.g. a music batch) -> burn_subs=False override; unchecked -> None -> global default.
        burn_subs = False if request.form.get("no_subs") else None
        return _run_panel(actions.save_uploads_and_ingest(cfg, request.files.getlist("files"),
                                                           batch_name=request.form.get("batch_name", ""),
                                                           target_accounts=request.form.getlist("target_accounts"),
                                                           burn_subs=burn_subs))

    @app.post("/run/resume")
    def do_run_resume():
        # MOL-123: Resume an errored / moments_empty source from the Run tab. Goes through the same
        # stage-aware helper as the CLI (pipeline.resume_source via actions.resume_source_studio), then
        # re-renders the panel so the recovered source drops off the errored list in place.
        return _run_panel(actions.resume_source_studio(cfg, request.form.get("source_id", "")))

    @app.post("/run/advance")
    def do_run_advance():
        # confirm derived from the checkbox the template shows ONLY on a live backend (Track C guard).
        return _run_panel(actions.run_advance(cfg, request.form.get("base_time") or None,
                                              confirmed=bool(request.form.get("confirm"))))

    @app.post("/run/pull-metrics")
    def do_pull_metrics():
        return _run_panel(actions.pull_metrics_studio(cfg))

    @app.post("/run/prepare")
    def do_run_prepare():
        # Auto-prepare: answer the gates (via the responder) + advance until stable, so the operator
        # never hand-writes a caption. Same live-publish confirm checkbox as advance.
        return _run_panel(actions.run_prepare(cfg, request.form.get("base_time") or None,
                                              confirmed=bool(request.form.get("confirm"))))

    @app.get("/library")
    def library():
        # M1 asset memory: every Source the system remembers, split native vs third-party.
        return render_template("library.html", catalog=views.asset_catalog(cfg), tab="library")

    @app.post("/library/upload")
    def do_thirdparty_upload():
        # Validate + land third-party assets (peer staging dir), then catalogue them INERT — only if the
        # save succeeded (a fully-rejected upload surfaces the save error, never a misleading "0 added").
        res = actions.save_thirdparty_uploads(cfg, request.files.getlist("files"))
        if res.ok:
            res = actions.run_ingest_thirdparty(cfg)
        return render_template("_library_panel.html", catalog=views.asset_catalog(cfg), result=res, tab="library")

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        # An over-MAX_CONTENT_LENGTH upload: Werkzeug raised 413 before do_run_upload ran. Re-render the
        # Run panel with a clean "too large" message at HTTP 200 — htmx 2.0.3 only swaps 2xx bodies, so a
        # 413 panel would be silently dropped and the operator would see nothing. The cap is enforced by
        # Werkzeug regardless of this status; only the friendly response's status changes.
        mb = (app.config["MAX_CONTENT_LENGTH"] or 0) // (1024 * 1024)
        return _run_panel(actions.ActionResult(ok=False, error=f"file too large — the upload cap is {mb} MB"))
