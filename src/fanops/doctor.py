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
from fanops.validation_gate import learning_validated


def _check(label: str, ok: bool, hint: str = "") -> dict:
    return {"label": label, "ok": bool(ok), "hint": "" if ok else hint}


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
        return False, f"accounts.json unreadable -- cannot verify per-account ig_user_id ({str(e)[:120]}); fix it in the Studio Go-Live tab"
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


def _meta_token_expiry_check(cfg: Config, *, get=None):
    """T9: build the 'Meta Graph token not expiring' check dict, or None when no Meta token is configured (the
    check is simply N/A then — never a false alarm). Introspects EVERY distinct resolvable token (global +
    per-handle) via meta_graph.debug_token_expiry: an expired OR unintrospectable (FAIL-CLOSED) token -> ok=False;
    a token inside the _META_TOKEN_LEAD_DAYS window -> ok=True + warn=True (surface, never block). The token value
    is NEVER read into the label/hint (only the handle label + the human expiry date). Fail-open around the
    enumeration so a torn accounts.json can't crash the report (resolvable_meta_tokens already degrades to global)."""
    from datetime import datetime, timezone
    from fanops.meta_graph import resolvable_meta_tokens, debug_token_expiry
    try:
        toks = resolvable_meta_tokens(cfg)
    except Exception:
        toks = []                                        # never crash the report over enumeration
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
    c = _check(lbl, True, "")
    if soon:
        def _fmt(e): return datetime.fromtimestamp(e, tz=timezone.utc).date().isoformat()
        who = ", ".join(f"{h} (expires {_fmt(e)})" for h, e in sorted(soon, key=lambda x: x[1]))
        c["warn"] = True
        c["warn_hint"] = ("Meta token expiring within %d days: %s — rotate it now (docs/META_CREDS_OPS.md) "
                          "before Graph verification + metrics go dark." % (_META_TOKEN_LEAD_DAYS, who))
    return c


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
        return _check(lbl, False, f"Zernio probe error ({str(e)[:120]}); see docs/POSTIZ_OPS.md.")


_DAEMON_STALE_TICKS = 3                                    # heartbeat older than this many install intervals == dead pump (mirrors daemon.status)
_DAEMON_DEFAULT_INTERVAL_S = 600                           # fallback tick interval when the daemon isn't installed / interval unknown


def _daemon_liveness_check(cfg: Config) -> dict:
    """T12: (dict) 'the publish pump is alive AND the queue is draining'. TWO fail conditions:
      (a) the last `fanops run` heartbeat in run.log is older than _DAEMON_STALE_TICKS install intervals
          (dead/stopped/crashing pump) — OR the signal is ABSENT (never ran) -> FAIL CLOSED (unknown != alive);
      (b) queued posts are PAST-DUE beyond a grace window (the pump missed cycles / can't drain) -> FAIL naming
          the count + the oldest age.
    Signal: daemon._heartbeat_age_s (the run.log heartbeat line, a LIVE-clock write each completed tick) — a
    real liveness signal, not a proxy. The past-due gate reuses timeutil.is_due_or_past, the SAME <=now check
    publish_due fires on, so 'past-due here' == 'should have published already'. A grace window (2 intervals)
    keeps a just-due post from a false backlog flag. Ledger read is fail-open: an unreadable ledger degrades the
    backlog half to 'unknown' (surfaced in the hint) while the heartbeat half still governs -> never a crash."""
    from datetime import datetime, timezone
    from fanops import daemon
    interval = daemon.installed_interval(cfg) or _DAEMON_DEFAULT_INTERVAL_S
    lbl = "publish daemon alive + queue draining (heartbeat + past-due backlog)"
    try:
        st = daemon.status(cfg, interval=interval)
    except Exception:
        st = {"installed": False, "loaded": False, "verdict": "unknown", "heartbeat_age_s": None}
    if st.get("installed") and not st.get("loaded"):
        return _check(lbl, False, f"{st['verdict']} — reload with `fanops daemon install` then `fanops daemon status`")
    if st.get("exec_fail"):
        target = st["exec_fail"].get("target", "fanops")
        return _check(lbl, False, f"daemon cannot exec fanops ({target}) — fix the venv/symlink, then `fanops daemon install`")
    try:
        age = st.get("heartbeat_age_s")
        if age is None:
            age = daemon._heartbeat_age_s(cfg)
    except Exception:
        age = None                                        # a read hiccup -> treat as no signal (fail-closed)
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
        backlog_unknown = True
        logging.getLogger("fanops.doctor").debug("daemon backlog read failed: %s", e)
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
    return notes


def _assemble_doctor_checks(cfg: Config, *, get=None, postiz_probe=None, zernio_auth=None) -> list[dict]:
    """Setup gate checks only (deps/field-shape composed by health_model.build_health_report)."""
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
    # 2. autonomous responder needs the LLM CLI ONLY when FANOPS_RESPONDER=llm (mirrors preflight)
    if cfg.responder_mode == "llm":
        from fanops.llm import _CURSOR_SUPPORTS_VISION
        cli_bin = cfg.llm_cli_binary
        hint = ("install Cursor CLI" if cli_bin == "cursor-agent"
                else "install Claude Code + run `claude login` (uses your subscription, no API key)")
        checks.append(_check(f"{cli_bin} on PATH (FANOPS_RESPONDER=llm)", shutil.which(cli_bin) is not None, hint))
        if cfg.llm_transport == "cursor" and not _CURSOR_SUPPORTS_VISION:
            checks.append(_check("claude on PATH (vision fallback for cursor transport)",
                                 shutil.which("claude") is not None,
                                 "install Claude Code + run `claude login` (vision gates fall back to claude)"))
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
        problems = [str(e)[:160]]
    checks.append(_check("accounts.json valid (every active channel mapped to an id)", not problems,
                         "; ".join(problems) + " — add accounts + map each channel in the Studio Go-Live tab"))
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

    # D15: live-route COHERENCE. FANOPS_LIVE=1 (is_live) but nothing actually routes live — a typo'd
    # FANOPS_POSTER (W4 -> dryrun) with no live per-channel backend — is the HALF-LIVE state: the banner
    # would say LIVE while every publish halts in `queued`. Flag it LOUD with the fix. A not-live config or
    # a genuinely-live one (any live route) passes. Guarded so a bad accounts.json can't crash the report.
    try:
        half_live = cfg.is_live and not cfg.live_route_exists
    except Exception as exc:
        from fanops.log import get_logger     # a route-read hiccup falls to not-half-live — record it, don't hide it
        get_logger(cfg)("doctor", "-", "half_live_error", err=str(exc)[:160])
        half_live = False
    if cfg.is_live:
        checks.append(_check("live route exists (FANOPS_LIVE=1 actually publishes)", not half_live,
                             "LIVE flag set but nothing routes live — FANOPS_POSTER is a legacy bridge, not "
                             "the switch. Route a channel to a provider with creds (Studio Go-Live tab), or "
                             "`fanops` back to dryrun. Every publish stays stuck in `queued` until then."))

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

    # T9: Meta token expiry preflight. When the Graph token (or a per-handle token) lapses, Postiz keeps
    # publishing via its OWN OAuth while Graph verification + metrics silently die -> a repeat of this incident
    # on a KNOWN date (~2026-08-18). Introspect each distinct resolvable token via debug_token: FAIL on an
    # expired one, WARN inside a 10-day lead window (ok stays True so it never blocks a run, warn surfaces it),
    # FAIL CLOSED on an unreadable introspection. The token VALUE is never echoed (only the handle + a date).
    # `get` is injected so tests never hit the network; None -> the real requests.get inside meta_graph.
    tcheck = _meta_token_expiry_check(cfg, get=get)
    if tcheck is not None:
        checks.append(tcheck)

    # T10: REAL backend reachability on the publish path (the operator-confirms-health step, as code). Postiz's
    # docker health-check is nginx-only and LIES while the Node backend crash-loops (mastra_ai_spans) — the real
    # probe (GET /integrations, postiz_health_probe) goes PAST nginx. Zernio auth can lapse the same way. Each
    # check is applicable ONLY when that backend has creds (else it is N/A — never a false alarm on a single-
    # backend deployment), FAILS CLOSED on a down/unauthorized/erroring probe, and NEVER echoes a key. The
    # probes are injected so tests never hit the network; None -> the real client.
    pcheck = _postiz_reach_check(cfg, probe=postiz_probe)
    if pcheck is not None:
        checks.append(pcheck)
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
    dchk = _daemon_liveness_check(cfg)
    checks.append(dchk)
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
        return [str(e)[:160]]


def setup_state(cfg: Config) -> str:
    """Derive the operator's setup position from existing signals (never cached)."""
    if not _brief_ok(cfg) or _accounts_problems(cfg):
        return SetupState.NOT_CONFIGURED
    if cfg.postiz_api_key is None:
        return SetupState.CONFIGURED
    try:
        ready = bool(Accounts.load(cfg).live_ready_channels())
    except Exception:
        ready = False
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
