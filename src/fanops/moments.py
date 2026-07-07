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
from fanops.agentstep import write_request, read_response, latest_request_id, discard_gates_for
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

def _pick_spans(pick) -> list[tuple[float, float]]:
    """S2: a segments==[] pick is a synthetic one-element [(start,end)] for segment-set comparisons."""
    if pick.segments:
        return list(pick.segments)
    return [(pick.start, pick.end)]

def _frame_counts_for_spans(n: int, spans: list[tuple[float, float]]) -> list[int]:
    """Distribute `n` hook stills across spans by length (total budget, NOT per-span n)."""
    weights = [e - s for s, e in spans]
    total = sum(weights)
    if total <= 0 or n <= 0:
        return [0] * len(spans)
    raw = [n * w / total for w in weights]
    counts = [int(x) for x in raw]
    rem = n - sum(counts)
    order = sorted(range(len(spans)), key=lambda i: (raw[i] - int(raw[i]), weights[i]), reverse=True)
    for j in range(rem):
        counts[order[j % len(order)]] += 1
    return counts

def _window_frames(cfg: Config, src, start: float, end: float,
                   segments: list[tuple[float, float]] | None = None) -> list[str]:
    """PASS 2 — stills over the PICKED+FITTED window [start,end]. S2 supercut: when `segments` is set,
    distribute _HOOK_FRAME_COUNT stills across spans (total budget, not 3×N). Single-window unchanged."""
    if not (src.source_path and os.path.exists(src.source_path) and (src.duration or 0) > 0):
        return []
    out_dir = cfg.agent_io / "keyframes" / src.id
    if segments:
        frames: list[str] = []
        for (s, e), c in zip(segments, _frame_counts_for_spans(_HOOK_FRAME_COUNT, segments)):
            if c > 0:
                frames.extend(extract_keyframes(src.source_path, s, e, count=c, out_dir=out_dir))
    else:
        frames = extract_keyframes(src.source_path, start, end, count=_HOOK_FRAME_COUNT, out_dir=out_dir)
    if not frames:
        get_logger(cfg)("source", src.id, "hook_window_frames_empty", warn=True,
                        window=f"{start:.2f}-{end:.2f}")
    return frames

def _content_token(start: float, end: float, segments: list[tuple[float, float]]) -> str:
    """S2: bare envelope token for single-window; segment-hash suffix when spans present."""
    from fanops.ids import _hash
    base = f"{start:.2f}-{end:.2f}"
    if segments:
        span_key = "|".join(f"{s:.2f}-{e:.2f}" for s, e in segments)
        return f"{base}\x1f{_hash('seg', span_key)}"
    return base

def _token(pick: MomentPick) -> str:
    return _content_token(pick.start, pick.end, pick.segments or [])

def _owned_moment_id(source_id: str, owner: str | None, token: str) -> str:
    """P3: owner handle in the id so two personas at the same timecode yield two moments. owner=None
    -> bare-token id (persona-blind picks stay byte-identical to the pre-P3 construction)."""
    if owner is None:
        return child_id("moment", source_id, token)
    return child_id("moment", source_id, f"{owner}\x1f{token}")

def _peak_in_window(p, cs: float, ce: float) -> bool:
    """True iff a signal peak's timecode falls in [cs,ce]. Fail-open PER PEAK: a malformed peak (not a
    dict, missing/non-numeric `t`) is simply excluded, never an exception — a bad peak must not error the
    whole source's hook gate (the never-wedge contract)."""
    try:
        return isinstance(p, dict) and cs <= float(p.get("t")) <= ce
    except (TypeError, ValueError):
        return False

def _peak_in_segments(p, segments: list[tuple[float, float]]) -> bool:
    """True iff a peak's timecode falls inside any supercut span. Fail-open per peak (same contract)."""
    try:
        t = float(p.get("t"))
        return isinstance(p, dict) and any(s <= t <= e for s, e in segments)
    except (TypeError, ValueError):
        return False

# ffprobe durations round; a pick may overrun probed EOF by this much before it's "past the end".
_EOF_TOLERANCE_S = 0.5
# shorter than this can't carry a hook + payoff — reject as noise
_MIN_MOMENT_S = 0.5
# two picks overlapping by more than this fraction of the SHORTER window are near-duplicate clips;
# keep the first (start-ordered), drop the later. The cross-pick guard validate_pick can't do.
_MAX_OVERLAP_FRAC = 0.5

def _spans_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    if overlap <= 0:
        return False
    return overlap > _MAX_OVERLAP_FRAC * min(a[1] - a[0], b[1] - b[0])

def _picks_overlap(p: MomentPick, q: MomentPick) -> bool:
    """S2: overlap on SEGMENT SETS ([] coerced to [(start,end)] — single-window byte-identical)."""
    for sa in _pick_spans(p):
        for sb in _pick_spans(q):
            if _spans_overlap(sa, sb):
                return True
    return False

def _drop_overlaps(picks: list[MomentPick]) -> list[MomentPick]:
    """Keep start-ordered picks, dropping any whose segment set overlaps an already-kept pick by more than
    _MAX_OVERLAP_FRAC of the shorter span. Keeps the FIRST of an overlapping pair."""
    out: list[MomentPick] = []
    for p in sorted(picks, key=lambda x: (x.start, x.end)):
        if not any(_picks_overlap(p, q) for q in out):
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
def _corpus_hit(text, corpus) -> bool:
    """True if text contains any corpus term (case-insensitive, '#' stripped). Fail-open."""
    if not corpus: return False
    try:
        hay = str(text or "").lower()
        for term in corpus:
            if not term: continue
            needle = str(term).lstrip("#").lower()
            if needle and needle in hay: return True
    except Exception: return False
    return False
def _bounded_transcript(transcript: list, peaks: list, *, corpus=None) -> tuple:
    """Return (segments_to_send, dropped_count). Keeps segments whose [start,end] midpoint is nearest a peak's
    `t` until the char budget is spent; preserves chronological order. Empty/under-budget -> (transcript, 0).
    When corpus is non-empty, corpus-matching segments rank ahead of equidistant non-matches (bonus, not filter)."""
    segs = transcript or []
    if sum(len(str(s.get("text", ""))) for s in segs) <= _TRANSCRIPT_CHAR_BUDGET:
        return segs, 0
    pts = sorted({float(p.get("t")) for p in (peaks or []) if isinstance(p, dict) and _is_num(p.get("t"))})
    corp = corpus or None
    def _near(s):
        try: mid = (float(s.get("start", 0)) + float(s.get("end", 0))) / 2
        except (TypeError, ValueError): return 1e9
        return min((abs(mid - t) for t in pts), default=0.0)
    def _rank(it):
        d = _near(it[1])
        if corp: return (d, 0 if _corpus_hit(it[1].get("text", ""), corp) else 1)
        return d
    ranked = sorted(enumerate(segs), key=_rank)   # nearest-peak first, corpus tiebreak, stable on index
    spent, keep_idx = 0, set()
    for i, s in ranked:
        c = len(str(s.get("text", "")))
        if spent + c > _TRANSCRIPT_CHAR_BUDGET: break
        spent += c; keep_idx.add(i)
    kept = [s for i, s in enumerate(segs) if i in keep_idx]         # restore chronological order
    return kept, len(segs) - len(kept)

def _pick_personas(cfg: Config, accounts) -> list[dict]:
    """P4a: ONE assembly point for the per-active-persona FULL spec the pick + downstream gates read.
    Returns handle+directive+selection_scope+band+framing+hook_angle+corpus. Empty when casting OFF or no
    truthy casting directive (byte-identical persona-blind pick). Fail-open: a bad account row is skipped."""
    if accounts is None or not cfg.account_casting:
        return []
    from fanops.persona_directives import casting_directive, resolved_cut_spec
    from fanops.persona_levers import derive_intensity_from_focus
    out: list[dict] = []
    for a in accounts.active():
        try:
            d = casting_directive(a)
            if not d: continue
            prof = cfg.resolve_clip_profile(a)
            band = band_for(prof)
            pin_fr = (getattr(a, "framing", None) or "").strip().lower()
            _, derived_fr = resolved_cut_spec(a)
            framing = pin_fr or derived_fr or ("top" if cfg.resolve_top_bias(a) else "center")
            content_focus = list(getattr(a, "content_focus", None) or [])
            intensity = derive_intensity_from_focus(content_focus)
            out.append({"handle": a.handle,
                        "directive": d.select_rule or d.register,
                        "selection_scope": d.scope_lens,
                        "band": f"{band.lo:g}-{band.hi:g}s",
                        "framing": framing,
                        "content_focus": content_focus,
                        "intensity": intensity or "",
                        "hook_angle": (getattr(a, "hook_angle", None) or ""),
                        "corpus": list(getattr(a, "hashtag_corpus", None) or [])})
        except Exception:
            continue
    return out

def _persona_peaks(peaks: list[dict], personas: list[dict]) -> list[dict]:
    """P4b: attach each persona's intensity-filtered peak view (ONE gate, per-persona lens)."""
    if not personas: return personas
    from fanops.signals import filter_peaks_by_intensity
    for pe in personas:
        pe["signal_peaks"] = filter_peaks_by_intensity(peaks, pe.get("intensity") or None)
    return personas

def request_moments(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 1 — request the WINDOWS. P4a: the picker SEES per-persona lenses via _pick_personas so each
    pick is attributed to its owner inside ONE source gate. The on-screen hook still rides moment_hooks."""
    src = led.sources[source_id]
    transcript, dropped = _bounded_transcript(src.transcript or [], src.signal_peaks or [])   # AGENT-2: bound the payload
    if dropped:
        get_logger(cfg)("source", source_id, "transcript_truncated", dropped=dropped, total=len(src.transcript or []))
    personas = _persona_peaks(src.signal_peaks or [], _pick_personas(cfg, accounts))
    payload = MomentRequest(source_id=source_id, request_id="",   # filled by write_request
                            duration=src.duration or 0.0,
                            transcript=transcript,
                            transcript_total=len(src.transcript or []),   # for the prompt's M-of-N truncation marker
                            signal_peaks=src.signal_peaks or [],
                            language=src.language,
                            guidance=load_guidance(cfg),
                            clip_profile=cfg.clip_profile,
                            personas=personas,
                            frames=_source_frames(cfg, src)).model_dump()   # band + the picker's eyes
    payload.pop("request_id", None)
    if not payload.get("personas"):
        payload.pop("personas", None)   # empty -> drop key so persona-blind path is byte-identical
    write_request(cfg, kind="moments", key=source_id, payload=payload)
    led.set_source_state(source_id, SourceState.moments_requested)
    return led

def _stamp_owner_spec(cfg: Config, owner: str | None, by_handle: dict) -> tuple[str | None, str | None]:
    """P5: resolve clip_profile + framing from the pick's single owner Account. persona-blind -> (None, None)."""
    if not owner:
        return None, None
    acct = by_handle.get(owner)
    if acct is None:
        return None, None
    prof = cfg.resolve_clip_profile(acct)
    pin_fr = (getattr(acct, "framing", None) or "").strip().lower()
    if pin_fr in ("top", "center"):
        fr = pin_fr
    else:
        fr = "top" if cfg.resolve_top_bias(acct) else "center"
    return prof, fr

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
    try:
        from fanops.accounts import Accounts
        by_handle = {a.handle: a for a in Accounts.load(cfg).accounts}
    except Exception:
        by_handle = {}
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
        owner = (pick.personas or [None])[0]          # P3: single-owner handle at ingest (None when blind)
        mid = _owned_moment_id(source_id, owner, token)
        clip_prof, framing = _stamp_owner_spec(cfg, owner, by_handle)
        # Born `picked` with NO hook — the hook is authored in pass 2 (ingest_moment_hooks), seeing this
        # window's frames. hook/hook_removed stay at their empty defaults until then.
        keep[mid] = Moment(id=mid, parent_id=source_id, state=MomentState.picked,
                           content_token=token, start=pick.start, end=pick.end,
                           reason=pick.reason,
                           transcript_excerpt=pick.transcript_excerpt,
                           signal_score=pick.signal_score,
                           affinities=list(pick.personas),   # P1: owner stamped at birth; [] when persona-blind
                           clip_profile=clip_prof, framing=framing,
                           segments=list(pick.segments))     # S2: supercut spans ride the moment
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
    # P11 (MOL-152): the moment_casting gate + durable AccountSelection are gone. A re-pick's fresh single-owner
    # affinities are stamped by reconcile_moments below (owner attribution rides on the pick), so there is no
    # stale per-account selection to discard here anymore.
    led.reconcile_moments(source_id, keep)          # upsert + cascade-delete dropped lineages
    led.set_source_state(source_id, SourceState.picks_decided)   # M1b: picks reconciled; hook gates next
    return led


def _hook_persona_entry(a):
    """Per-account hook gate payload — voice string + optional structured directive fields (MOL-173)."""
    from fanops.personas import hook_directive
    hd = hook_directive(a)
    entry = {"handle": a.handle, "persona": hook_author_slot(a)}
    if hd.demos: entry["demos"] = hd.demos
    if hd.ban_additions: entry["ban_additions"] = hd.ban_additions
    if hd.mechanism_lean: entry["mechanism_lean"] = hd.mechanism_lean
    return entry

def _hook_personas_for_moment(m, accounts) -> list:
    """P6: send ONLY the moment's owner to the hook author; persona-blind -> [] (shared hook)."""
    if accounts is None or not m.affinities:
        return []
    owner = m.affinities[0]
    for a in accounts.accounts:
        if a.status is AccountStatus.active and a.handle == owner:
            return [_hook_persona_entry(a)]
    return []

def request_moment_hooks(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 2 request — open ONE frame-seeing hook gate per `picked` moment of this source. Each
    request carries the picked WINDOW + stills extracted over that window (fit_window — the same cut the
    renderer makes), plus the per-account personas + learned hook styles (the hook-authoring context that
    used to ride the single-pass gate). Write-ONCE per moment (guard: a request already on disk is never
    re-stamped, so an in-flight answer is never invalidated). The source stays `picks_decided`;
    ingest_moment_hooks promotes it once every pick's hook has landed."""
    src = led.sources[source_id]
    # P6: each moment's hook author sees ONLY its owner (m.affinities[0]); persona-blind moments get no
    # personas key content -> the shared hook path (byte-identical fallback).
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
        env_peaks = [p for p in (src.signal_peaks or []) if _peak_in_window(p, cs, ce)]
        if m.segments:
            span_peaks = [p for p in (src.signal_peaks or []) if _peak_in_segments(p, m.segments)]
            peaks = span_peaks if span_peaks else env_peaks   # fail-open to envelope peaks
        else:
            peaks = env_peaks
        segs = list(m.segments) if m.segments else None
        personas = _hook_personas_for_moment(m, accounts)
        payload = MomentHookRequest(source_id=source_id, moment_id=m.id, token=m.content_token,
                                    request_id="", start=m.start, end=m.end, reason=m.reason,
                                    transcript_excerpt=m.transcript_excerpt, signal_score=m.signal_score,
                                    language=src.language, guidance=guidance,
                                    clip_profile=cfg.clip_profile,
                                    frames=_window_frames(cfg, src, cs, ce, segments=segs),
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
        hook = h or None
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
        led.moments[m.id] = m.model_copy(update={"hook": hook, "hook_removed": hook_removed,
                                                 "hook_frames_unread": bool(getattr(dec, "hook_frames_unread", False)),  # AGENT-9
                                                 "state": MomentState.decided})
    led.set_source_state(source_id, SourceState.moments_decided)   # every pick's hook landed atomically
    return led
