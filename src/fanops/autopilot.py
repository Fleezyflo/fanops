"""`fanops autopilot` — the one-command "make me autonomous". Gates are always answered by the LLM (the
manual responder was retired), so autopilot no longer flips an AI switch; it wires up unattended
operation and reports readiness:

  1. Install the supervising launchd daemon (the unattended loop) unless --no-daemon; off-darwin it
     is skipped with a note, never a crash (the rest still applies).
  2. Return a readiness report (reused from `doctor`) so the operator sees what — if anything — is
     left for go-live (including that the LLM CLI is present on PATH).

dryrun by default (produces fully-scheduled posts, publishes NOTHING). Going live is a
SEPARATE, deliberate step via Postiz (self-hosted) or the manual publish-queue — never assumed here.
This module wires autonomy of the per-clip WORK; it never publishes and never edits the ledger."""
from __future__ import annotations
import os
from pathlib import Path
from fanops.config import Config


def set_env_var(env_path: Path, key: str, value: str) -> None:
    """Idempotently set `KEY=value` in a .env file, PRESERVING every other line (the file may hold
    secrets like POSTIZ_API_KEY). Updates an existing assignment in place (tolerating `KEY = value`
    spacing AND a dotenv `export KEY=value` prefix, which it keeps; ignoring a commented `# KEY=...`);
    appends if absent; creates the file if missing. NB: line endings are normalized to `\\n`.
    A value containing a newline is REJECTED (ValueError) — it would inject an arbitrary KEY=VALUE
    line and could silently overwrite an adjacent secret. The write is ATOMIC (temp + os.replace) so
    a crash mid-write never truncates the secrets-bearing .env (ecc audit: security + python)."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"set_env_var: value for {key!r} contains a newline — rejected (would corrupt .env)")
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    out: list[str] = []
    found = False
    for ln in lines:
        stripped = ln.lstrip()
        raw_key = ln.split("=", 1)[0].strip()
        had_export = raw_key.startswith("export ")          # dotenv allows `export KEY=value`
        bare_key = raw_key[len("export "):].strip() if had_export else raw_key
        if stripped and not stripped.startswith("#") and bare_key == key:
            out.append(f"{'export ' if had_export else ''}{key}={value}"); found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    try: os.chmod(tmp, 0o600)                            # owner-only at rest (audit): .env may hold config + residual secrets
    except OSError: pass
    os.replace(tmp, env_path)                            # atomic: never a half-written .env (mirrors the atomic accounts.json write)


def unset_env_var(env_path: Path, key: str) -> None:
    """Remove `KEY=...` from a .env file, preserving every other line. No-op when the file or key
    is absent. Atomic write (temp + os.replace), mirroring set_env_var."""
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    out: list[str] = []
    for ln in lines:
        stripped = ln.lstrip()
        raw_key = ln.split("=", 1)[0].strip()
        had_export = raw_key.startswith("export ")
        bare_key = raw_key[len("export "):].strip() if had_export else raw_key
        if stripped and not stripped.startswith("#") and bare_key == key:
            continue
        out.append(ln)
    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""))
    try: os.chmod(tmp, 0o600)                            # owner-only at rest (audit): mirror set_env_var
    except OSError: pass
    os.replace(tmp, env_path)


def autopilot(cfg: Config, *, interval: int, install_daemon: bool = True) -> dict:
    """Make FanOps autonomous: optionally install the supervising daemon and return a readiness dict
    {responder, backend, checks, notes, daemon, daemon_note}. Gates are always answered by the LLM, so
    there is no AI switch to flip — `responder` is REPORTED (cfg.responder_mode, validate-or-refuse),
    never written. Off-darwin (or with install_daemon=False) the daemon is skipped. Never publishes."""
    from fanops.doctor import doctor_report
    report = doctor_report(cfg)

    daemon_res = None
    daemon_note = None
    if install_daemon:
        from fanops import daemon
        try:
            daemon_res = daemon.install(cfg, interval=interval)
        except RuntimeError as e:                        # non-darwin: skip the launchd agent, the rest still applies
            daemon_note = str(e)
    else:
        daemon_note = "daemon install skipped (--no-daemon)"

    return {
        "responder": cfg.responder_mode,                 # always 'llm' (validate-or-refuse); reported, never written
        # UI-LIE-FIX: per-channel truth (M3), not the legacy global. The autopilot summary is shown
        # to the operator; lying about the publish mode here was the same bug as the Studio status.
        "backend": cfg.effective_publish_mode(),
        "checks": report["checks"],
        "notes": report["notes"],
        "daemon": daemon_res,
        "daemon_note": daemon_note,
    }
