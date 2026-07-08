"""Unit tests for .cursor/hooks/orchestration_gate.py — the delegation-only enforcement hook.

Pins the deterministic decisions that make the orchestrator contract non-optional:
- LAND-to-main is denied unless a sub-agent verification record exists (guardrail: no unverified land).
- destructive git is denied (reset --hard, force-push/direct-push to main).
- the attribution ledger records which sub-agent did each unit.
The `gh`/`git` I/O is a thin wrapper; the pure decision logic is what's tested here.
"""
import json, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / ".cursor" / "hooks"))
import orchestration_gate as og  # noqa: E402


# ---- command classification -------------------------------------------------

def test_classify_land_commands():
    assert og.classify_command("gh pr merge 381 --squash") == "land"
    assert og.classify_command("gh pr merge --merge 381") == "land"
    assert og.classify_command("  gh   pr   merge 7 ") == "land"


def test_classify_destructive_commands():
    assert og.classify_command("git reset --hard origin/main") == "destructive"
    assert og.classify_command("git push --force origin main") == "destructive"
    assert og.classify_command("git push -f origin main") == "destructive"
    assert og.classify_command("git push origin main") == "destructive"       # direct main push
    assert og.classify_command("git checkout -B feature origin/main") == "destructive"


def test_classify_read_and_other():
    assert og.classify_command("git status") == "read"
    assert og.classify_command("gh pr list --state open") == "read"
    assert og.classify_command("git push origin cursor/mol-190-x") == "other"  # feature push: fine
    assert og.classify_command("git commit -m 'x'") == "other"


def test_parse_pr_merge_number():
    assert og.parse_pr_merge("gh pr merge 381 --squash") == "381"
    assert og.parse_pr_merge("gh pr merge --merge 7") == "7"
    assert og.parse_pr_merge("git status") is None


def test_unit_ids_from_text():
    assert og.unit_ids_from_text("cursor/mol-190-sha-pin") == ["MOL-190"]
    assert og.unit_ids_from_text("MOL-181 and mol-182 done") == ["MOL-181", "MOL-182"]
    assert og.unit_ids_from_text("no ticket here") == []


# ---- verification records (the land-gate substrate) -------------------------

def _write_record(root, unit_id, **over):
    rec = {"unit_id": unit_id, "executor": "subagent:generalPurpose:exec1",
           "verifier": "subagent:generalPurpose:ver1", "passed": True,
           "acceptance_criteria_checked": True, "verified_at": "2026-07-08T00:00:00Z"}
    rec.update(over)
    d = Path(root) / ".orchestration" / "state" / "verified"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{unit_id}.json").write_text(json.dumps(rec))


def test_is_unit_verified_true_for_valid_record(tmp_path):
    _write_record(tmp_path, "MOL-190")
    ok, _ = og.is_unit_verified("MOL-190", tmp_path)
    assert ok is True


def test_is_unit_verified_false_when_missing(tmp_path):
    ok, reason = og.is_unit_verified("MOL-999", tmp_path)
    assert ok is False and "no verification record" in reason.lower()


def test_is_unit_verified_false_when_not_passed(tmp_path):
    _write_record(tmp_path, "MOL-190", passed=False)
    ok, reason = og.is_unit_verified("MOL-190", tmp_path)
    assert ok is False and "passed" in reason.lower()


def test_is_unit_verified_false_when_verifier_is_orchestrator(tmp_path):
    # the orchestrator may not self-verify — a sub-agent must
    _write_record(tmp_path, "MOL-190", verifier="orchestrator")
    ok, reason = og.is_unit_verified("MOL-190", tmp_path)
    assert ok is False and "verifier" in reason.lower()


def test_is_unit_verified_false_on_corrupt_record(tmp_path):
    d = Path(tmp_path) / ".orchestration" / "state" / "verified"
    d.mkdir(parents=True, exist_ok=True)
    (d / "MOL-190.json").write_text("{not json")
    ok, _ = og.is_unit_verified("MOL-190", tmp_path)
    assert ok is False


# ---- land decision over a set of units --------------------------------------

def test_land_decision_allows_only_when_all_units_verified(tmp_path):
    _write_record(tmp_path, "MOL-190")
    allow, _ = og.land_decision(["MOL-190"], tmp_path)
    assert allow is True
    allow2, reason = og.land_decision(["MOL-190", "MOL-191"], tmp_path)
    assert allow2 is False and "MOL-191" in reason


def test_land_decision_denies_when_no_units_found(tmp_path):
    # a land with no identifiable unit cannot be verified -> deny (fail safe)
    allow, reason = og.land_decision([], tmp_path)
    assert allow is False


# ---- ledger append (attribution) --------------------------------------------

def test_append_ledger_writes_jsonl(tmp_path):
    og.append_ledger(tmp_path, {"event": "subagent_stop", "subagent_type": "generalPurpose",
                                "task": "impl MOL-190", "status": "completed"})
    p = Path(tmp_path) / ".orchestration" / "state" / "ledger.jsonl"
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["subagent_type"] == "generalPurpose"


# ---- CLI dispatch (stdin JSON -> permission decision) -----------------------

def _run_cli(event, payload, root):
    import io
    from contextlib import redirect_stdout
    old_stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = og.main([event, "--root", str(root)])
    finally:
        sys.stdin = old_stdin
    out = buf.getvalue().strip()
    return code, (json.loads(out) if out else {})


def test_cli_before_shell_denies_destructive(tmp_path):
    code, out = _run_cli("before-shell", {"command": "git reset --hard origin/main"}, tmp_path)
    assert out.get("permission") == "deny"


def test_cli_before_shell_allows_readonly(tmp_path):
    code, out = _run_cli("before-shell", {"command": "git status"}, tmp_path)
    assert out.get("permission") == "allow"


def test_cli_before_shell_allows_feature_commit(tmp_path):
    code, out = _run_cli("before-shell", {"command": "git commit -m 'wip'"}, tmp_path)
    assert out.get("permission") == "allow"


def test_cli_subagent_start_records_ledger(tmp_path):
    code, out = _run_cli("subagent-start", {"subagent_type": "explore", "task": "scope MOL-190",
                                            "subagent_id": "sa1", "parent_conversation_id": "conv1"}, tmp_path)
    p = Path(tmp_path) / ".orchestration" / "state" / "ledger.jsonl"
    assert p.exists() and "scope MOL-190" in p.read_text()
    # a spawn is always allowed (delegation is the point)
    assert out.get("permission", "allow") == "allow"


# ==== FORTIFICATION ==========================================================

def test_land_via_gh_api_merge_is_classified_land():
    # bypass closed: merging through the API, not `gh pr merge`
    assert og.classify_command("gh api --method PUT repos/o/r/pulls/398/merge") == "land"
    assert og.classify_command("gh api -X PUT repos/o/r/pulls/398/merge -f merge_method=squash") == "land"


def test_parse_pr_merge_handles_gh_api_form():
    assert og.parse_pr_merge("gh api --method PUT repos/o/r/pulls/398/merge") == "398"


def test_protected_write_target_flags_gate_and_state_tampering():
    # forging a verification record via shell
    assert og.protected_write_target("echo '{}' > .orchestration/state/verified/MOL-1.json")
    # disabling the gate itself
    assert og.protected_write_target("rm .cursor/hooks.json")
    assert og.protected_write_target("sed -i s/deny/allow/ .cursor/hooks/orchestration_gate.py")
    assert og.protected_write_target("git checkout -- .githooks/pre-push")
    # reading a protected path is fine; editing a normal src file is fine (workers do that)
    assert og.protected_write_target("cat .cursor/hooks.json") is None
    assert og.protected_write_target("sed -i s/a/b/ src/fanops/models.py") is None


def test_cli_before_shell_denies_forging_verification_record(tmp_path):
    _, out = _run_cli("before-shell",
                      {"command": "echo '{\"passed\":true,\"verifier\":\"x\"}' > .orchestration/state/verified/MOL-1.json"},
                      tmp_path)
    assert out.get("permission") == "deny"


def test_cli_before_shell_denies_disabling_the_gate(tmp_path):
    _, out = _run_cli("before-shell", {"command": "rm -f .cursor/hooks.json"}, tmp_path)
    assert out.get("permission") == "deny"


def test_cli_before_shell_allows_worker_editing_src_via_shell(tmp_path):
    # we deliberately do NOT block src edits (workers do them); only the machinery/state is protected
    _, out = _run_cli("before-shell", {"command": "sed -i s/a/b/ src/fanops/models.py"}, tmp_path)
    assert out.get("permission") == "allow"


def test_is_unit_verified_false_when_verifier_equals_executor(tmp_path):
    _write_record(tmp_path, "MOL-190", executor="subagent:same", verifier="subagent:same")
    ok, reason = og.is_unit_verified("MOL-190", tmp_path)
    assert ok is False and "differ" in reason.lower()


def test_prefer_units_uses_branch_then_title_then_body():
    assert og.prefer_units("cursor/mol-190-x", "title MOL-7", "body MOL-8") == ["MOL-190"]
    assert og.prefer_units("cursor/feature", "MOL-7 fix", "body MOL-8") == ["MOL-7"]
    assert og.prefer_units("cursor/feature", "no id", "closes MOL-8") == ["MOL-8"]
    assert og.prefer_units("cursor/feature", "no id", "nothing") == []


def test_malformed_payload_denies_shell_but_allows_subagent_events(tmp_path):
    import io
    from contextlib import redirect_stdout
    def run_raw(event, raw):
        old, sys.stdin = sys.stdin, io.StringIO(raw)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                og.main([event, "--root", str(tmp_path)])
        finally:
            sys.stdin = old
        return json.loads(buf.getvalue().strip())
    assert run_raw("before-shell", "{bad json").get("permission") == "deny"      # security: fail closed
    assert run_raw("subagent-start", "{bad json").get("permission") == "allow"   # ledger: fail open
