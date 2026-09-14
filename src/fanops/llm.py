# src/fanops/llm.py
"""Wire an LLM via the Grok CLI (`grok --prompt-file` / `--prompt-json`), not an SDK.

Captions use `--prompt-file`; vision uses `--prompt-json` with inline base64 ACP image blocks.
`--json-schema` is the native structured path; a CLI that rejects the flag gets one prompt-side
schema retry. `--tools ""` keeps the call a pure generator.

AUTH: `ANTHROPIC_API_KEY` stays unset — Grok uses the operator's `grok login` session, and
`_grok_env` pops `XAI_API_KEY` / `GROK_CODE_XAI_API_KEY` so a leftover key cannot override it.
Historical `--bare` was a Claude-subscription choice; we still do not provision an Anthropic API key.
"""
from __future__ import annotations
import base64, errno, json, logging, os, random, subprocess, tempfile, time
from pathlib import Path
from fanops.errors import ToolchainMissingError
from fanops.llm_errors import (
    LlmContextLimitError,
    LlmFramesUnreadError,  # noqa: F401 — re-export; test_responder imports from here
    LlmRateLimitError,
    LlmSchemaError,
    LlmTimeoutError,
    LlmToolchainError,
    _is_context_limit,
    _is_toolchain_error,
)
from fanops.llm_json import _extract_json_object
from fanops.llm_json import _json_candidates  # noqa: F401 — re-export for test_llm

logger = logging.getLogger("fanops.llm")
_sleep = time.sleep                                  # indirection so tests can stub the backoff wait


def _missing_required_keys(obj, schema) -> list[str]:
    """Top-level JSON-schema `required` keys absent from `obj`. Non-dict obj → ['<object>']."""
    req = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(obj, dict):
        return ["<object>"]
    if not isinstance(req, list):
        return []
    return [k for k in req if isinstance(k, str) and k not in obj]


def _salvage_json(raw: str, schema: dict) -> dict | None:
    """Extract a JSON object from prose. Missing schema required keys → LlmSchemaError, not a pass."""
    salvaged = _extract_json_object(raw)
    if salvaged is None:
        return None
    missing = _missing_required_keys(salvaged, schema)
    if missing:
        raise LlmSchemaError(
            f"salvaged object missing schema required keys: {', '.join(missing)}")
    return salvaged

_GROK_SUPPORTS_VISION = True  # measured: inline b64 ACP image, 64x64 crimson → color=red. empty data+file:// DROPPED.
_GROK_RATE_LIMIT_MARKERS = ("rate limit", "too many requests", "429", "503", "529", "overloaded")
_GROK_TOOLCHAIN_MARKERS = ("unknown model", "invalid params", "not logged in")

# HTTP statuses claude -p surfaces (in the stdout envelope's api_error_status) when the request is
# rejected pre-processing and is therefore SAFE to retry. A 429 is the common one (usage spike).
_RATELIMIT_STATUSES = {429, 503, 529}
_MAX_RL_RETRIES = 4                                  # total attempts = retries + 1
_RL_BASE_DELAY = 2.0                                 # seconds; doubled per attempt + jittered

def _claude_strict_schema(schema: dict) -> dict:
    """Rewrite a pydantic JSON schema for Claude Code `--json-schema` strict mode.

    Pydantic emits draft-2020-12 `prefixItems` for `tuple[...]` fields (notably
    `MomentPick.segments: list[tuple[float, float]]`). Claude Code ≥2.1.x validates the flag payload
    in strict mode and rejects `prefixItems` as an unknown keyword — that mass-failed every `moments`
    gate while `captions` (no tuples) kept working. Map each fixed-length prefix array to
    `minItems`/`maxItems` + a homogeneous `items` schema so shape intent survives without the keyword.
    Deep-copies; caller's dict is untouched."""
    import copy
    out = copy.deepcopy(schema) if isinstance(schema, dict) else schema
    if not isinstance(out, dict):
        return out

    def walk(node):
        if isinstance(node, dict):
            prefs = node.get("prefixItems")
            if isinstance(prefs, list):
                node.pop("prefixItems", None)
                n = len(prefs)
                node.setdefault("minItems", n)
                node.setdefault("maxItems", n)
                types = [p.get("type") for p in prefs if isinstance(p, dict)]
                if types and all(t == types[0] for t in types) and types[0] is not None:
                    node.setdefault("items", {"type": types[0]})
                else:
                    node.setdefault("items", True)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out)
    return out


def _prompt_side_schema(schema: dict, base: str) -> str:
    """Prompt-side schema constraint — the transport-agnostic fallback when the CLI can't take the
    schema as a flag (cursor-agent always; claude CLIs predating --json-schema). The envelope then
    has no structured_output, so the caller's `result`-parse path does the extraction."""
    return ("Respond with ONLY a single JSON object conforming to this schema — no prose, no markdown:\n"
            + json.dumps(schema) + "\n\n" + base)

def _json_schema_flag_rejected(returncode: int, body: str) -> bool:
    """True iff this failure is specifically the claude CLI not KNOWING --json-schema (an outdated
    install — 2026-07-12: a stale daemon PATH pinned claude 2.0.30 and every gate call died on
    `error: unknown option '--json-schema'`). Keyed on the flag name so no other usage error is
    mistaken for it."""
    return returncode != 0 and "--json-schema" in (body or "") and _is_toolchain_error(body)

def _rate_limit_status(returncode: int, stdout: str) -> int | None:
    """The retryable rate-limit status if this result is one, else None. claude -p emits a nonzero
    rc AND a JSON envelope on stdout carrying `api_error_status` when rate-limited (observed live).
    A nonzero exit with NO such envelope is a hard failure (auth, bad args) — NOT retried."""
    if returncode == 0:
        return None
    try:
        env = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    status = env.get("api_error_status") if isinstance(env, dict) else None
    return status if status in _RATELIMIT_STATUSES else None


def claude_json_meta(prompt, schema, *, timeout=300.0, images=None, model=None, read_root=None):
    if isinstance(schema, dict):
        schema = _claude_strict_schema(schema)
    return _grok_json_meta(prompt, schema, timeout=timeout, images=images, model=model, read_root=read_root)

def _resolve_grok_model(model: str | None) -> str:
    from fanops.config import _GROK_MODEL_ALIASES
    if not model:
        return "grok-4.6"
    return _GROK_MODEL_ALIASES.get(model, model)

def _build_grok_cmd(prompt_path: str, model: str | None, *, cwd: str, schema: dict,
                    schema_flag: bool = True, images: list[str] | None = None,
                    prompt_text: str = "") -> list[str]:
    resolved = _resolve_grok_model(model)
    cmd = ["grok", "--no-auto-update", "--cwd", cwd]
    if images:
        blocks = [{"type": "text", "text": prompt_text}]
        for path in images:
            try:
                raw = Path(path).read_bytes()
            except OSError as e:
                raise LlmToolchainError(f"grok vision frame unreadable {path}: {e}") from e
            mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            blocks.append({"type": "image", "mimeType": mime, "data": base64.b64encode(raw).decode("ascii")})
        cmd += ["--prompt-json", json.dumps(blocks)]
    else:
        cmd += ["--prompt-file", prompt_path]
    if schema_flag:
        cmd += ["--json-schema", json.dumps(schema)]
    cmd += ["--output-format", "json", "--tools", "", "--disable-web-search", "--no-subagents",
            "-m", resolved]
    return cmd

def _grok_env() -> dict:
    e = os.environ.copy()
    e.pop("XAI_API_KEY", None)
    e.pop("GROK_CODE_XAI_API_KEY", None)
    e.update({
        "GROK_CLAUDE_HOOKS_ENABLED": "0",
        "GROK_CLAUDE_MCPS_ENABLED": "0",
        "GROK_CLAUDE_SKILLS_ENABLED": "0",
        "GROK_CLAUDE_AGENTS_ENABLED": "0",
        "GROK_CURSOR_HOOKS_ENABLED": "0",
        "GROK_CURSOR_MCPS_ENABLED": "0",
        "GROK_DISABLE_AUTOUPDATER": "1",
    })
    return e

def grok_models_ok() -> bool:
    """True iff `grok --no-auto-update models` exits 0 (logged-in session). Never raises."""
    try:
        r = subprocess.run(["grok", "--no-auto-update", "models"], timeout=15,
                            env=_grok_env(), capture_output=True)
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False

def _grok_rate_limit_status(returncode: int, stdout: str, stderr: str) -> int | None:
    rl = _rate_limit_status(returncode, stdout)
    if rl is not None:
        return rl
    if returncode == 0:
        return None
    body = (stdout or stderr or "").lower()
    if any(m in body for m in _GROK_RATE_LIMIT_MARKERS):
        return 429
    return None

def _is_grok_toolchain(body: str) -> bool:
    t = (body or "").lower()
    return _is_toolchain_error(t) or any(m in t for m in _GROK_TOOLCHAIN_MARKERS)

def _write_grok_prompt(path: str, text: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)

def _grok_json_meta(prompt: str, schema: dict, *, timeout: float = 300.0,
                    images: list[str] | None = None, model: str | None = None,
                    read_root: str | None = None) -> tuple[dict, str | None, bool]:
    """Call `grok` with a JSON schema; return (object, model, frames_unread=False)."""
    pin = _resolve_grok_model(model)
    with tempfile.TemporaryDirectory(prefix="fanops-grok-") as tmpdir:
        prompt_path = os.path.join(tmpdir, "prompt.txt")

        def _run(prompt_text: str) -> dict:
            def _attempt(schema_flag: bool) -> subprocess.CompletedProcess:
                body_text = prompt_text if schema_flag else _prompt_side_schema(schema, prompt_text)
                if not images:
                    _write_grok_prompt(prompt_path, body_text)
                cmd = _build_grok_cmd(prompt_path, model, cwd=tmpdir, schema=schema,
                                      schema_flag=schema_flag, images=images, prompt_text=body_text)
                if sum(len(a) + 1 for a in cmd) > 900_000:
                    raise LlmContextLimitError("grok argv exceeded ARG_MAX")
                delay = _RL_BASE_DELAY
                for attempt in range(_MAX_RL_RETRIES + 1):
                    try:
                        r = subprocess.run(
                            cmd, check=False, capture_output=True, text=True,
                            timeout=timeout, cwd=tmpdir, env=_grok_env())
                    except FileNotFoundError as e:
                        raise ToolchainMissingError(
                            f"grok not found on PATH — install Grok CLI ({type(e).__name__})") from e
                    except OSError as e:
                        if e.errno == errno.E2BIG:
                            raise LlmContextLimitError("grok argv exceeded ARG_MAX") from e
                        raise ToolchainMissingError(
                            f"grok not found on PATH — install Grok CLI ({type(e).__name__})") from e
                    except subprocess.TimeoutExpired as e:
                        raise LlmTimeoutError(f"grok timed out after {timeout}s") from e
                    rl = _grok_rate_limit_status(r.returncode, r.stdout, r.stderr)
                    if rl is None:
                        return r
                    if attempt >= _MAX_RL_RETRIES:
                        raise LlmRateLimitError(
                            f"grok rate-limited (status={rl}) after {_MAX_RL_RETRIES} retries")
                    logger.warning("grok rate-limited (status=%s) — backing off %.1fs "
                                   "(attempt %d/%d)", rl, delay, attempt + 1, _MAX_RL_RETRIES)
                    _sleep(delay + random.uniform(0, delay))
                    delay *= 2
                return r
            r = _attempt(True)
            if _json_schema_flag_rejected(r.returncode, r.stderr or r.stdout):
                logger.warning("grok CLI rejected --json-schema (outdated install?) — retrying with "
                               "prompt-side schema")
                r = _attempt(False)
            if r.returncode != 0:
                body = (r.stderr or r.stdout or "")[:300]
                if _is_context_limit(body):
                    raise LlmContextLimitError(f"grok context limit (rc={r.returncode}): {body}")
                if _is_grok_toolchain(body):
                    raise LlmToolchainError(f"grok toolchain error (rc={r.returncode}): {body}")
                raise RuntimeError(f"grok failed (rc={r.returncode}): {body}")
            try:
                env = json.loads(r.stdout)
            except Exception as e:
                raise LlmSchemaError(
                    f"grok output could not parse as JSON envelope: {(r.stdout or '')[:300]}") from e
            if not isinstance(env, dict):
                raise LlmSchemaError(
                    f"grok output could not parse as JSON envelope (not an object): {(r.stdout or '')[:300]}")
            return env

        env = _run(prompt)

        def _resolve_from_env(e: dict) -> dict | None:
            so = e.get("structuredOutput")
            if isinstance(so, dict):
                return so
            text = e.get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                salvaged = _salvage_json(text, schema)
                if salvaged is not None:
                    logger.warning("grok text salvaged via JSON-repair (prose-wrapped reply)")
                    return salvaged
            return None

        usage = env.get("modelUsage")
        if isinstance(usage, dict) and usage:
            first = next(iter(usage), None)
            resolved = first if isinstance(first, str) and first.strip() else pin
        else:
            resolved = pin
        obj = _resolve_from_env(env)
        if obj is not None:
            return obj, resolved, False
        raise LlmSchemaError(f"grok envelope had no structuredOutput or JSON text: {env}")

def claude_json(prompt: str, schema: dict, *, timeout: float = 300.0,
                images: list[str] | None = None, model: str | None = None,
                read_root: str | None = None) -> dict:
    """Bare-dict contract preserved for every caller that doesn't need provenance — including
    studio/actions.py, which binds `model = claude_json` and calls it expecting a dict (audit C2:
    a tuple-return there would TypeError). The model-aware path is claude_json_meta."""
    return claude_json_meta(prompt, schema, timeout=timeout, images=images, model=model,
                            read_root=read_root)[0]
