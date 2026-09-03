"""CLI argparse registry — subcommand tree for `fanops` (extracted from cli.main)."""
from __future__ import annotations

import argparse
import math as _math


def _http_url(s: str) -> str:
    """argparse type for `pull url` (stage-4 audit): the url is handed to yt-dlp verbatim, so
    validate the scheme at the boundary — file:///generic schemes and flag-lookalike args
    (argument injection into yt-dlp) die with the standard usage error, never reach a subprocess."""
    if not s.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError(f"url must be http(s)://, got {s[:60]!r}")
    return s


def _parse_segments(s: str) -> list:
    # argparse `type=` callback: a malformed value raises ArgumentTypeError, so argparse exits 2 with a clean
    # usage message instead of letting float() throw an uncaught traceback. Non-finite bounds (nan/inf) are
    # rejected HERE so they can never reach the identity-bearing canonical JSON (canary Phase 8).
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part: continue
        a, sep, b = part.partition("-")
        if not sep:
            raise argparse.ArgumentTypeError(f"segment {part!r} must be 't0-t1' (dash-separated seconds)")
        try:
            a_f, b_f = float(a), float(b)
        except ValueError:
            raise argparse.ArgumentTypeError(f"segment {part!r} has non-numeric bounds")
        if not (_math.isfinite(a_f) and _math.isfinite(b_f)):
            raise argparse.ArgumentTypeError(f"segment {part!r} bounds must be finite (no nan/inf)")
        out.append((a_f, b_f))
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fanops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("ingest"); sub.add_parser("digest"); sub.add_parser("respond")
    sub.add_parser("pause", help="stop the unattended pump (survives restarts; operator verbs still run)"); sub.add_parser("resume", help="clear the pause marker")
    p_reconcile = sub.add_parser("reconcile")
    p_reconcile.add_argument("--report-terminals", action="store_true",
                             help="S04: preview which parked posts the (state, age) rule WOULD escalate "
                                  "submitting->needs_reconcile — reads only, writes nothing (to the log)")
    p_reframe = sub.add_parser("reframe", help="classify (--dry-run) or migrate (--apply) the clip corpus framing")
    p_reframe.add_argument("--dry-run", action="store_true",
                           help="READ-ONLY classification; writes only to a scratch root")
    p_reframe.add_argument("--limit", type=int, help="classify at most N clips (a PARTIAL run: go/no-go is suppressed)")
    p_reframe.add_argument("--scratch", help="scratch root (default: a fresh temp dir). ALL writes land here.")
    p_reframe.add_argument("--json", action="store_true", help="emit the full manifest as JSON")
    # ---- MUTATION. Never the default; mutually exclusive with --dry-run; every verb needs an explicit run id.
    p_reframe.add_argument("--apply", action="store_true",
                           help="MUTATE: reframe the ELIGIBLE clips of a reviewed full-corpus manifest (needs --manifest)")
    p_reframe.add_argument("--manifest", help="path to the REVIEWED full-corpus dry-run manifest (--apply plans from it)")
    p_reframe.add_argument("--run-id", help="the migration run id (immutable; names 07_reports/reframe/<run_id>/)")
    p_reframe.add_argument("--source", help="restrict --apply to ONE source id (the pilot)")
    p_reframe.add_argument("--plan-only", action="store_true", help="--apply: write the plan and stop before mutating")
    p_reframe.add_argument("--status", metavar="RUN_ID", help="what a run actually did, re-read from disk")
    p_reframe.add_argument("--resume", metavar="RUN_ID", help="resume a run from its immutable plan + journal")
    p_reframe.add_argument("--rollback", metavar="RUN_ID", help="restore the original bytes (whole run, or --clip)")
    p_reframe.add_argument("--clip", metavar="CLIP_ID", help="--rollback: restore just this clip")
    p_reframe.add_argument("--cleanup", metavar="RUN_ID", help="delete a terminal run's backups (explicit, refused otherwise)")
    p_or = sub.add_parser("overlay-reburn", help="recut awaiting-only Review clips in place (ass-only). Reuses reframe.lock; stale lock = operator unlink. fanops reframe --status will not understand or_ run dirs.")
    p_or.add_argument("--dry-run", action="store_true", help="READ-ONLY classify (the default)")
    p_or.add_argument("--apply", action="store_true", help="MUTATE: stage-then-replace eligible clips (pauses the pump)")
    p_or.add_argument("--limit", type=int, help="classify/apply at most N awaiting clips")
    p_or.add_argument("--scratch", help="scratch root (default: a fresh temp dir). ALL prove writes land here.")
    p_rec = sub.add_parser("recover", help="delivery recovery read-models")
    rec_sub = p_rec.add_subparsers(dest="recover_cmd", required=True)
    rec_sub.add_parser("audit", help="read-only live/inflight/failed bucket table")
    p_adv = sub.add_parser("advance"); p_adv.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_pull = sub.add_parser("pull"); p_pull.add_argument("url", type=_http_url)
    p_trk = sub.add_parser("track"); p_trk.add_argument("--window", default="30d")
    sub.add_parser("map-media", help="Leg 2: resolve each live IG post's Graph media_id from its permalink (read-only; instagram_basic)")
    sub.add_parser("verify-live", help="MOL-113: per-object liveness report over the confirm-post-live seam (read-only; ledger untouched)")
    p_adj = sub.add_parser("adjust"); p_adj.add_argument("--winner-pct", type=float, default=0.3)
    p_adj.add_argument("--retire-pct", type=float, default=0.2); p_adj.add_argument("--lift-floor", type=float, default=20.0)
    p_gc = sub.add_parser("gc"); p_gc.add_argument("--keep-days", type=int, default=None)   # None -> cfg.gc_keep_days
    sub.add_parser("amplify-variants")     # variant-gated amplification (v3); inert unless flag on
    sub.add_parser("p4-bias")              # P4(b) cross-account reach dim-bias; inert unless flag on + validated
    p_res = sub.add_parser("resolve"); p_res.add_argument("post_id")
    p_res.add_argument("status", choices=["published", "failed", "analyzed", "retired"]); p_res.add_argument("--url", default=None)
    p_unh = sub.add_parser("unhold"); p_unh.add_argument("clip_id")
    p_rs = sub.add_parser("retry-source"); p_rs.add_argument("source_id")
    p_rs.add_argument("--from-stage", choices=["auto", "catalogued", "transcribed"], default="auto")   # MOL-121: AUTO preserves a good transcript
    p_rs.add_argument("--force", action="store_true", help="MOL-471: purge caches + rewind terminal sources to catalogued (requires --from-stage catalogued)")
    p_ret = sub.add_parser("retire-source", help="preview or execute source retire (cascade-deletes unshipped media; snapshot-gated)")
    p_ret.add_argument("source_id")
    p_ret.add_argument("--i-understand-this-deletes-unshipped-media",
                       dest="i_understand_this_deletes_unshipped_media", action="store_true",
                       help="execute retire-source (requires a verified-restorable pre-retire snapshot)")
    p_prom = sub.add_parser("promote-source"); p_prom.add_argument("source_id")
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
    p_disc = sub.add_parser("discover"); p_disc.add_argument("folder")
    sub.add_parser("intake")
    p_comp = sub.add_parser("compose", help="produced clip: intro/outro brand cards + dynamic title + crossfades (MoviePy; needs .[compose])")
    p_comp.add_argument("clip_id")
    p_comp.add_argument("--title", default=None, help="on-screen title (default: the clip's hook)")
    p_comp.add_argument("--intro", default=None, help="intro card text (default: artist name; pass '' to disable)")
    p_comp.add_argument("--outro", default=None, help="outro card text, e.g. an @handle (default: none)")
    p_doctor = sub.add_parser("doctor", help="read-only first-run health screen (toolchain/accounts/key/go-live readiness)")
    sub.add_parser("config", help="introspect every env var (type, default, effective value, source, Studio-settable)")
    p_doctor.add_argument("--fix-routing", action="store_true",
                          help="(R2) READ-ONLY: list every accounts.json (handle, platform) routing-drift state with a proposed fix")
    p_doctor.add_argument("--json", action="store_true", help="machine-readable health JSON (exit 1 when unhealthy)")
    p_init = sub.add_parser("init", help="walk a fresh checkout to doctor-clean ready-to-go-live")
    p_init.add_argument("--postiz-url", default="", help="Postiz instance URL (optional; connects when set)")
    p_init.add_argument("--postiz-key", default="", help="Postiz public API key (optional)")
    p_init.add_argument("--go-live", action="store_true", help="optionally flip live via golive.go_live (all gates apply)")
    p_init.add_argument("--validate-learning", action="store_true", help="optionally run golive.validate_learning")
    p_health = sub.add_parser("health", help="runtime dependency health (docker/postiz/zernio) from the unified model")
    p_health.add_argument("--json", action="store_true", help="machine-readable JSON (exit 1 when unhealthy)")
    sub.add_parser("publish-queue", help="list queued posts to publish BY HAND (manual / no-service free path)")
    p_posts = sub.add_parser("posts", help="post-lifecycle utilities")
    posts_sub = p_posts.add_subparsers(dest="posts_cmd", required=True)
    p_rc = posts_sub.add_parser("recaption", help="re-run the ORIGINAL caption pipeline over the backlog "
                                "(awaiting_approval + non-imminent queued posts); default = read-only dry-run listing")
    p_rc.add_argument("--apply", action="store_true", help="MUTATE: request->answer->ingest->sync per seed clip; "
                      "snapshot first; resumable via 00_control/.recaption_progress.json")
    p_rc.add_argument("--dry-run", action="store_true", help="READ-ONLY target listing (the default)")
    p_rc.add_argument("--limit", type=int, default=None,
                      help="max seed clips after --account filter (positive int; omit = all)")
    p_rc.add_argument("--account", default=None,
                      help="exact handle filter; unknown handle lists 0")
    posts_sub.add_parser("census-retired", help="census: posts under a RETIRED lineage "
                         "(read-only; suppression is derived, never written; "
                         "renamed from reconcile-retired)")
    p_audit = sub.add_parser("audit", help="(R3) operator audit-trail commands")
    audit_sub = p_audit.add_subparsers(dest="audit_cmd")
    p_at = audit_sub.add_parser("tail", help="print the last N lines of 00_control/studio_audit.log")
    p_at.add_argument("-n", type=int, default=20)
    p_bsr = sub.add_parser("bulk-send-to-review", help="(R3) revert posts to awaiting_approval; clears scheduled_time/public_url/metrics/published_at")
    p_bsr.add_argument("post_ids", nargs="+")
    p_bsr.add_argument("--reason", required=True, help="operator intent recorded in the audit (e.g. bad_batch_revert)")
    p_studio = sub.add_parser("studio", help="local content-cockpit web UI (Review/Schedule/Lift)")
    p_studio.add_argument("--host", default="127.0.0.1")   # localhost only; no auth in v1
    p_studio.add_argument("--port", type=int, default=8787)
    p_studio.add_argument("--managed", action="store_true", help=argparse.SUPPRESS)
    p_studio.add_argument(
        "--dev-reload",
        action="store_true",
        help="UNSAFE DEV ONLY: run Studio in the foreground with automatic source reload",
    )
    p_studio.add_argument(
        "--app",
        action="store_true",
        help="open a native window onto the already-running Studio (does not start the server)",
    )
    st_grp = p_studio.add_mutually_exclusive_group()
    st_grp.add_argument("--install", action="store_true", help="install + load as launchd KeepAlive resident (macOS)")
    st_grp.add_argument("--uninstall", action="store_true", help="unload the launchd Studio agent and remove its plist")
    p_cut = sub.add_parser("cutover", help="live-cutover validation harness — prove the pipeline against a REAL Postiz backend")
    cut_sub = p_cut.add_subparsers(dest="cutover_action", required=True)
    cut_sub.add_parser("auth", help="step 1: prove POSTIZ_API_KEY authenticates (read-only)")
    p_cpost = cut_sub.add_parser("post", help="step 2: publish ONE 2099-scheduled probe to a THROWAWAY account")
    p_cpost.add_argument("account_id")
    p_cpost.add_argument("--i-understand-this-posts-to-a-real-account", dest="confirmed", action="store_true")
    p_cmet = cut_sub.add_parser("metrics", help="step 3: pull the real row + reconcile fields vs track._W")
    p_cmet.add_argument("submission_id")
    p_clift = cut_sub.add_parser("lift", help="step 4: compute one real lift_score from the captured row")
    p_clift.add_argument("submission_id")
    p_wipe = sub.add_parser("wipe", help="preview or execute the ledger fall-away (unbacked cache removal; snapshot-gated)")
    p_wipe.add_argument("--i-understand-this-clears-unshipped-content", dest="i_understand_this_clears_unshipped_content", action="store_true",
                        help="execute scoped wipe (keeps shipped history; requires pre-wipe snapshot)")
    p_wipe.add_argument("--include-shipped-history", dest="include_shipped_history", action="store_true",
                        help="total wipe mode — remove shipped history too (requires both total confirm flags)")
    p_wipe.add_argument("--i-understand-this-erases-shipped-history", dest="i_understand_this_erases_shipped_history", action="store_true",
                        help="confirm total wipe — must be paired with --include-shipped-history")
    p_purge = sub.add_parser("purge", help="scoped purge: days+origins dual-facet agreement; plan-only by default; deletes rows AND clip media")
    p_purge.add_argument("--day", action="append", default=[], metavar="YYYY-MM-DD",
                         help="ISO day facet over Post.created_at (repeatable; required with --origin)")
    p_purge.add_argument("--origin", action="append", default=[], metavar="ORIGIN",
                         help="MomentOrigin member facet (repeatable; required with --day): operator|machine|machine_inferred|unknown")
    p_purge.add_argument("--force-live", action="append", default=[], dest="force_live", metavar="POST_ID",
                         help="enumerated live-guard override for a specific post id (repeatable; never a bare boolean)")
    p_purge.add_argument("--i-understand-this-permanently-deletes-rows-and-media",
                         dest="i_understand_this_permanently_deletes_rows_and_media", action="store_true",
                         help="execute the purge (snapshot-gated; irreversible for media)")
    p_restore = sub.add_parser("restore", help="restore the ledger from a pre-wipe snapshot (the reversible half of `fanops wipe`)")
    p_restore.add_argument("snapshot_path", help="path to a ledger.snapshot.*.sqlite (the 'snapshot' path printed by `fanops wipe`)")
    p_prb = sub.add_parser("paths-rebase", help="(R1) rebase stale absolute media paths after FANOPS_ROOT move")
    p_prb.add_argument("--apply", action="store_true", help="snapshot + rewrite ledger/manifests (default: dry-run counts only)")
    p_learn = sub.add_parser("learn", help="learning-loop diagnostics (read-only)")
    learn_sub = p_learn.add_subparsers(dest="learn_cmd", required=True)
    learn_sub.add_parser("doctor", help="read-only: does live Postiz analytics carry the reach signal lift_score needs?")
    p_hash = sub.add_parser("hashtags", help="source-lock measurement cache (Safari play_count)")
    hash_sub = p_hash.add_subparsers(dest="hashtags_cmd", required=True)
    hash_sub.add_parser(
        "refresh",
        help="remesure sidecar pile and lock names now via Safari",
        description="remesure sidecar pile and lock names now via Safari",
    )
    hash_sub.add_parser(
        "scrape-login",
        help="open Safari on Instagram and promote the device envelope",
        description="open Safari on Instagram and promote the device envelope",
    )
    hash_sub.add_parser(
        "discover",
        help="report each source lock (read-only, zero network)",
        description="report each source lock (read-only, zero network)",
    )
    p_lever = sub.add_parser("lever", help="persona lever reference docs (generated from the live registry)")
    lever_sub = p_lever.add_subparsers(dest="lever_cmd", required=True)
    lever_sub.add_parser("docs", help="regenerate docs/LEVERS.md + docs/LEVER-THRESHOLDS.md")
    p_thresh = sub.add_parser("threshold", help="selection threshold reference docs (generated from live constants)")
    thresh_sub = p_thresh.add_subparsers(dest="thresh_cmd", required=True)
    thresh_sub.add_parser("docs", help="regenerate docs/LEVERS.md + docs/LEVER-THRESHOLDS.md")
    p_run = sub.add_parser("run"); p_run.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_run.add_argument("--loop", action="store_true", help="resident outer loop: re-run each --interval with a fresh base-time")
    p_run.add_argument("--interval", default="10m", help="sleep between --loop iterations (e.g. 10m, 90s)")
    p_dae = sub.add_parser("daemon", help="run fanops unattended via launchd (survives logout, restarts on crash)")
    dae_sub = p_dae.add_subparsers(dest="dae_cmd", required=True)
    p_dins = dae_sub.add_parser("install", help="install + load the launchd agent (macOS)")
    p_dins.add_argument("--interval", default="10m")
    dae_sub.add_parser("status", help="is the agent loaded + actually firing (heartbeat)?")
    dae_sub.add_parser("ensure", help="re-assert main daemon load if absent (keeper hook)")
    p_dstop = dae_sub.add_parser("stop", help="unload the launchd agent"); p_dstop.add_argument("--remove", action="store_true")
    p_dlog = dae_sub.add_parser("logs", help="tail the run log"); p_dlog.add_argument("-n", type=int, default=40)
    p_auto = sub.add_parser("autopilot", help="one command -> autonomous: install the supervising daemon + report readiness (doctor)")
    p_auto.add_argument("--interval", default="10m"); p_auto.add_argument("--no-daemon", action="store_true")
    p_up = sub.add_parser("up", help="one-step self-healing bring-up: git/Postiz/daemon/Studio -> one READY/NOT-READY verdict")
    p_up.add_argument("--no-restart", action="store_true", help="skip the daemon freshness kickstart (leave a running daemon on its current code)")
    p_can = sub.add_parser("canary", help="isolated single-lineage publish-path probe (prepare/discard/cancel + baseline/compare)")
    can_sub = p_can.add_subparsers(dest="canary_cmd", required=True)
    p_cprep = can_sub.add_parser("prepare", help="mint ONE isolated canary Source+Moment+Clip+Batch (0 Posts, 0 Renders)")
    p_cprep.add_argument("--media", required=True); p_cprep.add_argument("--handle", default="fanops_canary")
    p_cprep.add_argument("--run-label", default=None); p_cprep.add_argument("--start", required=True)
    p_cprep.add_argument("--end", default=None); p_cprep.add_argument("--segments", default=None, type=_parse_segments, help='"t0-t1,t2-t3"')
    p_cprep.add_argument("--caption", required=True); p_cprep.add_argument("--hashtag", action="append", default=[])
    p_cprep.add_argument("--hook", default=None); p_cprep.add_argument("--plan-only", action="store_true")
    p_cdisc = can_sub.add_parser("discard", help="pre-mint only: retire the canary lineage, close its batch"); p_cdisc.add_argument("run_id")
    p_ccanc = can_sub.add_parser("cancel", help="retire an awaiting/queued canary Post before any network acceptance")
    p_ccanc.add_argument("post_id"); p_ccanc.add_argument("--reason", required=True)
    p_cbase = can_sub.add_parser("baseline", help="capture a read-only CANDIDATE multilayer posts baseline"); p_cbase.add_argument("--output", required=True)
    p_ccmp = can_sub.add_parser("compare", help="compare the live ledger against a baseline manifest (non-zero exit on mismatch)"); p_ccmp.add_argument("--baseline", required=True)
    return parser
