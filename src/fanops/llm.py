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
import json, subprocess
from fanops.errors import ToolchainMissingError

class LlmTimeoutError(RuntimeError):
    """`claude -p` exceeded its time budget. Distinct from a generic RuntimeError so the responder
    can RETRY it (a timeout is usually transient) rather than treating it like a hard failure."""

def claude_json(prompt: str, schema: dict, *, timeout: float = 300.0,
                images: list[str] | None = None) -> dict:
    """Call `claude -p` with a JSON schema; return the model's schema-valid object.
    Prefers the envelope's `structured_output`; falls back to json.loads(`result`).
    Raises ToolchainMissingError if `claude` is absent, RuntimeError on nonzero exit or
    unparseable output. The CALLER (the responder) validates against the pydantic model and
    quarantines per-request, so this stays a thin, honest shell wrapper.
    NO `--bare`: the operator uses the existing `claude` subscription/OAuth (not ANTHROPIC_API_KEY);
    `--strict-mcp-config` + `--allowedTools ""` keep it a clean, no-tool, no-MCP generator.
    `images`: when given (the vision-grounded hook editor), the Read tool is granted and the frame
    paths are named in the prompt so the model READS and SEES them before deciding (proven in the
    Task 0a spike). Read is the ONLY tool granted — still no write/exec/MCP — and the default
    (images=None) path is byte-identical to before (pure no-tool generator)."""
    if images:
        prompt = ("FIRST read these image frames with the Read tool, then answer using what you SEE:\n"
                  + "\n".join(images) + "\n\n" + prompt)
    allowed = "Read" if images else ""
    cmd = ["claude", "-p", prompt,
           "--output-format", "json",
           "--json-schema", json.dumps(schema),
           "--allowedTools", allowed,
           "--strict-mcp-config"]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"claude not found on PATH — install Claude Code to run the autonomous responder "
            f"({type(e).__name__})") from e
    except subprocess.TimeoutExpired as e:
        raise LlmTimeoutError(f"claude -p timed out after {timeout}s") from e
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[:300]}")
    try:
        env = json.loads(r.stdout)
    except Exception as e:
        raise RuntimeError(f"claude -p output could not parse as JSON envelope: {(r.stdout or '')[:300]}") from e
    if not isinstance(env, dict):
        raise RuntimeError(f"claude -p output could not parse as JSON envelope (not an object): {(r.stdout or '')[:300]}")
    so = env.get("structured_output")
    if isinstance(so, dict):
        return so
    result = env.get("result")
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception as e:
            raise RuntimeError(f"claude -p `result` was not JSON: {result[:300]}") from e
    raise RuntimeError(f"claude -p envelope had no structured_output or JSON result: {(r.stdout or '')[:300]}")
