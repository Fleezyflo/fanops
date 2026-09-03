"""CLI verb dispatch router (extracted from cli.py, SA-C8-5).

Public callers should continue to import handlers from ``fanops.cli``; this module is the routing home
for ``fanops`` subcommand dispatch after argparse.
"""
from __future__ import annotations

from fanops.config import Config

from fanops.cli import (
    cmd_advance,
    cmd_adjust,
    cmd_amplify_variants,
    cmd_audit,
    cmd_autopilot,
    cmd_bulk_send_to_review,
    cmd_canary,
    cmd_compose,
    cmd_config,
    cmd_cutover,
    cmd_daemon,
    cmd_digest,
    cmd_discover,
    cmd_doctor,
    cmd_gc,
    cmd_health,
    cmd_init,
    cmd_ingest,
    cmd_intake,
    cmd_map_media,
    cmd_overlay_reburn,
    cmd_p4_bias,
    cmd_pause,
    cmd_promote_source,
    cmd_publish_queue,
    cmd_pull,
    cmd_purge,
    cmd_recover_audit,
    cmd_reconcile,
    cmd_reframe,
    cmd_resolve,
    cmd_respond,
    cmd_restore,
    cmd_retire_source,
    cmd_retry_metrics,
    cmd_retry_source,
    cmd_run,
    cmd_status,
    cmd_studio,
    cmd_track,
    cmd_unhold,
    cmd_up,
    cmd_verify_live,
    cmd_wipe,
)


def dispatch(args, cfg: Config) -> int:
    if args.cmd == "reframe":  return cmd_reframe(cfg, args)
    if args.cmd == "overlay-reburn": return cmd_overlay_reburn(cfg, args)
    if args.cmd == "status":   return cmd_status(cfg)
    if args.cmd == "pause":    return cmd_pause(cfg, on=True)
    if args.cmd == "resume":   return cmd_pause(cfg, on=False)
    if args.cmd == "recover":
        if args.recover_cmd == "audit": return cmd_recover_audit(cfg)
        return 2
    if args.cmd == "ingest":     return cmd_ingest(cfg)
    if args.cmd == "pull":       return cmd_pull(cfg, args)
    if args.cmd == "respond":    return cmd_respond(cfg)
    if args.cmd == "digest":     return cmd_digest(cfg)
    if args.cmd == "advance":    return cmd_advance(cfg, args)
    if args.cmd == "track":    return cmd_track(cfg, args.window)
    if args.cmd == "map-media": return cmd_map_media(cfg)
    if args.cmd == "verify-live": return cmd_verify_live(cfg)
    if args.cmd == "reconcile": return cmd_reconcile(cfg, report_terminals=getattr(args, "report_terminals", False))
    if args.cmd == "adjust":   return cmd_adjust(cfg, args.winner_pct, args.retire_pct, args.lift_floor)
    if args.cmd == "amplify-variants": return cmd_amplify_variants(cfg)
    if args.cmd == "p4-bias": return cmd_p4_bias(cfg)
    if args.cmd == "cutover":  return cmd_cutover(cfg, args)
    if args.cmd == "wipe":     return cmd_wipe(cfg, args)
    if args.cmd == "purge":    return cmd_purge(cfg, args)
    if args.cmd == "restore":  return cmd_restore(cfg, args)
    if args.cmd == "paths-rebase":
        from fanops.paths_rebase import cmd_paths_rebase
        return cmd_paths_rebase(cfg, args)
    if args.cmd == "learn":
        if args.learn_cmd == "doctor":
            from fanops.learn_doctor import cmd_learn_doctor   # lazy: keeps requests/postiz off the core path
            return cmd_learn_doctor(cfg)
        return 2
    if args.cmd == "hashtags":
        if args.hashtags_cmd == "refresh":
            from fanops.fanops_hashtags import cmd_hashtags_refresh   # lazy: keeps it off the hot path
            return cmd_hashtags_refresh(cfg)
        if args.hashtags_cmd == "scrape-login":
            from fanops.fanops_hashtags import cmd_hashtags_scrape_login
            return cmd_hashtags_scrape_login(cfg)
        if args.hashtags_cmd == "discover":
            from fanops.fanops_hashtags import cmd_hashtags_discover  # lazy: keeps it off the hot path
            return cmd_hashtags_discover(cfg)
        return 2
    if args.cmd in ("lever", "threshold"):
        if getattr(args, "lever_cmd", None) == "docs" or getattr(args, "thresh_cmd", None) == "docs":
            from fanops.lever_docs import cmd_lever_docs
            return cmd_lever_docs(cfg)
        return 2
    if args.cmd == "init":     return cmd_init(cfg, args)
    if args.cmd == "health":   return cmd_health(cfg, args)
    if args.cmd == "config":   return cmd_config(cfg)
    if args.cmd == "doctor":   return cmd_doctor(cfg, args)
    if args.cmd == "publish-queue": return cmd_publish_queue(cfg)
    if args.cmd == "posts":
        if args.posts_cmd == "recaption":
            from fanops.recaption import cmd_posts_recaption   # lazy, matching the hashtags-verb precedent
            return cmd_posts_recaption(cfg, args)
        if args.posts_cmd == "census-retired":
            from fanops.stranded_posts import cmd_posts_reconcile_retired   # lazy, same precedent
            return cmd_posts_reconcile_retired(cfg, args)
    if args.cmd == "daemon":   return cmd_daemon(cfg, args)
    if args.cmd == "autopilot": return cmd_autopilot(cfg, args)
    if args.cmd == "up":       return cmd_up(cfg, args)
    if args.cmd == "canary":   return cmd_canary(cfg, args)
    if args.cmd == "gc":       return cmd_gc(cfg, args.keep_days if args.keep_days is not None else cfg.gc_keep_days)
    if args.cmd == "compose":  return cmd_compose(cfg, args)
    if args.cmd == "resolve":
        return cmd_resolve(cfg, args)
    if args.cmd == "audit":
        return cmd_audit(cfg, args)
    if args.cmd == "bulk-send-to-review":
        return cmd_bulk_send_to_review(cfg, args)
    if args.cmd == "unhold":     return cmd_unhold(cfg, args)
    if args.cmd == "retry-source": return cmd_retry_source(cfg, args)
    if args.cmd == "retire-source": return cmd_retire_source(cfg, args)
    if args.cmd == "promote-source": return cmd_promote_source(cfg, args)
    if args.cmd == "retry-metrics": return cmd_retry_metrics(cfg, args)
    if args.cmd == "discover":   return cmd_discover(cfg, args)
    if args.cmd == "intake":     return cmd_intake(cfg)
    if args.cmd == "studio":    return cmd_studio(cfg, args)
    if args.cmd == "run":
        return cmd_run(cfg, args)
    return 1
