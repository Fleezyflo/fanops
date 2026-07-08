# C8: Ops, CLI & Daemon

## Files covered (all 12 read in full, cross-checked against structural_index.json — function/method lists match exactly)

1. `src/fanops/cli.py` (957 lines) — read
2. `src/fanops/daemon.py` (264 lines) — read
3. `src/fanops/doctor.py` (107 lines) — read
4. `src/fanops/cutover.py` (91 lines) — read
5. `src/fanops/cutover_postiz.py` (92 lines) — read
6. `src/fanops/health.py` (115 lines) — read
7. `src/fanops/health_model.py` (179 lines) — read — **MOL-298: typed health owner; doctor/health/learn_doctor are views**
8. `src/fanops/digest.py` (307 lines) — read
9. `src/fanops/audit.py` (60 lines) — read
10. `src/fanops/timeutil.py` (152 lines) — read
11. `src/fanops/_fwrun.py` (87 lines) — read
12. `src/fanops/__init__.py` (1 line) — read

## Pipeline/data-flow overview

```
                                   operator shell
                                        │
                                   fanops <verb>
                                        │
                              cli.main() (argparse)
                                        │
                              cli._dispatch(cfg, args)
                    ┌───────────────────┼────────────────────────────┐
                    │                   │                            │
         READ-ONLY diagnostics   TRANSACTIONAL verbs          LIVE / DAEMON verbs
    status/recover/audit/doctor  ingest/advance/track/adjust  pull/run/daemon/cutover/
    publish-queue/map-media      reconcile/gc/resolve/unhold  autopilot/studio
                    │             retry-*/bulk-send-to-review          │
                    │                   │                              │
                    ▼                   ▼                              ▼
            Ledger.load (RO)   Ledger.transaction(cfg)          ingest.download_url /
            digest.render_*    (flock) → pipeline.advance /     daemon.install/status/
                                adjust.classify_outcomes/        stop / cutover_postiz.* /
                                amplify/retire / track.pull_     health.ensure_up /
                                metrics / reconcile.reconcile_   studio.app.create_app
                                due (network OUTSIDE lock,
                                apply INSIDE lock)
                                        │
                                write_digest(led, cfg)  ── digest.py renders reports/digest.md
                                        │
                        `fanops run` loop (unattended):
             ┌──────────────────────────────────────────────────────────┐
             │ for up to 10 iterations:                                  │
             │   responder.answer_pending(cfg)   # drains agent gates    │
             │   s = pipeline.advance(cfg, base_time=...)                │
             │   break when no gate is awaiting                          │
             │ → _gates_blocked_note if still stuck                      │
             │ → _learn_pass (live-backend only)                         │
             │ → variant_amplify / p4_dim_bias / timing_bias passes      │
             │   (each own kill switch + own try/except, swallow-safe)   │
             │ → refresh_store_if_due (hashtags, own try/except)         │
             │ → _heartbeat(cfg, s)  → stdout + run.log                  │
             └──────────────────────────────────────────────────────────┘
                                        │
                     daemon.py (launchd wrapper around `fanops run`)
                  install → wrapper.sh + plist → launchctl bootstrap
                  StartInterval fires wrapper.sh every N seconds
                  wrapper.sh: cd cfg.root && exec fanops run --base-time "$(date -u ...)"
                  (one-shot per fire; launchd itself is the loop/scheduler)
```

Distinct from the above: `cutover.py` / `cutover_postiz.py` are a **manual, operator-only, four-step go-live proof harness** (`fanops cutover auth|post|metrics|lift`) that is NEVER imported or reached from `run`/`advance`/`daemon` — it writes exclusively to `00_control/cutover.json`.

`timeutil.py` is a pure leaf (no side effects, no imports of ledger/config-writing code) consumed by `crosspost.py`, `reconcile.py`, `studio/*`, `pipeline.py`, and `digest.py` is unrelated to CLI dispatch — it's an observability renderer called from many transactional verbs after their transaction closes. `_fwrun.py` is a standalone subprocess entry point (`python -m fanops._fwrun`), spawned by `transcribe.py` (outside this cluster) — not part of the CLI/daemon dispatch tree at all, but included in this cluster's file list.

## CLI verb inventory (complete — every subparser in `cli.main`)

| Verb | Handler | Purpose | LIVE / READ-ONLY |
|---|---|---|---|
| `status` | `cmd_status` | Print unit/post counts + gate-awaiting counts + effective backend | READ-ONLY |
| `ingest` | inline in `_dispatch` (`ingest_drops`) | Catalogue `01_inbox/` files into the ledger | READ-ONLY (local disk copy/hash; no external service) |
| `digest` | inline (`write_digest`) | Write `reports/digest.md` from current ledger state | READ-ONLY |
| `respond` | inline (`get_responder(cfg).answer_pending`) | Drain pending agent gates (LLM or manual responder) | **LIVE** if `FANOPS_RESPONDER=llm` (shells `claude -p`); no-op/manual otherwise — costs LLM tokens when live |
| `reconcile` | `cmd_reconcile` | Poll backend (Postiz/Zernio) for submitting/needs_reconcile posts, apply ground truth | **LIVE** (network poll against a real backend if creds are set; skips cleanly in dryrun) |
| `recover audit` | `cmd_recover_audit` | Read-only delivery bucket table (live_trackable/inflight/queued/failed) | READ-ONLY |
| `advance` | inline (`pipeline.advance`) | Run one pipeline sweep (transcribe/moments/clip/caption/crosspost/publish_due) | **LIVE** — `publish_due` inside `advance()` publishes any `queued` post whose scheduled_time is due, on whatever backend is configured (dryrun writes a payload only; postiz/zernio actually POSTs) |
| `pull <url>` | inline (`download_url` + `ingest_drops`) | Shell `yt-dlp` to download a URL, then catalogue the result | **LIVE**-ish (external network fetch of media, not a publish, but hits an external service and costs bandwidth/time) |
| `track` | `cmd_track` | Pull metrics from the live backend for published/analyzed posts | **LIVE** (network call to Postiz/Zernio analytics; skips cleanly with no key) |
| `map-media` | `cmd_map_media` | Resolve IG post → Graph media_id via live Meta Graph read | **LIVE** (read-only Graph API call; needs Meta creds; fail-open to no-op without them) |
| `adjust` | `cmd_adjust` | Classify winners/losers by lift_score, amplify winners, retire losers | READ-ONLY w.r.t. external services (pure ledger + local agent-request-file writes; no network) |
| `amplify-variants` | `cmd_amplify_variants` | Apply variant-gated amplification (v3), inert unless `FANOPS_VARIANT_AMPLIFY` on | READ-ONLY (local ledger mutation + request-file writes only) |
| `p4-bias` | `cmd_p4_bias` | Apply cross-account reach dim-bias, inert unless flag on + learning validated | READ-ONLY (local only) |
| `resolve <post_id> <status>` | `cmd_resolve` | Manually force a post's terminal state (operator escape hatch for ambiguous backend state) | READ-ONLY (local ledger mutation only — records the operator's manual observation of a REAL external event, does not itself call out) |
| `unhold <clip_id>` | inline in `_dispatch` | Clear a brand-risk hold, re-enter the caption gate | READ-ONLY (local only) |
| `retry-source <source_id>` | inline in `_dispatch` | Reset a source to `catalogued` to force re-transcribe | READ-ONLY (local only; triggers local whisper re-run on next `advance`, not itself an external call) |
| `retry-metrics <post_id>` | inline in `_dispatch` | No-op marker verb — a `published` post is already re-pollable by `track` | READ-ONLY |
| `discover <folder>` | inline (`discover.discover`) | Scan a folder, write thumbnails + manifest into `00_review/` | READ-ONLY (local ffmpeg thumbnail extraction only) |
| `intake` | inline (`discover.intake`) | Copy operator-approved `00_review/approved/*` originals into `01_inbox/` | READ-ONLY (local file copy) |
| `compose <clip_id>` | `cmd_compose` | Composite intro/outro/title cards onto a rendered clip via MoviePy | READ-ONLY (local render only, needs `[compose]` extra; fails open to base clip) |
| `doctor` | `cmd_doctor` | First-run health screen: toolchain, accounts, key, live-route coherence, IG insights readability | READ-ONLY |
| `doctor --fix-routing` | `_cmd_doctor_fix_routing` | Read-only survey of per-channel routing drift + proposed fix text (never auto-writes) | READ-ONLY |
| `publish-queue` | `cmd_publish_queue` | List queued posts for manual by-hand publishing | READ-ONLY |
| `audit tail [-n]` | `cmd_audit` | Print last N lines of the operator audit log | READ-ONLY |
| `bulk-send-to-review <post_ids> --reason` | `cmd_bulk_send_to_review` | Revert N posts to `awaiting_approval`, clearing publish telemetry | READ-ONLY (local ledger only; reverses a publish INTENT, doesn't call an external service) |
| `studio` | inline in `_dispatch` (`studio.app.create_app`) | Launch the local Flask cockpit web UI | READ-ONLY to start (may bring up Docker/Postiz via `health.ensure_up`) — **LIVE actions are reachable from within the UI** (Studio approve/publish flows are the human-gated live surface, outside this cluster) |
| `cutover auth` | `cmd_cutover` → `cutover.cutover_auth` | Step 1: prove `POSTIZ_API_KEY` authenticates (read-only integrations probe) | **LIVE** (real network call to Postiz, no mutation) |
| `cutover post <account_id> --i-understand-...` | `cmd_cutover` → `cutover.cutover_post` | Step 2: publish ONE real throwaway post at a 2099 schedule to a confirmed account | **LIVE / DESTRUCTIVE-ISH** — actually POSTs to a real Postiz integration; gated by `cfg.is_live`, an explicit `--i-understand-this-posts-to-a-real-account` flag, and integration-id validation |
| `cutover metrics <submission_id>` | `cmd_cutover` → `cutover.cutover_metrics` | Step 3: pull the real metrics row for the cutover post | **LIVE** (network read) |
| `cutover lift <submission_id>` | `cmd_cutover` → `cutover.cutover_lift` | Step 4: compute lift_score from the captured row | READ-ONLY (pure computation on already-captured local data) |
| `learn doctor` | inline (`learn_doctor.cmd_learn_doctor`) | Read-only: does live analytics carry the reach signal lift_score needs | READ-ONLY (lazy-imported; likely reads live data, but non-mutating) |
| `hashtags refresh` | inline (`fanops_hashtags.cmd_hashtags_refresh`) | Rebuild hashtag store from live Meta Graph reach (harvest→measure→rank) | **LIVE** (Meta Graph reads; fail-open without creds) |
| `hashtags discover` | inline (`fanops_hashtags.cmd_hashtags_discover`) | Report fresh per-persona hashtags from live category top_media | **LIVE** (Meta Graph reads; read-only, never writes the menu) |
| `run` | inline in `_dispatch` | The unattended loop: respond+advance to convergence, then learning passes, then heartbeat | **LIVE** — this is THE verb that autonomously publishes due posts (via `advance`→`publish_due`), calls the LLM responder if configured, and runs all gated learning-bias passes on a live backend |
| `daemon install [--interval] [--responder]` | `cmd_daemon` → `daemon.install` | Write + load a macOS launchd LaunchAgent that fires `fanops run` on a cadence | **LIVE** side effect (installs an OS-level recurring job that will itself execute LIVE `run` cycles unattended); the install call itself is local (writes plist/wrapper, calls `launchctl`) |
| `daemon status` | `cmd_daemon` → `daemon.status` | Report whether the agent is loaded and its last heartbeat age | READ-ONLY |
| `daemon stop [--remove]` | `cmd_daemon` → `daemon.stop` | Unload the launchd agent (and optionally delete plist/wrapper) | READ-ONLY / local-only OS action (no external network) |
| `daemon logs [-n]` | `cmd_daemon` → `daemon.tail_logs` | Tail `run.log` | READ-ONLY |
| `autopilot [--interval] [--no-daemon]` | `cmd_autopilot` → `autopilot.autopilot` | One command: enable LLM responder durably + install the daemon | **LIVE** side effect (persists `.env` FANOPS_RESPONDER=llm, installs the recurring daemon — each subsequent fire is a live unattended `run`) — publishing itself stays dryrun by default until the operator separately goes live |
| `gc [--keep-days]` | `cmd_gc` | Delete old retired/analyzed clip/render files + old scheduled payloads | READ-ONLY (local disk cleanup only; refuses `--keep-days < 1`) |

**Verbs the CLAUDE.md warning is specifically about**: `pull`, `respond` (llm mode), `reconcile`, `track`, `map-media`, `hashtags refresh|discover`, `advance`/`run` (via `publish_due`), and especially `cutover post` (an explicit-confirm real publish) and `daemon install`/`autopilot` (which arm an *unattended, recurring* live loop). All other verbs are safe to run anytime.

## Per-file breakdown (every function)

### `cli.py` — the argparse dispatch table + all inline/thin command handlers

- `_gates_blocked_note(s)` — pure: given the `advance()`/`run` summary dict's `awaiting` map, returns a loud diagnostic string naming every still-open gate kind, or `None` if converged. Iterates `GATE_KINDS` (from `pipeline.py`) generically so a future gate kind is covered automatically. Called by `_dispatch` (inside the `run` verb).
- `cmd_status(cfg)` — READ-ONLY: loads the ledger, prints one summary line of unit/post counts by state plus per-gate-kind `awaiting_<kind>=` counts (via `agentstep.pending`) and the effective per-channel publish mode. Called by `_dispatch`.
- `cmd_recover_audit(cfg)` — READ-ONLY: prints the delivery-bucket table from `studio.views_results.delivery_audit`. Called by `_dispatch`.
- `cmd_track(cfg, window)` — the metrics-poll verb: snapshots published/analyzed posts, fetches rows via `_default_list_posts` **outside** the ledger lock (network), then applies inside a tight `Ledger.transaction`; writes the digest after. Catches `RuntimeError`/`AuthError` (no key configured) and skips cleanly. **Side effects**: network call to Postiz/Zernio, ledger mutation, digest write. Called by `_dispatch`.
- `_learn_pass(cfg, *, window="30d")` — the extracted E1 learning pass (metrics fetch outside lock, classify/amplify/retire inside one transaction). Raises on error; caller (`run`) catches and logs. **Side effects**: network fetch, ledger mutation (amplify/retire), agent-request-file writes. Called by `_dispatch` (`run` verb, live-backend gated).
- `cmd_reconcile(cfg)` — polls backend status for stranded posts via `reconcile_due` (pre-polls outside lock, applies inside one transaction internally), writes digest. Catches `RuntimeError`/`AuthError` and skips cleanly. **Side effects**: network poll, ledger mutation. Called by `_dispatch`.
- `cmd_map_media(cfg)` — READ-ONLY-w.r.t.-Instagram: resolves Graph `media_id` for published/analyzed IG posts (`reconcile.resolve_media_ids`), mirrors live-only media (`reconcile.project_imported_media`), fills imported-row metrics (`track.pull_imported_insights`), saves ledger, prints counts. **Side effects**: Meta Graph GET calls, ledger save. Called by `_dispatch`.
- `cmd_adjust(cfg, winner_pct, retire_pct, lift_floor)` — one transaction: `classify_outcomes` → `amplify` → `retire`, then digest write. No network. Called by `_dispatch`.
- `cmd_amplify_variants(cfg)` — one transaction wrapping `apply_variant_amplify`; inert unless `FANOPS_VARIANT_AMPLIFY` set. No network. Called by `_dispatch`.
- `cmd_p4_bias(cfg)` — one transaction wrapping `apply_p4_dim_bias`; inert unless flag on + learning validated. No network. Called by `_dispatch`.
- `cmd_publish_queue(cfg)` — READ-ONLY: prints the manual-publish queue via `studio.views.publish_queue`, one line per queued post + instructions to `fanops resolve`. Called by `_dispatch`.
- `cmd_doctor(cfg, args=None)` — READ-ONLY: dispatches to `_cmd_doctor_fix_routing` if `--fix-routing`, else prints `doctor.doctor_report`'s PASS/FAIL checks + notes; returns 1 if any check failed. Called by `_dispatch`.
- `_cmd_doctor_fix_routing(cfg)` — READ-ONLY survey: walks `accounts.json`, lists every (handle, platform) with `integrations` XOR `backends` set (drift), prints a proposed `set-channel-routing` fix line per drift, never writes. Returns 0 always (a surveyor, not a failure gate). Called by `cmd_doctor`.
- `cmd_resolve(cfg, args)` — the manual reconcile escape hatch: forces a post's terminal state. Requires `--url` when resolving to a terminal-with-URL state (`_POST_TERMINAL_REQUIRES_URL`), else refuses with exit 2. Sets `public_url` before the state flip (model-validator ordering). Local-only ledger mutation, no network. Called by `_dispatch`.
- `cmd_audit(cfg, args)` — READ-ONLY: `fanops audit tail [-n]` prints the last N audit-log lines via `audit.read_audit_tail`. Called by `_dispatch`.
- `cmd_bulk_send_to_review(cfg, args)` — reverts N posts to `awaiting_approval` via `studio.actions.bulk_send_to_review`; local-only, audited. Called by `_dispatch`.
- `cmd_cutover(cfg, args)` — lazy-imports `fanops.cutover`, dispatches on `args.cutover_action` (`auth`/`post`/`metrics`/`lift`), prints JSON result. Never reachable from `run`/`advance`. **Side effects**: depend on the sub-action (see cutover breakdown below — all LIVE network except `lift`). Called by `_dispatch`.
- `cmd_compose(cfg, args)` — composites intro/outro/title cards onto a rendered clip via `compose.compose_clip` (MoviePy, optional `[compose]` extra); runs outside any ledger lock. Fails open to the base uncomposited clip on missing MoviePy/render error, exit code distinguishes real-compose (0) from fallback (1). **Side effects**: writes a new `_composed.mp4` file, logs. Called by `_dispatch`.
- `cmd_gc(cfg, keep_days)` — deletes retired/analyzed clip `.mp4` files, unreferenced `Render` files, and old `05_scheduled/*.json` payloads older than `keep_days`; refuses `keep_days < 1` (wipe-safety). **Side effects**: `os.remove`/`unlink` calls, stderr on OSError per file (never aborts the whole sweep). Called by `_dispatch`.
- `cmd_daemon(cfg, args)` — dispatches `install`/`status`/`stop`/`logs` to `daemon.py` functions, prints a report; catches `RuntimeError`/`ToolchainMissingError`/`ValueError` (non-darwin / launchctl absent / bad interval) into one clean stderr line + exit 2. Called by `_dispatch`.
- `cmd_autopilot(cfg, args)` — one-command autonomy: `autopilot.autopilot(cfg, interval=..., install_daemon=not args.no_daemon)`, prints a readiness report (responder, backend, daemon status, remaining manual checks). Catches `RuntimeError`/`ToolchainMissingError`/`ValueError`/`OSError`. Called by `_dispatch`.
- `_http_url(s)` — argparse `type=` validator for `pull <url>`: rejects any URL not starting `http://`/`https://` (blocks `file://` schemes and flag-injection into yt-dlp) via `argparse.ArgumentTypeError`. Called by `main` (registered as the `pull` positional's type).
- `main(argv=None)` — builds the full argparse subparser tree (every verb in the table above), parses args, constructs `Config()`, calls `_dispatch` inside a try/except ladder catching `ControlFileError`/`LockBusyError`/`AuthError`/`ToolchainMissingError`/`DownloadError`/`CutoverError`/`subprocess.TimeoutExpired` — each mapped to one clean stderr line + a specific exit code (1 for `LockBusyError`, 2 for the rest). Entry point (`if __name__ == "__main__"`).
- `_check_accounts(cfg)` — pre-run gate: validates `Accounts.load(cfg).validate()`; prints problems and returns 2 if any, else 0. Called by `_dispatch` (`advance` and `run` verbs).
- `_check_preflight(cfg)` — pre-run gate: blocks a run that would silently do credentialless nothing — checks `FANOPS_RESPONDER=llm` needs `claude` on PATH, and `FANOPS_POSTER=postiz` needs both `POSTIZ_URL`+`POSTIZ_API_KEY`. Prints problems and returns 2 if any. Called by `_dispatch` (`advance` and `run` verbs).
- `_heartbeat(cfg, s)` — emits one heartbeat line (`{heartbeat, fanops_version, published_in_run, last_published_age_hours}`) to stdout (JSON) and to `cfg.log_path` via `get_logger`. The live-clock timestamp is the load-bearing "still alive" signal for `daemon.status`'s staleness check. Called by `_dispatch` (`advance` and `run` verbs).
- `_dispatch(cfg, args)` — the single big if/elif ladder routing `args.cmd` to every handler above (and inlining a handful of trivial verbs — `ingest`, `pull`, `respond`, `digest`, `unhold`, `retry-source`, `retry-metrics`, `discover`, `intake`, `studio`, `run` — directly rather than via a named `cmd_*` function). The `run` verb's full unattended loop lives entirely here (see Daemon tick trace section — though this is the CLI-level loop, not the daemon's launchd firing). Called by `main`.

### `daemon.py` — macOS launchd packaging of `fanops run`

- `plist_path()` — pure: `~/Library/LaunchAgents/com.fanops.run.plist`. Called by `install`, `installed_interval`, `stop`.
- `wrapper_path(cfg)` — pure: `cfg.control / "fanops-run.sh"` (inside the workspace, beside the ledger). Called by `install`, `render_plist`, `stop`.
- `_fanops_bin()` — pure: the `fanops` binary next to the running interpreter (same venv). Called by `render_wrapper`, `studio.actions_run.kick_prepare`.
- `_daemon_path()` — pure: builds the full `PATH` string to bake into the wrapper+plist (venv bin, `claude`'s parent dir if found, homebrew, system dirs), de-duped and absolute — compensates for launchd's bare PATH. Called by `render_plist`, `render_wrapper`, `studio.actions_run.kick_prepare`.
- `resolve_responder(cfg)` — pure: returns `cfg.responder_mode` (single source of truth for what a hands-off fire will use). Called by `install`, `studio.views.daemon_health`.
- `render_wrapper(cfg, *, interval)` — pure string builder: the bash script launchd execs — `cd cfg.root && exec fanops run --base-time "$(date -u ...)"`, with every interpolated path `shlex.quote`d (space/quote/`$` safety) except the deliberately-unquoted `$(date ...)` substitution. Called by `install`.
- `render_plist(cfg, *, interval)` — pure: builds the plist dict (`Label`, `ProgramArguments`, `StartInterval`, `RunAtLoad=True`, `WorkingDirectory=cfg.root`, stdout/stderr paths, `ThrottleInterval=60`, `EnvironmentVariables={PATH, HOME}`), serializes via `plistlib.dumps`. Called by `install`.
- `parse_interval(raw)` — pure: parses `'10m'`/`'90s'`/`'2h'`/bare-seconds into an int; raises `ValueError` on malformed input or an interval `< 60s` (the launchd `ThrottleInterval` floor). Called by `cli.cmd_autopilot`, `cli.cmd_daemon`, `studio.golive.install_daemon`.
- `installed_interval(cfg)` — reads `StartInterval` back from the on-disk plist; returns `None` on missing file/corrupt plist/non-int value (broad `except Exception` by design — a corrupt plist must never crash `daemon status`). Called by `cli.cmd_daemon`, `studio.views.daemon_health`.
- `_launchctl(*args)` — shells `launchctl` with a 30s timeout; absent binary → `ToolchainMissingError`; timeout → a synthetic `returncode=124` result (never raises on hang). Called by `install`, `status`, `stop`.
- `_grep_int(text, key)` — pure regex extraction of an integer field (`PID`, `LastExitStatus`) from `launchctl list`'s plist-style dump text. Called by `status`.
- `_require_darwin()` — pure guard: raises `RuntimeError` if `sys.platform != "darwin"`. Called by `install`, `stop`.
- `install(cfg, *, interval, responder="inherit")` — **the daemon-install verb**: mkdirs `reports`/`control`, resolves the responder choice (`'inherit'` touches nothing; `'llm'`/`'manual'` persist to `.env` via `autopilot.set_env_var`, lazy-imported to avoid a cycle), writes+chmods the wrapper script (0o755), writes the plist, then `launchctl bootout` (idempotent reinstall, ignore rc) → `bootstrap` → falls back to `load -w` on older macOS. Returns `{plist, wrapper, interval, loaded, responder, discloses_llm}`. **Side effects**: mkdir, file writes+chmod, `launchctl` subprocess calls, `.env` write (llm/manual only). Called by `cli.cmd_daemon`, `autopilot.autopilot`, `studio.golive.install_daemon`.
- `status(cfg, *, interval=600)` — READ-ONLY: `launchctl list` for loaded/pid/last_exit, plus heartbeat age via `_heartbeat_age_s`; computes a verdict string (`not installed` / `loaded but no heartbeat yet` / `alive` / `loaded but stale`) — alive iff heartbeat age `< 3*interval`. Called by `cli.cmd_daemon`, `studio.views.daemon_health`.
- `stop(cfg, *, remove=False)` — `launchctl bootout` (fallback `unload -w` on failure), then CONFIRMS via a fresh `launchctl list` (source of truth for `stopped`, not a hardcoded True). Optionally deletes the plist/wrapper files (`remove=True`). **Side effects**: `launchctl` calls, optional file deletion. Called by `cli.cmd_daemon`, `studio.golive.uninstall_daemon`.
- `tail_logs(cfg, n=40)` — READ-ONLY: reads the last `n` lines of `cfg.log_path` via a bounded `collections.deque` (never loads the whole file). Returns `"no logs yet"` if absent. Called by `cli.cmd_daemon`.
- `_heartbeat_age_s(cfg)` — READ-ONLY: scans `run.log` for the most recent `\theartbeat\t` line, parses its leading ISO timestamp, returns age in seconds (or `None` on no log/no heartbeat/unparseable). Called by `status`.

### `doctor.py` — read-only first-run health screen

- `_check(label, ok, hint="")` — pure: builds one `{label, ok, hint}` result dict (hint blanked when ok). Called by `doctor_report`.
- `doctor_report(cfg)` — the single composed check: media toolchain presence (ffmpeg/ffprobe/whisper/yt-dlp), `claude` on PATH (only if `FANOPS_RESPONDER=llm`), brand-brief (`context.md`) non-empty, `accounts.json` validity, Postiz key+URL consistency + learning-readiness (booleans only, key never echoed), live-route coherence (`FANOPS_LIVE=1` but nothing actually routes live — the "half-live" trap), IG-insights-readable (Meta Graph scope check via `meta_graph.insights_blocked_signal`), plus informational notes (poster backend + dryrun/live, learning-validated state, review-queue depth). Reads `learning_validated(cfg)` **once** and reuses it in both the Postiz-readiness check and the notes block. Performs no writes/mutations — pure diagnosis. Called by `autopilot.autopilot`, `cli.cmd_doctor`, `studio.views.golive_status`.

### `cutover.py` — the manual live-cutover validation harness (writes ONLY `cutover.json`)

- `_load_state(cfg)` — reads `cfg.cutover_path`; returns `{}` if absent or on any parse error (fail-open, never crashes). Called by `_save_state`, `cutover_lift`.
- `_save_state(cfg, patch)` — **the sole write path**: `write_json_atomic(cfg.cutover_path, {**_load_state(cfg), **patch})` — a merge-then-atomic-write onto `00_control/cutover.json`. No `Ledger` import, no `led.*` call anywhere in this file (confirmed by grep — zero hits). Called by `cutover_postiz.postiz_metrics`, `cutover_postiz.postiz_post`, `track._auto_validate_metrics_shape` (a separate, unrelated auto-validation write path in `track.py`, outside this cluster, that reuses the same cutover-state file for its own stamp).
- `reconcile_fields(metrics)` — pure: diffs a live metrics row's numeric keys against `track._W` (the weighted-field set `lift_score` uses), returning `{scored, present_unweighted, weighted_absent}`. Called by `cutover_postiz.postiz_metrics`.
- `cutover_auth(cfg, *, get=None)` — Step 1: dispatches to `cutover_postiz.postiz_auth` if `cfg.poster_backend == "postiz"`, else raises `CutoverError` (fails closed for any non-postiz backend — no other backend is supported). Called by `cli.cmd_cutover`, `studio.golive.validate_learning`.
- `cutover_post(cfg, account_id, *, confirmed, post=None)` — Step 2: dispatches to `cutover_postiz.postiz_post`. Called by `cli.cmd_cutover`, `studio.golive.validate_learning`.
- `cutover_metrics(cfg, submission_id, *, list_posts=None)` — Step 3: dispatches to `cutover_postiz.postiz_metrics`. Called by `cli.cmd_cutover`, `studio.golive.validate_learning`.
- `cutover_lift(cfg, submission_id)` — Step 4: pure computation — reads the captured `metrics_row` from `_load_state`, raises `CutoverError` if absent, computes `lift_score(metrics, weights)` using `cfg.tuning()["lift_weights"]`. No dispatch, no network — the only step that touches no backend. Called by `cli.cmd_cutover`, `studio.golive.validate_learning`.

### `cutover_postiz.py` — the Postiz half of the harness (writes ONLY via `cutover._save_state`, same `cutover.json`)

- `_require_postiz(cfg)` — pure guard: raises `CutoverError` (not `PostizAuthError`) if `cfg.postiz_api_key` is unset — a config problem, not a live 401. Called by `postiz_auth`, `postiz_post`.
- `postiz_auth(cfg)` — Step 1: calls `postiz.postiz_check_auth(cfg)` (a real read-only integrations probe against the live Postiz API), returns `{ok, backend, status_code}`. **Network call.** Called by `cutover.cutover_auth`.
- `postiz_post(cfg, integration_id, *, confirmed, post=None)` — Step 2: refuses unless `cfg.is_live`, refuses unless `confirmed=True`, refuses an unknown `integration_id`, builds a payload via `build_postiz_payload` (platform derived from the chosen integration, `content="fanops cutover probe — delete me"`, `scheduled_time=CUTOVER_SCHEDULE` = `"2099-01-01T00:00:00Z"`, hardcoded not operator-supplied so it can never go live), POSTs it to `{postiz_base}/posts` (real network write, default `requests.post`, 30s timeout). Raises `PostizAuthError` on 401, `CutoverError` on other non-2xx or unparseable response. On success, calls `cutover._save_state(cfg, {submission_id, integration_id, platform, scheduled_time, backend, post_response_keys})` — the confirmed write-target is `cutover.json`, never `led`. **This is a real live publish to a real Postiz integration** — the single most dangerous verb in this cluster, gated by `cfg.is_live` + explicit `--i-understand-this-posts-to-a-real-account` confirm flag + integration-id membership check. Called by `cutover.cutover_post`.
- `postiz_metrics(cfg, submission_id, *, list_posts=None)` — Step 3: fetches the cutover post's real metrics row via `PostizMetricsClient(cfg, submission_ids=[submission_id]).list_posts` (or an injected `list_posts` for tests), raises `CutoverError` if the row or its `metrics` dict is missing/empty (treated as "Postiz analytics lag, retry later" rather than a hard fail — and deliberately does NOT set `metrics_confirmed=True` on an empty row, avoiding a false-positive validation stamp). On success, calls `cutover._save_state(cfg, {metrics_row, reconciliation, postiz_labels, label_map, metrics_confirmed: True, backend})`. **Network call.** Called by `cutover.cutover_metrics`.

### `health.py` — live dependency health + best-effort bring-up

> **MOL-298:** `DepHealth` and the Postiz probe helpers now live in `health_model.py`; this module keeps
> docker bring-up (`_start_docker`, `ensure_up`) and re-exports `system_health` wiring that delegates
> dependency rows to `health_model.dep_health_list`.

- `DepHealth` (`NamedTuple`) — **moved to `health_model.py:9`**; kept as a lazy re-export here for backward compat.
- `_docker_health()` — shells `docker info` (8s timeout); `False` if `docker` isn't on PATH or the call raises/times out (broad `except Exception`, deliberate — never let a health probe itself crash). Called by `_start_docker`, `ensure_up`, `system_health`.
- `_http_reachable(url, name)` — pure-ish: a bare `requests.get(url, timeout=3)` — ANY HTTP response (even 404) counts as "reachable" (it's a liveness ping, not a real API call). `False`/`"not configured"` if `url` is empty. Called by `postiz_health`, `zernio_health`.
- `postiz_health(cfg)` — `_http_reachable(cfg.postiz_url, "postiz")`. Called by `ensure_up`, `system_health`.
- `zernio_health(cfg)` — `_http_reachable(cfg.zernio_url, "zernio")`. Called by `system_health`.
- `system_health(cfg)` — the live red/green list for Docker + Postiz + Zernio, in launch order. Called by `cli._dispatch` (`studio` verb), `studio.app_routes_golive.register_golive_routes`.
- `_postiz_compose_dir()` — pure: resolves the Postiz docker-compose stack directory (`FANOPS_POSTIZ_COMPOSE_DIR` env override, else `~/postiz-selfhost/postiz-docker-compose`); `None` if neither exists. Called by `ensure_up`.
- `_start_docker(log)` — shells `open -a Docker` (macOS-specific launch), then polls `_docker_health()` up to 30×3s; appends progress lines to the caller-supplied `log` list; never raises (falls through to "did not come up in time" or "no `open` to launch it"). **Side effect**: launches Docker Desktop as a subprocess. Called by `ensure_up`.
- `_start_postiz(compose_dir, log)` — shells `docker compose --project-directory <dir> up -d` (180s timeout); catches any exception into a log line, never raises. **Side effect**: brings up the Postiz docker stack. Called by `ensure_up`.
- `ensure_up(cfg)` — the launch bring-up orchestrator: starts Docker if down, starts Postiz compose if a compose dir is configured and Postiz is unreachable. Logs everything via the module logger too. Never raises. **Side effects**: subprocess launches (Docker, docker compose). Called by `cli._dispatch` (`studio` verb), `post.run.publish_due`, `post.run.publish_post`, `reconcile.reconcile_due`.

### `health_model.py` — typed health owner (MOL-298)

- `DepHealth` (`NamedTuple`) — `(name, ok, detail)`, one dependency's red/green verdict (`health_model.py:9-13`).
- `HealthReport` (`dataclass`) — composes `checks`, `notes`, `deps`, optional `field_shape`; `as_dict()` for doctor consumers (`health_model.py:16-31`).
- `build_health_report(cfg, ...)` — **THE health owner** — composes doctor checks, dependency rows, learning field-shape, bounded live confirm (`health_model.py:167-178`). Called by `doctor.doctor_report` (view layer).
- `dep_health_list(cfg, ...)` — docker + Postiz + Zernio rows; Postiz uses the unified `postiz_health_probe` (`health_model.py:87-91`). Called by `health.system_health`.
- `postiz_doctor_check(cfg, ...)` — doctor-shaped Postiz row from the same probe (`health_model.py:94-108`).
- `heartbeat_stale(cfg, ...)` — shared daemon heartbeat staleness threshold (`health_model.py:117-127`).
- `build_field_shape(cfg, ...)` — learning field-shape verdict; fail-open (`health_model.py:130-140`).

### `digest.py` — human-readable ledger digest renderer (read-only observability)

- `_counts(units)` — pure: `Counter` of `.state.value` across a unit iterable, formatted as indented lines. Called by `render_digest`.
- `aggregate_by_dim(led, dim)` — pure: groups `analyzed` posts by a stamped creative dim (`first_frame_kind`/`clip_profile`/`top_bias`), reporting per value `{n, reach_sum, reach_mean, saves_mean, shares_mean, retention_mean}` — REACH-FIRST (not the engagement-skewed `lift_score`). Called by `_reach_by_dim` (also independently by `p4_dim_bias.dim_bias_candidates`, `timing_bias.timing_bias_winner`, `validation_gate.enough_attributed_signal`, outside this cluster).
- `gate_state(led, cfg, account, platform, _cache=None, accounts=None)` — the per-surface learning-loop state label ("UCB -> …" / "learning ACTIVE" / "borrowing platform signal" / "gathering data"), reusing the SAME gated scorer (`variant_learning.best_hooks`/`ucb_rank`) that biases caption generation — so the label can never disagree with actual behavior. Fail-open to `"gathering data"` on any exception (logged via `logger.warning`). Memoized via an optional cache dict. Called by `_variant_lift`, `studio.views_results._loop_state`.
- `_holds(led)` — pure: one Markdown section listing every held clip + its `held_reason`. Called by `render_digest`.
- `_failures(led)` — pure: one section listing `failed`/`error` posts and any unit (source/moment/clip/stitch) in `error` state. Called by `render_digest`.
- `_needs_reconcile(led)` — pure: one section for posts stuck `needs_reconcile` (ambiguous publish outcome — may be live, needs manual verification before resubmit). Called by `render_digest`.
- `_unmeasured(led)` — pure: one section for `published` posts with empty `metrics` (shipped but never measured). Called by `render_digest`.
- `_variant_lift(led, cfg, accounts=None)` — pure-ish (calls `gate_state`): ranks analyzed variant posts by lift_score, annotates each with its degraded-lift marker (if `lift_degraded`) and learning-loop state. Called by `render_digest`.
- `_variant_amplify(led, cfg)` — fail-open (own `try/except`, logs on failure): per-surface sustained-win-streak status toward the v3 amplify gate ("amplified"/"building streak (n/MIN)"/"gathering data"); section entirely absent when `cfg.variant_amplify` is off. Called by `render_digest`.
- `_reach_by_dim(led, cfg)` — fail-open: for each stamped creative dim once `validation_gate.p4_unlocked` clears it, renders `aggregate_by_dim`'s reach-first rollup sorted descending by `reach_mean`. READ-ONLY observability — never biases generation itself. Called by `render_digest`.
- `_culmination(led, cfg)` — fail-open: for each structural dim (framing/length/first-frame via `p4_dim_bias.dim_bias_candidates`, timing via `timing_bias.timing_bias_winner`), renders the trusted winner AND whether its kill switch is actually ON (`"ACTIVE (biasing)"` vs `"winner found (bias OFF)"` vs `"gathering data"`) — reuses the SAME actuator functions so the digest can never disagree with real behavior. Section omitted entirely if nothing qualifies. Called by `render_digest`.
- `_pending_gates(cfg)` — pure: computes the pending-gate lists ONCE (moments/moment_hooks/captions via `agentstep.pending`) and renders it into TWO differently-titled sections that share the same body (an "Awaiting agent" section and a searchable "Pending agent gates" section). Called by `render_digest`.
- `render_digest(led, cfg, accounts=None)` — pure composition: concatenates the header + all the section functions above in a fixed order. Called by `write_digest`.
- `write_digest(led, cfg)` — the disk-write entry point: mkdirs the digest's parent dir, self-loads `Accounts` (fail-open to `None`, only needed when `cfg.variant_transfer` is on) so the "borrowing platform signal" label works without threading accounts through every call site, then writes `cfg.digest_path`. **Fail-open on `OSError`** (disk full/perms) — logs a warning, never raises (a digest write must not abort the caller's already-committed transaction). Called by `cli._dispatch` (`ingest`/`digest` verbs), `cli.cmd_adjust`, `cli.cmd_amplify_variants`, `cli.cmd_p4_bias`, `cli.cmd_reconcile`, `cli.cmd_track`, `pipeline._build_summary`, `studio.actions.pull_metrics_studio`, `studio.actions_run.run_ingest`/`run_ingest_thirdparty`/`run_pull`.

### `audit.py` — append-only operator audit trail

- `write_audit(cfg, action, post_ids, *, reason, **kw)` — appends ONE JSON line (`{ts, action, post_ids, reason, **kw}`) to `00_control/studio_audit.log`, chmods it 0o600. **Wrapped in a top-level bare `except Exception: pass`** by explicit contract — "the action must complete even if the audit write fails (audit is observability, never a blocker)." **Side effects**: mkdir, file append, chmod (best-effort — a chmod failure is itself individually swallowed). Called by `cli.cmd_bulk_send_to_review` (via `studio.actions.bulk_send_to_review`) and extensively by `studio/actions*.py` (`mark_published`, `publish_due_bucket`, `publish_now`, `pull_metrics_studio`, `recover_posts`, `reschedule_bucket`, `resolve_post`, `retry_oversize_failures`, `retry_rate_limited_failures`, `_approve_ids_with_render`) — all outside this cluster but confirming this is the durable action-log for every state-changing Studio action.
- `read_audit_tail(cfg, n=20)` — READ-ONLY: returns the last N lines of the audit log as raw JSON strings; `[]` if the file is missing; wrapped in a broad `except Exception: return []` (never raises on a corrupt/unreadable log). Called by `cli.cmd_audit`.

### `timeutil.py` — the single ISO-8601 / operator-timezone conversion layer

- `_operator_zone(cfg)` — the fail-closed-to-UTC core: resolves `cfg.operator_tz` (an IANA name, default `"UTC"`) to a `tzinfo` suitable for `.astimezone()`. Returns `None` if `cfg is None` (back-compat: caller falls through to system-tz behavior). For `"UTC"` or if `zoneinfo` isn't importable (`ZoneInfo is None`, pre-3.9), returns `timezone.utc` directly. Otherwise tries `ZoneInfo(name)`; **on any exception (unknown/malformed IANA name) returns `timezone.utc`** — this is the exact fail-closed line: a bad `FANOPS_OPERATOR_TZ` value never silently mis-localizes into an unintended zone, it visibly renders as UTC instead. Called by `local_input_to_utc_z`, `publish_buckets`, `to_local_display`, `to_local_input`, and by `crosspost._mint_surface_post`/`studio.views_common._roll_into_window` directly (outside this cluster).
- `parse_iso(ts)` — pure, STRICT: `datetime.fromisoformat(ts.replace("Z", "+00:00"))`; raises on malformed/`None` input (matches call sites that always feed a known-present timestamp). Called extensively across `ledger.py`, `pipeline.py`, `reconcile.py`, `metrics_schedule.py`, `post/metrics.py`, `studio/*` (30+ call sites per the call graph) plus internally by `is_past_due`, `is_due_or_past`, `publish_buckets`.
- `iso_z(dt)` — pure: serializes an aware datetime to UTC ISO-8601 with a trailing `Z` (normalizes to UTC first via `.astimezone(timezone.utc)`, then swaps `+00:00`→`Z`). Called extensively (batches, casting, crosspost, ingest, ledger migrations, post/postiz, post/run, reconcile, studio/actions*, track — 25+ call sites per the call graph).
- `publish_buckets(ts, cfg)` — Leg 3 timing-bias support: buckets an ISO publish timestamp into `(hour, weekday)` **in the operator's local timezone** (via `_operator_zone`), so the stamp site and the timing-bias actuator can never disagree on which hour a post shipped. Fail-safe: empty/unparseable `ts` → `(None, None)`, never raises. Called by `reconcile.reconcile_posts`.
- `is_past_due(scheduled_time, now)` — pure, strict `<` (equal is NOT past-due); `None`/unparseable/tz-naive → `False` (never raises, never auto-triggers). No repo callers found in the call graph (dead-code candidate — see Anomalies).
- `is_due_or_past(scheduled_time, now)` — pure, `<=` (mirrors `publish_due`'s gate semantics — "would fire on the next tick"); **unparseable → `True`** (treat-as-stale, the safe direction for the `go_live` readiness check — a torn time blocks the flip rather than silently passing). Called by `studio.golive.go_live`.
- `_aware_utc(ts)` — pure: parses a stored timestamp into an aware UTC datetime, or `None` on absent/garbage; a naive stored time is treated as canonical-UTC-by-storage-convention (mirrors `studio.actions._normalize_z`). Called by `to_local_display`, `to_local_input`.
- `to_local_display(ts, *, cfg=None)` — a stored UTC ISO string → `'YYYY-MM-DD HH:MM TZ'` friendly local string in the operator's configured timezone; `cfg=None` falls back to system tz (test-only path — real callers thread `cfg`, e.g. the Jinja `localdt` filter). Empty/unparseable → `''`. No repo callers found in the call graph directly (likely invoked via a Jinja filter registration in `studio/` that the AST call-graph doesn't trace as a direct call).
- `to_local_input(ts, *, cfg=None)` — a stored UTC ISO string → naive-local `'YYYY-MM-DDTHH:MM'` for an `<input type=datetime-local>` value (minute precision, no tz suffix per the HTML spec). No repo callers found directly in the call graph (same Jinja-filter caveat as `to_local_display`).
- `local_input_to_utc_z(s, *, cfg=None)` — the inverse web-boundary conversion: a naive datetime-local form value (interpreted as LOCAL per the HTML spec, in `cfg.operator_tz` when set) → canonical UTC `iso_z`. A value already carrying a tz is normalized to UTC, never reinterpreted as local. Unparseable → the raw string unchanged (so the caller's own "bad time" error path fires, kept at one home). Documents a known DST fall-back-hour ambiguity (resolves to the standard-time side deterministically — accepted as a 1h tolerable skew). Called by `studio.app._time_arg`.

### `_fwrun.py` — bounded faster-whisper subprocess runner (spawned by `transcribe.py`, outside this cluster's CLI/daemon tree)

- `_certifi_env()` — best-effort: points `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` at `certifi.where()` if not already set (macOS framework Python TLS-cert workaround for the first-run HuggingFace model download); `except ImportError: pass` — narrower than `vocals._demucs_env`'s `except Exception` sibling (only masks the expected "certifi not installed" case, not other faults). Called by `main`.
- `_load_model(model)` — loads a `faster_whisper.WhisperModel` on CPU with `int8` quantization; isolated behind this function so `transcribe_to_json`'s JSON-shaping logic is unit-testable without the heavy optional `[asr]` dependency (tests patch this). Called by `transcribe_to_json`.
- `_word(w)` — pure: serializes one faster-whisper word timing to `{word, start, end}`, None-guarding `start`/`end` (the runtime can emit null word timings). Called by `transcribe_to_json`.
- `transcribe_to_json(audio, out_dir, model, language)` — the core transcription call: loads the model, resolves a comma-list `language` into either per-segment auto-detection (`multilingual=True`, >1 candidate) or a single pinned language, runs `wm.transcribe(...)`, builds whisper-compatible JSON (`{language, segments:[{start,end,text[,words]}]}`), writes it atomically (`.tmp` + `os.replace`) to `<out_dir>/<audio-stem>.json`. Called by `main`.
- `main(argv=None)` — the subprocess entry point: calls `_certifi_env()`, parses `--model`/`--language`/`--output_dir`/`audio` via argparse, calls `transcribe_to_json`. **FAIL-LOUD by design**: any exception here propagates and exits nonzero, so the parent `transcribe.py` (which spawns this as `python -m fanops._fwrun ...` and holds the ledger lock) parks the source as a retriable error rather than silently producing an empty transcript. Entry point (`if __name__ == "__main__": sys.exit(main())`).

### `__init__.py`

- `__version__ = "0.3.0"` — the sole module-level constant; consumed by `cli._heartbeat` (`fanops.__version__` in the heartbeat payload) and presumably packaging metadata.

## Daemon tick trace + cutover.json-only verification

### One `fanops run` cycle (the unit both the CLI's `run` verb and each daemon firing execute)

1. **Preflight gates** (`cli._dispatch`, `run` branch, `cli.py:858-861`): `_check_accounts(cfg)` then `_check_preflight(cfg)` — both READ-ONLY; either can abort the whole cycle with exit 2 before any mutation happens (bad `accounts.json`, or an `llm`/`postiz` config missing its binary/key).
2. **Respond+advance convergence loop** (`cli.py:868-879`, up to **10 iterations**):
   - `get_responder(cfg).answer_pending(cfg)` — drains pending agent gates. If `FANOPS_RESPONDER=llm`, this shells `claude -p` per pending gate (a real LLM call, real cost). Otherwise the manual/no-op responder does nothing.
   - `s = advance(cfg, base_time=args.base_time)` — one full pipeline sweep (transcribe → moments → clip → caption → crosspost → **`publish_due`**, which is where an actual live publish to Postiz/Zernio happens for any `queued` post whose scheduled time has passed).
   - Loop breaks early once `not any(s["awaiting"].values())` — every gate kind clear.
   - **Error handling**: the entire iteration body is wrapped in `try/except Exception as e: print("run halted: ..."); return 1` — **a single failing iteration DOES abort the whole `run` invocation** (not fail-open/continue). This is deliberate: the responder's LLM call or `publish_due`'s auth re-raise are treated as fatal-enough to stop rather than silently retry-loop. The `run` verb itself is one-shot per invocation; it is the **daemon's launchd `StartInterval`** that provides the retry cadence at the next scheduled fire, not an in-process retry.
3. **Stuck-gate reporting** (`cli.py:883-885`): if the loop exhausted 10 iterations still with an open gate, `_gates_blocked_note` prints a loud stderr line + logs `gates_blocked` — but this does NOT change the exit code (stays 0); it's a monitoring signal, not a failure.
4. **Learning passes** (`cli.py:891-950`), each independently gated and each in its **own try/except that swallows and logs, never propagates**:
   - `_learn_pass(cfg)` — only if `cfg.is_live_backend`; `AuthError` prints a distinct "learn skipped" line + logs `auth_error`; any other `Exception` logs `error` and is swallowed.
   - `apply_variant_amplify` — only if `cfg.variant_amplify` AND live backend; own transaction; any exception logged and swallowed.
   - `apply_p4_dim_bias` — only if `cfg.p4_dim_bias` AND live backend; own transaction; swallowed.
   - `apply_timing_bias` — only if `cfg.timing_bias` AND live backend; own transaction; swallowed.
   - `refresh_store_if_due` (hashtags) — NOT gated on live backend (only on Meta creds internally), throttled to once per 12h via file mtime; own try/except; swallowed. A corrupt-personas abort is logged loudly (`store_refresh_aborted`), distinct from a genuine refresh.
5. **Heartbeat** (`cli.py:953`): `_heartbeat(cfg, s)` — ALWAYS runs (outside all the gated try/excepts above, using the final `advance()` summary `s`), printing a JSON line to stdout and appending to `run.log`. This is the sole liveness signal `daemon.status`'s staleness check reads.
6. **Exit**: 0 in the normal/stuck-gate/learning-pass-swallowed-error cases; only step 2's per-iteration hard failure (a raised exception from responder or `advance` itself) returns exit 1.

**Conclusion on fail-open vs fail-closed**: the pipeline's per-unit stages (steps inside `advance()`) are individually quarantined (a bad clip/source doesn't halt others — traced in other clusters), and the four post-loop learning passes are each independently fail-open (swallow+log, never crash the run). But the **respond+advance loop itself is NOT fail-open at the top level** — an uncaught exception from the responder or from `advance()` (e.g. a fatal auth error escaping `publish_due`) aborts the entire `run` invocation with exit 1. The **daemon** (launchd) is what makes the overall system resilient to this: each `StartInterval` fire is an independent, fresh `fanops run` process: one bad tick returns exit 1, but launchd fires the next tick at the same cadence regardless of the previous exit code (`RunAtLoad`/`StartInterval` don't gate on prior success) — so daemon-level continuity is provided by launchd's scheduling, not by in-process retry logic.

### Daemon install → tick cadence

- `fanops daemon install --interval 10m` → `daemon.install()`: writes `fanops-run.sh` (the one-shot wrapper: `cd cfg.root && exec fanops run --base-time "$(date -u ...)"`) and the `com.fanops.run.plist` (`StartInterval=600`, `RunAtLoad=True`, `ThrottleInterval=60` floor), then `launchctl bootstrap`/`load -w`.
- launchd itself is the scheduler: it fires the wrapper once at load (`RunAtLoad`) and then every `StartInterval` seconds thereafter, restarting after a crash per launchd's own semantics (this module does not implement its own scheduling loop in Python).
- Minimum interval enforced by `parse_interval`: **60 seconds** (`_MIN_INTERVAL`), matching launchd's own `ThrottleInterval` floor — sub-minute cadences are rejected with a clean `ValueError`, not silently clamped.

### Cutover.json-only verification (explicit proof)

Grep across both files for `Ledger` and `led.` returns **zero matches**:

```
$ grep -n "Ledger\|led\." src/fanops/cutover.py src/fanops/cutover_postiz.py
(no output)
```

- `cutover.py:14` imports only `from fanops.controlio import write_json_atomic` — no `fanops.ledger` import anywhere in the file.
- `cutover.py:38` — `_save_state`'s exact write call: `write_json_atomic(cfg.cutover_path, {**_load_state(cfg), **patch})`. `cfg.cutover_path` resolves to `00_control/cutover.json` (per the module docstring at `cutover.py:5-6`: "Writes ONLY to 00_control/cutover.json, NEVER ledger.json").
- `cutover_postiz.py:1-13` imports `requests`, `fanops.config.Config`, `fanops.errors`, `fanops.post.postiz` helpers — again no `fanops.ledger` import.
- `cutover_postiz.py:64-66` (`postiz_post`) and `cutover_postiz.py:90-91` (`postiz_metrics`) both call `_save_state(cfg, {...})` (lazy-imported `from fanops.cutover import ... _save_state`) as their only persistence action — confirming the write path terminates at the same single `write_json_atomic(cfg.cutover_path, ...)` call in `cutover.py`.
- The throwaway 2099-scheduled probe post created by `postiz_post` is a real Postiz-side artifact (deleted by the operator in the Postiz dashboard), but it is never `led.add_source`/`led.add_post`'d or otherwise represented in `ledger.json` — the module docstrings explicitly state this is the design intent ("the throwaway 2099-scheduled test post must never enter the real unit chain").

**Verdict: confirmed exactly as the project docs claim.** `cutover.py`/`cutover_postiz.py` mutate only `00_control/cutover.json`; no ledger read-for-write, no `Ledger.load()`/`Ledger.transaction()` call, no `led.*` mutation exists in either file.

## Anomalies found

**Dead-code candidates (zero call sites anywhere in `src/`, confirmed via call_graph.json):**
- `src/fanops/timeutil.py:70` `is_past_due` — `called_by_in_repo: []`. Its sibling `is_due_or_past` (the `<=` variant) IS used by `studio.golive.go_live`; the strict-`<` variant appears unused. Possibly test-only or reserved for a future strict-past check.
- `src/fanops/timeutil.py:110` `to_local_display` — `called_by_in_repo: []` per the call graph, but this is very likely a call-graph blind spot: it's the natural implementation behind a Jinja template filter (e.g. `localdt`) registered via `app.jinja_env.filters[...] = to_local_display` in `studio/` rather than called as a plain Python function — the AST-based graph would miss that registration pattern (the same caveat C4's report flagged for Jinja-imported macros). Flagging for verification against `studio/app.py`'s filter registrations, not asserting genuine dead code.
- `src/fanops/timeutil.py:124` `to_local_input` — same Jinja-filter blind-spot caveat as `to_local_display`.
- `src/fanops/cli.py:522` `_http_url` — technically has zero *function-level* callers in the graph (it's registered as an argparse `type=` callback at `cli.py:539`, `p_pull.add_argument("url", type=_http_url)`, which the AST call-graph doesn't trace as a "call"). Not dead code — confirmed wired via argparse's type mechanism, same blind-spot class as the Jinja filters above.

**Fail-open / broad-except handlers (all appear intentional per surrounding comments — cited for completeness, not flagged as bugs):**
- `src/fanops/audit.py:29-47` `write_audit` — the entire function body is wrapped in a top-level bare `except Exception: pass` (line 46-47). This is the broadest except-swallow in the cluster, but it is explicitly, deliberately contracted in the module docstring: "write_audit NEVER raises. The action must complete even if the audit write fails (audit is observability, never a blocker)." Not a defect.
- `src/fanops/audit.py:59-60` `read_audit_tail` — `except Exception: return []`, also documented as "never raises."
- `src/fanops/cutover.py:30-31` `_load_state` — `except Exception: return {}`, documented inline ("corrupt scratch file -> start clean, never crash").
- `src/fanops/daemon.py:134-135` `installed_interval` — `except Exception: return None`, documented inline as deliberate ("a corrupt plist... must NEVER crash `daemon status`").
- `src/fanops/doctor.py:46-47` `doctor_report` (accounts check) — `except Exception as e: problems = [str(e)[:160]]` — malformed `accounts.json` becomes a check FAILURE (visible), not a crash. Correctly surfaced, not swallowed silently.
- `src/fanops/doctor.py:74-75` `doctor_report` (half-live check) — `except Exception: half_live = False` — guards against a bad `accounts.json` crashing the whole report; degrades to the "not half-live" (safer, less alarming) reading rather than blocking the report. Worth noting this is the ONE place in `doctor.py` where a real config error could silently under-report a genuinely half-live/broken state, since the fallback value is "everything's fine" rather than "flag it." Low risk since the accounts-validity check immediately above would likely have already caught the same corruption.
- `src/fanops/health.py:45-46`, `health.py:100-101` — `_docker_health`/`_start_postiz`: both broad `except Exception`, both documented ("FileNotFound / Timeout / OSError -> down, never raise"; bring-up failures logged, never block launch). Intentional.
- `src/fanops/digest.py:85-87, 185-187, 206-208, 250-252, 298-300` — five fail-open `except Exception` blocks (`gate_state`, `_variant_amplify`, `_reach_by_dim`, `_culmination`, `write_digest`'s accounts self-load), every one logging via `logger.warning(..., exc_info=True)` before degrading — consistent, non-silent fail-open pattern across the whole file.
- `src/fanops/cli.py:873, 900, 911, 923, 934` (the four post-loop learning-pass try/excepts in the `run` verb) and `cli.py:949` (hashtag refresh) — all logged via `get_logger(cfg)(...)` before being swallowed; consistent with the documented "own try/except, hiccup swallowed, exit stays 0" design for each independently-gated learning feature.
- `src/fanops/timeutil.py:38-39` `_operator_zone` — `except Exception: return timezone.utc` — this IS the fail-closed-to-UTC behavior the project CLAUDE.md explicitly calls out ("fails CLOSED to UTC"), confirmed by direct read: an unknown/malformed IANA timezone name never silently mis-localizes, it renders UTC instead.

**No TODO/FIXME/XXX markers** found in any of the 11 files (grep confirmed zero hits).

**No bare `except:`** (unqualified) anywhere in the 11 files — every handler is typed `except Exception` or narrower (e.g. `_fwrun.py`'s `except ImportError`, `daemon.py`'s `except (FileNotFoundError, OSError)` / `except subprocess.TimeoutExpired`).

**No HIGH-severity live/destructive verb found without a confirmation gate.** The one CLI verb that actually performs a real, operator-visible live action with real-world consequence — `fanops cutover post` (POSTs a live throwaway ad to a real Postiz integration) — is triple-gated: `cfg.is_live` must already be true, the operator must pass the explicit `--i-understand-this-posts-to-a-real-account` flag, AND the target `integration_id` must already be one of the operator's own mapped integrations (`cutover_postiz.py:38-45`). `fanops daemon install`/`fanops autopilot` (which arm a *recurring* live loop) are not destructive by themselves — publishing stays gated by `cfg.is_live`/scheduled-time due-checks inside `advance()`, and the daemon's own docstring states "Backend stays dryrun by default — this never publishes" until the operator separately goes live via Postiz/manual-queue. `fanops gc` (the one file-deleting verb) explicitly refuses `--keep-days < 1` to block a one-keystroke wipe of reusable renders. No gap found matching the "live verb with no confirmation gate" pattern this cluster's brief asked to hunt for.
