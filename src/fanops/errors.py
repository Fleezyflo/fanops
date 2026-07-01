# src/fanops/errors.py
"""Typed errors the CLI can catch to print one clean line instead of a traceback."""
from __future__ import annotations


class ControlFileError(Exception):
    """A control file under 00_control/ (ledger.json, accounts.json) is unreadable —
    malformed JSON or schema-violating content. Message is operator-facing and one-line:
    e.g. 'ledger.json invalid: Expecting property name enclosed in double quotes'."""


class LockBusyError(Exception):
    """The ledger lock is held by another LIVE fanops process (overlapping cron) and did not
    free within the timeout. Operator-facing, one-line. Distinct from a *stale* lock, which the
    flock-based lock self-heals automatically (the kernel releases an flock on process death),
    so this only ever means genuine contention — never an orphan needing manual `rm`."""


class StageBusyError(Exception):
    """A per-stage producer lock (transcribe / framing / keyframes) is held by another LIVE fanops
    process for the SAME (stage, source_id) and did not free within the timeout. Distinct from
    LockBusyError — that's the ledger lock; this is the SLOW-SUBPROCESS lock that closes the
    'two whisper subprocesses on the same audio' race. Operator-facing, one-line. Self-healing
    like LockBusyError: orphan lockfiles from a kill -9'd producer are inert (the kernel releases
    the flock on process death). The point is the bad path becomes unconstructable — a second
    producer entering the same (stage, key) waits inside the lock, finds the artifact on disk the
    first producer wrote, and short-circuits, never spawning a duplicate subprocess."""


class AuthError(Exception):
    """Base class for a FATAL poster auth/credential failure (bad/missing key, HTTP 401). The
    publish loop halts the WHOLE queue by TYPE on this (every post fails on a bad key — grinding
    through is pointless, FIX F52). Backend-specific subclasses (PostizAuthError, ZernioAuthError)
    carry the right operator message; the halt + CLI-exit logic catch the base so a new backend's
    auth failure halts identically without touching every call site."""


class PostizAuthError(AuthError):
    """A Postiz authentication failure (bad/missing POSTIZ_API_KEY, HTTP 401) from the free
    self-hosted poster backend. Same fatal semantics as the AuthError base (halt the queue by type),
    different operator message (check POSTIZ_API_KEY). Body WITHHELD in the message to avoid leaking
    the key into stdout/ledger/run.log."""


class ZernioAuthError(AuthError):
    """A Zernio authentication failure (bad/missing ZERNIO_API_KEY, HTTP 401) from the hosted Zernio
    scheduler backend (publishes TikTok without the operator passing TikTok app review — Zernio owns the
    app). Same fatal semantics as the others (halt the queue by TYPE), different operator message (check
    ZERNIO_API_KEY). Body WITHHELD so the Bearer key never lands in stdout/ledger/run.log."""


class ToolchainMissingError(Exception):
    """A required media binary (ffprobe/ffmpeg/whisper) is absent from PATH at a point where the
    work CANNOT be deferred to a per-unit error state — specifically ingest (`ingest_drops` runs
    OUTSIDE the pipeline's per-unit quarantine, before any Source exists to mark `error`). Treated
    as an operator-facing config error (install ffmpeg), one-line, like ControlFileError: `cli.main`
    catches it -> clean exit 2, never a raw traceback. Distinct from the ffmpeg/whisper-absent case
    DOWNSTREAM of ingest (render_moment/transcribe_source), which CAN record ClipState.error /
    SourceState.error and leave the unit retriable — those do NOT raise this. Skipping the drop
    instead of raising would be WORSE (it silently drops a real video and never retries)."""


class DownloadError(Exception):
    """yt-dlp RAN but exited non-zero (dead/geoblocked/format-gone URL, network refusal). Distinct
    from ToolchainMissingError (binary absent from PATH) and subprocess.TimeoutExpired (hung past the
    bound) — here the tool started, failed, and printed a reason on stderr. Raised by ingest's
    download_url so `fanops pull` surfaces ONE operator-actionable line + exit 2 instead of silently
    ingesting an empty inbox and reporting 'pulled -> 0 sources' as if it succeeded (the discarded
    returncode was an audit silent-failure finding). Message carries the stderr tail, truncated."""


class CutoverError(Exception):
    """An operator-facing refusal or failure in the live-cutover validation harness (cutover.py):
    a missing key, a dryrun backend where the live path is required, a missing confirm flag, a
    non-2xx POST, or a not-yet-available metrics row. One-line, cli.main-caught -> exit 2, like
    ControlFileError. It is NOT a pipeline error (cutover never touches the ledger or the unit
    chain) — it only ever means the operator's manual go-live probe needs a different input."""


def redact(text: "str | None", *secrets: "str | None", limit: int = 200) -> str:
    """Scrub secret values (API keys) out of an external response body BEFORE it lands in a ledger
    error_reason / stderr / run.log, THEN truncate. The 401 paths already WITHHOLD the body entirely;
    this defends the NON-401 echoes — a 5xx/429/4xx debug or WAF page can reflect the presented key
    (stage-5 audit follow-up). Redact-then-truncate so a key straddling the cut is still scrubbed."""
    out = text or ""
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out[:limit]


def reason(exc: Exception) -> str:
    """Condense a parse/validation error into one operator-readable line.
    json.JSONDecodeError already stringifies tidily; pydantic's ValidationError is
    multi-line and noisy, so we summarize it as 'N validation error(s): <first loc> — <first msg>'."""
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        errs = exc.errors()
        head = errs[0] if errs else {}
        loc = ".".join(str(x) for x in head.get("loc", ())) or "?"
        return f"{len(errs)} validation error(s): {loc} — {head.get('msg', exc)}"
    return str(exc)
