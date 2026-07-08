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
    assert cmd[0] == "claude" and "-p" in cmd
    # AUTH (operator decision 2026-06-04): use the EXISTING `claude` subscription/login (OAuth),
    # NOT an API key. `--bare` is therefore REMOVED — under --bare, claude reads auth strictly from
    # ANTHROPIC_API_KEY and IGNORES the OAuth/keychain login, so a logged-in `claude` would still
    # fail "Not logged in". Plain `claude -p` uses the existing session. We keep it a clean
    # generator with --strict-mcp-config (no MCP servers bleed into the decision) + --allowedTools "".
    assert "--bare" not in cmd
    assert "--strict-mcp-config" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--json-schema" in cmd
    i = cmd.index("--allowedTools"); assert cmd[i + 1] == ""   # pure generator

def test_claude_json_with_images_allows_read_and_references_paths(mocker):
    # The vision-grounded hook editor must SEE frames: with images, the call grants the Read tool and
    # names the frame paths in the prompt so the model reads them (proven viable in the Task 0a spike).
    envelope = {"structured_output": {"x": 5}, "result": "", "session_id": "s"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    out = claude_json("judge these", _SCHEMA, images=["/tmp/a.jpg", "/tmp/b.jpg"])
    assert out == {"x": 5}
    cmd = run.call_args[0][0]
    i = cmd.index("--allowedTools"); assert cmd[i + 1] == "Read"          # vision needs the Read tool
    # ECC fix #11: the prompt now rides STDIN (input=), not argv — assert against the kwarg
    prompt = run.call_args.kwargs["input"]
    assert "/tmp/a.jpg" in prompt and "/tmp/b.jpg" in prompt              # told which frames to read
    assert "hook" not in prompt.lower()                                   # MOL-251: gate-neutral wrapper
    assert "ONLY the JSON" in prompt and "no prose" in prompt.lower()

def test_claude_json_vision_reask_wrapper_gate_neutral(mocker):
    # MOL-251: the re-ask string is also gate-neutral — no hook-specific wording.
    from fanops.llm import claude_json_meta
    seq = iter([json.dumps({"structured_output": {"x": 1}, "num_turns": 1}),
                json.dumps({"structured_output": {"x": 2}, "num_turns": 3})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    claude_json_meta("pick", _SCHEMA, images=["/f/1.jpg"])
    reask = run.call_args_list[1].kwargs["input"]
    assert "hook" not in reask.lower()
    assert "You did NOT open the frames" in reask
    assert "ONLY the JSON" in reask and "no prose" in reask.lower()

def test_claude_json_without_images_stays_pure_generator(mocker):
    # Regression: the default (text-only) path is byte-identical — no Read tool, no file access.
    envelope = {"structured_output": {"x": 1}}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    cmd = run.call_args[0][0]
    i = cmd.index("--allowedTools"); assert cmd[i + 1] == ""

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

def _rl_envelope():
    # what `claude -p` actually emits when rate-limited (observed live): rc=1 + an envelope on stdout
    # carrying api_error_status 429. The creative responder used to treat this as a generic failure
    # and silently produce nothing.
    return json.dumps({"type": "result", "subtype": "success", "is_error": True,
                       "api_error_status": 429, "result": "rate limited"})

def test_claude_json_retries_on_rate_limit_then_succeeds(mocker):
    from fanops.llm import claude_json as cj
    class RL: returncode = 1; stdout = _rl_envelope(); stderr = ""
    class OK: returncode = 0; stdout = json.dumps({"structured_output": {"x": 3}}); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=[RL(), RL(), OK()])
    sleep = mocker.patch("fanops.llm._sleep")                  # don't actually wait in tests
    assert cj("q", _SCHEMA) == {"x": 3}                        # succeeds after backing off the 429s
    assert run.call_count == 3 and sleep.call_count == 2       # retried twice, slept before each retry

def test_claude_json_raises_typed_error_on_persistent_rate_limit(mocker):
    from fanops.llm import LlmRateLimitError
    class RL: returncode = 1; stdout = _rl_envelope(); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=RL())
    mocker.patch("fanops.llm._sleep")
    with pytest.raises(LlmRateLimitError):                     # typed, not a generic RuntimeError
        claude_json("q", _SCHEMA)

def test_claude_json_hard_failure_not_retried(mocker):
    # a non-rate-limit nonzero exit (e.g. auth) must FAIL FAST — no backoff, no retry.
    class R: returncode = 1; stdout = ""; stderr = "auth failed"
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    sleep = mocker.patch("fanops.llm._sleep")
    with pytest.raises(RuntimeError, match="claude -p failed"):
        claude_json("q", _SCHEMA)
    assert run.call_count == 1 and sleep.call_count == 0

# --- V2 M1/F1 cycle 2: pin the model + per-call provenance (claude_json_meta) ---

def test_claude_json_passes_model_and_keeps_bare_dict_contract(mocker):
    # F1: pass the pinned model through to `claude -p --model`. claude_json's RETURN stays a bare dict
    # (audit C2: studio/actions.py binds `model = claude_json` and calls it expecting a dict — a
    # tuple-return there would TypeError; so the model-aware path is the sibling claude_json_meta).
    envelope = {"structured_output": {"x": 3}, "model": "claude-opus-4-x", "session_id": "s"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    out = claude_json("pick", _SCHEMA, model="opus")
    assert out == {"x": 3}                                     # unchanged contract: bare dict
    cmd = run.call_args[0][0]
    i = cmd.index("--model"); assert cmd[i + 1] == "opus"      # pinned model reaches the CLI
    j = cmd.index("--allowedTools"); assert cmd[j + 1] == ""   # the allowedTools pair stays intact (audit H)

def test_claude_json_no_model_omits_flag(mocker):
    # Default path (no model) is byte-compatible: no --model flag, existing callers unaffected.
    envelope = {"structured_output": {"x": 1}}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    run = mocker.patch("fanops.llm.subprocess.run", return_value=R())
    claude_json("q", _SCHEMA)
    assert "--model" not in run.call_args[0][0]

def test_claude_json_meta_returns_resolved_model(mocker):
    # The provenance path: claude_json_meta returns (dict, resolved_model). When the envelope reports
    # the model that actually answered, surface THAT (the true audit trail).
    from fanops.llm import claude_json_meta
    envelope = {"structured_output": {"x": 9}, "model": "claude-opus-4-x", "session_id": "s"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    out, model, _ = claude_json_meta("pick", _SCHEMA, model="opus")
    assert out == {"x": 9} and model == "claude-opus-4-x"

def test_claude_json_meta_falls_back_to_configured_model_when_envelope_lacks_it(mocker):
    # Audit C2/H: the `claude -p` envelope may NOT expose a `model` key (the test envelopes never did).
    # Defensive fallback: report the configured/pinned value so the provenance line is never empty/crash.
    from fanops.llm import claude_json_meta
    envelope = {"structured_output": {"x": 1}, "session_id": "s"}   # no "model" key
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    out, model, _ = claude_json_meta("pick", _SCHEMA, model="opus")
    assert out == {"x": 1} and model == "opus"


# --- HOOK-TRANSPORT: verify the vision author OPENED the frames (num_turns), re-ask once on a miss ---
def test_claude_json_meta_reasks_when_frames_unread(mocker):
    # images given but the model answered text-only (num_turns=1 -> Read never fired) -> re-ask ONCE; the
    # second, frame-reading answer (num_turns>=2) is the one returned.
    from fanops.llm import claude_json_meta
    seq = iter([json.dumps({"structured_output": {"x": 1}, "num_turns": 1}),
                json.dumps({"structured_output": {"x": 2}, "num_turns": 3})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    out, _, _ = claude_json_meta("hook", _SCHEMA, images=["/f/1.jpg"])
    assert out == {"x": 2} and run.call_count == 2          # re-asked; the frame-reading answer won


def test_claude_json_meta_no_reask_when_frames_read(mocker):
    from fanops.llm import claude_json_meta
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps({"structured_output": {"x": 9}, "num_turns": 2}), "stderr": ""})())
    out, _, _ = claude_json_meta("hook", _SCHEMA, images=["/f/1.jpg"])
    assert out == {"x": 9} and run.call_count == 1          # frames read first try -> no re-ask


def test_claude_json_meta_no_reask_without_images(mocker):
    # the no-image path never re-asks (num_turns ignored) -> byte-identical single call.
    from fanops.llm import claude_json_meta
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps({"structured_output": {"x": 5}, "num_turns": 1}), "stderr": ""})())
    out, _, _ = claude_json_meta("pick", _SCHEMA)
    assert out == {"x": 5} and run.call_count == 1


# ---- AGENT-9: claude_json_meta surfaces the frames-unread signal; claude_json bare-dict unaffected ----
def test_claude_json_meta_reports_frames_unread_after_reask(mocker):
    env = {"structured_output": {"hook": "x"}, "num_turns": 1, "model": "opus"}   # num_turns<=1 twice -> unread
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    from fanops.llm import claude_json_meta
    out, model, unread = claude_json_meta("author a hook", {"type": "object"}, images=["/tmp/a.jpg"])
    assert unread is True                                  # the degraded, text-grounded signal is RETURNED
    assert out == {"hook": "x"}

def test_claude_json_meta_frames_read_not_unread(mocker):
    env = {"structured_output": {"hook": "x"}, "num_turns": 2, "model": "opus"}   # a Read turn fired -> read
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    from fanops.llm import claude_json_meta
    _out, _model, unread = claude_json_meta("p", {"type": "object"}, images=["/tmp/a.jpg"])
    assert unread is False

def test_claude_json_bare_dict_unaffected(mocker):
    env = {"structured_output": {"x": 1}, "num_turns": 2}
    class R: returncode = 0; stdout = json.dumps(env); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    from fanops.llm import claude_json
    assert claude_json("p", {"type": "object"}, images=["/tmp/a.jpg"]) == {"x": 1}   # still a plain dict


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
        text = '{"bare": true}\n```json\n{"fenced": true}\n```'
        cands = _json_candidates(text)
        fenced_idx = next(i for i, c in enumerate(cands) if '"fenced"' in c)
        bare_idx = next(i for i, c in enumerate(cands) if '"bare"' in c)
        assert fenced_idx < bare_idx

    def test_empty_text_returns_empty(self):
        assert _json_candidates("") == []
        assert _json_candidates("no braces here") == []

    def test_array_not_included(self):
        cands = _json_candidates("[1, 2, 3]")
        assert all("[" not in c or "{" in c for c in cands)

    def test_nested_braces_balanced(self):
        text = '{"outer": {"inner": 1}}'
        cands = _json_candidates(text)
        assert any('"inner": 1' in c for c in cands)

    def test_fenced_block_without_lang_tag(self):
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
        text = '{"bare": 1}\n```json\n{"fenced": 2}\n```'
        assert _extract_json_object(text) == {"fenced": 2}

    def test_skips_invalid_falls_through_to_valid(self):
        text = '```json\nnot-valid\n```\n{"fallback": true}'
        assert _extract_json_object(text) == {"fallback": True}

    def test_nested_object(self):
        text = '{"outer": {"inner": [1, 2]}}'
        assert _extract_json_object(text) == {"outer": {"inner": [1, 2]}}


# --- MOL-241: wire JSON-repair into result-resolution tail ---

def test_claude_json_salvages_prose_wrapped_result(mocker):
    picks = {"picks": [{"id": "m1", "score": 0.9}]}
    prose = f'Here are my picks: {json.dumps(picks)}'
    envelope = {"structured_output": None, "result": prose, "session_id": "s"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    assert claude_json("pick moments", _SCHEMA) == picks

def test_claude_json_salvage_logs_warning_breadcrumb(mocker, caplog):
    import logging
    picks = {"x": 7}
    prose = f'prose {json.dumps(picks)}'
    envelope = {"structured_output": None, "result": prose}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with caplog.at_level(logging.WARNING, logger="fanops.llm"):
        claude_json("q", _SCHEMA)
    salvage = [r for r in caplog.records if "salvaged via JSON-repair" in r.message]
    assert len(salvage) == 1

def test_claude_json_happy_path_no_salvage_warning(mocker, caplog):
    import logging
    envelope = {"structured_output": {"x": 7}, "result": '{"x": 7}'}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with caplog.at_level(logging.WARNING, logger="fanops.llm"):
        assert claude_json("q", _SCHEMA) == {"x": 7}
    assert not any("salvaged via JSON-repair" in r.message for r in caplog.records)

def test_claude_json_raises_schema_error_when_repair_fails(mocker):
    from fanops.llm import LlmSchemaError
    envelope = {"structured_output": None, "result": "prose with no json object"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError, match="was not JSON"):
        claude_json("q", _SCHEMA)


# --- MOL-248: no-tools finalizer turn on vision path when repair empty ---

def test_claude_json_meta_vision_prose_triggers_one_no_tools_finalizer(mocker):
    from fanops.llm import claude_json_meta
    prose = "I see the frames but here is my reasoning, not JSON."
    seq = iter([json.dumps({"structured_output": None, "result": prose, "num_turns": 2}),
                json.dumps({"structured_output": {"x": 4}, "num_turns": 1})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    out, _, _ = claude_json_meta("judge", _SCHEMA, images=["/f/1.jpg"])
    assert out == {"x": 4}
    assert run.call_count == 2
    final_cmd = run.call_args_list[1][0][0]
    i = final_cmd.index("--allowedTools"); assert final_cmd[i + 1] == ""
    final_prompt = run.call_args_list[1].kwargs["input"]
    assert "ONLY" in final_prompt and json.dumps(_SCHEMA) in final_prompt

def test_claude_json_meta_finalizer_success_avoids_schema_error(mocker):
    from fanops.llm import claude_json_meta
    seq = iter([json.dumps({"structured_output": None, "result": "no json here", "num_turns": 2}),
                json.dumps({"structured_output": {"x": 11}, "num_turns": 1})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    out, _, _ = claude_json_meta("judge", _SCHEMA, images=["/f/1.jpg"])
    assert out == {"x": 11}

def test_no_finalizer_when_structured_output_present(mocker):
    # MOL-234: happy-path negative — finalizer must NOT fire when structured_output is already valid.
    from fanops.llm import claude_json_meta
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps({"structured_output": {"x": 3}, "num_turns": 2}), "stderr": ""})())
    out, _, _ = claude_json_meta("judge", _SCHEMA, images=["/f/1.jpg"])
    assert out == {"x": 3} and run.call_count == 1

def test_claude_json_meta_no_finalizer_when_repair_salvages(mocker):
    from fanops.llm import claude_json_meta
    picks = {"x": 8}
    prose = f'vision prose {json.dumps(picks)}'
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps({"structured_output": None, "result": prose, "num_turns": 2}), "stderr": ""})())
    out, _, _ = claude_json_meta("judge", _SCHEMA, images=["/f/1.jpg"])
    assert out == picks and run.call_count == 1

def test_claude_json_no_finalizer_without_images_even_when_repair_fails(mocker):
    from fanops.llm import LlmSchemaError
    envelope = {"structured_output": None, "result": "prose with no json object"}
    run = mocker.patch("fanops.llm.subprocess.run", return_value=type("R", (), {
        "returncode": 0, "stdout": json.dumps(envelope), "stderr": ""})())
    with pytest.raises(LlmSchemaError):
        claude_json("q", _SCHEMA)
    assert run.call_count == 1

def test_claude_json_meta_finalizer_still_raises_when_it_also_fails(mocker):
    from fanops.llm import LlmSchemaError, claude_json_meta
    seq = iter([json.dumps({"structured_output": None, "result": "no json", "num_turns": 2}),
                json.dumps({"structured_output": None, "result": "still no json", "num_turns": 1})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    with pytest.raises(LlmSchemaError):
        claude_json_meta("judge", _SCHEMA, images=["/f/1.jpg"])
    assert run.call_count == 2


# --- MOL-232: finalizer turn is schema-generic (not picker-specific) ---

_GENERIC_SCHEMA = {"type": "object", "properties": {"label": {"type": "string"}, "score": {"type": "number"}},
                 "required": ["label", "score"]}

def test_finalizer_turn_recovers_structured_output(mocker):
    from fanops.llm import claude_json_meta
    expected = {"label": "bright", "score": 0.92}
    seq = iter([json.dumps({"structured_output": None, "result": "analysis prose only", "num_turns": 2}),
                json.dumps({"structured_output": expected, "num_turns": 1})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    out, _, _ = claude_json_meta("classify", _GENERIC_SCHEMA, images=["/f/x.jpg"])
    assert out == expected and run.call_count == 2
    final_cmd = run.call_args_list[1][0][0]
    i = final_cmd.index("--allowedTools"); assert final_cmd[i + 1] == ""


# --- MOL-247: _extract_json_object salvages fenced + balanced-brace prose ---

def test_extract_json_object_from_prose():
    expected = {"winner": "clip_a", "score": 0.85}
    fenced = f'Here is my analysis:\n```json\n{json.dumps(expected)}\n```\nThanks.'
    unfenced = f'The result is {json.dumps(expected)} as requested.'
    assert _extract_json_object(fenced) == expected
    assert _extract_json_object(unfenced) == expected


# --- MOL-249: repair-empty -> typed LlmSchemaError (result-resolution path) ---

def test_no_json_object_raises_llm_schema_error(mocker):
    from fanops.llm import LlmSchemaError
    envelope = {"structured_output": None, "result": "pure prose with no extractable object"}
    class R: returncode = 0; stdout = json.dumps(envelope); stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(LlmSchemaError, match="was not JSON"):
        claude_json("q", _SCHEMA)
