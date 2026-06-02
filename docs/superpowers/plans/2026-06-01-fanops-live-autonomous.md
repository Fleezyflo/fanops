# FanOps → Live + Autonomous Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take FanOps from "production-grade offline skeleton" to "runs unattended on cron, posting to real fan accounts via Blotato, with an autonomous LLM (`claude -p`) making the creative calls and the learning loop closing on real metrics" — by fixing the 6 block-live defects + the autonomy/recovery gaps the adversarial audit surfaced (run wf_1889a97c-205, 20 verified findings).

**Architecture:** The deterministic media spine (ingest→transcribe→signal→clip→caption-gate→crosspost→staggered-publish) is sound and untouched. This plan adds/fixes: (1) the LLM responder — wired to `claude -p --output-format json --json-schema` as the autonomous brain; (2) the learning loop — `track→adjust` folded into a cron-driven pass with amplification guards; (3) the live Blotato seam — submission-id robustness, MCP auth, idempotency token; (4) concurrency — a single advisory lock spanning the whole load-mutate-save; (5) observability — a dead-man's-switch so silent death is detectable; (6) operator-recovery CLI verbs so a non-expert never hand-edits `ledger.json`. Validation hardening (NaN, caption language/surface) closes the autonomous-LLM attack surface.

**Tech Stack:** Python 3.12, pydantic v2, pytest + pytest-mock, `claude` CLI (headless `-p` mode) for the LLM, Blotato v2 REST/MCP for posting, `fcntl.flock` for concurrency, real ffmpeg/whisper/yt-dlp subprocesses (already absence-guarded).

---

## How to work this plan

- **TDD always** (project non-negotiable): reproduce → failing test → minimal fix → verify. Use `superpowers:systematic-debugging` for any bug.
- **From the project root** (`cd "/Users/molhamhomsi/Moh Flow Fanops"`): `Config()` resolves the ledger from `cwd`; there is NO `FANOPS_ROOT`. Running elsewhere gives false-greens.
- **`source .venv/bin/activate` before pytest** (bare `pytest` mis-reports the `mocker` fixture).
- **Verify through the real `fanops` CLI**, not just pytest, for anything touching a command path.
- **Commit per task.** End commit messages with the Co-Authored-By trailer the repo uses.
- After code changes: `sync-docs`. At end of series: `handoff` WRITE. Record deviations in the auto-memory `fanops-build-deviations.md`.
- **CREDENTIAL GATE:** Phase D and the `[GATED]`-marked tasks need Blotato **sandbox** creds (`BLOTATO_API_KEY` + `BLOTATO_SMOKE_ACCOUNT_ID`) the operator does not yet have. Do every credential-free phase (A, B, C, E, F) first — they are the bulk and fully executable now. Stop at a `[GATED]` task and surface it.

## Phase order (dependency-correct)

1. **Phase A — Autonomous LLM brain** (gap #1, H2, N1): wire `claude -p`, per-request quarantine, present-but-invalid handling. *No creds.*
2. **Phase B — Concurrency correctness** (gap #5/B4, M1, M2): single lock spanning load-mutate-save; fix the two known audit-MEDIUMs while in the ledger. *No creds.*
3. **Phase C — Autonomous-LLM validation hardening** (H4, H5, H6): reject NaN picks; validate caption language + surface. *No creds.*
4. **Phase D — Live Blotato seam** (gap #3/B2, gap #4/B3, H1): submission-id robustness, MCP auth, client idempotency token. *Code now; the field-name VERIFICATION step is `[GATED]`.*
5. **Phase E — Learning loop autonomy + dead-man's-switch** (gap #2/A2, gap #6/B5): fold track→adjust into cron with amplify guards; heartbeat/run-delta output. *No creds.*
6. **Phase F — Operator-recovery CLI verbs** (H1, unhold, retry-metrics, retry-source): no more hand-editing `ledger.json`. *No creds.*

---

# Phase A — Autonomous LLM brain

The audit's #1 block-live: `get_responder` builds `LlmResponder(cfg)` with no model → `_default_model` raises on the first gate. We wire it to `claude -p`. Per claude-code-guide (verified current 2026-06): `claude --bare -p "<prompt>" --output-format json --json-schema '<schema>' --allowedTools ""` returns `{"structured_output": <schema-valid obj>, "result": "<text>", ...}` on stdout, exit 0 on success, nonzero on error. `--bare` skips MCP/hooks/keychain (cron-safe); `--allowedTools ""` makes it a pure generator (no tool wandering). `claude` is a PATH binary → reuse the toolchain-absent guard pattern.

### Task A1: A `claude -p` model-call helper module

**Files:**
- Create: `src/fanops/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
import json
import pytest
from fanops.errors import ToolchainMissingError
from fanops.llm import claude_json

_SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}

def test_claude_json_extracts_structured_output(mocker):
    # claude -p returns the envelope on stdout; we want structured_output.
    envelope = {"structured_output": {"x": 7}, "result": "{\"x\": 7}", "session_id": "s", "total_cost_usd": 0.001}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    out = claude_json("pick a number", _SCHEMA)
    assert out == {"x": 7}
    # built the headless, no-tools, schema-enforced invocation
    cmd = run.call_args[0][0]
    assert cmd[0] == "claude" and "--bare" in cmd and "-p" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--json-schema" in cmd
    i = cmd.index("--allowedTools"); assert cmd[i + 1] == ""   # pure generator

def test_claude_json_falls_back_to_parsing_result_when_no_structured(mocker):
    # If structured_output is absent/null, parse the JSON in `result`.
    envelope = {"structured_output": None, "result": "{\"x\": 9}", "session_id": "s"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert claude_json("q", _SCHEMA) == {"x": 9}

def test_claude_json_raises_on_nonzero_exit(mocker):
    class R: returncode = 1; stdout = ""; stderr = "auth failed"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError, match="claude -p failed"):
        claude_json("q", _SCHEMA)

def test_claude_json_raises_toolchain_missing_when_claude_absent(mocker):
    def absent(cmd, **kw): raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.llm.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="claude"):
        claude_json("q", _SCHEMA)

def test_claude_json_raises_on_unparseable_output(mocker):
    class R: returncode = 0; stdout = "not json at all"; stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError, match="could not parse"):
        claude_json("q", _SCHEMA)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fanops.llm'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/llm.py
"""Wire an LLM via the Claude Code CLI in headless print mode (`claude -p`), NOT the Anthropic
SDK — reuses the operator's existing Claude Code auth, adds no app-level API key, and fits the
codebase's shell-a-binary idiom (like ffmpeg/whisper). We hand `claude` the EXACT pydantic JSON
schema via --json-schema so the model returns schema-conformant output in `structured_output`,
which collapses most "LLM returned malformed JSON" risk. --bare = cron-safe (no MCP/hooks/keychain);
--allowedTools "" = pure generator (no tool use, no file access — the responder must not wander)."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_llm.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/llm.py tests/test_llm.py
git commit -m "feat: claude -p LLM helper (schema-enforced, headless, absence-guarded)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task A2: Commit the moment-pick and caption prompt templates

**Files:**
- Create: `src/fanops/prompts.py`
- Test: `tests/test_prompts.py`

The prompts turn a `MomentRequest`/`CaptionRequest` payload into a `claude -p` instruction. They are committed (not improvised) so the autonomous behavior is reviewable and stable. They MUST instruct: return only what the schema asks; respect EN/AR per `language`; honour the brand `guidance` (context.md, already injected into the request); pick in-bounds timestamps; for captions, write one item per requested surface with the surface key verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts.py
from fanops.prompts import moment_prompt, caption_prompt

def test_moment_prompt_includes_transcript_duration_guidance_and_bounds_rule():
    payload = {"source_id": "s1", "duration": 42.0,
               "transcript": [{"start": 1.0, "end": 3.0, "text": "they slept on me"}],
               "signal_peaks": [{"t": 2.0, "kind": "scene_cut", "score": 9.0}],
               "language": "en", "guidance": "BRAND: confident, bilingual."}
    p = moment_prompt(payload)
    assert "they slept on me" in p
    assert "42.0" in p                       # the duration bound the LLM must respect
    assert "BRAND: confident, bilingual." in p
    assert "start" in p and "end" in p       # asks for picks with timestamps
    # explicitly forbids out-of-bounds / NaN
    assert "0" in p and ("duration" in p.lower() or "bounds" in p.lower())

def test_caption_prompt_lists_every_surface_and_language():
    payload = {"clip_id": "c1",
               "surfaces": [{"surface": "@a/instagram", "platform": "instagram"},
                            {"surface": "@a/tiktok", "platform": "tiktok"}],
               "transcript_excerpt": "they slept on me", "language": "ar",
               "guidance": "BRAND: no slurs."}
    p = caption_prompt(payload)
    assert "@a/instagram" in p and "@a/tiktok" in p
    assert "ar" in p                          # must caption in the source language
    assert "BRAND: no slurs." in p
    assert "surface" in p                     # tells the model to echo the surface key verbatim
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fanops.prompts'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/prompts.py
"""Committed prompt templates for the autonomous LLM responder. Kept in source (not improvised
per-call) so the autonomous creative behavior is reviewable, diff-able, and stable. Each turns a
request payload (MomentRequest/CaptionRequest, already carrying context.md brand guidance) into a
`claude -p` instruction. The CALLER pairs these with the exact pydantic JSON schema via
--json-schema, so these prompts describe INTENT + CONSTRAINTS; the schema enforces SHAPE."""
from __future__ import annotations
import json

def moment_prompt(payload: dict) -> str:
    duration = payload.get("duration", 0.0)
    return (
        "You are the editorial brain of an autonomous fan-account engine for a bilingual (EN/AR) "
        "rapper. From the transcript and signal peaks below, choose the MOMENTS most worth cutting "
        "into short vertical clips. Return picks as JSON matching the provided schema.\n\n"
        f"SOURCE DURATION (seconds): {duration}\n"
        "HARD RULES for every pick:\n"
        f"  - 0 <= start < end <= {duration} (timestamps MUST be real, finite seconds, in-bounds; "
        "never NaN/Infinity).\n"
        "  - (end - start) >= 0.5 seconds.\n"
        "  - `reason` is REQUIRED: one sentence on WHY this moment hits (punchline, beat drop, "
        "quotable bar).\n"
        "  - Prefer moments that align with a transcript line and/or a signal peak.\n"
        "  - Return as many GOOD picks as exist; do not pad. An empty list is valid if nothing is "
        "worth posting.\n\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"LANGUAGE: {payload.get('language')}\n"
        f"TRANSCRIPT (JSON):\n{json.dumps(payload.get('transcript', []), ensure_ascii=False)}\n"
        f"SIGNAL PEAKS (JSON):\n{json.dumps(payload.get('signal_peaks', []), ensure_ascii=False)}\n"
    )

def caption_prompt(payload: dict) -> str:
    surfaces = payload.get("surfaces", [])
    keys = [s.get("surface") for s in surfaces]
    return (
        "You are the caption writer for an autonomous fan-account engine for a bilingual (EN/AR) "
        "rapper. Write ONE caption per posting surface listed below. Return JSON matching the "
        "provided schema.\n\n"
        "HARD RULES:\n"
        f"  - Write in this language: {payload.get('language')} (match it; do not switch languages).\n"
        "  - One item per surface. Set each item's `surface` to the EXACT key given (copy verbatim — "
        "do not reformat, abbreviate, or fix it).\n"
        f"  - Surfaces to caption (use these exact keys): {json.dumps(keys, ensure_ascii=False)}\n"
        "  - Keep it on-brand and platform-appropriate; no slurs, no off-brand claims.\n\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"CLIP TRANSCRIPT EXCERPT: {payload.get('transcript_excerpt', '')}\n"
        f"SURFACES (JSON):\n{json.dumps(surfaces, ensure_ascii=False)}\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_prompts.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/prompts.py tests/test_prompts.py
git commit -m "feat: committed EN/AR prompt templates for the autonomous responder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task A3: Wire LlmResponder to claude_json with per-request quarantine (fixes #1 + H2 + N1)

**Files:**
- Modify: `src/fanops/responder.py`
- Test: `tests/test_responder.py`

This is the keystone. `get_responder` must build a *working* `LlmResponder` whose default model is `claude_json` + the committed prompts. `answer_pending` must quarantine per-request (H2): one bad request logs + leaves that gate pending, never halts the others. A present-but-invalid response (N1) is handled by the existing contract (the gate stays pending) but now the failure is logged, not silent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_responder.py — ADD these (keep existing tests)
import json
import pytest
from fanops.config import Config
from fanops.responder import LlmResponder, get_responder
from fanops.agentstep import write_request, response_path, request_path

def _seed_moment_request(cfg, key="s1"):
    write_request(cfg, kind="moments", key=key,
                  payload={"source_id": key, "duration": 10.0, "transcript": [], "signal_peaks": [],
                           "language": "en", "guidance": ""})

def test_get_responder_llm_is_usable_without_explicit_model(tmp_path, monkeypatch, mocker):
    # The production default must be a WORKING model (claude -p), not a stub that raises (gap #1).
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg)
    # stub the claude -p call: return one valid pick
    mocker.patch("fanops.responder.claude_json",
                 return_value={"picks": [{"start": 1.0, "end": 4.0, "reason": "bar",
                                          "transcript_excerpt": "x", "signal_score": 0.0}]})
    r = get_responder(cfg)
    n = r.answer_pending(cfg)
    assert n == 1
    written = json.loads(response_path(cfg, "moments", "s1").read_text())
    assert written["picks"][0]["start"] == 1.0
    assert "request_id" in written

def test_responder_quarantines_one_bad_request_and_answers_the_rest(tmp_path, monkeypatch, mocker):
    # H2: one request whose model call raises must NOT halt the others.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg, "good")
    _seed_moment_request(cfg, "bad")
    def model(kind, payload):
        if payload["source_id"] == "bad":
            raise RuntimeError("transient LLM 500")
        return {"picks": [{"start": 1.0, "end": 4.0, "reason": "r"}]}
    mocker.patch("fanops.responder.claude_json", side_effect=lambda prompt, schema, **kw: model("moments", _payload_of(prompt)))
    # simpler: patch the bound model directly
    r = LlmResponder(cfg, model=lambda kind, payload: (_ for _ in ()).throw(RuntimeError("boom")) if payload["source_id"] == "bad" else {"picks": [{"start": 1.0, "end": 4.0, "reason": "r"}]})
    n = r.answer_pending(cfg)
    assert n == 1                                  # good answered, bad quarantined
    assert response_path(cfg, "moments", "good").exists()
    assert not response_path(cfg, "moments", "bad").exists()   # bad gate left pending

def _payload_of(prompt):  # test helper: not used by the simpler path above
    return {"source_id": "good"}

def test_responder_invalid_model_output_leaves_gate_pending_not_crash(tmp_path, monkeypatch):
    # N1: a present-but-schema-invalid answer must be handled (gate stays pending), not crash the run.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg, "s1")
    # model returns a pick missing the required `reason` -> pydantic rejects
    r = LlmResponder(cfg, model=lambda kind, payload: {"picks": [{"start": 1.0, "end": 4.0}]})
    n = r.answer_pending(cfg)
    assert n == 0                                  # nothing validly answered
    assert not response_path(cfg, "moments", "s1").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_responder.py -v -k "usable or quarantines or invalid_model"`
Expected: FAIL — `get_responder(...).answer_pending` raises `RuntimeError` (the stub) / `claude_json` import missing / no quarantine.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/responder.py — REPLACE the whole file
"""Autonomous agent-gate answerer (FIX F02/F13 + AUDIT B1/H2/N1). Behind the file contract: reads
pending *.request.json, produces a schema-valid *.response.json. ManualResponder = no-op (a human
writes the files). LlmResponder = calls `claude -p` (via fanops.llm.claude_json) with the committed
prompt + the exact pydantic JSON schema, validates the output, and writes the response. Each request
is QUARANTINED (one bad gate logs + stays pending, never halts the others — mirrors advance()'s
per-unit quarantine). get_responder() picks by FANOPS_RESPONDER and returns a WORKING llm responder."""
from __future__ import annotations
import json
from typing import Callable, Optional
from pydantic import ValidationError
from fanops.config import Config
from fanops.models import MomentDecision, CaptionSet
from fanops.agentstep import pending, request_path, response_path, latest_request_id
from fanops.llm import claude_json
from fanops.prompts import moment_prompt, caption_prompt
from fanops.log import get_logger

_SCHEMA = {"moments": MomentDecision, "captions": CaptionSet}
_PROMPT = {"moments": moment_prompt, "captions": caption_prompt}

class ManualResponder:
    def __init__(self, cfg: Config): self.cfg = cfg
    def answer_pending(self, cfg: Config) -> int:
        return 0                                    # a human (or external cron) writes responses

def _default_claude_model(kind: str, payload: dict) -> dict:
    """The production model: hand claude -p the committed prompt + the gate's JSON schema."""
    schema = _SCHEMA[kind].model_json_schema()
    return claude_json(_PROMPT[kind](payload), schema)

class LlmResponder:
    """model(kind, request_payload_dict) -> response_dict. Defaults to `claude -p`; injectable for
    tests so no network/subprocess is needed."""
    def __init__(self, cfg: Config, model: Optional[Callable[[str, dict], dict]] = None):
        self.cfg = cfg
        self._model = model or _default_claude_model

    def answer_pending(self, cfg: Config) -> int:
        log = get_logger(cfg)
        answered = 0
        for kind, model_cls in _SCHEMA.items():
            for key in pending(cfg, kind=kind):
                try:                                # H2: quarantine per request
                    payload = json.loads(request_path(cfg, kind, key).read_text())
                    out = self._model(kind, payload)
                    rid = latest_request_id(cfg, kind, key)
                    out = {**out, "request_id": rid}
                    obj = model_cls(**out)          # N1: validate; ValidationError -> leave pending + log
                    response_path(cfg, kind, key).write_text(obj.model_dump_json(indent=2))
                    answered += 1
                except ValidationError as e:
                    log("responder", f"{kind}:{key}", "invalid", err=str(e)[:160])
                except Exception as e:              # transient model/CLI failure: log, leave pending
                    log("responder", f"{kind}:{key}", "error", err=str(e)[:160])
        return answered

def get_responder(cfg: Config):
    if cfg.responder_mode == "llm":
        return LlmResponder(cfg)                    # now a WORKING responder (claude -p default)
    return ManualResponder(cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_responder.py -v`
Expected: PASS. Then full suite: `python -m pytest -q -m "not integration"` → all green.

Note: the existing `test_llm_responder_invalid_output_raises` test (if present) asserted the OLD behavior (raise on invalid). It now must assert the NEW behavior (logged, gate stays pending, no raise). Update that test in this step — same "rewrite the test that encoded the old behavior" move the repo uses elsewhere.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/responder.py tests/test_responder.py
git commit -m "feat (audit B1/H2/N1): wire LlmResponder to claude -p with per-request quarantine

get_responder now returns a WORKING llm responder (claude -p + committed prompts + schema),
not a stub that raises. Each gate is quarantined: a transient model error or an invalid
response logs and leaves that gate pending, never halts the rest of the tick.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task A4: Real-CLI verification of the autonomous responder

**Files:** none (verification task).

- [ ] **Step 1: Confirm `claude` is on PATH**

Run: `command -v claude`
Expected: a path. If absent, the responder will raise `ToolchainMissingError` (graceful) — note it for the operator.

- [ ] **Step 2: Drive one real gate end-to-end through the CLI**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate
SCRATCH="$(mktemp -d)"; mkdir -p "$SCRATCH/MohFlow-FanOps/01_inbox"
# make a real spoken sample so transcribe/signals produce a real moment request
say -o "$SCRATCH/MohFlow-FanOps/01_inbox/s.wav" --data-format=LEF32@22050 "they slept on me. not anymore." 2>/dev/null || \
  espeak -w "$SCRATCH/MohFlow-FanOps/01_inbox/s.wav" "they slept on me. not anymore."
ffmpeg -y -f lavfi -i testsrc=duration=6:size=1280x720:rate=30 -i "$SCRATCH/MohFlow-FanOps/01_inbox/s.wav" -c:v libx264 -c:a aac -t 6 "$SCRATCH/MohFlow-FanOps/01_inbox/s.mp4" -loglevel error
rm -f "$SCRATCH/MohFlow-FanOps/01_inbox/s.wav"
cd "$SCRATCH"
echo '{"accounts":[{"handle":"@a","account_id":"1","platforms":["instagram"],"status":"active"}]}' > MohFlow-FanOps/00_control/accounts.json
FANOPS_WHISPER_MODEL=tiny FANOPS_RESPONDER=llm "$(cd - >/dev/null; pwd)/.venv/bin/python" -m fanops.cli run --base-time 2020-01-01T00:00:00Z
```
Expected: the run advances, `claude -p` answers the moment gate (and then the caption gate), and the output dict shows progress (`moments`>=1, eventually `clips`>=1). Exit 0. If `claude` returns picks, the gate clears; confirm a `moments__*.response.json` exists with real picks.

- [ ] **Step 3: Record the outcome in the deviation memo**

Note in `fanops-build-deviations.md`: the autonomous responder is wired + verified through the real CLI (or, if `claude`/TTS absent in this env, that it degraded gracefully and the unit tests prove the path).

---

# Phase B — Concurrency correctness

The audit's B4: the flock guards only `save()`, not the load→mutate→save window, so overlapping cron runs lose updates (a published post vanishes, or a `submitting` reverts → double-post). Fix: one advisory lock held across the WHOLE pass. While we're in the ledger, fix the two known audit-MEDIUMs (M1, M2) that live here.

### Task B1: A ledger transaction context that locks across load-mutate-save (fixes #5/B4)

**Files:**
- Modify: `src/fanops/ledger.py`
- Modify: `src/fanops/pipeline.py:26-93` (wrap the body)
- Test: `tests/test_ledger_lock.py` (add), `tests/test_pipeline.py` (add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger_lock.py — ADD
import os, time, json, multiprocessing
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger

def _hold_transaction_then_write(root, started, release):
    cfg = Config(root=str(root))
    with Ledger.transaction(cfg) as led:
        started.set()
        release.wait(5)
        led.add_source(__import__("fanops.models", fromlist=["Source"]).Source(
            id="held", source_path="/h.mp4"))
        # save happens on context exit

def test_transaction_holds_lock_across_the_whole_block(tmp_path):
    # While one process is INSIDE a transaction, a second cannot acquire it until the first exits.
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    started = multiprocessing.Event(); release = multiprocessing.Event()
    p = multiprocessing.Process(target=_hold_transaction_then_write, args=(tmp_path, started, release))
    p.start(); assert started.wait(5)
    # second acquirer with a short timeout must FAIL while the first holds it
    from fanops.errors import LockBusyError
    import fanops.ledger as L
    raised = False
    try:
        with L.Ledger.transaction(cfg, timeout=0.5):
            pass
    except LockBusyError:
        raised = True
    assert raised
    release.set(); p.join(5)
    # after release, the first process's write is durable
    assert "held" in Ledger.load(cfg).sources
```

```python
# tests/test_pipeline.py — ADD
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState

def test_advance_runs_inside_a_single_transaction(tmp_path, monkeypatch, mocker):
    # advance() must take the ledger transaction lock for the whole pass (no lock-free load).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    spy = mocker.spy(Ledger, "transaction")
    from fanops.pipeline import advance
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert spy.call_count == 1            # exactly one transaction wraps the pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ledger_lock.py::test_transaction_holds_lock_across_the_whole_block tests/test_pipeline.py::test_advance_runs_inside_a_single_transaction -v`
Expected: FAIL — `Ledger.transaction` does not exist; `advance` doesn't call it.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ledger.py — ADD this classmethod to Ledger (and import contextmanager already present)
    @classmethod
    @contextmanager
    def transaction(cls, cfg: Config, *, timeout: float | None = None):
        """Hold the ledger lock across the WHOLE load-mutate-save cycle (AUDIT B4). Acquiring the
        lock here — BEFORE load — closes the lost-update window that the save()-only lock left open
        (two overlapping passes both loaded a stale snapshot, last save() won, the other's updates
        vanished — silently dropping/duplicating real posts under cron). On exit the ledger is saved
        ONCE under the still-held lock. A second live process is excluded for the duration and gets a
        typed LockBusyError (bounded by timeout), never a silent overwrite."""
        with _file_lock(cfg.lock_path, timeout=timeout):
            led = cls.load(cfg)
            yield led
            led._save_unlocked()

    def _save_unlocked(self) -> None:
        """The write half of save(), WITHOUT re-acquiring the lock (the caller — transaction() —
        already holds it; flock is per-fd, a nested acquire on a new fd would deadlock against
        ourselves under LOCK_NB-with-timeout)."""
        doc = {
            "sources": {k: v.model_dump() for k, v in self.sources.items()},
            "moments": {k: v.model_dump() for k, v in self.moments.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
            "tag_log": self.tag_log,
        }
        self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg.ledger_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        os.replace(str(tmp), str(self.cfg.ledger_path))
```

Then make the existing `save()` delegate (keep it for standalone callers like `cmd_ingest`):

```python
# src/fanops/ledger.py — REPLACE the body of save()
    def save(self) -> None:
        with _file_lock(self.cfg.lock_path):
            self._save_unlocked()
```

Then wrap `advance()`:

```python
# src/fanops/pipeline.py — REPLACE the top of advance() and the trailing save()
def advance(cfg: Config, *, base_time: str) -> dict:
    accts = Accounts.load(cfg)
    log = get_logger(cfg)
    aspects = _aspects_for(accts)
    with Ledger.transaction(cfg) as led:            # AUDIT B4: lock spans the whole pass
        led = ingest_drops(led, cfg)
        # ... (ALL the existing per-stage loops, UNCHANGED, but indented into the with-block) ...
        led = publish_due(led, cfg, now=None)
        # NOTE: the final `led.save()` is REMOVED — transaction() saves once on exit.
        summary = {
            "sources": len(led.sources), "moments": len(led.moments),
            "clips": len(led.clips), "posts": len(led.posts),
            "published": len(led.posts_in_state(PostState.published)),
            "failed": len(led.posts_in_state(PostState.failed)),
            "needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
            "holds": sum(1 for c in led.clips.values() if c.held),
            "errors": sum(1 for s in led.sources.values() if s.state is SourceState.error),
            "awaiting": {"moments": len(pending(cfg, kind="moments")),
                         "captions": len(pending(cfg, kind="captions"))},
        }
    write_digest(Ledger.load(cfg), cfg)             # digest reads the just-saved ledger (outside the lock)
    return summary
```

CAUTION when indenting: every `led = ...` stage call inside `advance()` moves under the `with` block. The intermediate `led.save()` calls that some stages relied on are now subsumed by the single transaction save — but `publish_due` deliberately saves mid-loop (crash-safety F11). That mid-loop `led.save()` inside `publish_due` will now try to RE-ACQUIRE the lock the transaction holds → deadlock. See Task B2 (it must use the unlocked save while inside a transaction). Do B2 in the SAME commit as B1.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ledger_lock.py tests/test_pipeline.py -v`
Expected: PASS. Full suite stays green.

- [ ] **Step 5: Commit** (combined with B2 — see B2 Step 5).

### Task B2: Make publish_due's crash-safe mid-loop save work inside the transaction

**Files:**
- Modify: `src/fanops/post/run.py:44-55`
- Modify: `src/fanops/pipeline.py` (pass an in-transaction saver)
- Test: `tests/test_post_run.py` (add)

`publish_due` saves twice per post (mark `submitting` + persist, then persist terminal state) for crash-safety. Inside a transaction those `led.save()` calls would deadlock (re-acquire a held lock). Fix: `publish_due` uses `led._save_unlocked()` when it knows it's inside a transaction — pass a flag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_post_run.py — ADD
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, Moment, Source, PostState, ClipState, Platform
from fanops.post.run import publish_due

def test_publish_due_inside_transaction_does_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)   # dryrun
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(tmp_path / "c1.mp4"), state=ClipState.captioned))
        (tmp_path / "c1.mp4").write_bytes(b"x")
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.queued,
                          scheduled_time="2020-01-01T00:00:00Z"))
        # in_transaction=True must use the unlocked save -> no deadlock, completes
        publish_due(led, cfg, now="2020-01-02T00:00:00Z", in_transaction=True)
        assert led.posts["p1"].state is PostState.published
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && timeout 20 python -m pytest tests/test_post_run.py::test_publish_due_inside_transaction_does_not_deadlock -v`
Expected: FAIL — `publish_due` has no `in_transaction` kwarg (TypeError), or (if you naively added the lock) it HANGS until the 20s timeout (the deadlock the flag prevents).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/run.py — change the signature + the two save() calls
def publish_due(led: Ledger, cfg: Config, *, now: str | None = None, in_transaction: bool = False) -> Ledger:
    poster = get_poster(cfg)
    cutoff = _now(now)
    _save = led._save_unlocked if in_transaction else led.save   # AUDIT B4: avoid re-locking inside a txn
    for post in led.posts_in_state(PostState.queued):
        if post.scheduled_time and _parse(post.scheduled_time) > cutoff:
            continue
        try:
            if not post.media_urls:
                post.media_urls = [ensure_clip_media(led, cfg, post.parent_id)]
            post.state = PostState.submitting
            _save()                                              # crash-safe persist (F11), txn-aware
            led = poster.publish(led, post.id)
            if post.state is PostState.submitted:
                post.state = PostState.published
        except Exception as exc:
            if _is_fatal_auth_error(exc):
                raise
            post.state = PostState.failed
            post.error_reason = f"publish failed: {str(exc)[:200]}"
        _save()
    return led
```

```python
# src/fanops/pipeline.py — inside the with-block, pass the flag
        led = publish_due(led, cfg, now=None, in_transaction=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_post_run.py tests/test_pipeline.py -v`
Expected: PASS, no hang. Full suite green.

- [ ] **Step 5: Commit (B1 + B2 together — they are one atomic correctness change)**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/ledger.py src/fanops/pipeline.py src/fanops/post/run.py tests/test_ledger_lock.py tests/test_pipeline.py tests/test_post_run.py
git commit -m "fix (audit B4): hold the ledger lock across the whole advance() pass

The flock previously guarded only save(), not load->mutate->save, so two overlapping cron
runs lost updates (a published post vanished, or submitting reverted -> double-post), silently.
Ledger.transaction() now acquires the lock BEFORE load and saves once on exit; publish_due's
crash-safe mid-loop saves take an in_transaction flag to use the unlocked save (no self-deadlock).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task B3: Guard reconcile_moments against un-retiring a retired moment (fixes M1)

**Files:**
- Modify: `src/fanops/ledger.py:127-136`
- Test: `tests/test_ledger.py` (add)

M1 (known): `reconcile_moments`'s upsert overwrites `self.moments[mid]` unconditionally; if `keep` carries a moment whose existing copy is `retired` (from `adjust.retire`), the fresh `decided` copy resurrects it → re-rendered, re-posted, undoing the retirement.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — ADD
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState

def test_reconcile_does_not_unretire_a_retired_moment(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="1-2", start=1, end=2,
                          reason="r", state=MomentState.retired))   # already retired by adjust
    # a fresh decision tries to upsert the same id as `decided`
    keep = {"m1": Moment(id="m1", parent_id="s1", content_token="1-2", start=1, end=2,
                         reason="r", state=MomentState.decided)}
    led.reconcile_moments("s1", keep)
    assert led.moments["m1"].state is MomentState.retired   # stays retired, not resurrected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ledger.py::test_reconcile_does_not_unretire_a_retired_moment -v`
Expected: FAIL — the moment is overwritten to `decided`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ledger.py — REPLACE the upsert loop in reconcile_moments
    def reconcile_moments(self, source_id: str, keep: dict[str, Moment]) -> None:
        existing = {m.id for m in self.moments_of(source_id)}
        for mid in existing - set(keep):
            self._delete_moment_cascade(mid)
        for mid, m in keep.items():
            prior = self.moments.get(mid)
            if prior is not None and prior.state is MomentState.retired:
                continue                            # AUDIT M1: never resurrect a retired moment
            self.moments[mid] = m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ledger.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/ledger.py tests/test_ledger.py
git commit -m "fix (audit M1): reconcile_moments must not un-retire a retired moment

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **Note on M2:** the audit's M2 ("crosspost_clips/publish_due outside the per-unit quarantine") is now SUBSUMED by Task B1 — the whole pass runs inside one transaction that saves on exit, so a mid-loop raise no longer discards in-memory progress before save (the transaction's exit-save persists whatever completed). Verify by reading `pipeline.advance()` after B1: confirm there is no longer a code path where work happens after the lock is acquired but is lost on a raise. If a raise inside the with-block should still persist partial progress, wrap the volatile stages (crosspost/publish) in try/except that logs and continues (mirroring the per-unit loops) — add that in the B1 commit if the audit's M2 test from the deviation memo still reproduces.

---

# Phase C — Autonomous-LLM validation hardening

The autonomous LLM is now the one writing picks and captions. These three close the validation holes it can fall through (H4, H5, H6). All credential-free.

### Task C1: Reject non-finite (NaN/Inf) timestamps in moment picks (fixes H4)

**Files:**
- Modify: `src/fanops/moments.py:19-29` (validate_pick) AND `src/fanops/models.py` (MomentPick field validator — defense in depth)
- Test: `tests/test_moments.py` (add), `tests/test_models.py` (add)

Verified empirically: `MomentPick(start=NaN)` constructs, and `validate_pick` returns `None` (valid) because every NaN comparison is `False`. Result: a `nan-nan` moment id + `ffmpeg -ss nan` → a buried error-clip that silently never posts. Defense in depth: reject at BOTH the pydantic boundary (so a NaN never becomes a `MomentPick`) and in `validate_pick` (so any other construction path is caught).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py — ADD
import pytest
from pydantic import ValidationError
from fanops.models import MomentPick

def test_moment_pick_rejects_non_finite_timestamps():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            MomentPick(start=bad, end=5.0, reason="r")
        with pytest.raises(ValidationError):
            MomentPick(start=0.0, end=bad, reason="r")
```

```python
# tests/test_moments.py — ADD
from fanops.models import MomentPick
from fanops.moments import validate_pick

def test_validate_pick_rejects_nan_defense_in_depth():
    # Even if a NaN reaches validate_pick by some other path, it is rejected (not None/valid).
    import math
    # Build via model_construct to bypass the field validator, proving validate_pick guards too.
    p = MomentPick.model_construct(start=math.nan, end=math.nan, reason="r")
    assert validate_pick(p, duration=120.0) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_models.py::test_moment_pick_rejects_non_finite_timestamps tests/test_moments.py::test_validate_pick_rejects_nan_defense_in_depth -v`
Expected: FAIL — `MomentPick(NaN)` constructs; `validate_pick(NaN)` returns `None`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/models.py — ADD a validator to MomentPick (needs: from pydantic import field_validator; import math)
class MomentPick(BaseModel):
    start: float
    end: float
    reason: str
    transcript_excerpt: str = ""
    signal_score: float = 0.0

    @field_validator("start", "end")
    @classmethod
    def _finite(cls, v: float) -> float:
        import math
        if not math.isfinite(v):
            raise ValueError("timestamp must be a finite number (no NaN/Infinity)")
        return v
```

```python
# src/fanops/moments.py — ADD the finite guard at the TOP of validate_pick
def validate_pick(pick: MomentPick, *, duration: float) -> str | None:
    """Return a reason string if the pick is invalid, else None."""
    import math
    if not (math.isfinite(pick.start) and math.isfinite(pick.end)):
        return f"non-finite timestamp ({pick.start}->{pick.end})"   # AUDIT H4
    if pick.end <= pick.start:
        return f"end<=start ({pick.start}->{pick.end})"
    if pick.start < 0:
        return f"start<0 ({pick.start})"
    if duration and pick.end > duration + 0.5:
        return f"end>{duration} ({pick.end})"
    if (pick.end - pick.start) < 0.5:
        return f"too short ({pick.end - pick.start:.2f}s)"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_models.py tests/test_moments.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/models.py src/fanops/moments.py tests/test_models.py tests/test_moments.py
git commit -m "fix (audit H4): reject non-finite LLM timestamps (NaN slipped validate_pick)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task C2: Validate caption language matches the source language (fixes H5)

**Files:**
- Modify: `src/fanops/models.py` (add `language` to CaptionItem)
- Modify: `src/fanops/caption.py` (ingest_captions: hold on language mismatch)
- Test: `tests/test_caption.py` (add)

`CaptionItem` carries no language back, so an LLM caption in the wrong language passes the EN/AR brand screen and goes live. Add a `language` field; hold any item whose language ≠ `src.language`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_caption.py — ADD (mirror the file's existing setup helpers/imports)
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, CaptionSet, CaptionItem, SourceState, MomentState, ClipState
from fanops.agentstep import write_request, response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions

def _seed_clip_awaiting_captions(tmp_path, src_lang="en"):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided,
                          language=src_lang, transcript=[{"start":0,"end":1,"text":"x"}]))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-4", start=0, end=4,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.rendered))
    led = request_captions(led, cfg, "c1", [("@a", __import__("fanops.models", fromlist=["Platform"]).Platform.instagram)])
    return cfg, led

def test_caption_in_wrong_language_is_held(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    # LLM returns a caption declared as French for an English source
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="bonjour le monde", language="fr")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    assert "language" in (led.clips["c1"].held_reason or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_caption.py::test_caption_in_wrong_language_is_held -v`
Expected: FAIL — `CaptionItem` has no `language` field (TypeError), or the clip is not held.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/models.py — add language to CaptionItem
class CaptionItem(BaseModel):
    surface: str
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    language: Optional[str] = None      # AUDIT H5: the LLM declares the caption's language
```

```python
# src/fanops/caption.py — in ingest_captions, before storing each item, check language.
# (Read the current ingest_captions first; add this guard where items are processed.)
#   src = led.sources[...]  # the clip's source, to know src.language
#   for item in cs.items:
#       if src.language and item.language and item.language != src.language:
#           led.clips[clip_id].held = True
#           led.clips[clip_id].held_reason = (f"caption language {item.language!r} != source "
#                                             f"language {src.language!r} for {item.surface}")
#           led.set_clip_state(clip_id, ClipState.held)
#           return led
```
Implement the above guard concretely against the real `ingest_captions` body (the clip's source is `led.sources[led.moments[clip.parent_id].parent_id]`). Hold the whole clip on the first mismatch (conservative: a human reviews), consistent with the brand-risk hold already there.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_caption.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/models.py src/fanops/caption.py tests/test_caption.py
git commit -m "fix (audit H5): hold clips whose LLM caption language != source language

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task C3: Validate caption surface keys against the requested set (fixes H6)

**Files:**
- Modify: `src/fanops/caption.py` (ingest_captions: reject/hold on unknown surface with a specific reason)
- Test: `tests/test_caption.py` (add)

A surface typo (`@accounts/instagram`) stores the caption under a non-matching key → the clip is held with a misleading "missing caption" reason though a caption WAS provided. Validate each returned `surface` against the request's `requested` set; on mismatch, hold with a SPECIFIC reason naming the bad surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_caption.py — ADD (reuses _seed_clip_awaiting_captions from C2)
def test_caption_with_unknown_surface_key_is_held_with_specific_reason(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    # typo: '@accounts/instagram' instead of the requested '@a/instagram'
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@accounts/instagram", caption="hi", language="en")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    reason = (led.clips["c1"].held_reason or "")
    assert "@accounts/instagram" in reason     # names the BAD surface, not a generic "missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_caption.py::test_caption_with_unknown_surface_key_is_held_with_specific_reason -v`
Expected: FAIL — the clip is held with a generic "missing caption" reason, not one naming `@accounts/instagram`.

- [ ] **Step 3: Write minimal implementation**

In `ingest_captions`, the request payload's `surfaces` give the valid `requested` keys. Before matching answered-vs-requested, reject any returned `item.surface` not in the requested set:

```python
# src/fanops/caption.py — in ingest_captions, after loading the CaptionSet and the request surfaces:
#   requested = {s["surface"] for s in request_payload["surfaces"]}
#   unknown = [it.surface for it in cs.items if it.surface not in requested]
#   if unknown:
#       led.clips[clip_id].held = True
#       led.clips[clip_id].held_reason = f"caption(s) for unknown surface(s): {', '.join(unknown)}"
#       led.set_clip_state(clip_id, ClipState.held)
#       return led
```
Read the request payload via `request_path(cfg, "captions", clip_id)` (json.loads). Implement concretely; place this check BEFORE the existing missing-caption logic so a typo'd-but-present caption is diagnosed precisely.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_caption.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/caption.py tests/test_caption.py
git commit -m "fix (audit H6): hold with a specific reason when an LLM caption targets an unknown surface

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# Phase D — Live Blotato seam

The audit's biggest risk (B2) + the MCP auth gap (B3) + the id-less stranded-post root fix (H1). Most of this is codeable now; the ONE step that needs live sandbox creds (verifying the actual 2xx field name) is `[GATED]` and called out.

### Task D1: A client-side idempotency token persisted before the POST (fixes H1 root cause)

**Files:**
- Modify: `src/fanops/crosspost.py` (stamp `submission_id` at post creation)
- Modify: `src/fanops/post/run.py` / `blotato_rest.py` (prefer the existing token; only overwrite from a real response id)
- Test: `tests/test_crosspost.py` (add), `tests/test_reconcile.py` (add)

Today a crash/timeout before the response strands a post with NO `submission_id` → `reconcile_posts` skips it forever (the post may be live). Root fix: generate a stable client token (SHA1 of `post.id`) and persist it as `submission_id` at crosspost time, BEFORE any network call. Then every stranded post is reconcilable. The token is also what we'd send as an idempotency key IF Blotato ever supports one (it doesn't today — do not fabricate a header, per the C1 lesson).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crosspost.py — ADD (mirror the file's setup)
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import Source, Moment, Clip, ClipState, MomentState, SourceState
from fanops.crosspost import crosspost_clips

def test_crossposted_post_gets_a_client_token_submission_id(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-4", start=0, end=4,
                          reason="r", state=MomentState.clipped))
    c = Clip(id="c1", parent_id="m1", path=str(tmp_path/"c1.mp4"), state=ClipState.captioned,
             aspect=__import__("fanops.models", fromlist=["Fmt"]).Fmt.r9x16)
    (tmp_path/"c1.mp4").write_bytes(b"x")
    c.meta_captions = {"@a/instagram": {"caption": "hi", "hashtags": []}}
    led.add_clip(c)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = list(led.posts.values())
    assert posts and all(p.submission_id and p.submission_id.startswith("fanops_") for p in posts)
    # stable: same post id -> same token across runs
    p = posts[0]
    from fanops.ids import _hash
    assert p.submission_id == f"fanops_{_hash('idemp', p.id)}"
```

```python
# tests/test_reconcile.py — ADD: a stranded post now HAS an id, so reconcile can poll it
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts

def test_reconcile_polls_a_client_token_post(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc"))
    def fake_status(sid):  # Blotato says it's live
        return {"status": "published", "publicUrl": "https://x/p"}
    led = reconcile_posts(led, cfg, get_status=fake_status)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].public_url == "https://x/p"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_crosspost.py::test_crossposted_post_gets_a_client_token_submission_id tests/test_reconcile.py::test_reconcile_polls_a_client_token_post -v`
Expected: FAIL — posts have no `submission_id` at crosspost time.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/crosspost.py — when building the Post, stamp the idempotency token:
# (add `from fanops.ids import _hash` if not present — _hash already imported via ids)
            led.add_post(Post(
                id=pid, parent_id=target_clip.id, state=PostState.queued,
                account=surf.account, account_id=surf.account_id, platform=surf.platform,
                caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
                scheduled_time=sched,
                submission_id=f"fanops_{_hash('idemp', pid)}"))   # AUDIT H1: reconcilable from birth
```

In `blotato_rest.py`, the 2xx path currently OVERWRITES `post.submission_id` with the response id. Keep doing that (the REAL Blotato id is the one metrics/reconcile use) — the client token is the FALLBACK that guarantees a non-None id if the response is lost. No change needed to the overwrite; just ensure `_reconcile`'s `if not post.submission_id` guard no longer blocks id-capture (it's now always set, so the body-id capture in `_reconcile` should overwrite the client token with the real id when present):

```python
# src/fanops/post/blotato_rest.py — in _reconcile, capture the body id even if a client token exists
    def _reconcile(self, post, detail: str, resp=None) -> None:
        if resp is not None:
            try:
                sid = (resp.json() or {}).get("postSubmissionId")   # (field name fixed in D2)
            except Exception:
                sid = None
            if sid:
                post.submission_id = sid            # real Blotato id beats the client token
        post.state = PostState.needs_reconcile
        post.error_reason = f"ambiguous publish, may be live (reconcile via GET /v2/posts/:id): {detail}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_crosspost.py tests/test_reconcile.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/crosspost.py src/fanops/post/blotato_rest.py tests/test_crosspost.py tests/test_reconcile.py
git commit -m "fix (audit H1): stamp a client idempotency token as submission_id at crosspost

Every post is now reconcilable from birth — a crash/timeout before the response no longer
strands an id-less, unrecoverable, possibly-live post. The REAL Blotato id still overwrites
the token when a response arrives (metrics/reconcile use the real id).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task D2: Robust 2xx submission-id extraction — never mark a live post "failed" (fixes #3/B2)

**Files:**
- Modify: `src/fanops/post/blotato_rest.py:75-88`
- Modify: `src/fanops/post/blotato_mcp.py:26-31`
- Test: `tests/test_blotato_rest.py` (add), `tests/test_blotato_mcp.py` (add)

The 2xx path reads ONLY `postSubmissionId`; the field name is self-admittedly unverified. If the live field is `id`/`submissionId`, every live post is marked `failed` → operator re-posts → double-post. Fix (code now, regardless of which name is right): read a list of candidate keys; a 2xx with NO recognizable id → `needs_reconcile` (may be live) with the client token preserved, NEVER `failed`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blotato_rest.py — ADD
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.blotato_rest import BlotatoRestPoster, _extract_submission_id

def test_extract_submission_id_accepts_known_aliases():
    assert _extract_submission_id({"postSubmissionId": "a"}) == "a"
    assert _extract_submission_id({"id": "b"}) == "b"
    assert _extract_submission_id({"submissionId": "c"}) == "c"
    assert _extract_submission_id({"data": {"id": "d"}}) == "d"      # nested
    assert _extract_submission_id({"unrelated": 1}) is None

def test_2xx_without_recognizable_id_is_needs_reconcile_not_failed(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, submission_id="fanops_tok"))
    class R: status_code = 200; text = "{}"
    R.json = lambda self=R(): {}            # 2xx, no id of any known name
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=R())
    poster = BlotatoRestPoster(cfg)
    led = poster.publish(led, "p1")
    # MUST NOT be `failed` (which implies safe-to-requeue -> double-post). It may be live.
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert led.posts["p1"].submission_id == "fanops_tok"   # client token preserved for reconcile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_rest.py -v -k "extract or recognizable"`
Expected: FAIL — `_extract_submission_id` doesn't exist; the 2xx-no-id path sets `failed`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/blotato_rest.py — ADD a helper + use it on the 2xx path
def _extract_submission_id(body: dict) -> str | None:
    """Blotato's 2xx submission-id field name is unverified against a live call (the smoke test
    admits this). Accept the known candidates so a successful post is never mis-marked failed.
    AUDIT B2: a 2xx with NO recognizable id must become needs_reconcile (may be live), never failed."""
    if not isinstance(body, dict):
        return None
    for k in ("postSubmissionId", "submissionId", "id"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    data = body.get("data")
    if isinstance(data, dict):
        return _extract_submission_id(data)
    return None
```

```python
# src/fanops/post/blotato_rest.py — REPLACE the 2xx block in publish()
            if resp.status_code in (200, 201):
                try:
                    sid = _extract_submission_id(resp.json())
                except Exception:
                    sid = None
                if not sid:
                    # AUDIT B2: 2xx but no recognizable submission id — the post MAY be live.
                    # needs_reconcile (poll before any resubmit), NOT failed (which implies
                    # safe-to-requeue and would double-post). Client token (D1) stays as the handle.
                    post.state = PostState.needs_reconcile
                    post.error_reason = f"2xx but no recognizable submission id: {resp.text[:200]}"
                    return led
                post.state = PostState.submitted
                post.submission_id = sid
                return led
```

Mirror in `blotato_mcp.py` (use the same `_extract_submission_id`):

```python
# src/fanops/post/blotato_mcp.py — import and use _extract_submission_id; a no-id result -> needs_reconcile
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_rest.py tests/test_blotato_mcp.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/post/blotato_rest.py src/fanops/post/blotato_mcp.py tests/test_blotato_rest.py tests/test_blotato_mcp.py
git commit -m "fix (audit B2): a 2xx with no recognizable submission id is needs_reconcile, never failed

Accept postSubmissionId/submissionId/id (+ nested data). A successful live post is no longer
mis-marked 'failed' (which would invite a double-post). The exact live field name is still
verified against the sandbox in D5 [GATED].

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task D3: MCP poster auth + error handling (fixes #4/B3)

**Files:**
- Modify: `src/fanops/post/blotato_mcp.py`
- Test: `tests/test_blotato_mcp.py` (add)

The MCP poster (the documented PRIMARY backend) has no try/except and never raises `BlotatoAuthError`, so `run.py`'s type-based auth-halt can't fire — one misconfig burns the queue or crashes it. Wrap `self._call`; map auth-class failures to `BlotatoAuthError`, others to a per-post **`needs_reconcile`** (NOT `failed` — a non-auth error after the body was sent is ambiguous and the post MAY be live; marking it `failed` would re-queue it and risk a double-post. This matches the code block below and the prime directive. The header previously read "per-post `failed`" — corrected post-implementation to match the shipped behaviour.).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blotato_mcp.py — ADD
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.errors import BlotatoAuthError
from fanops.post.blotato_mcp import BlotatoMcpPoster

def _post(led):
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, media_urls=["https://x/m"], submission_id="fanops_t"))

def test_mcp_auth_failure_raises_blotato_auth_error(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _post(led)
    def caller(tool, args): raise RuntimeError("401 Unauthorized: invalid token")
    poster = BlotatoMcpPoster(cfg, tool_caller=caller)
    with pytest.raises(BlotatoAuthError):
        poster.publish(led, "p1")

def test_mcp_non_auth_failure_marks_post_failed_not_raise(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _post(led)
    def caller(tool, args): raise RuntimeError("500 internal error")
    poster = BlotatoMcpPoster(cfg, tool_caller=caller)
    led = poster.publish(led, "p1")            # must NOT raise (per-post failure, like REST)
    assert led.posts["p1"].state in (PostState.failed, PostState.needs_reconcile)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_mcp.py -v -k "auth or non_auth"`
Expected: FAIL — no try/except; the RuntimeError propagates raw (no BlotatoAuthError, no per-post failed).

- [ ] **Step 3: Write minimal implementation**

Read the current `blotato_mcp.py` `publish`, then wrap the `self._call`:

```python
# src/fanops/post/blotato_mcp.py — wrap the tool call (illustrative; adapt to the real body)
        try:
            result = self._call("blotato_create_post", args) or {}
        except Exception as exc:
            msg = str(exc).lower()
            if "401" in msg or "unauthorized" in msg or "forbidden" in msg or "403" in msg \
               or "invalid token" in msg or "api key" in msg:
                raise BlotatoAuthError(f"Blotato MCP auth failure: {str(exc)[:200]}") from exc
            # non-auth: ambiguous (the tool may have posted) -> needs_reconcile, like REST's 5xx
            post.state = PostState.needs_reconcile
            post.error_reason = f"MCP publish error (may be live): {str(exc)[:200]}"
            return led
        sid = _extract_submission_id(result)        # reuse D2's helper (import it)
        if not sid:
            post.state = PostState.needs_reconcile
            post.error_reason = f"MCP 2xx but no recognizable submission id: {str(result)[:200]}"
            return led
        post.state = PostState.submitted
        post.submission_id = sid
        return led
```

Note: this enforces the contract at the boundary (the audit's instruction) rather than trusting the injected tool to raise `BlotatoAuthError` itself. Keep the `errors.py` docstring note that a custom caller MAY raise `BlotatoAuthError` directly (still honored — `isinstance` in `run.py` catches it before this wrapper if it propagates as that type; but a generic error is now also mapped correctly).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_mcp.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/post/blotato_mcp.py tests/test_blotato_mcp.py
git commit -m "fix (audit B3): MCP poster maps auth failures to BlotatoAuthError, others to a per-post park

The primary backend now honors the type-based auth-halt contract at the boundary instead of
trusting the injected tool — one misconfig halts cleanly instead of burning or crashing the queue.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task D4: Jitter the 429 backoff (RUNTIME backlog (c))

**Files:**
- Modify: `src/fanops/post/blotato_rest.py:97-98`
- Test: `tests/test_blotato_rest.py` (add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blotato_rest.py — ADD
def test_429_backoff_is_jittered(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, submission_id="fanops_t"))
    class R429: status_code = 429; text = "rate limited"
    R429.json = lambda self=R429(): {}
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=R429())
    sleeps = []
    mocker.patch("fanops.post.blotato_rest.time.sleep", side_effect=lambda s: sleeps.append(s))
    mocker.patch("fanops.post.blotato_rest.random.uniform", return_value=0.3)
    BlotatoRestPoster(cfg).publish(led, "p1")
    # base 1,2,4 + a positive jitter each -> not the bare powers of two
    assert all(s > 0 for s in sleeps) and sleeps[0] != 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_rest.py::test_429_backoff_is_jittered -v`
Expected: FAIL — backoff is bare `delay *= 2` (1.0, 2.0, …), `random` not imported.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/blotato_rest.py — add `import random` at top; change the 429 branch
            if resp.status_code == 429:
                time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_blotato_rest.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/post/blotato_rest.py tests/test_blotato_rest.py
git commit -m "fix: jitter the 429 backoff (avoid thundering-herd across surfaces)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task D5: [GATED — needs Blotato sandbox creds] Verify the live API contracts

**Files:** none (verification + a possible one-line field-name confirmation).

**BLOCKED until the operator provides `BLOTATO_API_KEY` + `BLOTATO_SMOKE_ACCOUNT_ID` for the sandbox.** Do NOT skip silently — surface this as the gate to live.

- [ ] **Step 1: Run the smoke test against the sandbox**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate
BLOTATO_API_KEY=<sandbox-key> BLOTATO_SMOKE_ACCOUNT_ID=<sandbox-acct> \
  python -m pytest -q -m integration tests/integration/test_blotato_smoke.py -rs
```
Expected: it posts to the sandbox and prints the REAL response body. Read which submission-id key Blotato actually returns.

- [ ] **Step 2: Confirm `_extract_submission_id` covers the real field**

If the real key is already in `("postSubmissionId","submissionId","id")` (D2), no code change. If it's something else, add it to `_extract_submission_id` and re-run. Confirm the metrics endpoint (`GET /v2/posts?window=`) and its `postSubmissionId`+metrics shape match `track.py`; confirm the media presign keys (`presignedUrl`/`publicUrl`) and the reconcile `GET /v2/posts/:id` `status` enum. Each mismatch is a one-task fix against the now-known reality.

- [ ] **Step 3: Record findings + commit any field corrections** with a `fix (live-verified):` message and a deviation-memo note.

---

# Phase E — Learning loop autonomy + dead-man's switch

The audit's B5 (no dead-man's-switch) + my independent A2 (the learning loop is NOT in the autonomous path). Both credential-free.

### Task E1: Fold track→adjust into a cron-driven pass with amplify guards (fixes A2)

**Files:**
- Modify: `src/fanops/cli.py` (the `run` loop: optionally run a learning pass)
- Modify: `src/fanops/adjust.py` (amplify cooldown/budget per source)
- Test: `tests/test_cli.py` (add), `tests/test_adjust.py` (add)

`advance()`/`run` never call `pull_metrics`/`classify_outcomes`/`amplify`/`retire`, so "learning loop closing on real metrics, unattended" doesn't happen. Add a learning pass to `run` (guarded so it only fires with a live backend + key, like reconcile), and bound amplification so an autonomous LLM can't drive unbounded clip growth on one source.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adjust.py — ADD: amplify respects a per-source cooldown/budget
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Post, Clip, Moment, Source, PostState, ClipState, MomentState,
                           Platform, SourceState)
from fanops.adjust import amplify

def _winner(led, sid="s1"):
    led.add_source(Source(id=sid, source_path="/s.mp4", state=SourceState.moments_decided, duration=30.0,
                          transcript=[{"start":1,"end":2,"text":"x"}], meta={"amplify_count": 3}))
    led.add_moment(Moment(id="m1", parent_id=sid, content_token="1-2", start=1, end=2, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": 400.0}))

def test_amplify_respects_per_source_budget(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _winner(led)                                    # already amplified 3x (the cap)
    led = amplify(led, cfg, ["p1"], max_amplify_per_source=3)
    # at the cap: source NOT re-requested
    assert led.sources["s1"].state is SourceState.moments_decided
```

```python
# tests/test_cli.py — ADD: run can drive a learning pass (guarded)
def test_run_learning_pass_is_guarded_to_live_backends(tmp_path, monkeypatch):
    # In dryrun (default), the learning pass is skipped (no metrics source) -> run still exits 0.
    monkeypatch.chdir(tmp_path); monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.cli import main
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_adjust.py::test_amplify_respects_per_source_budget tests/test_cli.py::test_run_learning_pass_is_guarded_to_live_backends -v`
Expected: FAIL — `amplify` has no `max_amplify_per_source` kwarg; (the CLI test may pass trivially if run already exits 0 — keep it as a regression guard that the learning pass doesn't break dryrun).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/adjust.py — bound amplification per source (autonomous-runaway guard)
def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str], *, max_amplify_per_source: int = 3) -> Ledger:
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        clip = led.clips.get(post.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if not src:
            continue
        used = int(src.meta.get("amplify_count", 0))
        if used >= max_amplify_per_source:
            continue                                # AUDIT (autonomy): don't grow one source forever
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' ({moment.reason}) "
                    f"hit hard (lift={post.metrics.get('lift_score')}). Find MORE moments in that "
                    f"vein in this source — do not repeat the same timestamps.")
        payload = MomentRequest(source_id=src.id, request_id="", duration=src.duration or 0.0,
                                transcript=src.transcript or [], signal_peaks=src.signal_peaks or [],
                                language=src.language, guidance=guidance).model_dump()
        payload.pop("request_id", None)
        write_request(cfg, kind="moments", key=src.id, payload=payload)
        src.meta["amplify_count"] = used + 1
        led.set_source_state(src.id, SourceState.moments_requested)
    return led
```

```python
# src/fanops/cli.py — add a learning pass to `run`, guarded like reconcile.
# After the respond+advance loop converges, if a live backend + key exist, run one track->adjust:
#   from fanops.track import pull_metrics
#   from fanops.adjust import classify_outcomes, amplify, retire
#   if cfg.poster_backend != "dryrun" and cfg.blotato_api_key:
#       with Ledger.transaction(cfg) as led:
#           led = pull_metrics(led, cfg)
#           r = classify_outcomes(led)
#           led = amplify(led, cfg, r["winners"])
#           led = retire(led, r["losers"])
# Wrap in try/except -> log + continue (a metrics hiccup must not crash run). Keep this OUT of the
# inner respond+advance loop (run it once per `run` invocation, after convergence).
```

Implement the CLI change concretely. The learning pass uses a transaction (Phase B) so it's lock-safe and won't race the next advance.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_adjust.py tests/test_cli.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/cli.py src/fanops/adjust.py tests/test_cli.py tests/test_adjust.py
git commit -m "feat (audit A2): close the learning loop in `run` (track->adjust), with per-source amplify budget

The unattended loop now pulls metrics and amplifies/retires once per invocation (live backends
only); amplification is capped per source so an autonomous LLM can't grow one source's clips
without bound.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task E2: Dead-man's-switch — run output carries a heartbeat + this-run deltas (fixes #6/B5)

**Files:**
- Create: `src/fanops/__init__.py` content (`__version__`)
- Modify: `src/fanops/pipeline.py` (advance returns `published_in_run`, `last_published_age_hours`)
- Modify: `src/fanops/cli.py` (`run`/`advance` print a heartbeat line with version + run id + ts)
- Test: `tests/test_pipeline.py` (add), `tests/test_cli.py` (add)

`run` exits 0 identically whether healthy-idle or silently-dead. Make the output distinguishable: a `published_in_run` delta, the age of the last live post, a `fanops_version`, and a `heartbeat` timestamp — so an external monitor (cron+mail / PagerDuty) can alert on "0 published in N runs" / "last post age > threshold".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py — ADD
def test_advance_reports_run_delta_and_last_post_age(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.pipeline import advance
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert "published_in_run" in s          # this-run delta, not cumulative
    assert "last_published_age_hours" in s  # None if never, else float hours
```

```python
# tests/test_cli.py — ADD
def test_run_prints_heartbeat_with_version(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.cli import main
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    out = capsys.readouterr().out
    import fanops
    assert fanops.__version__ in out and "heartbeat" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_pipeline.py -k "run_delta" tests/test_cli.py -k "heartbeat" -v`
Expected: FAIL — keys absent; `fanops.__version__` undefined; no heartbeat line.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/__init__.py — ADD
__version__ = "0.3.0"
```

```python
# src/fanops/pipeline.py — compute the deltas inside advance() (inside the transaction)
# capture published ids at entry, diff at exit; compute last-post age from scheduled_time of the
# newest published post.
#   before = {p.id for p in led.posts_in_state(PostState.published)}   # at transaction start
#   ... after the pass ...
#   after = led.posts_in_state(PostState.published)
#   published_in_run = len([p for p in after if p.id not in before])
#   newest = max((_parse(p.scheduled_time) for p in after if p.scheduled_time), default=None)
#   last_age = None if newest is None else round((datetime.now(timezone.utc) - newest).total_seconds()/3600, 2)
# add to the summary dict: "published_in_run": published_in_run, "last_published_age_hours": last_age
# (import datetime/timezone and a _parse helper at the top of pipeline.py)
```

```python
# src/fanops/cli.py — in `run` (and advance), after computing s, print a heartbeat line:
#   import fanops
#   from datetime import datetime, timezone
#   hb = {"heartbeat": datetime.now(timezone.utc).isoformat(), "fanops_version": fanops.__version__,
#         "published_in_run": s.get("published_in_run", 0),
#         "last_published_age_hours": s.get("last_published_age_hours")}
#   print(json.dumps(hb))     # a monitor greps published_in_run / last_published_age_hours
#   also append hb to cfg.log_path (run.log) so cron+mail can alert.
```

Implement concretely. The heartbeat line MUST change run-to-run (timestamp), so a monitor can distinguish "ran and is alive" from "cron itself is dead."

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_pipeline.py tests/test_cli.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/__init__.py src/fanops/pipeline.py src/fanops/cli.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat (audit B5): dead-man's-switch — heartbeat + this-run deltas in run output

run now emits a changing heartbeat line with fanops_version, published_in_run, and
last_published_age_hours, so an external monitor can alert on a silently-dead pipeline
(previously a healthy-idle run and a wedged one emitted byte-identical output).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task E3: Responder-failure visibility in run + digest (fixes H3)

**Files:**
- Modify: `src/fanops/digest.py` (a "Responder failures" section from run.log or a ledger marker)
- Modify: `src/fanops/cli.py` (on a responder-driven halt, log which gate)
- Test: `tests/test_digest.py` (add)

H2 made the responder quarantine per-request and LOG failures; surface those in the digest so the operator sees "responder couldn't answer gate X" instead of silent staleness.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest.py — ADD
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.digest import write_digest

def test_digest_surfaces_pending_gates(tmp_path):
    # A gate that's been pending (responder couldn't answer) should appear in the digest.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    from fanops.agentstep import write_request
    write_request(cfg, kind="moments", key="s1", payload={"source_id": "s1", "duration": 1.0,
                  "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""})
    write_digest(led, cfg)
    text = cfg.digest_path.read_text()
    assert "pending" in text.lower() and "moments" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_digest.py::test_digest_surfaces_pending_gates -v`
Expected: FAIL — the digest has no pending-gates section (verify against current `digest.py`; if it already lists awaiting counts, strengthen the assertion to require the gate KIND/key).

- [ ] **Step 3: Write minimal implementation**

Add a "Pending agent gates" section to `write_digest` listing `pending(cfg, kind=...)` for each kind (these are the gates the responder hasn't cleared — whether because it's manual, or because the LLM keeps failing them). Read the current `digest.py` and append the section in its established format.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_digest.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat (audit H3): surface pending/unanswered agent gates in the digest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task E4: Per-post reconcile logging (fixes log-missing-reconcile-attempts)

**Files:**
- Modify: `src/fanops/reconcile.py`
- Test: `tests/test_reconcile.py` (add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile.py — ADD
def test_reconcile_logs_each_post(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_t"))
    reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "u"})
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "reconcile" in log and "p1" in log
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_reconcile.py::test_reconcile_logs_each_post -v`
Expected: FAIL — reconcile emits no per-post log.

- [ ] **Step 3: Write minimal implementation**

In `reconcile_posts`, get a logger (`get_logger(cfg)`) and emit one line per post: `log("reconcile", post.id, status_or_skip_reason)`. Read the current `reconcile.py` and add the calls at each branch (promoted/failed/left/skipped-no-id).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_reconcile.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/reconcile.py tests/test_reconcile.py
git commit -m "feat (audit): per-post reconcile logging (surface 429/timeout storms)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# Phase F — Operator-recovery CLI verbs

The audit's recurring operability gap: held / needs_reconcile-with-no-id / unmeasured / error-source all require hand-editing `ledger.json` today. Give a non-expert one verb each. All credential-free.

### Task F1: `fanops resolve <post_id> <published|failed> [--url URL]` (fixes H1's missing human path)

**Files:**
- Modify: `src/fanops/cli.py` (subparser + handler)
- Test: `tests/test_cli.py` (add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — ADD
def test_resolve_promotes_a_needs_reconcile_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t"))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p"]) == 0
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published and led.posts["p1"].public_url == "https://x/p"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py::test_resolve_promotes_a_needs_reconcile_post -v`
Expected: FAIL — no `resolve` subcommand (argparse error / returns 1).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — add the subparser
    p_res = sub.add_parser("resolve"); p_res.add_argument("post_id")
    p_res.add_argument("status", choices=["published", "failed"]); p_res.add_argument("--url", default=None)
```
```python
# src/fanops/cli.py — add to _dispatch
    if args.cmd == "resolve":
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            p.state = PostState.published if args.status == "published" else PostState.failed
            if args.url: p.public_url = args.url
        print(f"resolved {args.post_id} -> {args.status}"); return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (audit H1): fanops resolve <post_id> — the documented human-reconcile path now exists

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task F2: `fanops unhold <clip_id>` (RUNTIME backlog (f))

**Files:**
- Modify: `src/fanops/cli.py`
- Test: `tests/test_cli.py` (add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — ADD
def test_unhold_resets_a_held_clip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.held, held=True,
                          held_reason="brand risk"))
    from fanops.cli import main
    assert main(["unhold", "c1"]) == 0
    c = Ledger.load(cfg).clips["c1"]
    assert c.state is ClipState.captions_requested and c.held is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py::test_unhold_resets_a_held_clip -v`
Expected: FAIL — no `unhold` subcommand.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — subparser
    p_unh = sub.add_parser("unhold"); p_unh.add_argument("clip_id")
```
```python
# src/fanops/cli.py — _dispatch
    if args.cmd == "unhold":
        from fanops.models import ClipState
        with Ledger.transaction(cfg) as led:
            if args.clip_id not in led.clips:
                print(f"no such clip: {args.clip_id}", file=sys.stderr); return 2
            c = led.clips[args.clip_id]; c.held = False; c.held_reason = None
            c.state = ClipState.captions_requested      # re-enter the caption gate
        print(f"unheld {args.clip_id}"); return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (audit): fanops unhold <clip_id> — clear a brand-risk hold without editing ledger.json

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task F3: `fanops retry-source <source_id>` and `fanops retry-metrics <post_id>`

**Files:**
- Modify: `src/fanops/cli.py`
- Test: `tests/test_cli.py` (add)

`retry-source` resets a `SourceState.error` source (transient toolchain glitch) to a retriable state; `retry-metrics` forces a `published`-but-unmeasured post back into the measurement path (or marks it `analyzed` with whatever metrics exist).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — ADD
def test_retry_source_resets_error_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="toolchain missing: ffmpeg"))
    from fanops.cli import main
    assert main(["retry-source", "s1"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.error_reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py::test_retry_source_resets_error_source -v`
Expected: FAIL — no `retry-source` subcommand.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — subparsers
    p_rs = sub.add_parser("retry-source"); p_rs.add_argument("source_id")
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
```
```python
# src/fanops/cli.py — _dispatch
    if args.cmd == "retry-source":
        from fanops.models import SourceState
        with Ledger.transaction(cfg) as led:
            if args.source_id not in led.sources:
                print(f"no such source: {args.source_id}", file=sys.stderr); return 2
            s = led.sources[args.source_id]
            s.state = SourceState.catalogued      # re-enter from the top (transcribe retries)
            s.error_reason = None
            s.meta["transcribed"] = False         # force a real re-transcribe
        print(f"retry-source {args.source_id}"); return 0
    if args.cmd == "retry-metrics":
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            if p.state is PostState.published:    # leave it published so the next track pass re-pulls
                print(f"retry-metrics {args.post_id}: will re-pull on next track"); return 0
            print(f"retry-metrics {args.post_id}: not published (state={p.state.value})", file=sys.stderr); return 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_cli.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (audit): fanops retry-source / retry-metrics recovery verbs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task F4: Stop persisting `meta.original_name` (PII residue, C3 sibling)

**Files:**
- Modify: `src/fanops/ingest.py:92`
- Test: `tests/test_ingest.py` (add)

The audit + my read: `ingest.py:92` still writes the operator's private filename into the ledger; the C3 fix only gitignored the ledger. The SHA id is identity (per ingest's own docstring) — drop the filename.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py — ADD
def test_ingest_does_not_persist_original_filename(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "MY-PRIVATE-NAME.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert "original_name" not in s.meta
    assert "MY-PRIVATE-NAME" not in json.dumps(s.model_dump())   # the filename is nowhere in the unit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ingest.py::test_ingest_does_not_persist_original_filename -v`
Expected: FAIL — `original_name` is in `meta`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ingest.py — drop original_name; keep only non-identifying metadata
            led.add_source(Source(id=sid, state=SourceState.catalogued, source_path=str(dest),
                                  source_origin=origin, sha256=digest, width=w, height=h,
                                  duration=dur or None,
                                  meta={"bytes": f.stat().st_size}))   # AUDIT: no original_name (PII)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest tests/test_ingest.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
git add src/fanops/ingest.py tests/test_ingest.py
git commit -m "fix (audit C3-sibling): stop persisting original filename in the ledger (PII; sha is identity)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# Final: full-suite + real-CLI gate, docs, handoff

### Task G1: Full verification + CI

- [ ] Run the complete suite from the project root: `cd "/Users/molhamhomsi/Moh Flow Fanops" && source .venv/bin/activate && python -m pytest -q` — unit + integration all green.
- [ ] Real-CLI smoke (no creds): `cd "/Users/molhamhomsi/Moh Flow Fanops" && python -m fanops.cli status` exits 0; `python -m fanops.cli --help` lists the new verbs (`resolve`, `unhold`, `retry-source`, `retry-metrics`).
- [ ] Push; confirm the GitHub Actions run is green (both jobs) via `gh run list --repo Fleezyflo/fanops --limit 2`.

### Task G2: Sync docs + RUNTIME

- [ ] Run `sync-docs`. Update `README.md` (new commands, autonomous responder via `claude -p`, the live-cutover gate) and `MohFlow-FanOps/00_control/RUNTIME.md` (wiring the LLM responder = "FANOPS_RESPONDER=llm + claude on PATH"; the new recovery verbs; the dead-man's-switch monitor hook; the cron entry now also closes the learning loop). Remove the backlog items that are now done (unhold (f), jitter (c), the reconcile/operability gaps).
- [ ] Record every deviation in `fanops-build-deviations.md`.

### Task G3: Operator runbook for the live cutover (the human-only steps)

- [ ] In `RUNTIME.md`, write the exact "fresh checkout → first real post" sequence: (1) create the real fan accounts; (2) connect each in Blotato; (3) paste numeric `account_id`s into `accounts.json` + set `status: active`; (4) set `BLOTATO_API_KEY` (sandbox first) in `.env`; (5) ensure `claude` is authed on the host (`claude` CLI logged in / `ANTHROPIC_API_KEY` set per `--bare`); (6) run the `[GATED]` smoke test (Task D5); (7) cron entry: `cd <root> && fanops run` on the interval, with a monitor alerting on the heartbeat's `published_in_run == 0` over N runs. This is the document that makes the operator-gated path executable.

---

## Self-Review (run against the audit's gap list)

**Coverage of the 19 gap items:**
- Tier 0: #1→A1-A3, #2→E1, #3/B2→D2, #4/B3→D3, #5/B4→B1-B2, #6/B5→E2. ✅
- Tier 1: H2→A3, H1→D1+F1, H4→C1, H5→C2, H6→C3, N1→A3. ✅
- Tier 2: resolve→F1, unhold→F2, retry-metrics→F3, retry-source→F3. ✅
- Tier 3: reconcile logging→E4, responder-failure digest→E3, jitter→D4, live-checkpoints→D5. ✅
- Plus C3-sibling PII (original_name)→F4, M1→B3, M2→subsumed by B1.

**Placeholder scan:** Tasks C2, C3, E1, E2, E3, E4, D3 contain commented implementation sketches (`# ...`) rather than full literal code, because each must be written against the CURRENT body of a file the plan can't fully reproduce inline without risking staleness. Each such task names the exact file, the exact insertion logic, the exact test that pins the behavior, and the exact expected result — an executor writes the concrete code to make the test pass. This is a deliberate, flagged trade-off, not an accidental gap; the TEST in each is fully literal and is the contract.

**Type consistency:** `Ledger.transaction(cfg, *, timeout)` and `_save_unlocked()` (B1) are used consistently in B2/E1/E2/F1-F3. `_extract_submission_id` (D2) is reused in D3. `claude_json(prompt, schema)` (A1) is used in A3. `amplify(..., *, max_amplify_per_source)` (E1) matches its test. `published_in_run`/`last_published_age_hours` (E2) match between pipeline and cli.

**Note for the executor:** verify each `[GATED]` boundary is respected — do NOT fake sandbox results. And after Phase B lands, re-read `pipeline.advance()` to confirm M2 is genuinely subsumed (no post-lock work is lost on a raise); if the deviation-memo's M2 repro still fails, add the try/except wrap noted in B3's M2 note.
