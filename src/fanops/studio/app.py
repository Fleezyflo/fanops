"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import Platform, PostState, LIFT_SCORE
from fanops.discover import make_thumbnail        # reuse the cheap one-frame ffmpeg extractor for clip posters
from fanops.studio import views, actions, golive
from fanops.studio import personas as studio_personas   # A2: the Personas-page actions (create/edit/connect)
from fanops.hashtags import TAG_LEANS            # the add-account lean picker options (no drift from the engine)
from fanops.personas import lever_catalog        # the code-derived lever catalog (every option + its real effect)
from fanops.timeutil import local_input_to_utc_z, to_local_display, to_local_input  # local-time rendering at the web boundary

_ALL_PLATFORMS = [p.value for p in Platform]    # the add-account form's platform checkboxes (no enum drift)
_TAG_LEANS = sorted(TAG_LEANS)                  # add-account lean picker options (sourced from the engine)
# Lever exposure for the Personas tab — ALL sourced from personas.lever_catalog() so the option lists, their
# effects, and the reference never drift from the engine. `_LEVERS` keeps the macro's keyed option lists,
# `_LEVER_EFFECTS` maps each option to its engine-true effect (rendered next to the control), `_LEVER_REF` is
# the ordered catalog (the "what the levers are" reference). Computed once (pure).
_CATALOG = lever_catalog()
_LEVERS = {lv["key"]: [o["value"] for o in lv["options"]] for lv in _CATALOG if lv["options"]}
_LEVER_EFFECTS = {lv["key"]: {o["value"]: o["effect"] for o in lv["options"]} for lv in _CATALOG}
_LEVER_REF = _CATALOG
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024      # 2 GiB upload cap — a long raw clip fits; an abusive body is refused (DoS)

# Slice 1: which endpoints carry the workflow spine, mapping each to its stage key ('here'). `index` shows the
# stepper with no active stage (None). Everything else (Setup/Insights/htmx partials/404) is skipped via the
# sentinel — None is a real value here (Home), so it cannot double as "not a workflow page".
_SPINE_SKIP = object()
_SPINE_HERE = {"index": None, "run_panel": "make", "review": "review", "schedule": "schedule", "posted": "posted"}

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
        for s, e, r in zip(form.getlist("pick_start"), form.getlist("pick_end"), form.getlist("pick_reason")):
            if not (s or e or r):
                continue                            # skip blank rows
            picks.append({"start": s, "end": e, "reason": r})
        return {"picks": picks}
    if kind == "moment_hooks":
        # M1b: the manual frame-seeing hook answer — one shared hook (blank -> null -> clean clip) plus
        # any per-account hooks the operator typed (persona_hook__<handle>). A blank persona field drops.
        hook = (form.get("hook") or "").strip()
        hbp = {k[len("persona_hook__"):]: v.strip() for k in form
               if k.startswith("persona_hook__") and (v := (form.get(k) or "")).strip()}
        return {"hook": hook or None, "hooks_by_persona": hbp}
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
    v = (request.args.get("account") or "").strip()
    return v or None

def _batch_arg():
    # Face 4 follow-up (B2): drill into ONE batch from ?batch=<Batch.id> (content-addressed id, NOT name —
    # names aren't unique). Mirrors _account_arg: blank/absent -> None (unfiltered); read from request.args so
    # an htmx POST carrying batch= in its action URL re-applies the same scope after a mutation (R1). Review-
    # local (NOT injected into the cross-tab nav like account) — a batch id is meaningless on other tabs.
    v = (request.args.get("batch") or "").strip()
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

def _view_arg():
    # Slice 2: the Review view mode from ?view=. 'list' -> the legacy moment-first cards; 'account' -> the
    # account-first PIVOT (one account's run as a flat list); 'matrix' (or absent/unknown) -> the DEFAULT
    # moment×account matrix. Read from request.args so it rides the action/pagination URLs (R1).
    v = (request.args.get("view") or "").strip().lower()
    return v if v in ("account", "list", "matrix") else None

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
    app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES    # Werkzeug refuses an oversize upload body BEFORE the view runs (413)
    # Stored times are canonical UTC; render them in the operator's local tz. `localdt` -> friendly display,
    # `localinput` -> the naive-local value an <input type=datetime-local> edits. (Inverse: _time_arg below.)
    # Both return "" on None/absent/garbage, so a display cell reads `{{ t | localdt or '—' }}` (filter binds
    # tighter than `or` in Jinja, so the dash is the fallback for an empty/missing time).
    app.jinja_env.filters["localdt"] = to_local_display
    app.jinja_env.filters["localinput"] = to_local_input
    # Face 4: group the editable Review cards by their REAL Batch (Post.batch_id) for collapsible
    # per-batch <details> sections. Pure read-model helper (views), exposed as a filter so the
    # already-paginated card slice is grouped at render time without threading it through every route.
    app.jinja_env.filters["group_review_by_batch"] = views.group_review_by_batch
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
        return {"nav_account": _account_arg(), "compact": _compact_arg(),
                "active_source": _source_arg(), "active_state": _state_arg(),
                "active_view": _view_arg(), "ultra": _ultra_arg(),
                "creative_variation": cfg.creative_variation,
                "cast_state": {"casting": cfg.account_casting,
                               "budget": cfg.cast_pick_budget, "profile": cfg.clip_profile}}

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
        return {"spine": views.build_spine(counts=st.counts, has_accounts=bool(st.accounts), here=here)}

    @app.get("/")
    def index():
        # Face 2: a real read-only status home (accounts + connection + headline counts + batch entry + per-
        # account post counts), NOT a redirect. nav_account is injected globally (Face 4 spine); no chip context
        # here (Home renders no per-surface chip row — the chip universe is a per-tab concern).
        return render_template("home.html", status=views.home_status(cfg), batches=views.home_batches(cfg), tab="home")

    def _review_context(*, result=None):
        # Phase 4: ONE builder for the Review render-kwargs, shared by the full page (review) AND the htmx swap
        # body (_review_panel) so the scope (account/batch/source/state), the pivot, the progress header, and the
        # pagination NEVER drift between the two. All four filters compose (P5 + B2 + Phase 4 source/state);
        # every arg defaults None so the unfiltered render is byte-identical. The pivot rows + progress are pure
        # reads over the SAME scoped cards, re-derived each swap so they ride the URL (R1).
        led = Ledger.load(cfg); accounts = Accounts.load(cfg); now = datetime.now(timezone.utc)
        account = _account_arg(); batch = _batch_arg(); source = _source_arg(); state = _state_arg()
        view = _view_arg()
        cards_full = views.review_buckets(led, accounts, cfg, now=now)               # universe for chips
        scoped = bool(account or batch or source or state)
        cards = (views.review_buckets(led, accounts, cfg, now=now, account=account, batch=batch,
                                      source=source, state=state) if scoped else cards_full)
        counts = views.review_counts(cards)              # counts reflect what's shown (the scoped worklist)
        progress = views.review_progress(cards)          # Phase 4 per-scope header (awaiting/approved/held/prepared)
        sources = views.source_universe(cards_full)      # Phase 4 source-filter chip universe (key, basename)
        # Phase 4 account-first pivot: only meaningful WITH an account; view=account but no account falls back to
        # the moment view (account_pivot_rows returns [] -> the body renders the cards path, never a 500).
        pivot_rows = (views.account_pivot_rows(led, accounts, cfg, now=now, account=account, batch=batch,
                                               source=source, state=state) if (view == "account" and account) else None)
        pivot = views.paginate(pivot_rows, _offset_arg()) if pivot_rows is not None else None
        page = views.paginate(cards, _offset_arg())
        # Slice 2: the moment×account MATRIX is the DEFAULT awaiting view (view absent/'matrix'); ?view=list is the
        # legacy-card escape, ?view=account the pivot. It renders ONE focused source — the ?source= filter doubles as
        # the picker; with no pick we focus the newest (source_choices[0]). Built only when it'll actually show (not
        # list, not the active pivot) and a source exists, so the empty install falls through to the guided card path.
        choices = views.source_choices(led)
        focused = source if source else (choices[0][0] if choices else None)
        show_matrix = view != "list" and not (view == "account" and account)
        matrix = (views.review_matrix(led, accounts, cfg, source_id=focused, now=now, state=(state or "awaiting"))
                  if (show_matrix and focused) else None)
        ctx = dict(cards=page.items, page=page, tab="review", backend=cfg.poster_backend, counts=counts,
                   awaiting_total=counts["awaiting"], active_batch=batch, progress=progress, sources=sources,
                   pivot=(pivot.items if pivot is not None else None), pivot_page=pivot, result=result,
                   matrix=matrix, source_choices=choices, focused_source=focused,
                   **_card_chips(cards_full, account))
        return ctx

    @app.get("/review")
    def review():
        ctx = _review_context()
        return render_template("review.html", shown=ctx["counts"]["awaiting"], **ctx)

    def _review_panel(result=None):
        # R1: account/batch/source/state/offset/view all ride the POST URL into request.args -> scope preserved.
        return render_template("_review_body.html", **_review_context(result=result))

    @app.get("/review/live")
    def review_live():
        # The Review tab's self-polling strip: live bucket counts + a 'load them' button when new
        # awaiting posts exceed what the worklist currently shows (?shown, read live from the body's
        # data-awaiting). A garbage/negative ?shown -> 0 (never a 500); the banner is gated on '>'.
        # P5: the strip counts the SAME per-account scope the body shows (else a filtered worklist's
        # scoped data-awaiting would forever trail the unscoped poll, pinning the 'new' banner open).
        led = Ledger.load(cfg); accounts = Accounts.load(cfg); account = _account_arg()
        cards = views.review_buckets(led, accounts, cfg, now=datetime.now(timezone.utc), account=account)
        counts = views.review_counts(cards)
        try:
            shown = max(0, int(request.args.get("shown", 0)))
        except (TypeError, ValueError):
            shown = 0
        return render_template("_review_live.html", counts=counts, shown=shown, active=account)

    @app.get("/review/refresh")
    def review_panel_refresh():
        return _review_panel()                           # GET, no mutation — the 'load them' button pulls a fresh worklist

    @app.post("/posts/approve")
    def do_approve_posts():
        # the human gate (multi-select): awaiting_approval -> queued; approved posts leave Review for the Schedule.
        return _review_panel(actions.approve_posts(cfg, request.form.getlist("ids")))

    @app.post("/posts/reject")
    def do_reject_posts():
        return _review_panel(actions.reject_posts(cfg, request.form.getlist("ids")))

    @app.post("/posts/unapprove/<post_id>")
    def do_unapprove_post(post_id):
        # send an approved-but-unsent post back to Review (the Schedule 'send back' control). Re-render the
        # Review worklist so the returned post is visible there again; surface any error (unknown post, etc.).
        return _review_panel(actions.unapprove_post(cfg, post_id))

    @app.post("/posts/approve-with-hook/<clip_id>")
    def do_approve_with_hook(clip_id):
        # removed-hook choice (slice 2): restore the auto-stripped hook, re-render so it burns, then approve
        # every awaiting post of this clip in ONE click. Re-render the Review worklist so the card leaves it.
        return _review_panel(actions.approve_with_hook(cfg, clip_id))

    @app.post("/posts/approve-as-is/<clip_id>")
    def do_approve_as_is(clip_id):
        # removed-hook choice (slice 2): ship the clip CLEAN — approve every awaiting post without restoring
        # the hook. One click per card; mirrors do_approve_with_hook's panel re-render.
        return _review_panel(actions.approve_as_is(cfg, clip_id))

    @app.post("/posts/approve-clip/<clip_id>")
    def do_approve_clip(clip_id):
        # M3b 'all accounts of this moment': approve every awaiting surface of ONE clip in one click (no hook
        # semantics — the generic per-card bulk approve). Re-render the worklist so the card leaves it.
        return _review_panel(actions.approve_clip(cfg, clip_id))

    @app.post("/posts/approve-account")
    def do_approve_account():
        # M3b/Phase 4 'this account across the whole video': approve every awaiting post of the ACTIVE account
        # filter (?account=), scoped to the active batch (?batch=) AND the active source (?source=). The target
        # IS the filter — the button only shows under an active account filter. Re-render stays scoped (R1) so the
        # now-empty view reflects the approve.
        return _review_panel(actions.approve_account(cfg, _account_arg(), batch=_batch_arg(), source=_source_arg()))

    @app.post("/posts/approve-moment/<moment_id>")
    def do_approve_moment(moment_id):
        # Matrix 'approve this whole moment-ROW': approve every awaiting post across all channels + clips of ONE
        # moment in one click (source-implicit — a moment uniquely identifies its source). Re-render stays scoped (R1).
        return _review_panel(actions.approve_moment(cfg, moment_id))

    @app.post("/posts/approve-channel")
    def do_approve_channel():
        # Matrix 'approve this whole channel-COLUMN': approve ONE (handle × platform) channel within the focused
        # source. The TARGET rides DISTINCT ch_* args so it never collides with the VIEW's account/source filter
        # (which drive the scope-stable re-render). GUARD: the column contract is "this channel within THIS source",
        # so a missing ch_account OR ch_source is REJECTED — never silently widened to approve_account's all-sources
        # path (a stale/replayed/hand-crafted POST must not sweep a sibling source), and never a misleading 0-count
        # success. The matrix template always bakes both, so the normal htmx UI never hits this guard.
        ch_account = request.args.get("ch_account") or ""
        ch_source = request.args.get("ch_source") or None
        if not ch_account or not ch_source:
            return _review_panel(actions.ActionResult(ok=False, error="Approve column needs a channel and its source."))
        return _review_panel(actions.approve_account(cfg, ch_account,
                             platform=(request.args.get("ch_platform") or None), source=ch_source))

    def _schedule_panel(result=None, *, full=False):
        led = Ledger.load(cfg); now = datetime.now(timezone.utc); account = _account_arg(); batch = _batch_arg()
        rows_full = views.schedule_rows(led, cfg, now=now)                            # universe for chips (account-only)
        rows = (views.schedule_rows(led, cfg, now=now, account=account, batch=batch)
                if (account or batch) else rows_full)
        approved_total = sum(1 for r in rows if r.editable)              # Face 5: full scoped count (pre-slice, page-safe banner)
        page = views.paginate(rows, _offset_arg())
        groups = views.group_schedule_by_account(page.items)            # regroup the SLICE (header re-emits across a page)
        tmpl = "schedule.html" if full else "_schedule_panel.html"
        return render_template(tmpl, rows=page.items, groups=groups, page=page, approved_total=approved_total,
                               active_batch=batch, result=result, tab="schedule",
                               backend=cfg.poster_backend, **_row_chips(rows_full, "schedule", account))

    @app.get("/schedule")
    def schedule():
        return _schedule_panel(full=True)

    @app.post("/schedule/respread")
    def do_reschedule_bucket():
        # routine re-spread of the approved bucket onto a fresh cadence from now.
        return _schedule_panel(actions.reschedule_bucket(cfg))

    @app.post("/schedule/unapprove/<post_id>")
    def do_schedule_unapprove(post_id):
        # send an approved post back to Review from the Schedule cockpit; re-render the bucket.
        return _schedule_panel(actions.unapprove_post(cfg, post_id))

    @app.post("/schedule/move/<post_id>")
    def do_schedule_move(post_id):
        # reschedule from the Schedule cockpit and re-render the WHOLE bucket so the row's time is fresh
        # (the shared /reschedule route returns only an inline result, leaving the time input stale).
        return _schedule_panel(actions.reschedule_post(cfg, post_id, _time_arg()))

    @app.post("/schedule/clear/<post_id>")
    def do_schedule_clear(post_id):
        # P1: clear the time on an approved (queued) post -> it goes back to awaiting_approval and LEAVES the
        # bucket; re-render the whole bucket (the #schedule-body outerHTML swap drops the now-absent row).
        return _schedule_panel(actions.clear_time(cfg, post_id))

    @app.post("/schedule/publish/<post_id>")
    def do_schedule_publish(post_id):
        # Phase 1 (bug fix): ship ONE approved post from the Schedule bucket via the SAME poster path as
        # /publish/now, then RE-RENDER the bucket so the shipped post (no longer queued) drops out of the
        # actionable list. Distinct from /publish/now (Publish tab), which returns a one-off result fragment
        # into a per-row span and left the shipped post stale in the bucket until a manual refresh.
        return _schedule_panel(actions.publish_now(cfg, post_id, confirmed=bool(request.form.get("confirm"))))

    @app.get("/lift")
    def lift():
        led = Ledger.load(cfg); accts = Accounts.load(cfg); account = _account_arg()
        view = views.lift_rows(led, cfg, accts, account=account)
        views.lineage_stats(view.variant_rows)            # S6: rank which hook won within each clip's lineage
        peaks = views.metric_peaks(view.variant_rows)     # S6: micro-bar normalisation over the shown variants
        # Chip universe from a CHEAP post scan (the same analyzed-variant predicate lift_rows uses), so we
        # call lift_rows ONCE — building an unfiltered view just for chips would re-run its per-row gate I/O.
        vcounts = Counter(p.account for p in led.posts.values()
                          if p.variant_key and p.state is PostState.analyzed and LIFT_SCORE in p.metrics)
        chips = {"chip_accounts": _with_active(vcounts, account), "chip_counts": dict(vcounts),
                 "chip_route": "lift", "chip_total": sum(vcounts.values()), "active": account}
        return render_template("lift.html", view=view, peaks=peaks, tab="lift", **chips)

    def _posted_panel(result=None, *, full=False):
        led = Ledger.load(cfg); account = _account_arg(); batch = _batch_arg()
        rows_full = views.posted_library(led, cfg)                                    # universe for chips (account-only)
        rows = (views.posted_library(led, cfg, account=account, batch=batch)
                if (account or batch) else rows_full)
        rollup = views.posted_batch_rollup(rows) if batch else None     # Face 5: full scoped (pre-slice) per-batch summary
        views.lineage_stats(rows)                         # S6: rank repost/crosspost siblings within the filtered set
        page = views.paginate(rows, _offset_arg())
        groups = views.group_posted_by_day(page.items)    # content-lifecycle Phase 3: publish-day buckets (over the slice)
        peaks = views.metric_peaks(rows)                  # S6: normalise micro-bars over the FULL filtered set (same
                                                          # denominator as lineage_stats) so a bar is a STABLE reference
                                                          # across pages — a saves=10 row reads the same width on any page
        accounts = Accounts.load(cfg).active()            # content-lifecycle Phase 4: cross-account picker options
        return render_template("posted.html" if full else "_posted_panel.html", rows=page.items, groups=groups,
                               page=page, rollup=rollup, peaks=peaks, active_batch=batch, accounts=accounts,
                               result=result, tab="posted", **_row_chips(rows_full, "posted", account))

    @app.get("/posted")
    def posted():
        return _posted_panel(full=True)

    @app.post("/posts/repost/<post_id>")
    def do_repost_post(post_id):
        # 'Post again': spawn a fresh awaiting_approval repost from a shipped post; re-render the library.
        return _posted_panel(actions.repost_post(cfg, post_id))

    @app.post("/posts/crosspost/<clip_id>")
    def do_crosspost_to_account(clip_id):
        # content-lifecycle Phase 4: mint an awaiting_approval post of this clip on another account/platform.
        return _posted_panel(actions.crosspost_to_account(
            cfg, clip_id, request.form.get("target_account", ""), request.form.get("platform", "")))

    @app.post("/posts/crosspost-all")
    def do_crosspost_all():
        # content-lifecycle Phase 4: bulk-backfill every clip posted to source_account onto target/platform.
        return _posted_panel(actions.crosspost_all_to_account(
            cfg, request.form.get("source_account", ""), request.form.get("target_account", ""),
            request.form.get("platform", "")))

    @app.get("/run")
    def run_panel():
        # The pipeline DRIVER: ingest/pull/advance from the browser so the operator never needs the
        # terminal. Read-only status; the actions below go through the same lock-safe paths as the CLI.
        return render_template("run.html", status=views.pipeline_status(cfg), tab="run")

    @app.get("/run/status")
    def run_status():
        # The Make tab's self-polling status counts — so a background run's progress shows live without
        # the operator clicking anything (swaps only #run-status, never the upload/add-link forms).
        return render_template("_run_status.html", status=views.pipeline_status(cfg))

    def _run_panel(result):
        # Re-render the panel partial with FRESH status after an action (htmx swaps #run-panel), so the
        # counts update in place — drop files, click ingest, watch sources tick up, no page reload.
        return render_template("_run_panel.html", status=views.pipeline_status(cfg), result=result, tab="run")

    @app.post("/run/ingest")
    def do_run_ingest():
        return _run_panel(actions.run_ingest(cfg, batch_name=request.form.get("batch_name", ""),
                                             target_accounts=request.form.getlist("target_accounts")))

    @app.post("/run/pull")
    def do_run_pull():
        return _run_panel(actions.run_pull(cfg, request.form.get("url", "")))

    @app.post("/run/upload")
    def do_run_upload():
        # Stream operator-uploaded raw video into 01_inbox AND catalogue it in one click (M5 auto-ingest)
        # — the browser replacement for a Finder drag + Ingest. save_uploads owns validation + atomic
        # os.replace; save_uploads_and_ingest chains the ingest pass; the panel re-renders with fresh
        # counts (htmx outerHTML). The manual "Ingest inbox" button stays for a re-ingest / failed retry.
        return _run_panel(actions.save_uploads_and_ingest(cfg, request.files.getlist("files"),
                                                           batch_name=request.form.get("batch_name", ""),
                                                           target_accounts=request.form.getlist("target_accounts")))

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
                               backend=cfg.poster_backend, **_row_chips(rows_full, "publish_panel", account))

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

    def _render_surface_edit(post_id, result):
        # P1: on success re-render _surface_edit.html via surface_for_post so the editor's time input
        # reflects the fresh value (mirrors do_regenerate); on failure show the clean inline error.
        if not result.ok:
            return render_template("_result.html", result=result)
        s = views.surface_for_post(Ledger.load(cfg), Accounts.load(cfg), post_id,
                                   now=datetime.now(timezone.utc), cfg=cfg)
        if s is None:
            return render_template("_result.html",
                                   result=actions.ActionResult(ok=False, error=f"post vanished: {post_id}"))
        return render_template("_surface_edit.html", s=s, backend=cfg.poster_backend)

    @app.post("/reschedule/<post_id>")
    def do_reschedule(post_id):
        # legacy route kept for back-compat (any other caller) — returns only the inline result.
        result = actions.reschedule_post(cfg, post_id, _time_arg())
        return render_template("_result.html", result=result)

    @app.post("/reschedule-surface/<post_id>")
    def do_reschedule_surface(post_id):
        # R4 fix: the Review editor's reschedule + "Use suggested" forms post HERE so the time input
        # re-renders with the fresh scheduled_time (the legacy /reschedule left it stale).
        result = actions.reschedule_post(cfg, post_id, _time_arg())
        return _render_surface_edit(post_id, result)

    @app.post("/clear/<post_id>")
    def do_clear(post_id):
        # P1: drop the time on a Review (awaiting) post; re-render the editor with an EMPTY time input.
        # (On a queued post clear_time sends it back to awaiting first, then clears — same re-render.)
        result = actions.clear_time(cfg, post_id)
        return _render_surface_edit(post_id, result)

    @app.post("/caption/<post_id>")
    def do_caption(post_id):
        result = actions.edit_caption(cfg, post_id, request.form.get("caption", ""))
        return render_template("_result.html", result=result)

    @app.post("/regenerate/<post_id>")
    def do_regenerate(post_id):
        # Review-first milestone 3: re-run the caption model for this one post, then swap the editable
        # field so the operator SEES the new caption land in the box. On failure (not editable, bad
        # model output, off-brand reject, claude absent) show the clean error instead of a 500.
        result = actions.regenerate_caption(cfg, post_id, request.form.get("guidance") or "")
        if not result.ok:
            return render_template("_result.html", result=result)
        s = views.surface_for_post(Ledger.load(cfg), Accounts.load(cfg), post_id,
                                   now=datetime.now(timezone.utc), cfg=cfg)
        if s is None:
            return render_template("_result.html",
                                   result=actions.ActionResult(ok=False, error=f"post vanished: {post_id}"))
        return render_template("_surface_edit.html", s=s, regen_note=result.detail, backend=cfg.poster_backend)

    @app.post("/reburn-hook/<post_id>")
    def do_reburn_hook(post_id):
        # Face 4: re-burn the operator's edited on-screen HOOK for ONE surface (ffmpeg only, no LLM), then
        # swap the editor so the new hook lands in the box (and a "couldn't burn (no libass)" warning shows
        # if the burn failed open). Clean inline error on a guard/unknown-post failure, never a 500.
        result = actions.reburn_hook(cfg, post_id, request.form.get("hook") or "")
        if not result.ok:
            return render_template("_result.html", result=result)
        s = views.surface_for_post(Ledger.load(cfg), Accounts.load(cfg), post_id,
                                   now=datetime.now(timezone.utc), cfg=cfg)
        if s is None:
            return render_template("_result.html",
                                   result=actions.ActionResult(ok=False, error=f"post vanished: {post_id}"))
        return render_template("_surface_edit.html", s=s, reburn_note=result.detail, backend=cfg.poster_backend)

    @app.post("/snooze/<clip_id>")
    def do_snooze(clip_id):
        result = actions.snooze_clip(cfg, clip_id)
        return render_template("_result.html", result=result)

    @app.post("/unhold/<clip_id>")
    def do_unhold(clip_id):
        # Release a brand-risk hold from the Review tab (UI twin of `fanops unhold`). On success the clip
        # becomes captions_requested with NO posts yet, so it leaves the held bucket entirely (and isn't
        # editable until the next advance re-runs captions) — the outerHTML swap of an EMPTY fragment
        # removes the held card in place, no dangling HELD badge. Failure shows the inline ✗.
        result = actions.release_held_clip(cfg, clip_id)
        if not result.ok:
            return render_template("_result.html", result=result)
        return ""                                        # released -> card vanishes from the held bucket

    # ── A2: the Personas page — personas become editable/addable/connectable in the browser ───────────
    @app.get("/personas")
    def personas_view():
        # First-class personas (voice/tag_lean/corpus/intake) — list, add via intake, edit, connect to
        # accounts. nav_account is injected globally but the page is account-agnostic (it lists ALL).
        return render_template("personas.html", page=views.personas_page(cfg), tag_leans=_TAG_LEANS,
                               levers=_LEVERS, effects=_LEVER_EFFECTS, lever_ref=_LEVER_REF, result=None, tab="personas")

    def _personas_panel(result=None):
        # Re-render the panel with FRESH personas_page after an action (htmx swaps #personas-panel).
        return render_template("_personas_panel.html", page=views.personas_page(cfg), tag_leans=_TAG_LEANS,
                               levers=_LEVERS, effects=_LEVER_EFFECTS, lever_ref=_LEVER_REF, result=result, tab="personas")

    @app.get("/personas/drawer/<pid>")
    def do_personas_drawer(pid):
        # Slice 3: render the focused persona's levers as a slide-out DRAWER body (htmx swaps it into the
        # body-level #persona-drawer mount). Levers are visible here — no nested collapse. Save/Delete reuse
        # /personas/edit + /personas/delete (re-render #personas-panel). Fail-open: an unknown id renders a
        # clean "not found" dialog (p=None), never a 404/500 (htmx would swap an error page into the mount).
        card = next((c for c in views.personas_page(cfg).personas if c.id == pid), None)
        return render_template("_persona_drawer.html", p=card, tag_leans=_TAG_LEANS,
                               levers=_LEVERS, effects=_LEVER_EFFECTS)

    @app.post("/personas/compose")
    def do_personas_compose():
        # LIVE TRANSLATION: recompute what the in-progress (unsaved) persona compiles to from the posted form
        # values and render the compose panel. Transient — preview_compose NEVER persists. htmx swaps the
        # per-form #persona-compose-<id> target on every lever change.
        return render_template("_persona_compose.html", result=studio_personas.preview_compose(cfg, request.form))

    @app.post("/personas/add")
    def do_personas_add():
        return _personas_panel(studio_personas.create_persona(
            cfg, request.form.get("name", ""), request.form.get("voice", ""), request.form.get("tag_lean", ""),
            request.form.get("genre", ""), request.form.get("language", ""),
            request.form.get("refs", ""), request.form.get("notes", ""),
            content_focus=request.form.getlist("content_focus"), energy=request.form.get("energy", ""),
            hook_angle=request.form.get("hook_angle", ""), hook_tone=request.form.get("hook_tone", ""),
            clip_profile=request.form.get("clip_profile", ""), framing=request.form.get("framing", ""),
            casting_directive=request.form.get("casting_directive", ""), hook_directive=request.form.get("hook_directive", ""),
            caption_directive=request.form.get("caption_directive", ""), clip_count=request.form.get("clip_count", "")))

    @app.post("/personas/edit")
    def do_personas_edit():
        return _personas_panel(studio_personas.edit_persona(
            cfg, request.form.get("id", ""), request.form.get("name", ""), request.form.get("voice", ""),
            request.form.get("tag_lean", ""), request.form.get("genre", ""), request.form.get("language", ""),
            request.form.get("refs", ""), request.form.get("notes", ""),
            content_focus=request.form.getlist("content_focus"), energy=request.form.get("energy", ""),
            hook_angle=request.form.get("hook_angle", ""), hook_tone=request.form.get("hook_tone", ""),
            clip_profile=request.form.get("clip_profile", ""), framing=request.form.get("framing", ""),
            brief=request.form.get("brief", ""),
            casting_directive=request.form.get("casting_directive", ""), hook_directive=request.form.get("hook_directive", ""),
            caption_directive=request.form.get("caption_directive", ""), clip_count=request.form.get("clip_count", "")))

    @app.post("/personas/delete")
    def do_personas_delete():
        return _personas_panel(studio_personas.delete_persona(cfg, request.form.get("id", "")))

    @app.post("/personas/corpus/add")
    def do_personas_corpus_add():
        return _personas_panel(studio_personas.add_corpus_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/corpus/remove")
    def do_personas_corpus_remove():
        return _personas_panel(studio_personas.remove_corpus_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/research")
    def do_personas_research():
        # B3: propose the reach-best hashtags this persona lacks (bootstrap research) -> the panel renders
        # them with one-click Add. Grounded in the reach store + the persona's lean; instant + budget-free.
        return _personas_panel(studio_personas.research_corpus(cfg, request.form.get("id", "")))

    @app.post("/personas/recommend")
    def do_personas_recommend():
        # B2: look up a candidate tag's live Graph metrics (engagement) so the operator can decide before
        # adding it to the corpus. The panel renders the metrics + an Add button; no add happens here.
        return _personas_panel(studio_personas.recommend_tag(cfg, request.form.get("id", ""), request.form.get("tag", "")))

    @app.post("/personas/connect")
    def do_personas_connect():
        # Connect/disconnect ONE account to a persona (blank persona_id disconnects). Re-render the panel.
        return _personas_panel(studio_personas.connect_account(cfg, request.form.get("handle", ""), request.form.get("persona_id", "")))

    @app.post("/personas/migrate")
    def do_personas_migrate():
        # One-click: lift inline account persona strings into first-class Persona records + link (idempotent).
        return _personas_panel(studio_personas.run_migration(cfg))

    @app.get("/golive")
    def golive_view():
        # Milestone 5 (operator-gated): turn FanOps from dryrun into real Postiz publishing entirely in
        # the browser — add accounts, map each channel to its integration, see readiness, flip dryrun<->live.
        return render_template("golive.html", status=views.golive_status(cfg), result=None,
                               all_platforms=_ALL_PLATFORMS, tag_leans=_TAG_LEANS, effects=_LEVER_EFFECTS, tab="golive")

    def _golive_panel(result):
        # Re-render the panel with FRESH golive_status after an action (htmx swaps #golive-panel), so the
        # mode banner + readiness checks update in place — mirrors _run_panel. S8: `effects` carries the
        # engine-true _LEVER_EFFECTS so the clip-length bands render from the catalog, never a stale literal.
        return render_template("_golive_panel.html", status=views.golive_status(cfg), result=result,
                               all_platforms=_ALL_PLATFORMS, tag_leans=_TAG_LEANS, effects=_LEVER_EFFECTS, tab="golive")

    @app.post("/golive/config")
    def do_golive_config():
        return _golive_panel(golive.set_postiz_config(cfg, request.form.get("url", ""), request.form.get("key", "")))

    @app.post("/golive/account/add")
    def do_golive_account_add():
        # Onboard a new account from the UI: handle + platform checkboxes + optional persona -> a new
        # active/postiz account appended to accounts.json (no JSON hand-edit), ready to map below.
        return _golive_panel(golive.add_account(cfg, request.form.get("handle", ""),
                                                request.form.getlist("platform"),
                                                request.form.get("persona", ""),
                                                request.form.get("tag_lean", "")))

    @app.post("/golive/account/lean")
    def do_golive_account_lean():
        # Set/clear an account's tag_lean (persona differentiation) — blank clears; re-render the panel.
        return _golive_panel(golive.set_account_lean(cfg, request.form.get("handle", ""),
                                                      request.form.get("tag_lean", "")))

    @app.post("/golive/hooks")
    def do_golive_hooks():
        # Toggle per-account on-screen hooks (FANOPS_CREATIVE_VARIATION). Explicit "1"==on (NOT bool(str) —
        # bool("0") is True; the off button sends value=""/anything-not-1). Works in dryrun or live (changes
        # per-account render, not whether posts publish).
        return _golive_panel(golive.set_per_account_hooks(cfg, request.form.get("on") == "1"))

    @app.post("/golive/casting")
    def do_golive_casting():
        # Toggle per-account moment casting (FANOPS_ACCOUNT_CASTING). Same shape as do_golive_hooks: explicit
        # "1"==on (NOT bool(str) — bool("0") is True; the off button sends value=""). Works in dryrun or live
        # (changes which posts are BORN, not whether they publish).
        return _golive_panel(golive.set_account_casting(cfg, request.form.get("on") == "1"))

    @app.post("/golive/cast-budget")
    def do_golive_cast_budget():
        # Phase 2: set the budget-mode pick count (FANOPS_CAST_PICK_BUDGET); validated/clamped in the setter.
        return _golive_panel(golive.set_cast_pick_budget(cfg, request.form.get("budget", "")))

    @app.post("/golive/clip-profile")
    def do_golive_clip_profile():
        # Phase 2: set the clip-length band (FANOPS_CLIP_PROFILE = talk|song); validated in the setter.
        return _golive_panel(golive.set_clip_profile(cfg, request.form.get("profile", "")))

    @app.post("/golive/learning")
    def do_golive_learning():
        # Phase 6: toggle the A/B learning master switch (FANOPS_VARIANT_LEARNING) — explicit "1"==on. Intent
        # only; the apply paths stay learning_validated-frozen (ON does NOT unfreeze learning).
        return _golive_panel(golive.set_variant_learning(cfg, request.form.get("on") == "1"))

    @app.post("/golive/amplify")
    def do_golive_amplify():
        # Phase 6: toggle variant-driven amplify (FANOPS_VARIANT_AMPLIFY) — explicit "1"==on.
        return _golive_panel(golive.set_variant_amplify(cfg, request.form.get("on") == "1"))

    @app.post("/golive/ucb")
    def do_golive_ucb():
        # Phase 6: toggle UCB1 variant ranking (FANOPS_VARIANT_UCB) — explicit "1"==on.
        return _golive_panel(golive.set_variant_ucb(cfg, request.form.get("on") == "1"))

    @app.post("/golive/transfer")
    def do_golive_transfer():
        # Phase 6: toggle cross-account hook transfer (FANOPS_VARIANT_TRANSFER) — explicit "1"==on.
        return _golive_panel(golive.set_variant_transfer(cfg, request.form.get("on") == "1"))

    @app.post("/golive/zernio-config")
    def do_golive_zernio_config():
        # Zernio slice 4: connect Zernio (key only, hosted) — dual-writes ZERNIO_API_KEY + tests it.
        return _golive_panel(golive.set_zernio_config(cfg, request.form.get("key", "")))

    @app.post("/golive/account/backend")
    def do_golive_account_backend():
        # Zernio slice 4: route ONE (handle, platform) channel to a backend. A LIVE backend is gated
        # (creds + confirm) in the setter — the per-account 'go live'. confirm box -> confirmed=True.
        return _golive_panel(golive.set_account_backend(
            cfg, request.form.get("handle", ""), request.form.get("platform", ""),
            request.form.get("backend", ""), confirmed=request.form.get("confirm") == "1"))

    @app.post("/golive/account/persona")
    def do_golive_account_persona():
        # Phase 3: set/clear an existing account's persona (was add-time only -> accounts.json hand-edit).
        return _golive_panel(golive.set_persona(cfg, request.form.get("handle", ""), request.form.get("persona", "")))

    @app.post("/golive/account/promote")
    def do_golive_account_promote():
        # Phase 3: promote a demoted/planned account back to active (inverse of demote — no longer one-way).
        return _golive_panel(golive.promote_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/account/remove")
    def do_golive_account_remove():
        # Remove an account from the UI (no JSON hand-edit) — clears a placeholder like @TBD-1; re-render the panel.
        return _golive_panel(golive.remove_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/account/demote")
    def do_golive_account_demote():
        # Demote an account to `planned` — it leaves the active publishing fan-out but keeps its row/history.
        return _golive_panel(golive.demote_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/refresh")
    def do_golive_refresh():
        return _golive_panel(golive.refresh_integrations(cfg))

    @app.post("/golive/discover")
    def do_golive_discover():
        # M4b: list every channel the connected schedulers (Postiz + Zernio) already hold, each proposed
        # for one-click adoption (handle + provider + id + deterministic match). discover_channels never
        # writes — the operator confirms each row in adopt; re-render the panel with the proposed rows.
        return _golive_panel(golive.discover_channels(cfg))

    @app.post("/golive/adopt")
    def do_golive_adopt():
        # M4b: adopt the ticked discovered channels. Each ticked checkbox submits its row INDEX in `adopt`;
        # the row's hidden provider__i/id__i/platform__i + the editable handle__i/persona__i carry the data
        # (so adopt never re-discovers). confirm routes the adopted channels to their scheduler (creds-gated
        # in adopt_channels — without it a channel is mapped but unrouted, never publishing).
        sels = [{"provider": request.form.get(f"provider__{i}", ""), "id": request.form.get(f"id__{i}", ""),
                 "platform": request.form.get(f"platform__{i}", ""), "handle": request.form.get(f"handle__{i}", ""),
                 "persona": request.form.get(f"persona__{i}", "")} for i in request.form.getlist("adopt")]
        return _golive_panel(golive.adopt_channels(cfg, sels, confirmed=request.form.get("confirm") == "1"))

    @app.get("/golive/health")
    def do_golive_health():
        # Issue 1: live dependency verdicts (Docker / Postiz / Zernio), loaded on-demand via htmx so the
        # network/subprocess probes run ONLY when the tab is viewed — never in the golive_status read-model
        # (which the whole test suite calls). A down dependency is visible here, not buried in a later error.
        from fanops.health import system_health
        return render_template("_golive_health.html", health=system_health(cfg))

    @app.post("/golive/map")
    def do_golive_map():
        # Batch per-CHANNEL map: one <select name="map__<handle>__<platform>"> per channel, submitted
        # together. Split on the LAST "__" so a handle keeps its own characters; map only the channels the
        # operator actually picked (non-blank), via the per-platform unit action golive.map_account.
        picks = []
        for k in request.form:
            if not k.startswith("map__"):
                continue
            v = (request.form.get(k) or "").strip()
            rest = k[len("map__"):]
            if not v or "__" not in rest:
                continue
            handle, platform = rest.rsplit("__", 1)
            picks.append((handle, platform, v))
        if not picks:
            return _golive_panel(actions.ActionResult(ok=False, error="pick a Postiz integration for at least one channel"))
        errors = [r.error for r in (golive.map_account(cfg, h, p, v) for h, p, v in picks) if not r.ok]
        if errors:
            return _golive_panel(actions.ActionResult(ok=False, error="; ".join(errors)))
        return _golive_panel(actions.ActionResult(ok=True, detail={"mapped": len(picks)}))

    @app.post("/golive/live")
    def do_golive_live():
        # The ONLY route that can set FANOPS_LIVE=1 (the global live switch — provider is per-channel);
        # confirm derived from the checkbox, and go_live itself re-gates on readiness (≥1 channel with a
        # provider+creds) — a stray POST can't flip the system live.
        return _golive_panel(golive.go_live(cfg, confirmed=bool(request.form.get("confirm"))))

    @app.post("/golive/dryrun")
    def do_golive_dryrun():
        return _golive_panel(golive.go_dryrun(cfg))

    @app.post("/golive/validate")
    def do_golive_validate():
        # M3: run the Postiz cutover from the browser to unfreeze the learning loop — posts ONE real
        # throwaway probe to the operator-SELECTED integration behind a confirm. validate_learning
        # re-gates (live-postiz + known integration + confirm); a stray POST can't fire it.
        return _golive_panel(golive.validate_learning(cfg, integration_id=request.form.get("integration_id"),
                                                       confirmed=bool(request.form.get("confirm"))))

    from werkzeug.exceptions import RequestEntityTooLarge
    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        # An over-MAX_CONTENT_LENGTH upload: Werkzeug raised 413 before do_run_upload ran. Re-render the
        # Run panel with a clean "too large" message at HTTP 200 — htmx 2.0.3 only swaps 2xx bodies, so a
        # 413 panel would be silently dropped and the operator would see nothing. The cap is enforced by
        # Werkzeug regardless of this status; only the friendly response's status changes.
        mb = (app.config["MAX_CONTENT_LENGTH"] or 0) // (1024 * 1024)
        return _run_panel(actions.ActionResult(ok=False, error=f"file too large — the upload cap is {mb} MB"))

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
