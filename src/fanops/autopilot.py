"""`fanops autopilot` — the one-command "make me autonomous". Collapses the tribal knowledge of
"set FANOPS_RESPONDER=llm, then supervise the run" into a single, gated verb so the operator never
hand-answers a caption/moment gate again:

  1. Enable the `llm` responder DURABLY by writing FANOPS_RESPONDER=llm to .env (idempotent, every
     OTHER key/secret preserved) — so every run (manual or daemon) answers its own gates via the
     operator's `claude` login.
  2. Install the supervising launchd daemon (the unattended loop) unless --no-daemon; off-darwin it
     is skipped with a note, never a crash (the rest still applies).
  3. Return a readiness report (reused from `doctor`) so the operator sees what — if anything — is
     left for go-live.

dryrun by default (produces fully-scheduled posts, publishes NOTHING). Going live is a
SEPARATE, deliberate step via Postiz (self-hosted) or the manual publish-queue — never assumed here.
This module enables autonomy of the per-clip WORK; it never publishes and never edits the ledger."""
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
    os.replace(tmp, env_path)


def autopilot(cfg: Config, *, interval: int, install_daemon: bool = True) -> dict:
    """Make FanOps autonomous: persist the llm responder, optionally install the daemon, and return a
    readiness dict {responder, backend, checks, notes, daemon, daemon_note}. Off-darwin (or with
    install_daemon=False) the daemon is skipped — llm is still enabled. Never publishes (dryrun-safe)."""
    set_env_var(cfg.root / ".env", "FANOPS_RESPONDER", "llm")
    os.environ["FANOPS_RESPONDER"] = "llm"               # make THIS process autonomous too (and the report below)

    from fanops.doctor import doctor_report
    report = doctor_report(cfg)

    daemon_res = None
    daemon_note = None
    if install_daemon:
        from fanops import daemon
        try:
            daemon_res = daemon.install(cfg, interval=interval, responder="llm")
        except RuntimeError as e:                        # non-darwin: enable llm but skip the launchd agent
            daemon_note = str(e)
    else:
        daemon_note = "daemon install skipped (--no-daemon)"

    return {
        "responder": "llm",
        # UI-LIE-FIX: per-channel truth (M3), not the legacy global. The autopilot summary is shown
        # to the operator; lying about the publish mode here was the same bug as the Studio status.
        "backend": cfg.effective_publish_mode(),
        "checks": report["checks"],
        "notes": report["notes"],
        "daemon": daemon_res,
        "daemon_note": daemon_note,
    }
