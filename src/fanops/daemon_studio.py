"""Studio launchd KeepAlive resident — plist render/install/stop and port/fingerprint probes.

Extracted from daemon.py (SA-C8-6); re-exported via fanops.daemon for stable imports."""
from __future__ import annotations
import http.client
import json
import logging
import os
import plistlib
import secrets
import socket
import sys
from pathlib import Path

from fanops.config import Config
from fanops.daemon import (
    _MIN_INTERVAL,
    _VERDICT_UNLOADED_ALARM,
    _daemon_path,
    _fanops_bin,
    _grep_int,
    _launchctl,
    _load_plist,
    _require_darwin,
    sibling_plist_path,
)

_log = logging.getLogger(__name__)

STUDIO_LABEL = "com.fanops.studio"
STUDIO_DEFAULT_HOST = "127.0.0.1"
STUDIO_DEFAULT_PORT = 8787
_STUDIO_PORT_TRIES = 60         # confirm the CYCLED Studio answers again (~2 min at 2s)
_STUDIO_PORT_STEP = 2.0
_STUDIO_LAUNCH_CMD = f"fanops studio --managed --host {STUDIO_DEFAULT_HOST} --port {STUDIO_DEFAULT_PORT}"


def studio_plist_path() -> Path:
    return sibling_plist_path(STUDIO_LABEL)


def render_studio_plist(cfg: Config, *, host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT,
                        generation: str | None = None) -> str:
    """KeepAlive resident for the localhost Studio cockpit — direct `fanops studio` exec (keeper-style, no bash wrapper)."""
    fb, path = _fanops_bin(), _daemon_path()
    env = {"PATH": path, "HOME": str(Path.home())}
    if generation:
        env["FANOPS_STUDIO_GENERATION"] = generation
    pl = {
        "Label": STUDIO_LABEL,
        "ProgramArguments": [
            fb,
            "studio",
            "--managed",
            "--host",
            host,
            "--port",
            str(port),
        ],
        "KeepAlive": {"SuccessfulExit": False},
        "RunAtLoad": True,
        "WorkingDirectory": str(cfg.root),
        "StandardOutPath": str(cfg.reports / "studio.out"),
        "StandardErrorPath": str(cfg.reports / "studio.err"),
        "ThrottleInterval": _MIN_INTERVAL,
        "LSMultipleInstancesProhibited": True,
        "EnvironmentVariables": env,
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
    except Exception as exc:                             # launchctl blip -> report not-loaded (fail-open)
        _log.warning("studio_agent_status: launchctl list %s failed (%s)", STUDIO_LABEL, exc)
        loaded, pid = False, None
    if not installed:
        verdict = "not installed"
    elif not loaded:
        verdict = _VERDICT_UNLOADED_ALARM
    else:
        verdict = "loaded"
    return {"label": STUDIO_LABEL, "short": "Studio", "installed": installed, "loaded": loaded, "pid": pid,
            "verdict": verdict, "alarm": installed and not loaded}


def install_studio(cfg: Config, *, host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT,
                   generation: str | None = None, wait: bool = False) -> dict:
    """Write the Studio KeepAlive plist and load via launchctl. Idempotent: bootout any prior copy first.
    MOL-728: mint a `generation` nonce into the plist so the resident can prove it is the process launchd
    just started, not a survivor.

    `wait` picks whether we then BLOCK to confirm it came up — same seam as _redeploy_studio(wait=...).
    Default False, because installing and verifying are different jobs: fused, every caller paid a ~2min
    port poll whether it wanted the answer or not, which is a 120s hang for anything that only needed the
    plist written. `fanops studio --install` passes wait=True; it is the one caller that reports a verdict
    to a human standing there."""
    _require_darwin()
    cfg.reports.mkdir(parents=True, exist_ok=True)

    # 1. Own the generation invariant (MOL-728)
    if generation is None:
        generation = secrets.token_hex(16)

    # 2. Render and write new plist
    pp = studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    from fanops.controlio import write_text_atomic
    write_text_atomic(pp, render_studio_plist(cfg, host=host, port=port, generation=generation))

    # 3. Capture old PID — AFTER the write, so a failed/interrupted write never reaches launchctl at all.
    # (studio_agent_status shells `launchctl list`; reading it is harmless, but install_studio is fail-CLOSED
    # and its contract is that a write failure touches launchctl zero times. Ordering, not severity.)
    old_pid = studio_agent_status().get("pid")

    # 4. Load (bootout + bootstrap)
    loaded = _load_plist(pp, STUDIO_LABEL)
    if not loaded:
        return {"studio_loaded": False, "studio_plist": str(pp), "error": "failed to load plist"}

    # 5. Verify replacement and freshness — only when the caller asked to wait for it. Without `wait`,
    # `studio_loaded` means what it says: launchd accepted the job. `verdict` is None to say so, rather
    # than reporting an unproven True.
    if wait:
        from fanops import daemon
        loaded = daemon._studio_port_answers_within(host, port, expect_gen=generation, old_pid=old_pid)

    return {
        "studio_loaded": loaded,
        "studio_plist": str(pp),
        "host": host,
        "port": port,
        "generation": generation,
        "old_pid": old_pid
    }


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


def _studio_port_answers(host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT) -> bool:
    """True iff something is ACCEPTING on the Studio port (liveness, not launchd registration —
    mirrors cli._studio_port_busy). A refused connect is the expected negative, not an error."""
    try:
        with socket.create_connection((host or STUDIO_DEFAULT_HOST, port), timeout=1.0):
            return True
    except OSError:
        return False


def _studio_port_answers_within(host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT, *,
                                tries: int = _STUDIO_PORT_TRIES,
                                step: float = _STUDIO_PORT_STEP,
                                expect_sha: str | None = None,
                                expect_gen: str | None = None,
                                old_pid: int | None = None) -> bool:
    """Poll the Studio port until it ACCEPTS, bounded — the post-RESTART form of _studio_port_answers.
    MOL-728 verifies replacement (new PID != old_pid) and freshness (sha + generation) on top.

    LIVENESS and FRESHNESS are polled SEPARATELY. Fused in one loop they broke three ways: a healthy
    resident whose /_fingerprint was unreachable could never return True, so `fanops up` called a
    SERVING cockpit DOWN and burned the full ~2min budget doing it; the port probe kept firing long
    after the port was up, because the loop was really waiting on the fingerprint; and any test of the
    polling had to stub the fingerprint to get past it.

    An unreachable endpoint is absence of evidence, not evidence of stale code — it does NOT fail here.
    A REACHABLE endpoint that disagrees is evidence, and it does."""
    from fanops import daemon
    for _ in range(max(1, tries)):
        if daemon._studio_port_answers(host, port):
            break
        daemon.time.sleep(step)
    else:
        return False                             # never came back inside the budget
    if expect_sha is None and expect_gen is None and old_pid is None:
        return True                              # nothing was claimed, so nothing to disprove
    # ONE read, no retry loop: kickstart -k SIGKILLs the old resident, so whatever is accepting on the
    # port is already the new process, and create_app registers every route before app.run binds.
    fp = daemon._studio_get_fingerprint(host, port)
    if fp is None:
        return True                              # unreachable -> unproven, NOT failed
    return ((expect_sha is None or fp.get("sha") == expect_sha)
            and (expect_gen is None or fp.get("generation") == expect_gen)
            and (old_pid is None or fp.get("pid") != old_pid))


def _studio_get_fingerprint(host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT) -> dict | None:
    """MOL-728: probe the resident's /_fingerprint endpoint. Returns the JSON payload or None on error."""
    from fanops.errors import fail_open
    conn = http.client.HTTPConnection(host or STUDIO_DEFAULT_HOST, port, timeout=2.0)
    try:
        # DEBUG, not warning: _studio_port_answers_within polls this while the resident is still
        # booting, so connection-refused here is the EXPECTED steady state, not an incident.
        with fail_open("daemon._studio_get_fingerprint", log=logging.getLogger("fanops.daemon").debug):
            conn.request("GET", "/_fingerprint")
            resp = conn.getresponse()
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode())
    finally:
        conn.close()
    return None


def _studio_fingerprint_matches(expected: str, host: str = STUDIO_DEFAULT_HOST, port: int = STUDIO_DEFAULT_PORT) -> bool:
    """Thin compatibility alias for _redeploy_studio."""
    fp = _studio_get_fingerprint(host, port)
    return bool(fp and fp.get("sha") == expected)
