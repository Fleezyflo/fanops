"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.studio import views, actions

_HERE = Path(__file__).resolve().parent


def _bounded(cfg: Config, candidate) -> Path | None:
    """Require a servable path to resolve INSIDE cfg.base (the FanOps data tree). Ledger paths are
    trusted in normal operation, but a hand-edited/corrupt ledger must not turn the localhost
    cockpit into an arbitrary-file server (stage-5/6 audit) — anything else is a 404, not a serve."""
    if not candidate:
        return None
    p = Path(candidate).resolve()
    return p if p.is_relative_to(cfg.base.resolve()) else None


def _media_path_for_post(led: Ledger, post_id: str):
    """Resolve the local file to serve for a post: the variant overlay (media_urls[0], stripped of
    file://) when it is a local file, else the base clip path. Returns None if nothing resolvable.
    The id is only a dict-key lookup and the path comes from the trusted ledger (never the URL), so
    there is no path traversal."""
    post = led.posts.get(post_id)
    if post is None:
        return None
    candidate = None
    if post.media_urls:
        raw = post.media_urls[0]
        if raw.startswith("file://"):
            candidate = raw[len("file://"):]
        elif not raw.startswith(("http://", "https://")):
            candidate = raw            # a bare local path
        # http(s) publicUrl -> not locally servable; fall through to base clip
    if candidate is None:
        clip = led.clips.get(post.parent_id)
        candidate = clip.path if clip else None
    return candidate


def _parse_gate_form(kind: str, form) -> dict:
    """Map the Gates-tab form into answer_gate's data shape. Values stay strings — Pydantic coerces
    and validates (a non-numeric timestamp surfaces as a clean ActionResult error, never a 500)."""
    if kind == "captions":
        items = []
        for k in form:
            if not k.startswith("caption__"):
                continue
            surface = k[len("caption__"):]
            cap = (form.get(k) or "").strip()
            if not cap:
                continue                            # an empty surface caption is simply not submitted
            item = {"surface": surface, "caption": cap}
            for fld in ("language", "hook"):
                v = (form.get(f"{fld}__{surface}") or "").strip()
                if v:
                    item[fld] = v
            items.append(item)
        return {"items": items}
    if kind == "moments":
        picks = []
        for s, e, r in zip(form.getlist("pick_start"), form.getlist("pick_end"), form.getlist("pick_reason")):
            if not (s or e or r):
                continue                            # skip blank rows
            picks.append({"start": s, "end": e, "reason": r})
        return {"picks": picks}
    return {}


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder=str(_HERE / "templates"), static_folder=str(_HERE / "static"))

    @app.get("/")
    def index():
        return redirect(url_for("review"))

    @app.get("/review")
    def review():
        led = Ledger.load(cfg)
        accounts = Accounts.load(cfg)
        cards = views.review_buckets(led, accounts, cfg, now=datetime.now(timezone.utc))
        return render_template("review.html", cards=cards, tab="review")

    @app.get("/schedule")
    def schedule():
        led = Ledger.load(cfg)
        rows = views.schedule_rows(led, cfg, now=datetime.now(timezone.utc))
        return render_template("schedule.html", rows=rows, tab="schedule")

    @app.get("/lift")
    def lift():
        led = Ledger.load(cfg)
        view = views.lift_rows(led, cfg, Accounts.load(cfg))
        return render_template("lift.html", view=view, tab="lift")

    @app.get("/run")
    def run_panel():
        # The pipeline DRIVER: ingest/pull/advance from the browser so the operator never needs the
        # terminal. Read-only status; the actions below go through the same lock-safe paths as the CLI.
        return render_template("run.html", status=views.pipeline_status(cfg), tab="run")

    def _run_panel(result):
        # Re-render the panel partial with FRESH status after an action (htmx swaps #run-panel), so the
        # counts update in place — drop files, click ingest, watch sources tick up, no page reload.
        return render_template("_run_panel.html", status=views.pipeline_status(cfg), result=result, tab="run")

    @app.post("/run/ingest")
    def do_run_ingest():
        return _run_panel(actions.run_ingest(cfg))

    @app.post("/run/pull")
    def do_run_pull():
        return _run_panel(actions.run_pull(cfg, request.form.get("url", "")))

    @app.post("/run/advance")
    def do_run_advance():
        # confirm derived from the checkbox the template shows ONLY on a live backend (Track C guard).
        return _run_panel(actions.run_advance(cfg, request.form.get("base_time") or None,
                                              confirmed=bool(request.form.get("confirm"))))

    @app.post("/run/prepare")
    def do_run_prepare():
        # Auto-prepare: answer the gates (via the responder) + advance until stable, so the operator
        # never hand-writes a caption. Same live-publish confirm checkbox as advance.
        return _run_panel(actions.run_prepare(cfg, request.form.get("base_time") or None,
                                              confirmed=bool(request.form.get("confirm"))))

    @app.get("/candidates")
    def candidates():
        # Track C: approve discover footage in the browser (replaces the Finder drag into approved/).
        return render_template("candidates.html", rows=views.review_candidates(cfg), tab="footage")

    @app.post("/candidates/approve/<eid>")
    def do_approve_candidate(eid):
        return render_template("_result.html", result=actions.approve_candidate(cfg, eid))

    @app.get("/review-thumb/<eid>")
    def review_thumb(eid):
        if "/" in eid or "\\" in eid or ".." in eid:     # bare stem only — no traversal
            abort(404)
        path = _bounded(cfg, cfg.review / f"{eid}.jpg")  # must resolve inside cfg.base
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/publish")
    def publish_panel():
        # Track B: the manual / no-service worklist — queued posts to post by hand, with the clip to
        # download (/media/<post_id>) + the caption to copy + a "Mark posted" button.
        return render_template("publish.html",
                               rows=views.publish_queue(cfg, now=datetime.now(timezone.utc)), tab="publish")

    @app.post("/publish/posted/<post_id>")
    def do_mark_posted(post_id):
        return render_template("_result.html",
                               result=actions.mark_published(cfg, post_id, request.form.get("url") or None))

    @app.get("/gates")
    def gates():
        # Phase 3a: the moment/caption agent gates — the actual product decisions — answerable from
        # the browser instead of hand-editing 04_agent_io JSON. Lock-free read like the other tabs.
        return render_template("gates.html", rows=views.gate_rows(cfg), tab="gates")

    @app.post("/gates/answer/<kind>/<key>")
    def do_answer_gate(kind, key):
        result = actions.answer_gate(cfg, kind, key, _parse_gate_form(kind, request.form))
        return render_template("_result.html", result=result)

    @app.get("/media/<post_id>")
    def media(post_id):
        path = _bounded(cfg, _media_path_for_post(Ledger.load(cfg), post_id))
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/clips/<clip_id>")
    def clip_media(clip_id):
        clip = Ledger.load(cfg).clips.get(clip_id)
        path = _bounded(cfg, clip.path if clip else None)
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.post("/reschedule/<post_id>")
    def do_reschedule(post_id):
        result = actions.reschedule_post(cfg, post_id, request.form.get("new_time", ""))
        return render_template("_result.html", result=result)

    @app.post("/caption/<post_id>")
    def do_caption(post_id):
        result = actions.edit_caption(cfg, post_id, request.form.get("caption", ""))
        return render_template("_result.html", result=result)

    @app.post("/snooze/<clip_id>")
    def do_snooze(clip_id):
        result = actions.snooze_clip(cfg, clip_id)
        return render_template("_result.html", result=result)

    return app
