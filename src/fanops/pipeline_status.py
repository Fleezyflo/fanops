# src/fanops/pipeline_status.py
"""Operator-facing pipeline control-plane status: run lease + stuck-gate wait lines + source backlog."""
from __future__ import annotations
import json
from dataclasses import dataclass
from fanops.config import Config
from fanops.gate_keys import gate_source_id
from fanops.agentstep import pending, request_path, latest_request_id, _attempts_path
from fanops.pipeline import GATE_KINDS
from fanops.pipeline_run import run_status_line
from fanops.models import SourceState, ClipState

_RECOVERABLE = (SourceState.error, SourceState.moments_empty)
_INVENTORY_STATES = (SourceState.retired, SourceState.discovered)

_GATE_DETERMINISTIC_MAX = 3   # mirrors responder._GATE_DETERMINISTIC_MAX
_TERMINAL_CLIP = frozenset((ClipState.published, ClipState.analyzed, ClipState.retired, ClipState.error))


def _gate_attempt(cfg: Config, kind: str, key: str) -> int:
    p = _attempts_path(cfg, kind, key)
    if not p.exists():
        return 0
    try:
        return int(json.loads(p.read_text()).get("n", 0))
    except Exception:
        return 0


def _gate_is_corrupt(cfg: Config, kind: str, key: str) -> bool:
    p = request_path(cfg, kind, key)
    return p.exists() and latest_request_id(cfg, kind, key) is None


def wait_for_gate(cfg: Config, led, *, kind: str, key: str) -> str:
    """wait=<state>:<kind>:<key> (attempt n/3) | wait=error:<kind>:<key>"""
    if _gate_is_corrupt(cfg, kind, key):
        return f"wait=error:{kind}:{key}"
    sid = gate_source_id(led, kind, key)
    state = led.sources[sid].state.value if sid and sid in led.sources else "?"
    n = _gate_attempt(cfg, kind, key)
    return f"wait={state}:{kind}:{key} (attempt {n}/{_GATE_DETERMINISTIC_MAX})"


def _pending_gates(cfg: Config) -> list[tuple[float, str, str]]:
    out: list[tuple[float, str, str]] = []
    for kind in GATE_KINDS:
        for key in pending(cfg, kind=kind):
            req = request_path(cfg, kind, key)
            try:
                mtime = req.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append((mtime, kind, key))
    out.sort()
    return out


def top_wait_line(cfg: Config, led) -> str | None:
    """The oldest pending gate as a wait= line, or None when no gate is pending."""
    gates = _pending_gates(cfg)
    if not gates:
        return None
    _, kind, key = gates[0]
    return wait_for_gate(cfg, led, kind=kind, key=key)


def _source_has_pending_gate(cfg: Config, led, source_id: str) -> bool:
    for kind in GATE_KINDS:
        for key in pending(cfg, kind=kind):
            if gate_source_id(led, kind, key) == source_id:
                return True
    return False


def _source_has_non_terminal_clip(led, source_id: str) -> bool:
    for c in led.clips.values():
        if c.state not in _TERMINAL_CLIP:
            mom = led.moments.get(c.parent_id)
            if mom is not None and mom.parent_id == source_id:
                return True
    return False


def visible_source_ids(led, cfg: Config) -> list[str]:
    """Sources that belong on status — closes the disappearing-gate bug on moments_decided."""
    out: list[str] = []
    for sid, s in sorted(led.sources.items()):
        if s.state is SourceState.retired:
            continue
        if s.state is SourceState.moments_decided:
            if not (_source_has_pending_gate(cfg, led, sid) or _source_has_non_terminal_clip(led, sid)):
                continue
        out.append(sid)
    return out


def source_wait_line(cfg: Config, led, source_id: str) -> str | None:
    """The oldest pending gate owned by source_id, or None."""
    owned: list[tuple[float, str, str]] = []
    for mtime, kind, key in _pending_gates(cfg):
        if gate_source_id(led, kind, key) == source_id:
            owned.append((mtime, kind, key))
    if not owned:
        return None
    _, kind, key = owned[0]
    return wait_for_gate(cfg, led, kind=kind, key=key)


def status_control_lines(cfg: Config, led) -> tuple[str, str | None]:
    """(run_status_line, top_wait_line) for fanops status / Studio pipeline_status."""
    return run_status_line(cfg), top_wait_line(cfg, led)


@dataclass(frozen=True)
class SourceBacklogRow:
    id: str
    state: str
    bucket: str          # actionable | blocked_on_gates | recoverable | inventory
    wait_line: str | None
    block_reason: str | None


@dataclass(frozen=True)
class SourceBacklog:
    actionable: int
    blocked_on_gates: int
    recoverable: int
    inventory: int
    rows: list[SourceBacklogRow]


def _source_bucket(cfg: Config, led, source_id: str, s) -> str:
    """Classify one native source into a backlog bucket (priority order)."""
    if s.state in _INVENTORY_STATES:
        return "inventory"
    if s.state in _RECOVERABLE:
        return "recoverable"
    if s.state is SourceState.moments_decided:
        if not (_source_has_pending_gate(cfg, led, source_id) or _source_has_non_terminal_clip(led, source_id)):
            return "inventory"
    if _source_has_pending_gate(cfg, led, source_id):
        return "blocked_on_gates"
    return "actionable"


def source_backlog(led, cfg: Config) -> SourceBacklog:
    """Canonical projection: asset inventory vs actionable backlog. Every consumer (Studio, CLI, doctor)
    must derive source counts from here — never re-count raw ledger states."""
    rows: list[SourceBacklogRow] = []
    counts = {"actionable": 0, "blocked_on_gates": 0, "recoverable": 0, "inventory": 0}
    for sid, s in sorted(led.sources.items()):
        if s.origin_kind != "native":
            continue
        bucket = _source_bucket(cfg, led, sid, s)
        counts[bucket] += 1
        wl = source_wait_line(cfg, led, sid)
        br = s.error_reason if s.state in _RECOVERABLE else None
        if bucket == "blocked_on_gates" and wl and wl.startswith("wait=error:"):
            br = wl.split("wait=error:", 1)[-1]
        rows.append(SourceBacklogRow(id=sid, state=s.state.value, bucket=bucket, wait_line=wl, block_reason=br))
    return SourceBacklog(actionable=counts["actionable"], blocked_on_gates=counts["blocked_on_gates"],
                       recoverable=counts["recoverable"], inventory=counts["inventory"], rows=rows)


def heal_corrupt_gates(led, cfg: Config) -> int:
    """Auto-quarantine sources owning corrupt gate requests to error (fail-closed + auditable)."""
    from fanops.log import get_logger
    log = get_logger(cfg)
    healed = 0
    for kind in GATE_KINDS:
        for key in pending(cfg, kind=kind):
            if not _gate_is_corrupt(cfg, kind, key):
                continue
            sid = gate_source_id(led, kind, key)
            if not sid or sid not in led.sources:
                continue
            s = led.sources[sid]
            reason = f"corrupt gate request: {kind}/{key}"
            led.sources[sid] = s.model_copy(update={"state": SourceState.error, "error_reason": reason})
            log("pipeline", sid, "corrupt_gate_quarantine", kind=kind, key=key)
            healed += 1
    return healed
