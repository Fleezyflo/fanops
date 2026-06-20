"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import Platform, PostState, LIFT_SCORE
from fanops.discover import make_thumbnail        # reuse the cheap one-frame ffmpeg extractor for clip posters
from fanops.studio import views, actions, golive
from fanops.hashtags import TAG_LEANS            # the add-account lean picker options (no drift from the engine)

_ALL_PLATFORMS = [p.value for p in Platform]    # the add-account form's platform checkboxes (no enum drift)
_TAG_LEANS = sorted(TAG_LEANS)                  # add-account lean picker options (sourced from the engine)
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024      # 2 GiB upload cap — a long raw clip fits; an abusive body is refused (DoS)

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
    app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES    # Werkzeug refuses an oversize upload body BEFORE the view runs (413)

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

    @app.get("/")
    def index():
        return redirect(url_for("review"))

    @app.get("/review")
    def review():
        led = Ledger.load(cfg); accounts = Accounts.load(cfg); now = datetime.now(timezone.utc)
        account = _account_arg()
        cards_full = views.review_buckets(led, accounts, cfg, now=now)               # universe for chips
        cards = cards_full if account is None else views.review_buckets(led, accounts, cfg, now=now, account=account)
        counts = views.review_counts(cards)              # counts reflect what's shown (the scoped worklist)
        page = views.paginate(cards, _offset_arg())
        return render_template("review.html", cards=page.items, page=page, tab="review",
                               backend=cfg.poster_backend, counts=counts, shown=counts["awaiting"],
                               awaiting_total=counts["awaiting"], **_card_chips(cards_full, account))

    def _review_panel(result=None):
        led = Ledger.load(cfg); accounts = Accounts.load(cfg); now = datetime.now(timezone.utc)
        account = _account_arg()                          # R1: rides the POST URL into request.args -> scope preserved
        cards_full = views.review_buckets(led, accounts, cfg, now=now)
        cards = cards_full if account is None else views.review_buckets(led, accounts, cfg, now=now, account=account)
        awaiting_total = views.review_counts(cards)["awaiting"]    # keep #review-body's data-awaiting fresh after every mutation
        page = views.paginate(cards, _offset_arg())
        return render_template("_review_body.html", cards=page.items, page=page, result=result,
                               tab="review", backend=cfg.poster_backend, awaiting_total=awaiting_total,
                               **_card_chips(cards_full, account))

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

    def _schedule_panel(result=None, *, full=False):
        led = Ledger.load(cfg); now = datetime.now(timezone.utc); account = _account_arg()
        rows_full = views.schedule_rows(led, cfg, now=now)                            # universe for chips
        rows = rows_full if account is None else views.schedule_rows(led, cfg, now=now, account=account)
        groups = views.group_schedule_by_account(rows)   # per-account header groups (the "All" view reads per account)
        tmpl = "schedule.html" if full else "_schedule_panel.html"
        return render_template(tmpl, rows=rows, groups=groups, result=result, tab="schedule",
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
        return _schedule_panel(actions.reschedule_post(cfg, post_id, request.form.get("new_time", "")))

    @app.post("/schedule/clear/<post_id>")
    def do_schedule_clear(post_id):
        # P1: clear the time on an approved (queued) post -> it goes back to awaiting_approval and LEAVES the
        # bucket; re-render the whole bucket (the #schedule-body outerHTML swap drops the now-absent row).
        return _schedule_panel(actions.clear_time(cfg, post_id))

    @app.get("/lift")
    def lift():
        led = Ledger.load(cfg); accts = Accounts.load(cfg); account = _account_arg()
        view = views.lift_rows(led, cfg, accts, account=account)
        # Chip universe from a CHEAP post scan (the same analyzed-variant predicate lift_rows uses), so we
        # call lift_rows ONCE — building an unfiltered view just for chips would re-run its per-row gate I/O.
        vcounts = Counter(p.account for p in led.posts.values()
                          if p.variant_key and p.state is PostState.analyzed and LIFT_SCORE in p.metrics)
        chips = {"chip_accounts": _with_active(vcounts, account), "chip_counts": dict(vcounts),
                 "chip_route": "lift", "chip_total": sum(vcounts.values()), "active": account}
        return render_template("lift.html", view=view, tab="lift", **chips)

    def _posted_panel(result=None, *, full=False):
        led = Ledger.load(cfg); account = _account_arg()
        rows_full = views.posted_library(led, cfg)                                    # universe for chips
        rows = rows_full if account is None else views.posted_library(led, cfg, account=account)
        groups = views.group_posted_by_day(rows)          # content-lifecycle Phase 3: publish-day buckets
        accounts = Accounts.load(cfg).active()            # content-lifecycle Phase 4: cross-account picker options
        return render_template("posted.html" if full else "_posted_panel.html", rows=rows, groups=groups,
                               accounts=accounts, result=result, tab="posted",
                               **_row_chips(rows_full, "posted", account))

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
        return _run_panel(actions.run_ingest(cfg))

    @app.post("/run/pull")
    def do_run_pull():
        return _run_panel(actions.run_pull(cfg, request.form.get("url", "")))

    @app.post("/run/upload")
    def do_run_upload():
        # Stream operator-uploaded raw video into 01_inbox so the next "Ingest inbox" catalogues it — the
        # browser replacement for a Finder drag. save_uploads owns validation + atomic os.replace; the
        # panel re-renders with fresh counts (htmx outerHTML), mirroring do_run_ingest.
        return _run_panel(actions.save_uploads(cfg, request.files.getlist("files")))

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
        result = actions.reschedule_post(cfg, post_id, request.form.get("new_time", ""))
        return render_template("_result.html", result=result)

    @app.post("/reschedule-surface/<post_id>")
    def do_reschedule_surface(post_id):
        # R4 fix: the Review editor's reschedule + "Use suggested" forms post HERE so the time input
        # re-renders with the fresh scheduled_time (the legacy /reschedule left it stale).
        result = actions.reschedule_post(cfg, post_id, request.form.get("new_time", ""))
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

    @app.get("/golive")
    def golive_view():
        # Milestone 5 (operator-gated): turn FanOps from dryrun into real Postiz publishing entirely in
        # the browser — add accounts, map each channel to its integration, see readiness, flip dryrun<->live.
        return render_template("golive.html", status=views.golive_status(cfg), result=None,
                               all_platforms=_ALL_PLATFORMS, tag_leans=_TAG_LEANS, tab="golive")

    def _golive_panel(result):
        # Re-render the panel with FRESH golive_status after an action (htmx swaps #golive-panel), so the
        # mode banner + readiness checks update in place — mirrors _run_panel.
        return render_template("_golive_panel.html", status=views.golive_status(cfg), result=result,
                               all_platforms=_ALL_PLATFORMS, tag_leans=_TAG_LEANS, tab="golive")

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
        # The ONLY route that can set FANOPS_POSTER=postiz; confirm derived from the checkbox, and
        # go_live itself re-gates on readiness — a stray POST can't flip the system live.
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
