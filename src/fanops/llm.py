# src/fanops/llm.py
"""Wire an LLM via the Claude Code CLI in headless print mode (`claude -p`), NOT the Anthropic
SDK — keeps one toolchain (no second SDK dependency) and fits the codebase's shell-a-binary idiom
(like ffmpeg/whisper); `claude` becomes one more absence-guarded binary. We hand `claude` the EXACT
pydantic JSON schema via --json-schema so the model returns schema-conformant output in
`structured_output`, which collapses most "LLM returned malformed JSON" risk. --allowedTools "" =
pure generator (no tool use, no file access — the responder must not wander).

AUTH (load-bearing — operator decision 2026-06-04: use the EXISTING `claude` subscription, NOT an
API key): we DO NOT pass `--bare`. Under `--bare`, Anthropic auth is STRICTLY `ANTHROPIC_API_KEY`
and **OAuth/keychain are NEVER read** — so a `claude login` session would still fail "Not logged
in" (verified on this host: `claude --bare -p` → rc with "Not logged in", plain `claude -p` → ok).
Plain `claude -p` uses the operator's existing logged-in `claude` session (the subscription), which
is what we want: NO API key to provision in the cron environment. We keep the call a CLEAN PURE
GENERATOR despite dropping `--bare` by passing `--strict-mcp-config` (no MCP servers from any config
bleed into the moment/caption decision) plus `--allowedTools ""` (no tool use, no file access — the
responder must not wander). Tradeoff vs `--bare`: a non-bare `claude -p` also loads hooks/auto-memory/
CLAUDE.md-discovery, so it is slightly heavier per call and reads the host's `~/.claude` config; that
is the accepted cost of riding the existing login instead of an API key. The cron environment
therefore needs a logged-in `claude` (a valid `claude login` on the host), NOT `ANTHROPIC_API_KEY`.
Documented in RUNTIME.md "the autonomous LLM responder" and README install."""
from __future__ import annotations
import json, logging, random, subprocess, time
from fanops.errors import ToolchainMissingError

logger = logging.getLogger("fanops.llm")
_sleep = time.sleep                                  # indirection so tests can stub the backoff wait

class LlmTimeoutError(RuntimeError):
    """`claude -p` exceeded its time budget. Distinct from a generic RuntimeError so the responder
    can RETRY it (a timeout is usually transient) rather than treating it like a hard failure."""

class LlmRateLimitError(RuntimeError):
    """`claude -p` stayed rate-limited (api_error_status 429/503/529) across all backoff retries.
    Typed so the responder fails LOUDLY on a sustained rate limit instead of silently producing
    nothing (the asymmetry the publishers' backoff already fixed; the creative path lacked it)."""

class LlmContextLimitError(RuntimeError):
    """`claude -p` rejected the request as too large for the model context. Typed (AGENT-2) so the responder
    turns a payload-too-big failure into a VISIBLE degraded gate state instead of an infinite-pending wedge."""

_CONTEXT_LIMIT_MARKERS = ("prompt is too long", "context length", "exceeds the maximum", "too many tokens",
                          "maximum context")
def _is_context_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _CONTEXT_LIMIT_MARKERS)

# HTTP statuses claude -p surfaces (in the stdout envelope's api_error_status) when the request is
# rejected pre-processing and is therefore SAFE to retry. A 429 is the common one (usage spike).
_RATELIMIT_STATUSES = {429, 503, 529}
_MAX_RL_RETRIES = 4                                  # total attempts = retries + 1
_RL_BASE_DELAY = 2.0                                 # seconds; doubled per attempt + jittered

def _rate_limit_status(returncode: int, stdout: str) -> int | None:
    """The retryable rate-limit status if this result is one, else None. claude -p emits a nonzero
    rc AND a JSON envelope on stdout carrying `api_error_status` when rate-limited (observed live).
    A nonzero exit with NO such envelope is a hard failure (auth, bad args) — NOT retried."""
    if returncode == 0:
        return None
    try:
        env = json.loads(stdout)
    except Exception:
        return None
    status = env.get("api_error_status") if isinstance(env, dict) else None
    return status if status in _RATELIMIT_STATUSES else None

def _frames_unread(env: dict) -> bool:
    """HOOK-TRANSPORT: True iff the envelope PROVES the model answered without any tool turn — so the
    granted Read tool never fired and the attached frames were NOT opened. `num_turns` counts the agent
    turns; ==1 is a pure single-shot answer (no Read), >=2 means a tool turn ran (Read is the only tool
    granted). num_turns absent/non-int -> UNVERIFIABLE (older CLI / a synthetic test envelope) -> NOT
    treated as unread, so the no-num_turns path is byte-identical and never falsely re-asks."""
    n = env.get("num_turns")
    return isinstance(n, int) and n <= 1


def claude_json_meta(prompt: str, schema: dict, *, timeout: float = 300.0,
                     images: list[str] | None = None, model: str | None = None) -> tuple[dict, str | None, bool]:
    """Call `claude -p` with a JSON schema; return (schema-valid object, model-that-answered,
    frames_unread). frames_unread is True ONLY when frames were ATTACHED but the model answered
    without ever opening them (num_turns<=1 after a re-ask) — a degraded, text-grounded hook the
    responder breadcrumbs + RunSummary counts (AGENT-9); False on every non-vision / frames-read call.
    Prefers the envelope's `structured_output`; falls back to json.loads(`result`).
    Raises ToolchainMissingError if `claude` is absent, RuntimeError on nonzero exit or
    unparseable output. The CALLER (the responder) validates against the pydantic model and
    quarantines per-request, so this stays a thin, honest shell wrapper.
    NO `--bare`: the operator uses the existing `claude` subscription/OAuth (not ANTHROPIC_API_KEY);
    `--strict-mcp-config` + `--allowedTools ""` keep it a clean, no-tool, no-MCP generator.
    `images`: when given (the vision-grounded hook editor), the Read tool is granted and the frame
    paths are named in the prompt so the model READS and SEES them before deciding (proven in the
    Task 0a spike). Read is the ONLY tool granted — still no write/exec/MCP — and the no-image path
    is byte-identical to before (pure no-tool generator).
    `model` (V2 M1/F1): pin `claude -p --model` so the creative brain is REPRODUCIBLE — an unpinned
    call drifts with whatever the CLI defaults to. The returned model prefers the envelope's reported
    `model` (the true audit trail) and FALLS BACK to the pinned value when the envelope omits it."""
    allowed = "Read" if images else ""
    # ECC fix #11: pass the prompt on STDIN (the documented `… | claude -p` headless form, default
    # --input-format text), NOT as an argv positional. argv was world-visible via `ps`/`/proc/<pid>/
    # cmdline` (transcript + brand guidance leaked to any local process) and a very large transcript
    # could hit ARG_MAX -> E2BIG, surfaced misleadingly as "claude not found". STDIN has neither limit.
    # --model (when pinned) is appended LAST so it never lands between --allowedTools and its value
    # (the argv-order the tests assert on — audit H).
    cmd = ["claude", "-p",
           "--output-format", "json",
           "--json-schema", json.dumps(schema),
           "--allowedTools", allowed,
           "--strict-mcp-config"] + (["--model", model] if model else [])

    def _run(stdin_prompt: str) -> dict:
        # Rate-limit backoff (mirrors the publishers' jittered exponential retry (postiz/zernio)):
        # a 429/503/529 is rejected pre-processing and SAFE to retry. Without this a usage spike turned
        # the whole autonomous run into a silent no-op (one log line per gate). A timeout / hard nonzero
        # exit is NOT retried here (timeout has its own one-shot retry in the responder).
        delay = _RL_BASE_DELAY
        for attempt in range(_MAX_RL_RETRIES + 1):
            try:
                r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout, input=stdin_prompt)
            except (FileNotFoundError, OSError) as e:
                raise ToolchainMissingError(
                    f"claude not found on PATH — install Claude Code to run the autonomous responder "
                    f"({type(e).__name__})") from e
            except subprocess.TimeoutExpired as e:
                raise LlmTimeoutError(f"claude -p timed out after {timeout}s") from e
            rl = _rate_limit_status(r.returncode, r.stdout)
            if rl is None:
                break                                        # success or a hard (non-retryable) failure
            if attempt >= _MAX_RL_RETRIES:
                raise LlmRateLimitError(
                    f"claude -p rate-limited (api_error_status={rl}) after {_MAX_RL_RETRIES} retries")
            logger.warning("claude -p rate-limited (api_error_status=%s) — backing off %.1fs "
                           "(attempt %d/%d)", rl, delay, attempt + 1, _MAX_RL_RETRIES)
            _sleep(delay + random.uniform(0, delay))         # jitter so many gates don't retry in lockstep
            delay *= 2
        if r.returncode != 0:
            body = (r.stderr or r.stdout or "")[:300]
            if _is_context_limit(body):                       # AGENT-2: a too-big payload -> typed, not generic
                raise LlmContextLimitError(f"claude -p context limit (rc={r.returncode}): {body}")
            raise RuntimeError(f"claude -p failed (rc={r.returncode}): {body}")
        try:
            env = json.loads(r.stdout)
        except Exception as e:
            raise RuntimeError(f"claude -p output could not parse as JSON envelope: {(r.stdout or '')[:300]}") from e
        if not isinstance(env, dict):
            raise RuntimeError(f"claude -p output could not parse as JSON envelope (not an object): {(r.stdout or '')[:300]}")
        return env

    # HOOK-TRANSPORT: hand the frames + a read-them-first instruction, then VERIFY the model actually
    # OPENED them (num_turns proves a Read turn fired — Read is the only tool granted). If it answered
    # text-only, re-ask ONCE forcing the Read; if STILL unread, proceed but log a degraded breadcrumb
    # (the hook is then text-grounded, not frame-grounded — degraded but HONEST, surfaced by the breadcrumb).
    frames_unread = False                                    # AGENT-9: True iff frames were ATTACHED but never opened
    if images:
        env = _run("FIRST read these image frames with the Read tool, then answer using what you SEE:\n"
                   + "\n".join(images) + "\n\n" + prompt)
        if _frames_unread(env):
            env2 = _run("You did NOT open the frames. You MUST call the Read tool on EACH path below "
                        "BEFORE answering — ground your hook in what you SEE, not the text:\n"
                        + "\n".join(images) + "\n\n" + prompt)
            if not _frames_unread(env2):
                env = env2
            else:
                frames_unread = True                         # AGENT-9: surfaced to the responder, not just logged
                logger.warning("hook frames appear unread (num_turns<=1) after re-ask — hook is text-grounded")
    else:
        env = _run(prompt)

    rep = env.get("model")                                   # the model that actually answered, if reported
    resolved = rep if isinstance(rep, str) and rep.strip() else model   # else fall back to the pinned value
    so = env.get("structured_output")
    if isinstance(so, dict):
        return so, resolved, frames_unread
    result = env.get("result")
    if isinstance(result, str):
        try:
            return json.loads(result), resolved, frames_unread
        except Exception as e:
            raise RuntimeError(f"claude -p `result` was not JSON: {result[:300]}") from e
    raise RuntimeError(f"claude -p envelope had no structured_output or JSON result: {env}")

def claude_json(prompt: str, schema: dict, *, timeout: float = 300.0,
                images: list[str] | None = None, model: str | None = None) -> dict:
    """Bare-dict contract preserved for every caller that doesn't need provenance — including
    studio/actions.py, which binds `model = claude_json` and calls it expecting a dict (audit C2:
    a tuple-return there would TypeError). The model-aware path is claude_json_meta."""
    return claude_json_meta(prompt, schema, timeout=timeout, images=images, model=model)[0]
