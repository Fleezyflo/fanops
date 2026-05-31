"""The clip DECISION stage. request_moments() packages transcript+signals+language
(+ guidance) into an agent request. ingest_moments() VALIDATES the agent's picks and
RECONCILES them into content-addressed Moment units (upsert + cascade-delete of dropped
moments' lineage), so amplify actually changes the set instead of silently no-opping (the
v1 bug). No tiers, no quotas — the agent returns as many valid picks as are worth posting."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentRequest, MomentDecision, MomentPick, MomentState, SourceState
from fanops.ids import child_id
from fanops.agentstep import write_request, read_response

def _guidance(cfg: Config) -> str:
    return cfg.context_path.read_text() if cfg.context_path.exists() else ""

def _token(pick: MomentPick) -> str:
    return f"{pick.start:.2f}-{pick.end:.2f}"

def validate_pick(pick: MomentPick, *, duration: float) -> str | None:
    """Return a reason string if the pick is invalid, else None."""
    if pick.end <= pick.start:
        return f"end<=start ({pick.start}->{pick.end})"
    if pick.start < 0:
        return f"start<0 ({pick.start})"
    if duration and pick.end > duration + 0.5:          # tolerate tiny rounding past EOF
        return f"end>{duration} ({pick.end})"
    if (pick.end - pick.start) < 0.5:
        return f"too short ({pick.end - pick.start:.2f}s)"
    return None

def request_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    payload = MomentRequest(source_id=source_id, request_id="",   # filled by write_request
                            duration=src.duration or 0.0,
                            transcript=src.transcript or [],
                            signal_peaks=src.signal_peaks or [],
                            language=src.language,
                            guidance=_guidance(cfg)).model_dump()
    payload.pop("request_id", None)
    write_request(cfg, kind="moments", key=source_id, payload=payload)
    led.set_source_state(source_id, SourceState.moments_requested)
    return led

def ingest_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    dec = read_response(cfg, "moments", source_id, MomentDecision)
    if dec is None:
        return led                                  # still pending / stale ignored
    src = led.sources[source_id]
    keep: dict[str, Moment] = {}
    rejected = 0
    for pick in dec.picks:
        bad = validate_pick(pick, duration=src.duration or 0.0)
        if bad:
            rejected += 1
            continue
        token = _token(pick)
        mid = child_id("moment", source_id, token)
        keep[mid] = Moment(id=mid, parent_id=source_id, state=MomentState.decided,
                           content_token=token, start=pick.start, end=pick.end,
                           reason=pick.reason, transcript_excerpt=pick.transcript_excerpt,
                           signal_score=pick.signal_score)
    if not keep and dec.picks:
        src.state = SourceState.error
        src.error_reason = f"all {rejected} moment picks invalid"
        return led
    led.reconcile_moments(source_id, keep)          # upsert + cascade-delete dropped lineages
    led.set_source_state(source_id, SourceState.moments_decided)
    return led
