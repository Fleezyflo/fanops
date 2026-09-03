"""CLI verb dispatch router (extracted from cli.py, SA-C8-5).

Public callers should continue to import handlers from ``fanops.cli``; this module is the routing home
for ``fanops`` subcommand dispatch after argparse. Handlers are resolved through ``fanops.cli`` at call
time so tests can monkeypatch ``fanops.cli.<handler>`` and have ``main()`` observe the stub.
"""
from __future__ import annotations

from fanops.config import Config


def dispatch(args, cfg: Config) -> int:
    from fanops import cli
    if args.cmd == "reframe":  return cli.cmd_reframe(cfg, args)
    if args.cmd == "overlay-reburn": return cli.cmd_overlay_reburn(cfg, args)
    if args.cmd == "status":   return cli.cmd_status(cfg)
    if args.cmd == "pause":    return cli.cmd_pause(cfg, on=True)
    if args.cmd == "resume":   return cli.cmd_pause(cfg, on=False)
    if args.cmd == "recover":
        if args.recover_cmd == "audit": return cli.cmd_recover_audit(cfg)
        return 2
    if args.cmd == "ingest":     return cli.cmd_ingest(cfg)
    if args.cmd == "pull":       return cli.cmd_pull(cfg, args)
    if args.cmd == "respond":    return cli.cmd_respond(cfg)
    if args.cmd == "digest":     return cli.cmd_digest(cfg)
    if args.cmd == "advance":    return cli.cmd_advance(cfg, args)
    if args.cmd == "track":    return cli.cmd_track(cfg, args.window)
    if args.cmd == "map-media": return cli.cmd_map_media(cfg)
    if args.cmd == "verify-live": return cli.cmd_verify_live(cfg)
    if args.cmd == "reconcile": return cli.cmd_reconcile(cfg, report_terminals=getattr(args, "report_terminals", False))
    if args.cmd == "adjust":   return cli.cmd_adjust(cfg, args.winner_pct, args.retire_pct, args.lift_floor)
    if args.cmd == "amplify-variants": return cli.cmd_amplify_variants(cfg)
    if args.cmd == "p4-bias": return cli.cmd_p4_bias(cfg)
    if args.cmd == "cutover":  return cli.cmd_cutover(cfg, args)
    if args.cmd == "wipe":     return cli.cmd_wipe(cfg, args)
    if args.cmd == "purge":    return cli.cmd_purge(cfg, args)
    if args.cmd == "restore":  return cli.cmd_restore(cfg, args)
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
    if args.cmd == "init":     return cli.cmd_init(cfg, args)
    if args.cmd == "health":   return cli.cmd_health(cfg, args)
    if args.cmd == "config":   return cli.cmd_config(cfg)
    if args.cmd == "doctor":   return cli.cmd_doctor(cfg, args)
    if args.cmd == "publish-queue": return cli.cmd_publish_queue(cfg)
    if args.cmd == "posts":
        if args.posts_cmd == "recaption":
            from fanops.recaption import cmd_posts_recaption   # lazy, matching the hashtags-verb precedent
            return cmd_posts_recaption(cfg, args)
        if args.posts_cmd == "census-retired":
            from fanops.stranded_posts import cmd_posts_reconcile_retired   # lazy, same precedent
            return cmd_posts_reconcile_retired(cfg, args)
    if args.cmd == "daemon":   return cli.cmd_daemon(cfg, args)
    if args.cmd == "autopilot": return cli.cmd_autopilot(cfg, args)
    if args.cmd == "up":       return cli.cmd_up(cfg, args)
    if args.cmd == "canary":   return cli.cmd_canary(cfg, args)
    if args.cmd == "gc":       return cli.cmd_gc(cfg, args.keep_days if args.keep_days is not None else cfg.gc_keep_days)
    if args.cmd == "compose":  return cli.cmd_compose(cfg, args)
    if args.cmd == "resolve":
        return cli.cmd_resolve(cfg, args)
    if args.cmd == "audit":
        return cli.cmd_audit(cfg, args)
    if args.cmd == "bulk-send-to-review":
        return cli.cmd_bulk_send_to_review(cfg, args)
    if args.cmd == "unhold":     return cli.cmd_unhold(cfg, args)
    if args.cmd == "retry-source": return cli.cmd_retry_source(cfg, args)
    if args.cmd == "retire-source": return cli.cmd_retire_source(cfg, args)
    if args.cmd == "promote-source": return cli.cmd_promote_source(cfg, args)
    if args.cmd == "retry-metrics": return cli.cmd_retry_metrics(cfg, args)
    if args.cmd == "discover":   return cli.cmd_discover(cfg, args)
    if args.cmd == "intake":     return cli.cmd_intake(cfg)
    if args.cmd == "studio":    return cli.cmd_studio(cfg, args)
    if args.cmd == "run":
        return cli.cmd_run(cfg, args)
    return 1
