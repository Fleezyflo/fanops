"""Home route group for the Studio: the operator dashboard + recovery actions.

register_home_routes(app, cfg) registers them under their ORIGINAL endpoint names
(url_for byte-identical); create_app calls it."""
from __future__ import annotations

from flask import render_template, request

from fanops.studio import actions, views


def register_home_routes(app, cfg):
    @app.get("/")
    def index():
        # U3: three operator panels (accounts, sources gallery, week-ahead calendar) + slim health line.
        return render_template("home.html", status=views.home_status(cfg),
                               accounts_panel=views.home_accounts_panel(cfg),
                               gallery=views.home_source_gallery(cfg, page=1),
                               calendar=views.home_week_calendar(cfg),
                               zero_post_clips=views.zero_post_clips(cfg), tab="home")

    @app.get("/home/gallery")
    def home_gallery():
        page = max(1, int(request.args.get("page", 1) or 1))
        return render_template("_home_gallery.html", gallery=views.home_source_gallery(cfg, page=page))

    @app.post("/home/pull-metrics")
    def do_home_pull_metrics():
        return render_template("_publish_outcome.html", result=actions.pull_metrics_studio(cfg), cfg=cfg)

    @app.post("/home/reconcile")
    def do_home_reconcile():
        return render_template("_publish_outcome.html", result=actions.reconcile_inflight(cfg), cfg=cfg)

    @app.post("/home/retry-rate-limit")
    def do_home_retry_rate_limit():
        return render_template("_publish_outcome.html", result=actions.retry_rate_limited_failures(cfg), cfg=cfg)

    @app.post("/home/retry-oversize")
    def do_home_retry_oversize():
        return render_template("_publish_outcome.html", result=actions.retry_oversize_failures(cfg), cfg=cfg)

    @app.get("/home/daemon-health")
    def home_daemon_health():
        # WS-D1 Phase 2: the launchd PIPELINE-DRIVER liveness banner, htmx-loaded on Home (mirrors
        # /golive/health) so a dead/stale driver surfaces where the operator looks instead of rotting
        # exit-127 unseen. Fail-open: daemon_health_strip is None when the snapshot is missing -> empty partial.
        return render_template("_daemon_health.html", daemon=views.daemon_health_strip(cfg))
