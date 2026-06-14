"""`fanops doctor` (Phase 3b) — a READ-ONLY first-run health screen. Composes the guards that
already exist (Accounts.validate, the cutover-safety preflight, toolchain presence) into ONE
operator view: PASS/FAIL per item with the exact next action, plus informational notes. It performs
NOTHING — it cannot create platform accounts or obtain a Blotato key (the irreducibly-manual setup
steps), so usability for a brand-new operator is capped here by reality, not code; doctor just makes
'what's left' legible instead of buried in the source."""
from __future__ import annotations
import shutil
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.validation_gate import learning_validated


def _check(label: str, ok: bool, hint: str = "") -> dict:
    return {"label": label, "ok": bool(ok), "hint": "" if ok else hint}


def doctor_report(cfg: Config) -> dict:
    """Return {checks: [{label, ok, hint}], notes: [str]}. `checks` are pass/fail setup gates;
    `notes` are informational (learning-validation state, review-queue depth)."""
    checks: list[dict] = []
    # 1. media toolchain (host-dependent — informational pass/fail, the operator installs what's red)
    for tool in ("ffmpeg", "ffprobe", "whisper"):
        checks.append(_check(f"{tool} on PATH", shutil.which(tool) is not None,
                             f"install {tool} (brew install ffmpeg / pip install -e '.[transcribe]')"))
    checks.append(_check("yt-dlp on PATH (only for `fanops pull <url>`)", shutil.which("yt-dlp") is not None,
                         "pip install yt-dlp"))
    # 2. autonomous responder needs the claude CLI ONLY when FANOPS_RESPONDER=llm (mirrors preflight)
    if cfg.responder_mode == "llm":
        checks.append(_check("claude on PATH (FANOPS_RESPONDER=llm)", shutil.which("claude") is not None,
                             "install Claude Code + run `claude login` (uses your subscription, no API key)"))
    # 3. accounts.json valid + every active account has a numeric account_id (human step 2)
    try:
        problems = Accounts.load(cfg).validate()
    except Exception as e:                                # malformed accounts.json -> a check failure, not a crash
        problems = [str(e)[:160]]
    checks.append(_check("accounts.json valid (every active channel mapped to an id)", not problems,
                         "; ".join(problems) + " — add accounts + map each channel in the Studio Go-Live tab"))
    # 4. poster + key consistency (human step 3) — mirrors cli._check_preflight
    if cfg.poster_backend in {"rest", "mcp"}:
        checks.append(_check(f"BLOTATO_API_KEY set (FANOPS_POSTER={cfg.poster_backend})",
                             cfg.blotato_api_key is not None,
                             "export BLOTATO_API_KEY=... (publishing 401s without it)"))
    if cfg.poster_backend == "postiz":
        checks.append(_check("POSTIZ_URL + POSTIZ_API_KEY set (FANOPS_POSTER=postiz)",
                             cfg.postiz_url is not None and cfg.postiz_api_key is not None,
                             "set POSTIZ_URL (your self-hosted instance) + POSTIZ_API_KEY (Postiz "
                             "Settings > Developers > Public API) — the free, non-Blotato publisher"))
        # Postiz-learning readiness (booleans only, never the key): the loop only acts once the key is set,
        # every active channel is mapped, AND cutover confirmed the lift fields. Hint names the FIRST gap.
        ready = cfg.postiz_api_key is not None and not problems and learning_validated(cfg)
        if cfg.postiz_api_key is None: hint = "Connect Postiz (Go-Live > 1 · Connect Postiz)"
        elif problems:                hint = "map every channel (Go-Live > 3 · Map each channel to Postiz)"
        elif not learning_validated(cfg): hint = "run the Studio Validate learning step (Go-Live > 5 · Validate learning)"
        else:                         hint = ""
        checks.append(_check("Postiz learning ready (key + channels mapped + cutover validated)", ready, hint))

    notes: list[str] = []
    notes.append(f"poster backend: {cfg.poster_backend}"
                 + (" (dryrun — writes payloads, posts nothing)" if cfg.poster_backend == "dryrun" else " (LIVE)"))
    if learning_validated(cfg):
        notes.append("learning loop: validation-confirmed (lift fields reconciled by cutover) — amplify/bandit may be enabled")
    else:
        notes.append("learning loop: NOT validation-confirmed — variant-amplify stays inert even if enabled; "
                     "run the Studio Validate learning step (Go-Live > 5 · Validate learning), or `fanops cutover`, to confirm lift fields")
    try:
        n = len(list(cfg.review.glob("*.jpg"))) if cfg.review.exists() else 0
    except Exception:
        n = 0
    if n:
        notes.append(f"review queue: {n} candidate(s) in 00_review/ awaiting Finder approval — "
                     "move keepers to 00_review/approved/ then `fanops intake`")
    return {"checks": checks, "notes": notes}
