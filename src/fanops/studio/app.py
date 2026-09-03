"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template, request

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Platform
from fanops.studio import views, actions
from fanops.studio.app_request import (
    _account_arg,
    _compact_arg,
    _offset_arg,
    _row_chips,
    _source_arg,
    _state_arg,
    _ultra_arg,
    _view_arg,
)
from fanops.personas import lever_catalog        # the code-derived lever catalog (every option + its real effect)
from fanops.timeutil import to_local_display, to_local_display_hybrid, to_local_input  # local-time rendering at the web boundary

logger = logging.getLogger(__name__)

_ALL_PLATFORMS = [p.value for p in Platform]    # the add-account form's platform checkboxes (no enum drift)
# Lever exposure for the Personas tab — ALL sourced from personas.lever_catalog() so the option lists, their
# effects, and the reference never drift from the engine. `_LEVERS` keeps the macro's keyed option lists,
# `_LEVER_EFFECTS` maps each option to its engine-true effect (rendered next to the control), `_LEVER_REF` is
# the ordered catalog (the "what the levers are" reference). Computed once (pure).
_CATALOG = lever_catalog()
_LEVERS = {lv["key"]: [o["value"] for o in lv["options"]] for lv in _CATALOG if lv["options"]}
_LEVER_EFFECTS = {lv["key"]: {o["value"]: o["effect"] for o in lv["options"]} for lv in _CATALOG}
_LEVER_REF = _CATALOG

# Slice 1: which endpoints carry the workflow spine, mapping each to its stage key ('here'). `index` shows the
# stepper with no active stage (None). Everything else (Setup/Insights/htmx partials/404) is skipped via the
# sentinel — None is a real value here (Home), so it cannot double as "not a workflow page".
_SPINE_SKIP = object()
_SPINE_HERE = {"index": None, "run_panel": "make", "review": "review", "schedule": "schedule", "posted": "posted"}
_INFLIGHT_SURFACES = set(_SPINE_HERE) | {"publish_panel"}

_HERE = Path(__file__).resolve().parent
_START_TIME = datetime.now(timezone.utc).isoformat()
_PID = os.getpid()
_GENERATION = os.environ.get("FANOPS_STUDIO_GENERATION")


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
        # MOL-109: strict= — three independently-populated form field lists; a desynced submission must
        # surface as a ValueError (handled by do_answer_gate as form validation), never silently truncate.
        for s, e, r in zip(form.getlist("pick_start"), form.getlist("pick_end"), form.getlist("pick_reason"),
                           strict=True):
            if not (s or e or r):
                continue                            # skip blank rows
            picks.append({"start": s, "end": e, "reason": r})
        return {"picks": picks}
    if kind == "moment_hooks":
        # M1b/P6: the manual frame-seeing hook answer — one shared hook (blank -> null -> clean clip).
        hook = (form.get("hook") or "").strip()
        return {"hook": hook or None}
    return {}


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder=str(_HERE / "templates"), static_folder=str(_HERE / "static"))
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["FANOPS_CFG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = cfg.upload_max_bytes    # ING-8: configurable upload ceiling (FANOPS_UPLOAD_MAX_MB); Werkzeug 413s an oversize body before the view runs
    # Stored times are canonical UTC; render them in the operator's local tz. `localdt` -> friendly display,
    # `localinput` -> the naive-local value an <input type=datetime-local> edits. (Inverse: _time_arg in app_request.)
    # Both return "" on None/absent/garbage, so a display cell reads `{{ t | localdt or '—' }}` (filter binds
    # tighter than `or` in Jinja, so the dash is the fallback for an empty/missing time).
    app.jinja_env.filters["localdt"] = lambda ts: to_local_display(ts, cfg=cfg)
    app.jinja_env.filters["localdt_hybrid"] = lambda ts: to_local_display_hybrid(ts, cfg=cfg, now=datetime.now(timezone.utc))
    app.jinja_env.filters["localinput"] = lambda ts: to_local_input(ts, cfg=cfg)
    # Face 4: group the editable Review cards by their REAL Batch (Post.batch_id) for collapsible
    # per-batch <details> sections. Pure read-model helper (views), exposed as a filter so the
    # already-paginated card slice is grouped at render time without threading it through every route.
    app.jinja_env.filters["group_review_by_batch"] = views.group_review_by_batch
    app.jinja_env.filters["group_review_by_source"] = views.group_review_by_source
    # S2/S4: the provenance projection (a surface -> ordered "value ← cause" ProvChips), exposed as a Jinja
    # GLOBAL so _card.html can render the surface-spec via the shared _prov cause_chip macro (one renderer, no
    # parallel hand-rolled chips). Pure + fail-open ([] for an undifferentiated surface -> the row stays absent).
    app.jinja_env.globals["provenance_chips"] = views.provenance_chips
    # S3: the Make tab's "do this next" projection (pipeline_status counts -> {key,label,hint}). A Jinja GLOBAL so
    # _run_next.html reads it off the `status` BOTH render paths already pass — no handler change. Pure + fail-open.
    app.jinja_env.globals["run_next_step"] = views.run_next_step
    # S6: proportional micro-bar width (value vs the column peak from metric_peaks). Jinja GLOBAL so the
    # Posted/Results tables read it directly off the `peaks` dict the routes pass. Pure + fail-safe.
    app.jinja_env.globals["bar_pct"] = views.bar_pct
    # S9: the plain-language glossary lookup. A Jinja GLOBAL (not a context processor) so the _term.html macro —
    # which is imported context-isolated via {% from %} — can resolve term_def() inside itself. Pure, fail-soft.
    app.jinja_env.globals["term_def"] = views.term_def
    app.jinja_env.filters["operator_error"] = views.operator_error
    app.jinja_env.filters["failure_label"] = views.failure_label


    @app.context_processor
    def _inject_nav_account():
        # Face 4 SPINE: the active ?account= filter, injected GLOBALLY so base.html's nav links carry it
        # across tabs (cross-tab persistence — pick @a on Review, it stays @a when you click Schedule) and the
        # header shows a clearable "Filtering @x" indicator. Nav-level propagation, NOT a chip-row relocation:
        # the per-surface chip rows + their R1 htmx-swap scope preservation + live counts are untouched. None
        # (no filter / a partial swap with no request args) -> url_for drops the param -> byte-identical nav.
        # Phase 2: also inject the read-only casting/volume state GLOBALLY so the Run + Review surfaces can
        # show how this run is configured (the levers live in Go-Live; their EFFECT was invisible elsewhere).
        # M3c: inject `compact` GLOBALLY so the Review templates + their shared includes (_card.html,
        # _account_filter.html) all see it without per-render plumbing, and url_for(..., compact=(1 if compact
        # else none)) drops the param everywhere it's off -> byte-identical on every non-compact / non-Review surface.
        # Phase 4: inject the Review-scoped filters/modes GLOBALLY (like compact) so _review_body.html + its
        # includes (_card.html, _account_pivot.html, _account_filter.html) all see them without per-render
        # plumbing, and url_for(..., source=active_source|default(none), ...) drops each one where it's off ->
        # byte-identical everywhere it isn't set. active_source/active_state/active_view/ultra all None/False
        # by default (a non-Review surface / a partial swap with no request args) -> url_for drops them.
        # M5: inject `cfg` globally so templates can read cfg.is_live for the Posted-tab system-mode banner
        # (and any future banner that surfaces system state). Single source of truth — never recomputed
        # per surface, never out of sync with the running deployment's live/dryrun state.
        acct = _account_arg()
        review_nav = {"view": "account", "focus": 1}
        if acct:
            review_nav["account"] = acct
        return {"nav_account": acct, "review_nav": review_nav, "compact": _compact_arg(),
                "active_source": _source_arg(), "active_state": _state_arg(),
                "active_view": _view_arg(), "ultra": _ultra_arg(),
                "cast_state": {"casting": cfg.account_casting},
                "cfg": cfg}

    @app.context_processor
    def _inject_system_strip():
        from flask import g
        strip = views.build_system_strip(cfg)
        g.fanops_system_strip = strip
        return {"system_strip": strip}

    @app.context_processor
    def _inject_account_session():
        acct = _account_arg()
        if not acct:
            return {}
        empty = {"awaiting": 0, "scheduled": 0, "failed": 0, "inflight": 0}
        if request.endpoint not in _INFLIGHT_SURFACES:
            return {"account_session": {"handle": acct, **empty}}
        wc = views.account_work_counts(cfg).get(acct, empty)
        return {"account_session": {"handle": acct, **wc}}

    @app.context_processor
    def _inject_inflight_watch():
        if request.endpoint not in _INFLIGHT_SURFACES:
            return {}
        try:
            led = Ledger.load(cfg)
            acct = _account_arg()
            return {"inflight_watch": views.inflight_watch(led, cfg, account=acct)}
        except Exception as exc:
            logger.warning("inflight_watch inject failed (empty watch): %s", exc)
            return {"inflight_watch": []}

    @app.context_processor
    def _inject_spine():
        # Slice 1: the workflow stepper (Make→Review→Schedule→Posted + one next-action CTA). Injected ONLY on the
        # workflow surfaces (Home + Make/Review/Schedule/Posted); every other endpoint returns {} so `spine` is
        # undefined and base.html renders nothing — no ledger read on Setup/Insights pages or htmx partial swaps.
        # `index` maps to here=None (the spine shows the path but highlights no stage); a non-workflow / None
        # endpoint (404, partial) hits the sentinel and is skipped. Reads home_status DIRECTLY (fail-open): this
        # runs during error-page renders too, so it must NOT depend on flask.g / a request memo (an app-context
        # access there 500s the error page). On Home that's one extra small lock-free counts read vs the route's —
        # accepted over fragility; the read is zeroed-not-raised on a torn ledger so the spine never 500s a surface.
        here = _SPINE_HERE.get(request.endpoint, _SPINE_SKIP)
        if here is _SPINE_SKIP:
            return {}
        from flask import g
        st = views.home_status(cfg)  # still direct — same fail-open rule as today
        strip = getattr(g, "fanops_system_strip", None)
        if strip is None:
            strip = views.build_system_strip(cfg)
        np: dict = {}
        if st.counts.get("awaiting", 0) > 0:
            np = views.review_nav_params(cfg, _account_arg())
        elif st.counts.get("failed", 0) > 0:
            np = {"delivery": "failed"}
        elif st.counts.get("inflight", 0) > 0:
            np = {"delivery": "inflight"}
        return {"spine": views.build_spine(counts=st.counts, has_accounts=bool(st.accounts), here=here,
                                            inflight=st.counts.get("inflight", 0),
                                            blocked_gates=strip.get("blocked_gates"),
                                            strip_metrics_unknown=bool(strip.get("strip_metrics_unknown")),
                                            next_params=np)}

    @app.get("/healthz")
    def healthz():
        """Liveness: process is up. Doctor / Go-Live keep build_health_report."""
        from flask import jsonify
        return jsonify({"ok": True}), 200

    @app.get("/_fingerprint")
    def fingerprint():
        """MOL-728: deployment freshness fingerprint. The managed-service lifecycle
        uses this to verify the resident was successfully cycled onto current code."""
        from flask import jsonify
        from fanops.cli import _running_code_sha
        return jsonify({
            "sha": _running_code_sha(cfg),
            "generation": _GENERATION,
            "pid": _PID,
            "start_time": _START_TIME,
            "label": cfg.root.name
        })

    @app.get("/metrics")
    def metrics():
        """MOL-357: Prometheus text metrics from ledger + HealthReport. Fail-open — never 500."""
        from flask import Response
        from fanops.health_model import render_prometheus_metrics
        return Response(render_prometheus_metrics(cfg), mimetype="text/plain; version=0.0.4; charset=utf-8")

    from fanops.studio.app_routes_home import register_home_routes
    register_home_routes(app, cfg)

    from fanops.studio.app_routes_review import register_review_routes
    register_review_routes(app, cfg)

    from fanops.studio.app_routes_schedule import register_schedule_routes
    register_schedule_routes(app, cfg)

    from fanops.studio.app_routes_run import register_run_routes
    register_run_routes(app, cfg)

    from fanops.studio.app_routes_live import register_live_routes   # MOL-27: the Live library (imported_media)
    register_live_routes(app, cfg)

    @app.get("/stitches")
    def stitches():
        # M3 approval spine + M4 release: suggestions awaiting approval AND rendered drafts awaiting release.
        return render_template("stitches.html", plans=views.pending_stitches(cfg),
                               drafts=views.pending_stitch_drafts(cfg), tab="stitches")

    def _stitches_panel(res):
        return render_template("_stitches_panel.html", plans=views.pending_stitches(cfg),
                               drafts=views.pending_stitch_drafts(cfg), result=res, tab="stitches")

    @app.post("/stitches/approve")
    def do_approve_stitches():
        return _stitches_panel(actions.approve_stitches(cfg, request.form.getlist("ids")))

    @app.post("/stitches/dismiss")
    def do_dismiss_stitches():
        return _stitches_panel(actions.dismiss_stitches(cfg, request.form.getlist("ids")))

    @app.post("/stitches/release")
    def do_release_stitches():
        # M4 second gate: promote a reviewed rendered stitch_draft clip -> captioned (crosspost-eligible).
        return _stitches_panel(actions.release_stitches(cfg, request.form.getlist("ids")))

    @app.get("/candidates")
    def candidates():
        # Track C: approve discover footage in the browser (replaces the Finder drag into approved/).
        return render_template("candidates.html", rows=views.review_candidates(cfg), tab="footage")

    @app.post("/candidates/approve/<eid>")
    def do_approve_candidate(eid):
        return render_template("_result.html", result=actions.approve_candidate(cfg, eid))

    from fanops.studio.app_routes_media import register_media_routes
    register_media_routes(app, cfg)

    @app.get("/publish")
    def publish_panel():
        # Track B: the manual / no-service worklist — queued posts to post by hand, with the clip to
        # download (/media/<post_id>) + the caption to copy + a "Mark posted" button. Capped to a page
        # (the 164-<video>-at-once perf problem); the total stays visible with a show-more link.
        account = _account_arg(); now = datetime.now(timezone.utc)
        rows_full = views.publish_queue(cfg, now=now)                                 # universe for chips
        rows = rows_full if account is None else views.publish_queue(cfg, now=now, account=account)
        page = views.paginate(rows, _offset_arg())
        return render_template("publish.html", rows=page.items, page=page, tab="publish",
                               # R3-followup UI-LIE-FIX: per-channel truth, not the legacy global.
                               backend=views._publish_mode_label(cfg),
                               **_row_chips(rows_full, "publish_panel", account))

    @app.post("/publish/posted/<post_id>")
    def do_mark_posted(post_id):
        return render_template("_result.html",
                               result=actions.mark_published(cfg, post_id, request.form.get("url") or None))

    @app.post("/publish/now/<post_id>")
    def do_publish_now(post_id):
        # Milestone 5 (publish in the UI): ship ONE reviewed post immediately via the same poster path
        # the pipeline uses — dryrun marks it published locally; a live backend posts (same confirm
        # checkbox as the Run actions). Ignores the post's future schedule (the operator clicked ship).
        return render_template("_result.html",
                               result=actions.publish_now(cfg, post_id, confirmed=bool(request.form.get("confirm"))))

    @app.get("/reconcile-strip")
    def reconcile_strip_partial():
        led = Ledger.load(cfg); acct = _account_arg()
        return render_template("_reconcile_strip.html", inflight_watch=views.inflight_watch(led, cfg, account=acct),
                               nav_account=acct, tab=request.args.get("tab", ""))

    @app.get("/gates")
    def gates():
        # Phase 3a: the moment/caption agent gates — the actual product decisions — answerable from
        # the browser instead of hand-editing 04_agent_io JSON. Lock-free read like the other tabs.
        return render_template("gates.html", rows=views.gate_rows(cfg), tab="gates")

    @app.post("/gates/answer/<kind>/<key>")
    def do_answer_gate(kind, key):
        try:
            data = _parse_gate_form(kind, request.form)
        except ValueError:
            # MOL-109: length-desynced pick triples (zip strict=True) — a FORM-VALIDATION error, so
            # re-render the result partial with a clear message at HTTP 200 (htmx 2.x drops non-2xx
            # swaps; mirrors the oversize-upload convention). Never a 500, never a silent truncation.
            return render_template("_result.html", result=actions.ActionResult.failure(
                "mismatched pick rows: start/end/reason field counts differ — reload the gate and retry"))
        return render_template("_result.html", result=actions.answer_gate(cfg, kind, key, data))

    @app.post("/gates/dismiss/<kind>/<key>")
    def do_dismiss_gate(kind, key):
        return render_template("_result.html", result=actions.dismiss_gate_studio(cfg, kind, key))

    # ── A2: the Personas page — personas become editable/addable/connectable in the browser ───────────
    from fanops.studio.app_routes_personas import register_personas_routes
    register_personas_routes(app, cfg)


    from fanops.studio.app_routes_golive import register_golive_routes
    register_golive_routes(app, cfg)


    # U11: the Hashtags observatory (corpora/store/budget/rotation + the global ban lane). Registered AFTER
    # personas so the corpora rows' "edit →" link (url_for('personas_view')) resolves.
    from fanops.studio.app_routes_hashtags import register_hashtags_routes
    register_hashtags_routes(app, cfg)


    from fanops.errors import ControlFileError
    @app.errorhandler(ControlFileError)
    def _control_file_error(e):
        # A malformed accounts.json/ledger.json raised ControlFileError from an unguarded Accounts.load/
        # Ledger.load in a route. Without this, EVERY tab 500s on one corrupt file (a PROVEN live failure).
        # Render a degraded, operator-actionable page at HTTP 200 — same htmx-swap-safe status as _too_large
        # (htmx 2.x drops non-2xx, so a 500 panel would vanish on a POST). The template is STANDALONE: it must
        # not touch ledger/accounts context, since loading that is what failed.
        return render_template("error.html", message=str(e)), 200

    return app
