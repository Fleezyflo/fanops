# tests/test_batches.py — Face 1: the Batch primitive (model + id helper; create_batch + ledger ops follow).
# Mirrors the stitch_plan precedent: test_models.py:20-31 (StitchPlan defaults + stitch_plan_id determinism).
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Batch, BatchState, batch_id
from fanops.batches import create_batch


def _led(tmp_path):
    return Ledger.load(Config(root=tmp_path))   # empty ledger (no file on disk yet)


def test_batch_defaults():
    b = Batch(id="b1", name="Launch week")
    assert b.target_accounts == []          # [] == ALL-ACTIVE-ACCOUNTS sentinel (== today's fan-out)
    assert b.state is BatchState.open        # born open
    assert b.created_at is None and b.error_reason is None


def test_batch_state_members():
    assert {s.value for s in BatchState} == {"open", "closed", "error"}


def test_batch_id_is_deterministic_and_content_addressed():
    a = batch_id("launch", "2026-06-21T00:00:00.000001Z")
    assert a == batch_id("launch", "2026-06-21T00:00:00.000001Z")   # same (name, microsecond-birth) -> same id (re-submit idempotent)
    assert a.startswith("batch_")
    assert a != batch_id("launch", "2026-06-21T00:00:00.000002Z")   # distinct microsecond -> distinct id
    assert a != batch_id("teaser", "2026-06-21T00:00:00.000001Z")   # distinct name -> distinct id


def test_create_batch_blank_name_raises(tmp_path):
    led = _led(tmp_path)
    with pytest.raises(ValueError):
        create_batch(led, name="   ", target_accounts=[], now_iso="2026-06-21T00:00:00.000001Z")


def test_create_batch_normalizes_and_is_idempotent(tmp_path):
    led = _led(tmp_path)
    b = create_batch(led, name="  Launch  ", target_accounts=["a", "a", " @b ", ""],
                     now_iso="2026-06-21T00:00:00.000001Z")
    assert b.name == "Launch"                        # stripped (canonical, so the content id is stable)
    assert b.target_accounts == ["a", "b"]         # strip + drop-blank + dedup, first-occurrence order
    assert led.get_batch(b.id) is b                  # idempotent-added (setdefault stored it)
    b2 = create_batch(led, name="Launch", target_accounts=["a", "b"], now_iso="2026-06-21T00:00:00.000001Z")
    assert b2.id == b.id and len(led.batches) == 1   # same (name, now_iso) -> same id, no second entry


def test_create_batch_empty_target_is_all_sentinel(tmp_path):
    led = _led(tmp_path)
    b = create_batch(led, name="all", target_accounts=[], now_iso="2026-06-21T00:00:00.000001Z")
    assert b.target_accounts == [] and b.state is BatchState.open


def test_batches_for_account(tmp_path):
    led = _led(tmp_path)
    all_b = create_batch(led, name="all", target_accounts=[], now_iso="2026-06-21T00:00:00.000001Z")
    a_only = create_batch(led, name="a-only", target_accounts=["a"], now_iso="2026-06-21T00:00:00.000002Z")
    assert {b.id for b in led.batches_for_account("a")} == {all_b.id, a_only.id}   # []==ALL + specific match
    assert {b.id for b in led.batches_for_account("b")} == {all_b.id}             # only the ALL-sentinel batch


# ---- Face 1 follow-up (T1): create-time target validation — advisory error_reason, never a hard-fail ----
def test_create_batch_zero_active_target_sets_error_reason(tmp_path):
    # A target naming NO active handle is a diagnosable zero-result batch (crosspost would skip every
    # surface -> 0 posts, silently). Surface it via error_reason; the batch STILL mints (operator may
    # re-activate the handle later), state stays open — advisory, not fatal.
    led = _led(tmp_path)
    b = create_batch(led, name="launch", target_accounts=["ghost"],
                     now_iso="2026-06-21T00:00:00.000001Z", active_handles={"markmakmouly"})
    assert "ghost" in (b.error_reason or "") and b.state is BatchState.open

def test_create_batch_active_handles_none_is_byte_identical(tmp_path):
    # active_handles defaults None => NO validation => byte-identical to today (every pre-existing call unchanged).
    led = _led(tmp_path)
    b = create_batch(led, name="launch", target_accounts=["ghost"], now_iso="2026-06-21T00:00:00.000001Z")
    assert b.error_reason is None

def test_create_batch_target_intersects_active_no_error(tmp_path):
    led = _led(tmp_path)
    b = create_batch(led, name="launch", target_accounts=["a"],
                     now_iso="2026-06-21T00:00:00.000001Z", active_handles={"a", "b"})
    assert b.error_reason is None

def test_create_batch_empty_target_never_flagged(tmp_path):
    # [] is the ALL-ACTIVE sentinel: never a zero-target, even with zero active accounts (guard: `and tgt`).
    led = _led(tmp_path)
    b = create_batch(led, name="all", target_accounts=[],
                     now_iso="2026-06-21T00:00:00.000001Z", active_handles=set())
    assert b.error_reason is None
