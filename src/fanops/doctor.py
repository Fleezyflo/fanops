"""`fanops doctor` (Phase 3b) — a READ-ONLY first-run health screen. Composes the guards that
already exist (Accounts.validate, the cutover-safety preflight, toolchain presence) into ONE
operator view: PASS/FAIL per item with the exact next action, plus informational notes. It performs
NOTHING — it cannot create platform accounts or obtain a poster API key (the irreducibly-manual setup
steps), so usability for a brand-new operator is capped here by reality, not code; doctor just makes
'what's left' legible instead of buried in the source."""
from __future__ import annotations
import logging
import os
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
    if (os.getenv("FANOPS_RESPONDER") or "").strip().lower() == "llm":
        checks.append(_check("claude on PATH (FANOPS_RESPONDER=llm)", shutil.which("claude") is not None,
                             "install Claude Code + run `claude login` (uses your subscription, no API key)"))
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
    if cfg.poster_backend == "postiz":
        checks.append(_check("POSTIZ_URL + POSTIZ_API_KEY set (FANOPS_POSTER=postiz)",
                             cfg.postiz_url is not None and cfg.postiz_api_key is not None,
                             "set POSTIZ_URL (your self-hosted instance) + POSTIZ_API_KEY (Postiz "
                             "Settings > Developers > Public API) — the free, self-hosted publisher"))
        # Postiz-learning readiness (booleans only, never the key): the loop only acts once the key is set,
        # every active channel is mapped, AND cutover confirmed the lift fields. Hint names the FIRST gap.
        ready = cfg.postiz_api_key is not None and not problems and lv   # lv hoisted above (ECC fix #14)
        if cfg.postiz_api_key is None: hint = "Connect Postiz (Go-Live > 1 · Connect Postiz)"
        elif problems:                hint = "map every channel (Go-Live > 3 · Map each channel to Postiz)"
        elif not lv:                  hint = "run the Studio Validate learning step (Go-Live > 5 · Validate learning)"
        else:                         hint = ""
        checks.append(_check("Postiz learning ready (key + channels mapped + cutover validated)", ready, hint))

    notes: list[str] = []
    notes.append(f"poster backend: {cfg.poster_backend}"
                 + (" (dryrun — writes payloads, posts nothing)" if not cfg.is_live else " (LIVE)"))
    if lv:                                               # ECC fix #14: reuse the single read above
        notes.append("learning loop: validation-confirmed (lift fields reconciled by cutover) — amplify/bandit may be enabled")
    else:
        notes.append("learning loop: NOT validation-confirmed — variant-amplify stays inert even if enabled; "
                     "run the Studio Validate learning step (Go-Live > 5 · Validate learning), or `fanops cutover`, to confirm lift fields")
    try:
        n = len(list(cfg.review.glob("*.jpg"))) if cfg.review.exists() else 0
    except OSError as e:                                 # a glob/stat hiccup (perms, stale mount) -> fail-soft to 0,
        logging.getLogger("fanops.doctor").debug("review glob failed: %s", e)   # but leave a breadcrumb, not a silent 0
        n = 0
    if n:
        notes.append(f"review queue: {n} candidate(s) in 00_review/ awaiting Finder approval — "
                     "move keepers to 00_review/approved/ then `fanops intake`")
    return {"checks": checks, "notes": notes}
