# tests/test_llm.py
import json
import os
import subprocess
import pytest
from fanops.errors import ToolchainMissingError
from fanops.llm import claude_json, _claude_strict_schema

_SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def _grok_ok_env(obj, *, num_turns=1, model_key="grok-4.6-build"):
    return {
        "text": json.dumps(obj),
        "stopReason": "end_turn",
        "sessionId": "s",
        "requestId": "r",
        "num_turns": num_turns,
        "structuredOutput": obj,
        "modelUsage": {model_key: {"modelCalls": 1}},
    }


def _json_schema_reject():
    return type("R", (), {"returncode": 1, "stdout": "",
                          "stderr": "error: unknown option '--json-schema'"})()


def test_claude_strict_schema_rewrites_prefix_items():
    # Claude Code ≥2.1 strict --json-schema rejects draft-2020-12 prefixItems (Pydantic tuple fields).
    raw = {
        "type": "array",
        "prefixItems": [{"type": "number"}, {"type": "number"}],
        "minItems": 2,
        "maxItems": 2,
    }
    out = _claude_strict_schema({"properties": {"segments": {"items": raw}}})
    dump = json.dumps(out)
    assert "prefixItems" not in dump
    seg = out["properties"]["segments"]["items"]
    assert seg["items"] == {"type": "number"}
    assert seg["minItems"] == 2 and seg["maxItems"] == 2


def test_claude_strict_schema_clears_moment_decision_prefix_items():
    from fanops.models import MomentDecision
    raw = MomentDecision.model_json_schema()
    assert "prefixItems" in json.dumps(raw)                 # precondition: pydantic still emits it
    assert "prefixItems" not in json.dumps(_claude_strict_schema(raw))


def test_claude_json_sends_strict_schema_without_prefix_items(mocker):
    from fanops.models import MomentDecision
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"picks": []})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("pick", MomentDecision.model_json_schema())
    cmd = run.call_args[0][0]
    i = cmd.index("--json-schema")
    assert "prefixItems" not in cmd[i + 1]


def test_claude_json_raises_on_nonzero_exit(mocker):
    class R: returncode = 1; stdout = ""; stderr = "auth failed"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError, match="grok failed"):
        claude_json("q", _SCHEMA)

# --- toolchain error: unknown option / unrecognized option / usage: in body -> LlmToolchainError ---

def test_toolchain_error_on_unknown_option_stderr(mocker):
    from fanops.llm import LlmToolchainError
    class R: returncode = 1; stdout = ""; stderr = "error: unknown option '--json-schema'"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)

def test_toolchain_error_on_unrecognized_option_stdout(mocker):
    from fanops.llm import LlmToolchainError
    class R: returncode = 1; stdout = "unrecognized option '--json-schema'"; stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)

def test_toolchain_error_on_usage_output(mocker):
    from fanops.llm import LlmToolchainError
    class R: returncode = 1; stdout = "Usage: claude [options] [command]\n..."; stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)

def test_toolchain_error_on_unknown_command(mocker):
    from fanops.llm import LlmToolchainError
    class R: returncode = 1; stdout = ""; stderr = "unknown command: foo"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)

def test_toolchain_error_is_runtime_error_subclass(mocker):
    # LlmToolchainError subclasses RuntimeError so existing `raises(RuntimeError)` tests stay green
    class R: returncode = 1; stdout = ""; stderr = "unknown argument --json-schema"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError):
        claude_json("q", _SCHEMA)

def test_api_error_still_raises_generic_runtime_not_toolchain(mocker):
    # An API-level error (e.g. auth failure) must NOT be misclassified as LlmToolchainError
    from fanops.llm import LlmToolchainError
    class R: returncode = 1; stdout = ""; stderr = "API connection failed: 500 Internal Server Error"
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError) as exc_info:
        claude_json("q", _SCHEMA)
    assert not isinstance(exc_info.value, LlmToolchainError)

def test_claude_json_raises_on_unparseable_output(mocker):
    from fanops.llm import LlmSchemaError
    class R: returncode = 0; stdout = "not json at all"; stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError, match="could not parse"):
        claude_json("q", _SCHEMA)

def test_claude_json_raises_on_non_object_json(mocker):
    # Valid JSON but not an object (null/array/number/string) must become the typed
    # LlmSchemaError, not a raw AttributeError from env.get(...).
    from fanops.llm import LlmSchemaError
    for stdout in ("null", "[1, 2]", "42", "\"hi\""):
        class R: returncode = 0; stderr = ""
        R.stdout = stdout
        mocker.patch("fanops.llm.subprocess.run", return_value=R())
        with pytest.raises(LlmSchemaError, match="could not parse"):
            claude_json("q", _SCHEMA)

def test_claude_json_hard_failure_not_retried(mocker):
    # a non-rate-limit nonzero exit (e.g. auth) must FAIL FAST — no backoff, no retry.
    class R: returncode = 1; stdout = ""; stderr = "auth failed"
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError, match="grok failed"):
        claude_json("q", _SCHEMA)
    assert run.call_count == 1


# --- MOL-237: _json_candidates + _extract_json_object pure helpers ---

from fanops.llm import _json_candidates, _extract_json_object

class TestJsonCandidates:
    def test_fenced_block_is_first_candidate(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        cands = _json_candidates(text)
        assert cands[0].strip() == '{"a": 1}'

    def test_multiple_fenced_blocks_all_returned(self):
        text = '```json\n{"x": 1}\n```\nand\n```json\n{"y": 2}\n```'
        cands = _json_candidates(text)
        assert len(cands) >= 2
        assert any('"x": 1' in c for c in cands)
        assert any('"y": 2' in c for c in cands)

    def test_bare_brace_object_extracted(self):
        text = 'The answer is {"score": 9, "label": "good"} end.'
        cands = _json_candidates(text)
        assert any('"score": 9' in c for c in cands)

    def test_fenced_blocks_before_bare_braces(self):
        # fenced-block candidates come BEFORE bare-brace candidates
        text = '{"bare": true}\n```json\n{"fenced": true}\n```'
        cands = _json_candidates(text)
        fenced_idx = next(i for i, c in enumerate(cands) if '"fenced"' in c)
        bare_idx = next(i for i, c in enumerate(cands) if '"bare"' in c)
        assert fenced_idx < bare_idx

    def test_empty_text_returns_empty(self):
        assert _json_candidates("") == []
        assert _json_candidates("no braces here") == []

    def test_array_not_included(self):
        # object-only: bare arrays at top level are NOT returned
        cands = _json_candidates("[1, 2, 3]")
        assert all("[" not in c or "{" in c for c in cands)

    def test_nested_braces_balanced(self):
        text = '{"outer": {"inner": 1}}'
        cands = _json_candidates(text)
        assert any('"inner": 1' in c for c in cands)

    def test_fenced_block_without_lang_tag(self):
        # Only ```json tagged fences activate fenced-block extraction;
        # the braces are still reachable via the balanced-brace scan.
        text = '```\n{"x": 1}\n```'
        cands = _json_candidates(text)
        assert any('"x": 1' in c for c in cands)


class TestExtractJsonObject:
    def test_extracts_clean_object(self):
        assert _extract_json_object('{"k": "v"}') == {"k": "v"}

    def test_extracts_object_from_prose(self):
        text = 'Here is the output: {"score": 5, "tag": "good"} thanks.'
        assert _extract_json_object(text) == {"score": 5, "tag": "good"}

    def test_extracts_object_from_fenced_block(self):
        text = 'Result:\n```json\n{"x": 42}\n```\n'
        assert _extract_json_object(text) == {"x": 42}

    def test_returns_none_for_array(self):
        assert _extract_json_object("[1, 2, 3]") is None

    def test_returns_none_for_scalar(self):
        assert _extract_json_object("42") is None
        assert _extract_json_object('"hello"') is None

    def test_returns_none_for_invalid_json(self):
        assert _extract_json_object("not json at all") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_json_object("") is None

    def test_prefers_fenced_over_bare(self):
        # When both a fenced object and a bare object are present,
        # the fenced one (listed first by _json_candidates) is returned.
        text = '{"bare": 1}\n```json\n{"fenced": 2}\n```'
        result = _extract_json_object(text)
        assert result == {"fenced": 2}

    def test_skips_invalid_falls_through_to_valid(self):
        # If the first candidate is invalid JSON, fall through to a valid one.
        text = '```json\nnot-valid\n```\n{"fallback": true}'
        result = _extract_json_object(text)
        assert result == {"fallback": True}

    def test_nested_object(self):
        text = '{"outer": {"inner": [1, 2]}}'
        assert _extract_json_object(text) == {"outer": {"inner": [1, 2]}}


# --- MOL-241: wire JSON-repair into result-resolution tail ---

def test_claude_json_salvages_prose_wrapped_result(mocker):
    from fanops.llm import LlmSchemaError
    picks = {"picks": [{"id": "m1", "score": 0.9}]}   # missing schema required field "x"
    prose = f'Here are my picks: {json.dumps(picks)}'
    env = _grok_ok_env({"x": 1}); del env["structuredOutput"]; env["text"] = prose
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError):
        claude_json("pick moments", _SCHEMA)

def test_claude_json_salvage_logs_warning_breadcrumb(mocker, caplog):
    import logging
    picks = {"x": 7}
    prose = f'prose {json.dumps(picks)}'
    env = _grok_ok_env({"x": 1}); del env["structuredOutput"]; env["text"] = prose
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with caplog.at_level(logging.WARNING, logger="fanops.llm"):
        claude_json("q", _SCHEMA)
    salvage = [r for r in caplog.records if "salvaged via JSON-repair" in r.message]
    assert len(salvage) == 1

def test_claude_json_happy_path_no_salvage_warning(mocker, caplog):
    import logging
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 7})); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with caplog.at_level(logging.WARNING, logger="fanops.llm"):
        assert claude_json("q", _SCHEMA) == {"x": 7}
    assert not any("salvaged via JSON-repair" in r.message for r in caplog.records)

def test_claude_json_raises_schema_error_when_repair_fails(mocker):
    from fanops.llm import LlmSchemaError
    env = _grok_ok_env({"x": 1}); del env["structuredOutput"]; env["text"] = "prose with no json object"
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError, match="no structuredOutput or JSON text"):
        claude_json("q", _SCHEMA)

def test_claude_json_no_finalizer_without_images_even_when_repair_fails(mocker):
    from fanops.llm import LlmSchemaError
    env = _grok_ok_env({"x": 1}); del env["structuredOutput"]; env["text"] = "prose with no json object"
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps(env), "stderr": ""})())
    with pytest.raises(LlmSchemaError):
        claude_json("q", _SCHEMA)
    assert run.call_count == 1


# --- MOL-247: _extract_json_object salvages fenced + balanced-brace prose ---

def test_extract_json_object_from_prose():
    expected = {"winner": "clip_a", "score": 0.85}
    fenced = f'Here is my analysis:\n```json\n{json.dumps(expected)}\n```\nThanks.'
    unfenced = f'The result is {json.dumps(expected)} as requested.'
    assert _extract_json_object(fenced) == expected
    assert _extract_json_object(unfenced) == expected


def test_no_json_object_raises_llm_schema_error(mocker):
    from fanops.llm import LlmSchemaError
    env = _grok_ok_env({"x": 1}); del env["structuredOutput"]; env["text"] = "pure prose with no extractable object"
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError, match="no structuredOutput or JSON text"):
        claude_json("q", _SCHEMA)


# --- grok transport ---

def test_dispatch_routes_grok(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    monkeypatch.delenv("FANOPS_LLM_MODEL", raising=False)
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert claude_json("q", _SCHEMA) == {"x": 1}
    cmd = run.call_args[0][0]
    assert cmd[0] == "grok"
    assert "--no-auto-update" in cmd
    assert "-p" not in cmd
    assert "--prompt-file" in cmd
    assert "--json-schema" in cmd
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert "--disable-web-search" in cmd and "--no-subagents" in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--allowedTools" not in cmd
    assert "--bare" not in cmd
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "grok-4.6"   # pin unset → default
    assert "q" not in cmd
    assert run.call_args.kwargs.get("input") in (None, "")

def test_grok_pops_xai_api_key_from_child_env(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    monkeypatch.setenv("XAI_API_KEY", "xai-should-not-leak")
    monkeypatch.setenv("GROK_CODE_XAI_API_KEY", "also-no")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    child = run.call_args.kwargs["env"]
    assert "XAI_API_KEY" not in child
    assert "GROK_CODE_XAI_API_KEY" not in child

def test_grok_prompt_file_not_argv(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 3})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("SECRET_TRANSCRIPT_TOKEN", _SCHEMA)
    cmd = run.call_args[0][0]
    assert "SECRET_TRANSCRIPT_TOKEN" not in cmd
    pf = cmd[cmd.index("--prompt-file") + 1]
    assert pf.startswith("/")

def test_grok_structuredOutput_preferred(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    env = _grok_ok_env({"x": 7}); env["text"] = "prose that is not the object"
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert claude_json("q", _SCHEMA) == {"x": 7}

def test_grok_text_json_when_no_structuredOutput(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    env = _grok_ok_env({"x": 4}); del env["structuredOutput"]
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert claude_json("q", _SCHEMA) == {"x": 4}

def test_grok_answered_model_prefers_modelUsage_key(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    env = _grok_ok_env({"x": 1}, model_key="grok-4.6-build")
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    from fanops.llm import claude_json_meta
    _, model, _ = claude_json_meta("q", _SCHEMA, model="grok-4.5")
    assert model == "grok-4.6-build"

def test_grok_answered_model_falls_back_to_pin_without_modelUsage(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    env = _grok_ok_env({"x": 1}); del env["modelUsage"]
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    from fanops.llm import claude_json_meta
    _, model, _ = claude_json_meta("q", _SCHEMA, model="grok-4.5")
    assert model == "grok-4.5"

def test_grok_never_passes_opus_sonnet(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA, model="opus")
    cmd = run.call_args[0][0]
    assert cmd[cmd.index("-m") + 1] == "grok-4.6"
    assert "opus" not in cmd and "sonnet" not in cmd

def test_grok_missing_binary(mocker, monkeypatch):
    from fanops.errors import ToolchainMissingError
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    def absent(cmd, **kw): raise FileNotFoundError(2, "No such file", cmd[0])
    mocker.patch("fanops.llm.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="grok") as ei:
        claude_json("q", _SCHEMA)
    assert "FANOPS_LLM_TRANSPORT=claude" not in str(ei.value)

def test_grok_unknown_model_is_toolchain_error(mocker, monkeypatch):
    from fanops.llm import LlmToolchainError
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R:
        returncode = 1
        stdout = json.dumps({"type": "error", "message": "Couldn't set model 'x': Invalid params: \"unknown model id\""})
        stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmToolchainError, match="grok"):
        claude_json("q", _SCHEMA)

def test_grok_rate_limit_raises_typed_after_retries(mocker, monkeypatch):
    from fanops.llm import LlmRateLimitError
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class RL:
        returncode = 1
        stdout = json.dumps({"type": "error", "message": "rate limit 429"})
        stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=RL())
    mocker.patch("fanops.llm._sleep")
    with pytest.raises(LlmRateLimitError):
        claude_json("q", _SCHEMA)
    assert run.call_count == 5   # _MAX_RL_RETRIES 4 → 5 attempts

def test_grok_rate_limit_backoff_then_success(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class RL:
        returncode = 1
        stdout = json.dumps({"type": "error", "message": "rate limit 429"})
        stderr = ""
    class OK:
        returncode = 0
        stdout = json.dumps(_grok_ok_env({"x": 3}))
        stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=[RL(), RL(), OK()])
    sleep = mocker.patch("fanops.llm._sleep")
    assert claude_json("q", _SCHEMA) == {"x": 3}
    assert run.call_count == 3 and sleep.call_count == 2

def test_grok_success_envelope_digits_are_not_rate_limit(mocker, monkeypatch):
    # F0 success JSON is numeric-heavy (usage, cost, ids). Marker digits 429/503/529 must
    # not fire on rc=0 — that would retry a good captions call into LlmRateLimitError.
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    env = _grok_ok_env({"x": 1})
    env["usage"] = {"input_tokens": 1429, "output_tokens": 503}
    env["total_cost_usd"] = 0.0429
    env["requestId"] = "req-429-503-529"
    env["sessionId"] = "s529"
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    sleep = mocker.patch("fanops.llm._sleep")
    assert claude_json("q", _SCHEMA) == {"x": 1}
    assert run.call_count == 1 and sleep.call_count == 0

def test_grok_isolation_env_zeros(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    env = run.call_args.kwargs["env"]
    for k in ("GROK_CLAUDE_HOOKS_ENABLED", "GROK_CLAUDE_MCPS_ENABLED",
              "GROK_CLAUDE_SKILLS_ENABLED", "GROK_CLAUDE_AGENTS_ENABLED",
              "GROK_CURSOR_HOOKS_ENABLED", "GROK_CURSOR_MCPS_ENABLED"):
        assert env[k] == "0"
    assert env["GROK_DISABLE_AUTOUPDATER"] == "1"

def test_grok_cwd_is_fanops_tempdir(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    cwd = run.call_args.kwargs["cwd"]
    assert os.path.basename(cwd).startswith("fanops-grok-")
    cmd = run.call_args[0][0]
    assert "--cwd" in cmd and cmd[cmd.index("--cwd") + 1] == cwd

def test_grok_output_format_json(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    cmd = run.call_args[0][0]
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"

def test_grok_prompt_file_mode_is_0600(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    modes = []
    def _run(cmd, **kw):
        pf = cmd[cmd.index("--prompt-file") + 1]
        modes.append(os.stat(pf).st_mode & 0o777)
        return R()
    mocker.patch("fanops.llm.subprocess.run", side_effect=_run)
    claude_json("q", _SCHEMA)
    assert modes == [0o600]

def test_grok_timeout_raises_llm_timeout_error(mocker, monkeypatch):
    from fanops.llm import LlmTimeoutError
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    mocker.patch("fanops.llm.subprocess.run", side_effect=subprocess.TimeoutExpired("grok", 1))
    with pytest.raises(LlmTimeoutError, match="grok") as ei:
        claude_json("q", _SCHEMA)
    assert type(ei.value) is LlmTimeoutError

def test_grok_models_ok_argv(mocker, monkeypatch):
    from tests.conftest import _REAL_GROK_MODELS_OK
    monkeypatch.setenv("XAI_API_KEY", "xai-should-not-leak")
    monkeypatch.setenv("GROK_CODE_XAI_API_KEY", "also-no")
    class R: returncode = 0; stdout = ""; stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert _REAL_GROK_MODELS_OK() is True
    assert run.call_args[0][0] == ["grok", "--no-auto-update", "models"]
    env = run.call_args.kwargs["env"]
    assert "XAI_API_KEY" not in env
    assert "GROK_CODE_XAI_API_KEY" not in env
    for k in ("GROK_CLAUDE_HOOKS_ENABLED", "GROK_CLAUDE_MCPS_ENABLED",
              "GROK_CLAUDE_SKILLS_ENABLED", "GROK_CLAUDE_AGENTS_ENABLED",
              "GROK_CURSOR_HOOKS_ENABLED", "GROK_CURSOR_MCPS_ENABLED"):
        assert env[k] == "0"
    assert env["GROK_DISABLE_AUTOUPDATER"] == "1"
    assert run.call_args.kwargs["timeout"] == 15
    run.side_effect = subprocess.TimeoutExpired("grok", 15)
    assert _REAL_GROK_MODELS_OK() is False
    run.side_effect = FileNotFoundError(2, "No such file", "grok")
    assert _REAL_GROK_MODELS_OK() is False


def test_grok_json_schema_retry_writes_schema_into_prompt_file(mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_LLM_TRANSPORT", "grok")
    ok = type("R", (), {"returncode": 0, "stdout": json.dumps(_grok_ok_env({"x": 3})), "stderr": ""})()
    prompt_bodies = []
    def _run(cmd, **kw):
        pf = cmd[cmd.index("--prompt-file") + 1]
        with open(pf) as f:
            prompt_bodies.append(f.read())
        return _json_schema_reject() if len(prompt_bodies) == 1 else ok
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=_run)
    assert claude_json("q", _SCHEMA) == {"x": 3}
    assert run.call_count == 2
    first_cmd, retry_cmd = run.call_args_list[0][0][0], run.call_args_list[1][0][0]
    assert "--json-schema" in first_cmd
    assert "--json-schema" not in retry_cmd
    for cmd, call in zip((first_cmd, retry_cmd), run.call_args_list):
        assert "--prompt-file" in cmd
        assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
        assert call.kwargs.get("input") in (None, "")
    assert json.dumps(_SCHEMA) in prompt_bodies[1]
    assert "ONLY a single JSON object" in prompt_bodies[1]

def test_json_schema_fallback_logs_warning_breadcrumb(mocker, caplog):
    import logging
    ok = type("R", (), {"returncode": 0, "stdout": json.dumps(_grok_ok_env({"x": 1})), "stderr": ""})()
    mocker.patch("fanops.llm.subprocess.run", side_effect=[_json_schema_reject(), ok])
    with caplog.at_level(logging.WARNING, logger="fanops.llm"):
        claude_json("q", _SCHEMA)
    assert any("--json-schema" in r.message for r in caplog.records)

def test_json_schema_fallback_retry_failure_keeps_classification(mocker):
    # the retry's own failure surfaces through the existing typed classification (here: hard failure)
    hard = type("R", (), {"returncode": 1, "stdout": "", "stderr": "auth failed"})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=[_json_schema_reject(), hard])
    with pytest.raises(RuntimeError, match="grok failed"):
        claude_json("q", _SCHEMA)
    assert run.call_count == 2

def test_json_schema_persistent_reject_raises_toolchain_no_loop(mocker):
    # a CLI that errors identically on the flagless retry raises the typed toolchain error —
    # exactly one retry, never a loop.
    from fanops.llm import LlmToolchainError
    run = mocker.patch("fanops.llm.subprocess.run",
                       side_effect=[_json_schema_reject(), _json_schema_reject()])
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)
    assert run.call_count == 2

def test_other_toolchain_errors_do_not_trigger_fallback(mocker):
    # only a --json-schema rejection retries; any other unknown option fails fast as before.
    from fanops.llm import LlmToolchainError
    bad = type("R", (), {"returncode": 1, "stdout": "",
                         "stderr": "error: unknown option '--frobnicate'"})()
    run = mocker.patch("fanops.llm.subprocess.run", return_value=bad)
    with pytest.raises(LlmToolchainError):
        claude_json("q", _SCHEMA)
    assert run.call_count == 1


# --- D2/D3: grok is the only constructible transport, including vision ---

def test_claude_json_meta_dispatch_never_shells_claude(mocker, monkeypatch):
    from fanops.llm import claude_json_meta
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    for val in ("claude", "cursor", None):
        if val is None:
            monkeypatch.delenv("FANOPS_LLM_TRANSPORT", raising=False)
        else:
            monkeypatch.setenv("FANOPS_LLM_TRANSPORT", val)
        run.reset_mock()
        claude_json_meta("q", _SCHEMA)
        cmd = run.call_args[0][0]
        assert cmd[0] == "grok"
        assert "claude" not in cmd and "cursor-agent" not in cmd

def test_grok_vision_uses_prompt_json_inline_data(mocker, tmp_path):
    import base64
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8\xfftiny")
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("judge", _SCHEMA, images=[str(jpeg)])
    cmd = run.call_args[0][0]
    assert cmd[0] == "grok"
    assert "--prompt-json" in cmd
    assert "--prompt-file" not in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    payload = json.loads(cmd[cmd.index("--prompt-json") + 1])
    imgs = [b for b in payload if b.get("type") == "image"]
    assert len(imgs) == 1
    assert imgs[0]["data"] == base64.b64encode(jpeg.read_bytes()).decode("ascii")
    assert imgs[0]["data"]
    assert imgs[0]["mimeType"] == "image/jpeg"
    dumped = json.dumps(payload)
    assert "file://" not in dumped

def test_grok_captions_still_prompt_file(mocker):
    class R: returncode = 0; stdout = json.dumps(_grok_ok_env({"x": 1})); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA, images=None)
    cmd = run.call_args[0][0]
    assert "--prompt-file" in cmd
    assert "--prompt-json" not in cmd

def test_grok_vision_json_schema_retry_stays_prompt_json(mocker, tmp_path):
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8\xfftiny")
    ok = type("R", (), {"returncode": 0, "stdout": json.dumps(_grok_ok_env({"x": 3})), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=[_json_schema_reject(), ok])
    assert claude_json("judge", _SCHEMA, images=[str(jpeg)]) == {"x": 3}
    assert run.call_count == 2
    first_cmd, retry_cmd = run.call_args_list[0][0][0], run.call_args_list[1][0][0]
    for cmd in (first_cmd, retry_cmd):
        assert "--prompt-json" in cmd
        assert "--prompt-file" not in cmd
    retry_payload = json.loads(retry_cmd[retry_cmd.index("--prompt-json") + 1])
    texts = [b["text"] for b in retry_payload if b.get("type") == "text"]
    assert texts and json.dumps(_SCHEMA) in texts[0]

def test_grok_vision_argv_over_argmax_is_context_limit(mocker, tmp_path):
    from fanops.llm import LlmContextLimitError
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8\xfftiny")
    run = mocker.patch("fanops.llm.subprocess.run")
    with pytest.raises(LlmContextLimitError):
        claude_json("x" * 900_000, _SCHEMA, images=[str(jpeg)])
    assert run.call_count == 0

def test_grok_e2big_is_context_limit_not_missing_binary(mocker):
    import errno
    from fanops.llm import LlmContextLimitError
    def boom(cmd, **kw):
        raise OSError(errno.E2BIG, "Argument list too long")
    mocker.patch("fanops.llm.subprocess.run", side_effect=boom)
    with pytest.raises(LlmContextLimitError):
        claude_json("q", _SCHEMA)
    # ToolchainMissingError is not a parent of LlmContextLimitError; the raise type is the pin.
    assert not issubclass(LlmContextLimitError, ToolchainMissingError)
