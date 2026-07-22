"""The E2E trigger gate — proven in both directions, and proven for the shapes that matter.

THE DANGEROUS FAILURE CHANGED WITH THE POLARITY. Under the old relevance predicate it was a
docs-shaped rule quietly matching a runtime path and buying a green context that never ran. Under
the trigger gate the fast path is the DEFAULT, so nothing can sneak into it — the risk moved to the
other end: a lane that can no longer be reached at all, leaving `workflow_dispatch` and the nightly
schedule as dead switches. The tests that matter here are therefore the ones asserting the lane
still RUNS on an explicit request.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ci_e2e_trigger", Path(__file__).resolve().parents[1] / "scripts" / "ci_e2e_trigger.py")
trig = importlib.util.module_from_spec(_SPEC)
# `@dataclass` resolves annotations through `sys.modules[cls.__module__]`, so a path-loaded module
# must be registered BEFORE exec or the decorator raises on `Decision`.
sys.modules[_SPEC.name] = trig
_SPEC.loader.exec_module(trig)


# ---- ordinary push/PR events never run the heavy suite -----------------------------------------

def test_a_pull_request_does_not_run_the_full_suite():
    d = trig.decide(event="pull_request")
    assert d.run is False
    assert "on-demand only" in d.reason


def test_a_push_to_main_does_not_run_the_full_suite():
    """Post-merge it blocks nothing, but it is still an ordinary event and still costs ~7 minutes."""
    assert trig.decide(event="push").run is False


def test_a_pr_iteration_touching_runtime_code_does_not_run_the_full_suite():
    """THE HEADLINE CHANGE. Under the old predicate this ran the full lane every time."""
    assert trig.decide(event="pull_request").run is False


def test_a_715_shaped_change_does_not_run_the_full_suite():
    """PR #715 touched `tools/contract/**`, `tests/**`, `scripts/**`, `.github/**` and `docs/**`.

    Every one of those is a path the old relevance predicate classified as runtime-relevant, so a
    change of exactly this shape ran the full ~7-minute lane on every push. It must not any more,
    and the gate can no longer reach a different answer by inspecting paths — it has none.
    """
    assert trig.decide(event="pull_request").run is False
    assert trig.decide(event="push").run is False


def test_the_predicate_takes_no_paths_at_all():
    """The old signature accepted a changed-path list. Accepting one now would be an inert input
    that a future reader would reasonably believe still decides something."""
    import inspect
    params = inspect.signature(trig.decide).parameters
    assert set(params) == {"event", "forced"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())


# ---- an explicit request still runs it ---------------------------------------------------------

def test_manual_dispatch_runs_the_full_suite():
    d = trig.decide(event="workflow_dispatch")
    assert d.run is True
    assert "workflow_dispatch" in d.reason


def test_the_nightly_schedule_runs_the_full_suite():
    """Without this the lane rots unobserved: nothing else exercises the real toolchain."""
    assert trig.decide(event="schedule").run is True


def test_a_force_e2e_request_on_a_pull_request_runs_the_full_suite():
    d = trig.decide(event="pull_request", forced=True)
    assert d.run is True
    assert "force-e2e" in d.reason


def test_forced_also_wins_on_a_push():
    assert trig.decide(event="push", forced=True).run is True


def test_every_on_demand_event_is_honoured():
    """The workflow reads this tuple to build its `--forced` decision; a member it ignored would be
    a switch documented in the registry and wired to nothing."""
    for ev in trig.ON_DEMAND_EVENTS:
        assert trig.decide(event=ev).run is True, ev


def test_push_and_pull_request_are_not_on_demand_events():
    assert "push" not in trig.ON_DEMAND_EVENTS
    assert "pull_request" not in trig.ON_DEMAND_EVENTS


# ---- an unknown event does NOT run, and that is the recorded decision ---------------------------

def test_an_unknown_event_does_not_run_the_full_suite():
    """DELIBERATE, AND THE OPPOSITE OF THE OLD PREDICATE.

    `merge_group`, `pull_request_target` and anything else GitHub adds are events nobody asked the
    heavy lane to run on. Running them would put the ~7 minutes back in front of an ordinary merge,
    which is the behaviour this change exists to remove.
    """
    for ev in ("merge_group", "pull_request_target", "repository_dispatch", ""):
        assert trig.decide(event=ev).run is False, ev


# ---- the CLI contract the workflow depends on --------------------------------------------------

def test_cli_writes_the_github_output_the_job_reads(tmp_path, monkeypatch, capsys):
    out = tmp_path / "gh-out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert trig.main(["--event", "pull_request"]) == 0
    assert out.read_text() == "run=false\n"
    assert "DID NOT RUN" in capsys.readouterr().out

    out.write_text("")
    assert trig.main(["--event", "workflow_dispatch"]) == 0
    assert out.read_text() == "run=true\n"


def test_cli_forced_flag_reaches_the_decision(tmp_path, monkeypatch):
    out = tmp_path / "gh-out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert trig.main(["--event", "pull_request", "--forced"]) == 0
    assert out.read_text() == "run=true\n"


def test_cli_survives_a_missing_github_output(monkeypatch, capsys):
    """Run outside Actions — printing the verdict must still work and must not raise."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert trig.main(["--event", "schedule"]) == 0
    assert "RUNNING THE FULL REAL-TOOLING LANE" in capsys.readouterr().out


def test_the_skip_message_names_every_way_to_get_the_full_lane(capsys):
    """A fast pass that does not say how to ask for the slow one is a dead end for the reader."""
    trig.main(["--event", "pull_request"])
    msg = capsys.readouterr().out
    for how in ("gh workflow run", "force-e2e", "nightly"):
        assert how in msg, how
