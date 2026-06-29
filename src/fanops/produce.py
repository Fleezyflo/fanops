# src/fanops/produce.py
"""M3 — the single producer module. Lock-free, side-effect-only.

Replaces the prior `pipeline._prewarm` + `_produce_source` shape. The old shape loaded a
PRIVATE THROWAWAY Ledger.load(cfg) per worker, mutated it in memory, then discarded it —
its only valuable output was the on-disk artifacts (transcript JSON, signals sidecar,
detect sidecar, render mp4 + fingerprint, stitch mp4). Readers reasonably wondered why
the throwaway mutation existed at all; the answer was historical (the in-lock reducer
re-derived state from the artifacts). M3 names the contract straight: **producers OWN
artifact writes; the reducer (in `pipeline._stage_*`) OWNS ledger state, derived from
the artifacts on disk**.

Per-stage exclusion is enforced by `stage_lock` (M1/M2 primitive). Two concurrent
producers for the same `(stage, source_id)` are mutexed by the kernel flock; the second
short-circuits on the artifact the first wrote. Concurrent sources run in a bounded
ThreadPoolExecutor exactly as `_prewarm_concurrent` did — the parallelism story is
unchanged.

`run_all(cfg, aspects, log)` is the single entry point `pipeline.advance()` calls."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState, MomentState, Fmt
from fanops.transcribe import transcribe_source
from fanops.signals import detect_signals
from fanops.clip import render_aspects_for
from fanops.stitch_render import prewarm_approved_stitches


@dataclass(frozen=True)
class SourceResult:
    """The PURE result of one source's producer pass: the source id and an optional error
    reason. A producer NEVER mutates a shared ledger / saves / opens a transaction; it
    runs the slow chain side-effect-only and returns one of these. Mirrors the prior
    `pipeline.SourceResult` byte-for-byte so any test importing it still works."""
    source_id: str
    error_reason: str | None = None


def _enabled_strategies(cfg: Config) -> set[str]:
    """The structural-hook formats turned ON this pass — duplicates pipeline._enabled_strategies
    here so produce stays self-contained (a cycle into pipeline.py would land via the import
    side-effect of bringing transcribe/signals/clip back in)."""
    return {k for k, on in (("impact_cut", cfg.impact_cut), ("intro_tease", cfg.intro_tease)) if on}


def _produce_one(cfg: Config, source_id: str, aspects: set[Fmt], *, log) -> SourceResult:
    """Body for ONE source's producer pass. Loads a PRIVATE in-memory ledger (NOT the same as
    saving — the reducer pass is the only writer of the on-disk ledger), runs the slow chain
    (transcribe -> detect_signals -> render_aspects_for) to populate the on-disk artifacts that
    the reducer then adopts. The in-memory ledger here is discarded at return; ONLY the
    artifacts survive.

    Per-stage exclusion: transcribe acquires `stage_lock("transcribe", source_id)` (M1);
    framing.detect_window acquires `stage_lock("framing", source_id)` (M2); keyframes.extract_
    frames_grid acquires `stage_lock("keyframes", window_hash)` (M2). So two producers for the
    same `(stage, source)` cannot both shell out — the second short-circuits on the artifact
    the first wrote.

    NEVER raises (mirrors `_prewarm`'s per-unit fail-open contract): a per-unit error is logged
    and stamped on the returned SourceResult; the next pass's reducer fingerprint-skips warm
    artifacts and re-tries the missing pieces."""
    err: str | None = None
    try:
        led = Ledger.load(cfg)
    except Exception as e:
        log("produce", source_id, "warn", err=str(e)[:120])
        return SourceResult(source_id, str(e)[:120])
    s = led.sources.get(source_id)
    if s is None or s.origin_kind == "third_party":
        return SourceResult(source_id, None)             # gone / inert — nothing to produce
    try:
        if s.state is SourceState.catalogued:
            led = transcribe_source(led, cfg, source_id)
        if led.sources[source_id].state is SourceState.transcribed:
            led = detect_signals(led, cfg, source_id)
    except Exception as e:
        log("produce", source_id, "warn", err=str(e)[:120])
        err = f"{type(e).__name__}: {e}"
    for m in list(led.moments.values()):
        if m.parent_id != source_id:
            continue                                     # only THIS source's moments (disjoint paths)
        if m.state is MomentState.decided:
            try:
                led, _ = render_aspects_for(led, cfg, m.id, aspects=aspects)
            except Exception as e:
                log("produce", m.id, "warn", err=str(e)[:120])
    return SourceResult(source_id, err)


def run_all(cfg: Config, aspects: set[Fmt], log) -> None:
    """The single lock-free producer entry point pipeline.advance() calls between the short
    ingest transaction and the main reduce transaction. Warms every catalogued / transcribed /
    decided unit's on-disk artifacts so the reducer's in-lock transcribe / signals / render
    calls short-circuit (M1 + M2 cache hits) in microseconds — the multi-minute transcodes
    never run inside the ledger flock.

    Concurrency: cfg.concurrent_sources gates a ThreadPoolExecutor (max_workers =
    cfg.concurrent_workers). Default OFF -> sequential per source, byte-identical to the prior
    `_prewarm_sequential` ordering. Either path leaves the SAME on-disk artifacts warm.
    NEVER raises (each producer fail-opens; any thread-level crash is logged and continues)."""
    try:
        led = Ledger.load(cfg)
    except Exception as e:
        log("produce", "-", "warn", err=str(e)[:120])
        return
    ids = [s.id for s in led.sources.values() if s.origin_kind != "third_party"]
    if ids:
        if cfg.concurrent_sources:
            with ThreadPoolExecutor(max_workers=cfg.concurrent_workers) as ex:
                futs = [ex.submit(_produce_one, cfg, sid, aspects, log=log) for sid in ids]
                for fut in as_completed(futs):
                    try:
                        fut.result()                     # each producer already fail-opens
                    except Exception as e:
                        log("produce", "-", "warn",
                            err=f"worker crash: {type(e).__name__}: {str(e)[:120]}")
        else:
            for sid in ids:
                _produce_one(cfg, sid, aspects, log=log)
    # M4/M6 structural-hooks stitch prewarm: warms operator-approved stitch renders lock-free,
    # serial (independent of the per-source map). Both formats OFF -> the call is a no-op.
    strategies = _enabled_strategies(cfg)
    if strategies:
        try:
            prewarm_approved_stitches(led, cfg, log, strategies=strategies)
        except Exception as e:
            log("produce", "-", "warn", err=str(e)[:120])
