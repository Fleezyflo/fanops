# src/fanops/responder_policy.py
"""Gate-termination policy for LlmResponder escalation ceiling (MOL-235 / Wave 7)."""
from __future__ import annotations

from typing import Callable, Protocol

from fanops.config import Config
from fanops.models import SourceState
from fanops.agentstep import latest_request_id, write_response, discard_gate
from fanops.gate_keys import gate_source_id as _gate_source_id
from fanops.errors import fail_open
from fanops.escalation import ATTEMPT_CEILING as _GATE_DETERMINISTIC_MAX


class _GateResponse(Protocol):
    def model_dump_json(self, *, indent: int = ...) -> str: ...


def failopen_clean_degraded_reason(kind: str, reason: str) -> str:
    """Wave 7: enrichment-gate ceiling stamps a visible fail-open degraded_reason."""
    return f"agent gate {kind} fail-open clean: {reason}"[:200]


def terminate_enrichment_gate_failopen(
    cfg: Config,
    kind: str,
    key: str,
    reason: str,
    *,
    empty_response_factory: Callable[[str], _GateResponse],
    mark_degraded: Callable[[Config, str, str, str], None],
    screen_model_text: Callable[[object], object],
    log: Callable[..., None],
) -> None:
    """Enrichment gates (moment_hooks, captions) synthesize a clean fail-open response at ceiling."""
    rid = latest_request_id(cfg, kind, key)
    if rid is None:
        return
    mark_degraded(cfg, kind, key, failopen_clean_degraded_reason(kind, reason))
    obj = screen_model_text(empty_response_factory(rid))
    write_response(cfg, kind, key, obj.model_dump_json(indent=2))
    log("responder", f"{kind}:{key}", "gate_failopen_clean")


def terminate_moments_gate_failclosed(cfg: Config, kind: str, key: str, reason: str) -> None:
    """Moments gate ceiling -> SourceState.error (fail-closed); discard pending gate on success."""
    from fanops.ledger import Ledger

    saved = False
    # Secondary write after TERMINATE posture — fail_open protects ledger I/O only (policy §2.6).
    with fail_open(f"responder.{kind}:{key} terminate secondary write degrade:"):
        with Ledger.transaction(cfg) as led:
            sid = _gate_source_id(led, kind, key)
            src = led.sources.get(sid) if sid else None
            if src is not None and src.state != SourceState.error:
                led.set_source_state(
                    sid,
                    SourceState.error,
                    error_reason=(
                        f"agent gate {kind} failed (deterministic ceiling "
                        f"{_GATE_DETERMINISTIC_MAX}/{_GATE_DETERMINISTIC_MAX}): {reason}"[:200]
                    ),
                )
                saved = True
    if saved:
        discard_gate(cfg, kind, key)  # H07: terminal moments gate must not linger pending
