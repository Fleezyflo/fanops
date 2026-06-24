"""Schedule + Posted + Lift route group for the Studio: the approved-bucket Schedule cockpit (move/clear/
publish/respread/send-back), the all-time Posted library (+ repost / crosspost-to-account) and the cross-account
Lift view. register_schedule_routes(app, cfg) registers them under their ORIGINAL endpoint names (url_for
byte-identical); create_app calls it. Helpers come from app (loaded before create_app runs, so cycle-free)."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from flask import render_template, request
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, PostState
from fanops.studio import actions, views
from fanops.studio.app import _account_arg, _batch_arg, _offset_arg, _row_chips, _time_arg, _with_active


def register_schedule_routes(app, cfg):
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

