# tests/test_llm.py
import json
import pytest
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
