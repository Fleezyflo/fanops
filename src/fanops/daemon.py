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
import contextlib, os, plistlib, re, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.errors import ToolchainMissingError

LABEL = "com.fanops.run"
KEEPER_LABEL = "com.fanops.keeper"
STUDIO_LABEL = "com.fanops.studio"
STUDIO_DEFAULT_HOST = "127.0.0.1"
STUDIO_DEFAULT_PORT = 8787
KEEPER_POLL_INTERVAL_S = 120
_LAUNCHCTL_TIMEOUT = 30.0
_MIN_INTERVAL = 60                                    # launchd ThrottleInterval floor — sub-minute is meaningless


# ── pure path + render helpers (no side effects) ─────────────────────────────────────────────

def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

def _fanops_bin() -> str:
    # The `fanops` next to the running interpreter — so the daemon uses the SAME venv that installed it,
    # never a different one earlier on PATH.
    return str(Path(sys.executable).parent / "fanops")

def _daemon_path() -> str:
    """Full PATH to bake into the plist (launchd gives a bare one). Order: venv bin, the node
    bin holding `claude` (derived now), homebrew (ffmpeg/whisper), then the system defaults. De-duped,
    absolute — nothing depends on a sourced shell profile at fire time."""
    parts = [str(Path(sys.executable).parent)]
    claude = shutil.which("claude")
    if claude:
        parts.append(str(Path(claude).parent))
    parts += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    seen: set[str] = set(); out: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p); out.append(p)
    return ":".join(out)

def resolve_responder(cfg: Config) -> str:
    """The responder a hands-off `fanops run` fire WILL use — `Config.responder_mode` is the SINGLE
    source of truth (.env `FANOPS_RESPONDER`, else 'llm' when `claude` is on PATH, else 'manual'). The
    daemon plist is responder-AGNOSTIC: SCHEDULING (the launchd agent) and the AI SWITCH (.env) are
    decoupled, so installing the driver never silently turns the LLM on — this just reports what the run
    resolves at fire time, which the CLI/Studio then DISCLOSE."""
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
    except Exception:
        pass
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
    except Exception:
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

def _launchctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=_LAUNCHCTL_TIMEOUT)
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


# ── side-effecting verbs ─────────────────────────────────────────────────────────────────────

def install(cfg: Config, *, interval: int, responder: str = "inherit") -> dict:
    """Write the plist (direct `fanops run --loop` exec) and load via launchctl. Idempotent: bootout any
    prior copy first (ignore its rc), then bootstrap; fall back to `load -w` on older macOS.

    `responder` is the AI-switch CHOICE, decoupled from this scheduling install:
      - 'inherit' (default): touch NOTHING — the fire-time run resolves the ambient responder. Installing
        the driver never silently turns the LLM on.
      - 'llm'/'manual': PERSIST it to .env (the durable single source of truth) so every future fire honors it.
    Returns the RESOLVED responder + `discloses_llm` so the caller can DISCLOSE the recurring-LLM cost."""
    _require_darwin()
    cfg.reports.mkdir(parents=True, exist_ok=True)
    cfg.control.mkdir(parents=True, exist_ok=True)
    if responder in ("llm", "manual"):
        from fanops.autopilot import set_env_var          # lazy: avoids a daemon<->autopilot import cycle at load
        set_env_var(cfg.root / ".env", "FANOPS_RESPONDER", responder)   # durable; Config loads it override=True at fire time
        resolved = responder
    else:
        resolved = resolve_responder(cfg)                 # 'inherit' -> what the run resolves ambiently, persist nothing
    pp = plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_artifacts(cfg)
    pp.write_text(render_plist(cfg, interval=interval))
    loaded = _load_plist(pp, LABEL)
    keeper = _install_keeper(cfg)
    return {"plist": str(pp), "interval": interval, "loaded": loaded,
            "responder": resolved, "discloses_llm": resolved == "llm", **keeper}

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
            pp.write_text(render_plist(cfg, interval=iv))
            if loaded:
                _load_plist(pp, LABEL)
            _cleanup_legacy_artifacts(cfg)
            if action == "none":
                action = "rewrite_plist"
    return {"label": LABEL, "loaded": loaded, "action": action}

_VERDICT_UNLOADED_ALARM = "installed but NOT loaded — should be running"

# ── M2-D: host-level poll-timer siblings (explicitly NOT KeepAlive residents) ────────────────
# Decision (MOL-355): com.fanops.postiz-reaper + com.fanops.media-sync stay StartInterval 300s
# poll-timers — NOT the KeepAlive+--loop model used by com.fanops.run (M2-B). Each sibling is a
# short cron-style job: launchd fires it, it runs one bounded unit of work, exits cleanly, sleeps
# until the next StartInterval. KeepAlive would be wrong for both:
#   • postiz-reaper — probes whether local Postiz is idle and STOPS the Docker stack to reclaim RAM;
#     pairs with postiz_lifecycle.ensure_up (on-demand bring-up at publish). A resident process would
#     fight that on-demand/idle-stop cycle or respawn a successful one-shot endlessly.
#   • media-sync — batch-scans and mirrors uploads to R2 (~5 min). Publish-time mirror in postiz.py
#     is the correctness path; this job is a convenience pre-mirror. Fire-and-exit cron semantics,
#     not a long-lived sync daemon.
# Silent death is still caught: M2-C readiness alarms treat plist-on-disk + launchctl-not-loaded as
# ALARM for every installed agent in the fleet (main pump + siblings).
SIBLING_POLL_INTERVAL_S = 300
SIBLING_POLL_TIMERS_RATIONALE = (
    "postiz-reaper and media-sync remain StartInterval poll-timers (300s): each is a short "
    "cron-style job (run → exit → sleep until next fire), not a KeepAlive resident. "
    "Reaper stops idle local Postiz (RAM); media-sync pre-mirrors to R2 (publish path mirrors inline). "
    "M2-C readiness alarms still flag plist-on-disk + not-loaded for every installed sibling."
)
SIBLING_POLL_AGENTS: tuple[dict[str, str | int], ...] = (
    {"label": "com.fanops.postiz-reaper", "short": "Postiz reaper"},
    {"label": "com.fanops.media-sync", "short": "media-sync"},
    {"label": KEEPER_LABEL, "short": "daemon keeper", "poll_interval_s": KEEPER_POLL_INTERVAL_S},
)

def sibling_plist_path(label: str) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{label}.plist"

def keeper_plist_path() -> Path:
    return sibling_plist_path(KEEPER_LABEL)

def render_keeper_plist(cfg: Config) -> str:
    """StartInterval poll-timer: fire-and-exit `fanops daemon ensure` every 120s to re-assert main pump."""
    fb, path = _fanops_bin(), _daemon_path()
    pl = {
        "Label": KEEPER_LABEL,
        "ProgramArguments": [fb, "daemon", "ensure"],
        "StartInterval": KEEPER_POLL_INTERVAL_S,
        "RunAtLoad": True,
        "WorkingDirectory": str(cfg.root),
        "StandardOutPath": str(cfg.reports / "daemon-keeper.out"),
        "StandardErrorPath": str(cfg.reports / "daemon-keeper.err"),
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
    }
    return plistlib.dumps(pl).decode()

def _install_keeper(cfg: Config) -> dict:
    kp = keeper_plist_path()
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text(render_keeper_plist(cfg))
    return {"keeper_loaded": _load_plist(kp, KEEPER_LABEL), "keeper_plist": str(kp)}

def sibling_agent_status(label: str, *, short: str = "", poll_interval_s: int | None = None) -> dict:
    """Readiness for one host-level poll-timer sibling. plist-on-disk + not-loaded = ALARM."""
    if poll_interval_s is None:
        for spec in SIBLING_POLL_AGENTS:
            if spec["label"] == label:
                poll_interval_s = int(spec.get("poll_interval_s", SIBLING_POLL_INTERVAL_S))
                break
    installed = sibling_plist_path(label).exists()
    try:
        r = _launchctl("list", label)
        loaded = r.returncode == 0
        pid = _grep_int(r.stdout, "PID") if loaded else None
    except Exception:
        loaded, pid = False, None
    if not installed:
        verdict = "not installed"
    elif not loaded:
        verdict = _VERDICT_UNLOADED_ALARM
    else:
        verdict = "loaded"
    iv = poll_interval_s if poll_interval_s is not None else SIBLING_POLL_INTERVAL_S
    return {"label": label, "short": short or label, "installed": installed, "loaded": loaded, "pid": pid,
            "verdict": verdict, "poll_interval_s": iv, "alarm": installed and not loaded}

def sibling_agents_status() -> list[dict]:
    """All known poll-timer siblings — doctor + Studio readiness surfaces (fail-open off-darwin)."""
    if sys.platform != "darwin":
        return []
    out: list[dict] = []
    for spec in SIBLING_POLL_AGENTS:
        iv = spec.get("poll_interval_s", SIBLING_POLL_INTERVAL_S)
        try:
            out.append(sibling_agent_status(spec["label"], short=str(spec["short"]), poll_interval_s=int(iv)))
        except Exception:
            out.append({"label": spec["label"], "short": spec["short"], "installed": False, "loaded": False,
                        "verdict": "unknown", "poll_interval_s": int(iv), "alarm": False})
    return out


def status(cfg: Config, *, interval: int = 600) -> dict:
    """Read-only liveness + readiness: plist-on-disk + not-loaded is an ALARM (should be running);
    loaded + heartbeat-fresh is alive; loaded + heartbeat-stale is stale. `interval` is the installed
    cadence — alive iff the last heartbeat is younger than 3 intervals."""
    from fanops.health_model import heartbeat_stale
    installed = plist_path().exists()
    r = _launchctl("list", LABEL)
    loaded = r.returncode == 0
    pid = _grep_int(r.stdout, "PID") if loaded else None
    last_exit = _grep_int(r.stdout, "LastExitStatus") if loaded else None
    age, stale, iv = heartbeat_stale(cfg, interval=installed_interval(cfg) or interval)
    exec_fail = None
    target = _installed_program(cfg)
    if loaded and target and not os.access(target, os.X_OK):
        exec_fail = {"reason": "interpreter_not_executable", "target": target}
    if not loaded:
        verdict = _VERDICT_UNLOADED_ALARM if installed else "not installed"
    elif exec_fail:
        verdict = f"loaded but interpreter not executable: {exec_fail['target']}"
    elif age is None:
        verdict = "loaded but no heartbeat yet"
    elif not stale:
        verdict = "alive"
    else:
        verdict = f"loaded but stale (last heartbeat {int(age)}s ago)"
    return {"installed": installed, "loaded": loaded, "pid": pid, "last_exit": last_exit,
            "heartbeat_age_s": age, "verdict": verdict, "exec_fail": exec_fail}

def stop(cfg: Config, *, remove: bool = False) -> dict:
    """Unload the agent, then CONFIRM the real outcome (W10) instead of hardcoding success: the agent is
    stopped iff `launchctl list` no longer finds it (rc!=0). Idempotent — booting out an already-stopped
    label returns rc!=0, but the list confirm still reports stopped because the label isn't loaded ('already
    stopped' is not an error). The honest part: if an unload genuinely FAILED and the agent is STILL loaded,
    stopped is now False rather than a false True. Leaves the plist on disk unless remove=True."""
    _require_darwin()
    uid = os.getuid()
    r = _launchctl("bootout", f"gui/{uid}/{LABEL}")
    if r.returncode != 0:
        _launchctl("unload", "-w", str(plist_path()))    # fallback for older macOS (already-stopped is fine)
    stopped = _launchctl("list", LABEL).returncode != 0  # source of truth: not loaded -> stopped
    out = {"label": LABEL, "plist": str(plist_path()), "stopped": stopped}
    if remove:
        with contextlib.suppress(OSError): plist_path().unlink(missing_ok=True)
        _cleanup_legacy_artifacts(cfg)
        out["removed"] = True
    return out

def studio_plist_path() -> Path:
    return sibling_plist_path(STUDIO_LABEL)

def render_studio_plist(cfg: Config, *, host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT) -> str:
    """KeepAlive resident for the localhost Studio cockpit — direct `fanops studio` exec (keeper-style, no bash wrapper)."""
    fb, path = _fanops_bin(), _daemon_path()
    pl = {
        "Label": STUDIO_LABEL,
        "ProgramArguments": [fb, "studio", "--host", host, "--port", str(port)],
        "KeepAlive": {"SuccessfulExit": False},
        "RunAtLoad": True,
        "WorkingDirectory": str(cfg.root),
        "StandardOutPath": str(cfg.reports / "studio.out"),
        "StandardErrorPath": str(cfg.reports / "studio.err"),
        "ThrottleInterval": _MIN_INTERVAL,
        "LSMultipleInstancesProhibited": True,
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
    }
    return plistlib.dumps(pl).decode()

def studio_agent_status() -> dict:
    """Readiness for the Studio KeepAlive resident. plist-on-disk + not-loaded = ALARM (fail-open off-darwin)."""
    if sys.platform != "darwin":
        return {"label": STUDIO_LABEL, "short": "Studio", "installed": False, "loaded": False,
                "pid": None, "verdict": "not installed", "alarm": False}
    installed = studio_plist_path().exists()
    try:
        r = _launchctl("list", STUDIO_LABEL)
        loaded = r.returncode == 0
        pid = _grep_int(r.stdout, "PID") if loaded else None
    except Exception:
        loaded, pid = False, None
    if not installed:
        verdict = "not installed"
    elif not loaded:
        verdict = _VERDICT_UNLOADED_ALARM
    else:
        verdict = "loaded"
    return {"label": STUDIO_LABEL, "short": "Studio", "installed": installed, "loaded": loaded, "pid": pid,
            "verdict": verdict, "alarm": installed and not loaded}

def install_studio(cfg: Config, *, host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT) -> dict:
    """Write the Studio KeepAlive plist and load via launchctl. Idempotent: bootout any prior copy first."""
    _require_darwin()
    cfg.reports.mkdir(parents=True, exist_ok=True)
    pp = studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(render_studio_plist(cfg, host=host, port=port))
    loaded = _load_plist(pp, STUDIO_LABEL)
    return {"studio_loaded": loaded, "studio_plist": str(pp), "host": host, "port": port}

def stop_studio(cfg: Config, *, remove: bool = False) -> dict:
    """Unload the Studio agent; confirm via launchctl list. remove=True deletes the plist."""
    _require_darwin()
    uid = os.getuid()
    r = _launchctl("bootout", f"gui/{uid}/{STUDIO_LABEL}")
    if r.returncode != 0:
        _launchctl("unload", "-w", str(studio_plist_path()))
    stopped = _launchctl("list", STUDIO_LABEL).returncode != 0
    out = {"label": STUDIO_LABEL, "plist": str(studio_plist_path()), "stopped": stopped}
    if remove:
        try: studio_plist_path().unlink()
        except OSError: pass
        out["removed"] = True
    return out

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
    import json
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
                if rec.get("stage") == "heartbeat":
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
