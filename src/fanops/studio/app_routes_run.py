"""Make/Run route group for the Studio: the ingestion + pipeline driver (ingest / pull / upload+auto-
ingest / advance / prepare), the third-party asset library, and the oversize-upload (413) handler.
register_run_routes(app, cfg) registers them under their ORIGINAL endpoint names (url_for byte-identical);
create_app calls it. The 413 errorhandler re-renders the Run panel at HTTP 200 so htmx 2.x swaps it
(a non-2xx body is dropped); _run_panel is defined before it in the same scope, so the closure resolves."""
from __future__ import annotations
from flask import jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from fanops.ledger import Ledger
from fanops.studio import actions, views, actions_wipe


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
                                             burn_subs=(False if request.form.get("no_subs") else None),
                                             speech_trust=(True if request.form.get("speech_trust") else None)))

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
        speech_trust = True if request.form.get("speech_trust") else None
        return _run_panel(actions.save_uploads_and_ingest(cfg, request.files.getlist("files"),
                                                           batch_name=request.form.get("batch_name", ""),
                                                           target_accounts=request.form.getlist("target_accounts"),
                                                           burn_subs=burn_subs, speech_trust=speech_trust))

    @app.post("/run/upload/init")
    def do_run_upload_init():
        body = request.get_json(silent=True) or {}
        res = actions.upload_init(cfg, body.get("filename", ""), int(body.get("size") or 0), body.get("sha256", ""))
        if not res.ok:
            return jsonify({"ok": False, "error": res.error}), 400
        return jsonify({"ok": True, **(res.detail or {})})

    @app.put("/run/upload/chunk")
    def do_run_upload_chunk():
        upload_id = request.args.get("upload_id", "")
        try: offset = int(request.args.get("offset") or -1)
        except ValueError: offset = -1
        res = actions.upload_chunk(cfg, upload_id, offset, request.get_data())
        if not res.ok and res.detail and "received" in res.detail:
            return jsonify(res.detail), 409
        if not res.ok:
            return jsonify({"ok": False, "error": res.error}), 400
        return jsonify({"ok": True, **(res.detail or {})})

    @app.post("/run/upload/finalize")
    def do_run_upload_finalize():
        burn_subs = False if request.form.get("no_subs") else None
        speech_trust = True if request.form.get("speech_trust") else None
        return _run_panel(actions.upload_finalize(cfg, request.form.get("upload_id", ""),
                                                   batch_name=request.form.get("batch_name", ""),
                                                   target_accounts=request.form.getlist("target_accounts"),
                                                   burn_subs=burn_subs, speech_trust=speech_trust))

    @app.post("/run/resume")
    def do_run_resume():
        # MOL-123: Resume an errored / moments_empty source from the Run tab. Goes through the same
        # stage-aware helper as the CLI (pipeline.resume_source via actions.resume_source_studio), then
        # re-renders the panel so the recovered source drops off the errored list in place.
        return _run_panel(actions.resume_source_studio(cfg, request.form.get("source_id", ""),
                                                       from_stage=request.form.get("from_stage") or "auto",
                                                       force=bool(request.form.get("force"))))

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

    @app.post("/run/bind-queue")
    def do_bind_queue():
        burn_subs = False if request.form.get("no_subs") else None
        speech_trust = True if request.form.get("speech_trust") else None
        return _run_panel(actions.bind_queue(cfg, source_ids=request.form.getlist("source_ids"),
                                             batch_name=request.form.get("batch_name", ""),
                                             target_accounts=request.form.getlist("target_accounts"),
                                             burn_subs=burn_subs, speech_trust=speech_trust))

    @app.post("/run/release-batch")
    def do_release_batch():
        return _run_panel(actions.release_batch(cfg, request.form.get("batch_id", ""),
                                                confirmed=bool(request.form.get("confirm"))))

    @app.post("/run/release-all")
    def do_release_all():
        return _run_panel(actions.release_all_held(cfg, confirmed=bool(request.form.get("confirm"))))

    @app.get("/library")
    def library():
        # M1 asset memory: every Source the system remembers, split native vs third-party. U13: ?view=live
        # is the folded Live-library lens (the mirrored-from-IG media, "viewed there, not authored here"),
        # reusing the SAME read-models the retired /live-library page used. No ?view= (or any other value) ->
        # the byte-identical asset catalog.
        if request.args.get("view") == "live":
            led = Ledger.load(cfg)
            return render_template("library.html", view="live", catalog=views.library_catalog(cfg),
                                   rows=views.live_library(led, cfg), scope=views.live_library_scope(cfg),
                                   confirm_word=actions_wipe.CONFIRM_WORD, preview=None, wipe_result=None,
                                   tab="library")
        return render_template("library.html", catalog=views.library_catalog(cfg), tab="library")

    @app.get("/library/<source_id>")
    def library_source(source_id):
        from flask import abort
        from fanops.studio.app import _offset_arg
        detail = views.source_pipeline_map(cfg, source_id, offset=_offset_arg())
        if detail is None:
            abort(404)
        return render_template("library_source.html", detail=detail, tab="library")

    @app.get("/library/<source_id>/live")
    def library_source_live(source_id):
        from flask import abort
        from fanops.studio.app import _offset_arg
        detail = views.source_pipeline_map(cfg, source_id, offset=_offset_arg())
        if detail is None:
            abort(404)
        return render_template("_library_source_live.html", detail=detail, tab="library")

    @app.post("/library/upload")
    def do_thirdparty_upload():
        # Validate + land third-party assets (peer staging dir), then catalogue them INERT — only if the
        # save succeeded (a fully-rejected upload surfaces the save error, never a misleading "0 added").
        res = actions.save_thirdparty_uploads(cfg, request.files.getlist("files"))
        if res.ok:
            res = actions.run_ingest_thirdparty(cfg)
        return render_template("_library_panel.html", catalog=views.library_catalog(cfg), result=res, tab="library")

    def _library_panel(result=None):
        return render_template("_library_panel.html", catalog=views.library_catalog(cfg), result=result, tab="library")

    @app.post("/library/resume")
    def do_library_resume():
        res = actions.resume_source_studio(cfg, request.form.get("source_id", ""),
                                           from_stage=request.form.get("from_stage") or "auto",
                                           force=bool(request.form.get("force")))
        return _library_panel(res)

    @app.post("/library/retire")
    def do_library_retire():
        return _library_panel(actions.retire_source_studio(cfg, request.form.get("source_id", "")))

    @app.post("/library/promote")
    def do_library_promote():
        return _library_panel(actions.promote_source_studio(cfg, request.form.get("source_id", "")))

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        # An over-MAX_CONTENT_LENGTH upload: Werkzeug raised 413 before do_run_upload ran. Re-render the
        # Run panel with a clean "too large" message at HTTP 200 — htmx 2.0.3 only swaps 2xx bodies, so a
        # 413 panel would be silently dropped and the operator would see nothing. The cap is enforced by
        # Werkzeug regardless of this status; only the friendly response's status changes.
        mb = (app.config["MAX_CONTENT_LENGTH"] or 0) // (1024 * 1024)
        return _run_panel(actions.ActionResult(ok=False, error=f"file too large — the upload cap is {mb} MB"))
