"""CLI. Commands include: status, ingest, advance, respond, track, adjust, gc, digest, run … and the recovery/live verbs (reconcile, resolve, pull, doctor, studio, cutover, hashtags, daemon, autopilot).
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
from fanops.pipeline import advance, GATE_KINDS
from fanops.ingest import ingest_drops, download_url
from fanops.digest import write_digest
from fanops.agentstep import pending
from fanops.responder import get_responder
from fanops.track import pull_metrics, _default_list_posts
from fanops.reconcile import reconcile_due
from fanops.adjust import classify_outcomes, amplify, retire
from fanops.variant_amplify import apply_variant_amplify
from fanops.p4_dim_bias import apply_p4_dim_bias
from fanops.timing_bias import apply_timing_bias
from fanops import autopilot, daemon
from fanops.log import get_logger

def _gates_blocked_note(s) -> str | None:
    """A LOUD note when the run loop ends with gates still awaiting — distinguishes 'all blocked'
    from 'nothing to do' (which the bare summary buries). None when converged / no status, so the
    caller can `if (note := ...)` unconditionally."""
    aw = (s or {}).get("awaiting", {})
    # WS2 (audit x-f2): EVERY agent gate blocks downstream work — moments (pick) blocks the hook gate,
    # moment_hooks blocks the clip/caption stages, captions blocks crosspost. Iterate the awaiting dict itself
    # (built from pipeline.GATE_KINDS) so a stuck gate (the bug) — or any future gate — raises the same loud
    # signal; a hardcoded subset let a wedged gate read as converged. (P11/MOL-152: moment_casting is gone.)
    open_gates = {k: v for k, v in aw.items() if v}
    if open_gates:
        detail = " ".join(f"{k}={v}" for k, v in open_gates.items())
        return (f"gates STILL BLOCKED after the run loop: {detail} — the responder is not clearing "
                f"them (rate limit? repeated validation failures? run `fanops doctor`)")
    return None

def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    from fanops.models import SourceState        # local read (mirrors cmd_reconcile's local import)
    from fanops.doctor import setup_state, setup_next_action
    print(f"sources={len(led.sources)} moments={len(led.moments)} clips={len(led.clips)} "
          f"posts={len(led.posts)} "
          # V2 M1/F8: sources the model produced ZERO picks for — actionable (retry-source), never silent.
          f"moments_empty={len(led.sources_in_state(SourceState.moments_empty))} "
          # Audit: a source parked SourceState.error (e.g. a TRANSIENT whisper model-download/network failure)
          # is NOT auto-retried by design (the pipeline picks up only `catalogued` — auto-retry would loop on a
          # genuinely-broken source). Surface the count so the operator SEES it and runs `retry-source <id>`
          # (the existing operator-gated recovery, which flips error -> catalogued + forces a re-transcribe).
          f"sources_error={len(led.sources_in_state(SourceState.error))} "
          # post-approval gate: posts waiting on the operator's review (headless operators see them here,
          # not only in the Studio). rejected = operator-discarded.
          f"awaiting_approval={len(led.posts_in_state(PostState.awaiting_approval))} "
          f"published={len(led.posts_in_state(PostState.published))} "
          f"rejected={len(led.posts_in_state(PostState.rejected))} "
          f"failed={len(led.posts_in_state(PostState.failed))} "
          # AUDIT C1: parked-for-reconcile posts (may be live) are actionable — surface here
          # so the operator sees them without opening the digest.
          f"needs_reconcile={len(led.posts_in_state(PostState.needs_reconcile))} "
          # UI-LIE-FIX: per-channel truth (M3), not the legacy global. `fanops status` is an
          # operator-facing line; lying here was the same bug as the Studio status banner.
          f"backend={cfg.effective_publish_mode()} "
          # WS2 (audit xc-3): one awaiting_<kind>= per GATE_KINDS (the single source) so a stuck gate
          # is visible on `fanops status`; the surface can never omit a gate kind (it derives from GATE_KINDS).
          + " ".join(f"awaiting_{k}={len(pending(cfg, kind=k))}" for k in GATE_KINDS))
    print(f"setup={setup_state(cfg)} next={setup_next_action(cfg)}")
    return 0

def cmd_recover_audit(cfg: Config) -> int:
    """Read-only delivery bucket table (Sprint 3) — no ledger mutations."""
    from fanops.studio.views_results import delivery_audit
    aud = delivery_audit(Ledger.load(cfg))
    print(f"live_trackable={aud['live_trackable']} inflight={aud['inflight']} "
          f"queued={aud['queued']} failed={aud['failed']}")
    for kind, n in aud["buckets"].items():
        if n:
            print(f"  {kind}: {n}")
    return 0

def cmd_track(cfg: Config, window: str) -> int:
    # Phase-B-followup: close the lost-update window for `track` too (B4 was scoped to advance).
    # The metrics FETCH (up to ~30s network) runs OUTSIDE the ledger lock; only the apply
    # (record_metrics on the freshly-loaded ledger) runs inside a tight transaction — so a slow
    # fetch never serializes behind the flock, and a concurrent advance can't clobber the result.
    # Snapshot the published submission_ids FIRST (postiz/zernio read per-post analytics, so the client
    # must know which ids to fetch).
    led0 = Ledger.load(cfg)
    # P3: poll PUBLISHED OR ANALYZED posts (an analyzed post stays re-pollable so its metrics_series
    # accumulates later cadence offsets through the year; due_offset returns None once it's complete).
    # Slice-5: pass the POST OBJECTS so the fetch routes each to its own backend (IG-via-Postiz +
    # TikTok-via-Zernio in ONE pass); a no-override deployment is byte-identical to the old id-list path.
    pollable_posts = [p for p in led0.posts.values()
                      if p.submission_id and p.state in (PostState.published, PostState.analyzed)]
    try:
        rows = list(_default_list_posts(cfg, posts=pollable_posts)(window))   # network, NO lock held
    except (RuntimeError, AuthError) as e:               # postiz no-key raises PostizAuthError, not RuntimeError -> skip cleanly (mirror cmd_reconcile)
        print(f"track skipped: {e}"); return 0
    with Ledger.transaction(cfg) as led:
        # apply the pre-fetched rows: pull_metrics matches them to still-pollable posts in THIS
        # (re-loaded) ledger, so a post that changed between fetch and apply is simply not matched.
        before = {pid: len(p.metrics_series) for pid, p in led.posts.items()}   # P3: series rows BEFORE
        led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
        analyzed = len(led.posts_in_state(PostState.analyzed))
        added = deg = 0                                                          # P3: this-pass tally
        for pid, p in led.posts.items():
            new_rows = p.metrics_series[before.get(pid, 0):]                     # the rows appended THIS pass
            added += len(new_rows)
            deg += sum(1 for r in new_rows if r.get("lift_degraded"))
    write_digest(Ledger.load(cfg), cfg)              # digest read OUTSIDE the lock
    print(f"tracked; analyzed={analyzed} series_rows+={added} degraded={deg}")
    return 0

def _learn_pass(cfg: Config, *, window: str = "30d") -> None:
    # E1 post-loop learning pass, extracted from cmd_run for testability AND to close the same
    # lost-update window cmd_track closes (ECC-review fix #1): the metrics FETCH (up to ~30s network)
    # runs OUTSIDE the ledger lock; only classify/amplify/retire run inside a tight transaction.
    # Holding the flock across the network call serialized any concurrent advance/ingest behind it.
    # Snapshot the published submission_ids FIRST (postiz/zernio read per-post analytics, so the client
    # must know which ids to fetch).
    # Raises on a fetch/apply hiccup; the caller logs+swallows so the unattended run stays exit 0.
    led0 = Ledger.load(cfg)
    pollable_posts = [p for p in led0.posts.values()   # P3: published OR analyzed (re-pollable)
                      if p.submission_id and p.state in (PostState.published, PostState.analyzed)]
    rows = list(_default_list_posts(cfg, posts=pollable_posts)(window))   # network, NO lock held (per-post backend routing)
    with Ledger.transaction(cfg) as led:
        led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
        r = classify_outcomes(led, per_surface=cfg.adjust_per_surface)   # P4(a): per-surface WINNERS when on
        led = amplify(led, cfg, r["winners"])
        led = retire(led, r["losers"])

def cmd_reconcile(cfg: Config) -> int:
    # AUDIT H4 + M1: resolve posts stranded in submitting/needs_reconcile by polling the backend status.
    # reconcile_due pre-polls each status (network) OUTSIDE the lock against a lock-free snapshot, then
    # applies the cached results in ONE tight transaction (a single poll error is contained per-post —
    # parked, never guessed failed). Needs a key (dryrun has no live status source) — skip cleanly if
    # absent, like track: _default_get_status raises RuntimeError (non-postiz) / PostizAuthError (postiz)
    # when not configured, and a mid-poll fatal AuthError likewise = "can't reconcile, skip".
    try:
        r = reconcile_due(cfg)
    except (RuntimeError, AuthError) as e:
        print(f"reconcile skipped: {e}"); return 0
    write_digest(Ledger.load(cfg), cfg)
    print(f"reconciled; needs_reconcile={r['needs_reconcile']} published={r['published']}")
    return 0

def cmd_map_media(cfg: Config) -> int:
    # Leg 2 (Insight) ops mirror: resolve each published/analyzed IG post's Graph media_id from the live
    # media list (matched by permalink). READ-ONLY w.r.t. Instagram (a GET on /{ig_user}/media, needs only
    # instagram_basic); the daemon does this automatically inside pull_metrics, this is the on-demand mirror.
    # Fail-open (no creds -> resolves nobody, exit 0); never fabricates an id.
    from fanops.reconcile import resolve_media_ids, project_imported_media
    from fanops.track import pull_imported_insights
    led = Ledger.load(cfg)
    resolve_media_ids(led, cfg)                 # forward: enrich authored posts matched to a live media
    project_imported_media(led, cfg)            # inverse (ledger-rebuild M2): mirror live-only media as ImportedMedia
    pull_imported_insights(led, cfg)            # ledger-rebuild M3: fill each imported row's metrics by media_id
    led.save()
    mapped = sum(1 for p in led.posts.values() if p.media_id)
    ig = sum(1 for p in led.posts.values()
             if p.platform.value == "instagram" and p.state.value in ("published", "analyzed"))
    print(f"media mapped; ig_live={ig} with_media_id={mapped} imported_live_only={len(led.imported_media)}")
    return 0

def cmd_verify_live(cfg: Config) -> int:
    # MOL-113: READ-ONLY liveness report. For each published/analyzed post, ask the platform's own API about
    # THIS specific object via the confirm_post_live seam (IG per-object resolve / TikTok oEmbed) and print
    # confirmed/unconfirmed + owner. NEVER writes the ledger (load, iterate, print — no .save()); a run leaves
    # 00_control byte-identical. Fail-open: a post with no creds / no confirmable signal is reported unconfirmed,
    # never crashes. This is the on-demand mirror of the primitive MOL-117's gate consumes.
    from fanops.meta_graph import confirm_post_live
    from fanops.models import PostState
    led = Ledger.load(cfg)
    targets = [p for p in led.posts.values() if p.state in (PostState.published, PostState.analyzed)]
    confirmed = 0
    for p in targets:
        try:
            res = confirm_post_live(cfg, p, reported_username=p.account)   # best-effort username for the TikTok gate
        except Exception:
            res = {"confirmed": False, "owner": None}                      # read path never crashes on one post
        if res.get("confirmed"): confirmed += 1
        print(f"{p.id}\t{p.platform.value}\t{'LIVE' if res.get('confirmed') else 'unconfirmed'}\towner={res.get('owner')}")
    print(f"verify-live: {confirmed}/{len(targets)} confirmed live (read-only; ledger untouched)")
    return 0

def cmd_adjust(cfg: Config, winner_pct: float, retire_pct: float, lift_floor: float) -> int:
    # Phase-B-followup: wrap the whole classify->amplify->retire under one transaction (B4). No
    # network here — classify_outcomes/amplify/retire only read+mutate the ledger and write agent
    # request files (fast, local) — so holding the lock across them is correct and cheap.
    with Ledger.transaction(cfg) as led:
        r = classify_outcomes(led, winner_pct=winner_pct, retire_pct=retire_pct, lift_floor=lift_floor,
                              per_surface=cfg.adjust_per_surface)   # P4(a): per-surface WINNERS when on
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

def cmd_p4_bias(cfg: Config) -> int:
    # P4(b) cross-account reach dim-bias: one transaction wrapping apply_p4_dim_bias (no network — like
    # cmd_amplify_variants). Inert unless FANOPS_P4_DIM_BIAS is on AND learning is validated (the function
    # self-guards), so this verb is always safe to run/inspect. Amplify-only: never retires/deletes.
    from fanops.models import SourceState
    with Ledger.transaction(cfg) as led:
        before = len(led.sources_in_state(SourceState.moments_requested))
        led = apply_p4_dim_bias(led, cfg)
        after = len(led.sources_in_state(SourceState.moments_requested))
    write_digest(Ledger.load(cfg), cfg)
    print(f"p4-bias: {max(0, after - before)} source(s) amplified")
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

def cmd_health(cfg: Config, args=None) -> int:
    """MOL-299: dependency health from the unified model — human text or --json."""
    from fanops.health_model import build_health_report, report_is_healthy
    rep = build_health_report(cfg)
    if args is not None and getattr(args, "json", False):
        print(json.dumps(rep.to_json_dict(), indent=2))
    else:
        print("fanops health")
        for d in rep.deps:
            mark = "ok" if d.ok else "DOWN"
            print(f"  [{mark}] {d.name}: {d.detail}")
        for n in rep.notes:
            print(f"  - {n}")
    return 0 if report_is_healthy(rep) else 1


def cmd_doctor(cfg: Config, args=None) -> int:
    # Read-only first-run health screen (Phase 3b). Prints PASS/FAIL per setup gate + notes; exits 1
    # if any check fails (setup incomplete), else 0. Performs nothing — pure diagnosis + pointers.
    # R2: --fix-routing branches into the per-channel routing surveyor (read-only, lists every
    # accounts.json (handle, platform) drift state with a proposed fix; never auto-writes).
    if args is not None and getattr(args, "fix_routing", False):
        return _cmd_doctor_fix_routing(cfg)
    from fanops.health_model import build_health_report, report_is_healthy
    rep = build_health_report(cfg)
    if args is not None and getattr(args, "json", False):
        print(json.dumps(rep.to_json_dict(), indent=2))
        return 0 if report_is_healthy(rep) else 1
    print("fanops doctor")
    failed = 0
    for c in rep.checks:
        mark = "PASS" if c["ok"] else "FAIL"
        line = f"  [{mark}] {c['label']}"
        if not c["ok"]:
            failed += 1
            line += f"  -> {c['hint']}"
        print(line)
    for n in rep.notes:
        print(f"  - {n}")
    return 1 if failed else 0


def _cmd_doctor_fix_routing(cfg: Config) -> int:
    """R2 read-only surveyor: walk accounts.json, list every (handle, platform) routing-drift state
    with a proposed fix the operator can paste. NEVER auto-writes — drift is a sensitive config
    decision (which backend owns this id?), the operator picks. Drift = integrations[p] XOR backends[p].
    Proposes `postiz` for an IG/YouTube integration (the only realistic provider today) and asks the
    operator to pick postiz-or-zernio for TikTok. Exit 0 (read-only, never the failure exit)."""
    from fanops.accounts import load_accounts_safe
    accts, err = load_accounts_safe(cfg)
    if err:
        print(f"accounts.json unreadable: {err}", file=sys.stderr)
        return 0                                    # read-only surveyor never fails the exit
    print("fanops doctor --fix-routing (R2: routing-drift survey, read-only)")
    drift_count = 0
    for a in accts.accounts:
        for p in a.platforms:
            has_integ = bool(a.integrations.get(p.value))
            has_backend = bool(a.backends.get(p.value))
            if has_integ == has_backend:
                continue                            # both set (clean) or both unset (legacy) — fine
            drift_count += 1
            if has_integ and not has_backend:
                proposal = ("postiz" if p.value in ("instagram", "youtube")
                            else "postiz OR zernio (operator picks)")
                integ_id = a.integrations.get(p.value)
                print(f"  DRIFT: {a.handle}/{p.value}: integrations={integ_id!r}, backends=<UNSET>")
                print(f"     fix: fanops set-channel-routing --handle {a.handle} --platform {p.value} "
                      f"--backend {proposal} --integration-id {integ_id}")
                print("     reason: legacy FANOPS_POSTER bridge would silently route to dryrun on a live config")
            else:
                bk = a.backends.get(p.value)
                print(f"  DRIFT: {a.handle}/{p.value}: backends={bk!r}, integrations=<UNSET>")
                print(f"     fix: connect the {bk} integration first (Studio Go-Live tab), then re-route")
                print("     reason: backend has no id to publish through")
    if not drift_count:
        print("  no routing-drift found — every (handle, platform) is consistent.")
    else:
        print(f"  total drift channels: {drift_count}. NONE was modified — paste the proposed fix to apply.")
    return 0

def cmd_resolve(cfg: Config, args) -> int:
    """AUDIT H1: the documented human-reconcile escape hatch. When `reconcile` can't auto-resolve a
    post stuck in needs_reconcile (backend status ambiguous / never returns a terminal state), the
    operator checks the platform by hand and forces the ledger to ground truth:
    `fanops resolve <post_id> published --url <live-url>` or `... failed`. Tight transaction,
    local-only mutation (no network).

    R1/D10: resolving to `published` (or any terminal-with-URL state) now REQUIRES --url. Without
    it, the resolve closes the third door onto the ghost-row class (alongside D1: DryRunPoster,
    D2: _publish_one, D9: mark_published). Non-terminal targets (failed/error/etc) still resolve
    URL-less by design — a pre-network failure has nothing to point at. Order of checks: post
    existence first (so "no such post: <id>" wins over the URL message on a typo)."""
    from fanops.models import PostState, _POST_TERMINAL_REQUIRES_URL
    with Ledger.transaction(cfg) as led:
        if args.post_id not in led.posts:
            print(f"no such post: {args.post_id}", file=sys.stderr); return 2
        requires_url = args.status in {s.value for s in _POST_TERMINAL_REQUIRES_URL}
        if requires_url and not (getattr(args, "url", None) or "").strip():
            print(f"--url is REQUIRED when resolving to {args.status!r} (R1/D10): the post is moving "
                  f"to a terminal-success state and needs a permalink. If you don't have one, resolve "
                  f"to 'failed' instead.", file=sys.stderr)
            return 2
        p = led.posts[args.post_id]
        # R1: set the URL BEFORE the state flip so the @model_validator sees a consistent shape on
        # serialization (terminal-with-URL invariant holds at every persistence point).
        if getattr(args, "url", None):
            p.public_url = args.url
        try:
            p.state = PostState(args.status)
        except ValueError:
            # Unknown status string — back-compat: map "published" -> published, else "failed"
            p.state = PostState.published if args.status == "published" else PostState.failed
    print(f"resolved {args.post_id} -> {args.status}"); return 0


def cmd_audit(cfg: Config, args) -> int:
    """(R3/D7) `fanops audit tail [-n 20]` — print the last N lines of the operator
    audit log. Read-only; missing log -> 0 with a clear note."""
    sub = getattr(args, "audit_cmd", None) or "tail"
    if sub == "tail":
        from fanops.audit import read_audit_tail
        n = getattr(args, "n", 20) or 20
        lines = read_audit_tail(cfg, n=n)
        if not lines:
            print("(audit log empty — no state-changing actions recorded yet)")
            return 0
        for line in lines:
            print(line)
        return 0
    print(f"unknown audit subcommand: {sub!r}", file=sys.stderr); return 2


def cmd_bulk_send_to_review(cfg: Config, args) -> int:
    """(R3/D7) `fanops bulk-send-to-review p1 p2 ... --reason=…` — revert N posts to
    awaiting_approval and clear publish telemetry. Atomic; audited."""
    from fanops.studio.actions import bulk_send_to_review
    res = bulk_send_to_review(cfg, list(args.post_ids), reason=args.reason)
    if not res.ok:
        print(res.error, file=sys.stderr); return 2
    d = res.detail or {}
    print(f"moved {d.get('moved', 0)} -> awaiting_approval")
    unknown = d.get("unknown") or []
    if unknown:
        print(f"unknown ids skipped: {', '.join(unknown)}")
    return 0


# dryrun-boundary M3: cmd_revert_phantom_published + cmd_doctor_fix_ghosts are DELETED. Both existed
# only to detect/heal reconcile-laundered or pre-R1 ghost `published` rows — a class the boundary makes
# unconstructable. The migration-on-read back-fill they mirrored is gone from Ledger.load; the 29 legacy
# rows were pruned outright (M4). No detector, no healer, no CLI verb.


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
    from fanops.compose import compose_clip, TemplateSpec
    led = Ledger.load(cfg)
    clip = led.clips.get(args.clip_id)
    if clip is None or not clip.path:
        print(f"no such clip (or unrendered): {args.clip_id}"); return 2
    if not os.path.exists(clip.path):
        print(f"clip file missing on disk: {clip.path}"); return 2
    mom = led.moments.get(clip.parent_id)
    title = args.title
    if title is None and mom is not None:                    # default title = the clip's on-screen hook ONLY
        title = mom.hook                                     # RF5: no verbatim-transcript fallback; a hookless clip -> None -> the "nothing to compose" early-out below
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
    # per-account Render foundation: a Render file is reclaimable once NO live post points at it via
    # render_id — e.g. a reburn replaced it with a new content-addressed render, or its posts were deleted.
    # Reference-counted (not state-gated): content-addressed renders are SHARED, so reference is the right
    # liveness signal. Mirror the clip sweep — drop the FILE only (the Render record + its durable hook_text
    # persist for archive reconstruction); keep_days guards age exactly as for clips.
    referenced = {p.render_id for p in led.posts.values() if p.render_id}
    for r in led.renders.values():
        if r.id in referenced or not (r.path and os.path.exists(r.path)):
            continue
        try:
            if os.path.getmtime(r.path) < cutoff:
                os.remove(r.path); removed += 1
        except OSError as exc:
            print(f"gc: could not remove {r.path}: {exc}", file=sys.stderr)
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
            print(f"  wrapper {res['wrapper']}  |  interval {interval}s  |  loaded {res['loaded']}  |  responder {res['responder']}")
            if res["discloses_llm"]:                      # DISCLOSE the recurring-LLM cost — never silently turn the AI on
                print(f"  ⚠ hands-off runs the AI responder — invokes `claude` ~every {interval}s. Use `--responder manual` for no-LLM scheduling.")
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
    # daemon, then print a readiness report. dryrun by default (publishes nothing); going
    # live is a separate, deliberate step via Postiz or the manual publish-queue.
    rc = _bring_up_and_verify(cfg)
    if rc:
        print(f"autopilot: bring-up verify failed (exit {rc})", file=sys.stderr)
        return rc
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
    print("  go-live (separate, when you want posts to ship): self-host Postiz (FANOPS_POSTER=postiz) OR `fanops publish-queue` by hand")
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
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
    p_disc = sub.add_parser("discover"); p_disc.add_argument("folder")
    sub.add_parser("intake")
    p_comp = sub.add_parser("compose", help="produced clip: intro/outro brand cards + dynamic title + crossfades (MoviePy; needs .[compose])")
    p_comp.add_argument("clip_id")
    p_comp.add_argument("--title", default=None, help="on-screen title (default: the clip's hook)")
    p_comp.add_argument("--intro", default=None, help="intro card text (default: artist name; pass '' to disable)")
    p_comp.add_argument("--outro", default=None, help="outro card text, e.g. an @handle (default: none)")
    p_doctor = sub.add_parser("doctor", help="read-only first-run health screen (toolchain/accounts/key/go-live readiness)")
    p_doctor.add_argument("--fix-routing", action="store_true",
                          help="(R2) READ-ONLY: list every accounts.json (handle, platform) routing-drift state with a proposed fix")
    p_doctor.add_argument("--json", action="store_true", help="machine-readable health JSON (exit 1 when unhealthy)")
    p_health = sub.add_parser("health", help="runtime dependency health (docker/postiz/zernio) from the unified model")
    p_health.add_argument("--json", action="store_true", help="machine-readable JSON (exit 1 when unhealthy)")
    sub.add_parser("up", help="self-healing bring-up: start deps + preflight + health verify (headless)")
    sub.add_parser("publish-queue", help="list queued posts to publish BY HAND (manual / no-service free path)")
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
    p_learn = sub.add_parser("learn", help="learning-loop diagnostics (read-only)")
    learn_sub = p_learn.add_subparsers(dest="learn_cmd", required=True)
    learn_sub.add_parser("doctor", help="read-only: does live Postiz analytics carry the reach signal lift_score needs?")
    p_hash = sub.add_parser("hashtags", help="reach-ranked hashtag store from LIVE Meta Graph reach")
    hash_sub = p_hash.add_subparsers(dest="hashtags_cmd", required=True)
    hash_sub.add_parser("refresh", help="rebuild 00_control/hashtags.json from live Graph reach (harvest->measure->rank; needs Meta creds, fail-open)")
    hash_sub.add_parser("discover", help="report fresh per-persona hashtags from live category top_media (needs Meta creds; never writes the menu)")
    p_lever = sub.add_parser("lever", help="persona lever reference docs (generated from the live registry)")
    lever_sub = p_lever.add_subparsers(dest="lever_cmd", required=True)
    lever_sub.add_parser("docs", help="regenerate docs/LEVERS.md + docs/LEVER-THRESHOLDS.md")
    p_thresh = sub.add_parser("threshold", help="selection threshold reference docs (generated from live constants)")
    thresh_sub = p_thresh.add_subparsers(dest="thresh_cmd", required=True)
    thresh_sub.add_parser("docs", help="regenerate docs/LEVERS.md + docs/LEVER-THRESHOLDS.md")
    p_run = sub.add_parser("run"); p_run.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    p_dae = sub.add_parser("daemon", help="run fanops unattended via launchd (survives logout, restarts on crash)")
    dae_sub = p_dae.add_subparsers(dest="dae_cmd", required=True)
    p_dins = dae_sub.add_parser("install", help="install + load the launchd agent (macOS)")
    p_dins.add_argument("--interval", default="10m")
    # DECOUPLED AI switch: 'inherit' (default) installs scheduling WITHOUT forcing the LLM on — the run
    # resolves the ambient responder. 'llm'/'manual' persist an explicit choice to .env (durable).
    p_dins.add_argument("--responder", default="inherit", choices=["inherit", "llm", "manual"])
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
        # Bad/missing poster key (Postiz or Zernio, or a 401) escaping a publish — operator-actionable.
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
    account_id on an active account is caught before a run, never reaching the backend).
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

    The default dryrun+manual config (no creds) trips neither and passes cleanly (exit 0)."""
    import shutil
    problems = []
    if cfg.responder_mode == "llm" and shutil.which("claude") is None:
        problems.append(
            "FANOPS_RESPONDER=llm but `claude` is not on PATH — the autonomous responder shells "
            "`claude -p` using your existing Claude subscription. Install Claude Code and run "
            "`claude login` on this host (no API key needed).")
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


def _bring_up_and_verify(cfg: Config) -> int:
    """MOL-301: compose health.ensure_up + postiz_lifecycle.ensure_up + preflight + B2 verify."""
    from fanops.health import ensure_up
    from fanops.postiz_lifecycle import ensure_up as postiz_ensure_up
    from fanops.health_model import build_health_report, report_is_healthy
    for line in ensure_up(cfg):
        print(f"  {line}")
    postiz_ensure_up(cfg)
    if (rc := _check_preflight(cfg)):
        return rc
    rep = build_health_report(cfg)
    for d in rep.deps:
        print(f"  [{'ok  ' if d.ok else 'DOWN'}] {d.name}: {d.detail}")
    return 0 if report_is_healthy(rep) else 1


def cmd_up(cfg: Config, args=None) -> int:
    """Headless self-healing bring-up — studio launch block minus Flask, exit-coded."""
    print("fanops up — bringing dependencies up and verifying health")
    return _bring_up_and_verify(cfg)


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


def _dep_health_event(cfg: Config) -> None:
    """MOL-300: red/green dependency-health event beside heartbeat — headless run.log visibility."""
    from fanops.health_model import build_health_report, report_is_healthy
    rep = build_health_report(cfg)
    healthy = report_is_healthy(rep)
    deps = [{"name": d.name, "ok": d.ok, "detail": d.detail} for d in rep.deps]
    evt = {"dep_health": "ok" if healthy else "DOWN", "deps": deps}
    print(json.dumps(evt))
    get_logger(cfg)("dep_health", "-", "ok" if healthy else "down", deps=deps)


def _dispatch(cfg: Config, args) -> int:
    if args.cmd == "status":   return cmd_status(cfg)
    if args.cmd == "recover":
        if args.recover_cmd == "audit": return cmd_recover_audit(cfg)
        return 2
    if args.cmd == "ingest":
        # Phase-B-followup: catalogue under a transaction (B4). ffprobe runs inside the lock here,
        # but it is LOCAL + fast (tens of ms/file) — unlike the network commands, there is no slow
        # call to keep out, and the dedup (already_seen) needs the loaded ledger, so one transaction
        # is the right unit.
        with Ledger.transaction(cfg) as led:
            led, counts = ingest_drops(led, cfg)
            total = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
        print(f"ingested -> {counts.added} new ({total} total; {counts.deduped} dup, "
              f"{counts.excluded} excluded, {counts.skipped} skipped)"); return 0   # ING-2: this-pass delta, not cumulative
    if args.cmd == "pull":
        # Phase-B-followup: the yt-dlp DOWNLOAD (network, slow) runs OUTSIDE the lock; only the
        # ingest of what landed runs inside the transaction.
        from fanops.ingest import _pull_stage
        produced = download_url(cfg, args.url)       # network, NO lock held; returns the files it produced (in .pull stage)
        with Ledger.transaction(cfg) as led:
            # per-file origin (audit c0-f1 / ING-6): the pull catalogues ONLY its isolated .pull stage, so a
            # manual drop sitting in the inbox is never re-scanned or mislabeled by this pull.
            led, counts = ingest_drops(led, cfg, origin="url", inbox=_pull_stage(cfg), origin_paths=produced)
            total = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
        print(f"pulled -> {counts.added} new ({total} total)"); return 0
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
    if args.cmd == "map-media": return cmd_map_media(cfg)
    if args.cmd == "verify-live": return cmd_verify_live(cfg)
    if args.cmd == "reconcile": return cmd_reconcile(cfg)
    if args.cmd == "adjust":   return cmd_adjust(cfg, args.winner_pct, args.retire_pct, args.lift_floor)
    if args.cmd == "amplify-variants": return cmd_amplify_variants(cfg)
    if args.cmd == "p4-bias": return cmd_p4_bias(cfg)
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
        if args.hashtags_cmd == "discover":
            from fanops.fanops_hashtags import cmd_hashtags_discover  # lazy: keeps it off the hot path
            return cmd_hashtags_discover(cfg)
        return 2
    if args.cmd in ("lever", "threshold"):
        if getattr(args, "lever_cmd", None) == "docs" or getattr(args, "thresh_cmd", None) == "docs":
            from fanops.lever_docs import cmd_lever_docs
            return cmd_lever_docs(cfg)
        return 2
    if args.cmd == "health":   return cmd_health(cfg, args)
    if args.cmd == "up":       return cmd_up(cfg, args)
    if args.cmd == "doctor":   return cmd_doctor(cfg, args)
    if args.cmd == "publish-queue": return cmd_publish_queue(cfg)
    if args.cmd == "daemon":   return cmd_daemon(cfg, args)
    if args.cmd == "autopilot": return cmd_autopilot(cfg, args)
    if args.cmd == "gc":       return cmd_gc(cfg, args.keep_days if args.keep_days is not None else cfg.gc_keep_days)
    if args.cmd == "compose":  return cmd_compose(cfg, args)
    if args.cmd == "resolve":
        return cmd_resolve(cfg, args)
    if args.cmd == "audit":
        return cmd_audit(cfg, args)
    if args.cmd == "bulk-send-to-review":
        return cmd_bulk_send_to_review(cfg, args)
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
        from fanops.pipeline import resume_source
        with Ledger.transaction(cfg) as led:
            if args.source_id not in led.sources:
                print(f"no such source: {args.source_id}", file=sys.stderr); return 2
            resume_source(led, args.source_id, from_stage=args.from_stage)   # MOL-121: AUTO keeps a good transcript
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
        rc = _bring_up_and_verify(cfg)
        if rc:
            print(f"studio: bring-up verify returned {rc} — starting UI anyway", file=sys.stderr)
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
            get_logger(cfg)("run", "-", "gates_blocked", **s["awaiting"])   # WS2: log EVERY gate kind, not just moments/captions
        # E1: post-loop learning pass — close the feedback loop ONCE per `run` after respond+advance
        # converges. Gated by the identical reconcile guard (pipeline.py:106): live backend + key
        # only. In dryrun (default) the guard short-circuits and the pass is NEVER entered. Runs in
        # its own lock-safe transaction (won't race the next advance); a pull/classify/amplify/retire
        # hiccup is logged and swallowed so it can NEVER crash the unattended run (exit stays 0).
        if cfg.is_live_backend:
            try:
                _learn_pass(cfg)
            except AuthError as e:
                # A bad/rotated key is actionable, not a transient 5xx — surface it VISIBLY on stderr +
                # a distinct breadcrumb, but keep exit 0: the unattended run SKIPS the learn pass cleanly,
                # mirroring cmd_track/cmd_reconcile (read paths skip; only the WRITE path publish_due halts).
                print(f"learn skipped: auth failure ({type(e).__name__}) — check the API key", file=sys.stderr)
                get_logger(cfg)("learn", "-", "auth_error", err=f"{type(e).__name__}: {str(e)[:120]}")
            except Exception as e:
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
        # P4(b) cross-account reach dim-bias: SYMMETRIC with variant_amplify — a SEPARATE, independently
        # gated learning pass so the unattended run applies a proven higher-reach creative dim, not only
        # the manual `fanops p4-bias` verb. Gated by its OWN kill switch (cfg.p4_dim_bias, default OFF) AND
        # the live-backend+key guard; apply_p4_dim_bias is amplify-only AND stays INERT until cutover
        # validation (validation_gate.learning_validated), so wiring it in is fail-SAFE. Own try/except —
        # a hiccup is swallowed (exit stays 0) and can't touch the blocks above.
        if cfg.p4_dim_bias and cfg.is_live_backend:
            try:
                with Ledger.transaction(cfg) as led:
                    led = apply_p4_dim_bias(led, cfg)
            except Exception as e:
                get_logger(cfg)("p4_dim_bias", "-", "error", err=str(e)[:120])
        # Leg 3 (timing): SYMMETRIC with p4_dim_bias — a SEPARATE, independently gated pass so the unattended
        # run refreshes the reach-winning publish-HOUR prior (consumed by the next crosspost's surface_time).
        # Own kill switch (cfg.timing_bias, default OFF) AND the live-backend guard; apply_timing_bias is
        # bias-only (writes ONE prior file, never retires) AND validation-frozen, so wiring it in is fail-SAFE.
        # Own try/except — a hiccup is swallowed (exit stays 0) and can't touch the blocks above.
        if cfg.timing_bias and cfg.is_live_backend:
            try:
                with Ledger.transaction(cfg) as led:
                    led = apply_timing_bias(led, cfg)
            except Exception as e:
                get_logger(cfg)("timing_bias", "-", "error", err=str(e)[:120])
        # WS2: constant Graph-reach hashtag store update — refresh at most once per cadence (12h), throttled by
        # the store mtime so the 10-min publish cadence doesn't hammer the 30/7-day Graph budget. NOT gated on
        # is_live_backend (a hashtag's worth is its live platform reach, independent of whether WE publish) —
        # only on Meta creds, handled inside the helper. Its OWN try/except; refresh_store_if_due never raises,
        # so the unattended run can never break on a hashtag refresh.
        try:
            from fanops.fanops_hashtags import refresh_store_if_due
            r = refresh_store_if_due(cfg)
            if r.get("aborted"):     # corrupt personas.json: refresh_store preserved the store — report the abort LOUDLY,
                                     # never the false-success store_refreshed (a bad control file stripping strategy is not routine)
                get_logger(cfg)("hashtags", "-", "store_refresh_aborted", aborted=r.get("aborted"), reason=r.get("reason", ""))
            elif r.get("refreshed"):
                get_logger(cfg)("hashtags", "-", "store_refreshed", measured=r.get("measured", 0), total=r.get("total", 0))
        except Exception as e:
            get_logger(cfg)("hashtags", "-", "refresh_error", err=f"{type(e).__name__}: {str(e)[:120]}")
        # E2: emit one heartbeat for the WHOLE run from the final advance summary (so
        # published_in_run/last_published_age_hours reflect this run incl. the learning pass effect).
        _heartbeat(cfg, s)
        _dep_health_event(cfg)
        print(s); return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
