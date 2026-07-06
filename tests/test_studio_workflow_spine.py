# tests/test_studio_workflow_spine.py — Slice 1: the workflow SPINE. The Studio had 13 destinations and no
# through-line: after ingest nothing said "now Review", after approve nothing said "now Schedule". build_spine()
# turns the fail-open ledger counts into an ordered Make→Review→Schedule→Posted stepper carrying ONE next-action
# CTA + a you-are-here marker, so the operator always knows the path and the single next move. The pure builder
# is unit-tested directly; two client tests prove it renders on Home and highlights the current workflow tab.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.studio import views


def _counts(sources=0, awaiting=0, scheduled=0, posted=0, batches=0, failed=0, live_trackable=0):
    return {"sources": sources, "batches": batches, "awaiting": awaiting, "scheduled": scheduled,
            "posted": posted, "failed": failed, "live_trackable": live_trackable}


def _client(cfg, handles=("a",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_spine_has_four_ordered_workflow_stages():
    spine = views.build_spine(counts=_counts(), has_accounts=True, here=None)
    assert [s.key for s in spine.stages] == ["make", "review", "schedule", "posted"]
    assert [s.endpoint for s in spine.stages] == ["run_panel", "review", "schedule", "posted"]


def test_spine_next_action_is_connect_when_no_accounts():
    spine = views.build_spine(counts=_counts(sources=3, awaiting=2), has_accounts=False, here=None)
    assert spine.next_endpoint == "golive_view"          # the precondition beats any later pending work
    assert "connect" in spine.next_label.lower()


def test_spine_next_action_is_add_footage_when_no_sources():
    spine = views.build_spine(counts=_counts(sources=0), has_accounts=True, here=None)
    assert spine.next_endpoint == "run_panel"
    assert "footage" in spine.next_label.lower()


def test_spine_next_action_is_review_when_awaiting():
    spine = views.build_spine(counts=_counts(sources=2, awaiting=4), has_accounts=True, here=None)
    assert spine.next_endpoint == "review"
    assert "4" in spine.next_label and "review" in spine.next_label.lower()


def test_spine_next_action_is_schedule_when_queued_and_none_awaiting():
    spine = views.build_spine(counts=_counts(sources=2, awaiting=0, scheduled=3), has_accounts=True, here=None)
    assert spine.next_endpoint == "schedule"
    assert "3" in spine.next_label


def test_spine_caught_up_has_no_cta_when_only_posted():
    spine = views.build_spine(counts=_counts(sources=2, posted=5, live_trackable=5), has_accounts=True, here=None)
    assert spine.next_endpoint is None                   # nothing pending → no nagging CTA
    assert "caught up" in spine.next_label.lower()


def test_spine_not_caught_up_when_failed_even_if_posted():
    spine = views.build_spine(counts=_counts(sources=2, posted=5, live_trackable=5, failed=12),
                              has_accounts=True, here=None)
    assert spine.next_endpoint == "posted"
    assert "12" in spine.next_label
    assert "failed" in spine.next_label.lower()
    assert "caught up" not in spine.next_label.lower()


def test_spine_not_caught_up_when_inflight_and_no_queue():
    spine = views.build_spine(counts=_counts(sources=2, posted=3, live_trackable=3), has_accounts=True,
                              here=None, inflight=7)
    assert spine.next_endpoint == "schedule"
    assert "caught up" not in (spine.next_label or "").lower()


def test_spine_posted_stage_warns_when_failed():
    spine = views.build_spine(counts=_counts(sources=2, posted=2, live_trackable=2, failed=5),
                              has_accounts=True, here=None)
    by = {s.key: s for s in spine.stages}
    assert by["posted"].severity == "danger"
    assert by["posted"].count == 2                     # live_trackable, not failed+live conflation


def test_spine_marks_current_stage_active_and_upstream_done():
    spine = views.build_spine(counts=_counts(sources=2, awaiting=0, scheduled=1), has_accounts=True, here="schedule")
    by = {s.key: s for s in spine.stages}
    assert by["schedule"].state == "active"              # you-are-here wins regardless of completion
    assert by["make"].state == "done"                    # sources exist → Make complete
    assert by["review"].state == "done"                  # something queued → Review complete
    assert by["posted"].state == "todo"                  # nothing posted yet


def test_spine_review_not_done_while_awaiting():
    # the badge must never say "done" while the CTA still says "Review N" — Review is done only when the worklist
    # is clear (awaiting==0), not merely because some post already graduated downstream.
    spine = views.build_spine(counts=_counts(sources=2, awaiting=2, scheduled=3), has_accounts=True, here=None)
    by = {s.key: s for s in spine.stages}
    assert by["review"].state == "todo"                  # awaiting>0 → NOT done, despite 3 already scheduled
    assert spine.next_endpoint == "review"               # and the CTA agrees — no contradiction


def test_spine_stage_counts_track_the_pending_work():
    spine = views.build_spine(counts=_counts(sources=2, awaiting=4, scheduled=1, posted=7, live_trackable=7), has_accounts=True, here=None)
    by = {s.key: s for s in spine.stages}
    assert (by["make"].count, by["review"].count, by["schedule"].count, by["posted"].count) == (2, 4, 1, 7)


def test_spine_renders_on_home(tmp_path):
    html = _client(Config(root=tmp_path)).get("/").data.decode()
    assert "data-spine" in html                          # the stepper renders on the landing page
    assert 'data-stage="review"' in html                 # all four stages present


def test_spine_highlights_the_review_tab(tmp_path):
    html = _client(Config(root=tmp_path)).get("/review").data.decode()
    assert 'data-spine' in html and 'data-here="review"' in html
    assert 'aria-current="step"' in html                 # the active stage marks aria-current on its <a> (WCAG 4.1.2)
