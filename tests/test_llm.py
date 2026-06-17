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
    prompt = cmd[cmd.index("-p") + 1]
    assert "/tmp/a.jpg" in prompt and "/tmp/b.jpg" in prompt              # told which frames to read

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
    class R: returncode = 0; stdout = "not json at all"; stderr = ""
    mocker.patch("fanops.llm.subprocess.run", return_value=R())
    with pytest.raises(RuntimeError, match="could not parse"):
        claude_json("q", _SCHEMA)

def test_claude_json_raises_on_non_object_json(mocker):
    # Valid JSON but not an object (null/array/number/string) must become the clean
    # "could not parse" RuntimeError, not a raw AttributeError from env.get(...).
    for stdout in ("null", "[1, 2]", "42", "\"hi\""):
        class R: returncode = 0; stderr = ""
        R.stdout = stdout
        mocker.patch("fanops.llm.subprocess.run", return_value=R())
        with pytest.raises(RuntimeError, match="could not parse"):
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
