# tests/test_env_registration.py — MOL-782: the Settings field declaration is THE registration point.
#
# Adding one FANOPS_* bool used to mean editing nine hand-kept sites that each restated the name:
# settings._BOOL_ENV_FIELDS, config_introspect._STUDIO_SETTABLE, a bespoke golive setter body, the
# conftest scrub list, and so on. Four of those are gone — each is now a projection of the `EnvVar`
# marker on the field. These tests hold that shut from both ends: a PLANTED registration must reach
# every projection, and REMOVING it must drop it from every projection (the negative control — without
# it a projection that returned "everything" would pass the positive half and prove nothing).
from __future__ import annotations
import os

import pytest
from pydantic import ValidationError

import tests.conftest as conftest_mod
from fanops import config_introspect
from fanops.settings import (BOOL_ENV_FIELDS, BoolEnv, BoolFlag, EnvVar, OPERATOR_FLAGS,
                             STUDIO_SETTABLE, Settings, env_registry)
from fanops.studio import golive
from fanops.studio.actions import ActionResult

PLANTED = "FANOPS_PLANTED_FLAG"


class _WithPlanted(Settings):
    """The two-place cost, one place at a time: THIS is the whole `settings.py` edit a new flag needs
    (the other is its docs/CONFIG.md row, which no code reads). Everything asserted below follows."""
    FANOPS_PLANTED_FLAG: BoolFlag = ""


class _PlantedUnmarked(Settings):
    """Negative control's control: a field declared with a BARE `str` carries no `EnvVar`, so it must
    land in NO projection. Without this, `bool_word=True` could be the accidental default and every
    positive assertion below would still pass."""
    FANOPS_UNMARKED_FIELD: str = ""


def _bools(model): return {n for n, m in env_registry(model).items() if m.bool_word}
def _studio(model): return {n for n, m in env_registry(model).items() if m.studio}
def _flags(model): return {n for n, m in env_registry(model).items() if m.operator_flag}


# ── the registration reaches every projection ──────────────────────────────────────────────────────
@pytest.mark.parametrize("project", [_bools, _studio, _flags],
                         ids=["bool_env_fields", "studio_settable", "operator_flags"])
def test_planted_registration_lands_in_every_projection(project):
    assert PLANTED in project(_WithPlanted)


@pytest.mark.parametrize("project", [_bools, _studio, _flags],
                         ids=["bool_env_fields", "studio_settable", "operator_flags"])
def test_negative_control_removing_the_registration_drops_it_everywhere(project):
    # `Settings` IS `_WithPlanted` minus the planted field — the removal, expressed as the parent class.
    assert PLANTED not in project(Settings)


@pytest.mark.parametrize("project", [_bools, _studio, _flags],
                         ids=["bool_env_fields", "studio_settable", "operator_flags"])
def test_an_unmarked_field_is_registered_by_nothing(project):
    assert "FANOPS_UNMARKED_FIELD" not in project(_PlantedUnmarked)


def test_a_planted_bool_is_validated_as_a_bool_word_with_no_validator_edit():
    """The bool-word check rides on the annotation, so registering the field IS registering the check."""
    assert _WithPlanted.model_validate({PLANTED: " YES "}).FANOPS_PLANTED_FLAG == "YES"
    with pytest.raises(ValidationError):
        _WithPlanted.model_validate({PLANTED: "maybe"})


def test_a_planted_bool_is_readable_through_the_config_surface(tmp_path, monkeypatch):
    """The "readable via config / visible in Studio introspection" clause, driven through the REAL
    `config_rows`. Only the MODEL and the projection over it are swapped — every line that builds a row
    is the shipped one — so what passes here is `config_rows` finding the planted var off its field
    declaration, not this test restating the registry back to itself."""
    from fanops.config import Config
    monkeypatch.setattr(config_introspect, "Settings", _WithPlanted)
    monkeypatch.setattr(config_introspect, "STUDIO_SETTABLE", frozenset(_studio(_WithPlanted)))
    monkeypatch.setenv(PLANTED, "1")
    row = next(r for r in config_introspect.config_rows(Config(tmp_path)) if r["name"] == PLANTED)
    assert (row["effective"], row["default"], row["studio"], row["source"]) == ("1", "(empty)", True, "os.environ")


def test_negative_control_the_config_surface_has_no_row_for_the_unregistered_name(tmp_path, monkeypatch):
    """The same call UNPATCHED: with the planted field absent from `Settings` the row is gone. Without
    this, the clause above would equally pass a `config_rows` that emitted a row per os.environ key."""
    from fanops.config import Config
    monkeypatch.setenv(PLANTED, "1")
    assert not any(r["name"] == PLANTED for r in config_introspect.config_rows(Config(tmp_path)))


# ── each consumer IS the projection (not a copy that happens to agree today) ───────────────────────
def test_config_introspect_reads_the_derived_set_not_a_local_copy():
    assert config_introspect.STUDIO_SETTABLE is STUDIO_SETTABLE
    assert not hasattr(config_introspect, "_STUDIO_SETTABLE")


def test_config_rows_studio_column_is_the_derived_set(tmp_path):
    from fanops.config import Config
    rows = config_introspect.config_rows(Config(tmp_path))
    assert {r["name"] for r in rows if r["studio"]} == set(STUDIO_SETTABLE)


def test_conftest_scrub_list_covers_every_registered_bool_flag():
    assert set(BOOL_ENV_FIELDS) <= set(conftest_mod._LEAKY_ENV)
    # The fixture force-sets these two AFTER the strip loop and restores them from `saved` alone. That
    # single restore path is only correct while both are in _LEAKY_ENV — which they are BY DERIVATION
    # (each is a registered bool), so this pins the precondition the deleted save/restore pair used to
    # cover by hand. Drop the registration and the fixture would delete the operator's real value.
    assert {"FANOPS_ISOLATE_VOCALS", "FANOPS_BURN_SUBS"} <= set(BOOL_ENV_FIELDS)


def test_the_hermetic_fixture_actually_strips_every_registered_bool_flag():
    """The live half of the clause above: inside a test body the autouse fixture has already run, so
    no registered flag may carry an ambient value (the two the fixture force-disables read '0')."""
    forced = {"FANOPS_ISOLATE_VOCALS": "0", "FANOPS_BURN_SUBS": "0"}
    for name in BOOL_ENV_FIELDS:
        assert os.environ.get(name) == forced.get(name), f"{name} leaked into the unit suite"


# ── go-live: the registry decides what may be written, and the seven setters are one writer ───────
@pytest.mark.parametrize("setter,key", [
    (golive.set_account_casting, "FANOPS_ACCOUNT_CASTING"),
    (golive.set_variant_learning, "FANOPS_VARIANT_LEARNING"),
    (golive.set_variant_amplify, "FANOPS_VARIANT_AMPLIFY"),
    (golive.set_learn_amplify, "FANOPS_LEARN_AMPLIFY"),
    (golive.set_learn_retire, "FANOPS_LEARN_RETIRE"),
    (golive.set_variant_ucb, "FANOPS_VARIANT_UCB"),
    (golive.set_variant_transfer, "FANOPS_VARIANT_TRANSFER"),
])
@pytest.mark.parametrize("on", [True, False])
def test_each_named_setter_is_set_flag_on_its_registered_key(tmp_path, monkeypatch, setter, key, on):
    """No behavior change: the named setter and the generic writer produce the SAME ActionResult and
    the SAME dual-write. This is what lets the seven bespoke bodies collapse into one."""
    from fanops.config import Config
    cfg = Config(tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv(key, raising=False)
    named = setter(cfg, on)
    written = (os.environ[key], (cfg.root / ".env").read_text())
    monkeypatch.delenv(key, raising=False)
    (cfg.root / ".env").unlink()
    generic = golive.set_flag(cfg, key, on)
    assert named == generic == ActionResult(ok=True, detail={key.removeprefix("FANOPS_").lower(): on})
    assert written == (os.environ[key], (cfg.root / ".env").read_text())


def test_set_flag_refuses_an_unregistered_key_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_NOT_A_FLAG", raising=False)
    from fanops.config import Config
    cfg = Config(tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    res = golive.set_flag(cfg, "FANOPS_NOT_A_FLAG", True)
    assert not res.ok and "not an operator-settable flag" in res.error
    assert "FANOPS_NOT_A_FLAG" not in os.environ
    assert not (cfg.root / ".env").exists()


def test_set_flag_refuses_fanops_live_so_go_live_stays_its_only_setter(tmp_path, monkeypatch):
    """golive invariant #2. FANOPS_LIVE is registered STUDIO-settable (it shows in the config surface)
    but deliberately NOT an operator_flag — a generic toggle reaching it would flip the system live
    without the readiness+confirm gate. The registration is what holds the two apart."""
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    from fanops.config import Config
    cfg = Config(tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    assert "FANOPS_LIVE" in STUDIO_SETTABLE and "FANOPS_LIVE" not in OPERATOR_FLAGS
    res = golive.set_flag(cfg, "FANOPS_LIVE", True)
    assert not res.ok
    assert os.environ.get("FANOPS_LIVE") is None
    assert not (cfg.root / ".env").exists()


def test_every_operator_flag_is_a_registered_bool_and_studio_settable():
    """set_flag writes '1'/'0', so an operator flag that was not a bool word would be written in a
    vocabulary its own validator rejects; and one not studio-settable would be togglable but invisible."""
    assert OPERATOR_FLAGS <= set(BOOL_ENV_FIELDS)
    assert OPERATOR_FLAGS <= STUDIO_SETTABLE


def test_the_marker_defaults_to_registering_nothing():
    """`EnvVar()` opts INTO nothing: a field that carries the marker but sets no facts is not a bool,
    not studio-settable, not an operator flag. Widening is always an explicit act."""
    assert EnvVar() == EnvVar(bool_word=False, studio=False, operator_flag=False)
    assert BoolEnv is not BoolFlag
