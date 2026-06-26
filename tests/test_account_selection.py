# tests/test_account_selection.py — RF1 (account-first differentiation foundation), Task 1.
# The durable, account-owned AccountSelection entity is the crosspost gate's NEW input: (source_id, account)
# -> moment_ids, carrying a `method` that is the SUM-TYPE DISCRIMINATOR (the empty-list overload is dissolved,
# not relocated). This locks the entity shape, the sum-type invariant, the content-addressed id, the ledger
# round-trip (mirror SelectionFact / selection_facts), the schema-9 bump, and the inverted Post.state default.
import json
import pytest
from pydantic import ValidationError
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import (AccountSelection, SelectionMethod, account_selection_id,
                           Post, PostState, Platform)


# ---- schema bump (the migration scaffold rides on it) ----
def test_schema_version_is_nine():
    assert SCHEMA_VERSION == 9


# ---- inverted-default fix: a Post is BORN awaiting_approval, never queued (no-auto-publish invariant) ----
def test_post_state_default_is_awaiting_approval():
    p = Post(id="post_x", parent_id="clip_x", account="@a", account_id="123",
             platform=Platform.instagram, caption="hi")
    assert p.state == PostState.awaiting_approval


# ---- content-addressed id: one-per-(source, account), stable across processes ----
def test_account_selection_id_is_content_addressed():
    a = account_selection_id("src_abc", "@handle")
    b = account_selection_id("src_abc", "@handle")
    c = account_selection_id("src_abc", "@other")
    assert a == b and a != c
    assert a.startswith("acctsel_")


# ---- sum-type invariant: `method` is the discriminator; empty-list is NEVER ambiguous ----
def test_chosen_methods_require_nonempty_moment_ids():
    # llm / heuristic / operator / migrated MEAN "specific picks" -> moment_ids must be non-empty.
    for method in (SelectionMethod.llm, SelectionMethod.heuristic, SelectionMethod.operator,
                   SelectionMethod.migrated):
        with pytest.raises(ValidationError):
            AccountSelection(id="acctsel_1", source_id="src_a", account="@a",
                             moment_ids=[], method=method)


def test_fan_all_default_requires_empty_moment_ids():
    # fan_all_default / pending carry their meaning in the TAG -> moment_ids must be empty.
    for method in (SelectionMethod.fan_all_default, SelectionMethod.pending):
        with pytest.raises(ValidationError):
            AccountSelection(id="acctsel_2", source_id="src_a", account="@a",
                             moment_ids=["m1"], method=method)
    # the valid shapes construct cleanly:
    AccountSelection(id="acctsel_3", source_id="src_a", account="@a",
                     moment_ids=[], method=SelectionMethod.fan_all_default)
    AccountSelection(id="acctsel_4", source_id="src_a", account="@a",
                     moment_ids=["m1", "m2"], method=SelectionMethod.llm)


# ---- enforcement reach: the invariant holds on construct + load + assignment; model_copy is the ONE residual ----
def test_frozen_blocks_direct_mutation():
    # The whole design rests on the sum-type invariant being un-evadable. frozen=True closes the
    # direct-attribute-mutation bypass (`sel.moment_ids = []` would otherwise produce an illegal object).
    sel = AccountSelection(id="acctsel_5", source_id="src_a", account="@a",
                           moment_ids=["m1"], method=SelectionMethod.llm)
    with pytest.raises(ValidationError):
        sel.moment_ids = []                       # frozen -> raises, never silently mutates


def test_model_copy_revalidates_and_cannot_forge_an_illegal_selection():
    # #12: the residual is CLOSED. AccountSelection overrides model_copy to re-validate the result, so the
    # pydantic-v2 "model_copy(update=) skips validators even on a frozen model" forgery path no longer produces
    # a sum-type-illegal object. An illegal update RAISES; a legal field update (e.g. created_at) still works.
    sel = AccountSelection(id="acctsel_6", source_id="src_a", account="@a",
                           moment_ids=["m1"], method=SelectionMethod.llm)
    with pytest.raises(ValidationError):
        sel.model_copy(update={"moment_ids": []})         # chosen method + empty ids -> now re-validated -> RAISES
    with pytest.raises(ValidationError):
        AccountSelection(**{**sel.model_dump(), "moment_ids": []})   # the constructor path also rejects (unchanged)
    legal = sel.model_copy(update={"created_at": "2026-06-26T00:00:00Z"})   # a non-invariant field copies fine
    assert legal.created_at == "2026-06-26T00:00:00Z" and legal.moment_ids == ["m1"]


# ---- ledger round-trip: add (OVERWRITE on re-cast) + save + load, mirror selection_facts ----
def test_account_selection_roundtrip(tmp_path):
    cfg = Config(root=tmp_path)
    sel = AccountSelection(id=account_selection_id("src_a", "@a"), source_id="src_a",
                           account="@a", moment_ids=["m1", "m2"], method=SelectionMethod.llm)
    with Ledger.transaction(cfg) as led:
        led.add_account_selection(sel)
    led2 = Ledger.load(cfg)
    got = led2.account_selection_for("src_a", "@a")
    assert got is not None
    assert got.moment_ids == ["m1", "m2"]
    assert got.method == SelectionMethod.llm
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == 9


def test_add_account_selection_overwrites_on_recast(tmp_path):
    # one-per-(source, account): a re-cast updates in place (latest selection wins), never grows a history.
    cfg = Config(root=tmp_path)
    sid = account_selection_id("src_a", "@a")
    with Ledger.transaction(cfg) as led:
        led.add_account_selection(AccountSelection(id=sid, source_id="src_a", account="@a",
                                                   moment_ids=["m1"], method=SelectionMethod.llm))
        led.add_account_selection(AccountSelection(id=sid, source_id="src_a", account="@a",
                                                   moment_ids=["m9"], method=SelectionMethod.operator))
    led2 = Ledger.load(cfg)
    assert led2.account_selection_for("src_a", "@a").moment_ids == ["m9"]
    assert len(led2.selections_of_source("src_a")) == 1


def test_selections_of_source_and_moment_ids_selected_for(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_account_selection(AccountSelection(id=account_selection_id("src_a", "@a"),
                                                   source_id="src_a", account="@a",
                                                   moment_ids=["m1", "m2"], method=SelectionMethod.llm))
        led.add_account_selection(AccountSelection(id=account_selection_id("src_a", "@b"),
                                                   source_id="src_a", account="@b",
                                                   moment_ids=[], method=SelectionMethod.fan_all_default))
    led2 = Ledger.load(cfg)
    assert len(led2.selections_of_source("src_a")) == 2
    assert led2.moment_ids_selected_for("src_a", "@a") == {"m1", "m2"}
    assert led2.moment_ids_selected_for("src_a", "@b") == set()     # fan_all_default carries no specific ids
    assert led2.account_selection_for("src_a", "@nobody") is None


# ---- Task 2: ingest_moment_casting writes a durable AccountSelection per ACTIVE account ----
import json as _json                                                                    # noqa: E402
from fanops.models import Source, Moment, MomentState, MomentCastingDecision            # noqa: E402
from fanops.accounts import Accounts                                                    # noqa: E402
from fanops.agentstep import latest_request_id, response_path                           # noqa: E402
from fanops.casting import request_moment_casting, ingest_moment_casting                # noqa: E402


def _acct(handle, persona="x", aid="1"):
    return {"handle": handle, "account_id": aid, "platforms": ["instagram"], "status": "active", "persona": persona}

def _seed_casting(cfg, accts, moments=("m0", "m1", "m2")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(_json.dumps({"accounts": accts}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    for mid in moments:
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token=mid, start=0, end=7,
                              reason="r", signal_score=1.0, state=MomentState.decided))
    led.save()
    return Ledger.load(cfg)

def _respond_ingest(led, cfg, selections):
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections=selections).model_dump_json())
    return ingest_moment_casting(led, cfg, "src_1", Accounts.load(cfg))


def test_ingest_writes_llm_selection_for_picked_accounts(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed_casting(cfg, [_acct("@a", "guitar"), _acct("@b", "drums", aid="2")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_ingest(led, cfg, {"@a": ["m0", "m1"], "@b": ["m2"]})
    sa = led.account_selection_for("src_1", "@a")
    assert sa is not None and sa.moment_ids == ["m0", "m1"] and sa.method == SelectionMethod.llm
    sb = led.account_selection_for("src_1", "@b")
    assert sb is not None and sb.moment_ids == ["m2"] and sb.method == SelectionMethod.llm


def test_ingest_writes_no_selection_for_unpicked_account_and_gate_denies(tmp_path):
    # the no-fan-to-all-leak contract: an in-cohort account the LLM omitted gets NO selection -> the gate
    # DENIES it on this cast source (true differentiation). It is NEVER auto-fanned to all (that was the leak).
    from fanops.casting import account_selection_admits
    cfg = Config(root=tmp_path)
    led = _seed_casting(cfg, [_acct("@a", "guitar"), _acct("@c", "bass", aid="3")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_ingest(led, cfg, {"@a": ["m0"]})          # @c got nothing
    assert led.account_selection_for("src_1", "@c") is None  # no record minted
    m0 = led.moments["m0"]
    assert account_selection_admits(cfg, led, m0, "@a") is True    # @a selected it
    assert account_selection_admits(cfg, led, m0, "@c") is False   # @c excluded, NOT fanned


def test_ingest_selection_persists_and_carries_lineage(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed_casting(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    led = _respond_ingest(led, cfg, {"@a": ["m0", "m1"]})
    led.save()
    reloaded = Ledger.load(cfg)
    sa = reloaded.account_selection_for("src_1", "@a")
    assert sa is not None and sa.moment_ids == ["m0", "m1"]
    assert sa.source_id == "src_1" and sa.created_at is not None


def test_ingest_casting_error_sets_degraded_reason(tmp_path, monkeypatch):
    # the fail-open except must no longer swallow silently — it stamps the VISIBLE degradation channel.
    cfg = Config(root=tmp_path)
    led = _seed_casting(cfg, [_acct("@a", "guitar")])
    led = request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    import fanops.casting as casting_mod
    monkeypatch.setattr(casting_mod, "read_response", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    led = ingest_moment_casting(led, cfg, "src_1", Accounts.load(cfg))   # fail-open: still returns led
    src = led.sources["src_1"]
    assert src.degraded_reason and "casting" in src.degraded_reason.lower()


# ---- Task 3: account_selection_admits — the new crosspost gate predicate (selection-first, legacy fallback) ----
from types import SimpleNamespace                                                       # noqa: E402
from fanops.casting import account_selection_admits                                     # noqa: E402


def _mom(mid, parent="src_g", affinities=None):
    return SimpleNamespace(id=mid, parent_id=parent, affinities=list(affinities or []))

def _led_with_selection(cfg, **kw):
    led = Ledger.load(cfg)
    led.add_account_selection(AccountSelection(id=account_selection_id("src_g", kw["account"]),
                                               source_id="src_g", **kw))
    return led


def test_gate_admits_only_selected_moments_on_a_cast_source(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with_selection(cfg, account="@a", moment_ids=["m1"], method=SelectionMethod.llm)
    assert account_selection_admits(cfg, led, _mom("m1"), "@a") is True     # selected
    assert account_selection_admits(cfg, led, _mom("m2"), "@a") is False    # not selected -> DENY
    assert account_selection_admits(cfg, led, _mom("m1"), "@c") is False    # cast source, no record for @c -> DENY (not fan-to-all)


def test_gate_fan_all_default_admits_all_moments(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with_selection(cfg, account="@b", moment_ids=[], method=SelectionMethod.fan_all_default)
    assert account_selection_admits(cfg, led, _mom("m1"), "@b") is True     # labelled fan-to-all -> admit ALL
    assert account_selection_admits(cfg, led, _mom("m9"), "@b") is True


def test_gate_falls_back_to_affinities_when_source_has_no_selections(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)                                                   # NO selections anywhere
    assert account_selection_admits(cfg, led, _mom("m1", affinities=["@a"]), "@a") is True   # legacy: member
    assert account_selection_admits(cfg, led, _mom("m1", affinities=["@a"]), "@b") is False  # legacy: non-member
    assert account_selection_admits(cfg, led, _mom("m1", affinities=[]), "@z") is True        # legacy: uncast -> fan-to-all


def test_gate_off_firewall_admits_all(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    led = _led_with_selection(cfg, account="@a", moment_ids=["m1"], method=SelectionMethod.llm)
    assert account_selection_admits(cfg, led, _mom("m2"), "@a") is True     # OFF ignores selections (A2 firewall)


def test_gate_denies_missing_moment_under_casting_on(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    assert account_selection_admits(cfg, led, None, "@a") is False          # missing moment: DENY, never admit-all
