"""MOL-960: fanops.escalation sole ceiling + decide postures."""
from fanops.escalation import (
    ATTEMPT_CEILING, EscalationPosture, decide, record_attempt, clear_attempts, _GATE_DETERMINISTIC_MAX,
)
from fanops.config import Config
from fanops.agentstep import _attempts_path


def test_attempt_ceiling_is_three_sole_home():
    assert ATTEMPT_CEILING == 3
    assert _GATE_DETERMINISTIC_MAX is ATTEMPT_CEILING


def test_decide_deterministic_refuses_until_ceiling():
    assert decide("deterministic", 1) is EscalationPosture.refuse
    assert decide("deterministic", 2) is EscalationPosture.refuse
    assert decide("deterministic", ATTEMPT_CEILING) is EscalationPosture.terminate


def test_decide_context_limit_and_generic_match_deterministic():
    assert decide("context_limit", ATTEMPT_CEILING) is EscalationPosture.terminate
    assert decide("generic", ATTEMPT_CEILING) is EscalationPosture.terminate
    assert decide("enrichment", 99) is EscalationPosture.degrade
    assert decide("config", 0) is EscalationPosture.nonzero


def test_record_attempt_wraps_agentstep_store(tmp_path):
    cfg = Config(root=tmp_path)
    assert record_attempt(cfg, "moments", "src_1") == 1
    assert record_attempt(cfg, "moments", "src_1") == 2
    assert _attempts_path(cfg, "moments", "src_1").exists()
    clear_attempts(cfg, "moments", "src_1")
    assert not _attempts_path(cfg, "moments", "src_1").exists()
