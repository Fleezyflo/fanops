# tests/test_ci_registry_validator.py — the CI face of `python -m tools.ci selftest`.
#
# READ THE ASSERTION, NOT THE NAME. Every invariant this file claims to protect is in the assertion.
# It DELEGATES to tools.ci (selftest.detect + checks.run_static — the same implementations the CLI
# verbs run) so the pytest gate and the CLI can never report different results on the same commit —
# the drift tools/arch was bitten by. It proves the DCs DISCRIMINATE (fire on an injected defect)
# AND — now the Phase-D remediation has landed — that the committed tree is static-clean: DC-1/2/4/6/7
# find no blocking registry<->workflow divergence.
#
# The deployed-state plane (DC-3 protection, DC-8 workflow enablement, DC-9 repo security settings)
# needs the network. This offline gate does not execute the probes; the `reconcile` job does, on
# pull_request as well as on its schedule. What IS pinned here: each of those checks is a pure
# function, so this file injects live readings directly and proves they discriminate and are wired
# into `run_deployed`. No probe runs.
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ci import checks, schema, selftest  # noqa: E402
from tools.ci.cli import cmd_deployed  # noqa: E402
from tools.ci.common import PROSE_DOCS, SCHEMA, declared_workflows  # noqa: E402
from tools.ci.registry import load_registry, shape_findings  # noqa: E402
from tools.ci.workflows import discover_jobs  # noqa: E402

_ARCH_YML = _ROOT / ".github" / "workflows" / "architecture.yml"
_REQUIRED_CONTEXT = "unit (fast, no toolchain)"


def test_schema_is_not_stale():
    """The schema is GENERATED (tools/ci/schema.py). Byte-compare it against regeneration.

    Without this the schema is a hand-maintained file that merely looks generated: someone edits the
    JSON, the shape it enforces silently stops matching the shape the generator declares, and the
    divergence is invisible because the schema still validates. Same discipline tools/arch applies
    to its derived artifacts, and the same reason.
    """
    committed = SCHEMA.read_text(encoding="utf-8") if SCHEMA.exists() else ""
    assert committed == schema.render(), (
        "committed schema differs from regeneration — it was HAND-EDITED or is STALE. "
        "Run `python -m tools.ci regen` and commit the result.")


def test_registry_is_shape_valid():
    """The committed registry conforms to its schema shape (fields present, enums valid)."""
    findings = shape_findings(load_registry())
    assert findings == [], "registry shape errors:\n  " + "\n  ".join(f.render() for f in findings)


def test_static_planes_have_no_blocking_divergence():
    """The tree-clean gate. With the Phase-D remediation landed, the static planes are reconciled:
    the committed registry conforms to shape AND the registry<->workflow static checks
    (DC-1/2/4/6/7) produce no BLOCKING finding. Runs the SAME code the CLI `static` verb runs
    (shape_findings + checks.run_static), so the pytest gate and the CLI can never disagree. A future
    PR that reintroduces a static divergence — a renamed required context (DC-1), an untracked job or
    phantom control (DC-2), prose calling a required context advisory (DC-4), a job that drops its
    timeout / SHA-pin or declares an unknown GITHUB_TOKEN permission (DC-6), or an advisory job that
    can hard-fail the workflow (DC-7) — reddens the required `unit` lane here. No network (DC-3 is
    deployed-state; this gate does not probe live GitHub)."""
    reg = load_registry()
    findings = shape_findings(reg) + checks.run_static(reg, discover_jobs(), PROSE_DOCS)
    blocking = [f for f in findings if f.blocking and not f.skipped]
    assert blocking == [], ("static registry<->workflow divergence (DC-1/2/4/6/7):\n  "
                            + "\n  ".join(f.render() for f in blocking))


def test_every_blocking_condition_has_a_negative_control():
    """Each DC that can block must be exercised by at least one negative control — a check nobody
    has tried to fool is a check nobody should trust (the tools/arch NC-15 lesson)."""
    # DC-7 is IN this set now. It always had a negative control (NC-DC7-hardfail) but was never
    # REQUIRED to have one — so deleting that control would have gone unnoticed by the very test whose
    # job is to notice. DC-5 is out: the check, the duplicate_groups block it read and NC-DC5-dup were
    # deleted together 2026-07-26. DC-8/DC-9 (deployed-state enablement + repo security settings,
    # MOL-722) are in from birth.
    expected = {"DC-1", "DC-2", "DC-3", "DC-4", "DC-6", "DC-7", "DC-8", "DC-9"}
    covered = {c.expect_dc for c in selftest.CONTROLS}
    assert expected <= covered, f"uncovered DCs: {sorted(expected - covered)}"


def test_deployed_plane_wires_every_deployed_check():
    """`run_deployed` must actually CALL DC-8 and DC-9, not merely define them.

    The negative controls above invoke each check function DIRECTLY, so they stay green even if the
    aggregator that the CLI actually runs never mentions them — a check that discriminates
    perfectly and is wired to nothing. This feeds run_deployed the verbatim live divergence
    (nightly disabled, Dependabot security updates off) through its public signature and asserts
    both DCs surface. Pure and offline: every plane's input is injected, so no probe runs.
    """
    reg = load_registry()
    # Generic victim, not a named workflow: pinning `nightly.yml` here would make deleting that file
    # break a test about wiring. The nightly case is the forensic origin, not the contract.
    states = {c["workflow"]: "active" for c in reg["controls"] if c.get("workflow")}
    victim = sorted(states)[0]
    states[victim] = "disabled_manually"
    # Dependabot IS pinned by name — "its state is included in reconciliation" is the acceptance
    # criterion, so silently dropping the row must fail rather than quietly restore the blind spot.
    settings = {name: "enabled" for name in reg["required_security_settings"]}
    assert "dependabot_security_updates" in settings, "registry stopped declaring Dependabot"
    settings["dependabot_security_updates"] = "disabled"

    findings = checks.run_deployed(reg, list(reg["required_contexts"]), workflow_states=states,
                                   security_settings=settings)
    blocking = {f.dc: f.render() for f in findings if f.blocking and not f.skipped}
    assert "DC-8" in blocking, f"run_deployed dropped DC-8; got {sorted(blocking)}"
    assert "DC-9" in blocking, f"run_deployed dropped DC-9; got {sorted(blocking)}"
    assert victim in blocking["DC-8"] and "disabled_manually" in blocking["DC-8"]
    assert "dependabot_security_updates" in blocking["DC-9"]


def test_reconcile_job_checks_protection_on_pull_request(monkeypatch):
    """The merge path is a pull request. Job id `reconcile` must run there.

    A job-level `if` that is false skips the job, and a skipped job is a workflow success —
    the live protection probe never runs, so an unreadable probe becomes a pass. The deployed
    step keeps `--require-live`, which turns that probe error into a non-zero exit. The
    required status context stays the registry's single unit context; this job is not added
    to that list.
    """
    doc = yaml.safe_load(_ARCH_YML.read_text(encoding="utf-8")) or {}
    job = (doc.get("jobs") or {}).get("reconcile")
    assert isinstance(job, dict), "architecture.yml lost job id reconcile"
    cond = " ".join(str(job.get("if") or "").split())
    for event in ("pull_request", "schedule", "workflow_dispatch"):
        assert f"github.event_name == '{event}'" in cond, cond
    assert job.get("continue-on-error") is not True, (
        "continue-on-error would report the check green when the protection probe fails")
    deployed = next(
        (s for s in (job.get("steps") or [])
         if isinstance(s, dict) and str(s.get("name") or "").startswith("Deployed-state")),
        None)
    assert deployed is not None, "reconcile lost the deployed-state step"
    run = deployed.get("run") or ""
    assert "python -m tools.ci deployed --require-live" in run
    env = deployed.get("env") or {}
    assert "PROTECTION_READ_TOKEN" in str(env.get("PROTECTION_READ_TOKEN") or ""), (
        "reconcile must pass a token that can GET branch protection; GITHUB_TOKEN 403s")

    reg = load_registry()
    assert reg["required_contexts"] == [_REQUIRED_CONTEXT]

    states = declared_workflows(reg)
    monkeypatch.setattr("tools.ci.cli.probe_protection", lambda *a, **k: (None, "HTTP 403"))
    monkeypatch.setattr("tools.ci.cli.probe_workflows", lambda *a, **k: (states, None))
    monkeypatch.setattr("tools.ci.cli.probe_security", lambda *a, **k: (None, "needs admin"))
    assert cmd_deployed(True) == 1


def _exact_protection():
    return {
        "required_status_checks": {"contexts": [_REQUIRED_CONTEXT]},
        "allow_force_pushes": {"enabled": False},
        "enforce_admins": {"enabled": False},
    }


def test_protection_document_exact_context_force_off_admins_false():
    """A readable GET must match the document, not merely be non-empty."""
    reg = load_registry()
    good = _exact_protection()
    assert checks.dc3_protection_document(good) == []
    assert checks.dc3_deployed_state(reg, [_REQUIRED_CONTEXT], live_protection=good) == []

    extra = _exact_protection()
    extra["required_status_checks"] = {"contexts": [_REQUIRED_CONTEXT, "other"]}
    ctx = checks.dc3_protection_document(extra)
    assert any(f.blocking and "unit (fast, no toolchain)" in f.divergence for f in ctx)

    force = _exact_protection()
    force["allow_force_pushes"] = {"enabled": True}
    assert any("force-push" in f.divergence and f.blocking
               for f in checks.dc3_protection_document(force))

    admins = _exact_protection()
    admins["enforce_admins"] = {"enabled": True}
    assert any("enforce_admins" in f.divergence and f.blocking
               for f in checks.dc3_protection_document(admins))

    missing = _exact_protection()
    del missing["allow_force_pushes"]
    assert any(f.blocking for f in checks.dc3_protection_document(missing))


def test_deployed_probe_failures_do_not_mask_each_other():
    """One unreadable plane must never report another as clean.

    A single shared `live_error` would have made DC-3's standing 403 (no grantable `administration:
    read` scope) silence DC-8 and DC-9 as well — the check would report SKIP for everything and a
    disabled workflow would stay invisible for exactly the reason it was invisible before. Each
    probe carries its own error; this pins that.
    """
    reg = load_registry()
    states = {c["workflow"]: "active" for c in reg["controls"] if c.get("workflow")}
    states[sorted(states)[0]] = "disabled_manually"
    findings = checks.run_deployed(reg, [], live_error="HTTP 403", workflow_states=states,
                                   security_error="needs admin")
    by_dc = {f.dc: f for f in findings}
    assert by_dc["DC-3"].skipped and by_dc["DC-9"].skipped, "unreadable planes must SKIP, not pass"
    assert by_dc["DC-8"].blocking and not by_dc["DC-8"].skipped, (
        "DC-8 was readable and diverged — it must FAIL, not inherit another probe's SKIP")


@pytest.mark.parametrize("control", selftest.CONTROLS, ids=lambda c: c.id)
def test_negative_control_fires(control):
    """Inject one defect; the named DC must fire with evidence absent before."""
    fired, detail = selftest.detect(control)
    assert fired, (f"{control.expect_dc} did NOT fire on an injected `{control.defect}` ({detail}). "
                   f"The check is DECORATIVE — it manufactures confidence.")
