"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Platform
from fanops.discover import make_thumbnail        # reuse the cheap one-frame ffmpeg extractor for clip posters
from fanops.studio import views, actions
from fanops.personas import lever_catalog        # the code-derived lever catalog (every option + its real effect)
from fanops.timeutil import local_input_to_utc_z, to_local_display, to_local_display_hybrid, to_local_input  # local-time rendering at the web boundary

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


def _bounded(cfg: Config, candidate) -> Path | None:
    """Require a servable path to resolve INSIDE cfg.base (the FanOps data tree). Ledger paths are
    trusted in normal operation, but a hand-edited/corrupt ledger must not turn the localhost
    cockpit into an arbitrary-file server (stage-5/6 audit) — anything else is a 404, not a serve."""
    if not candidate:
        return None
    p = Path(candidate).resolve()
    return p if p.is_relative_to(cfg.base.resolve()) else None


def _media_path_for_post(led: Ledger, post_id: str):
    """Resolve the local file to serve for a post — a pure lookup, no guessing (the Render foundation
    killed the old 3-way heuristic that silently served a textless base):
      1. post.render_id -> the per-account Render's path (THE authoritative per-account artifact);
      2. else media_urls[0] when it is a local file:// / bare path (legacy pre-Render rows; resilient if
         a Render entity was swept but its file remains — media_urls still points at the same path);
      3. else the shared base clip.path (a hookless surface legitimately ships the base).
    An http(s) media_urls (an already-published URL) is NOT locally servable -> fall through. The id is a
    dict-key lookup and every path comes from the trusted ledger (never the URL), so no path traversal.
    The route 404s when the resolved path does not exist (a missing render surfaces, never a silent swap)."""
    post = led.posts.get(post_id)
    if post is None:
        return None
    if post.render_id:
        r = led.renders.get(post.render_id)
        if r is not None:
            return r.path                  # per-account render — the authoritative file for this surface
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
    if kind == "moment_casting":
        from fanops.models import validate_account_handle
        selections: dict[str, list[str]] = {}
        for k in form:
            if not k.startswith("cast__"):
                continue
            parts = k.split("__", 2)
            if len(parts) != 3:
                continue
            _, handle, mid = parts
            try:
                handle = validate_account_handle(handle)
            except ValueError:
                handle = (handle or "").strip().lstrip("@").lower()
            if form.get(k):
                selections.setdefault(handle, []).append(mid)
        return {"selections": selections}

    return {}


# ---- stateless request/render helpers (lifted out of create_app: they close over request + module
#      imports only, never cfg/app, so the route-group modules can import them directly). ----
def _time_arg() -> str:
    # The datetime-local control submits naive LOCAL; convert to canonical UTC before the action sees it.
    # A Z/offset value passes through normalized; garbage passes through so reschedule_post raises 'bad time'.
    return local_input_to_utc_z(request.form.get("new_time", ""))

def _offset_arg() -> int:
    # The grid show-more offset from ?offset=. A garbage/negative value -> 0 (paginate clamps too),
    # so a hand-typed URL can never 500 the grid.
    try:
        return max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0

def _account_arg():
    # P5: the per-account filter from ?account=. A blank/absent param -> None (the unfiltered "All"
    # view); read from request.args, so an htmx POST that carries account= in its action URL re-applies
    # the same scope after a mutation (R1). Never raises; an unknown handle simply matches zero rows.
    # @-agnostic: operators may type @handle while accounts.json/ledger use bare handles.
    v = (request.args.get("account") or "").strip()
    if not v:
        return None
    try:
        from flask import current_app
        cfg = current_app.config.get("FANOPS_CFG")
        if cfg:
            return views.resolve_account_handle(v, cfg)
    except Exception:
        logger.warning("account handle resolution failed (fail-open, using raw handle)", exc_info=True)
    return v

def _batch_arg():
    # Face 4 follow-up (B2): drill into ONE batch from ?batch=<Batch.id> (content-addressed id, NOT name —
    # names aren't unique). Mirrors _account_arg: blank/absent -> None (unfiltered); read from request.args so
    # an htmx POST carrying batch= in its action URL re-applies the same scope after a mutation (R1). Review-
    # local (NOT injected into the cross-tab nav like account) — a batch id is meaningless on other tabs.
    v = (request.args.get("batch") or "").strip()
    return v or None


def _delivery_arg():
    v = (request.args.get("delivery") or "").strip().lower()
    return v or None

def _failure_arg():
    v = (request.args.get("failure") or "").strip().lower()
    return v or None

def _compact_arg() -> bool:
    # M3c: the dense, video-less Review list mode from ?compact=. Read from request.args so it rides the
    # action/pagination URLs (templates carry compact=1) AND the htmx POST URL — so a mutation re-render
    # stays compact (R1). Truthy-words only; absent/blank/anything else -> False (the full video view).
    # Phase 4: ?compact=ultra is ALSO truthy here (so the compact code paths still fire) AND flips _ultra_arg.
    v = (request.args.get("compact") or "").strip().lower()
    return v in ("1", "true", "yes", "on", "ultra")

def _ultra_arg() -> bool:
    # Phase 4: the TRUE ultra-compact (zero-<video>, DOM-light) pivot mode from ?compact=ultra. The win at
    # 150 surfaces is the ELEMENT COUNT (one row per surface, no <video>, no poster fetch), not just preload.
    # Read from request.args so it rides the action/pagination URLs (R1). Anything but the exact word -> False.
    return (request.args.get("compact") or "").strip().lower() == "ultra"

def _source_arg():
    # Phase 4: the per-source filter from ?source=<Source.id>. Mirrors _account_arg/_batch_arg — blank/absent
    # -> None (unfiltered); read from request.args so an htmx POST carrying source= re-applies scope (R1).
    # Keyed on the STABLE source id (NOT the basename — two sources can share a filename); never raises.
    v = (request.args.get("source") or "").strip()
    return v or None

def _state_arg():
    # Phase 4: the per-state filter from ?state=. VALIDATED against the legal set (views._STATE_TO_BUCKET) —
    # an unknown word maps to None (the unfiltered view), so a hand-typed URL never 500s. Blank/absent -> None.
    v = (request.args.get("state") or "").strip().lower()
    return v if v in views._STATE_TO_BUCKET else None

def _focus_arg() -> bool:
    return (request.args.get("focus") or "").strip().lower() in ("1", "true", "yes", "on")

def _focus_idx_arg() -> int:
    try:
        return max(0, int(request.args.get("fi", 0)))
    except (TypeError, ValueError):
        return 0

def _view_arg():
    # Slice 2: the Review view mode from ?view=. 'list' -> the legacy moment-first cards; 'account' -> the
    # account-first PIVOT (one account's run as a flat list); 'lanes' (RF6) -> the account-first per-account
    # lanes; 'matrix' (or absent/unknown) -> the DEFAULT moment×account matrix. Read from request.args so it
    # rides the action/pagination URLs (R1).
    v = (request.args.get("view") or "").strip().lower()
    return v if v in ("account", "list", "matrix", "lanes") else None   # RF6: 'lanes' = the account-first per-account lanes

def _with_active(counts, active):
    # The chip UNIVERSE = the accounts present in the (unfiltered) list, PLUS the active filter itself, so
    # an account whose last item just left the list still shows its (active) chip — the filter stays
    # visible + recoverable ("No work for @a — clear the filter") instead of silently vanishing.
    accts = set(counts)
    if active: accts.add(active)
    return sorted(accts)

def _row_chips(rows, route, active):
    # Chip context for a row/dict-based surface: the distinct account UNIVERSE + per-account counts,
    # derived from the POSTS in this list (never accounts.json — a retired account's history stays
    # filterable). Splatted into render_template; the _account_filter.html include reads these.
    counts = Counter((r["account"] if isinstance(r, dict) else r.account) for r in rows)
    return {"chip_accounts": _with_active(counts, active), "chip_counts": dict(counts),
            "chip_route": route, "chip_total": len(rows), "active": active}

def _card_chips(cards, active):
    # Chip context for Review (cards have no scalar account — collect surface accounts; a fan-out card
    # contributes to each surface's account). chip_total counts cards, the count map counts surfaces.
    counts = Counter(s.account for c in cards for s in c.surfaces)
    return {"chip_accounts": _with_active(counts, active), "chip_counts": dict(counts),
            "chip_route": "review", "chip_total": len(cards), "active": active}

def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder=str(_HERE / "templates"), static_folder=str(_HERE / "static"))
    app.config["FANOPS_CFG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = cfg.upload_max_bytes    # ING-8: configurable upload ceiling (FANOPS_UPLOAD_MAX_MB); Werkzeug 413s an oversize body before the view runs
    # Stored times are canonical UTC; render them in the operator's local tz. `localdt` -> friendly display,
    # `localinput` -> the naive-local value an <input type=datetime-local> edits. (Inverse: _time_arg below.)
    # Both return "" on None/absent/garbage, so a display cell reads `{{ t | localdt or '—' }}` (filter binds
    # tighter than `or` in Jinja, so the dash is the fallback for an empty/missing time).
    app.jinja_env.filters["localdt"] = to_local_display
    app.jinja_env.filters["localdt_hybrid"] = to_local_display_hybrid   # T-17/D-02: absolute leads + relative parenthetical (Schedule + Lift)
    app.jinja_env.filters["localinput"] = to_local_input
    # Face 4: group the editable Review cards by their REAL Batch (Post.batch_id) for collapsible
    # per-batch <details> sections. Pure read-model helper (views), exposed as a filter so the
    # already-paginated card slice is grouped at render time without threading it through every route.
    app.jinja_env.filters["group_review_by_batch"] = views.group_review_by_batch
    app.jinja_env.filters["group_schedule_by_account"] = views.group_schedule_by_account
    # Phase 4: the account-first PIVOT grouper — group one account's flat SurfacePost rows by ingest day for a
    # running day header, exposed as a filter so the already-paginated row slice is grouped at render time.
    app.jinja_env.filters["group_review_by_account_surface"] = views.group_review_by_account_surface
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
        # M3d: inject creative_variation GLOBALLY so Review can hide the moment-hook RESTORE choice while ON
        # (per-surface hooks own the burn then, and approve_with_hook refuses) — the choice is an OFF-mode flow.
        # Phase 4: inject the Review-scoped filters/modes GLOBALLY (like compact) so _review_body.html + its
        # includes (_card.html, _account_pivot.html, _account_filter.html) all see them without per-render
        # plumbing, and url_for(..., source=active_source|default(none), ...) drops each one where it's off ->
        # byte-identical everywhere it isn't set. active_source/active_state/active_view/ultra all None/False
        # by default (a non-Review surface / a partial swap with no request args) -> url_for drops them.
        # M5: inject `cfg` globally so templates can read cfg.is_live for the Posted-tab system-mode banner
        # (and any future banner that surfaces system state). Single source of truth — never recomputed
        # per surface, never out of sync with the running deployment's live/dryrun state.
        return {"nav_account": _account_arg(), "review_nav": views.review_nav_params(cfg, _account_arg()), "compact": _compact_arg(),
                "active_source": _source_arg(), "active_state": _state_arg(),
                "active_view": _view_arg(), "ultra": _ultra_arg(),
                "creative_variation": False,
                "cast_state": {"casting": cfg.account_casting, "profile": cfg.clip_profile},
                "cfg": cfg}

    @app.context_processor
    def _inject_system_strip():
        return {"system_strip": views.build_system_strip(cfg)}

    @app.context_processor
    def _inject_account_session():
        acct = _account_arg()
        if not acct:
            return {}
        wc = views.account_work_counts(cfg).get(acct, {"awaiting": 0, "scheduled": 0, "failed": 0, "inflight": 0})
        return {"account_session": {"handle": acct, **wc}}

    @app.context_processor
    def _inject_inflight_watch():
        if request.endpoint not in _INFLIGHT_SURFACES:
            return {}
        try:
            led = Ledger.load(cfg)
            acct = _account_arg()
            return {"inflight_watch": views.inflight_watch(led, cfg, account=acct)}
        except Exception:
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
        st = views.home_status(cfg)
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
                                            blocked_gates=strip.get("blocked_gates", 0), next_params=np)}

    @app.get("/")
    def index():
        # Face 2: a real read-only status home (accounts + connection + headline counts + batch entry + per-
        # account post counts), NOT a redirect. nav_account is injected globally (Face 4 spine); no chip context
        # here (Home renders no per-surface chip row — the chip universe is a per-tab concern).
        return render_template("home.html", status=views.home_status(cfg), batches=views.home_batches(cfg), work_by_account=views.account_work_counts(cfg), review_handoff=views.review_handoff(cfg), zero_post_clips=views.zero_post_clips(cfg), tab="home")

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
        # exit-127 unseen. Fail-open: daemon_health is None on non-darwin/launchctl-absent -> empty partial.
        return render_template("_daemon_health.html", daemon=views.daemon_health(cfg))

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

    @app.get("/media/<post_id>")
    def media(post_id):
        path = _bounded(cfg, _media_path_for_post(Ledger.load(cfg), post_id))
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/media-preview/<post_id>")
    def media_preview(post_id):
        from fanops.studio.preview_media import preview_media_path
        path = _bounded(cfg, preview_media_path(cfg, Ledger.load(cfg), post_id))
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

    @app.get("/clip-thumb/<clip_id>")
    def clip_thumb(clip_id):
        # A cached JPEG first-frame for a clip, so the grid's <video preload="none"> shows a real
        # frame (poster=) instead of a black box. Mirrors clip_media's ledger-resolve + _bounded
        # path-safety; reuses discover.make_thumbnail (one ffmpeg frame). FAIL-OPEN: a missing clip,
        # a vanished file, or ffmpeg absent/failing is a 404, never a 500 — the player just shows its
        # own blank box, exactly as before, and the operator can still click to load the video.
        if "/" in clip_id or "\\" in clip_id or ".." in clip_id:  # bare id only — mirror review_thumb's guard
            abort(404)
        clip = Ledger.load(cfg).clips.get(clip_id)
        src = _bounded(cfg, clip.path if clip else None)
        if not src or not os.path.exists(src):
            abort(404)
        cache = _bounded(cfg, cfg.clips / f"{clip_id}.jpg")   # cache next to the clip, inside cfg.base
        if cache is None:
            abort(404)
        # Cache is FRESH only if it exists, is non-empty, AND is at least as new as the clip mp4. A
        # re-rendered clip (new burned hook, SAME clip_id) bumps the mp4 mtime, so a poster older than
        # the mp4 is stale and must be re-extracted — otherwise the cockpit shows the OLD hook forever.
        fresh = (cache.exists() and cache.stat().st_size > 0
                 and cache.stat().st_mtime >= os.path.getmtime(src))
        if not fresh:                                         # absent / 0-byte partial / older than the clip -> (re)extract
            if not make_thumbnail(src, cache, at_seconds=0.5) or cache.stat().st_size == 0:
                abort(404)                                    # ffmpeg missing/failed/empty -> fail-open
        return send_file(cache, mimetype="image/jpeg")

    # ── A2: the Personas page — personas become editable/addable/connectable in the browser ───────────
    from fanops.studio.app_routes_personas import register_personas_routes
    register_personas_routes(app, cfg)


    from fanops.studio.app_routes_golive import register_golive_routes
    register_golive_routes(app, cfg)


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
