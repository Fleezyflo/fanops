"""`fanops daemon` — durable unattended run via a macOS launchd LaunchAgent. Packages the existing
one-shot `fanops run` so it survives terminal/SSH/session death and restarts after a crash, WITHOUT
touching the pipeline or run loop (docs/GOLIVE.md calls `fanops run` "the cron/launchd entry point").

Two non-negotiable launchd gotchas drive the design:
  1. launchd's default cwd is `/`; combined with `Config(root=Path.cwd())` it would build a fresh
     empty MohFlow-FanOps/ workspace at `/`. WorkingDirectory in the plist is therefore cfg.ROOT.
  2. launchd jobs get a bare PATH (/usr/bin:/bin:...) and source NO shell profile — ffmpeg/whisper/
     claude/the venv `fanops` are all off it. The plist bakes in a full PATH derived at install time
     (`shutil.which` parents) so the background run finds its binaries.

macOS-only by intent (operator is on darwin). install/stop raise a clean RuntimeError off-darwin
rather than silently no-op'ing; a systemd --user sibling is the natural follow-up (the platform
guard marks the seam). Every `launchctl` call mirrors ingest._run_ffprobe (timeout + typed
ToolchainMissingError on absence). Backend stays dryrun by default — this never publishes."""
from __future__ import annotations
import contextlib, json, logging, os, plistlib, re, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.errors import ToolchainMissingError

_log = logging.getLogger(__name__)

LABEL = "com.fanops.run"
_LAUNCHCTL_TIMEOUT = 30.0
_VERDICT_UNLOADED_ALARM = "installed but NOT loaded — should be running"
_MIN_INTERVAL = 60                                    # launchd ThrottleInterval floor — sub-minute is meaningless
# kickstart -k SIGTERMs then waits for relaunch; launchd may hold "spawn scheduled" for the full
# ThrottleInterval. A 30s wrapper returns rc 124 mid-throttle → false NOT-READY (MOL-697).
_KICKSTART_TIMEOUT = float(_MIN_INTERVAL + 30)        # ThrottleInterval + slack


# ── pure path + render helpers (no side effects) ─────────────────────────────────────────────

def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

def installed_root() -> Path | None:
    """The root the installed main-daemon plist is pinned to (its WorkingDirectory), or None when no
    plist is installed / it is unreadable. Read-only; mirrors install's plist read (line ~251)."""
    p = plist_path()
    if not p.exists():
        return None
    try:
        wd = plistlib.loads(p.read_bytes()).get("WorkingDirectory")
    except Exception as exc:                             # unreadable/corrupt plist -> no divergence claim (fail-open)
        _log.warning("installed_root: could not read %s (%s); skipping divergence check", p, exc)
        return None
    return Path(wd).resolve() if wd else None

def root_divergence(cfg: Config) -> Path | None:
    """The installed daemon's pinned root when it DIFFERS from cfg.root AND cfg fell back to cwd
    (root_source == 'cwd') — i.e. this process would touch a different ledger than the daemon and the
    operator did not ask for that. None (no warning) when aligned, when FANOPS_ROOT/arg was explicit,
    or when no daemon is installed. FANOPS_ROOT is shell-only BY DESIGN (docs/CONFIG.md), so an
    unexported shell silently roots at cwd — this is the ONE surface that catches the split."""
    if getattr(cfg, "root_source", None) != "cwd":       # deliberate FANOPS_ROOT/arg -> never nag
        return None
    pinned = installed_root()
    if pinned is None or pinned == cfg.root.resolve():
        return None
    return pinned

def _fanops_bin() -> str:
    # The `fanops` next to the running interpreter — so the daemon uses the SAME venv that installed it,
    # never a different one earlier on PATH.
    return str(Path(sys.executable).parent / "fanops")

_FFMPEG_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin")

def _ffmpeg_full_dir() -> str | None:
    """Homebrew ffmpeg-full (libass / subtitles). None when the keg is absent — CI, Linux."""
    return str(_FFMPEG_FULL_BIN) if (_FFMPEG_FULL_BIN / "ffmpeg").exists() else None

def _daemon_path() -> str:
    """Full PATH to bake into the plist (launchd gives a bare one). Order: venv bin, ~/.local/bin
    when it holds `claude` (the native-install symlink — tracks the operator's CURRENT claude), the
    bin dirs holding `claude`/`cursor-agent` per shutil.which, homebrew (ffmpeg/whisper), then the
    system defaults. De-duped, absolute — nothing depends on a sourced shell profile at fire time.
    ffmpeg-full's bin precedes `/opt/homebrew/bin` so lite `ffmpeg` never wins (no on-screen hook).

    The stable shim dir goes AHEAD of the which()-derived parent because which() answers from THIS
    process's PATH: under the keeper's baked plist PATH that re-derives the same stale pin forever
    (2026-07-12: an nvm v18 dir pinned claude 2.0.30, which predates --json-schema, and every
    moment_hooks/captions gate call failed for days). The existence check is PATH-independent, so a
    plist rewrite from ANY environment converges on the current claude."""
    parts = [str(Path(sys.executable).parent)]
    stable = Path.home() / ".local" / "bin"
    if (stable / "claude").exists():
        parts.append(str(stable))
    for _bin in ("claude", "cursor-agent"):
        found = shutil.which(_bin)
        if found:
            parts.append(str(Path(found).parent))
    tail = []
    full = _ffmpeg_full_dir()
    if full:
        tail.append(full)
    tail += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    parts += tail
    seen: set[str] = set(); out: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p); out.append(p)
    return ":".join(out)

def resolve_responder(cfg: Config) -> str:
    """The responder a hands-off `fanops run` fire WILL use — always 'llm' now (the manual responder was
    retired; gates are answered ONLY by the LLM). Returns `cfg.responder_mode`, which VALIDATES
    FANOPS_RESPONDER (empty/unset OR 'llm' → 'llm'; anything else raises). The daemon plist is
    responder-AGNOSTIC — SCHEDULING is decoupled from the answer path; this just reports the fire-time mode."""
    return cfg.responder_mode

def format_interval(secs: int) -> str:
    """Seconds -> CLI `--interval` token (bare seconds; `parse_interval` round-trips)."""
    return str(secs)

def _installed_program(cfg: Config) -> str | None:
    """Read ProgramArguments[0] from the on-disk main plist (fail-open)."""
    p = plist_path()
    if not p.exists():
        return None
    try:
        pl = plistlib.loads(p.read_bytes())
        args = pl.get("ProgramArguments") or []
        if isinstance(args, list) and args and isinstance(args[0], str):
            return args[0]
    except Exception as exc:                             # unreadable/corrupt plist -> no program claim (fail-open)
        _log.warning("_installed_program: could not read %s (%s)", p, exc)
    return None

def _plist_spec(cfg: Config, interval: int) -> dict:
    return plistlib.loads(render_plist(cfg, interval=interval).encode())

def _cleanup_legacy_artifacts(cfg: Config) -> None:
    """Best-effort removal of pre-direct-exec wrapper + exec-fail marker (keeper migration path)."""
    with contextlib.suppress(OSError):
        (cfg.control / "fanops-run.sh").unlink(missing_ok=True)
        (cfg.control / "daemon-exec-fail.json").unlink(missing_ok=True)

def render_plist(cfg: Config, *, interval: int) -> str:
    """The LaunchAgent plist. WorkingDirectory=cfg.root (gotcha 1); EnvironmentVariables carries the
    full PATH+HOME+FANOPS_DAEMON_INTERVAL (gotcha 2). RunAtLoad starts once; KeepAlive restarts on crash
    (SuccessfulExit:false — NOT on clean stop). ThrottleInterval floors restart cadence at 60s.

    KeepAlive + LSMultipleInstancesProhibited: launchd never timer-re-fires — one resident loop only.
    A hung-but-alive process is NOT respawned (it hasn't exited); M2-C readiness alarms on a stale
    per-iteration heartbeat catch that case. plistlib produces valid, properly-escaped XML."""
    fb, path = _fanops_bin(), _daemon_path()
    pl = {
        "Label": LABEL,
        "ProgramArguments": [fb, "run", "--loop", "--interval", format_interval(interval)],
        "KeepAlive": {"SuccessfulExit": False},
        "RunAtLoad": True,
        "WorkingDirectory": str(cfg.root),
        "StandardOutPath": str(cfg.reports / "daemon.out"),
        "StandardErrorPath": str(cfg.reports / "daemon.err"),
        "ThrottleInterval": _MIN_INTERVAL,
        "LSMultipleInstancesProhibited": True,
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home()), "FANOPS_DAEMON_INTERVAL": str(interval)},
    }
    return plistlib.dumps(pl).decode()

def parse_interval(raw: str) -> int:
    """'10m'->600, '90s'->90, '2h'->7200, bare '600'->600. Rejects < 60s with a clean ValueError
    (the ThrottleInterval floor) rather than a silent clamp, so a typo'd cadence fails loudly."""
    raw = raw.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    # ECC fix #15: guard the numeric part so ""/"m"/"h" fail with a clean format message, not a raw
    # `int(): invalid literal` traceback leaking to the operator.
    digits = raw[:-1] if (raw and raw[-1] in units) else raw
    if not digits.isdigit():
        raise ValueError(f"invalid interval {raw!r} — use '10m', '90s', '2h', or bare seconds")
    secs = int(raw[:-1]) * units[raw[-1]] if raw and raw[-1] in units else int(raw)
    if secs < _MIN_INTERVAL:
        raise ValueError(f"interval must be >= {_MIN_INTERVAL}s (launchd ThrottleInterval floor), got {raw!r}")
    return secs

def installed_interval(cfg: Config) -> int | None:
    """Read the installed tick cadence so `status` judges staleness against the REAL interval, not a
    default. Under KeepAlive there is no plist StartInterval — read FANOPS_DAEMON_INTERVAL from the
    plist EnvironmentVariables (install writes it). Legacy StartInterval plists still round-trip. None
    if unreadable / absent. Best-effort, broad catch BY DESIGN: corrupt on-disk state must NEVER crash
    `daemon status`."""
    p = plist_path()
    if not p.exists():
        return None
    try:
        pl = plistlib.loads(p.read_bytes())
    except Exception as exc:                             # corrupt on-disk plist must never crash `daemon status`
        _log.warning("installed_interval: could not read %s (%s)", p, exc)
        return None
    env = pl.get("EnvironmentVariables") or {}
    if isinstance(env, dict):
        raw = env.get("FANOPS_DAEMON_INTERVAL")
        if raw is not None:
            try:
                return parse_interval(str(raw))
            except ValueError:
                pass
    val = pl.get("StartInterval")                                          # legacy pre-M2-B installs
    return val if isinstance(val, int) else None


# ── launchctl wrapper (mirror of ingest._run_ffprobe) ────────────────────────────────────────

def _launchctl(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    wait = _LAUNCHCTL_TIMEOUT if timeout is None else float(timeout)
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=wait)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError("launchctl not found on PATH — `fanops daemon` is macOS-only (launchd)") from e
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["launchctl", *args], returncode=124, stdout="", stderr="launchctl timed out")

def _grep_int(text: str, key: str) -> int | None:
    # launchctl list <label> prints a plist-style dump: `"PID" = 4321;`. Pull the int, None if absent.
    m = re.search(rf'"{key}"\s*=\s*(-?\d+)', text)
    return int(m.group(1)) if m else None

def _require_darwin() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("fanops daemon is macOS-only (launchd); no systemd --user port yet")

def _parse_etime(s: str) -> int | None:
    """BSD `ps -o etime=` elapsed time -> seconds. Formats: `MM:SS`, `HH:MM:SS`, `DD-HH:MM:SS`.
    Unparseable -> None."""
    s = (s or "").strip()
    if not s:
        return None
    days = 0
    if "-" in s:
        d, _, s = s.partition("-")
        try: days = int(d)
        except ValueError: return None
    parts = s.split(":")
    if not (2 <= len(parts) <= 3):
        return None
    try: nums = [int(p) for p in parts]
    except ValueError: return None
    h, m, sec = ([0] + nums) if len(nums) == 2 else nums
    return days * 86400 + h * 3600 + m * 60 + sec


def _confirm_loaded(label: str) -> bool:
    return _launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0

def _load_plist(plist: Path, label: str) -> bool:
    """Idempotent load with proof: bootout, bootstrap (retry until print confirms), load -w fallback."""
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{label}")          # idempotent; rc ignored
    for _ in range(3):
        _launchctl("bootstrap", f"gui/{uid}", str(plist))
        if _confirm_loaded(label):
            return True
        time.sleep(2)
    _launchctl("load", "-w", str(plist))
    return _confirm_loaded(label)


from fanops.daemon_siblings import (  # noqa: E402
    KEEPER_LABEL,
    KEEPER_POLL_INTERVAL_S,
    _install_keeper,
    ensure_keeper_loaded,
    keeper_plist_path,
)


def _adopt_settle_s(cfg: Config) -> int:
    """How long a freshly-kickstarted pump must be left alone before a SHA mismatch may justify ANOTHER
    kickstart: one full pass interval + one keeper tick.

    The storm guard used KEEPER_POLL_INTERVAL_S (120s) — but the keeper FIRES every 120s, so a pump's age
    is >= 120s at the very next fire and the guard let a kickstart through every cycle. Meanwhile the pump
    only stamps its running SHA into the heartbeat when a PASS completes (default interval 600s), so it was
    killed (SIGTERM) long before it could ever clear the mismatch: a permanent 120s restart loop in which no
    pass ever finished. That never fired historically only because `age` was always None (BSD ps has no
    `etimes`), so the guard skipped for the wrong reason and masked this. Fixing the ps keyword exposed it.

    The guard must therefore wait on what it is ACTUALLY waiting for — the pump's next heartbeat — not on
    the keeper's own cadence."""
    return (installed_interval(cfg) or 600) + KEEPER_POLL_INTERVAL_S


def _pump_pid_age_s() -> tuple[int | None, int | None]:
    """(pid, age_s) of the resident pump from launchd + `ps`, or (None, None) when the pump has no PID
    (not loaded / not running). age_s is None when a PID exists but its start-age is unreadable — the
    caller MUST treat that as 'do not storm' (skip), never as 'settled'. Used only by the keeper's
    code-drift storm guard: a freshly-kickstarted pump has a young PID, so its stale heartbeat (still the
    OLD SHA until the fresh pass finishes) must not trigger a re-kickstart.

    PORTABILITY (the bug this fixes): `etimes` (seconds) is a GNU/procps keyword and does NOT exist in BSD
    ps, so on macOS — the only platform this launchd path ever runs on — `ps -o etimes=` printed
    "ps: etimes: keyword not found" to stderr, exited 0, and left stdout EMPTY. age was therefore ALWAYS
    None, the storm guard's `age is None -> skip` always fired, and the keeper's code-drift self-heal could
    never adopt new code. Not a delay: permanently inert. Observed live 2026-07-16 — the pump sat on a
    day-old SHA through 18 merges while the keeper logged "skipping to avoid a restart storm" every 120s.
    Prefer BSD `etime` (always present here) and keep `etimes` as the Linux fallback."""
    r = _launchctl("list", LABEL)
    pid = _grep_int(r.stdout, "PID") if r.returncode == 0 else None
    if pid is None:
        return None, None
    age = None
    try:
        ps = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=10)
        if ps.returncode == 0:
            age = _parse_etime(ps.stdout)
        if age is None:                                   # GNU/procps fallback (etimes = seconds)
            ps = subprocess.run(["ps", "-o", "etimes=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=10)
            if ps.returncode == 0 and ps.stdout.strip():
                age = int(ps.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        age = None                                        # unreadable -> caller skips (never storm)
    return pid, age


# ── side-effecting verbs ─────────────────────────────────────────────────────────────────────

def install(cfg: Config, *, interval: int) -> dict:
    """Write the plist (direct `fanops run --loop` exec) and load via launchctl. Idempotent: bootout any
    prior copy first (ignore its rc), then bootstrap; fall back to `load -w` on older macOS.

    SCHEDULING ONLY — the daemon NEVER writes FANOPS_RESPONDER. Gates are always answered by the LLM (the
    manual responder was retired), so installing the driver has no AI-switch product choice to make; it just
    schedules the unattended loop. Returns the resolved responder (always 'llm') for the status line."""
    _require_darwin()
    cfg.reports.mkdir(parents=True, exist_ok=True)
    cfg.control.mkdir(parents=True, exist_ok=True)
    resolved = resolve_responder(cfg)                     # always 'llm' (validates FANOPS_RESPONDER); persists nothing
    pp = plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    if pp.exists():
        try:
            existing = plistlib.loads(pp.read_bytes())
        except (OSError, UnicodeDecodeError, plistlib.InvalidFileException, ValueError):
            existing = {}
        wd = existing.get("WorkingDirectory")
        if wd and Path(wd).resolve() != cfg.root.resolve():
            raise ValueError(f"existing daemon plist WorkingDirectory {wd!r} != {cfg.root!r} — "
                             f"refusing cross-checkout overwrite (stop --remove first)")
    _cleanup_legacy_artifacts(cfg)
    from fanops.controlio import write_text_atomic
    write_text_atomic(pp, render_plist(cfg, interval=interval))
    loaded = _load_plist(pp, LABEL)
    keeper = _install_keeper(cfg)
    return {"plist": str(pp), "interval": interval, "loaded": loaded,
            "responder": resolved, **keeper}

def ensure(cfg: Config) -> dict:
    """Keeper hook: re-assert main pump load when launchctl print says it is absent; also rewrite a
    stale on-disk plist when it no longer matches render_plist (direct-exec ProgramArguments + env)."""
    _require_darwin()
    action = "none"
    if _confirm_loaded(LABEL):
        loaded = True
    else:
        pp = plist_path()
        loaded = _load_plist(pp, LABEL) if pp.exists() else False
        action = "bootstrap" if pp.exists() else "none"
    iv = installed_interval(cfg)
    if iv is not None:
        pp = plist_path()
        expected = _plist_spec(cfg, iv)
        actual = plistlib.loads(pp.read_bytes()) if pp.exists() else {}
        if actual != expected:
            cfg.reports.mkdir(parents=True, exist_ok=True)
            pp.parent.mkdir(parents=True, exist_ok=True)
            from fanops.controlio import write_text_atomic
            write_text_atomic(pp, render_plist(cfg, interval=iv))
            if loaded:
                _load_plist(pp, LABEL)
            _cleanup_legacy_artifacts(cfg)
            if action == "none":
                action = "rewrite_plist"
    # Code-drift self-heal (keeper-adopts-pump): the pump's in-process os.execv adopter was deleted, so
    # the EXTERNAL keeper adopts new code. Compare the SHA the pump reports in its heartbeat to the SHA
    # on disk; kickstart the PUMP (not the keeper) when they differ. Kill switch default-on (matches
    # cli). Fail-open with one breadcrumb — a git/launchctl hiccup leaves the pump alone.
    # cfg.auto_adopt (default ON): off-words disable; a typo keeps the default ON
    # instead of the old `!= "0"` read, where "false"/"off" (anything but the literal "0") stayed ON.
    if cfg.auto_adopt:
        from fanops.errors import fail_open
        with fail_open("ensure.kickstart_stale_code"):
            running = _last_heartbeat_code(cfg)              # SHA the pump reports it is on
            deployed = _version_signal(cfg)[0]               # SHA on disk now
            if running is not None and deployed is not None and running != deployed:
                # STORM GUARD (replaces the deleted execv's baseline re-capture = one kickstart/deploy):
                # the keeper is stateless across 120s fires, and the pump's stale heartbeat keeps the OLD
                # SHA until its FIRST post-restart pass finishes (minutes-hours). Skip re-kickstarting while
                # the pump PID is younger than one keeper interval — err toward skip-not-storm when age is
                # unreadable. Exactly one kickstart per drift, then quiet until the fresh heartbeat clears it.
                pid, age = _pump_pid_age_s()
                settle = _adopt_settle_s(cfg)
                from fanops.pipeline_run import run_held
                if pid is not None and (age is None or age < settle):
                    _log.warning("ensure.kickstart_stale_code: pump pid=%s age=%ss < %ss settle (or unreadable) "
                                 "— skipping to avoid a restart storm (running=%s deployed=%s)",
                                 pid, age, settle, running, deployed)
                elif run_held(cfg):
                    # A live pass holds the run flock for the whole transcribe/produce — SIGTERM here
                    # restarts whisper from zero and the operator sees a "stuck" stage that was not stuck.
                    _log.warning("ensure.kickstart_stale_code: run flock held — skipping mid-pass SIGTERM "
                                 "(running=%s deployed=%s)", running, deployed)
                else:
                    _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LABEL}",
                               timeout=_KICKSTART_TIMEOUT)   # cycle the PUMP onto new code
                    _kickstart_studio_if_present(cfg)         # Studio's only adopter now (execv path deleted)
                    if action == "none":
                        action = "kickstart_stale_code"
    ensure_keeper_loaded(cfg)                             # keeper cannot heal itself when it is unloaded
    return {"label": LABEL, "loaded": loaded, "action": action}


def status(cfg: Config, *, interval: int = 600) -> dict:
    """Read-only liveness + readiness — PID-primary, thin caller of the one liveness owner. A live
    launchd PID + fresh activity is the PRIMARY truth: while loaded and daemon_progress reports
    alive_mid (the newest run.log line of ANY kind is younger than the ceiling), the verdict is
    `alive` REGARDLESS of the loop-heartbeat age (that heartbeat only lands after a whole pass
    finishes, so it must NEVER flip a fast-logging pass to stale). `heartbeat_age_s` stays in the
    dict for telemetry but no longer governs the verdict on its own. Not-loaded is an ALARM (should
    be running); a stage held with the log SILENT past the ceiling is stage-stuck; genuinely dead
    (no PID) is the ONLY 'not running' verdict. `interval` is the installed cadence. Also returns a
    SECOND, ORTHOGONAL verdict — `pass_verdict` / `last_success_age_s` (RC-6): is the pump COMPLETING
    passes? — which NEVER alters the liveness verdict above (a process can be `alive` on fresh activity
    yet be completing no passes)."""
    from fanops.health_model import heartbeat_stale, daemon_progress
    installed = plist_path().exists()
    r = _launchctl("list", LABEL)
    loaded = r.returncode == 0
    pid = _grep_int(r.stdout, "PID") if loaded else None
    last_exit = _grep_int(r.stdout, "LastExitStatus") if loaded else None
    age, stale, iv = heartbeat_stale(cfg, interval=installed_interval(cfg) or interval)
    # RC-6 (S08): the loop-origin heartbeat lands ONLY after a pass COMPLETES without halting
    # (cli._cmd_run_pass returns non-None -> _heartbeat(origin='loop')), so `age` IS the age of the last
    # SUCCESSFUL pass. Report it as a SECOND, ORTHOGONAL verdict — is the pump COMPLETING passes? — so a
    # bare `alive` (fresh log activity, via the alive_mid override below) can no longer HIDE a pump that
    # logs but never finishes a pass. Reuses the authoritative `stale` window (heartbeat_stale); invents
    # no new threshold and does NOT touch the alive/stale liveness decision.
    last_success_age_s = age
    from fanops.pipeline_run import paused as _paused
    # T1.3: a paused pump completes no passes BY DESIGN. Reporting that as "no successful pass in Ns"
    # is a lie the operator would page on, so the brake answers FIRST. The liveness `verdict` chain below
    # is untouched — a paused pump still logs run/paused every tick, so daemon_progress reads `alive`.
    if _paused(cfg):
        pass_verdict = "paused by operator"
    elif age is None:
        pass_verdict = "no completed pass yet"
    elif stale:
        pass_verdict = f"no successful pass in {int(age)}s"
    else:
        pass_verdict = "passes completing"
    run_line = None
    exec_fail = None
    target = _installed_program(cfg)
    if loaded and target and not os.access(target, os.X_OK):
        exec_fail = {"reason": "interpreter_not_executable", "target": target}
    if not loaded:
        verdict = _VERDICT_UNLOADED_ALARM if installed else "not installed"
    elif exec_fail:
        verdict = f"loaded but interpreter not executable: {exec_fail['target']}"
    elif not stale:
        verdict = "alive"                                    # fresh loop heartbeat (regression path)
    else:
        alive_mid, progress_line, snap = daemon_progress(cfg)
        run_line = progress_line
        if alive_mid:                                        # fresh activity governs — never 'stale'/'not running'
            verdict = "alive"
        elif snap and progress_line is not None:             # stage held AND log silent past ceiling
            act = _newest_activity_ts(cfg)                   # report the SILENCE (not stage_age) — matches the word
            silent = int((datetime.now(timezone.utc) - act).total_seconds()) if act else 0
            verdict = f"loaded but stage stuck ({snap['stage']} SILENT {silent}s)"
        elif age is None:
            verdict = "loaded but no heartbeat yet"
        else:
            verdict = f"loaded but stale (last heartbeat {int(age)}s ago)"
    return {"installed": installed, "loaded": loaded, "pid": pid, "last_exit": last_exit,
            "heartbeat_age_s": age, "last_success_age_s": last_success_age_s, "verdict": verdict,
            "pass_verdict": pass_verdict, "exec_fail": exec_fail, "run_line": run_line,
            "root": str(cfg.root), "daemon_root": str(installed_root() or "")}

def stop(cfg: Config, *, remove: bool = False) -> dict:
    """Unload the agent, then CONFIRM the real outcome (W10) instead of hardcoding success: the agent is
    stopped iff `launchctl list` no longer finds it (rc!=0). Idempotent — booting out an already-stopped
    label returns rc!=0, but the list confirm still reports stopped because the label isn't loaded ('already
    stopped' is not an error). The honest part: if an unload genuinely FAILED and the agent is STILL loaded,
    stopped is now False rather than a false True. Boots out the keeper FIRST so it cannot re-bootstrap the
    main pump within KEEPER_POLL_INTERVAL_S. Leaves plists on disk unless remove=True."""
    _require_darwin()
    uid = os.getuid()
    kr = _launchctl("bootout", f"gui/{uid}/{KEEPER_LABEL}")
    if kr.returncode != 0:
        _launchctl("unload", "-w", str(keeper_plist_path()))
    keeper_stopped = _launchctl("list", KEEPER_LABEL).returncode != 0
    r = _launchctl("bootout", f"gui/{uid}/{LABEL}")
    if r.returncode != 0:
        _launchctl("unload", "-w", str(plist_path()))    # fallback for older macOS (already-stopped is fine)
    stopped = _launchctl("list", LABEL).returncode != 0  # source of truth: not loaded -> stopped
    out = {"label": LABEL, "plist": str(plist_path()), "stopped": stopped, "keeper_stopped": keeper_stopped}
    if remove:
        with contextlib.suppress(OSError): plist_path().unlink(missing_ok=True)
        with contextlib.suppress(OSError): keeper_plist_path().unlink(missing_ok=True)
        _cleanup_legacy_artifacts(cfg)
        out["removed"] = True
    return out


# Sibling/keeper poll-timers — extracted to daemon_siblings; re-exported for stable imports.
from fanops.daemon_siblings import (  # noqa: E402, F401
    SIBLING_POLL_AGENTS,
    SIBLING_POLL_INTERVAL_S,
    SIBLING_POLL_TIMERS_RATIONALE,
    render_keeper_plist,
    sibling_agent_status,
    sibling_agents_status,
    sibling_plist_path,
)

# Studio launchd resident — extracted to daemon_studio; re-exported for stable imports.
from fanops.daemon_studio import (  # noqa: E402, F401
    STUDIO_DEFAULT_HOST,
    STUDIO_DEFAULT_PORT,
    STUDIO_LABEL,
    _STUDIO_LAUNCH_CMD,
    _STUDIO_PORT_STEP,
    _STUDIO_PORT_TRIES,
    _studio_fingerprint_matches,
    _studio_get_fingerprint,
    _studio_port_answers,
    _studio_port_answers_within,
    install_studio,
    render_studio_plist,
    stop_studio,
    studio_agent_status,
    studio_plist_path,
)

def tail_logs(cfg: Config, n: int = 40) -> str:
    p = cfg.log_path
    if not p.exists():
        return "no logs yet"
    # ECC fix #15: bounded memory — a long-running daemon's run.log can grow large; read the last n
    # lines via a deque instead of loading the whole file into memory to slice it.
    from collections import deque
    with p.open() as fh:
        return "\n".join(deque(fh, maxlen=n)).rstrip("\n")


# ── internals ────────────────────────────────────────────────────────────────────────────────

def _heartbeat_age_s(cfg: Config) -> float | None:
    """Age in seconds of the last heartbeat line in run.log, or None if no log / no heartbeat / a
    short or unparseable file. Reads JSON heartbeats (log.py) by stage+ts; legacy TAB lines still
    parse via the leading ISO column."""
    p = cfg.log_path
    if not p.exists():
        return None
    try:
        last_ts = None
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("stage") == "heartbeat" and rec.get("origin") == "loop":
                    last_ts = rec.get("ts")
            except json.JSONDecodeError:
                if "\theartbeat\t" in line:
                    last_ts = line.split("\t", 1)[0]
    except OSError:
        return None
    if last_ts is None:
        return None
    try:
        ts = datetime.fromisoformat(last_ts)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _last_heartbeat_code(cfg: Config) -> str | None:
    """The `code` (running-HEAD SHA) from the pump's most recent loop heartbeat in run.log, or None.
    Fail-open to None on: no log, unreadable, no heartbeat line, or a pre-upgrade heartbeat with no
    `code` field. Reads JSON heartbeats by stage=='heartbeat' + origin=='loop' (same convention as
    _heartbeat_age_s). None is load-bearing: the keeper's drift branch treats it as 'don't kickstart'
    (disarm), so a pre-upgrade pump missing the `code` key is never stormed."""
    p = cfg.log_path
    if not p.exists():
        return None
    try:
        code = None
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("stage") == "heartbeat" and rec.get("origin") == "loop":
                code = rec.get("code")
        return code
    except OSError:
        return None


# ── one-step bring-up (`fanops up`) ────────────────────────────────────────────────────────────
# Composes the four planes a cold/half-broken machine needs — git freshness (advisory), Docker+Postiz
# (self-healing via the existing on-demand script), daemon freshness (restart-onto-current-code), and
# Studio (report-only) — into ONE ordered, idempotent sequence that ends in a single honest verdict.
# Brief: docs/design/briefs/16-one-step-bring-up.md. Reuses `postiz-ondemand.sh ensure` verbatim
# (its docker_up + honest 200/401 probe + Mastra self-heal) and daemon.ensure's aliveness contract;
# it reimplements NONE of Docker/compose/launchd. Never publishes (posts are born awaiting_approval),
# never flips FANOPS_LIVE, never mutates the git tree.

_DEFAULT_ONDEMAND = Path.home() / "postiz-selfhost" / "postiz-ondemand.sh"
_ONDEMAND_WAIT_S = 200          # cold Postiz boot budget (script's own WAIT_S=180 + slack)
_KICKSTART_HEARTBEAT_TRIES = 60 # confirm one fresh loop heartbeat after a restart (~2 min at 2s)
_KICKSTART_HEARTBEAT_STEP = 2.0


def _ondemand_script() -> Path:
    """Path to the self-hosted Postiz on-demand script. FANOPS_POSTIZ_ONDEMAND overrides; else the
    conventional $HOME/postiz-selfhost/postiz-ondemand.sh (mirrors postiz_lifecycle._SCRIPT)."""
    v = (os.getenv("FANOPS_POSTIZ_ONDEMAND") or "").strip()
    return Path(v).expanduser() if v else (Path.home() / "postiz-selfhost" / "postiz-ondemand.sh")


def _tail(text: str, n: int = 6) -> str:
    """Last n non-empty lines of a captured stream, one-lined for the verdict."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) if lines else ""


def _newest_activity_ts(cfg: Config) -> datetime | None:
    """Timestamp of the newest parseable run.log line of ANY kind (tz-aware UTC), or None. Every
    structured record carries a top-level `ts` (log.py); legacy TAB lines expose the ISO in their
    leading column. This is the liveness signal that proves life DURING a long pass — a working stage
    emits a run.log line every ~60s (every stage/gate/llm call), so a fresh newest line means the pump
    is still working, however long the current stage runs. Read-only (no writes/threads), fail-open to
    None on no-log/unreadable/unparseable. Kept OFF _heartbeat_age_s (frozen byte-for-byte:
    health_model/doctor + the Prometheus gauge depend on it) — this reader counts EVERY line, not just
    the loop heartbeat, which is exactly why it can't share that byte-identical reader."""
    p = cfg.log_path
    if not p.exists():
        return None
    last_ts = None
    try:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts = rec.get("ts")
                if ts:
                    last_ts = ts
            except json.JSONDecodeError:
                first = line.split("\t", 1)[0].strip()       # legacy TAB line: leading ISO column
                if first:
                    last_ts = first
    except OSError:
        return None
    if last_ts is None:
        return None
    try:
        ts = datetime.fromisoformat(last_ts)
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _heartbeat_fresh_since(cfg: Config, since: datetime, *,
                           tries: int = _KICKSTART_HEARTBEAT_TRIES,
                           step: float = _KICKSTART_HEARTBEAT_STEP) -> bool:
    """Poll run.log until ANY new line newer than `since` (the restart instant) appears. The
    load-bearing freshness proof: a restarted daemon writes SOME run.log line within its first stage
    (not only a loop heartbeat, which won't land until the first whole pass FINISHES — hours on a big
    pass), so a line strictly newer than the kickstart means the fresh process is alive on current
    code within seconds. Bounded — returns False if none arrives within tries*step (the daemon didn't
    come back healthy)."""
    for _ in range(max(1, tries)):
        ts = _newest_activity_ts(cfg)
        if ts is not None and ts > since:
            return True
        time.sleep(step)
    return False


def _version_signal(cfg: Config) -> tuple[str | None, str]:
    """The running-code signal the self-adopt loop compares each tick, plus its SOURCE. Prefers
    `git rev-parse HEAD` in the CODE checkout that holds the running `fanops` package (moves
    per-commit — the real change signal); falls back to `fanops.__version__` (stale, doesn't move
    per-commit, so it only guards a totally git-less install); returns (None, 'unavailable') when
    BOTH are absent so the caller DISARMS self-adopt and logs a DEGRADED line rather than appear
    armed but never fire. NB: the signal follows the CODE tree, NOT cfg.root — cfg.root is the DATA
    workspace and (by design, the FANOPS_ROOT split) may have no .git. Fail-open with a breadcrumb:
    a git error / missing binary degrades to the version fallback."""
    from fanops.errors import fail_open
    import fanops, pathlib
    head = None
    with fail_open("version_signal.git"):
        code_root = pathlib.Path(fanops.__file__).resolve().parent
        r = subprocess.run(["git", "-C", str(code_root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            head = r.stdout.strip()
    if head:
        return head, "git-head"
    try:
        from fanops import __version__
    except ImportError:
        return None, "unavailable"
    return (str(__version__), "version") if __version__ else (None, "unavailable")


# ── the four planes (each returns a small verdict dict; tests drive them in isolation) ──────────

def _plane_git(cfg: Config) -> dict:
    """ADVISORY git freshness: `git fetch`, then report how far the CODE checkout's `main` trails
    `origin/main`. Runs against the tree holding the running `fanops` package (NOT cfg.root — the DATA
    workspace, which by the FANOPS_ROOT split may have no .git), same as the self-adopt signal. NEVER
    mutates the tree (no merge/reset/checkout — binding non-goal §6: a prior sync clobbered a live
    accounts.json, another produced a false verdict). Always ok=True: a stale tree or a failed fetch is
    surfaced, never fatal. The operator decides whether to sync."""
    import fanops, pathlib
    code_root = pathlib.Path(fanops.__file__).resolve().parent
    behind = ahead = None
    try:
        fetched = subprocess.run(["git", "-C", str(code_root), "fetch", "origin"],
                                 capture_output=True, text=True, timeout=60)
        if fetched.returncode != 0:
            return {"plane": "git", "ok": True, "behind": None, "ahead": None,
                    "detail": f"advisory: git fetch failed ({_tail(fetched.stderr, 2) or 'non-zero'}) — skipped freshness check"}
        rev = subprocess.run(["git", "-C", str(code_root), "rev-list", "--left-right", "--count", "main...origin/main"],
                             capture_output=True, text=True, timeout=30)
        if rev.returncode == 0 and rev.stdout.strip():
            parts = rev.stdout.split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"plane": "git", "ok": True, "behind": None, "ahead": None,
                "detail": f"advisory: freshness check skipped ({type(e).__name__})"}
    if behind is None:
        detail = "advisory: could not compare main to origin/main (skipped)"
    elif behind == 0:
        detail = "main is current with origin/main"
    else:
        detail = f"main is {behind} commit(s) behind origin/main — sync is the operator's call (bring-up does not mutate the tree)"
    return {"plane": "git", "ok": True, "behind": behind, "ahead": ahead, "detail": detail}


def _plane_postiz(cfg: Config) -> dict:
    """Docker + Postiz plane: shell out to `postiz-ondemand.sh ensure` (which starts Docker, brings
    the stack up idempotently, self-heals the Mastra crash-loop, and runs the honest 200/401-past-nginx
    probe — all reused verbatim). Gate: exit 0 = Postiz ready; non-zero surfaces the script's stderr
    tail (the honest diagnostic). A missing script emits a clear NOT-READY for this plane, no crash."""
    script = _ondemand_script()
    if not script.exists():
        return {"plane": "postiz", "ok": False,
                "detail": f"on-demand script not found at {script} — set FANOPS_POSTIZ_ONDEMAND or install ~/postiz-selfhost/postiz-ondemand.sh"}
    try:
        r = subprocess.run(["bash", str(script), "ensure"], capture_output=True, text=True, timeout=_ONDEMAND_WAIT_S)
    except subprocess.TimeoutExpired:
        return {"plane": "postiz", "ok": False, "detail": f"postiz-ondemand.sh ensure timed out after {_ONDEMAND_WAIT_S}s"}
    except OSError as e:
        return {"plane": "postiz", "ok": False, "detail": f"could not run postiz-ondemand.sh ({type(e).__name__}: {e})"}
    if r.returncode == 0:
        return {"plane": "postiz", "ok": True, "detail": _tail(r.stdout, 2) or "backend answering past nginx"}
    tail = _tail(r.stderr) or _tail(r.stdout) or f"exit {r.returncode}"
    return {"plane": "postiz", "ok": False, "detail": tail}


_LOCK_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+)")
_CI_UNIT_LOCK = "requirements/ci-unit.txt"


def _code_checkout_root() -> Path | None:
    """Repo root that holds the running `fanops` package (editable checkout), or None."""
    import fanops
    here = Path(fanops.__file__).resolve().parent
    for cand in (here, *here.parents):
        if (cand / _CI_UNIT_LOCK).is_file():
            return cand
    return None


def _locked_pins(lock: Path) -> dict[str, str]:
    """`name -> version` for every `==` pin in a pip-compile lock (comments/hashes ignored)."""
    pins: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = _LOCK_PIN_RE.match(line.strip())
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def _dep_drift(pins: dict[str, str]) -> list[str]:
    """Installed versions that differ from (or miss) the lock pins. Empty = fresh."""
    import importlib.metadata as md
    drifted: list[str] = []
    for name, want in sorted(pins.items()):
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            drifted.append(f"{name}:missing->{want}")
            continue
        if have != want:
            drifted.append(f"{name}:{have}->{want}")
    return drifted


def _sync_locked_deps() -> tuple[bool, str]:
    """Reconcile site-packages against requirements/ci-unit.txt. Site-packages only.

    Returns (ok, note). note is empty when already fresh (caller keeps its detail byte-identical).
    ok=False means the install failed — caller must not kickstart onto a half-synced venv."""
    root = _code_checkout_root()
    if root is None:
        return True, ""                                 # no lock beside the package — nothing to sync
    lock = root / _CI_UNIT_LOCK
    try:
        pins = _locked_pins(lock)
    except OSError as e:
        return False, f"lock unreadable ({type(e).__name__}: {e})"
    if not pins:
        return True, ""
    drifted = _dep_drift(pins)
    if not drifted:
        return True, ""
    # One install for the whole lock (hash-verified). Cap detail so the plane line stays readable.
    shown = ", ".join(drifted[:3])
    if len(drifted) > 3:
        shown += f" (+{len(drifted) - 3} more)"
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--require-hashes", "-r", str(lock)],
            cwd=str(root), capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"deps sync failed for {shown} ({type(e).__name__})"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        tail = err[-1][:120] if err else f"exit {r.returncode}"
        return False, f"deps sync failed for {shown}: {tail}"
    return True, f"deps synced ({shown})"


def _plane_daemon(cfg: Config, *, kickstart: bool = True) -> dict:
    """Daemon freshness plane. First ensure the launchd agent is LOADED via the existing
    daemon.ensure (unchanged aliveness contract — the keeper depends on it). If it was ALREADY
    running, `launchctl kickstart -k` restarts it onto current code (safe: posts are born
    awaiting_approval, nothing publishes on restart) and we confirm ONE fresh heartbeat newer than
    the restart. A not-yet-loaded daemon is brought up by ensure's own bootstrap (no kickstart).
    Off-darwin / launchctl-absent -> a typed honest skip (ok=False, skipped=True), never a crash —
    freshness simply can't be proven on this platform (mirrors cmd_daemon's degrade posture).

    Before kickstart, site-packages are reconciled against requirements/ci-unit.txt — "current code"
    is a false claim when the lock moved and the venv did not. Sync mutates site-packages only
    (never the tree / 00_control). A failed sync returns ok=False and skips kickstart."""
    try:
        was_running = _confirm_loaded(LABEL)
        ens = ensure(cfg)                          # aliveness: load if absent / rewrite a stale plist
    except (RuntimeError, ToolchainMissingError) as e:
        return {"plane": "daemon", "ok": False, "skipped": True, "restarted": False, "detail": str(e)}
    if not ens.get("loaded"):
        return {"plane": "daemon", "ok": False, "skipped": False, "restarted": False,
                "detail": "daemon plist present but launchctl could not load it — run `fanops daemon status`"}
    sync_ok, sync_note = _sync_locked_deps()
    if not sync_ok:
        return {"plane": "daemon", "ok": False, "skipped": False, "restarted": False,
                "detail": sync_note or "deps sync failed"}
    if was_running and kickstart:
        since = datetime.now(timezone.utc)
        kr = _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LABEL}", timeout=_KICKSTART_TIMEOUT)
        if kr.returncode != 0:
            return {"plane": "daemon", "ok": False, "skipped": False, "restarted": False,
                    "detail": f"kickstart failed (rc {kr.returncode}: {_tail(kr.stderr, 2) or 'no output'})"}
        if not _heartbeat_fresh_since(cfg, since):
            return {"plane": "daemon", "ok": False, "skipped": False, "restarted": True,
                    "detail": "restarted but no fresh heartbeat within the wait window — check 07_reports/daemon.err"}
        detail = "restarted onto current code; fresh heartbeat confirmed"
        if sync_note:
            detail = f"{detail}; {sync_note}"
        return {"plane": "daemon", "ok": True, "skipped": False, "restarted": True, "detail": detail}
    detail = f"loaded ({ens.get('action')})"
    if sync_note:
        detail = f"{detail}; {sync_note}"
    return {"plane": "daemon", "ok": True, "skipped": False, "restarted": False, "detail": detail}


def _redeploy_studio(cfg: Config, *, wait: bool = False) -> bool:
    """Cycle the Studio resident onto current code, iff its launchd job is installed. `kickstart -k`
    SIGKILLs the old resident (no request drain — an in-flight localhost request is dropped; tolerable
    for a single-operator cockpit) and launchd relaunches it via KeepAlive. Returns True when the job
    exists AND the port answers after the restart. Returns False when no plist is installed (nothing to
    cycle) or the restart didn't come back answering. Off-darwin / launchctl-absent -> False (fail-open;
    the caller keeps its report-only posture). Shared by _plane_studio and _kickstart_studio_if_present.

    `wait` picks the port gate: the bounded POLL (Studio needs seconds to rebind after the SIGKILL) or
    a single instant probe. `fanops up` waits — it reports the verdict to the operator. The keeper path
    does NOT: _kickstart_studio_if_present discards this result and runs inside daemon.ensure, which
    the keeper fires every KEEPER_POLL_INTERVAL_S (120s), so a poll there could overlap fires.

    MOL-728: when `wait=True`, also verifies deployment freshness and PID replacement. Since a
    `kickstart` does NOT change the plist, the `generation` invariant is preserved from the existing
    plist (if any)."""
    if not studio_plist_path().exists():
        return False
    
    # Capture old PID to verify replacement
    old_st = studio_agent_status()
    old_pid = old_st.get("pid")
    
    try:
        kr = _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{STUDIO_LABEL}", timeout=_KICKSTART_TIMEOUT)
    except (RuntimeError, ToolchainMissingError):
        return False
    if kr.returncode != 0:
        return False
    
    if not wait:
        import fanops.daemon as _daemon
        return _daemon._studio_port_answers()
        
    # Freshness verification: SHA on disk must match serving code.
    # PID replacement: serving PID must differ from old_pid.
    # Generation: if the plist has one, it must match.
    sha, _src = _version_signal(cfg)
    expect_gen = None
    from fanops.errors import fail_open
    with fail_open("daemon._redeploy_studio.plist_generation"):   # no plist / unreadable -> skip the
        import plistlib                                          # generation check, keep sha + pid
        pl = plistlib.loads(studio_plist_path().read_bytes())
        expect_gen = pl.get("EnvironmentVariables", {}).get("FANOPS_STUDIO_GENERATION")
    
    import fanops.daemon as _daemon
    return _daemon._studio_port_answers_within(expect_sha=sha, expect_gen=expect_gen, old_pid=old_pid)


def _kickstart_studio_if_present(cfg: Config) -> None:
    """Best-effort Studio redeploy used by the self-adopt path (cli run loop): if the Studio launchd
    job is installed, cycle it onto current code; no-op when absent. Never raises (fail-open with a
    breadcrumb) — a Studio redeploy hiccup must not abort the daemon's own self-adopt re-exec."""
    from fanops.errors import fail_open
    with fail_open("kickstart_studio_if_present"):
        _redeploy_studio(cfg)


def _plane_studio(cfg: Config) -> dict:
    """Studio plane — non-gating (never fails the overall verdict). When the com.fanops.studio launchd
    job is installed, ACTUALLY cycle it onto current code via _redeploy_studio(wait=True) — waiting out
    the seconds Flask needs to rebind after the SIGKILL, so a healthy cycle is never reported DOWN — and
    report cycled + answering; when absent (no plist to cycle), stay report-only (the port is probed
    ONCE there: nothing was restarted) and print the exact launch command
    (in .claude/launch.json). Either way Studio never blocks `up`'s READY (report-only gate posture
    unchanged) — the difference is `fanops up` now cycles Studio instead of just printing a command."""
    import fanops.daemon as _daemon
    if studio_plist_path().exists():
        if _redeploy_studio(cfg, wait=True):
            return {"plane": "studio", "ok": True, "report_only": True,
                    "detail": f"cycled onto current code; answering at http://{STUDIO_DEFAULT_HOST}:{STUDIO_DEFAULT_PORT}"}
        return {"plane": "studio", "ok": False, "report_only": True,
                "detail": f"restarted but not answering on {STUDIO_DEFAULT_HOST}:{STUDIO_DEFAULT_PORT} after {int(_STUDIO_PORT_TRIES * _STUDIO_PORT_STEP)}s — check 07_reports/studio.err"}
    if _daemon._studio_port_answers():
        return {"plane": "studio", "ok": True, "report_only": True,
                "detail": f"answering at http://{STUDIO_DEFAULT_HOST}:{STUDIO_DEFAULT_PORT}"}
    return {"plane": "studio", "ok": False, "report_only": True,
            "detail": f"not serving — start it with: {_STUDIO_LAUNCH_CMD}"}


def up(cfg: Config, *, kickstart: bool = True) -> dict:
    """Compose the four planes in dependency order, gating each GATING plane on the prior's real
    health signal, and return ONE verdict. git (advisory) -> postiz (gate) -> daemon (gate) ->
    studio (report). Short-circuits at the first FAILING gate (postiz/daemon): a plane whose probe
    fails stops the sequence with its diagnostic (honesty principle — never READY on an unproven
    plane). git behind-main and a down Studio are surfaced but never block READY."""
    git = _plane_git(cfg)
    result = {"git": git, "postiz": None, "daemon": None, "studio": None,
              "ready": False, "first_fail": None, "verdict": ""}

    postiz = _plane_postiz(cfg); result["postiz"] = postiz
    if not postiz["ok"]:
        result["first_fail"] = "postiz"
        result["verdict"] = _render_verdict(result)
        return result

    daemon_p = _plane_daemon(cfg, kickstart=kickstart); result["daemon"] = daemon_p
    if not daemon_p["ok"]:
        result["first_fail"] = "daemon"
        result["verdict"] = _render_verdict(result)
        return result

    studio = _plane_studio(cfg); result["studio"] = studio    # report-only: never a gate
    result["ready"] = True
    result["verdict"] = _render_verdict(result)
    return result


def _render_verdict(result: dict) -> str:
    """One READY / NOT-READY line. NOT-READY names the first failing plane + its diagnostic tail."""
    if result["ready"]:
        return "READY"
    ff = result["first_fail"]
    detail = (result.get(ff) or {}).get("detail", "") if ff else ""
    return f"NOT-READY: {ff} — {detail}" if ff else "NOT-READY"


def format_up_report(result: dict) -> list[str]:
    """The 4-line plane status + the verdict, for the CLI to print. A plane not reached (short-circuit)
    prints as pending."""
    def line(name: str) -> str:
        p = result.get(name)
        if p is None:
            return f"  [ -- ] {name}: (not reached)"
        mark = "ok  " if p["ok"] else ("skip" if p.get("skipped") else "DOWN")
        return f"  [{mark}] {name}: {p.get('detail', '')}"
    return [line("git"), line("postiz"), line("daemon"), line("studio"), result["verdict"]]
