"""Host-level poll-timer siblings and the daemon keeper — plist render/install/status.

Extracted from daemon.py (SA-C8-7); re-exported via fanops.daemon for stable imports."""
from __future__ import annotations
import logging
import plistlib
import sys
from pathlib import Path

from fanops.config import Config

_log = logging.getLogger(__name__)

KEEPER_LABEL = "com.fanops.keeper"
KEEPER_POLL_INTERVAL_S = 120

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
    from fanops import daemon
    fb, path = daemon._fanops_bin(), daemon._daemon_path()
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
    from fanops import daemon
    kp = keeper_plist_path()
    kp.parent.mkdir(parents=True, exist_ok=True)
    from fanops.controlio import write_text_atomic
    write_text_atomic(kp, render_keeper_plist(cfg))
    return {"keeper_loaded": daemon._load_plist(kp, KEEPER_LABEL), "keeper_plist": str(kp)}


def ensure_keeper_loaded(cfg: Config) -> bool:
    """Re-bootstrap the keeper if its plist is on disk but launchd has dropped it.

    The keeper cannot heal itself: it is the thing that is unloaded. The pump (KeepAlive resident)
    calls this each loop tick; `ensure()` also calls it so a still-firing keeper is a no-op."""
    if sys.platform != "darwin":
        return False
    from fanops import daemon
    kp = keeper_plist_path()
    if not kp.exists():
        return False
    if daemon._confirm_loaded(KEEPER_LABEL):
        return True
    return daemon._load_plist(kp, KEEPER_LABEL)


def sibling_agent_status(label: str, *, short: str = "", poll_interval_s: int | None = None) -> dict:
    """Readiness for one host-level poll-timer sibling. plist-on-disk + not-loaded = ALARM."""
    from fanops import daemon
    if poll_interval_s is None:
        for spec in SIBLING_POLL_AGENTS:
            if spec["label"] == label:
                poll_interval_s = int(spec.get("poll_interval_s", SIBLING_POLL_INTERVAL_S))
                break
    installed = sibling_plist_path(label).exists()
    try:
        # `print gui/UID/label` is the loaded probe (`_confirm_loaded`). `list label` is PID-only —
        # a StartInterval job is loaded-and-idle with no PID, and list has been observed to miss it.
        loaded = daemon._confirm_loaded(label)
        pid = None
        if loaded:
            r = daemon._launchctl("list", label)
            pid = daemon._grep_int(r.stdout, "PID") if r.returncode == 0 else None
    except Exception as exc:                             # launchctl blip -> report not-loaded (fail-open)
        _log.warning("sibling_agent_status: launchctl probe %s failed (%s)", label, exc)
        loaded, pid = False, None
    if not installed:
        verdict = "not installed"
    elif not loaded:
        verdict = daemon._VERDICT_UNLOADED_ALARM
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
        except Exception as exc:                         # one sibling's probe failing must not sink the rest (fail-open)
            _log.warning("sibling_agents_status: %s status failed (%s)", spec.get("label"), exc)
            out.append({"label": spec["label"], "short": spec["short"], "installed": False, "loaded": False,
                        "verdict": "unknown", "poll_interval_s": int(iv), "alarm": False})
    return out
