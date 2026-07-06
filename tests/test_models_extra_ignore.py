# tests/test_models_extra_ignore.py — WS7 (audit x-f4): the ledger models rely on pydantic v2's DEFAULT
# extra="ignore" (they set no extra=...), so an OLDER binary can load a ledger written by a NEWER schema —
# unknown fields are silently dropped, never a crash. This pins that forward-compat contract: a regression to
# extra="forbid" (or a pydantic-default change) would turn a forward-rolled ledger into a hard load error and
# this test would catch it. Covers a plain model AND the frozen-config model (frozen must not drop the default).
from fanops.models import Source, AccountSelection, SelectionMethod


def test_unknown_field_is_ignored(tmp_path):
    s = Source(id="s1", source_path="/x.mp4", future_field_from_a_newer_schema="whatever")
    assert not hasattr(s, "future_field_from_a_newer_schema")   # silently dropped, not stored
    assert "future_field_from_a_newer_schema" not in s.model_dump()
    assert s.id == "s1" and s.source_path == "/x.mp4"           # the known fields still parse


def test_frozen_config_model_also_ignores_unknown_fields():
    # AccountSelection sets ConfigDict(frozen=True) but NOT extra= — so it must still inherit extra="ignore".
    sel = AccountSelection(id="as1", source_id="s1", account="a", moment_ids=["m1"],
                           method=SelectionMethod.llm, brand_new_key=123)
    assert not hasattr(sel, "brand_new_key")
    assert sel.account == "a" and sel.moment_ids == ["m1"]
