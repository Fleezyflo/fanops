"""The clip DECISION stage. request_moments() packages transcript+signals+language
(+ guidance) into an agent request. ingest_moments() VALIDATES the agent's picks and
RECONCILES them into content-addressed Moment units (upsert + cascade-delete of dropped
moments' lineage), so amplify actually changes the set instead of silently no-opping (the
v1 bug). No tiers, no quotas — the agent returns as many valid picks as are worth posting."""
from __future__ import annotations
import math
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Moment, MomentRequest, MomentDecision, MomentPick, MomentState, SourceState,
                           MomentHookRequest, MomentHookDecision)
from fanops.ids import child_id
from fanops.agentstep import write_request, read_response, latest_request_id, discard_gates_for, discard_gate
from fanops.text import sanitize_generated_text
from fanops.hookcheck import is_weak_hook
from fanops.keyframes import extract_keyframes
from fanops.bands import band_for
from fanops.clip import fit_window
from fanops.log import get_logger
from fanops.control import load_guidance
from fanops.moment_hook_learning import proven_hook_styles
from fanops.personas import hook_author_slot
from fanops.accounts import AccountStatus
import os

# M1b PASS 1: how many SOURCE stills the PICK author gets — a whole-source survey (a picking aid: judge
# which windows are visually strong). Bounded so the opus+vision call stays under the claude -p image
# ceiling.
_AUTHOR_FRAME_COUNT = 6
# M1b PASS 2: how many stills the HOOK author gets — sampled over the PICKED+FITTED window only (fewer
# than the survey: one window, the author's actual eyes for THIS clip's opening).
_HOOK_FRAME_COUNT = 3

def _source_frames(cfg: Config, src) -> list[str]:
    """PASS 1 — a few stills sampled EVENLY across the whole SOURCE, the PICK author's eyes for judging
    which windows are visually strong (who/where/lighting/motion). NOT for hook authoring: the hook is
    written in pass 2, seeing the picked window's own frames (_window_frames). Fail-open: no real source
    file (tests / not-yet-downloaded) or an unprobed/zero duration -> [] -> text-only pick, never spawns
    ffmpeg on a path that isn't there."""
    if not (src.source_path and os.path.exists(src.source_path) and (src.duration or 0) > 0):
        return []
    return extract_keyframes(src.source_path, 0.0, src.duration, count=_AUTHOR_FRAME_COUNT,
                             out_dir=cfg.agent_io / "keyframes" / src.id)

def _window_frames(cfg: Config, src, start: float, end: float) -> list[str]:
    """PASS 2 — stills over the PICKED+FITTED window [start,end], the HOOK author's eyes for THIS exact
    clip's opening (the operator's #1 ask: SEE the footage you ride the hook for). The window is the same
    fit_window the renderer cuts, so the frames match what the clip actually opens on (snap/visual-start
    drift accepted). Fail-open: no real source / unprobed / a window probe that yields nothing -> [] +
    a breadcrumb -> the author writes text-only (degraded but HONEST). We deliberately do NOT substitute
    whole-source frames: the hook prompt asserts the attached stills ARE this clip's window, so feeding
    footage from OUTSIDE the window would actively mislead the author (review finding)."""
    if not (src.source_path and os.path.exists(src.source_path) and (src.duration or 0) > 0):
        return []
    frames = extract_keyframes(src.source_path, start, end, count=_HOOK_FRAME_COUNT,
                               out_dir=cfg.agent_io / "keyframes" / src.id)
    if not frames:                                  # window probe yielded nothing -> honest text-only
        get_logger(cfg)("source", src.id, "hook_window_frames_empty", warn=True,
                        window=f"{start:.2f}-{end:.2f}")
    return frames

def _token(pick: MomentPick) -> str:
    return f"{pick.start:.2f}-{pick.end:.2f}"

def _peak_in_window(p, cs: float, ce: float) -> bool:
    """True iff a signal peak's timecode falls in [cs,ce]. Fail-open PER PEAK: a malformed peak (not a
    dict, missing/non-numeric `t`) is simply excluded, never an exception — a bad peak must not error the
    whole source's hook gate (the never-wedge contract)."""
    try:
        return isinstance(p, dict) and cs <= float(p.get("t")) <= ce
    except (TypeError, ValueError):
        return False

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
    if not (pick.reason or "").strip():
        return "blank reason"   # MOM-6: a rationale-less pick rides the casting fit signal + hook brief blind
    return None

# AGENT-2: the pick prompt must stay under the claude -p context ceiling. A long source's whole transcript
# can blow the prompt and wedge the gate. Bound it to a char budget, SAMPLING segments near a signal peak (the
# picker reads signals to find energy, so keep the segments around them) and dropping the rest with a marker. A
# transcript already under budget is returned UNCHANGED (small inputs byte-identical).
_TRANSCRIPT_CHAR_BUDGET = 60000   # generous: a real talk source fits; a pathological one is trimmed, not wedged
def _is_num(v) -> bool:
    try: float(v); return True
    except (TypeError, ValueError): return False
def _bounded_transcript(transcript: list, peaks: list) -> tuple:
    """Return (segments_to_send, dropped_count). Keeps segments whose [start,end] midpoint is nearest a peak's
    `t` until the char budget is spent; preserves chronological order. Empty/under-budget -> (transcript, 0)."""
    segs = transcript or []
    if sum(len(str(s.get("text", ""))) for s in segs) <= _TRANSCRIPT_CHAR_BUDGET:
        return segs, 0
    pts = sorted({float(p.get("t")) for p in (peaks or []) if isinstance(p, dict) and _is_num(p.get("t"))})
    def _near(s):
        try: mid = (float(s.get("start", 0)) + float(s.get("end", 0))) / 2
        except (TypeError, ValueError): return 1e9
        return min((abs(mid - t) for t in pts), default=0.0)
    ranked = sorted(enumerate(segs), key=lambda it: _near(it[1]))   # nearest-peak first, stable on index (deterministic)
    spent, keep_idx = 0, set()
    for i, s in ranked:
        c = len(str(s.get("text", "")))
        if spent + c > _TRANSCRIPT_CHAR_BUDGET: break
        spent += c; keep_idx.add(i)
    kept = [s for i, s in enumerate(segs) if i in keep_idx]         # restore chronological order
    return kept, len(segs) - len(kept)

def request_moments(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 1 — request the WINDOWS only. The on-screen hook (and the per-account hooks + learned
    hook styles) ride the SEPARATE moment_hooks gate (request_moment_hooks), which sees each picked
    window's own frames. `accounts` is accepted for caller-signature stability but unused here — personas
    and learned styles belong to the hook pass, not picking."""
    src = led.sources[source_id]
    transcript, dropped = _bounded_transcript(src.transcript or [], src.signal_peaks or [])   # AGENT-2: bound the payload
    if dropped:
        get_logger(cfg)("source", source_id, "transcript_truncated", dropped=dropped, total=len(src.transcript or []))
    payload = MomentRequest(source_id=source_id, request_id="",   # filled by write_request
                            duration=src.duration or 0.0,
                            transcript=transcript,
                            transcript_total=len(src.transcript or []),   # for the prompt's M-of-N truncation marker
                            signal_peaks=src.signal_peaks or [],
                            language=src.language,
                            guidance=load_guidance(cfg),
                            clip_profile=cfg.clip_profile,
                            frames=_source_frames(cfg, src)).model_dump()   # band + the picker's eyes
    payload.pop("request_id", None)
    payload.pop("personas", None)   # M1b: per-account hooks ride the moment_hooks pass, not the pick pass
    write_request(cfg, kind="moments", key=source_id, payload=payload)
    led.set_source_state(source_id, SourceState.moments_requested)
    return led

def ingest_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    """M1b PASS 1 ingest — validate + reconcile the picks into `picked` moments (window chosen, hook NOT
    yet authored). The source lands `picks_decided`; request_moment_hooks then opens a per-pick hook gate,
    and ingest_moment_hooks authors the hook + promotes picked -> decided. Render keys on `decided`, so a
    picked moment never renders hookless."""
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
    for pick in deduped:
        token = _token(pick)
        mid = child_id("moment", source_id, token)
        # Born `picked` with NO hook — the hook is authored in pass 2 (ingest_moment_hooks), seeing this
        # window's frames. hook/hook_removed/hooks_by_persona stay at their empty defaults until then.
        keep[mid] = Moment(id=mid, parent_id=source_id, state=MomentState.picked,
                           content_token=token, start=pick.start, end=pick.end,
                           reason=sanitize_generated_text(pick.reason),   # strip AI-tell em-dashes
                           transcript_excerpt=pick.transcript_excerpt,
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
            # valid). V2 M1/F8: land the DISTINCT moments_empty state, not a look-alike moments_decided
            # — so `fanops status` can surface it and `retry-source` can re-request (no consumer gates
            # clipping on source state; the preserved prior moment renders off MomentState.decided).
            get_logger(cfg)("source", source_id, "zero_moments", warn=True)
            led.set_source_state(source_id, SourceState.moments_empty)
        return led
    # CRITICAL (review): a NEW pick decision SUPERSEDES the prior per-pick hook gates. Moment ids are
    # content-addressed on the token, so a same-window re-pick (amplify) UPSERTS in place and resets to
    # `picked`; without clearing the prior moment_hooks gate files, request_moment_hooks' write-once guard
    # would skip re-authoring and ingest_moment_hooks would re-apply the STALE hook (authored against the
    # OLD reason/window/frames). Discard them BEFORE reconcile so every reconciled pick re-authors fresh.
    # (Only on the reconcile path — the empty/error paths preserve prior moments AND their valid hooks.)
    discard_gates_for(cfg, "moment_hooks", f"{source_id}.")
    # M1 (Option C): a new pick decision SUPERSEDES the prior per-source moment_casting selection too — its
    # moment ids/windows changed, so the stale per-account affinities must not be re-applied (and the new
    # moments must get a FRESH selection). The casting gate is keyed on source_id (one per source), so a
    # single discard suffices. Without this, request_moment_casting's write-once guard would skip re-asking.
    discard_gate(cfg, "moment_casting", source_id)
    # MOM-1: a re-pick changed this source's moment set; the prior per-account AccountSelections reference
    # possibly-gone moments and STALE casting intent. Drop them ALL here (symmetric to the gate discard above)
    # so the re-opened casting gate writes a FRESH selection and the crosspost gate can't fan a surviving
    # captioned clip on stale intent before the re-cast lands. (selections key on (source, account); iterate
    # this source's selections.) Only on the reconcile path — the empty/error early-returns above preserve them.
    for sel in list(led.selections_of_source(source_id)):
        led.drop_account_selection(source_id, sel.account)
    led.reconcile_moments(source_id, keep)          # upsert + cascade-delete dropped lineages
    led.set_source_state(source_id, SourceState.picks_decided)   # M1b: picks reconciled; hook gates next
    return led

def request_moment_hooks(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 2 request — open ONE frame-seeing hook gate per `picked` moment of this source. Each
    request carries the picked WINDOW + stills extracted over that window (fit_window — the same cut the
    renderer makes), plus the per-account personas + learned hook styles (the hook-authoring context that
    used to ride the single-pass gate). Write-ONCE per moment (guard: a request already on disk is never
    re-stamped, so an in-flight answer is never invalidated). The source stays `picks_decided`;
    ingest_moment_hooks promotes it once every pick's hook has landed."""
    src = led.sources[source_id]
    # Per-account voices reach the frame-seeing hook author so IT writes each handle's on-screen hook
    # (the root fix). EVERY active account rides along — hook_author_slot fail-opens to a handle floor so
    # empty-inline personas (common on TikTok rows) still get hooks_by_persona instead of shared_fallback.
    personas = ([{"handle": a.handle, "persona": hook_author_slot(a)}
                 for a in accounts.accounts if a.status is AccountStatus.active]
                if accounts is not None else [])
    # P4(c): cross-surface union of gated winning hook STYLES (the SAME signal caption uses). [] when the
    # flag is off / accounts is None / on any scorer error (fail-open).
    styles = proven_hook_styles(led, cfg, accounts)
    band = band_for(cfg.clip_profile)
    guidance = load_guidance(cfg)
    for m in list(led.moments.values()):
        if m.parent_id != source_id or m.state is not MomentState.picked:
            continue
        key = f"{source_id}.{m.content_token}"
        if latest_request_id(cfg, "moment_hooks", key) is not None:
            continue                                # write-ONCE: never re-stamp an existing (pending/answered) gate
        cs, ce = fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)   # the cut the renderer makes
        peaks = [p for p in (src.signal_peaks or []) if _peak_in_window(p, cs, ce)]   # window-scoped transients (fail-open per peak)
        payload = MomentHookRequest(source_id=source_id, moment_id=m.id, token=m.content_token,
                                    request_id="", start=m.start, end=m.end, reason=m.reason,
                                    transcript_excerpt=m.transcript_excerpt, signal_score=m.signal_score,
                                    language=src.language, guidance=guidance,
                                    clip_profile=cfg.clip_profile,
                                    frames=_window_frames(cfg, src, cs, ce),
                                    signal_peaks=peaks, personas=personas).model_dump()
        payload.pop("request_id", None)
        if styles:
            payload["learned_hooks"] = styles      # optional KEY (mirrors caption), not a model field
        write_request(cfg, kind="moment_hooks", key=key, payload=payload)
    return led

def ingest_moment_hooks(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 2 ingest — apply the window-grounded hooks to a source's `picked` moments and promote them
    to `decided`. ATOMIC PER SOURCE (review fix): we wait until EVERY pick's gate has a valid answer, then
    author all of them in ONE deterministic (start,end)-ordered pass — exactly like the old single-pass
    ingest_moments. Doing it incrementally (a pick promotes the instant its own gate lands) made the
    cross-clip + opening-template dedup ORDER-DEPENDENT (an exact dup could ship twice, or a different
    pick get stripped, by pure response-arrival order). While any pick is still pending the source stays
    `picks_decided` (VISIBLE in awaiting.moment_hooks — never a silent wedge). A gate that VALIDATES with
    hook=null decides that pick CLEAN (the author's honest 'no hook beats slop')."""
    picked = sorted([m for m in led.moments.values()
                     if m.parent_id == source_id and m.state is MomentState.picked],
                    key=lambda m: (m.start, m.end))   # stable pick order -> deterministic dedup
    if not picked:
        return led
    # ATOMIC: gather every pick's decision first; if ANY hasn't landed (read_response None), wait — no
    # partial promotion, so the dedup below always sees the WHOLE source at once (order-independent).
    decisions: dict[str, MomentHookDecision] = {}
    for m in picked:
        dec = read_response(cfg, "moment_hooks", f"{source_id}.{m.content_token}", MomentHookDecision)
        if dec is None:
            return led                              # not all hooks in yet -> leave the source picks_decided
        decisions[m.id] = dec
    # Cross-clip hook de-dup: seed `used` from OTHER sources' hooks (an EXACT repeat reads like a bot);
    # `cluster_used` (the opening-template scope) starts empty and accumulates within THIS atomic pass —
    # byte-identical to the old single-pass loop. Both grow as we accept hooks in pick order.
    from fanops.caption import brand_risk_flag    # function-local: the ONE off-brand guardrail captions use (no module cycle)
    used = {(m.hook or "").strip().lower() for m in led.moments.values()
            if m.hook and m.parent_id != source_id}
    cluster_used: set[str] = set()
    for m in picked:
        dec = decisions[m.id]
        h = (dec.hook or "").strip()
        hook = sanitize_generated_text(h) if h else None
        hook_removed = None
        # Reject only MECHANICAL slop (is_weak_hook: exact cross/within-source dup, opening-template cluster)
        # OR an off-BRAND hook (brand_risk_flag). RF5: the post-generation PERSPECTIVE strip is REMOVED — the
        # generator owns perspective now (viewer-POV demos/echoes/voice/learned styles), so a third-person hook
        # is NOT nulled here; any stray one is caught in Studio Review, never stripped at ingest. The stripped
        # (mechanical/brand) hook is still PRESERVED so Review can restore it.
        if hook and (is_weak_hook(hook, used, cluster_scope=cluster_used)
                     or brand_risk_flag(hook, cfg)):   # HIGH (audit): the burned hook gets the SAME brand-risk screen captions get
            hook_removed = hook
            hook = None                             # ...the clip still ships CLEAN by default
        if hook:
            used.add(hook.lower()); cluster_used.add(hook.lower())
        # per-account hooks: sanitize each (em-dash/quote burn-safety) + drop an off-BRAND one; a dropped
        # handle falls back to the shared `hook` at crosspost. RF5: NO perspective strip here either — the
        # generator authors these viewer-POV (each account's voice is its own stance), so a third-person
        # per-account hook is KEPT, not dropped. No cross-clip dedup (these are per-account variants of ONE clip).
        raw_hbp = dec.hooks_by_persona or {}
        # AGENT-5: crosspost reads m.hooks_by_persona.get(surf.account) by EXACT handle, so an author-echoed
        # key matching no real account (a near-miss like @MohFlow vs @mohflow, or a hallucinated handle)
        # would silently fall back to the shared hook with no trace -- the per-account hook just vanishes.
        # When the active accounts are known, intersect the returned keys with the REAL handles and DROP +
        # LOG each unmatched one (a VISIBLE breadcrumb, not a silent collapse). accounts=None (legacy/test)
        # keeps every key -- byte-identical to before.
        if accounts is not None:
            valid = {a.handle for a in accounts.accounts}
            unknown = [hh for hh in raw_hbp if hh not in valid]
            if unknown:
                get_logger(cfg)("source", source_id, "hook_persona_unknown_handle",
                                moment=m.id, handles=",".join(sorted(unknown)))
            raw_hbp = {hh: ph for hh, ph in raw_hbp.items() if hh in valid}
        hbp: dict[str, str] = {}
        hbp_removed: dict[str, str] = {}
        for hh, ph in raw_hbp.items():
            s = sanitize_generated_text(ph) if ph else ""
            if s and not brand_risk_flag(s, cfg):
                hbp[hh] = s
            elif (ph or "").strip():
                hbp_removed[hh] = s or (ph or "").strip()
                get_logger(cfg)("source", source_id, "hook_persona_stripped", moment=m.id, account=hh)
        led.moments[m.id] = m.model_copy(update={"hook": hook, "hook_removed": hook_removed,
                                                 "hooks_by_persona": hbp, "hooks_by_persona_removed": hbp_removed,
                                                 "hook_frames_unread": bool(getattr(dec, "hook_frames_unread", False)),  # AGENT-9
                                                 "state": MomentState.decided})
    led.set_source_state(source_id, SourceState.moments_decided)   # every pick's hook landed atomically
    return led
