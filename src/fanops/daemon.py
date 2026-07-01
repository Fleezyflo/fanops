"""`fanops daemon` — durable unattended run via a macOS launchd LaunchAgent. Packages the existing
one-shot `fanops run` so it survives terminal/SSH/session death and restarts after a crash, WITHOUT
touching the pipeline or run loop (docs/GOLIVE.md calls `fanops run` "the cron/launchd entry point").

Two non-negotiable launchd gotchas drive the design:
  1. launchd's default cwd is `/`; combined with `Config(root=Path.cwd())` it would build a fresh
     empty MohFlow-FanOps/ workspace at `/`. WorkingDirectory in the plist is therefore cfg.ROOT.
  2. launchd jobs get a bare PATH (/usr/bin:/bin:...) and source NO shell profile — ffmpeg/whisper/
     claude/the venv `fanops` are all off it. The wrapper + plist bake in a full PATH derived at
     install time (`shutil.which` parents) so the background run finds its binaries.

macOS-only by intent (operator is on darwin). install/stop raise a clean RuntimeError off-darwin
rather than silently no-op'ing; a systemd --user sibling is the natural follow-up (the platform
guard marks the seam). Every `launchctl` call mirrors ingest._run_ffprobe (timeout + typed
ToolchainMissingError on absence). Backend stays dryrun by default — this never publishes."""
from __future__ import annotations
import os, plistlib, re, shlex, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.errors import ToolchainMissingError

LABEL = "com.fanops.run"
_LAUNCHCTL_TIMEOUT = 30.0
_MIN_INTERVAL = 60                                    # launchd ThrottleInterval floor — sub-minute is meaningless


# ── pure path + render helpers (no side effects) ─────────────────────────────────────────────

def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

def wrapper_path(cfg: Config) -> Path:
    return cfg.control / "fanops-run.sh"             # inside the workspace (00_control), beside the ledger

def _fanops_bin() -> str:
    # The `fanops` next to the running interpreter — so the daemon uses the SAME venv that installed it,
    # never a different one earlier on PATH.
    return str(Path(sys.executable).parent / "fanops")

def _daemon_path() -> str:
    """Full PATH to bake into the wrapper + plist (launchd gives a bare one). Order: venv bin, the node
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
    daemon wrapper is responder-AGNOSTIC: SCHEDULING (the launchd agent) and the AI SWITCH (.env) are
    decoupled, so installing the driver never silently turns the LLM on — this just reports what the run
    resolves at fire time, which the CLI/Studio then DISCLOSE."""
    return cfg.responder_mode

def render_wrapper(cfg: Config, *, interval: int) -> str:
    """The `#!/bin/bash` script launchd execs. One-shot: `exec fanops run` with a FRESH now as
    --base-time each fire (the default base-time is a fixed past date — a daemon must advance it).

    This operator-installed loop is THE autonomous publish+reconcile trigger (P2): each fire's
    `fanops run` -> advance -> reconciles parked posts (Postiz or Zernio) AND publishes every `queued`
    post whose operator-set scheduled_time is now due (publish_due's due-gate). No Python-side scheduler
    exists or is added — a due post fires unattended ONLY if the operator ran `fanops daemon install`;
    otherwise the supported paths are a manual `fanops run` / Studio Publish-now. dryrun publishes nothing.

    DECOUPLED from the AI switch: the wrapper bakes NO FANOPS_RESPONDER. The fire-time `fanops run`
    resolves the responder via `Config.responder_mode` (.env is loaded override=True at Config init), so
    the operator sets the AI on/off ONCE (in .env) and scheduling merely honors it — never welds them."""
    # shlex.quote every interpolated path/value: a workspace path with a space/quote/$ would otherwise
    # break out of the double-quotes (or let bash expand `$x`), silently running `fanops run` from the
    # WRONG cwd and defeating the daemon's #1 invariant. The `$(date ...)` substitution is left literal
    # ON PURPOSE — it must execute at fire time, not be quoted.
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"# launchd reruns this wrapper every {interval}s (StartInterval); each run is one-shot.\n"
        f"export PATH={shlex.quote(_daemon_path())}\n"
        f"cd {shlex.quote(str(cfg.root))}\n"
        f'exec {shlex.quote(_fanops_bin())} run --base-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
    )

def render_plist(cfg: Config, *, interval: int) -> str:
    """The LaunchAgent plist. WorkingDirectory=cfg.root (gotcha 1); EnvironmentVariables carries the
    full PATH+HOME (gotcha 2). RunAtLoad fires once on load; StartInterval reruns every `interval`s;
    ThrottleInterval floors restart cadence at 60s. plistlib produces valid, properly-escaped XML."""
    path = _daemon_path()
    pl = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(wrapper_path(cfg))],
        "StartInterval": interval,
        "RunAtLoad": True,
        "WorkingDirectory": str(cfg.root),
        "StandardOutPath": str(cfg.reports / "daemon.out"),
        "StandardErrorPath": str(cfg.reports / "daemon.err"),
        "ThrottleInterval": _MIN_INTERVAL,
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
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
    """Read StartInterval back from the on-disk plist so `status` judges staleness against the REAL
    cadence, not a default. None if no plist / key absent / non-int / unreadable. Best-effort, broad
    catch BY DESIGN: a corrupt plist (XML/expat errors are varied) must NEVER crash `daemon status` —
    same posture as Config.tuning() for a malformed tuning.json. The isinstance guard avoids the
    int(None)/int(<str>) trap when the key is missing or hand-edited to a non-integer."""
    p = plist_path()
    if not p.exists():
        return None
    try:
        val = plistlib.loads(p.read_bytes()).get("StartInterval")
    except Exception:
        return None
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


# ── side-effecting verbs ─────────────────────────────────────────────────────────────────────

def install(cfg: Config, *, interval: int, responder: str = "inherit") -> dict:
    """Write the wrapper (chmod 0755) + plist, then load via launchctl. Idempotent: bootout any
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
    wp, pp = wrapper_path(cfg), plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(render_wrapper(cfg, interval=interval)); wp.chmod(0o755)
    pp.write_text(render_plist(cfg, interval=interval))
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{LABEL}")          # idempotent reinstall; not-loaded -> rc!=0, ignored
    r = _launchctl("bootstrap", f"gui/{uid}", str(pp))   # modern (macOS 11+)
    if r.returncode != 0:
        r = _launchctl("load", "-w", str(pp))            # fallback for older / edge-case macOS
    return {"plist": str(pp), "wrapper": str(wp), "interval": interval, "loaded": r.returncode == 0,
            "responder": resolved, "discloses_llm": resolved == "llm"}

def status(cfg: Config, *, interval: int = 600) -> dict:
    """Read-only liveness: is the agent loaded (launchctl list) AND actually firing (heartbeat fresh)?
    `interval` is the installed cadence — alive iff the last heartbeat is younger than 3 intervals."""
    r = _launchctl("list", LABEL)
    loaded = r.returncode == 0
    pid = _grep_int(r.stdout, "PID") if loaded else None
    last_exit = _grep_int(r.stdout, "LastExitStatus") if loaded else None
    age = _heartbeat_age_s(cfg)
    if not loaded:
        verdict = "not installed"
    elif age is None:
        verdict = "loaded but no heartbeat yet"
    elif age < 3 * interval:
        verdict = "alive"
    else:
        verdict = f"loaded but stale (last heartbeat {int(age)}s ago)"
    return {"loaded": loaded, "pid": pid, "last_exit": last_exit, "heartbeat_age_s": age, "verdict": verdict}

def stop(cfg: Config, *, remove: bool = False) -> dict:
    """Unload the agent, then CONFIRM the real outcome (W10) instead of hardcoding success: the agent is
    stopped iff `launchctl list` no longer finds it (rc!=0). Idempotent — booting out an already-stopped
    label returns rc!=0, but the list confirm still reports stopped because the label isn't loaded ('already
    stopped' is not an error). The honest part: if an unload genuinely FAILED and the agent is STILL loaded,
    stopped is now False rather than a false True. Leaves the plist/wrapper on disk unless remove=True."""
    _require_darwin()
    uid = os.getuid()
    r = _launchctl("bootout", f"gui/{uid}/{LABEL}")
    if r.returncode != 0:
        _launchctl("unload", "-w", str(plist_path()))    # fallback for older macOS (already-stopped is fine)
    stopped = _launchctl("list", LABEL).returncode != 0  # source of truth: not loaded -> stopped
    out = {"label": LABEL, "plist": str(plist_path()), "wrapper": str(wrapper_path(cfg)), "stopped": stopped}
    if remove:
        for f in (plist_path(), wrapper_path(cfg)):
            try: f.unlink()
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
    short or unparseable file. Depends ONLY on the line's leading ISO timestamp column (log.py:14),
    never on the kv tail — so logger internals can change without breaking liveness."""
    p = cfg.log_path
    if not p.exists():
        return None
    try:
        last = None
        for line in p.read_text().splitlines():
            if "\theartbeat\t" in line:
                last = line
    except OSError:
        return None
    if last is None:
        return None
    try:
        ts = datetime.fromisoformat(last.split("\t", 1)[0])
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()
