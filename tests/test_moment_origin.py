# tests/test_moment_origin.py — MOL-747 (T2.1): a minted moment carries its provenance.
#
# What this pins is the FIELD and its VOCABULARY, not a stamp: nothing writes `origin` yet (see the
# MomentOrigin docstring-comment in models.py for why the request->mint gap forbids a parameter), so
# the contract under test is exactly the one the downstream lifecycle work consumes —
#   * the member set is closed and complete (an origin-keyed review/purge selects on these names), and
#   * an unobserved row reads `unknown`, never `operator`.
# The second half is the load-bearing one: an optimistic `operator` default would make every row
# already on disk claim operator provenance, so an origin-keyed purge would plan zero rows and report
# success. `unknown` is what makes "unlabelled" a state the system can actually see.
import pytest
from pydantic import ValidationError
from fanops.models import Moment, MomentOrigin


def _moment(**over) -> Moment:
    """The minimal construction shape every existing Moment( ... ) in tests/ uses — no `origin` kwarg."""
    return Moment(id="m1", parent_id="src_1", start=0.0, end=7.0, reason="r", **over)


def test_origin_vocabulary_is_exactly_these_four():
    # A CLOSED classification: adding or renaming a member changes what an origin-keyed selection
    # matches, so the set is pinned by name, not by count.
    assert {m.value for m in MomentOrigin} == {"operator", "machine", "machine_inferred", "unknown"}


def test_moment_is_born_unknown_not_operator():
    m = _moment()
    assert m.origin is MomentOrigin.unknown          # THE defect this ticket exists to avoid
    assert m.origin != MomentOrigin.operator         # never assert provenance nothing observed


def test_defaulted_field_does_not_break_an_existing_construction():
    # The whole suite constructs Moment without `origin`; this is that shape asserted directly, so a
    # regression to a REQUIRED field fails here with one clear message instead of across ~255 call sites.
    m = _moment(hook="h", signal_score=1.5)
    assert m.hook == "h" and m.signal_score == 1.5 and m.origin is MomentOrigin.unknown


def test_a_stored_row_round_trips_through_the_ledger_dump_load_path():
    # Ledger.save dumps with model_dump(); Ledger.load rebuilds with Moment(**row). Both directions
    # must carry the enum, else a label written today is unreadable tomorrow.
    dumped = _moment(origin=MomentOrigin.machine_inferred).model_dump()
    assert Moment(**dumped).origin is MomentOrigin.machine_inferred
    assert _moment().model_dump(mode="json")["origin"] == "unknown"   # a plain string on any JSON backend


def test_a_legacy_row_with_no_origin_key_loads_unknown():
    # The real migration case: every row written before this field existed. No key -> `unknown`, no error.
    legacy = _moment().model_dump()
    legacy.pop("origin")
    assert "origin" not in legacy
    assert Moment(**legacy).origin is MomentOrigin.unknown


def test_a_raw_string_from_a_stored_row_coerces_to_the_enum():
    # JSON has no enums — the value arrives as a bare string on every load and must land as a member.
    assert Moment(**{**_moment().model_dump(), "origin": "machine"}).origin is MomentOrigin.machine


def test_an_unknown_label_is_refused():
    # The reason this is an enum and not a str: a typo'd or invented label must not silently become a
    # fourth category that an origin-keyed selection then misses. Moment sets validate_assignment=True,
    # so the guard holds on mutation as well as construction.
    with pytest.raises(ValidationError):
        _moment(origin="operatr")
    m = _moment()
    with pytest.raises(ValidationError):
        m.origin = "not_a_real_origin"
