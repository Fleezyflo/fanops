"""`fanops doctor` (Phase 3b) — a READ-ONLY first-run health screen. Composes the guards that
already exist (Accounts.validate, the cutover-safety preflight, toolchain presence) into ONE
operator view: PASS/FAIL per item with the exact next action, plus informational notes. It performs
NOTHING — it cannot create platform accounts or obtain a poster API key (the irreducibly-manual setup
steps), so usability for a brand-new operator is capped here by reality, not code; doctor just makes
'what's left' legible instead of buried in the source."""
from __future__ import annotations
import logging
import shutil
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.personas import Personas
from fanops.validation_gate import learning_validated
from fanops.errors import fail_open
from fanops.escalation import EscalationPosture, decide


def _check(label: str, ok: bool = True, hint: str = "", *, severity: str | None = None) -> dict:
    """Construct a doctor check. Severity is the public contract; ok is derived for migration.

    Severity is imported call-time so doctor→health_model stays lazy (layer DAG / ARCH-007).
    Callers may pass a severity string ("warn"/"fail"/…) or a Severity enum member.
    """
    from fanops.health_model import Severity
    if severity is None:
        sev = Severity.OK if ok else Severity.FAIL
    elif isinstance(severity, Severity):
        sev = severity
    else:
        sev = Severity(severity)
    derived_ok = sev not in (Severity.FAIL, Severity.UNKNOWN)
    # hint carries FAIL/UNKNOWN/WARN/INFO prose; OK keeps empty hint
    out_hint = "" if sev is Severity.OK else hint
    return {"label": label, "ok": derived_ok, "severity": sev.value, "hint": out_hint}


def _env_settings_check(cfg: Config) -> dict:
    """Strict Settings boundary — FAIL LOUD on enum/bool typos the runtime path would fail-open on."""
    from dotenv import load_dotenv
    from pydantic import ValidationError
    from fanops.settings import Settings
    load_dotenv(cfg.root / ".env", override=True)
    lbl = ".env / FANOPS_* vars valid (strict Settings)"
    try:
        Settings.strict_validate()
        return _check(lbl, True, "")
    except ValidationError as exc:
        parts: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            msg = err.get("msg", "invalid")
            parts.append(f"{loc}: {msg}" if loc else str(msg))
        hint = "; ".join(parts[:6])
        if len(parts) > 6:
            hint += f" (+{len(parts) - 6} more)"
        hint += " — fix the typo'd value in .env or your shell env"
        return _check(lbl, False, hint)


def _ig_user_id_check(cfg: Config) -> tuple[bool, str]:
    """T3: (ok, hint) for 'every active IG account resolves to its OWN ig_user_id'. Loads accounts FAIL-CLOSED
    (a torn accounts.json -> ok=False, never a silent pass). BORROWERS = active IG-carrying accounts with no
    own ig_user_id while >=2 active IG accounts exist (each falls back to the single global -> unverifiable
    against its own media). DUPES = two active handles sharing the SAME non-None id. A single active IG on the
    global is legitimate (not flagged). Reads only accounts.json (the OWN id) -- not resolve_meta_creds -- so
    the global env id is never treated as an account's own; that separation is the demote-to-bootstrap point."""
    from fanops.accounts import Accounts
    from fanops.models import Platform
    try:
        active_ig = [a for a in Accounts.load(cfg).active() if Platform.instagram in a.platforms]
    except Exception as e:                                # corrupt/unreadable accounts.json -> fail CLOSED (unknown != pass)
        if decide("operator", 0) is EscalationPosture.nonzero:
            logging.getLogger("fanops.doctor").debug("ig_user_id accounts read failed: %s", e)
            return False, f"accounts.json unreadable -- cannot verify per-account ig_user_id ({str(e)[:120]}); fix it in the Studio Go-Live tab"
        raise
    own = {a.handle: ((a.ig_user_id or "").strip() or None) for a in active_ig}
    borrowers = [h for h, i in own.items() if i is None]
    borrow_bad = borrowers if len(active_ig) >= 2 else []   # a lone active IG on the global is fine; borrow only harms once >=2 share one id
    dupes: list[str] = []; seen: dict[str, str] = {}
    for h, i in own.items():
        if i is None: continue
        if i in seen: dupes.extend([seen[i], h])         # both handles that collide on this id
        else: seen[i] = h
    dupes = sorted(set(dupes))
    if not borrow_bad and not dupes:
        return True, ""
    parts = []
    if borrow_bad:
        parts.append("set a per-account ig_user_id for: " + ", ".join(sorted(borrow_bad))
                     + " (they fall back to the single global META_IG_USER_ID and can't be verified against their own media)")
    if dupes:
        parts.append("duplicate ig_user_id shared by: " + ", ".join(dupes) + " (each active handle needs a distinct IG Business id)")
    return False, "; ".join(parts) + " -- set it per account in the Studio Go-Live tab (accounts.json ig_user_id)"


_META_TOKEN_LEAD_DAYS = 10                                # WARN this many days before a Meta token expires



def _hashtag_scrape_check(cfg: Config, *, open_client=None, probe_resolve=None) -> dict | None:
    """Hashtag Layer A needs a LIVE instagrapi scrape session that can RESOLVE a tag.

    Soft setup incompleteness (not configured / no session yet) is N/A — omit the check
    (MOL-965: never ok=True pretend PASS). A session that fails login OR whose hashtag_info
    returns login_required fails LOUD (MOL-687 / MOL-696). Never echoes password or session contents.
    Multi-account (MOL-857): omit when no listed user has a session yet; probe via open_client
    when any session exists. `open_client` / `probe_resolve` injectable so tests never hit Instagram."""
    from fanops.ig_hashtag_scrape import (ScrapeUnavailable, any_scrape_session, scrape_configured)
    lbl = "hashtag Layer A scrape session live (instagrapi)"
    if not scrape_configured(cfg):
        return None  # N/A — setup incomplete, not a green PASS
    # Password alone is setup-in-progress; the expiry bug is a SESSION that LOOKS fine but is dead.
    if not any_scrape_session(cfg):
        return None  # N/A — credentials without session is setup incompleteness
    # Multi-account: platform stop on the preferred user must arm cooldown (same ledger Layer A
    # uses) then retry open_client once so a healthy peer can PASS. ScrapeUnavailable on the first
    # open stays the setup failure; after a platform fail, a later ScrapeUnavailable means peers
    # are exhausted — report the platform text, not a scrape-login prescription (MOL-879).
    opener = open_client
    if opener is None:
        from fanops.ig_hashtag_scrape import open_client as opener
    probe = probe_resolve
    if probe is None:
        from fanops.ig_hashtag_scrape import resolve_hashtag_scrape as probe
    from datetime import datetime, timezone
    from fanops.fanops_hashtags import _freeze_for, _persist_cooldown
    now = datetime.now(timezone.utc)
    last_user, last_exc = "", None
    try:
        for _ in range(2):
            user = ""
            try:
                client = opener(cfg)
            except ScrapeUnavailable:
                if last_exc is not None:
                    break
                raise
            except Exception as e:                          # noqa: BLE001 — open raised platform error
                if decide("observability", 0) is EscalationPosture.degrade:
                    last_exc = e
                    break
                raise
            user = getattr(client, "_fanops_scrape_user", "") or ""
            try:
                probe(client, "#hiphop")
                return _check(lbl, True, "")
            except ScrapeUnavailable:
                raise
            except Exception as e:                          # noqa: BLE001 — probe platform error
                if decide("observability", 0) is EscalationPosture.degrade:
                    last_user, last_exc = user, e
                    if not user:
                        break
                    reason, delay_s = _freeze_for(e)
                    _persist_cooldown(cfg, now, reason=reason, delay_s=delay_s, user=user)
                    continue
                raise
        return _check(lbl, False,
                      f"@{last_user}: {last_exc}" if last_user else str(last_exc))
    except ScrapeUnavailable as e:
        return _check(lbl, False, f"{e} — run `fanops hashtags scrape-login`")

def _meta_token_expiry_check(cfg: Config, *, get=None):
    """T9: build the 'Meta Graph token not expiring' check dict, or None when no Meta token is configured (the
    check is simply N/A then — never a false alarm). Introspects EVERY distinct resolvable token (global +
    per-handle) via meta_graph.debug_token_expiry: an expired OR unintrospectable (FAIL-CLOSED) token -> ok=False;
    a token inside the _META_TOKEN_LEAD_DAYS window -> Severity.WARN (surface, never block). The token value
    is NEVER read into the label/hint (only the handle label + the human expiry date). Fail-open around the
    enumeration so a torn accounts.json can't crash the report (resolvable_meta_tokens already degrades to global)."""
    from datetime import datetime, timezone
    from fanops.meta_graph import resolvable_meta_tokens, debug_token_expiry
    try:
        toks = resolvable_meta_tokens(cfg)
    except Exception as e:
        if decide("observability", 0) is EscalationPosture.degrade:
            logging.getLogger("fanops.doctor").debug("meta token enumeration failed: %s", e)
            toks = []                                    # never crash the report over enumeration
        else:
            raise
    if not toks:
        return None                                      # no Meta token to introspect -> check N/A
    now = int(datetime.now(timezone.utc).timestamp())
    lead = _META_TOKEN_LEAD_DAYS * 86400
    expired: list[str] = []; unknown: list[str] = []; soon: list[tuple[str, int]] = []
    for label, tok in toks:
        status, detail = debug_token_expiry(cfg, tok, get=get)
        if status == "expired":
            expired.append(label)
        elif status == "unknown":
            unknown.append(label)
        elif status == "ok" and isinstance(detail, int) and detail != 0 and detail - now <= lead:
            soon.append((label, detail))                 # a real future expiry inside the lead window (0 == never-expires)
    lbl = "Meta Graph token valid + not near expiry (debug_token)"
    if expired or unknown:
        parts = []
        if expired: parts.append("EXPIRED for: " + ", ".join(sorted(expired)))
        if unknown: parts.append("could not introspect (fail-closed) for: " + ", ".join(sorted(unknown)))
        hint = ("; ".join(parts) + " — mint a fresh long-lived token + set it (global META_GRAPH_TOKEN, or the "
                "per-handle META_GRAPH_TOKEN__<SLUG>) via the Studio Go-Live tab; see docs/META_CREDS_OPS.md. "
                "Postiz keeps publishing on its own OAuth while Graph verification + metrics go dark.")
        return _check(lbl, False, hint)
    if soon:
        def _fmt(e): return datetime.fromtimestamp(e, tz=timezone.utc).date().isoformat()
        who = ", ".join(f"{h} (expires {_fmt(e)})" for h, e in sorted(soon, key=lambda x: x[1]))
        hint = ("Meta token expiring within %d days: %s — rotate it now (docs/META_CREDS_OPS.md) "
                "before Graph verification + metrics go dark." % (_META_TOKEN_LEAD_DAYS, who))
        return _check(lbl, severity="warn", hint=hint)
    return _check(lbl, True, "")


def _postiz_reach_check(cfg: Config, *, probe=None):
    """Deprecated internal — use health_model.postiz_doctor_check (ONE probe). Kept as thin alias for tests."""
    from fanops.health_model import postiz_doctor_check
    return postiz_doctor_check(cfg, probe=probe)


def _zernio_reach_check(cfg: Config, *, auth=None):
    """T10: the 'Zernio backend auth ok' check dict, or None when the deployment has no Zernio key (N/A). Does
    the lightest authenticated read (zernio_check_auth -> GET /accounts): True ok; a 401 (ZernioAuthError) or
    any unreachable (False) FAILS CLOSED. Never echoes the key. `auth` is injected for tests; None -> the real
    zernio_check_auth (which lives in post/zernio.py, outside the reconcile-owned metrics.py)."""
    if not cfg.backend_has_creds("zernio"):
        return None                                          # no Zernio key -> the check is N/A
    from fanops.post.zernio import zernio_check_auth
    from fanops.errors import ZernioAuthError
    auth = auth or zernio_check_auth
    lbl = "Zernio backend auth ok (authenticated /accounts read)"
    try:
        return _check(lbl, bool(auth(cfg)), "Zernio unreachable — check ZERNIO_API_KEY / the Zernio API; see docs/POSTIZ_OPS.md.")
    except ZernioAuthError:
        return _check(lbl, False, "Zernio rejected the API key (401) — check ZERNIO_API_KEY (Studio Go-Live); see docs/POSTIZ_OPS.md.")
    except Exception as e:
        if decide("operator", 0) is EscalationPosture.nonzero:
            logging.getLogger("fanops.doctor").debug("zernio reach probe failed: %s", e)
            return _check(lbl, False, f"Zernio probe error ({str(e)[:120]}); see docs/POSTIZ_OPS.md.")
        raise


_DAEMON_STALE_TICKS = 3                                    # heartbeat older than this many install intervals == dead pump (mirrors daemon.status)
_DAEMON_DEFAULT_INTERVAL_S = 600                           # fallback tick interval when the daemon isn't installed / interval unknown
_GATE_STALE_TICKS = 3                                      # a pending agent-gate older than this many ticks is WARN-worthy (responder may be stuck)


def _daemon_liveness_check(cfg: Config, *, status_reader=None) -> dict:
    """T12: (dict) 'the publish pump is alive AND the queue is draining'. TWO fail conditions:
      (a) the last `fanops run` heartbeat in run.log is older than _DAEMON_STALE_TICKS install intervals
          (dead/stopped/crashing pump) — OR the signal is ABSENT (never ran) -> FAIL CLOSED (unknown != alive);
      (b) queued posts are PAST-DUE beyond a grace window (the pump missed cycles / can't drain) -> FAIL naming
          the count + the oldest age.
    Signal: daemon._heartbeat_age_s (the run.log heartbeat line, a LIVE-clock write each completed tick) — a
    real liveness signal, not a proxy. The past-due gate reuses timeutil.is_due_or_past, the SAME <=now check
    publish_due fires on, so 'past-due here' == 'should have published already'. A grace window (2 intervals)
    keeps a just-due post from a false backlog flag. Ledger read is fail-open: an unreadable ledger degrades the
    backlog half to 'unknown' (surfaced in the hint) while the heartbeat half still governs -> never a crash.

    `status_reader(cfg, interval) -> dict` injects launchd status (default: daemon.status). Observe-mode
    passes a snapshot reader so CP Home never re-probes launchctl (MOL-965 WP2-fix2)."""
    from datetime import datetime, timezone
    from fanops import daemon
    interval = daemon.installed_interval(cfg) or _DAEMON_DEFAULT_INTERVAL_S
    lbl = "publish daemon alive + queue draining (heartbeat + past-due backlog)"
    st = {"installed": False, "loaded": False, "verdict": "unknown", "heartbeat_age_s": None}
    reader = status_reader or (lambda c, iv: daemon.status(c, interval=iv))
    try:
        st = reader(cfg, interval)
    except Exception:
        with fail_open("doctor.daemon status read degrade:", log=logging.getLogger("fanops.doctor").debug):
            raise
    if st.get("installed") and not st.get("loaded"):
        return _check(lbl, False, f"{st['verdict']} — reload with `fanops daemon install` then `fanops daemon status`")
    if st.get("exec_fail"):
        target = st["exec_fail"].get("target", "fanops")
        return _check(lbl, False, f"daemon cannot exec fanops ({target}) — fix the venv/symlink, then `fanops daemon install`")
    age = None                                            # a read hiccup -> treat as no signal (fail-closed)
    try:
        age = st.get("heartbeat_age_s")
        if age is None:
            age = daemon._heartbeat_age_s(cfg)
    except Exception:
        with fail_open("doctor.daemon heartbeat age read degrade:", log=logging.getLogger("fanops.doctor").debug):
            raise
    # (b) past-due backlog — fail-open ledger read
    now = datetime.now(timezone.utc)
    backlog_n = 0; oldest_h = 0.0; backlog_unknown = False
    try:
        from fanops.ledger import Ledger
        from fanops.models import PostState
        from fanops.timeutil import is_due_or_past, parse_iso
        led = Ledger.load(cfg)
        grace = 2 * interval
        for p in led.posts_in_state(PostState.queued):
            if not is_due_or_past(p.scheduled_time, now):
                continue
            try:
                due_age = (now - parse_iso(p.scheduled_time)).total_seconds()
            except (ValueError, TypeError):
                due_age = grace + 1                       # unparseable due time counts as stale-past (mirrors is_due_or_past)
            if due_age > grace:
                backlog_n += 1; oldest_h = max(oldest_h, due_age / 3600.0)
    except Exception as e:
        if decide("observability", 0) is EscalationPosture.degrade:
            logging.getLogger("fanops.doctor").debug("daemon backlog read failed: %s", e)
            backlog_unknown = True
        else:
            raise
    # (a) heartbeat staleness / absence — mid-pass stage overrides stale heartbeat (shared with daemon.status)
    from fanops.health_model import daemon_progress, _STAGE_HANG_CEILING_S
    alive_mid, progress_line, snap = daemon_progress(cfg)
    stale = age is None or age > _DAEMON_STALE_TICKS * interval
    if alive_mid:
        stale = False
    ok = (not stale) and backlog_n == 0 and not backlog_unknown
    if ok:
        return _check(lbl, True, "")
    parts = []
    if age is None:
        parts.append("no daemon heartbeat in run.log — the pump has never completed a tick (or run.log is "
                     "missing). Install/start it: `fanops daemon install` then check `fanops daemon status`")
    elif stale:
        if progress_line is not None and not alive_mid:
            if snap:
                act = daemon._newest_activity_ts(cfg)         # log-SILENCE, not stage_age — the wedged signal
                silent = int((now - act).total_seconds()) if act else 0
                parts.append(f"daemon mid-pass stage stuck — {snap['stage']} log SILENT {silent}s "
                             f"(>{_STAGE_HANG_CEILING_S}s ceiling); the pump may be wedged")
            else:
                parts.append(f"daemon heartbeat is {int(age)}s old (> {_DAEMON_STALE_TICKS}x the {interval}s tick) — the "
                             f"pump looks dead/stopped; approved posts won't send. Restart it (`fanops daemon status`)")
        else:
            parts.append(f"daemon heartbeat is {int(age)}s old (> {_DAEMON_STALE_TICKS}x the {interval}s tick) — the "
                         f"pump looks dead/stopped; approved posts won't send. Restart it (`fanops daemon status`)")
    if backlog_n:
        parts.append(f"{backlog_n} queued post(s) past-due by up to {oldest_h:.1f}h — backlog is piling up "
                     f"(the pump isn't draining the queue)")
    if backlog_unknown:
        parts.append("could not read the ledger to assess past-due backlog (fail-closed)")
    return _check(lbl, False, "; ".join(parts))

def _doctor_notes(cfg: Config) -> list[str]:
    lv = learning_validated(cfg)
    notes: list[str] = []
    notes.append(f"poster backend: {cfg.poster_backend}"
                 + (" (dryrun — writes payloads, posts nothing)" if not cfg.is_live else " (LIVE)"))
    if lv:
        notes.append("learning loop: validation-confirmed (lift fields reconciled by cutover) — amplify/bandit may be enabled")
    else:
        notes.append("learning loop: NOT validation-confirmed — variant-amplify stays inert even if enabled; "
                     "run the Studio Validate learning step (Go-Live > 5 · Validate learning), or `fanops cutover`, to confirm lift fields")
    try:
        n = len(list(cfg.review.glob("*.jpg"))) if cfg.review.exists() else 0
    except OSError as e:
        logging.getLogger("fanops.doctor").debug("review glob failed: %s", e)
        n = 0
    if n:
        notes.append(f"review queue: {n} candidate(s) in 00_review/ awaiting Finder approval — "
                     "move keepers to 00_review/approved/ then `fanops intake`")
    # Approval backlog is INFO only — nothing auto-publishes, so a queue awaiting the operator is the
    # EXPECTED steady state, never a warning (the human Review gate is deliberately kept, not automated).
    try:
        from fanops.ledger import Ledger
        from fanops.models import PostState
        aw = Ledger.load(cfg).state_histogram().get(PostState.awaiting_approval, 0)
        if aw:
            notes.append(f"approval backlog: {aw} post(s) awaiting your approval in Review "
                         "(expected — nothing auto-publishes)")
    except Exception as e:
        if decide("observability", 0) is EscalationPosture.degrade:
            notes.append(f"approval backlog: could not read ({type(e).__name__})")
        else:
            raise
    # FANOPS_POSTIZ_ONDEMAND is a registered bootstrap key; missing-script note is bring-up, not a publish gate.
    import os
    from pathlib import Path
    ond = (os.getenv("FANOPS_POSTIZ_ONDEMAND") or "").strip()
    if ond and not Path(ond).expanduser().exists():
        notes.append(f"FANOPS_POSTIZ_ONDEMAND points at {ond} which does not exist — `fanops up` bring-up "
                     "cannot find the Postiz on-demand script (set it or install the script)")
    return notes


def _operational_sensor_checks(cfg: Config) -> list[dict]:
    """Operational sensors for live backlog. Progress-blocking classes emit Severity.FAIL so
    report_is_healthy / doctor exit are NONZERO (MOL-960/MOL-965). Optional Layer A scrape cooldown
    is Severity.WARN (non-blocking). Sensor read failures → Severity.UNKNOWN (required → unhealthy).
    No mutation. Severity is set at construction via _check — not a post-hoc ok+warn soft-lie."""
    from datetime import datetime, timezone
    log = logging.getLogger("fanops.doctor")
    out: list[dict] = []

    # 1. pending agent-gates — unknown age (missing opened_at) is NOT clean silence: FAIL (required unknown).
    #    Known age: surface only when the OLDEST has aged past _GATE_STALE_TICKS ticks. A fresh
    #    pending gate is normal transient work the responder is clearing, so it is not surfaced.
    try:
        from fanops import pipeline_status, daemon
        gates = pipeline_status._pending_gates(cfg)  # (opened_at_epoch|None, kind, key); unknown age first
        if gates:
            oldest_opened = gates[0][0]
            interval = daemon.installed_interval(cfg) or _DAEMON_DEFAULT_INTERVAL_S
            if oldest_opened is None:
                n_unknown = sum(1 for g in gates if g[0] is None)
                out.append(_check(
                    "no stale agent gates (responder answering)",
                    severity="unknown",
                    hint=f"gate age unknown — {n_unknown} pending agent gate(s) missing readable "
                         f"opened_at; cannot prove freshness (not clean silence)"))
            else:
                age_s = max(0.0, datetime.now(timezone.utc).timestamp() - oldest_opened)
                if age_s > _GATE_STALE_TICKS * interval:
                    out.append(_check(
                        "no stale agent gates (responder answering)",
                        severity="fail",
                        hint=f"{len(gates)} pending agent gate(s); oldest ~{int(age_s // 60)}m old "
                             f"(> {_GATE_STALE_TICKS}x the {interval}s tick) — the LLM responder may be "
                             f"stuck; check `fanops status` and that the daemon gates loop is running"))
    except Exception as e:
        if decide("operator", 0) is EscalationPosture.nonzero:
            out.append(_check(
                "pending-gate sensor readable",
                severity="unknown",
                hint=f"pending-gate sensor failed ({type(e).__name__}: {str(e)[:120]}) — "
                     f"doctor cannot prove agent gates are clear"))
        else:
            raise

    # 2-4. ledger-derived backlog: sources awaiting a gate answer, degraded/errored sources, parked re-opens.
    try:
        from fanops.ledger import Ledger
        from fanops.pipeline_status import source_backlog
        led = Ledger.load(cfg)
        bl = source_backlog(led, cfg)
        if bl.blocked_on_gates:
            out.append(_check(
                "no sources awaiting gate answers",
                severity="fail",
                hint=f"{bl.blocked_on_gates} source(s) awaiting gate answer(s) — answer in "
                     f"Studio Gates or run `fanops status`"))
        if bl.recoverable:
            out.append(_check(
                "no degraded/errored sources",
                severity="fail",
                hint=f"{bl.recoverable} source(s) in error/moments-empty — Resume/Reset in "
                     f"Studio Make or run `fanops status`"))
        parked = sum(1 for src in led.sources.values() if src.meta.get("pending_reopen"))
        if parked:
            out.append(_check(
                "no parked machine re-opens",
                severity="fail",
                hint=f"{parked} source(s) hold a parked amplify re-open (FANOPS_QUEUE_GATE) — "
                     f"release them in Studio Make"))
    except Exception as e:
        if decide("operator", 0) is EscalationPosture.nonzero:
            out.append(_check(
                "source backlog sensor readable",
                severity="unknown",
                hint=f"backlog sensor failed ({type(e).__name__}: {str(e)[:120]}) — "
                     f"doctor cannot prove sources are unblocked"))
        else:
            raise

    # 5. hashtag-scrape cooldown — optional enrichment; Severity.WARN (non-blocking). Surface WHY Layer A
    #    is frozen with the honest remedy from _OUTAGE_REMEDY when NO healthy peer remains.
    try:
        from fanops.fanops_hashtags import _read_active_cooldown, _OUTAGE_REMEDY
        cool = _read_active_cooldown(cfg, datetime.now(timezone.utc))
        if cool:
            reason = cool.get("reason") or "cooldown"
            remedy = _OUTAGE_REMEDY.get(reason, "wait for the cooldown to clear")
            until = cool.get("until") or "?"
            out.append(_check(
                "hashtag Layer A not in cooldown",
                severity="warn",
                hint=f"scrape frozen ({reason}) until {until} — {remedy}"))
    except Exception:
        with fail_open("doctor.scrape-cooldown sensor degrade:", log=log.debug):
            raise

    return out


def _assemble_doctor_checks(cfg: Config, *, get=None, postiz_probe=None, zernio_auth=None,
                            daemon_status=None, probe_policy: str = "live") -> list[dict]:
    """Setup gate checks only (deps/field-shape composed by health_model.build_health_report).

    probe_policy='observe': skip live Meta token network; postiz/zernio/daemon use injectables
    (caller supplies snapshot readers). probe_policy='live' (default): real probes."""
    checks: list[dict] = []
    checks.append(_env_settings_check(cfg))
    # 1. media toolchain (host-dependent — informational pass/fail, the operator installs what's red)
    for tool in ("ffmpeg", "ffprobe", "whisper"):
        checks.append(_check(f"{tool} on PATH", shutil.which(tool) is not None,
                             f"install {tool} (brew install ffmpeg / pip install -e '.[transcribe]')"))
    from fanops import transcribe
    checks.append(_check("faster-whisper importable ([asr] extra)", transcribe._fw_available(),
                         "python3.12 -m venv .venv && .venv/bin/pip install -e '.[asr]' — "
                         "the preferred ASR engine (Demucs + CTranslate2); without it transcribe "
                         "falls back to the legacy whisper CLI"))
    checks.append(_check("yt-dlp on PATH (only for `fanops pull <url>`)", shutil.which("yt-dlp") is not None,
                         "pip install yt-dlp"))
    # 2. gates are answered ONLY by the LLM, so the LLM CLI is ALWAYS required on PATH (mirrors preflight —
    # no longer gated on FANOPS_RESPONDER=llm, since there is no other responder). A bad FANOPS_RESPONDER
    # value is surfaced as its own failing check rather than a traceback.
    from fanops.llm import _CURSOR_SUPPORTS_VISION
    try:
        cfg.responder_mode                               # validate FANOPS_RESPONDER (empty/'llm' ok; anything else raises)
        responder_err = None
    except ValueError as e:
        responder_err = str(e)
    checks.append(_check("FANOPS_RESPONDER valid (llm-only)", responder_err is None,
                         responder_err or "leave FANOPS_RESPONDER unset, or set it to 'llm' — it has no other valid value"))
    cli_bin = cfg.llm_cli_binary
    hint = ("install Cursor CLI, or set LLM transport to claude in Studio Go-Live"
            if cli_bin == "cursor-agent"
            else "install Claude Code + run `claude login` (uses your subscription, no API key)")
    # PATH presence is NOT proof of login; no cheap non-mutating auth probe. When present, emit
    # Severity.WARN (non-blocking) so we never claim a silent authenticated PASS.
    if shutil.which(cli_bin) is not None:
        login_cmd = "cursor-agent login" if cli_bin == "cursor-agent" else "claude login"
        checks.append(_check(
            f"{cli_bin} on PATH",
            severity="warn",
            hint=(f"{cli_bin} is on PATH but that is NOT proof it is logged in — if gates "
                  f"start failing with auth errors, run `{login_cmd}`")))
    else:
        checks.append(_check(f"{cli_bin} on PATH", False, hint))
    if cfg.llm_transport == "cursor" and not _CURSOR_SUPPORTS_VISION:
        # Absolute transport: cursor cannot run vision gates — operator must flip the ONE switch.
        checks.append(_check("LLM transport can run vision gates", False,
                             "set LLM transport to claude in Studio Go-Live "
                             "(single switch; no silent claude fallback when transport=cursor)"))
    # 2b. brand brief present + non-empty. context.md is injected verbatim into every moment +
    # caption decision (the #1 output lever); its absence used to be SILENT (load_guidance now warns,
    # but a preflight is the visible gate). Read directly + safely so the report never crashes.
    try:
        brief_ok = bool(cfg.context_path.read_text().strip()) if cfg.context_path.exists() else False
    except OSError:
        brief_ok = False
    checks.append(_check("brand brief present (context.md)", brief_ok,
                         f"create {cfg.context_path} — it steers every clip/caption; without it the engine runs UNGROUNDED"))
    # 3. accounts.json valid + every active account has a numeric account_id (human step 2)
    try:
        problems = Accounts.load(cfg).validate()
    except Exception as e:                                # malformed accounts.json -> a check failure, not a crash
        if decide("operator", 0) is EscalationPosture.nonzero:
            logging.getLogger("fanops.doctor").debug("accounts.json validate failed: %s", e)
            problems = [str(e)[:160]]
        else:
            raise
    checks.append(_check("accounts.json valid (every active channel mapped to an id)", not problems,
                         "; ".join(problems) + " — add accounts + map each channel in the Studio Go-Live tab"))
    # personas.json valid — same MOL-79 surface as accounts: per-row skips promote via validate(),
    # corrupt JSON / wrong top-level shape fail the check (never a silent pass).
    try:
        persona_problems = Personas.load(cfg).validate()
    except Exception as e:
        if decide("operator", 0) is EscalationPosture.nonzero:
            logging.getLogger("fanops.doctor").debug("personas.json validate failed: %s", e)
            persona_problems = [str(e)[:160]]
        else:
            raise
    checks.append(_check("personas.json valid (malformed rows skipped loud)", not persona_problems,
                         "; ".join(persona_problems) + " — fix the row in personas.json"))
    # ECC fix #14: read cutover state ONCE here and reuse in BOTH the postiz branch and the notes
    # block below (it was read twice per doctor_report — two cutover.json reads on every call).
    lv = learning_validated(cfg)
    # 4. poster + key consistency (human step 3) — mirrors cli._check_preflight
    if cfg.backend_has_creds("postiz"):
        checks.append(_check("POSTIZ_URL + POSTIZ_API_KEY set (Postiz routed)",
                             cfg.postiz_url is not None and cfg.postiz_api_key is not None,
                             "set POSTIZ_URL (your self-hosted instance) + POSTIZ_API_KEY (Postiz "
                             "Settings > Developers > Public API) — connect in Studio Go-Live"))
        # Postiz-learning readiness (booleans only, never the key): the loop only acts once the key is set,
        # every active channel is mapped, AND cutover confirmed the lift fields. Hint names the FIRST gap.
        ready = cfg.postiz_api_key is not None and not problems and lv   # lv hoisted above (ECC fix #14)
        if cfg.postiz_api_key is None: hint = "Connect Postiz (Go-Live > 1 · Connect Postiz)"
        elif problems:                hint = "map every channel (Go-Live > 3 · Map each channel to Postiz)"
        elif not lv:                  hint = "run the Studio Validate learning step (Go-Live > 5 · Validate learning)"
        else:                         hint = ""
        checks.append(_check("Postiz learning ready (key + channels mapped + cutover validated)", ready, hint))

    # D15: live-route COHERENCE via health_model.half_live_state — ONE compute for doctor + UI (MOL-965 WP2).
    from fanops.health_model import HALF_LIVE_CHECK_LABEL, half_live_state
    hl = half_live_state(cfg)
    if cfg.is_live:
        # Always emit the check from half_live_state — same hint strip/Go-Live project.
        checks.append(_check(
            HALF_LIVE_CHECK_LABEL, not hl.is_half_live,
            hl.hint or (
                "LIVE flag set but nothing routes live — FANOPS_POSTER is a legacy bridge, not "
                "the switch. Route a channel to a provider with creds (Studio Go-Live tab), or "
                "`fanops` back to dryrun. Every publish stays stuck in `queued` until then.")))

    # Leg 2 (Insight): the ONE external gate — a persisted breadcrumb means a Graph media-insights read was
    # refused for lack of the instagram_manage_insights scope, so IG posts kept their PRIOR snapshot (fail-
    # closed, never a wrong number). Surface it LOUD with the exact unblock; self-clears once insights flow.
    from fanops.meta_graph import insights_blocked_signal
    blocked = insights_blocked_signal(cfg)
    checks.append(_check("IG insights readable (Meta Graph media insights)", not blocked,
                         "grant the instagram_manage_insights token scope — IG performance (reach/retention) "
                         "is frozen at its last snapshot until then; identification still works on instagram_basic"))

    # T3: per-account ig_user_id required for ACTIVE IG accounts (the shared/borrowed-id root bug). With ≥2
    # active IG accounts, one lacking its OWN ig_user_id silently BORROWS the global META_IG_USER_ID (another
    # handle's id) -> it can never be verified against its own media, and its insights attribute to the wrong
    # account. FAIL naming every borrower + any two handles resolving to the SAME non-None id. A single active
    # IG account legitimately using the global is fine. FAIL CLOSED: a corrupt/unreadable accounts.json is
    # reported failing (unknown != silent pass) — the whole point is that this class of drift stays LOUD.
    ig_ok, ig_hint = _ig_user_id_check(cfg)
    checks.append(_check("active IG accounts have their OWN ig_user_id (no shared/borrowed Meta id)", ig_ok, ig_hint))

    # Hashtag Layer A scrape session (instagrapi) — omit when setup incomplete (N/A, not green PASS).
    htag = _hashtag_scrape_check(cfg)
    if htag is not None:
        checks.append(htag)

    # T9: Meta token expiry — live network only. Observe-mode skips (CP uses snapshots; no Graph re-probe).
    if probe_policy != "observe":
        tcheck = _meta_token_expiry_check(cfg, get=get)
        if tcheck is not None:
            checks.append(tcheck)

    # T10: REAL backend reachability on the publish path (the operator-confirms-health step, as code). Postiz's
    # docker health-check is nginx-only and LIES while the Node backend crash-loops (mastra_ai_spans) — the real
    # probe (GET /integrations, postiz_health_probe) goes PAST nginx. Zernio auth can lapse the same way. Each
    # check is applicable ONLY when that backend has creds (else it is N/A — never a false alarm on a single-
    # backend deployment), FAILS CLOSED on a down/unauthorized/erroring probe, and NEVER echoes a key. The
    # probes are injected so tests never hit the network; None -> the real client.
    # Observe: caller must inject snapshot probes (build_health_report does); never default to live.
    pcheck = _postiz_reach_check(cfg, probe=postiz_probe)
    if pcheck is not None:
        checks.append(pcheck)
    if probe_policy == "observe" and zernio_auth is None:
        zcheck = None  # no live Zernio on observe unless injectable supplied
    else:
        zcheck = _zernio_reach_check(cfg, auth=zernio_auth)
    if zcheck is not None:
        checks.append(zcheck)

    # T12: PERMANENT daemon-liveness + past-due-backlog sensor. The launchd daemon runs `fanops run` ticks
    # that call publish_due; if that pump is dead/stopped, approved posts silently never send (27-queued-stuck
    # incident). FAIL when the last heartbeat is stale (dead pump) OR queued posts are past-due beyond a grace
    # window (backlog piling up). FAIL CLOSED: no heartbeat signal at all -> FAIL (unknown != healthy). Signal
    # is REAL, not a proxy: daemon._heartbeat_age_s reads the `heartbeat` line `fanops run` writes to run.log
    # every completed tick (a live clock -> a frozen age means the cron is dead / the tick crashes before the
    # heartbeat). The past-due gate mirrors the pump's own due-check (timeutil.is_due_or_past).
    # Observe: status_reader from snapshot (no daemon.status / launchctl).
    dchk = _daemon_liveness_check(cfg, status_reader=daemon_status)
    checks.append(dchk)
    # Operational sensors: progress-blocking backlog → ok=False (doctor exit NONZERO); Layer A cooldown
    # stays warn-only. See _operational_sensor_checks.
    checks.extend(_operational_sensor_checks(cfg))
    checks.extend(_sibling_launchd_checks())
    schk = _studio_resident_check()
    if schk is not None:
        checks.append(schk)
    return checks


def _studio_resident_check() -> dict | None:
    """KeepAlive Studio resident: plist-on-disk + not-loaded alarm. Omitted when never installed."""
    from fanops import daemon
    st = daemon.studio_agent_status()
    if not st.get("installed"):
        return None
    lbl = "launchd Studio resident loaded (KeepAlive, localhost cockpit)"
    if st.get("alarm"):
        return _check(lbl, False, f"{st['verdict']} — reload with `fanops studio --install`")
    return _check(lbl, True, "")


def _sibling_launchd_checks() -> list[dict]:
    """M2-D: poll-timer siblings share M2-C's plist-on-disk + not-loaded alarm. N/A when not installed."""
    from fanops import daemon
    checks: list[dict] = []
    for sib in daemon.sibling_agents_status():
        if not sib.get("installed"):
            continue
        lbl = (f"launchd sibling {sib['short']} loaded "
               f"(StartInterval {daemon.SIBLING_POLL_INTERVAL_S}s poll-timer)")
        if sib.get("alarm"):
            checks.append(_check(lbl, False,
                                 f"{sib['verdict']} — reload ~/Library/LaunchAgents/{sib['label']}.plist "
                                 f"then `launchctl bootstrap gui/$UID` that plist"))
        else:
            checks.append(_check(lbl, True, ""))
    return checks



class SetupState:
    """MOL-302: derived setup position — never persisted, recomputed on every read."""
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    CONNECTED = "CONNECTED"
    VALIDATED = "VALIDATED"
    LIVE = "LIVE"


def _brief_ok(cfg: Config) -> bool:
    try:
        return bool(cfg.context_path.read_text().strip()) if cfg.context_path.exists() else False
    except OSError:
        return False


def _accounts_problems(cfg: Config) -> list[str]:
    try:
        return Accounts.load(cfg).validate()
    except Exception as e:
        if decide("operator", 0) is EscalationPosture.nonzero:
            logging.getLogger("fanops.doctor").debug("accounts problems read failed: %s", e)
            return [str(e)[:160]]
        raise


def setup_state(cfg: Config) -> str:
    """Derive the operator's setup position from existing signals (never cached)."""
    if not _brief_ok(cfg) or _accounts_problems(cfg):
        return SetupState.NOT_CONFIGURED
    if cfg.postiz_api_key is None:
        return SetupState.CONFIGURED
    try:
        ready = bool(Accounts.load(cfg).live_ready_channels())
    except Exception as e:
        if decide("observability", 0) is EscalationPosture.degrade:
            logging.getLogger("fanops.doctor").debug("setup_state live_ready read failed: %s", e)
            ready = False
        else:
            raise
    if not ready:
        return SetupState.CONNECTED
    if not learning_validated(cfg):
        return SetupState.CONNECTED
    if not cfg.is_live:
        return SetupState.VALIDATED
    return SetupState.LIVE


def setup_next_action(cfg: Config) -> str:
    """Next operator action for the current setup_state — mirrors doctor Postiz-learning hints."""
    from fanops.pipeline_status import source_backlog
    from fanops.ledger import Ledger
    led = Ledger.load(cfg)
    bl = source_backlog(led, cfg)
    if bl.blocked_on_gates:
        return f"{bl.blocked_on_gates} source(s) awaiting gate answer(s) — answer in Studio Gates or run `fanops status`"
    if bl.recoverable:
        return f"{bl.recoverable} source(s) need attention — run `fanops status` then Resume/Reset in Studio Make"
    state = setup_state(cfg)
    problems = _accounts_problems(cfg)
    if state == SetupState.NOT_CONFIGURED:
        if not _brief_ok(cfg):
            return f"create {cfg.context_path} — it steers every clip/caption"
        return "; ".join(problems) + " — add accounts + map each channel in the Studio Go-Live tab"
    if state == SetupState.CONFIGURED:
        return "Connect Postiz (Go-Live > 1 · Connect Postiz)"
    if state == SetupState.CONNECTED:
        if problems:
            return "map every channel (Go-Live > 3 · Map each channel to Postiz)"
        if not learning_validated(cfg):
            return "run the Studio Validate learning step (Go-Live > 5 · Validate learning)"
        return "map every channel (Go-Live > 3 · Map each channel to Postiz)"
    if state == SetupState.VALIDATED:
        return "go live when ready (Go-Live > flip to LIVE, or `fanops init --go-live`)"
    return "operating — run `fanops run` or open the Studio"


def doctor_report(cfg: Config, *, get=None, postiz_probe=None, zernio_auth=None) -> dict:
    """Return {checks, notes, deps?, field_shape?} — thin view over health_model.build_health_report."""
    from fanops.health_model import build_health_report
    return build_health_report(cfg, get=get, postiz_probe=postiz_probe, zernio_auth=zernio_auth).as_dict()
