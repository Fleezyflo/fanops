"""Unit tests for scripts/lane_guard.py — the mechanical lane file-ownership guard.

Lane resolution: `<lane>/` prefix, then `tickets` (embedded MOL id), then Linear. Ticket-shaped
branches with no mapping FAIL CLOSED; non-ticket ad-hoc branches WARN that ownership was NOT
checked. Only `hot_files` in .agents/lanes.json are restricted. These tests pin that contract.
"""
import copy, json, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import lane_guard  # noqa: E402  (path insert must precede import)


def _manifest():
    return lane_guard.load_manifest(_ROOT / ".agents" / "lanes.json")


def _manifest_with_tickets(**per_lane):
    m = copy.deepcopy(_manifest())
    for name, tickets in per_lane.items():
        m["lanes"][name]["tickets"] = list(tickets)
    return m


def test_shipped_manifest_is_valid_and_self_consistent():
    m = _manifest()
    assert "lanes" in m and "guard" in m
    lanes = set(m["lanes"])
    assert {"publish", "picking", "rfd", "ci"} <= lanes
    # every hot-file owner must be a declared lane
    for path, owner in m["guard"]["hot_files"].items():
        for lane in ([owner] if isinstance(owner, str) else owner):
            assert lane in lanes, f"{path} owned by unknown lane {lane!r}"
    # branch prefixes must be unique across lanes (no ambiguous mapping)
    seen = {}
    for name, cfg in m["lanes"].items():
        for p in cfg["branch_prefixes"]:
            assert p not in seen, f"prefix {p!r} claimed by {seen.get(p)} and {name}"
            seen[p] = name


def test_lane_for_branch_maps_prefixes_and_ignores_others():
    m = _manifest()
    assert lane_guard.lane_for_branch("publish/mol-128-x", m) == "publish"
    assert lane_guard.lane_for_branch("pick/mol-159-y", m) == "picking"
    assert lane_guard.lane_for_branch("picking/mol-159-y", m) == "picking"
    assert lane_guard.lane_for_branch("rfd/mol-166-z", m) == "rfd"
    assert lane_guard.lane_for_branch("ci/mol-190-sha-pin", m) == "ci"
    # prefix-only: no `<lane>/` → None (tickets / Linear are separate steps)
    assert lane_guard.lane_for_branch("cursor/whatever-655a", m) is None
    assert lane_guard.lane_for_branch("bycreamco/mol-181-ci-01", m) is None
    assert lane_guard.lane_for_branch("main", m) is None


def test_tickets_map_cursor_and_bycreamco_mol_branches():
    # MOL-751 reproduction class: unit-slug branches without a lane prefix
    m = _manifest_with_tickets(ci=["MOL-751"])
    assert lane_guard.lane_for_ticket("MOL-751", m) == "ci"
    assert lane_guard.evaluate(
        ["README.md"], "bycreamco/mol-751-first-class-operator-pause", m
    )[0] == "ci"
    assert lane_guard.evaluate(["README.md"], "cursor/mol-751-x", m)[0] == "ci"
    assert lane_guard.evaluate(["README.md"], "bycreamco/MOL-751-X", m)[0] == "ci"


def test_shipped_manifest_lists_mol_836_under_ci_tickets():
    # this unit's own offline map — cursor/mol-836-… must resolve without Linear
    m = _manifest()
    assert "MOL-836" in (m["lanes"]["ci"].get("tickets") or [])
    assert lane_guard.evaluate(
        ["scripts/lane_guard.py"], "cursor/mol-836-lane-guard-no-silent-skip", m
    )[0] == "ci"


def test_owned_hot_file_by_own_lane_is_allowed():
    m = _manifest()
    changed = ["src/fanops/post/run.py", "tests/test_publish.py", "docs/RUNBOOK.md"]
    lane, strays = lane_guard.evaluate(changed, "publish/mol-128-x", m)
    assert lane == "publish"
    assert strays == []


def test_straying_onto_another_lanes_hot_file_is_flagged():
    m = _manifest()
    # publish lane editing a picking-ONLY hot file (ledger.py stays exclusive; models.py is
    # temporarily co-owned picking+publish for MOL-775 comment scrub — cannot serve as the NC).
    lane, strays = lane_guard.evaluate(["src/fanops/ledger.py"], "publish/mol-1-x", m)
    assert lane == "publish"
    assert strays == ["src/fanops/ledger.py"]


def test_non_hot_files_are_never_restricted():
    m = _manifest()
    # track.py is not a hot file -> any lane may touch it
    lane, strays = lane_guard.evaluate(["src/fanops/track.py", "src/fanops/meta_graph.py"], "publish/mol-1", m)
    assert strays == []


def test_shared_hot_file_allows_each_of_its_owners():
    m = _manifest()
    # moments.py is shared by picking + rfd
    assert lane_guard.evaluate(["src/fanops/moments.py"], "picking/mol-1", m)[1] == []
    assert lane_guard.evaluate(["src/fanops/moments.py"], "rfd/mol-1", m)[1] == []
    # ...but publish does not own it
    assert lane_guard.evaluate(["src/fanops/moments.py"], "publish/mol-1", m)[1] == ["src/fanops/moments.py"]


def test_ci_lane_is_flagged_if_it_touches_a_source_hot_file():
    m = _manifest()
    changed = [".github/workflows/ci.yml", "pyproject.toml", "src/fanops/config.py"]
    lane, strays = lane_guard.evaluate(changed, "ci/mol-190-sha-pin", m)
    assert lane == "ci"
    assert strays == ["src/fanops/config.py"]   # config.py is publish-owned


def test_non_lane_branch_short_circuits_to_no_strays():
    m = _manifest()
    lane, strays = lane_guard.evaluate(["src/fanops/models.py"], "cursor/env-655a", m)
    assert lane is None
    assert strays == []


def test_main_fail_closed_on_unmapped_ticket_shaped_branch(capsys):
    # ticket-shaped, no prefix, not in tickets → exit 1 (never quiet SKIP)
    code = lane_guard.main([
        "--branch", "bycreamco/mol-999999-unmapped",
        "--changed", "README.md",
        "--manifest", str(_ROOT / ".agents" / "lanes.json"),
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "MOL-999999" in err
    assert "tickets" in err
    assert "SKIP:" not in err


def test_main_warns_loud_on_ad_hoc_non_ticket_branch(capsys):
    code = lane_guard.main([
        "--branch", "cursor/env-655a",
        "--changed", "src/fanops/models.py",
        "--manifest", str(_ROOT / ".agents" / "lanes.json"),
    ])
    assert code == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "NOT checked" in err
    assert "SKIP:" not in err


def test_main_resolves_ticket_branch_via_tickets(capsys, tmp_path):
    m = _manifest_with_tickets(ci=["MOL-751"])
    path = tmp_path / "lanes.json"
    path.write_text(json.dumps(m))
    code = lane_guard.main([
        "--branch", "bycreamco/mol-751-first-class-operator-pause",
        "--changed", "README.md",
        "--manifest", str(path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "resolved lane=ci from tickets via MOL-751" in out
    assert "OK" in out


def test_lane_override_forces_a_lane_regardless_of_branch():
    m = _manifest()
    lane, strays = lane_guard.evaluate(["src/fanops/ledger.py"], "cursor/x", m, lane_override="picking")
    assert lane == "picking"
    assert strays == []   # picking owns ledger.py


def test_manifest_round_trips_as_json():
    # guard must never choke on the shipped file
    raw = (_ROOT / ".agents" / "lanes.json").read_text()
    json.loads(raw)


def test_mol_id_from_branch_reads_real_branch_conventions():
    # the point of the Linear path: engage on per-ticket branches that carry NO lane prefix
    assert lane_guard.mol_id_from_branch("cursor/mol-156-doc-sync") == "MOL-156"
    assert lane_guard.mol_id_from_branch("fix/mol-169-overlap-home") == "MOL-169"
    assert lane_guard.mol_id_from_branch("bycreamco/mol-181-ci-01") == "MOL-181"
    assert lane_guard.mol_id_from_branch("MOL-42-hotfix") == "MOL-42"
    assert lane_guard.mol_id_from_branch("cursor/env-655a") is None
    assert lane_guard.mol_id_from_branch("main") is None


def test_lane_from_issue_fields_matches_project_then_labels():
    m = _manifest()
    # ci lane matches by PROJECT
    assert lane_guard._lane_from_issue_fields([], "FanOps: CI Hardening (2026 Audit)", m) == "ci"
    # source lanes match by their proposed PRD label
    assert lane_guard._lane_from_issue_fields(["PRD:hook-viewer-pov"], None, m) == "picking"
    assert lane_guard._lane_from_issue_fields(["PRD:dryrun-boundary"], None, m) == "publish"
    assert lane_guard._lane_from_issue_fields(["PRD:degradation-honesty"], None, m) == "rfd"
    # an unmapped label / no project -> no lane (guard will SKIP, fail-open)
    assert lane_guard._lane_from_issue_fields(["Improvement"], None, m) is None
    assert lane_guard._lane_from_issue_fields([], None, m) is None


def test_parse_issue_payload_reads_linear_graphql_shape():
    payload = {"data": {"issues": {"nodes": [{
        "project": {"name": "FanOps: CI Hardening (2026 Audit)"},
        "labels": {"nodes": [{"name": "Improvement"}, {"name": "PRD:dryrun-boundary"}]},
    }]}}}
    labels, project = lane_guard._parse_issue_payload(payload)
    assert project == "FanOps: CI Hardening (2026 Audit)"
    assert set(labels) == {"Improvement", "PRD:dryrun-boundary"}
    # empty / malformed payloads degrade to ([], None) — never raise
    assert lane_guard._parse_issue_payload({"data": {"issues": {"nodes": []}}}) == ([], None)
    assert lane_guard._parse_issue_payload({}) == ([], None)


def test_lane_from_linear_is_fail_open_without_key():
    m = _manifest()
    # no api key -> None (never raises, never blocks)
    assert lane_guard.lane_from_linear("cursor/mol-190-x", m, "") is None
    # no mol id in branch -> None
    assert lane_guard.lane_from_linear("cursor/env-655a", m, "fake-key") is None
