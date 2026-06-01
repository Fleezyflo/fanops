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

def test_claude_json_raises_on_non_object_json(mocker):
    # Valid JSON but not an object (null/array/number/string) must become the clean
    # "could not parse" RuntimeError, not a raw AttributeError from env.get(...).
    for stdout in ("null", "[1, 2]", "42", "\"hi\""):
        class R: returncode = 0; stderr = ""
        R.stdout = stdout
        mocker.patch("fanops.llm.subprocess.run", return_value=R())
        with pytest.raises(RuntimeError, match="could not parse"):
            claude_json("q", _SCHEMA)
