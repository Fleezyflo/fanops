"""The clip DECISION stage. request_moments() packages transcript+signals+language
(+ guidance) into an agent request. ingest_moments() VALIDATES the agent's picks and
RECONCILES them into content-addressed Moment units (upsert + cascade-delete of dropped
moments' lineage), so amplify actually changes the set instead of silently no-opping (the
v1 bug). No tiers, no quotas — the agent returns as many valid picks as are worth posting."""
from __future__ import annotations
import logging, math, os
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Moment, MomentRequest, MomentDecision, MomentPick, MomentState, SourceState,
                           MomentHookRequest, MomentHookDecision)
from fanops.ids import child_id
from fanops.agentstep import write_request, read_response, latest_request_id, discard_gates_for, discard_gate, clear_attempts, gate_keys_for
from fanops.hookcheck import is_weak_hook
from fanops.keyframes import extract_keyframes
from fanops.clip import fit_window
from fanops.log import get_logger
from fanops.timeutil import iso_z
from fanops.control import load_guidance
from fanops.moment_hook_learning import proven_hook_styles
from fanops.personas import hook_author_slot
from fanops.accounts import AccountStatus

_log = logging.getLogger("fanops.moments")

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

def _hook_gate_key(source_id: str, m: Moment) -> str:
    owner = (m.affinities or [None])[0]
    return f"{source_id}.{owner}.{m.content_token}" if owner else f"{source_id}.{m.content_token}"

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
# scrap fragments — reject at ingest (no pad-to-band)
_MIN_MOMENT_S = 6.0
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

def _pick_owner(p: MomentPick) -> str | None:
    """The pick's single owner handle — matches ingest's `(pick.personas or [None])[0]`. None = persona-blind."""
    return (p.personas or [None])[0]

def _drop_overlaps(picks: list[MomentPick]) -> list[MomentPick]:
    """WITHIN-OWNER near-duplicate filter (MOL-169): keep start-ordered picks, dropping any whose segment
    set overlaps an already-kept pick OF THE SAME OWNER by more than _MAX_OVERLAP_FRAC of the shorter span.
    Two DIFFERENT owners overlapping in time are two legitimate moments (single-owner rebuild) — never
    cross-owner dropped. Keeps the FIRST of a same-owner overlapping pair; persona-blind picks share the
    None owner (byte-identical to the pre-owner dedup)."""
    kept_by_owner: dict[str | None, list[MomentPick]] = {}
    out: list[MomentPick] = []
    for p in sorted(picks, key=lambda x: (x.start, x.end)):
        peers = kept_by_owner.setdefault(_pick_owner(p), [])
        if not any(_picks_overlap(p, q) for q in peers):
            peers.append(p); out.append(p)
    return out

def validate_pick(pick: MomentPick, *, duration: float, src=None, cfg=None, band=None) -> str | None:
    """Return a reason string if the pick is invalid, else None."""
    if not (math.isfinite(pick.start) and math.isfinite(pick.end)):
        return f"non-finite timestamp ({pick.start}->{pick.end})"   # AUDIT H4
    if pick.end <= pick.start:
        return f"end<=start ({pick.start}->{pick.end})"
    if pick.start < 0:
        return f"start<0 ({pick.start})"
    if duration and pick.end > duration + _EOF_TOLERANCE_S:   # duration==0 means unprobed: skip EOF check
        return f"end>{duration} ({pick.end})"
    length = pick.end - pick.start
    if band is not None and duration and duration < band.lo:
        if pick.start > 0 or pick.end < duration - _EOF_TOLERANCE_S:
            return f"too short ({length:.2f}s)"
    elif band is not None and length < band.lo:
        return f"too short ({length:.2f}s)"
    elif band is None and length <= _MIN_MOMENT_S:
        return f"too short ({length:.2f}s)"
    if not (pick.reason or "").strip():
        return "blank reason"   # MOM-6: a rationale-less pick rides the casting fit signal + hook brief blind
    if src is not None and cfg is not None and (pick.transcript_excerpt or "").strip():
        from fanops.transcribe import window_has_trusted_speech
        if not window_has_trusted_speech(src, pick.start, pick.end):
            get_logger(cfg)("moments", getattr(src, "id", "-"), "pick_speech_mismatch", warn=True)
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
    except Exception as exc:
        _log.warning("_corpus_hit: corpus scan failed (%s)", exc)
        return False
    return False
def _bounded_transcript(transcript: list, peaks: list, *, corpus=None, src_lang=None,
                        cfg=None, source_id: str | None = None) -> tuple:
    """Return (segments_to_send, dropped_count). Keeps segments whose [start,end] midpoint is nearest a peak's
    `t` until the char budget is spent; preserves chronological order. Empty/under-budget -> (transcript, 0).
    When corpus is non-empty, corpus-matching segments rank ahead of equidistant non-matches (bonus, not filter).
    Full-trust segments only — untrusted ASR is dropped before budgeting."""
    from fanops.transcribe import trusted_segments
    raw = transcript or []
    segs = trusted_segments(raw, src_lang=src_lang)
    dropped_untrusted = len(raw) - len(segs)
    if dropped_untrusted and cfg:
        get_logger(cfg)("source", source_id or "-", "speech_untrusted_dropped", count=dropped_untrusted)
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

def _owner_profile(cfg: Config, a) -> str:
    """Pin > cut_policy-derived > global FANOPS_CLIP_PROFILE. ONE resolver for prompt, ingest, stamp."""
    from fanops.persona_directives import resolved_cut_spec
    prof, _ = resolved_cut_spec(a)
    return prof or cfg.resolve_clip_profile(a)

def _band_for_pick(cfg: Config, pick, by_handle: dict):
    from fanops.bands import band_for
    owner = _pick_owner(pick)
    acct = by_handle.get(owner) if owner else None
    if acct is not None:
        return band_for(_owner_profile(cfg, acct))
    return band_for(cfg.clip_profile)

def _persona_entry(cfg: Config, a) -> dict:
    """Per-account pick spec — handle + compiled lens (directive/band/scope) for the pick prompt."""
    from fanops.persona_directives import casting_directive, resolved_cut_spec
    from fanops.bands import band_for
    d = casting_directive(a)
    prof = _owner_profile(cfg, a)
    band = band_for(prof)
    pin_fr = (getattr(a, "framing", None) or "").strip().lower()
    _, derived_fr = resolved_cut_spec(a)
    framing = pin_fr if pin_fr in ("top", "center") else (derived_fr or ("top" if cfg.resolve_top_bias(a) else "center"))
    intensity = (getattr(a, "intensity", None) or "")
    if isinstance(intensity, str): intensity = intensity.strip().lower() or None
    else: intensity = None
    cf = getattr(a, "content_focus", None)
    cf_s = cf if isinstance(cf, str) else ""
    return {"handle": a.handle,
            "directive": (d.select_rule or d.register) if d else "",
            "selection_scope": (d.scope_lens if d else "") or (getattr(a, "selection_scope", None) or ""),
            "band": f"{band.lo:g}-{band.hi:g}s",
            "framing": framing,
            "content_focus": cf_s,
            "intensity": intensity or "",
            "hook_angle": (getattr(a, "hook_angle", None) or ""),
            "corpus": list(getattr(a, "hashtag_corpus", None) or [])}

def _pick_personas(cfg: Config, accounts) -> list[dict]:
    """P4a: ONE assembly point for the per-active-persona FULL spec the pick + downstream gates read.
    Returns handle+directive+selection_scope+band+framing+hook_angle+corpus. Empty when casting OFF. Every active account is included (directive-less still get a gate). Fail-open: a bad account row is skipped."""
    if accounts is None or not cfg.account_casting:
        return []
    out: list[dict] = []
    for a in accounts.active():
        try:
            out.append(_persona_entry(cfg, a))
        except Exception as exc:
            get_logger(cfg)("moments", getattr(a, "handle", "-"), "persona_entry_skipped", err=str(exc)[:120])
            continue
    return out

def _targeted_active_accounts(led: Ledger, cfg: Config, source_id: str, accounts):
    """Batch target_accounts ∩ active accounts when casting ON; None -> legacy bare gate (casting OFF /
    accounts None / no actives). Directive is NOT a filter — directive-less actives still get a gate."""
    if accounts is None or not cfg.account_casting:
        return None
    actives = list(accounts.active())
    if not actives:
        return None
    src = led.sources.get(source_id)
    tgt: list[str] = []
    if src and getattr(src, "batch_id", None):
        b = led.get_batch(src.batch_id)
        if b is not None:
            tgt = list(b.target_accounts or [])
    if not tgt:
        return actives
    want = set(tgt)
    return [a for a in actives if a.handle in want]

def _persona_peaks(peaks: list[dict], personas: list[dict]) -> list[dict]:
    """P4b: attach each persona's intensity-filtered peak view (ONE gate, per-persona lens)."""
    if not personas: return personas
    from fanops.signals import filter_peaks_by_intensity
    for pe in personas:
        pe["signal_peaks"] = filter_peaks_by_intensity(peaks, pe.get("intensity") or None)
    return personas

def request_moments(led: Ledger, cfg: Config, source_id: str, accounts=None, *, guidance=None,
                    origin: str = "operator", operator_release: bool = False) -> Ledger:
    """M1b PASS 1 — request the WINDOWS. Per-account isolation: casting ON fans one gate per targeted
    active account (`{source_id}.{handle}`); casting OFF keeps the legacy bare source gate.

    `origin` names WHO asked. `operator_release=True` is the one key that opens the admission guard
    below WITHOUT relabelling the request as operator work — the release path must keep the machine
    provenance it is releasing."""
    src = led.sources[source_id]
    # T2.3 ADMISSION. FANOPS_QUEUE_GATE holds NEW footage at SourceState.pending until an operator
    # releases it — but a machine RE-OPEN of an already-catalogued source (adjust.amplify) calls this
    # function directly, so it never meets that gate and the tick's converge loop carries it to
    # completion in one pass. Park it on the source instead of serving it: no _source_frames read, no
    # write_request, no set_source_state — nothing is rendered until an operator releases it via
    # studio.actions_run.release_reopens, which re-enters here with operator_release=True (without
    # that key the release would re-enter this same guard and re-park itself forever). Last write
    # wins: at most one parked re-open per source, mirroring write_request's stale-request policy.
    # BYTE-IDENTICAL on the two paths this ticket does not touch: the operator's own path
    # (origin == "operator") short-circuits on the first term without even reading cfg, and a gate-OFF
    # deployment (FANOPS_QUEUE_GATE=0) fails the second — both fall through to exactly the code below.
    if origin != "operator" and cfg.queue_gate and not operator_release:
        src.meta["pending_reopen"] = {"origin": origin, "guidance": guidance or "",
                                      "requested_at": iso_z(datetime.now(timezone.utc))}
        get_logger(cfg)("source", source_id, "reopen_parked", origin=origin)
        return led
    frames = _source_frames(cfg, src)
    peaks = src.signal_peaks or []
    g = load_guidance(cfg) if guidance is None else guidance
    targets = _targeted_active_accounts(led, cfg, source_id, accounts)
    if targets is None:
        transcript, dropped = _bounded_transcript(src.transcript or [], peaks,
                                                    src_lang=src.language, cfg=cfg, source_id=source_id)
        if dropped:
            get_logger(cfg)("source", source_id, "transcript_truncated", dropped=dropped, total=len(src.transcript or []))
        personas = _persona_peaks(peaks, _pick_personas(cfg, accounts))
        payload = MomentRequest(source_id=source_id, request_id="",
                                duration=src.duration or 0.0,
                                transcript=transcript,
                                transcript_total=len(src.transcript or []),
                                signal_peaks=peaks,
                                language=src.language,
                                guidance=g,
                                clip_profile=cfg.clip_profile,
                                personas=personas,
                                frames=frames).model_dump()
        payload.pop("request_id", None)
        if not payload.get("personas"):
            payload.pop("personas", None)
        discard_gates_for(cfg, "moments", source_id)
        write_request(cfg, kind="moments", key=source_id, payload=payload)
        led.set_source_state(source_id, SourceState.moments_requested)
        return led
    if not targets:
        get_logger(cfg)("source", source_id, "no_targeted_active_accounts", warn=True)
        discard_gates_for(cfg, "moments", source_id)   # L07: drop stale gates before terminal flip
        led.set_source_state(source_id, SourceState.moments_empty)
        return led
    discard_gates_for(cfg, "moments", source_id)
    from fanops.signals import filter_peaks_by_intensity
    for a in targets:
        entry = _persona_entry(cfg, a)
        persona_peaks = filter_peaks_by_intensity(peaks, entry.get("intensity") or None)
        transcript, dropped = _bounded_transcript(src.transcript or [], persona_peaks, corpus=entry.get("corpus"),
                                                    src_lang=src.language, cfg=cfg, source_id=source_id)
        if dropped:
            get_logger(cfg)("source", source_id, "transcript_truncated", dropped=dropped,
                            total=len(src.transcript or []), handle=a.handle)
        pe = {**entry, "signal_peaks": persona_peaks}
        payload = MomentRequest(source_id=source_id, request_id="",
                                duration=src.duration or 0.0,
                                transcript=transcript,
                                transcript_total=len(src.transcript or []),
                                signal_peaks=persona_peaks,
                                language=src.language,
                                guidance=g,
                                clip_profile=_owner_profile(cfg, a),
                                personas=[pe],
                                frames=frames).model_dump()
        payload.pop("request_id", None)
        write_request(cfg, kind="moments", key=f"{source_id}.{a.handle}", payload=payload)
    led.set_source_state(source_id, SourceState.moments_requested)
    return led

def _stamp_owner_spec(cfg: Config, owner: str | None, by_handle: dict) -> tuple[str | None, str | None]:
    """P5: resolve clip_profile + framing from the pick's single owner Account. persona-blind -> (None, None)."""
    if not owner:
        return None, None
    acct = by_handle.get(owner)
    if acct is None:
        return None, None
    from fanops.persona_directives import resolved_cut_spec
    prof = _owner_profile(cfg, acct)
    pin_fr = (getattr(acct, "framing", None) or "").strip().lower()
    _, derived_fr = resolved_cut_spec(acct)
    fr = pin_fr if pin_fr in ("top", "center") else (derived_fr or ("top" if cfg.resolve_top_bias(acct) else "center"))
    return prof, fr

def _reconcile_valid_picks(led: Ledger, cfg: Config, source_id: str, deduped: list[MomentPick]) -> Ledger:
    """Upsert deduped valid picks + hook-gate sweep + picks_decided. Caller handles empty/error."""
    try:
        from fanops.accounts import Accounts
        by_handle = {a.handle: a for a in Accounts.load(cfg).accounts}
    except Exception as exc:
        get_logger(cfg)("source", source_id, "accounts_load_failed", err=str(exc)[:120])
        by_handle = {}
    keep: dict[str, Moment] = {}
    owner_n: dict[str, int] = {}
    for pick in deduped:
        o = _pick_owner(pick) or ""
        owner_n[o] = owner_n.get(o, 0) + 1
    if owner_n:
        get_logger(cfg)("source", source_id, "owner_picks", **owner_n)
    src = led.sources[source_id]
    log = get_logger(cfg)
    from fanops.transcribe import excerpt_for_window
    for pick in deduped:
        token = _token(pick)
        owner = (pick.personas or [None])[0]
        mid = _owned_moment_id(source_id, owner, token)
        clip_prof, framing = _stamp_owner_spec(cfg, owner, by_handle)
        excerpt = excerpt_for_window(src, pick.start, pick.end) or ""
        llm_excerpt = (pick.transcript_excerpt or "").strip()
        if llm_excerpt and not excerpt:
            log("source", source_id, "excerpt_overwritten", token=token)
        keep[mid] = Moment(id=mid, parent_id=source_id, state=MomentState.picked,
                           content_token=token, start=pick.start, end=pick.end,
                           reason=pick.reason,
                           transcript_excerpt=excerpt,
                           signal_score=pick.signal_score,
                           affinities=list(pick.personas),
                           clip_profile=clip_prof, framing=framing,
                           segments=list(pick.segments))
    discard_gates_for(cfg, "moment_hooks", f"{source_id}.")
    led.reconcile_moments(source_id, keep)
    led.set_source_state(source_id, SourceState.picks_decided)
    return led

def _sanitize_source_title(raw: str | None) -> str | None:
    from fanops.text import sanitize_generated_text
    t = sanitize_generated_text(raw, max_words=8)
    return (t[:80] if t else None)

def _maybe_stamp_source_title(led: Ledger, source_id: str, dec: MomentDecision) -> None:
    src = led.sources[source_id]
    if src.title is not None: return
    title = _sanitize_source_title(dec.source_title)
    if title: src.title = title

def _ingest_moments_dotted(led: Ledger, cfg: Config, source_id: str, keys: list[str]) -> Ledger:
    """Atomic union over per-account pick gates — owner from key, personas pinned to owner."""
    src = led.sources[source_id]
    all_picks: list[MomentPick] = []
    any_contrib = False
    any_invalid = False
    all_empty = True
    log = get_logger(cfg)
    try:
        from fanops.accounts import Accounts
        by_handle = {a.handle: a for a in Accounts.load(cfg).accounts}
    except Exception as exc:
        get_logger(cfg)("source", source_id, "accounts_load_failed", err=str(exc)[:120])
        by_handle = {}
    prefix = f"{source_id}."
    for key in sorted(keys):
        dec = read_response(cfg, "moments", key, MomentDecision)
        if dec is None:
            return led
        _maybe_stamp_source_title(led, source_id, dec)
        owner = key.removeprefix(prefix)
        gate_valid: list[MomentPick] = []
        for pick in dec.picks:
            echoed = _pick_owner(pick)
            if echoed and echoed != owner:
                log("moments", f"{source_id}.{owner}", "owner_mismatch", warn=True, echoed=echoed)
            stamped = pick.model_copy(update={"personas": [owner]})
            bad = validate_pick(stamped, duration=src.duration or 0.0, src=src, cfg=cfg,
                                band=_band_for_pick(cfg, stamped, by_handle))
            if bad:
                continue
            gate_valid.append(stamped)
        if gate_valid:
            any_contrib = True
            all_empty = False
            all_picks.extend(gate_valid)
            log("moments", f"{source_id}.{owner}", "contrib", n=len(gate_valid))
        elif dec.picks:
            any_invalid = True
            all_empty = False
            log("moments", f"{source_id}.{owner}", "invalid")
        else:
            log("moments", f"{source_id}.{owner}", "empty")
    if any_contrib:
        deduped = _drop_overlaps(all_picks)
        if len(deduped) < len(all_picks):
            get_logger(cfg)("source", source_id, "overlaps_dropped", count=len(all_picks) - len(deduped))
        return _reconcile_valid_picks(led, cfg, source_id, deduped)
    if any_invalid:
        led.set_source_state(source_id, SourceState.error, error_reason="all per-account moment picks invalid")
        return led
    if all_empty:
        get_logger(cfg)("source", source_id, "zero_moments", warn=True)
        led.set_source_state(source_id, SourceState.moments_empty)
    return led

def ingest_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    """M1b PASS 1 ingest — validate + reconcile picks into `picked` moments. Per-account gates aggregate
    atomically when `{source_id}.*` keys exist; else the legacy bare source gate (casting OFF)."""
    dotted = gate_keys_for(cfg, "moments", f"{source_id}.")
    if dotted:
        return _ingest_moments_dotted(led, cfg, source_id, dotted)
    dec = read_response(cfg, "moments", source_id, MomentDecision)
    if dec is None:
        return led
    _maybe_stamp_source_title(led, source_id, dec)
    src = led.sources[source_id]
    try:
        from fanops.accounts import Accounts
        by_handle = {a.handle: a for a in Accounts.load(cfg).accounts}
    except Exception as exc:
        get_logger(cfg)("source", source_id, "accounts_load_failed", err=str(exc)[:120])
        by_handle = {}
    rejected = 0
    reasons: list[str] = []
    valid: list[MomentPick] = []
    for pick in dec.picks:
        bad = validate_pick(pick, duration=src.duration or 0.0, src=src, cfg=cfg,
                            band=_band_for_pick(cfg, pick, by_handle))
        if bad:
            rejected += 1; reasons.append(bad)
            continue
        valid.append(pick)
    deduped = _drop_overlaps(valid)
    if len(deduped) < len(valid):
        get_logger(cfg)("source", source_id, "overlaps_dropped", count=len(valid) - len(deduped))
    if not deduped:
        if dec.picks:
            led.set_source_state(source_id, SourceState.error,
                error_reason=f"all {rejected} moment picks invalid: {'; '.join(sorted(set(reasons)))[:200]}")
        else:
            get_logger(cfg)("source", source_id, "zero_moments", warn=True)
            led.set_source_state(source_id, SourceState.moments_empty)
        return led
    return _reconcile_valid_picks(led, cfg, source_id, deduped)


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

def _unhooked_decided(led: Ledger, source_id: str) -> list:
    """Decided or clipped moments that never got a hook and were not ingest-stripped.
    Skip leftovers render to clipped (hook=None) — PASS 2 still never authored; reopen them too."""
    return [m for m in led.moments.values()
            if m.parent_id == source_id
            and m.state in (MomentState.decided, MomentState.clipped)
            and not (m.hook or "").strip()
            and not (m.hook_removed or "").strip()]


def _fake_null_hook_answer(cfg: Config, source_id: str, m) -> bool:
    """True when PASS 2 wrote hook=None onto disk (the old no-speech skip / author-null). A decided
    hookless moment with NO gate file is a test/render fixture, not a skip leftover."""
    dec = read_response(cfg, "moment_hooks", _hook_gate_key(source_id, m), MomentHookDecision)
    return dec is not None and not (dec.hook or "").strip()


def source_needs_asr_retry(led: Ledger, cfg: Config, source_id: str) -> bool:
    """True when PASS 2 would only have frames+reason because the window has no trusted speech
    AND vocal isolation never ran. Frames+reason are not a substitute — isolate+ASR must re-run
    once. Missing media / already-retried / already-isolated → False (author opens with what exists)."""
    src = led.sources.get(source_id)
    if src is None or not cfg.isolate_vocals:
        return False
    if src.meta.get("vocals_isolated"):
        return False
    if not (src.source_path and os.path.exists(src.source_path)):
        return False
    from fanops.transcribe import asr_retry_marker, window_has_trusted_speech
    stem = Path(src.source_path).stem
    out = cfg.agent_io / "transcripts"
    if (out / f"{stem}.mp3").exists() or asr_retry_marker(cfg, src.source_path).exists():
        return False
    for m in led.moments.values():
        if m.parent_id != source_id:
            continue
        if m.state is MomentState.picked:
            pass
        elif m.state in (MomentState.decided, MomentState.clipped) and (
                getattr(m, "hook_frames_unread", False)
                or (not (m.hook or "").strip() and not (m.hook_removed or "").strip())):
            pass
        else:
            continue
        dur = src.duration or 0.0
        hi = dur if dur > 0 else float("inf")
        cs, ce = fit_window(m.start, m.end, dur, lo=0.0, hi=hi)
        if not window_has_trusted_speech(src, cs, ce):
            return True
    return False


def source_needs_hook_pass(led: Ledger, cfg: Config, source_id: str) -> bool:
    """True when PASS 2 still owes a hook: picks waiting, a skip/null answer still on disk,
    or a hook authored without actually reading the attached frames."""
    src = led.sources.get(source_id)
    if src is None:
        return False
    if src.state is SourceState.picks_decided:
        return True
    if any(_fake_null_hook_answer(cfg, source_id, m) for m in _unhooked_decided(led, source_id)):
        return True
    return any(getattr(m, "hook_frames_unread", False)
               for m in led.moments.values()
               if m.parent_id == source_id and m.state is MomentState.decided)


def _reopen_unhooked(led: Ledger, cfg: Config, source_id: str) -> int:
    """Demote skip/null-hook and unread-frame decided moments back to picked and drop their
    answers so request_moment_hooks can open a real author gate. Not a backfill job — PASS 2
    was never done (null skip) or was not frame-grounded (unread)."""
    n = 0
    for m in _unhooked_decided(led, source_id):
        if not _fake_null_hook_answer(cfg, source_id, m):
            continue
        discard_gate(cfg, "moment_hooks", _hook_gate_key(source_id, m))
        led.set_moment_state(m.id, MomentState.picked)
        n += 1
    for m in list(led.moments.values()):
        if m.parent_id != source_id or m.state is not MomentState.decided:
            continue
        if not getattr(m, "hook_frames_unread", False):
            continue
        discard_gate(cfg, "moment_hooks", _hook_gate_key(source_id, m))
        led.moments[m.id] = m.model_copy(update={"hook_frames_unread": False})
        led.set_moment_state(m.id, MomentState.picked)
        n += 1
    if n:
        led.set_source_state(source_id, SourceState.picks_decided)
        get_logger(cfg)("source", source_id, "hooks_reopened", count=n)
    return n


def request_moment_hooks(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 2 request — open ONE frame-seeing hook gate per `picked` moment of this source. Each
    request carries the picked WINDOW + stills extracted over that window (fit_window — the same cut the
    renderer makes), plus the per-account personas + learned hook styles (the hook-authoring context that
    used to ride the single-pass gate). Write-ONCE per moment (guard: a request already on disk is never
    re-stamped, so an in-flight answer is never invalidated). The source stays `picks_decided`;
    ingest_moment_hooks promotes it once every pick's hook has landed.

    A clip without a hook is not a valid completion. Speech-trust does not auto-answer hook=None.
    Already-decided hookless moments (skip leftovers) and unread-frame hooks are reopened here.
    The author gets a LIVE transcript excerpt (trust recomputed); frames+reason without words
    defer the gate so isolate+ASR can re-run once."""
    _reopen_unhooked(led, cfg, source_id)
    src = led.sources[source_id]
    # P6: each moment's hook author sees ONLY its owner (m.affinities[0]); persona-blind moments get no
    # personas key content -> the shared hook path (byte-identical fallback).
    # P4(c): cross-surface union of gated winning hook STYLES (the SAME signal caption uses). [] when the
    # flag is off / accounts is None / on any scorer error (fail-open).
    styles = proven_hook_styles(led, cfg, accounts)
    guidance = load_guidance(cfg)
    from fanops.transcribe import excerpt_for_window
    retry = source_needs_asr_retry(led, cfg, source_id)
    for m in list(led.moments.values()):
        if m.parent_id != source_id or m.state is not MomentState.picked:
            continue
        key = _hook_gate_key(source_id, m)
        if latest_request_id(cfg, "moment_hooks", key) is not None:
            continue                                # write-ONCE: never re-stamp an existing (pending/answered) gate
        owner = (m.affinities or [None])[0]
        if owner:
            discard_gate(cfg, "moment_hooks", f"{source_id}.{m.content_token}")
        dur = src.duration or 0.0
        hi = dur if dur > 0 else float("inf")
        cs, ce = fit_window(m.start, m.end, dur, lo=0.0, hi=hi)   # EOF clamp only — the picked moment
        env_peaks = [p for p in (src.signal_peaks or []) if _peak_in_window(p, cs, ce)]
        if m.segments:
            span_peaks = [p for p in (src.signal_peaks or []) if _peak_in_segments(p, m.segments)]
            peaks = span_peaks if span_peaks else env_peaks   # fail-open to envelope peaks
        else:
            peaks = env_peaks
        segs = list(m.segments) if m.segments else None
        excerpt = excerpt_for_window(src, cs, ce)
        if excerpt and excerpt != (m.transcript_excerpt or ""):
            led.moments[m.id] = m.model_copy(update={"transcript_excerpt": excerpt})
            m = led.moments[m.id]
        if not excerpt and retry:
            get_logger(cfg)("source", source_id, "hook_deferred_asr_retry", moment=m.id)
            continue
        personas = _hook_personas_for_moment(m, accounts)
        payload = MomentHookRequest(source_id=source_id, moment_id=m.id, token=m.content_token,
                                    request_id="", start=m.start, end=m.end, reason=m.reason,
                                    transcript_excerpt=excerpt or m.transcript_excerpt,
                                    signal_score=m.signal_score,
                                    language=src.language, guidance=guidance,
                                    clip_profile=cfg.clip_profile,
                                    frames=_window_frames(cfg, src, cs, ce, segments=segs),
                                    signal_peaks=peaks, personas=personas).model_dump()
        payload.pop("request_id", None)
        if styles:
            payload["learned_hooks"] = styles      # optional KEY (mirrors caption), not a model field
        write_request(cfg, kind="moment_hooks", key=key, payload=payload)
    return led

def _hook_lang_base(text: str) -> str | None:
    """Detect base language of a short hook string via Unicode script analysis (mirrors caption._lang_base).
    Arabic-script majority (>40% of alphabetic chars in U+0600-U+06FF) → 'ar'; Latin-only → 'en';
    None for indeterminate (no alpha chars at all). Reliable ONLY for the Arabic vs. Latin split."""
    alpha = sum(1 for c in text if c.isalpha())
    if not alpha: return None
    ar = sum(1 for c in text if '\u0600' <= c <= '\u06ff')
    return 'ar' if ar / alpha > 0.4 else 'en'

def ingest_moment_hooks(led: Ledger, cfg: Config, source_id: str, accounts=None) -> Ledger:
    """M1b PASS 2 ingest — apply the window-grounded hooks to a source's `picked` moments and promote them
    to `decided`. ATOMIC PER SOURCE (review fix): we wait until EVERY pick's gate has a valid answer, then
    author all of them in ONE deterministic (start,end)-ordered pass — exactly like the old single-pass
    ingest_moments. Doing it incrementally (a pick promotes the instant its own gate lands) made the
    cross-clip + opening-template dedup ORDER-DEPENDENT (an exact dup could ship twice, or a different
    pick get stripped, by pure response-arrival order). While any pick is still pending the source stays
    `picks_decided` (VISIBLE in awaiting.moment_hooks — never a silent wedge). A null author hook promotes
    CLEAN (hook=None) through the dedup loop — never a retry wedge."""
    picked = sorted([m for m in led.moments.values()
                     if m.parent_id == source_id and m.state is MomentState.picked],
                    key=lambda m: (m.start, m.end))   # stable pick order -> deterministic dedup
    if not picked:
        return led
    # ATOMIC: gather every pick's decision first; if ANY hasn't landed (read_response None), wait — no
    # partial promotion, so the dedup below always sees the WHOLE source at once (order-independent).
    decisions: dict[str, MomentHookDecision] = {}
    for m in picked:
        dec = read_response(cfg, "moment_hooks", _hook_gate_key(source_id, m), MomentHookDecision)
        if dec is None:
            return led                              # not all hooks in yet -> leave the source picks_decided
        decisions[m.id] = dec
    # Cross-clip hook de-dup: seed `used` from OTHER sources' hooks (an EXACT repeat reads like a bot);
    # `cluster_used` (the opening-template scope) starts empty and accumulates within THIS atomic pass —
    # byte-identical to the old single-pass loop. Both grow as we accept hooks in pick order.
    from fanops.caption import _lang_base, brand_risk_flag    # function-local: avoids module cycle; _lang_base for lang gate
    src_lang = _lang_base((led.sources[source_id].language if source_id in led.sources else None))
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
        if hook and src_lang:                       # language gate: hook script must match source language (fail-open when src_lang unknown)
            hook_lang = _hook_lang_base(hook)
            if hook_lang is not None and hook_lang != src_lang:
                hook_removed = hook
                hook = None                         # wrong language → ships CLEAN; Review can restore
        if hook:
            used.add(hook.lower()); cluster_used.add(hook.lower())
            clear_attempts(cfg, "moment_hooks", _hook_gate_key(source_id, m))
        led.moments[m.id] = m.model_copy(update={"hook": hook, "hook_removed": hook_removed,
                                                 "hook_frames_unread": bool(getattr(dec, "hook_frames_unread", False))})  # AGENT-9
        led.set_moment_state(m.id, MomentState.decided)
    led.set_source_state(source_id, SourceState.moments_decided)   # every pick's hook landed atomically
    return led
