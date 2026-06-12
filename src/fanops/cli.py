"""CLI. Commands: status, ingest, advance, respond, track, adjust, gc, digest, run.
advance() lives in pipeline.py; track/adjust close the feedback loop (FIX F04); respond drains
the agent gates via the responder (FIX F02/F13); gc reclaims disk (FIX F83); run loops
respond+advance until stable for unattended operation."""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
import fanops
from fanops.config import Config
from fanops.errors import BlotatoAuthError, ControlFileError, LockBusyError, ToolchainMissingError
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import PostState
from fanops.pipeline import advance
from fanops.ingest import ingest_drops, download_url
from fanops.digest import write_digest
from fanops.agentstep import pending
from fanops.responder import get_responder
from fanops.track import pull_metrics, _default_list_posts
from fanops.reconcile import reconcile_posts, _default_get_status, _RECONCILABLE
from fanops.adjust import classify_outcomes, amplify, retire
from fanops.variant_amplify import apply_variant_amplify
from fanops.log import get_logger

def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    print(f"sources={len(led.sources)} moments={len(led.moments)} clips={len(led.clips)} "
          f"posts={len(led.posts)} published={len(led.posts_in_state(PostState.published))} "
          f"failed={len(led.posts_in_state(PostState.failed))} "
          # AUDIT C1: parked-for-reconcile posts (may be live) are actionable — surface here
          # so the operator sees them without opening the digest.
          f"needs_reconcile={len(led.posts_in_state(PostState.needs_reconcile))} "
          f"backend={cfg.poster_backend} "
          f"awaiting_moments={len(pending(cfg, kind='moments'))} "
          f"awaiting_captions={len(pending(cfg, kind='captions'))}")
    return 0

def cmd_track(cfg: Config, window: str) -> int:
    # Phase-B-followup: close the lost-update window for `track` too (B4 was scoped to advance).
    # The Blotato metrics FETCH (up to ~30s network) runs OUTSIDE the ledger lock; only the apply
    # (record_metrics on the freshly-loaded ledger) runs inside a tight transaction — so a slow
    # fetch never serializes behind the flock, and a concurrent advance can't clobber the result.
    try:
        rows = list(_default_list_posts(cfg)(window))   # network, NO lock held
    except RuntimeError as e:
        print(f"track skipped: {e}"); return 0
    with Ledger.transaction(cfg) as led:
        # apply the pre-fetched rows: pull_metrics matches them to still-published posts in THIS
        # (re-loaded) ledger, so a post that changed between fetch and apply is simply not matched.
        led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
        analyzed = len(led.posts_in_state(PostState.analyzed))
    write_digest(Ledger.load(cfg), cfg)              # digest read OUTSIDE the lock
    print(f"tracked; analyzed={analyzed}")
    return 0

def cmd_reconcile(cfg: Config) -> int:
    # AUDIT H4: resolve posts stranded in submitting/needs_reconcile by polling GET /v2/posts/:id.
    # Needs a key (dryrun has no live status source) — skip cleanly if absent, like track.
    # Phase-B-followup: the per-post POLLS (network) run OUTSIDE the lock against a lock-free
    # snapshot; only the apply runs inside a tight transaction. So N status polls never hold the
    # ledger flock, and a concurrent advance can't be clobbered.
    try:
        poll = _default_get_status(cfg)              # raises if no key -> skip cleanly (like track)
    except RuntimeError as e:
        print(f"reconcile skipped: {e}"); return 0
    snapshot = Ledger.load(cfg)                      # lock-free read to learn WHICH posts to poll
    results: dict[str, dict] = {}
    for p in snapshot.posts.values():
        if p.state in _RECONCILABLE and p.submission_id:
            results[p.submission_id] = poll(p.submission_id) or {}   # network, NO lock held
    with Ledger.transaction(cfg) as led:
        # apply the pre-polled results; reconcile_posts re-checks each post's CURRENT state in the
        # re-loaded ledger, so a post that changed between poll and apply is handled correctly
        # (an id with no pre-polled result yields {} -> left parked, retried next pass).
        led = reconcile_posts(led, cfg, get_status=lambda sid: results.get(sid, {}))
        nr = len(led.posts_in_state(PostState.needs_reconcile))
        pub = len(led.posts_in_state(PostState.published))
    write_digest(Ledger.load(cfg), cfg)
    print(f"reconciled; needs_reconcile={nr} published={pub}")
    return 0

def cmd_adjust(cfg: Config, winner_pct: float, retire_pct: float, lift_floor: float) -> int:
    # Phase-B-followup: wrap the whole classify->amplify->retire under one transaction (B4). No
    # network here — classify_outcomes/amplify/retire only read+mutate the ledger and write agent
    # request files (fast, local) — so holding the lock across them is correct and cheap.
    with Ledger.transaction(cfg) as led:
        r = classify_outcomes(led, winner_pct=winner_pct, retire_pct=retire_pct, lift_floor=lift_floor)
        led = amplify(led, cfg, r["winners"])
        led = retire(led, r["losers"])
    write_digest(Ledger.load(cfg), cfg)
    print(f"adjusted; winners={len(r['winners'])} losers={len(r['losers'])}")
    return 0

def cmd_amplify_variants(cfg: Config) -> int:
    # Variant-gated amplification (v3): one transaction wrapping apply_variant_amplify (no network —
    # like cmd_adjust). Inert unless FANOPS_VARIANT_AMPLIFY is on (the function self-guards), so this
    # verb is always safe to run/inspect. Amplify-only: apply_variant_amplify never retires/deletes.
    from fanops.models import SourceState
    with Ledger.transaction(cfg) as led:
        before = len(led.sources_in_state(SourceState.moments_requested))
        led = apply_variant_amplify(led, cfg)
        after = len(led.sources_in_state(SourceState.moments_requested))
    write_digest(Ledger.load(cfg), cfg)
    print(f"variant-amplify: {max(0, after - before)} source(s) amplified")
    return 0

def cmd_gc(cfg: Config, keep_days: int) -> int:
    # FIX F83: reclaim disk — drop the .mp4 files of retired/analyzed clips older than keep_days
    # (the ledger record + the post's cached media_url persist; the local file is dead weight
    # post-upload). Transcript JSONs are tiny and intentionally left.
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

def _http_url(s: str) -> str:
    """argparse type for `pull url` (stage-4 audit): the url is handed to yt-dlp verbatim, so
    validate the scheme at the boundary — file:///generic schemes and flag-lookalike args
    (argument injection into yt-dlp) die with the standard usage error, never reach a subprocess."""
    if not s.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError(f"url must be http(s)://, got {s[:60]!r}")
    return s

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fanops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("ingest"); sub.add_parser("digest"); sub.add_parser("respond")
    sub.add_parser("reconcile")
    p_adv = sub.add_parser("advance"); p_adv.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_pull = sub.add_parser("pull"); p_pull.add_argument("url", type=_http_url)
    p_trk = sub.add_parser("track"); p_trk.add_argument("--window", default="30d")
    p_adj = sub.add_parser("adjust"); p_adj.add_argument("--winner-pct", type=float, default=0.3)
    p_adj.add_argument("--retire-pct", type=float, default=0.2); p_adj.add_argument("--lift-floor", type=float, default=20.0)
    p_gc = sub.add_parser("gc"); p_gc.add_argument("--keep-days", type=int, default=30)
    sub.add_parser("amplify-variants")     # variant-gated amplification (v3); inert unless flag on
    p_res = sub.add_parser("resolve"); p_res.add_argument("post_id")
    p_res.add_argument("status", choices=["published", "failed"]); p_res.add_argument("--url", default=None)
    p_unh = sub.add_parser("unhold"); p_unh.add_argument("clip_id")
    p_rs = sub.add_parser("retry-source"); p_rs.add_argument("source_id")
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
    p_disc = sub.add_parser("discover"); p_disc.add_argument("folder")
    sub.add_parser("intake")
    p_studio = sub.add_parser("studio", help="local content-cockpit web UI (Review/Schedule/Lift)")
    p_studio.add_argument("--host", default="127.0.0.1")   # localhost only; no auth in v1
    p_studio.add_argument("--port", type=int, default=8787)
    p_run = sub.add_parser("run"); p_run.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cfg = Config()

    try:
        return _dispatch(cfg, args)
    except ControlFileError as e:
        # A control file (ledger.json/accounts.json) is malformed — almost always a hand-edit
        # typo. Print the one-line reason and exit 2 (distinct from the run-halt/usage exit 1)
        # so the operator gets a clear pointer instead of a stack trace.
        print(str(e), file=sys.stderr)
        return 2
    except LockBusyError as e:
        # Another LIVE fanops process holds the ledger lock (overlapping cron). Degrade cleanly:
        # one line + exit 1 (transient, retry next tick), NOT a traceback. A *stale* lock can't
        # reach here — the flock self-heals it (H6); this only ever means real contention.
        print(str(e), file=sys.stderr)
        return 1
    except BlotatoAuthError as e:
        # Bad/missing BLOTATO_API_KEY (or a 401) escaping a publish — operator-actionable. One
        # clean line + exit 2 (config-level, like ControlFileError), not a stack dump (AUDIT H8).
        # In `run` this is already caught by the loop guard; this covers advance/other commands.
        print(str(e), file=sys.stderr)
        return 2
    except ToolchainMissingError as e:
        # ffprobe/ffmpeg absent at INGEST (outside the pipeline quarantine, before any Source
        # exists to mark `error`) — an operator config error. One clean line ("install ffmpeg") +
        # exit 2, like ControlFileError, never a raw traceback. Downstream toolchain-absent cases
        # (render/transcribe) don't reach here — they record a retriable per-unit error state.
        print(str(e), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as e:
        # A bounded external tool hung past its hard timeout and was killed. Only `pull`'s yt-dlp
        # download can reach here (pre-Source, outside any quarantine) — every in-pipeline tool
        # (ffmpeg/ffprobe/whisper) handles its own timeout into a per-unit error state. One
        # operator-actionable line + exit 2, never a raw traceback.
        tool = e.cmd[0] if isinstance(e.cmd, (list, tuple)) and e.cmd else str(e.cmd)
        print(f"{tool} timed out after {e.timeout:.0f}s — check the network/file and re-run",
              file=sys.stderr)
        return 2


def _check_accounts(cfg: Config) -> int:
    """Fail a run early if the active-account config is unusable (README promise: an empty
    account_id on an active account is caught before a run, never reaching Blotato).
    Returns 0 when clean, else prints the problems and returns 2."""
    problems = Accounts.load(cfg).validate()
    if problems:
        print("accounts.json has problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    return 0


def _check_preflight(cfg: Config) -> int:
    """The silent-zero-output guard. Block a run up front when the operator's env would make it
    do credentialless nothing — the #1 cutover trap. Sibling to _check_accounts (config-level):
    returns 0 clean, else prints an actionable line to stderr and returns 2.

      - FANOPS_RESPONDER=llm but `claude` is not on PATH: the responder shells `claude -p`; without
        the binary every gate raises ToolchainMissingError and stays pending -> zero content. Hard
        exit 2 with an install + `claude login` pointer. (AUTH NOTE 2026-06-04: the responder uses
        the operator's EXISTING `claude` subscription/login — plain `claude -p`, NOT `--bare`, so it
        rides the OAuth/keychain session, NOT an API key. We therefore require `claude` PRESENT +
        logged in, NOT `ANTHROPIC_API_KEY`. A true login check needs a network call, so we hard-block
        only on the binary's ABSENCE and otherwise point the operator at `claude login` — a
        logged-out `claude` then surfaces loudly via the run's `run halted`/heartbeat path, not a
        traceback.)
      - FANOPS_POSTER in {rest, mcp} but no BLOTATO_API_KEY: publishing will 401 -> hard exit 2.

    The default dryrun+manual config (no creds) trips neither and passes cleanly (exit 0)."""
    import shutil
    problems = []
    if cfg.responder_mode == "llm" and shutil.which("claude") is None:
        problems.append(
            "FANOPS_RESPONDER=llm but `claude` is not on PATH — the autonomous responder shells "
            "`claude -p` using your existing Claude subscription. Install Claude Code and run "
            "`claude login` on this host (no API key needed).")
    if cfg.poster_backend in {"rest", "mcp"} and cfg.blotato_api_key is None:
        problems.append(
            f"FANOPS_POSTER={cfg.poster_backend} but BLOTATO_API_KEY is not set — publishing will "
            "fail auth (401). Export BLOTATO_API_KEY.")
    if problems:
        print("preflight: refusing to run — this config would silently produce no output:",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    return 0


def _heartbeat(cfg: Config, s: dict) -> None:
    """B5/E2: emit a heartbeat line every run/advance so an external monitor diffing consecutive
    lines can tell 'alive-but-idle' (ts advances, published_in_run may be 0) from 'cron is dead'
    (ts frozen / no new line). The ts comes from a LIVE clock so it changes every invocation —
    that mutation is the load-bearing signal, not cosmetic. Printed to stdout AND appended to
    cfg.log_path via get_logger (which mkdirs reports/) so cron+mail/PagerDuty can alert."""
    hb = {
        "heartbeat": datetime.now(timezone.utc).isoformat(),
        "fanops_version": fanops.__version__,
        "published_in_run": s.get("published_in_run", 0),
        "last_published_age_hours": s.get("last_published_age_hours"),
    }
    print(json.dumps(hb))
    get_logger(cfg)("heartbeat", "-", "ok", **hb)


def _dispatch(cfg: Config, args) -> int:
    if args.cmd == "status":   return cmd_status(cfg)
    if args.cmd == "ingest":
        # Phase-B-followup: catalogue under a transaction (B4). ffprobe runs inside the lock here,
        # but it is LOCAL + fast (tens of ms/file) — unlike the network commands, there is no slow
        # call to keep out, and the dedup (already_seen) needs the loaded ledger, so one transaction
        # is the right unit.
        with Ledger.transaction(cfg) as led:
            led = ingest_drops(led, cfg)
            n = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
        print(f"ingested -> {n} sources"); return 0
    if args.cmd == "pull":
        # Phase-B-followup: the yt-dlp DOWNLOAD (network, slow) runs OUTSIDE the lock; only the
        # ingest of what landed runs inside the transaction.
        download_url(cfg, args.url)                  # network, NO lock held
        with Ledger.transaction(cfg) as led:
            led = ingest_drops(led, cfg, origin="url")
            n = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
        print(f"pulled -> {n} sources"); return 0
    if args.cmd == "respond":
        n = get_responder(cfg).answer_pending(cfg); print(f"responder answered {n} request(s)"); return 0
    if args.cmd == "digest":
        write_digest(Ledger.load(cfg), cfg); print(f"wrote {cfg.digest_path}"); return 0
    if args.cmd == "advance":
        if (rc := _check_accounts(cfg)):  return rc
        if (rc := _check_preflight(cfg)):  return rc
        s = advance(cfg, base_time=args.base_time)
        _heartbeat(cfg, s); print(s); return 0
    if args.cmd == "track":    return cmd_track(cfg, args.window)
    if args.cmd == "reconcile": return cmd_reconcile(cfg)
    if args.cmd == "adjust":   return cmd_adjust(cfg, args.winner_pct, args.retire_pct, args.lift_floor)
    if args.cmd == "amplify-variants": return cmd_amplify_variants(cfg)
    if args.cmd == "gc":       return cmd_gc(cfg, args.keep_days)
    if args.cmd == "resolve":
        # AUDIT H1: the documented human-reconcile escape hatch. When `reconcile` can't auto-resolve
        # a post stuck in needs_reconcile (Blotato status ambiguous / never returns a terminal
        # state), the operator checks the platform by hand and forces the ledger to ground truth:
        # `fanops resolve <post_id> published --url <live-url>` or `... failed`. Tight transaction,
        # local-only mutation (no network).
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            p.state = PostState.published if args.status == "published" else PostState.failed
            if args.url: p.public_url = args.url
        print(f"resolved {args.post_id} -> {args.status}"); return 0
    if args.cmd == "unhold":
        # RUNTIME backlog (f): clear a brand-risk hold WITHOUT a hand-edit of ledger.json. When a
        # clip was parked in `held` (held=True, held_reason set) by the brand-risk gate, the
        # operator who has reviewed it forces it back into the caption gate from here. Tight
        # transaction, local-only mutation (no network), like resolve.
        from fanops.models import ClipState
        with Ledger.transaction(cfg) as led:
            if args.clip_id not in led.clips:
                print(f"no such clip: {args.clip_id}", file=sys.stderr); return 2
            c = led.clips[args.clip_id]; c.held = False; c.held_reason = None
            c.state = ClipState.captions_requested      # re-enter the caption gate
        print(f"unheld {args.clip_id}"); return 0
    if args.cmd == "retry-source":
        from fanops.models import SourceState
        with Ledger.transaction(cfg) as led:
            if args.source_id not in led.sources:
                print(f"no such source: {args.source_id}", file=sys.stderr); return 2
            s = led.sources[args.source_id]
            s.state = SourceState.catalogued      # re-enter from the top (transcribe retries)
            s.error_reason = None
            s.meta["transcribed"] = False         # force a real re-transcribe
        print(f"retry-source {args.source_id}"); return 0
    if args.cmd == "retry-metrics":
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            if p.state is PostState.published:    # leave it published so the next track pass re-pulls
                print(f"retry-metrics {args.post_id}: will re-pull on next track"); return 0
            print(f"retry-metrics {args.post_id}: not published (state={p.state.value})", file=sys.stderr); return 2
    if args.cmd == "discover":
        from pathlib import Path as _P
        from fanops.discover import discover as _discover
        root = _P(args.folder)
        if not root.exists() or not root.is_dir():
            print(f"no such folder: {args.folder}", file=sys.stderr); return 2
        s = _discover(cfg, [root])
        print(f"discovered {s['found']} candidate(s): {s['new']} new in 00_review/, {s['skipped']} already seen. "
              f"Review them in Finder, move keepers into 00_review/approved/, then `fanops intake`.")
        return 0
    if args.cmd == "intake":
        from fanops.discover import intake as _intake
        s = _intake(cfg)
        print(f"intake: {s['intaken']} approved original(s) copied into 01_inbox/ "
              f"({s['approved']} approved, {s['missing']} missing). Run `fanops advance`/`run` to pipeline them.")
        return 0
    if args.cmd == "studio":
        # LAZY import (spec §10): Flask is an optional extra; importing create_app here — never at
        # module top — keeps `import fanops.cli` (hence every other verb) working on a core,
        # no-[studio] install. Mirrors the discover/intake lazy-import idiom (cli.py:325,334).
        from fanops.studio.app import create_app
        app = create_app(cfg)
        print(f"FanOps Studio on http://{args.host}:{args.port}  (Ctrl-C to stop)")
        # debug EXPLICITLY off (stage-5 audit): a stray FLASK_DEBUG=1 in the operator's env would
        # otherwise enable the Werkzeug interactive debugger — arbitrary code exec on the cockpit.
        app.run(host=args.host, port=args.port, debug=False)
        return 0
    if args.cmd == "run":
        if (rc := _check_accounts(cfg)):  return rc
        if (rc := _check_preflight(cfg)):  return rc
        # unattended: respond to gates, advance, repeat until no progress.
        # BOTH the responder and advance() are inside the guard: advance()'s deterministic
        # stages are per-unit quarantined, but the responder (FIX H7 — the LLM model call or a
        # response that fails validation can raise) and crosspost/publish run outside those
        # guards, and publish_due RE-RAISES on fatal auth (bad key/401) by design. So a raise
        # from either degrades cleanly here (log one line + stop) rather than crashing the
        # unattended cron loop with a traceback.
        s = None
        for _ in range(10):
            try:
                get_responder(cfg).answer_pending(cfg)
                s = advance(cfg, base_time=args.base_time)
            except Exception as e:
                print(f"run halted: {type(e).__name__}: {e}", file=sys.stderr)
                return 1
            if s["awaiting"]["moments"] == 0 and s["awaiting"]["captions"] == 0:
                break
        # E1: post-loop learning pass — close the feedback loop ONCE per `run` after respond+advance
        # converges. Gated by the identical reconcile guard (pipeline.py:106): live backend + key
        # only. In dryrun (default) the guard short-circuits and the pass is NEVER entered. Runs in
        # its own lock-safe transaction (won't race the next advance); a pull/classify/amplify/retire
        # hiccup is logged and swallowed so it can NEVER crash the unattended run (exit stays 0).
        if cfg.is_live_backend:
            try:
                with Ledger.transaction(cfg) as led:
                    led = pull_metrics(led, cfg)
                    r = classify_outcomes(led)
                    led = amplify(led, cfg, r["winners"])
                    led = retire(led, r["losers"])
            except Exception as e:
                get_logger(cfg)("learn", "-", "error", err=str(e)[:120])
        # variant-amplify (v3): a SEPARATE, independently-gated learning pass — proven SUSTAINED
        # variant winners auto-amplify their source. Gated by its OWN kill switch (cfg.variant_amplify,
        # default OFF) AND the same live-backend+key guard as the learn block. Its OWN try/except so it
        # can never affect the block above and a hiccup is swallowed (exit stays 0). apply_variant_amplify
        # is amplify-only (never retires/deletes) and self-guards on the flag, so this is fail-SAFE.
        if cfg.variant_amplify and cfg.is_live_backend:
            try:
                with Ledger.transaction(cfg) as led:
                    led = apply_variant_amplify(led, cfg)
            except Exception as e:
                get_logger(cfg)("variant_amplify", "-", "error", err=str(e)[:120])
        # E2: emit one heartbeat for the WHOLE run from the final advance summary (so
        # published_in_run/last_published_age_hours reflect this run incl. the learning pass effect).
        _heartbeat(cfg, s); print(s); return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
