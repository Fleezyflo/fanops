"""The clip DECISION stage. request_moments() packages transcript+signals+language
(+ guidance) into an agent request. ingest_moments() VALIDATES the agent's picks and
RECONCILES them into content-addressed Moment units (upsert + cascade-delete of dropped
moments' lineage), so amplify actually changes the set instead of silently no-opping (the
v1 bug). No tiers, no quotas — the agent returns as many valid picks as are worth posting."""
from __future__ import annotations
import math
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentRequest, MomentDecision, MomentPick, MomentState, SourceState
from fanops.ids import child_id
from fanops.agentstep import write_request, read_response
from fanops.text import sanitize_generated_text
from fanops.hookcheck import is_weak_hook
from fanops.log import get_logger

def _guidance(cfg: Config) -> str:
    return cfg.context_path.read_text() if cfg.context_path.exists() else ""

def _token(pick: MomentPick) -> str:
    return f"{pick.start:.2f}-{pick.end:.2f}"

# ffprobe durations round; a pick may overrun probed EOF by this much before it's "past the end".
_EOF_TOLERANCE_S = 0.5
# shorter than this can't carry a hook + payoff — reject as noise
_MIN_MOMENT_S = 0.5
# two picks overlapping by more than this fraction of the SHORTER window are near-duplicate clips;
# keep the first (start-ordered), drop the later. The cross-pick guard validate_pick can't do.
_MAX_OVERLAP_FRAC = 0.5

def _drop_overlaps(picks: list[MomentPick]) -> list[MomentPick]:
    """Keep start-ordered picks, dropping any that overlap an already-kept pick by more than
    _MAX_OVERLAP_FRAC of the shorter window. Keeps the FIRST of an overlapping pair, so an
    all-overlapping set still yields one pick (never empties a valid decision -> never a false error)."""
    out: list[MomentPick] = []
    for p in sorted(picks, key=lambda x: (x.start, x.end)):
        if not any((min(p.end, q.end) - max(p.start, q.start)) >
                   _MAX_OVERLAP_FRAC * min(p.end - p.start, q.end - q.start) for q in out):
            out.append(p)
    return out

def validate_pick(pick: MomentPick, *, duration: float) -> str | None:
    """Return a reason string if the pick is invalid, else None."""
    if not (math.isfinite(pick.start) and math.isfinite(pick.end)):
        return f"non-finite timestamp ({pick.start}->{pick.end})"   # AUDIT H4
    if pick.end <= pick.start:
        return f"end<=start ({pick.start}->{pick.end})"
    if pick.start < 0:
        return f"start<0 ({pick.start})"
    if duration and pick.end > duration + _EOF_TOLERANCE_S:   # duration==0 means unprobed: skip EOF check
        return f"end>{duration} ({pick.end})"
    if (pick.end - pick.start) < _MIN_MOMENT_S:
        return f"too short ({pick.end - pick.start:.2f}s)"
    return None

def request_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    payload = MomentRequest(source_id=source_id, request_id="",   # filled by write_request
                            duration=src.duration or 0.0,
                            transcript=src.transcript or [],
                            signal_peaks=src.signal_peaks or [],
                            language=src.language,
                            guidance=_guidance(cfg),
                            clip_profile=cfg.clip_profile).model_dump()   # band reaches the model's picks
    payload.pop("request_id", None)
    write_request(cfg, kind="moments", key=source_id, payload=payload)
    led.set_source_state(source_id, SourceState.moments_requested)
    return led

def ingest_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    dec = read_response(cfg, "moments", source_id, MomentDecision)
    if dec is None:
        return led                                  # still pending / stale ignored
    src = led.sources[source_id]
    rejected = 0
    reasons: list[str] = []
    valid: list[MomentPick] = []
    for pick in dec.picks:
        bad = validate_pick(pick, duration=src.duration or 0.0)
        if bad:
            rejected += 1; reasons.append(bad)
            continue
        valid.append(pick)
    keep: dict[str, Moment] = {}
    deduped = _drop_overlaps(valid)                 # drop near-duplicate windows (keep first)
    if len(deduped) < len(valid):                   # don't silently suppress picks — surface the count
        get_logger(cfg)("source", source_id, "overlaps_dropped", count=len(valid) - len(deduped))
    # Cross-clip hook de-dup: seed `used` from OTHER sources' hooks so a repeat is rejected (the
    # 'reads like a bot' tell), then add each kept hook as we go.
    used = {(m.hook or "").strip().lower() for m in led.moments.values()
            if m.hook and m.parent_id != source_id}
    for pick in deduped:
        token = _token(pick)
        mid = child_id("moment", source_id, token)
        # On-screen text = the model's RETENTION hook ONLY (curiosity-gap, signal-driven, NOT a
        # transcript quote). Reject KNOWN slop (hookcheck.is_weak_hook: generic-superlative templates,
        # cliches, editing/cuts hooks, cross-clip repeats) AND an omitted hook to None -> a CLEAN clip;
        # burning slop or the unreliable transcript on screen is exactly what the operator rejected.
        h = (pick.hook or "").strip()
        hook = sanitize_generated_text(h) if h else None
        if hook and is_weak_hook(hook, used):
            hook = None
        if hook:
            used.add(hook.lower())
        keep[mid] = Moment(id=mid, parent_id=source_id, state=MomentState.decided,
                           content_token=token, start=pick.start, end=pick.end,
                           reason=sanitize_generated_text(pick.reason),   # strip AI-tell em-dashes
                           transcript_excerpt=pick.transcript_excerpt, hook=hook,
                           signal_score=pick.signal_score)
    if not keep:
        if dec.picks:
            # a wholly-INVALID new decision quarantines the source but does NOT reconcile — prior
            # valid moments/lineage are preserved. name WHY (distinct reasons) for the operator.
            src.state = SourceState.error
            src.error_reason = f"all {rejected} moment picks invalid: {'; '.join(sorted(set(reasons)))[:200]}"
        else:
            # the model returned [] (nothing worth posting): VISIBLE but NON-terminal. Log loudly so
            # 'most content wasn't generated' is never silent, but DON'T reconcile (that would
            # cascade-delete a prior good moment set) and DON'T error (the prompt blesses empty as
            # valid — erroring would wrongly need a manual retry-source).
            get_logger(cfg)("source", source_id, "zero_moments", warn=True)
            led.set_source_state(source_id, SourceState.moments_decided)
        return led
    led.reconcile_moments(source_id, keep)          # upsert + cascade-delete dropped lineages
    led.set_source_state(source_id, SourceState.moments_decided)
    return led
