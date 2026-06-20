"""CLI. Commands: status, ingest, advance, respond, track, adjust, gc, digest, run.
advance() lives in pipeline.py; track/adjust close the feedback loop (FIX F04); respond drains
the agent gates via the responder (FIX F02/F13); gc reclaims disk (FIX F83); run loops
respond+advance until stable for unattended operation."""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
import fanops
from fanops.config import Config
from fanops.errors import AuthError, ControlFileError, CutoverError, DownloadError, LockBusyError, ToolchainMissingError
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
from fanops import autopilot, daemon
from fanops.log import get_logger

def _gates_blocked_note(s) -> str | None:
    """A LOUD note when the run loop ends with gates still awaiting — distinguishes 'all blocked'
    from 'nothing to do' (which the bare summary buries). None when converged / no status, so the
    caller can `if (note := ...)` unconditionally."""
    aw = (s or {}).get("awaiting", {})
    # Both agent gates block downstream work: moments blocks the clip/caption stages, captions blocks
    # crosspost — a run that ends with either open has NOT converged, so both raise the same loud signal.
    open_gates = {k: v for k in ("moments", "captions") if (v := aw.get(k, 0))}
    if open_gates:
        detail = " ".join(f"{k}={v}" for k, v in open_gates.items())
        return (f"gates STILL BLOCKED after the run loop: {detail} — the responder is not clearing "
                f"them (rate limit? repeated validation failures? run `fanops doctor`)")
    return None

def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    from fanops.models import SourceState        # local read (mirrors cmd_reconcile's local import)
    print(f"sources={len(led.sources)} moments={len(led.moments)} clips={len(led.clips)} "
          f"posts={len(led.posts)} "
          # V2 M1/F8: sources the model produced ZERO picks for — actionable (retry-source), never silent.
          f"moments_empty={len(led.sources_in_state(SourceState.moments_empty))} "
          # post-approval gate: posts waiting on the operator's review (headless operators see them here,
          # not only in the Studio). rejected = operator-discarded.
          f"awaiting_approval={len(led.posts_in_state(PostState.awaiting_approval))} "
          f"published={len(led.posts_in_state(PostState.published))} "
          f"rejected={len(led.posts_in_state(PostState.rejected))} "
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
    # The metrics FETCH (up to ~30s network) runs OUTSIDE the ledger lock; only the apply
    # (record_metrics on the freshly-loaded ledger) runs inside a tight transaction — so a slow
    # fetch never serializes behind the flock, and a concurrent advance can't clobber the result.
    # Snapshot the published submission_ids FIRST (postiz reads per-post analytics, so the client must
    # know which ids to fetch; the Blotato client ignores them and fetches the bulk list).
    led0 = Ledger.load(cfg)
    sub_ids = [p.submission_id for p in led0.posts.values()
               if p.submission_id and p.state is PostState.published]
    try:
        rows = list(_default_list_posts(cfg, submission_ids=sub_ids)(window))   # network, NO lock held
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

def _learn_pass(cfg: Config, *, window: str = "30d") -> None:
    # E1 post-loop learning pass, extracted from cmd_run for testability AND to close the same
    # lost-update window cmd_track closes (ECC-review fix #1): the metrics FETCH (up to ~30s network)
    # runs OUTSIDE the ledger lock; only classify/amplify/retire run inside a tight transaction.
    # Holding the flock across the network call serialized any concurrent advance/ingest behind it.
    # Snapshot the published submission_ids FIRST (postiz reads per-post analytics, so the client
    # must know which ids to fetch; the Blotato client ignores them and fetches the bulk list).
    # Raises on a fetch/apply hiccup; the caller logs+swallows so the unattended run stays exit 0.
    led0 = Ledger.load(cfg)
    sub_ids = [p.submission_id for p in led0.posts.values()
               if p.submission_id and p.state is PostState.published]
    rows = list(_default_list_posts(cfg, submission_ids=sub_ids)(window))   # network, NO lock held
    with Ledger.transaction(cfg) as led:
        led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
        r = classify_outcomes(led)
        led = amplify(led, cfg, r["winners"])
        led = retire(led, r["losers"])

def cmd_reconcile(cfg: Config) -> int:
    # AUDIT H4: resolve posts stranded in submitting/needs_reconcile by polling GET /v2/posts/:id.
    # Needs a key (dryrun has no live status source) — skip cleanly if absent, like track.
    # Phase-B-followup: the per-post POLLS (network) run OUTSIDE the lock against a lock-free
    # snapshot; only the apply runs inside a tight transaction. So N status polls never hold the
    # ledger flock, and a concurrent advance can't be clobbered.
    snapshot = Ledger.load(cfg)                      # lock-free read: learn WHICH posts to poll AND (P2)
                                                     # give the Postiz poll each post's scheduled_time so it
    try:                                             # can date-window GET /public/v1/posts (else a future/
        poll = _default_get_status(cfg, snapshot)    # old post is permanently off the default ~week page).
    except (RuntimeError, AuthError) as e:           # no key -> skip cleanly: RuntimeError (blotato) /
        print(f"reconcile skipped: {e}"); return 0   # PostizAuthError (postiz) both = "not configured".
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

def cmd_publish_queue(cfg: Config) -> int:
    # Track B: the manual / no-service free path. Print the queued posts to post BY HAND (clip id +
    # caption + surface), then the operator marks each done with `fanops resolve <id> published`.
    from fanops.studio.views import publish_queue       # Flask-free read-model (studio.__init__ is too)
    rows = publish_queue(cfg)
    if not rows:
        print("publish queue empty (no queued posts)"); return 0
    for r in rows:
        print(f"[{'DUE' if r['due'] else 'future'}] {r['post_id']}  {r['account']}/{r['platform']}  @ {r['scheduled_time']}")
        print(f"    clip {r['clip_id']}  |  {r['caption']}")
    print(f"-- {len(rows)} post(s). Post each clip by hand, then: fanops resolve <post_id> published --url <live-url>")
    return 0

def cmd_doctor(cfg: Config) -> int:
    # Read-only first-run health screen (Phase 3b). Prints PASS/FAIL per setup gate + notes; exits 1
    # if any check fails (setup incomplete), else 0. Performs nothing — pure diagnosis + pointers.
    from fanops.doctor import doctor_report
    rep = doctor_report(cfg)
    print("fanops doctor")
    failed = 0
    for c in rep["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        line = f"  [{mark}] {c['label']}"
        if not c["ok"]:
            failed += 1
            line += f"  -> {c['hint']}"
        print(line)
    for n in rep["notes"]:
        print(f"  - {n}")
    return 1 if failed else 0

def cmd_cutover(cfg: Config, args) -> int:
    # The live-cutover validation harness (Phase 1). Lazy-import so the rest of the CLI never pays for
    # it and there's no import cycle. Each action prints its result as JSON and returns 0; a refusal/
    # failure raises CutoverError -> main()'s ladder -> one clean line + exit 2. NEVER reached by
    # run/advance — this is a manual, operator-only go-live probe.
    from fanops import cutover
    act = args.cutover_action
    if act == "auth":    print(json.dumps(cutover.cutover_auth(cfg), indent=2)); return 0
    if act == "post":    print(json.dumps(cutover.cutover_post(cfg, args.account_id, confirmed=args.confirmed), indent=2)); return 0
    if act == "metrics": print(json.dumps(cutover.cutover_metrics(cfg, args.submission_id), indent=2)); return 0
    if act == "lift":    print(json.dumps(cutover.cutover_lift(cfg, args.submission_id), indent=2)); return 0
    return 2

def cmd_compose(cfg: Config, args) -> int:
    # Produced-clip compositing (operator verb; runs OUTSIDE any ledger lock — a long MoviePy render
    # must never sit inside advance()'s flock). Composes ONE rendered clip into <clip>_composed.mp4:
    # intro/outro brand cards + a dynamic title (the clip's hook) + crossfades. FAILS OPEN (uses the
    # base clip, composed=false) on missing MoviePy / render error. Needs the [compose] extra.
    import os
    from pathlib import Path
    from fanops import overlay
    from fanops.compose import compose_clip, TemplateSpec
    led = Ledger.load(cfg)
    clip = led.clips.get(args.clip_id)
    if clip is None or not clip.path:
        print(f"no such clip (or unrendered): {args.clip_id}"); return 2
    if not os.path.exists(clip.path):
        print(f"clip file missing on disk: {clip.path}"); return 2
    mom = led.moments.get(clip.parent_id)
    title = args.title
    if title is None and mom is not None:                    # default title = the clip's on-screen hook
        title = mom.hook or overlay.derive_hook(mom.transcript_excerpt)
    intro = cfg.artist_name if args.intro is None else args.intro   # default branded intro; '' disables
    spec = TemplateSpec(title=title or None, intro_text=(intro or None), outro_text=(args.outro or None))
    if spec.is_empty():
        print("nothing to compose (no title/intro/outro) — pass --title/--intro/--outro"); return 0
    out = str(Path(clip.path).with_name(Path(clip.path).stem + "_composed.mp4"))
    log = get_logger(cfg); notes: list[str] = []
    ok = compose_clip(clip.path, out, spec,
                      log=lambda m: notes.append(m) or log("compose", args.clip_id, "info", err=m))
    result = {"clip_id": args.clip_id, "composed": ok, "out": out,
              "title": title, "intro": intro or None, "outro": args.outro or None}
    if not ok and notes:                                # surface WHY it fell back (e.g. MoviePy absent)
        result["reason"] = notes[-1]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # Exit nonzero when we fell back to the base clip (composed=false): a scripted `compose && upload`
    # must be able to tell a real produced render from the fail-open copy. The file still exists at out.
    return 0 if ok else 1

def cmd_gc(cfg: Config, keep_days: int) -> int:
    # FIX F83: reclaim disk — drop the .mp4 files of retired/analyzed clips older than keep_days
    # (the ledger record + the post's cached media_url persist; the local file is dead weight
    # post-upload). Transcript JSONs are tiny and intentionally left.
    # WIPE-SAFETY (content-lifecycle Phase 1): refuse keep_days < 1 — keep_days=0 sets cutoff=now and sweeps
    # EVERY retired/analyzed .mp4 regardless of age (a one-keystroke wipe of reusable renders, needed for
    # cross-account reuse); negative is nonsense. Clean exit 2; gc stays MANUAL (no auto-cron).
    if keep_days < 1:
        print(f"gc: refusing --keep-days {keep_days} (min 1) — would delete reusable render files", file=sys.stderr); return 2
    import os, time
    led = Ledger.load(cfg)
    removed = 0
    cutoff = time.time() - keep_days * 86400
    for c in led.clips.values():
        if c.state.value in ("retired", "analyzed") and c.path and os.path.exists(c.path):
            try:
                if os.path.getmtime(c.path) < cutoff:
                    os.remove(c.path); removed += 1
            except OSError as exc:
                # Surface a failed removal (perms / read-only mount / disk issue) instead of hiding
                # it — a silent pass could mask a disk filling up. gc still completes (other clips).
                print(f"gc: could not remove {c.path}: {exc}", file=sys.stderr)
    # content-lifecycle Phase 3: fold the 05_scheduled/ dryrun-payload cleanup into gc. These would-send JSON
    # records accumulate unbounded every dryrun pass; drop the ones older than cutoff. NEVER 06_published/ (the
    # durable archive) and no subdir recursion — only top-level *.json. Fail-open per file.
    sched_removed = 0
    if cfg.scheduled.exists():
        for f in cfg.scheduled.glob("*.json"):
            try:
                if os.path.getmtime(str(f)) < cutoff:
                    f.unlink(); sched_removed += 1
            except OSError as exc:
                print(f"gc: could not remove {f}: {exc}", file=sys.stderr)
    print(f"gc removed {removed} clip files + {sched_removed} scheduled payloads older than {keep_days}d")
    return 0

def cmd_daemon(cfg: Config, args) -> int:
    # Durable-unattended-run verb family (launchd packaging of `fanops run`). Thin: parse the interval,
    # delegate to daemon.{install,status,stop,tail_logs}, print a report. macOS-only / launchctl-absent
    # / bad-interval all degrade to one clean stderr line + exit 2 (the cli ladder posture), never a trace.
    act = args.dae_cmd
    try:
        if act == "install":
            interval = daemon.parse_interval(args.interval)
            res = daemon.install(cfg, interval=interval, responder=args.responder)
            print(f"daemon installed -> {res['plist']}")
            print(f"  wrapper {res['wrapper']}  |  interval {interval}s  |  loaded {res['loaded']}  |  responder {args.responder}")
            print("  next: fanops daemon status   |   stop: fanops daemon stop")
            return 0
        if act == "status":
            rep = daemon.status(cfg, interval=daemon.installed_interval(cfg) or 600)
            age = rep["heartbeat_age_s"]
            print(f"fanops daemon ({daemon.LABEL})")
            print(f"  loaded {rep['loaded']}  |  pid {rep['pid']}  |  last_exit {rep['last_exit']}"
                  f"  |  heartbeat {'none' if age is None else f'{int(age)}s ago'}")
            print(f"  -> {rep['verdict']}")
            return 0
        if act == "stop":
            res = daemon.stop(cfg, remove=args.remove)
            removed = "  + plist/wrapper removed" if res.get("removed") else ""
            if not res["stopped"]:                       # W10: reflect a real failure, don't claim success
                print(f"daemon may still be loaded (label {res['label']}) — run `fanops daemon status`" + removed,
                      file=sys.stderr)
                return 1
            print(f"daemon stopped (label {res['label']})" + removed)
            return 0
        if act == "logs":
            print(daemon.tail_logs(cfg, args.n))
            return 0
        return 2
    except (RuntimeError, ToolchainMissingError, ValueError) as e:
        # non-darwin (RuntimeError), launchctl absent (ToolchainMissingError), bad --interval (ValueError)
        print(f"daemon: {e}", file=sys.stderr)
        return 2

def cmd_autopilot(cfg: Config, args) -> int:
    # One command -> autonomous: enable the llm responder (durably, in .env) + install the supervising
    # daemon, then print a readiness report. BLOTATO-FREE: dryrun by default (publishes nothing); going
    # live is a separate, deliberate step via Postiz or the manual publish-queue.
    try:
        interval = daemon.parse_interval(args.interval)
        res = autopilot.autopilot(cfg, interval=interval, install_daemon=not args.no_daemon)
    except (RuntimeError, ToolchainMissingError, ValueError, OSError) as e:
        # non-darwin / launchctl absent / bad --interval / unwritable .env -> one clean line + exit 2
        print(f"autopilot: {e}", file=sys.stderr); return 2
    print("fanops autopilot — the per-clip work is now autonomous")
    print(f"  responder -> {res['responder']} (answers its own moment/caption gates via your `claude` login; no hand-typing)")
    print(f"  backend   -> {res['backend']}" + ("  (dryrun: schedules posts, publishes NOTHING)" if res["backend"] == "dryrun" else ""))
    d = res["daemon"]
    if d:
        print(f"  daemon    -> loaded ({d['interval']}s cadence, survives logout, restarts on crash)   check: fanops daemon status")
    else:
        print(f"  daemon    -> not installed ({res['daemon_note']})")
    failed = [c for c in res["checks"] if not c["ok"]]
    if failed:
        print("  still needs a human:")
        for c in failed:
            print(f"    [ ] {c['label']}  -> {c['hint']}")
    else:
        print("  readiness -> all checks pass")
    print("  go-live (separate, when you want posts to ship): self-host Postiz (FANOPS_POSTER=postiz) OR `fanops publish-queue` by hand — Blotato not required")
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
    p_gc = sub.add_parser("gc"); p_gc.add_argument("--keep-days", type=int, default=None)   # None -> cfg.gc_keep_days
    sub.add_parser("amplify-variants")     # variant-gated amplification (v3); inert unless flag on
    p_res = sub.add_parser("resolve"); p_res.add_argument("post_id")
    p_res.add_argument("status", choices=["published", "failed"]); p_res.add_argument("--url", default=None)
    p_unh = sub.add_parser("unhold"); p_unh.add_argument("clip_id")
    p_rs = sub.add_parser("retry-source"); p_rs.add_argument("source_id")
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
    p_disc = sub.add_parser("discover"); p_disc.add_argument("folder")
    sub.add_parser("intake")
    p_comp = sub.add_parser("compose", help="produced clip: intro/outro brand cards + dynamic title + crossfades (MoviePy; needs .[compose])")
    p_comp.add_argument("clip_id")
    p_comp.add_argument("--title", default=None, help="on-screen title (default: the clip's hook)")
    p_comp.add_argument("--intro", default=None, help="intro card text (default: artist name; pass '' to disable)")
    p_comp.add_argument("--outro", default=None, help="outro card text, e.g. an @handle (default: none)")
    sub.add_parser("doctor", help="read-only first-run health screen (toolchain/accounts/key/go-live readiness)")
    sub.add_parser("publish-queue", help="list queued posts to publish BY HAND (manual / no-service free path)")
    p_studio = sub.add_parser("studio", help="local content-cockpit web UI (Review/Schedule/Lift)")
    p_studio.add_argument("--host", default="127.0.0.1")   # localhost only; no auth in v1
    p_studio.add_argument("--port", type=int, default=8787)
    p_cut = sub.add_parser("cutover", help="live-cutover validation harness — prove the pipeline against REAL Blotato")
    cut_sub = p_cut.add_subparsers(dest="cutover_action", required=True)
    cut_sub.add_parser("auth", help="step 1: prove BLOTATO_API_KEY authenticates (read-only)")
    p_cpost = cut_sub.add_parser("post", help="step 2: publish ONE 2099-scheduled probe to a THROWAWAY account")
    p_cpost.add_argument("account_id")
    p_cpost.add_argument("--i-understand-this-posts-to-a-real-account", dest="confirmed", action="store_true")
    p_cmet = cut_sub.add_parser("metrics", help="step 3: pull the real row + reconcile fields vs track._W")
    p_cmet.add_argument("submission_id")
    p_clift = cut_sub.add_parser("lift", help="step 4: compute one real lift_score from the captured row")
    p_clift.add_argument("submission_id")
    p_learn = sub.add_parser("learn", help="learning-loop diagnostics (read-only)")
    learn_sub = p_learn.add_subparsers(dest="learn_cmd", required=True)
    learn_sub.add_parser("doctor", help="read-only: does live Postiz analytics carry the reach signal lift_score needs?")
    p_hash = sub.add_parser("hashtags", help="dynamic reach-ranked hashtag store (own-post reach, doctor-gated)")
    hash_sub = p_hash.add_subparsers(dest="hashtags_cmd", required=True)
    hash_sub.add_parser("refresh", help="recompute 00_control/hashtags.json from analyzed posts' reach (needs learn-doctor PASS)")
    p_run = sub.add_parser("run"); p_run.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_dae = sub.add_parser("daemon", help="run fanops unattended via launchd (survives logout, restarts on crash)")
    dae_sub = p_dae.add_subparsers(dest="dae_cmd", required=True)
    p_dins = dae_sub.add_parser("install", help="install + load the launchd agent (macOS)")
    p_dins.add_argument("--interval", default="10m"); p_dins.add_argument("--responder", default="llm", choices=["llm", "manual"])
    dae_sub.add_parser("status", help="is the agent loaded + actually firing (heartbeat)?")
    p_dstop = dae_sub.add_parser("stop", help="unload the launchd agent"); p_dstop.add_argument("--remove", action="store_true")
    p_dlog = dae_sub.add_parser("logs", help="tail the run log"); p_dlog.add_argument("-n", type=int, default=40)
    p_auto = sub.add_parser("autopilot", help="one command -> autonomous: enable llm responder (durably) + install the daemon")
    p_auto.add_argument("--interval", default="10m"); p_auto.add_argument("--no-daemon", action="store_true")
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
    except AuthError as e:
        # Bad/missing poster key (Blotato or Postiz, or a 401) escaping a publish — operator-actionable.
        # str(e) carries the backend-specific message. One clean line + exit 2 (config-level, like
        # ControlFileError), not a stack dump (AUDIT H8).
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
    except DownloadError as e:
        # yt-dlp ran but exited non-zero (dead/geoblocked URL) during `pull` — pre-Source, outside
        # any quarantine. Without this the discarded rc let `pull` print "pulled -> 0 sources" as
        # success; surface the one-line reason (stderr tail) + exit 2, like the toolchain/timeout arms.
        print(str(e), file=sys.stderr)
        return 2
    except CutoverError as e:
        # An operator refusal/failure in the live-cutover harness (dryrun backend, missing confirm
        # flag, no key, non-2xx POST, metrics not landed yet). One actionable line + exit 2 — it is
        # never a pipeline/ledger error, only the manual go-live probe needing a different input.
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
    if cfg.poster_backend == "postiz" and (cfg.postiz_url is None or cfg.postiz_api_key is None):
        miss = " and ".join(n for n, v in (("POSTIZ_URL", cfg.postiz_url),
                                           ("POSTIZ_API_KEY", cfg.postiz_api_key)) if v is None)
        problems.append(
            f"FANOPS_POSTER=postiz but {miss} not set — the Postiz backend needs both (your instance "
            "URL + its public API key). Publishing would fail.")
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
    if args.cmd == "cutover":  return cmd_cutover(cfg, args)
    if args.cmd == "learn":
        if args.learn_cmd == "doctor":
            from fanops.learn_doctor import cmd_learn_doctor   # lazy: keeps requests/postiz off the core path
            return cmd_learn_doctor(cfg)
        return 2
    if args.cmd == "hashtags":
        if args.hashtags_cmd == "refresh":
            from fanops.fanops_hashtags import cmd_hashtags_refresh   # lazy: keeps it off the hot path
            return cmd_hashtags_refresh(cfg)
        return 2
    if args.cmd == "doctor":   return cmd_doctor(cfg)
    if args.cmd == "publish-queue": return cmd_publish_queue(cfg)
    if args.cmd == "daemon":   return cmd_daemon(cfg, args)
    if args.cmd == "autopilot": return cmd_autopilot(cfg, args)
    if args.cmd == "gc":       return cmd_gc(cfg, args.keep_days if args.keep_days is not None else cfg.gc_keep_days)
    if args.cmd == "compose":  return cmd_compose(cfg, args)
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
            # Converge only when EVERY gate is clear. any() over all awaiting kinds (moments, captions)
            # is robust to future gates too — a run that exits with any open has not produced its clips/posts.
            if not any(s["awaiting"].values()):
                break
        # B2: if the loop ended with gates still awaiting, say so LOUDLY (a stuck responder used to
        # exhaust the iterations and fall through silently). Exit stays 0 — a stuck gate is not a
        # crash; the distinct stderr line + run.log event is what monitoring greps.
        if (note := _gates_blocked_note(s)):
            print(note, file=sys.stderr)
            get_logger(cfg)("run", "-", "gates_blocked",
                            moments=s["awaiting"]["moments"], captions=s["awaiting"]["captions"])
        # E1: post-loop learning pass — close the feedback loop ONCE per `run` after respond+advance
        # converges. Gated by the identical reconcile guard (pipeline.py:106): live backend + key
        # only. In dryrun (default) the guard short-circuits and the pass is NEVER entered. Runs in
        # its own lock-safe transaction (won't race the next advance); a pull/classify/amplify/retire
        # hiccup is logged and swallowed so it can NEVER crash the unattended run (exit stays 0).
        if cfg.is_live_backend:
            try:
                _learn_pass(cfg)
            except Exception as e:
                # Include the exception TYPE so a swallowed AuthError (a real auth failure that must be
                # actioned) is distinguishable in run.log from a transient 5xx — not all one level.
                get_logger(cfg)("learn", "-", "error", err=f"{type(e).__name__}: {str(e)[:120]}")
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
