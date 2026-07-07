# tests/test_p11_casting_teardown.py — MOL-152 (P11): the v9 casting teardown.
# The LLM casting stage + durable AccountSelection gate are gone; the crosspost gate is a SINGLE-OWNER
# Moment.affinities predicate that KEEPS the missing-selection DENY branch (no silent fan-to-all regression).
from pathlib import Path
from types import SimpleNamespace
import pytest
from fanops.config import Config
from fanops.casting import affinity_admits

_SRC = Path(__file__).resolve().parent.parent / "src" / "fanops"


def _mom(affinities=None):
    return SimpleNamespace(id="m0", parent_id="src_1", affinities=list(affinities or []))


# ---- Step 1: the single-owner gate WITH the DENY branch (the RF1/MOM-2 guard carried forward) ----
def test_gate_denies_unattributed_account_on_attributed_source(tmp_path):
    # THE regression guard: a moment attributed to @a (non-empty affinities) DENIES @b — never a silent fan-to-all.
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, _mom(affinities=["a"]), "b") is False


def test_gate_admits_owner(tmp_path):
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, _mom(affinities=["a"]), "a") is True


def test_gate_fans_persona_blind_empty(tmp_path):
    # affinities==[] is a persona-blind (unattributed) moment -> legitimately fans to all.
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, _mom(affinities=[]), "z") is True


def test_gate_denies_missing_moment(tmp_path):
    # moment is None -> DENY (scrutiny), NEVER the old admit-all that affinity_admits used to do.
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, None, "a") is False


def test_gate_off_firewall_admits_all(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, _mom(affinities=["a"]), "b") is True   # A2 firewall: OFF ignores affinities


# ---- Step 2: the casting stage + moment_casting responder gate are gone ----
def test_casting_stage_gone():
    import fanops.pipeline as pipeline
    import fanops.responder as responder
    assert not hasattr(pipeline, "_stage_casting")
    assert "moment_casting" not in responder._SCHEMA
    assert "moment_casting" not in responder._PROMPT
    assert "moment_casting" not in responder._VISION_GATES
    assert "moment_casting" not in pipeline.GATE_KINDS
    assert "moment_casting" not in getattr(pipeline.AwaitingCounts, "__annotations__", {})


def test_casting_module_consumer_functions_removed():
    # P11 stops every AccountSelection/casting-stage consumer in the casting module (schema drop is P12).
    import fanops.casting as casting
    for gone in ("request_moment_casting", "ingest_moment_casting", "account_selection_admits",
                 "repair_casting_selections", "casting_gate_pending", "casting_gate_failed_to_open"):
        assert not hasattr(casting, gone), f"{gone} must be removed by P11"
    # the sole kept predicate (scoped_caption_surfaces deleted in P10/MOL-151 — pipeline._owner_caption_surfaces owns caption scope):
    assert hasattr(casting, "affinity_admits")
    assert not hasattr(casting, "scoped_caption_surfaces")


# ---- Step 0: the import that deleting casting.py would have broken stays wired to personas ----
def test_moments_import_casting_directive_from_personas():
    from fanops.personas import casting_directive   # noqa: F401 — the sibling-parity redirect target
    src = (_SRC / "moments.py").read_text()
    assert "from fanops.casting import" not in src   # never re-import through the (torn-down) casting module
    assert "discard_gate(cfg, \"moment_casting\"" not in src   # the dead gate discard is gone
    assert "selections_of_source" not in src and "drop_account_selection" not in src   # AccountSelection drop loop gone


# ---- Step 5: casting_bias is fully removed ----
def test_casting_bias_removed(tmp_path):
    with pytest.raises(ModuleNotFoundError):
        import fanops.casting_bias  # noqa: F401
    cfg = Config(root=tmp_path)
    assert not hasattr(cfg, "casting_bias")
    from fanops.models import MomentCastingRequest
    assert "reach_prior" not in MomentCastingRequest.model_fields


# ---- cast_moments confirmed already-absent (WS-M1/MOM-7) ----
def test_cast_moments_stays_absent():
    import fanops.casting as casting
    assert not hasattr(casting, "cast_moments")


# ---- Closed-loop tail unaffected: reconcile + post read ZERO casting/AccountSelection/affinities ----
def test_reconcile_tail_unaffected():
    from fanops.reconcile import reconcile_due   # importable -> the publish tail is intact
    from fanops.post.run import publish_due       # noqa: F401
    forbidden = ("account_selection", "AccountSelection", "scoped_caption", "affinities",
                 "request_moment_casting", "ingest_moment_casting")
    for rel in ("reconcile.py", "post/run.py"):
        text = (_SRC / rel).read_text()
        for tok in forbidden:
            assert tok not in text, f"{rel} unexpectedly references {tok}"
    assert reconcile_due is not None
