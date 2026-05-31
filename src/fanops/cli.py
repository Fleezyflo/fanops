"""CLI. Commands: status, ingest, advance, respond, track, adjust, gc, digest, run.
advance() lives in pipeline.py; track/adjust close the feedback loop (FIX F04); respond drains
the agent gates via the responder (FIX F02/F13); gc reclaims disk (FIX F83); run loops
respond+advance until stable for unattended operation."""
from __future__ import annotations
import argparse, sys
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState, SourceState
from fanops.pipeline import advance
from fanops.ingest import ingest_drops, download_source
from fanops.digest import write_digest
from fanops.agentstep import pending
from fanops.responder import get_responder
from fanops.track import pull_metrics
from fanops.adjust import classify_outcomes, amplify, retire

def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    print(f"sources={len(led.sources)} moments={len(led.moments)} clips={len(led.clips)} "
          f"posts={len(led.posts)} published={len(led.posts_in_state(PostState.published))} "
          f"failed={len(led.posts_in_state(PostState.failed))} backend={cfg.poster_backend} "
          f"awaiting_moments={len(pending(cfg, kind='moments'))} "
          f"awaiting_captions={len(pending(cfg, kind='captions'))}")
    return 0

def cmd_track(cfg: Config, window: str) -> int:
    led = Ledger.load(cfg)
    try:
        led = pull_metrics(led, cfg, window=window)   # binds to BlotatoMetricsClient
    except RuntimeError as e:
        print(f"track skipped: {e}"); return 0
    led.save(); write_digest(led, cfg)
    print(f"tracked; analyzed={len(led.posts_in_state(PostState.analyzed))}")
    return 0

def cmd_adjust(cfg: Config, winner_pct: float, retire_pct: float, lift_floor: float) -> int:
    led = Ledger.load(cfg)
    r = classify_outcomes(led, winner_pct=winner_pct, retire_pct=retire_pct, lift_floor=lift_floor)
    led = amplify(led, cfg, r["winners"])
    led = retire(led, r["losers"])
    led.save(); write_digest(led, cfg)
    print(f"adjusted; winners={len(r['winners'])} losers={len(r['losers'])}")
    return 0

def cmd_gc(cfg: Config, keep_days: int) -> int:
    # FIX F83: reclaim disk — drop clip files for retired/analyzed lineages and orphan transcripts.
    import os, time
    led = Ledger.load(cfg)
    removed = 0
    cutoff = time.time() - keep_days * 86400
    for c in led.clips.values():
        if c.state.value in ("retired", "analyzed") and c.path and os.path.exists(c.path):
            try:
                if os.path.getmtime(c.path) < cutoff:
                    os.remove(c.path); removed += 1
            except OSError:
                pass
    print(f"gc removed {removed} clip files older than {keep_days}d")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fanops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("ingest"); sub.add_parser("digest"); sub.add_parser("respond")
    p_adv = sub.add_parser("advance"); p_adv.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_pull = sub.add_parser("pull"); p_pull.add_argument("url")
    p_trk = sub.add_parser("track"); p_trk.add_argument("--window", default="30d")
    p_adj = sub.add_parser("adjust"); p_adj.add_argument("--winner-pct", type=float, default=0.3)
    p_adj.add_argument("--retire-pct", type=float, default=0.2); p_adj.add_argument("--lift-floor", type=float, default=20.0)
    p_gc = sub.add_parser("gc"); p_gc.add_argument("--keep-days", type=int, default=30)
    p_run = sub.add_parser("run"); p_run.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cfg = Config()

    if args.cmd == "status":   return cmd_status(cfg)
    if args.cmd == "ingest":
        led = ingest_drops(Ledger.load(cfg), cfg); led.save(); write_digest(led, cfg)
        print(f"ingested -> {len(led.sources)} sources"); return 0
    if args.cmd == "pull":
        led = download_source(Ledger.load(cfg), cfg, args.url); led.save(); write_digest(led, cfg)
        print(f"pulled -> {len(led.sources)} sources"); return 0
    if args.cmd == "respond":
        n = get_responder(cfg).answer_pending(cfg); print(f"responder answered {n} request(s)"); return 0
    if args.cmd == "digest":
        write_digest(Ledger.load(cfg), cfg); print(f"wrote {cfg.digest_path}"); return 0
    if args.cmd == "advance":  print(advance(cfg, base_time=args.base_time)); return 0
    if args.cmd == "track":    return cmd_track(cfg, args.window)
    if args.cmd == "adjust":   return cmd_adjust(cfg, args.winner_pct, args.retire_pct, args.lift_floor)
    if args.cmd == "gc":       return cmd_gc(cfg, args.keep_days)
    if args.cmd == "run":
        # unattended: respond to gates, advance, repeat until no progress
        for _ in range(10):
            get_responder(cfg).answer_pending(cfg)
            before = cmd_status(cfg)
            s = advance(cfg, base_time=args.base_time)
            if s["awaiting"]["moments"] == 0 and s["awaiting"]["captions"] == 0:
                break
        print(s); return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
