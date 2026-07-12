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
from fanops.studio.views_common import REVIEW_FEED_SLICE
from fanops.studio.app import _account_all_arg, _account_arg, _batch_arg, _card_chips, _compact_arg, _focus_arg, _offset_arg, _source_arg, _state_arg, _time_arg, _ultra_arg, _view_arg


def register_review_routes(app, cfg):
    def _review_context(*, result=None, regen_note=None, reburn_note=None):
        led = Ledger.load(cfg); accounts = Accounts.load(cfg); now = datetime.now(timezone.utc)
        account = _account_arg(); batch = _batch_arg(); source = _source_arg(); state = _state_arg()
        account_all = _account_all_arg()
        view = _view_arg()
        compact = _compact_arg()
        ultra = _ultra_arg()
        cards_full = views.review_buckets(led, accounts, cfg, now=now)
        pending_full = {h: n for h, n in views.review_awaiting_by_account(cards_full).items() if n > 0}
        bare_entry = (not account and not batch and not source and not state and not account_all
                      and view is None and "compact" not in request.args and "ultra" not in request.args)
        single_bare = bare_entry and len(pending_full) == 1
        switcher_only = bare_entry and len(pending_full) >= 2
        feed_account = None
        if account and account != "all":
            feed_account = account
        elif single_bare:
            feed_account = next(iter(pending_full))
        show_feed = feed_account is not None and not switcher_only
        mixed_view = account_all or (bare_entry and len(pending_full) == 0)
        scoped = bool(account or batch or source or state or account_all)
        cards = (views.review_buckets(led, accounts, cfg, now=now, account=account, batch=batch,
                                      source=source, state=state) if scoped else cards_full)
        if switcher_only:
            cards = []
        counts = views.review_counts(cards)
        full_counts = views.review_counts(cards_full) if switcher_only else counts
        progress = views.review_progress(cards_full if switcher_only else cards)
        sources = views.source_universe(cards_full)
        feed_rows_full = None
        feed_page = None
        if show_feed:
            feed_rows_full = views.review_feed_rows(led, accounts, cfg, now=now, account=feed_account,
                                                    batch=batch, source=source, state=state)
            feed_page = views.paginate(feed_rows_full, _offset_arg(), page_size=REVIEW_FEED_SLICE)
        picker_accounts = []
        if len(pending_full) >= 1:
            active_map = {a.handle: a for a in accounts.active()}
            for h, n in sorted(pending_full.items()):
                if h not in active_map: continue
                acct = active_map[h]
                plats = [getattr(p, "value", p) for p in acct.platforms]
                picker_accounts.append({"handle": h, "platforms": plats, "pending": n,
                                        "active": h == feed_account})
        choices = views.source_choices(led)
        focused = source if source else (choices[0][0] if choices else None)
        show_matrix = view == "matrix" and not show_feed
        matrix = (views.review_matrix(led, accounts, cfg, source_id=focused, now=now, state=(state or "awaiting"))
                  if (show_matrix and focused) else None)
        show_lanes = view == "lanes" and not show_feed
        lanes = (views.account_lanes(led, accounts, cfg, source_id=focused, now=now, state=(state or "awaiting"))
                 if (show_lanes and focused) else None)
        acct_cards = views.review_buckets(led, accounts, cfg, now=now, account=account, batch=batch,
                                          source=source, state=state)
        awaiting_by_account = views.review_awaiting_by_account(acct_cards)
        if show_feed and feed_account:
            awaiting_by_account = {feed_account: len([r for r in (feed_rows_full or []) if r.editable])}
        page = views.paginate(cards, _offset_arg())
        ctx = dict(cards=page.items, page=page, tab="review", compact=compact, ultra=ultra,
                   active_view=view, awaiting_by_account=awaiting_by_account, backend=cfg.poster_backend,
                   counts=counts, awaiting_total=full_counts["awaiting"], active_batch=batch, progress=progress,
                   sources=sources, result=result, matrix=matrix, lanes=lanes, source_choices=choices,
                   focused_source=focused, review_picker=switcher_only, switcher_only=switcher_only,
                   account_all=account_all, mixed_view=mixed_view, picker_accounts=picker_accounts,
                   pending_accounts=pending_full, bare_entry=bare_entry, show_feed=show_feed,
                   feed_account=feed_account, feed=(feed_page.items if feed_page else None),
                   feed_page=feed_page, feed_rows_full=feed_rows_full,
                   regen_note=regen_note, reburn_note=reburn_note,
                   **_card_chips(cards_full, account if not show_feed else feed_account))
        return ctx

    @app.get("/review")
    def review():
        ctx = _review_context()
        return render_template("review.html", shown=ctx["counts"]["awaiting"], **ctx)

    def _review_panel(result=None, *, regen_note=None, reburn_note=None):
        return render_template("_review_body.html", **_review_context(result=result, regen_note=regen_note, reburn_note=reburn_note))

    @app.get("/review/feed-slice")
    def review_feed_slice():
        ctx = _review_context()
        return render_template("_review_feed_slice.html", **ctx)

    def _focus_mutation_response(result, *, regen_note=None, reburn_note=None, post_id=None):
        if _focus_arg():
            return _review_panel(result=result, regen_note=regen_note, reburn_note=reburn_note)
        if not result.ok:
            return render_template("_result.html", result=result)
        if post_id is None:
            return render_template("_result.html", result=result)
        s = views.surface_for_post(Ledger.load(cfg), Accounts.load(cfg), post_id,
                                   now=datetime.now(timezone.utc), cfg=cfg)
        if s is None:
            return render_template("_result.html",
                                   result=actions.ActionResult(ok=False, error=f"post vanished: {post_id}"))
        return render_template("_surface_edit.html", s=s, regen_note=regen_note, reburn_note=reburn_note,
                               backend=cfg.poster_backend)

    @app.get("/review/live")
    def review_live():
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
        return _review_panel()

    @app.post("/posts/approve")
    def do_approve_posts():
        return _review_panel(actions.approve_posts(cfg, request.form.getlist("ids"),
                                               confirmed=bool(request.form.get("batch_confirm"))))

    @app.post("/posts/reject")
    def do_reject_posts():
        return _review_panel(actions.reject_posts(cfg, request.form.getlist("ids")))

    @app.post("/posts/unapprove/<post_id>")
    def do_unapprove_post(post_id):
        return _review_panel(actions.unapprove_post(cfg, post_id))

    @app.post("/posts/approve-with-edits/<post_id>")
    def do_approve_with_edits(post_id):
        return _review_panel(actions.approve_with_edits(cfg, post_id,
                          caption=request.form.get("caption", ""), hook=request.form.get("hook") or ""))

    @app.post("/posts/approve-with-hook/<clip_id>")
    def do_approve_with_hook(clip_id):
        return _review_panel(actions.approve_with_hook(cfg, clip_id))

    @app.post("/posts/approve-as-is/<clip_id>")
    def do_approve_as_is(clip_id):
        return _review_panel(actions.approve_as_is(cfg, clip_id))

    @app.post("/posts/approve-batch/<batch_id>")
    def do_approve_batch(batch_id):
        return _review_panel(actions.approve_batch(cfg, batch_id))

    @app.post("/posts/approve-clip/<clip_id>")
    def do_approve_clip(clip_id):
        return _review_panel(actions.approve_clip(cfg, clip_id))

    @app.post("/posts/approve-account")
    def do_approve_account():
        return _review_panel(actions.approve_account(cfg, _account_arg(), batch=_batch_arg(), source=_source_arg()))

    @app.post("/posts/approve-moment/<moment_id>")
    def do_approve_moment(moment_id):
        return _review_panel(actions.approve_moment(cfg, moment_id))

    @app.post("/posts/approve-channel")
    def do_approve_channel():
        ch_account = request.args.get("ch_account") or ""
        ch_source = request.args.get("ch_source") or None
        if not ch_account or not ch_source:
            return _review_panel(actions.ActionResult(ok=False, error="Approve column needs a channel and its source."))
        return _review_panel(actions.approve_account(cfg, ch_account,
                             platform=(request.args.get("ch_platform") or None), source=ch_source))

    @app.post("/cast/add/<moment_id>")
    def do_cast_add(moment_id):
        src = _source_arg(); acct = request.args.get("cast_account") or _account_arg()
        if not src or not acct:
            return _review_panel(actions.ActionResult(ok=False, error="Cast override needs a source and an account."))
        return _review_panel(actions.cast_add(cfg, src, acct, moment_id))

    @app.post("/cast/remove/<moment_id>")
    def do_cast_remove(moment_id):
        src = _source_arg(); acct = request.args.get("cast_account") or _account_arg()
        if not src or not acct:
            return _review_panel(actions.ActionResult(ok=False, error="Cast override needs a source and an account."))
        return _review_panel(actions.cast_remove(cfg, src, acct, moment_id))

    @app.post("/segments/set/<moment_id>")
    def do_set_segments(moment_id):
        src = _source_arg()
        if not src:
            return _review_panel(actions.ActionResult(ok=False, error="Set segments needs a source."))
        raw = (request.form.get("segments") or "").strip()
        pairs: list[tuple[float, float]] = []
        try:
            for part in raw.split(";"):
                part = part.strip()
                if not part: continue
                a, b = part.split("-", 1)
                pairs.append((float(a), float(b)))
        except (ValueError, AttributeError):
            return _review_panel(actions.ActionResult(ok=False, error="Segments must be start-end pairs separated by semicolons."))
        return _review_panel(actions.set_segments(cfg, src, moment_id, pairs))

    @app.post("/segments/clear/<moment_id>")
    def do_clear_segments(moment_id):
        src = _source_arg()
        if not src:
            return _review_panel(actions.ActionResult(ok=False, error="Clear segments needs a source."))
        return _review_panel(actions.clear_segments(cfg, src, moment_id))

    def _render_surface_edit(post_id, result, *, regen_note=None, reburn_note=None):
        return _focus_mutation_response(result, regen_note=regen_note, reburn_note=reburn_note, post_id=post_id)

    @app.post("/reschedule/<post_id>")
    def do_reschedule(post_id):
        result = actions.reschedule_post(cfg, post_id, _time_arg())
        return render_template("_result.html", result=result)

    @app.post("/reschedule-surface/<post_id>")
    def do_reschedule_surface(post_id):
        result = actions.reschedule_post(cfg, post_id, _time_arg())
        return _render_surface_edit(post_id, result)

    @app.post("/clear/<post_id>")
    def do_clear(post_id):
        result = actions.clear_time(cfg, post_id)
        return _render_surface_edit(post_id, result)

    @app.post("/caption/<post_id>")
    def do_caption(post_id):
        result = actions.edit_caption(cfg, post_id, request.form.get("caption", ""))
        if _focus_arg():
            return _review_panel(result=result)
        return render_template("_result.html", result=result)

    @app.post("/regenerate/<post_id>")
    def do_regenerate(post_id):
        result = actions.regenerate_caption(cfg, post_id, request.form.get("guidance") or "")
        return _focus_mutation_response(result, regen_note=result.detail if result.ok else None, post_id=post_id)

    @app.post("/restore-persona-hook/<post_id>")
    def do_restore_persona_hook(post_id):
        result = actions.restore_persona_hook(cfg, post_id)
        return _review_panel(result=result)

    @app.post("/reburn-hook/<post_id>")
    def do_reburn_hook(post_id):
        result = actions.reburn_hook(cfg, post_id, request.form.get("hook") or "")
        return _focus_mutation_response(result, reburn_note=result.detail if result.ok else None, post_id=post_id)

    @app.post("/snooze/<clip_id>")
    def do_snooze(clip_id):
        result = actions.snooze_clip(cfg, clip_id)
        return render_template("_result.html", result=result)

    @app.post("/unhold/<clip_id>")
    def do_unhold(clip_id):
        result = actions.release_held_clip(cfg, clip_id)
        if not result.ok:
            return render_template("_result.html", result=result)
        return ""
