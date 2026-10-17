# Pack: C8 — ops, CLI & daemon

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C8-F01 | low | mis-report | closed on this tree | `half_live_state` surfaces a compute failure in the hint. Doctor emits that check | `health_projectors.half_live_state` |

## Evidence commands run

```
rg 'half_live' src/fanops/doctor.py
tests/test_doctor.py tests/test_cli.py tests/test_daemon_keeper.py
```

## Full checklist 2026-10-17

No new finding ID. The daemon parity gap is closed on `daemon.ensure`. `doctor._deploy_code_check` was already closed. Aliases in `src/fanops/CLAUDE.md` were grepped; nothing was deleted.

### Doctor checks

`doctor._assemble_doctor_checks` (via `cli.cmd_doctor` → `health_model.build_health_report`). Exit 1 when `report_is_healthy` is false. `--json` uses the same exit. `--fix-routing` is read-only and exits 0.

Always or when the predicate says so:

- `_env_settings_check` — `.env / FANOPS_* vars valid (strict Settings)`
- ffmpeg, ffprobe, faster-whisper (`[asr]`), yt-dlp on PATH
- `FANOPS_RESPONDER` valid (llm-only); LLM CLI on PATH (WARN when the binary is present, FAIL when it is absent); vision-gate check only when transport is cursor and vision is unsupported
- brand brief `context.md`; `accounts.json`; `personas.json`
- Postiz URL/key and learning-ready, only when Postiz creds exist
- `health_projectors.half_live_state` (`C8-F01`), only when `cfg.is_live`
- hashtag Layer A session, only when scrape is configured and a session exists; otherwise omitted
- `_postiz_reach_check` → `health_model.postiz_doctor_check`, only when Postiz creds exist
- `_zernio_reach_check`, only when Zernio creds exist; no key is N/A
- `_daemon_liveness_check` — heartbeat absent, stale, or past-due backlog is FAIL; snapshot miss is UNKNOWN
- `_deploy_code_check` — N/A when the daemon is not loaded; FAIL when loaded and either SHA is missing or the two differ; pass when both exist and are equal
- `_operational_sensor_checks`, `_sibling_launchd_checks`, `_studio_resident_check` (omitted when Studio has no plist)

`init_flow.run_init` reuses that report. It does not invent a second health screen.

### Daemon install path

`fanops daemon install` → `cli.cmd_daemon` → `daemon.install`. macOS only (`_require_darwin`). Writes `~/Library/LaunchAgents/com.fanops.run.plist` through `controlio.write_text_atomic`, refuses a plist whose `WorkingDirectory` is a different checkout, then `_load_plist` (bootout, `launchctl bootstrap`, `load -w` fallback). Also installs `com.fanops.keeper`, whose program arguments end in `daemon ensure`. `fanops daemon ensure` → `daemon.ensure`.

`daemon.ensure` kickstarts a **loaded** pump when `cfg.auto_adopt` is on and either `_last_heartbeat_code` or `_version_signal()[0]` is missing, or the two differ. Both present and equal does not kickstart. An unloaded pump is bootstrap, not this kickstart. Storm guard, run flock, and `_sync_locked_deps` still sit in front of `launchctl kickstart -k`. This pass did not run launchctl.

### Documented start command

- `README.md` quickstart step 2: `fanops doctor`
- `docs/RUNBOOK.md` and `docs/GOLIVE.md`: `fanops doctor`, then `fanops daemon install --interval 10m`
- `docs/runbooks/RUNTIME.md` steps 6–7: `fanops doctor`, `fanops run` (dry-run), `fanops daemon install`
- After pull, `docs/ENGINEERING_STANDARDS.md` STD-VER-02 and `cli_parser` `up`: `fanops up` (git advisory, Postiz, daemon, Studio). `fanops up --no-restart` skips the plane's freshness kickstart

`cli.main` loads `.env` with override, then `cli_dispatch.dispatch` routes `doctor`, `daemon`, `up`, and `init`.

### Missing-env posture

Absent `.env` does not abort `main`. `Settings.strict_validate` strict-checks values that are present (bool words, poster, responder, LLM transport, scrape delay). A bad present value FAILs `_env_settings_check` and does not raise out of doctor. Keys that are simply unset are not that failure. Runtime `Config` readers fail open to defaults (`auto_adopt` stays on unless an off-word is set). `cli._check_preflight` exits 2 when the LLM binary is missing or `FANOPS_RESPONDER` is not `llm`. A cwd root that disagrees with the installed daemon `WorkingDirectory` exits 2, except `fanops daemon status`.

### Evidence commands

```
git show d2cded31e29e027e4ed55bfdb99b9a93ac4f024e:src/fanops/daemon.py
git show d2cded31e29e027e4ed55bfdb99b9a93ac4f024e:src/fanops/doctor.py
git status --short --branch
```

HEAD `daemon.ensure` kickstarted only when both SHAs were present and different. Working-tree `doctor._deploy_code_check` already FAILs a loaded daemon when either SHA is missing. `daemon.ensure` now uses that same loaded miss-or-mismatch rule. Pytest was not run.

## Wave 1 evidence (dirty tree)

OPS-SHA-DAEMON is not a new defect. The working tree already uses the same loaded miss-or-mismatch invariant in both places. Launchd behaviour was not changed.

Observed `daemon.ensure` (`src/fanops/daemon.py:393`), inside `if cfg.auto_adopt` and `fail_open("ensure.kickstart_stale_code")`:

```python
if loaded and (running is None or deployed is None or running != deployed):
```

`running` is `_last_heartbeat_code(cfg)` (`daemon.py:389`). `deployed` is `_version_signal(cfg)[0]` (`daemon.py:390`). A missing SHA enters this kickstart predicate. It is not a quiet success. Storm guard, run flock, and `_sync_locked_deps` still sit after the predicate (`daemon.py:399-416`) and were not edited.

`doctor._deploy_code_check` (`src/fanops/doctor.py:244-258`): unloaded returns `None` (`if not st or not st.get("loaded")`). When loaded, `if running is None or deployed is None` FAILs, then `if running != deployed` FAILs. Both present and equal pass (`doctor.py:259`).

HEAD (`git show HEAD:src/fanops/daemon.py`) was `if running is not None and deployed is not None and running != deployed:` — kickstart only when both SHAs existed and differed. The dirty diff replaces that with the line above.

Existing tests already encode the predicate (not re-run): `tests/test_deploy_code_check.py` (`test_ensure_kickstarts_loaded_daemon_when_running_sha_missing`, `test_ensure_skips_kickstart_when_unloaded_and_sha_missing`) and `tests/test_self_adopt.py` (`test_kickstart_when_running_sha_absent`, `test_kickstart_when_deployed_sha_absent`). No new test was added.

Skim of the dirty `cli.py` / `health_probes.py` diffs: no new high-severity behaviour gap. `cli.cmd_retire_source` now passes `cfg` into `ledger_wipe.snapshot_is_restorable` (`cli.py` retire-source call); unrestorable still returns false and the command still exits 2. `health_probes.zernio_dep_health` maps `doctor._zernio_reach_check` onto `DepHealth` (`health_probes.py:69-79`); a failed auth check stays `ok=False`.

Commands (no pytest, no launchctl, no kickstart, no `fanops daemon ensure`):

```
git status --short --branch
git diff -U8 -- src/fanops/daemon.py
git diff -- src/fanops/doctor.py src/fanops/cli.py src/fanops/health_probes.py
git show HEAD:src/fanops/daemon.py
rg -n 'if loaded and \(running' src/fanops/daemon.py
rg -n 'if not st or not st.get|if running is None or deployed is None|if running != deployed' src/fanops/doctor.py
```
