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
    """Every pending gate as (mtime, kind, key), oldest first. ONE scan across all gate kinds — the
    single place the request dir is globbed. Callers that need the same list per source should build a
    PendingIndex once (below) and thread it, not call this per source."""
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


@dataclass(frozen=True)
class PendingIndex:
    """A ONE-scan projection of the pending gates, so a full status render is O(files) not O(sources).

    `ordered` is every pending gate (mtime, kind, key) oldest-first (identical to `_pending_gates`).
    `by_source` maps each owning source id -> its own gates, oldest-first — so `source_wait_line` and
    `_source_has_pending_gate` become dict lookups instead of re-globbing the request dir per source.
    Gates whose owner can't be resolved (gate_source_id -> None) are omitted from `by_source` (they were
    never attributable to a source, so no source ever considered them owned)."""
    ordered: list[tuple[float, str, str]]
    by_source: dict[str, list[tuple[float, str, str]]]

    @classmethod
    def build(cls, cfg: Config, led) -> "PendingIndex":
        ordered = _pending_gates(cfg)
        by_source: dict[str, list[tuple[float, str, str]]] = {}
        for mtime, kind, key in ordered:
            sid = gate_source_id(led, kind, key)
            if sid is not None:
                by_source.setdefault(sid, []).append((mtime, kind, key))
        return cls(ordered=ordered, by_source=by_source)


def top_wait_line(cfg: Config, led, idx: PendingIndex | None = None) -> str | None:
    """The oldest pending gate as a wait= line, or None when no gate is pending."""
    gates = (idx or PendingIndex.build(cfg, led)).ordered
    if not gates:
        return None
    _, kind, key = gates[0]
    return wait_for_gate(cfg, led, kind=kind, key=key)


def _source_has_pending_gate(led, source_id: str, idx: PendingIndex) -> bool:
    return source_id in idx.by_source


def _source_has_non_terminal_clip(led, source_id: str) -> bool:
    for c in led.clips.values():
        if c.state not in _TERMINAL_CLIP:
            mom = led.moments.get(c.parent_id)
            if mom is not None and mom.parent_id == source_id:
                return True
    return False


def visible_source_ids(led, cfg: Config) -> list[str]:
    """Sources that belong on status — closes the disappearing-gate bug on moments_decided."""
    idx = PendingIndex.build(cfg, led)
    out: list[str] = []
    for sid, s in sorted(led.sources.items()):
        if s.state is SourceState.retired:
            continue
        if s.state is SourceState.moments_decided:
            if not (_source_has_pending_gate(led, sid, idx) or _source_has_non_terminal_clip(led, sid)):
                continue
        out.append(sid)
    return out


def source_wait_line(cfg: Config, led, source_id: str, idx: PendingIndex | None = None) -> str | None:
    """The oldest pending gate owned by source_id, or None."""
    owned = (idx or PendingIndex.build(cfg, led)).by_source.get(source_id)
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
    bucket: str          # actionable | blocked_on_gates | recoverable | inventory | held
    wait_line: str | None
    block_reason: str | None
    artifacts: str | None = None


@dataclass(frozen=True)
class SourceBacklog:
    actionable: int
    blocked_on_gates: int
    recoverable: int
    inventory: int
    held: int
    rows: list[SourceBacklogRow]


def _source_bucket(led, source_id: str, s, idx: PendingIndex) -> str:
    """Classify one native source into a backlog bucket (priority order)."""
    if s.state is SourceState.pending:
        return "held"
    if s.state in _INVENTORY_STATES:
        return "inventory"
    if s.state in _RECOVERABLE:
        return "recoverable"
    if s.state is SourceState.moments_decided:
        if not (_source_has_pending_gate(led, source_id, idx) or _source_has_non_terminal_clip(led, source_id)):
            return "inventory"
    if _source_has_pending_gate(led, source_id, idx):
        return "blocked_on_gates"
    return "actionable"


def source_backlog(led, cfg: Config) -> SourceBacklog:
    """Canonical projection: asset inventory vs actionable backlog. Every consumer (Studio, CLI, doctor)
    must derive source counts from here — never re-count raw ledger states."""
    from fanops.artifacts import artifact_summary
    idx = PendingIndex.build(cfg, led)   # ONE scan of the request dir — makes this render O(files), not O(sources)
    rows: list[SourceBacklogRow] = []
    counts = {"actionable": 0, "blocked_on_gates": 0, "recoverable": 0, "inventory": 0, "held": 0}
    for sid, s in sorted(led.sources.items()):
        if s.origin_kind != "native":
            continue
        bucket = _source_bucket(led, sid, s, idx)
        counts[bucket] += 1
        wl = source_wait_line(cfg, led, sid, idx)
        br = s.error_reason if s.state in _RECOVERABLE else None
        if bucket == "blocked_on_gates" and wl and wl.startswith("wait=error:"):
            br = wl.split("wait=error:", 1)[-1]
        art = artifact_summary(cfg, sid) if bucket == "recoverable" else None
        rows.append(SourceBacklogRow(id=sid, state=s.state.value, bucket=bucket, wait_line=wl,
                                     block_reason=br, artifacts=art))
    return SourceBacklog(actionable=counts["actionable"], blocked_on_gates=counts["blocked_on_gates"],
                       recoverable=counts["recoverable"], inventory=counts["inventory"], held=counts["held"],
                       rows=rows)


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
