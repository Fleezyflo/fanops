# src/fanops/llm.py
"""Wire an LLM via the Claude Code CLI in headless print mode (`claude -p`), NOT the Anthropic
SDK — keeps one toolchain (no second SDK dependency) and fits the codebase's shell-a-binary idiom
(like ffmpeg/whisper); `claude` becomes one more absence-guarded binary. We hand `claude` the EXACT
pydantic JSON schema via --json-schema so the model returns schema-conformant output in
`structured_output`, which collapses most "LLM returned malformed JSON" risk. --allowedTools "" =
pure generator (no tool use, no file access — the responder must not wander).

AUTH (load-bearing — read before deploying autonomous mode): we pass `--bare`, which is cron-safe
(skips hooks/MCP/plugin-sync/auto-memory/keychain) BUT under `--bare` Anthropic auth is STRICTLY
`ANTHROPIC_API_KEY` (or apiKeyHelper via --settings) — **OAuth and keychain are NEVER read**. So a
`claude login` (OAuth) session is NOT sufficient: the environment that runs `fanops` MUST export
`ANTHROPIC_API_KEY`, or every gate gets `claude -p` rc=1 "Not logged in" → RuntimeError → the gate
is quarantined and stays pending (no autonomous content). This is the deliberate trade for cron-safety;
it is documented in RUNTIME.md "the autonomous LLM responder" and README install."""
from __future__ import annotations
import json, subprocess
from fanops.errors import ToolchainMissingError

def claude_json(prompt: str, schema: dict, *, timeout: float = 180.0) -> dict:
    """Call `claude -p` with a JSON schema; return the model's schema-valid object.
    Prefers the envelope's `structured_output`; falls back to json.loads(`result`).
    Raises ToolchainMissingError if `claude` is absent, RuntimeError on nonzero exit or
    unparseable output. The CALLER (the responder) validates against the pydantic model and
    quarantines per-request, so this stays a thin, honest shell wrapper."""
    cmd = ["claude", "--bare", "-p", prompt,
           "--output-format", "json",
           "--json-schema", json.dumps(schema),
           "--allowedTools", ""]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"claude not found on PATH — install Claude Code to run the autonomous responder "
            f"({type(e).__name__})") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude -p timed out after {timeout}s") from e
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
