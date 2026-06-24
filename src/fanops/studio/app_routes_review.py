"""Review + edit route group for the Studio: the per-account review-and-approve worklist (the
matrix/pivot/list views + the approve_* bulk actions) and the per-surface editor (reschedule /
clear-time / caption / regenerate / re-burn-hook / snooze / unhold). register_review_routes(app, cfg)
registers them under their ORIGINAL endpoint names (url_for byte-identical); create_app calls it.
Shared arg-parsers + the card-chip helper come from app (loaded before create_app runs, so cycle-free)."""
from __future__ import annotations
from datetime import datetime, timezone
from flask import render_template, request
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.studio import actions, views
from fanops.studio.app import _account_arg, _batch_arg, _card_chips, _offset_arg, _source_arg, _state_arg, _time_arg, _view_arg


def register_review_routes(app, cfg):
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
