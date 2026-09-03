"""CLI run loop — respond+advance converge, learning passes, heartbeat (extracted from cli.py, SA-C8-4).

Public callers and tests should continue to import run-loop symbols from ``fanops.cli``; this module is
the implementation home for the unattended ``fanops run`` pump.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import fanops
from fanops.config import Config
from fanops.errors import AuthError, fail_open
from fanops.escalation import EscalationPosture, decide
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops import daemon
from fanops.log import get_logger


def gates_blocked_note(s) -> str | None:
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


def learn_pass(cfg: Config, *, window: str = "30d") -> None:
    from fanops import cli
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
    rows = list(cli._default_list_posts(cfg, posts=pollable_posts)(window))   # network, NO lock held (per-post backend routing)
    with Ledger.transaction(cfg) as led:
        led = cli.pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
        r = cli.classify_outcomes(led, per_surface=cfg.adjust_per_surface)   # P4(a): per-surface WINNERS when on
        # BOTH learn-pass actuators carry an operator-INTENT flag, default OFF, and both leave a
        # breadcrumb whichever way they go. AMPLIFY MINTS NEW WORK — a winner re-opens a moment
        # request on its source, producing new moments -> clips -> posts. RETIRE DESTROYS — a loser's
        # clip is suppressed, its moment too when no live sibling remains, and every unshipped post of
        # that lineage is rewritten to `retired`. Both ran on `cfg.is_live_backend` alone, so going
        # live to PUBLISH switched on an autonomous generator AND an autonomous destroyer. NOTE the
        # validation freeze is deliberately absent: nothing ever writes metrics_confirmed False, so
        # once the first real metric auto-stamps it `learning_validated` can never re-bind — a
        # condition that cannot bind is theatre, not a gate. With both flags OFF this pass is
        # read-only: pull metrics, classify, log the counts, write nothing.
        if cfg.learn_amplify:
            before = {sid: int(s.meta.get("amplify_count", 0)) for sid, s in led.sources.items()}
            led = cli.amplify(led, cfg, r["winners"])
            fired = [sid for sid, s in led.sources.items() if int(s.meta.get("amplify_count", 0)) > before.get(sid, 0)]
            get_logger(cfg)("learn", "-", "amplified", sources=len(fired), winners=len(r["winners"]))
        else:
            get_logger(cfg)("learn", "-", "amplify_skipped", winners=len(r["winners"]))
        if cfg.learn_retire:
            led = cli.retire(led, r["losers"])
            get_logger(cfg)("learn", "-", "retired", losers=len(r["losers"]))
        else:
            get_logger(cfg)("learn", "-", "retire_skipped", losers=len(r["losers"]))


def fresh_run_base_time() -> str:
    """UTC now as --base-time (matches a per-iteration resident loop advance)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _CmdRunPassOutcome:
    status: dict | None
    halt_stderr: str | None = None
    reraise: BaseException | None = None
    gates_stderr: str | None = None
    learn_skip_stderr: str | None = None


def cmd_run_pass(cfg: Config, base_time: str) -> _CmdRunPassOutcome:
    """One respond+advance converge-then-learn pass. status=None with halt_stderr = halted."""
    from fanops import cli
    from fanops.fanops_hashtags import reset_safari_tick_slot
    from fanops.pipeline_run import paused, run_lease
    reset_safari_tick_slot()
    # T1.3: the operator brake, checked BEFORE the lease is taken — a paused pump must not even
    # contend for the run flock, so `fanops advance` by hand stays unblocked while paused. Returning a
    # DICT (not None) is load-bearing twice over: the --loop path still emits its heartbeat (a silent
    # pause would freeze the recorded code SHA and the keeper would SIGTERM-kickstart every ~720s after
    # any code change), and the one-shot path exits 0 because a pause is not a failure. `awaiting: {}`
    # keeps gates_blocked_note quiet — no open gates, so it returns None.
    if paused(cfg):
        get_logger(cfg)("run", "-", "paused")
        return _CmdRunPassOutcome(status={"paused": True, "awaiting": {}})
    # unattended: respond to gates, advance, repeat until no progress.
    # BOTH the responder and advance() are inside the guard: advance()'s deterministic
    # stages are per-unit quarantined, but the responder (FIX H7 — the LLM model call or a
    # response that fails validation can raise) and crosspost/publish run outside those
    # guards, and publish_due RE-RAISES on fatal auth (bad key/401) by design. So a raise
    # from either degrades cleanly here (log one line + stop) rather than crashing the
    # unattended cron loop with a traceback.
    s = None
    with run_lease(cfg):
        for _ in range(10):
            try:
                cli.get_responder(cfg).answer_pending(cfg)
                s = cli.advance(cfg, base_time=base_time)
            except Exception as e:
                # Progress spine: converge fault → NONZERO (None → cmd_run exit 1; loop skips tick).
                get_logger(cfg)("run", "-", "halted", err=f"{type(e).__name__}: {e}"[:160])
                halt_stderr = f"run halted: {type(e).__name__}: {e}"
                if decide("toolchain_run", 0) is EscalationPosture.nonzero:
                    return _CmdRunPassOutcome(status=None, halt_stderr=halt_stderr)
                return _CmdRunPassOutcome(status=None, halt_stderr=halt_stderr, reraise=e)
            # Converge only when EVERY gate is clear. any() over all awaiting kinds (moments, captions)
            # is robust to future gates too — a run that exits with any open has not produced its clips/posts.
            if not any(s["awaiting"].values()):
                break
    # B2 / MOL-960: gates still awaiting after converge → LOUD stderr + run.log; cmd_run maps this
    # to exit 1 (intentional pause keeps awaiting={} so exit stays 0).
    gates_stderr = gates_blocked_note(s)
    if gates_stderr:
        get_logger(cfg)("run", "-", "gates_blocked", **s["awaiting"])   # WS2: log EVERY gate kind, not just moments/captions
    learn_skip_stderr = None
    # E1: post-loop learning pass — close the feedback loop ONCE per `run` after respond+advance
    # converges. Gated by the identical reconcile guard (pipeline.py:106): live backend + key
    # only. In dryrun (default) the guard short-circuits and the pass is NEVER entered. Runs in
    # its own lock-safe transaction (won't race the next advance); a pull/classify/amplify/retire
    # hiccup is logged and swallowed so it can NEVER crash the unattended run (exit stays 0).
    if cfg.is_live_backend:
        try:
            learn_pass(cfg)
        except AuthError as e:
            # A bad/rotated key is actionable, not a transient 5xx — surface it VISIBLY on stderr +
            # a distinct breadcrumb, but keep exit 0: the unattended run SKIPS the learn pass cleanly,
            # mirroring cmd_track/cmd_reconcile (read paths skip; only the WRITE path publish_due halts).
            learn_skip_stderr = f"learn skipped: auth failure ({type(e).__name__}) — check the API key"
            get_logger(cfg)("learn", "-", "auth_error", err=f"{type(e).__name__}: {str(e)[:120]}")
        except Exception:
            with fail_open("cli._run_once learn degrade:"):
                raise
    # variant-amplify (v3): a SEPARATE, independently-gated learning pass — proven SUSTAINED
    # variant winners auto-amplify their source. Gated by its OWN kill switch (cfg.variant_amplify,
    # default OFF) AND the same live-backend+key guard as the learn block. Its OWN try/except so it
    # can never affect the block above and a hiccup is swallowed (exit stays 0). apply_variant_amplify
    # is amplify-only (never retires/deletes) and self-guards on the flag, so this is fail-SAFE.
    if cfg.variant_amplify and cfg.is_live_backend:
        try:
            with Ledger.transaction(cfg) as led:
                led = cli.apply_variant_amplify(led, cfg)
        except Exception:
            with fail_open("cli._run_once variant_amplify degrade:"):
                raise
    # P4(b) cross-account reach dim-bias: SYMMETRIC with variant_amplify — a SEPARATE, independently
    # gated learning pass so the unattended run applies a proven higher-reach creative dim, not only
    # the manual `fanops p4-bias` verb. Gated by its OWN kill switch (cfg.p4_dim_bias, default OFF) AND
    # the live-backend+key guard; apply_p4_dim_bias is amplify-only AND stays INERT until cutover
    # validation (validation_gate.learning_validated), so wiring it in is fail-SAFE. Own try/except —
    # a hiccup is swallowed (exit stays 0) and can't touch the blocks above.
    if cfg.p4_dim_bias and cfg.is_live_backend:
        try:
            with Ledger.transaction(cfg) as led:
                led = cli.apply_p4_dim_bias(led, cfg)
        except Exception:
            with fail_open("cli._run_once p4_dim_bias degrade:"):
                raise
    # Leg 3 (timing): SYMMETRIC with p4_dim_bias — a SEPARATE, independently gated pass so the unattended
    # run refreshes the reach-winning publish-HOUR prior (consumed by the next crosspost's surface_time).
    # Own kill switch (cfg.timing_bias, default OFF) AND the live-backend guard; apply_timing_bias is
    # bias-only (writes ONE prior file, never retires) AND validation-frozen, so wiring it in is fail-SAFE.
    # Own try/except — a hiccup is swallowed (exit stays 0) and can't touch the blocks above.
    if cfg.timing_bias and cfg.is_live_backend:
        try:
            with Ledger.transaction(cfg) as led:
                led = cli.apply_timing_bias(led, cfg)
        except Exception:
            with fail_open("cli._run_once timing_bias degrade:"):
                raise
    # HV1-PR4: vocab expand is not called from the run loop (it restocked persona search seeds).
    # Module stays on disk; the tick remesures sidecar pile∪lock names only.
    # WS2: remesure sidecar names at most once per cadence (12h), gated on last_complete_pass (not
    # file mtime) so a throttled write cannot buy silence. NOT gated on is_live_backend — only on
    # scrape session, handled inside the helper. Its OWN try/except; refresh_store_if_due never
    # raises. Non-fresh skips log (MOL-525): a missing scrape session must not look identical to a
    # correctly-throttled tick.
    try:
        r = cli.refresh_store_if_due(cfg)
        if r.get("aborted"):     # no_scrape / freeze / busy: report the abort LOUDLY, never a false
                                 # store_refreshed (a skipped remesure is not a refresh)
            get_logger(cfg)("hashtags", "-", "store_refresh_aborted", aborted=r.get("aborted"), reason=r.get("reason", ""))
        elif r.get("refreshed"):
            get_logger(cfg)("hashtags", "-", "store_refreshed", measured=r.get("measured", 0), total=r.get("total", 0))
        elif r.get("reason") and r.get("reason") != "fresh":
            get_logger(cfg)("hashtags", "-", "store_refresh_skipped", reason=r.get("reason", ""))
    except Exception:
        with fail_open("cli._run_once hashtags refresh degrade:"):
            raise
    # U3: throttled IG follower snapshot — own try/except; refresh_account_stats_if_due never raises.
    try:
        from fanops.fanops_account_stats import refresh_account_stats_if_due
        r = refresh_account_stats_if_due(cfg)
        if r.get("refreshed"):
            get_logger(cfg)("account_stats", "-", "refreshed", updated=r.get("updated", 0), total=r.get("total", 0))
    except Exception:
        with fail_open("cli._run_once account_stats refresh degrade:"):
            raise
    return _CmdRunPassOutcome(status=s, gates_stderr=gates_stderr, learn_skip_stderr=learn_skip_stderr)


_RUNNING_CODE_SHA: tuple[str | None] | None = None   # process-lifetime snapshot; see running_code_sha


def running_code_sha(cfg: Config) -> str | None:
    """The git-HEAD SHA this pump PROCESS was loaded from, snapshotted ONCE at the first heartbeat and
    cached for the process's life. This is deliberately NOT re-read per tick: _version_signal reads the
    checkout's CURRENT on-disk HEAD, so after an operator `git pull` it would report the NEW disk SHA
    while this process still runs the OLD code in memory — which would make the keeper's drift check
    (heartbeat `code` vs disk SHA) ALWAYS equal and adoption NEVER fire. A start-of-process snapshot is
    the running-code truth the keeper needs: it stays the OLD SHA until a restart loads the new code and
    a fresh process snapshots the new SHA (clearing the drift). Also spares a `git rev-parse` per tick."""
    global _RUNNING_CODE_SHA
    if _RUNNING_CODE_SHA is None:
        _RUNNING_CODE_SHA = (daemon._version_signal(cfg)[0],)
    return _RUNNING_CODE_SHA[0]


def heartbeat_dict(cfg: Config, s: dict) -> dict:
    """Build the heartbeat JSON payload (stdout print stays in cli.py for the print-count ratchet)."""
    from fanops import cli
    return {
        "heartbeat": datetime.now(timezone.utc).isoformat(),
        "fanops_version": fanops.__version__,
        "published_in_run": s.get("published_in_run", 0),
        # UNCONDITIONAL, unlike `origin`: a monitor telling "paused" from "dead" needs the key on EVERY
        # line — a key present only when true makes its absence ambiguous between "not paused" and "old code".
        "paused": bool(s.get("paused", False)),
        "last_published_age_hours": s.get("last_published_age_hours"),
        "code": cli._running_code_sha(cfg),   # patch surface: tests stub fanops.cli._running_code_sha
    }


def heartbeat_log(cfg: Config, hb: dict, *, origin: str | None = None) -> None:
    """Append heartbeat to run.log via get_logger (follows the stdout print in cli._heartbeat)."""
    fields = dict(hb)
    if origin:
        fields["origin"] = origin
    get_logger(cfg)("heartbeat", "-", "ok", **fields)
