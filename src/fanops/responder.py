# src/fanops/responder.py
"""Autonomous agent-gate answerer (FIX F02/F13 + AUDIT B1/H2/N1). Behind the file contract: reads
pending *.request.json, produces a schema-valid *.response.json. ManualResponder = no-op (a human
writes the files). LlmResponder = calls `claude -p` (via fanops.llm.claude_json_meta) with the committed
prompt + the exact pydantic JSON schema, validates the output, and writes the response. Each request
is QUARANTINED (one bad gate logs + stays pending, never halts the others — mirrors advance()'s
per-unit quarantine). get_responder() picks by FANOPS_RESPONDER and returns a WORKING llm responder."""
from __future__ import annotations
import contextlib
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional
from pydantic import ValidationError
from fanops.config import Config
from fanops.models import MomentDecision, MomentHookDecision, MomentCastingDecision, CaptionSet
from fanops.agentstep import pending, request_path, write_response, latest_request_id
from fanops.llm import claude_json_meta, LlmTimeoutError, LlmContextLimitError
from fanops.prompts import moment_pick_prompt, moment_hook_prompt, moment_casting_prompt, caption_prompt
from fanops.control import guidance_sha
from fanops.log import get_logger

def screen_model_text(obj):
    """MOL-166: ONE text-screen chokepoint — sanitize model-authored strings before *.response.json is written."""
    from fanops.text import sanitize_generated_text
    if isinstance(obj, MomentDecision):
        picks = [p.model_copy(update={"reason": sanitize_generated_text(p.reason) or ""}) for p in obj.picks]
        return obj.model_copy(update={"picks": picks})
    if isinstance(obj, MomentHookDecision):
        h = (obj.hook or "").strip()
        hook = sanitize_generated_text(h) if h else None
        return obj.model_copy(update={"hook": hook})
    if isinstance(obj, CaptionSet):
        items = []
        for item in obj.items:
            items.append(item.model_copy(update={
                "caption": sanitize_generated_text(item.caption) or "",
                "hashtags": [sanitize_generated_text(t) or "" for t in (item.hashtags or [])],
            }))
        return obj.model_copy(update={"items": items})
    return obj

# Agent gates: `moments` (M1b pass 1 — pick the WINDOWS, sees whole-source frames), `moment_hooks` (pass 2 —
# the vision hook AUTHOR, sees the PICKED WINDOW's frames), `moment_casting` (M1 Option C — per-account moment
# SELECTION, text-only), and `captions` (text-only hashtags). The two vision gates attach `frames` as images.
_SCHEMA = {"moments": MomentDecision, "moment_hooks": MomentHookDecision,
           "moment_casting": MomentCastingDecision, "captions": CaptionSet}
_PROMPT = {"moments": moment_pick_prompt, "moment_hooks": moment_hook_prompt,
           "moment_casting": moment_casting_prompt, "captions": caption_prompt}
_VISION_GATES = ("moments", "moment_hooks", "moment_casting")   # gates whose payload MAY carry top-level `frames` to attach
# (moment_casting only carries `frames` when keyframes were extracted; an empty/absent list -> images=None -> text-only)

class ManualResponder:
    def __init__(self, cfg: Config): self.cfg = cfg
    def answer_pending(self, cfg: Config) -> int:
        return 0                                    # a human (or external cron) writes responses

def _default_claude_model(kind: str, payload: dict, *, cfg: Config | None = None, log=None) -> dict:
    """The production model: hand claude -p the committed prompt + the gate's JSON schema, PINNED to
    cfg.llm_model_for(kind) (V2 M1/F1 — an unpinned `claude -p` drifts with the CLI default; the tier is
    PER-GATE — opus for the creative VISION moments gate, sonnet for the mechanical caption gate). For
    the two VISION gates (`moments` = window picks, `moment_hooks` = the frame-seeing on-screen-hook author),
    also hand the relevant frames (top-level `frames`) as images; `moment_casting` and `captions` stay
    text-only. When
    cfg is given, emit ONE provenance line per call (the model that ANSWERED, the prompt fingerprint, and
    the brief fingerprint) so every creative output is traceable to the exact model + brief that produced
    it (M1/F10). cfg=None (the legacy test path) keeps the old behavior: no pin, no provenance."""
    schema = _SCHEMA[kind].model_json_schema()
    images = (payload.get("frames") or None) if kind in _VISION_GATES else None   # M1b: pick pass SEES source stills; hook pass SEES the picked WINDOW's stills
    prompt = _PROMPT[kind](payload)
    out, answered, frames_unread = claude_json_meta(prompt, schema, images=images,
                                     model=(cfg.llm_model_for(kind) if cfg else None))
    if cfg is not None:
        emit = log or get_logger(cfg)
        uid = str(payload.get("source_id") or payload.get("clip_id") or kind)
        emit("llm", uid, "call", model=answered or cfg.llm_model_for(kind),
             prompt_sha=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
             brief_sha=guidance_sha(cfg))
        if frames_unread:                               # AGENT-9: a degraded, text-grounded hook — VISIBLE in run.log
            emit("llm", uid, "frames_unread")
    if kind == "moment_hooks" and frames_unread:        # AGENT-9: STAMP the response so ingest lifts it onto the moment
        out = {**out, "hook_frames_unread": True}        # (MomentHookDecision tolerates the key; default False otherwise)
    return out

class LlmResponder:
    """model(kind, request_payload_dict) -> response_dict. Defaults to `claude -p`; injectable for
    tests so no network/subprocess is needed."""
    def __init__(self, cfg: Config, model: Optional[Callable[[str, dict], dict]] = None):
        self.cfg = cfg
        # the default model binds cfg so it pins cfg.llm_model + emits the provenance line; an injected
        # test model keeps the bare (kind, payload) -> dict contract.
        self._model = model or (lambda kind, payload: _default_claude_model(kind, payload, cfg=cfg))

    def _answer_one(self, cfg: Config, kind: str, model_cls, key: str, log) -> bool:
        # One gate's answer (the verbatim body of the old inner loop). Returns True iff a fresh,
        # schema-valid response was written; False on stale-reseed / ValidationError / transient error
        # (gate stays pending, quarantined — never raises). EVERY guard here is per-key local state
        # (rid_before/rid_after, the unique response_path), so it is already thread-safe: the pooled
        # fan-out runs N of these concurrently without shared mutable state or a lock.
        try:                                    # decision (b): quarantine per request
            payload = json.loads(request_path(cfg, kind, key).read_text())
            # AUDIT A3 (answer-stale TOCTOU): capture the rid the payload was read under
            # BEFORE the slow model call. The agent-gate files live OUTSIDE the ledger flock,
            # so an overlapping `fanops run` can re-seed THIS gate (new request_id + new
            # payload, via write_request) WHILE the model call is running. If we read the rid
            # AFTER the call (the old behavior), we would stamp this OLD-payload answer with
            # the NEW rid -> read_response's freshness check would PASS and apply a
            # wrong-payload answer as fresh. So we re-verify AFTER the call that the rid is
            # still latest and drop the answer on mismatch (gate stays pending for the new
            # request). We do NOT hold a lock across the model call: that would serialize the
            # up-to-180s claude -p behind every other gate. (Mirrors the FIX-F21 request_id
            # correlation that already guards the read side.)
            rid_before = latest_request_id(cfg, kind, key)
            try:
                out = self._model(kind, payload)
            except LlmTimeoutError:         # transient: a caption gate timed out (stranded 2 clips before)
                log("responder", f"{kind}:{key}", "timeout_retry", err="model timed out; retrying once")
                out = self._model(kind, payload)   # second timeout -> falls to the except below -> visible, pending
            rid_after = latest_request_id(cfg, kind, key)
            if rid_after is None or rid_after != rid_before:
                log("responder", f"{kind}:{key}", "stale",
                    err=f"gate re-seeded mid-call ({rid_before}->{rid_after}); dropping stale answer")
                return False                    # do not write a stale-payload answer
            out = {**out, "request_id": rid_before}   # gate self-stamps the authoritative rid (== rid_after)
            if kind == "moments":           # MomentDecision.source_id is gate-populated; the GATE wins, not the model
                out["source_id"] = payload.get("source_id")
            obj = model_cls(**out)          # decision (a): validate; ValidationError -> pending + log
            obj = screen_model_text(obj)    # MOL-166: screen model-authored text once at the responder boundary
            write_response(cfg, kind, key, obj.model_dump_json(indent=2))   # ATOMIC (audit): no torn-read window for a concurrent reader
            return True
        except LlmContextLimitError as e:   # AGENT-2: a too-big payload is a LABELLED degraded state, never an
            log("responder", f"{kind}:{key}", "context_limit", err=str(e)[:160])   # infinite-pending wedge
            self._mark_context_limit(cfg, kind, key, str(e)[:160])
            return False
        except ValidationError as e:        # present-but-invalid: log "invalid", gate stays pending
            log("responder", f"{kind}:{key}", "invalid", err=str(e)[:160])
        except Exception as e:              # transient model/CLI failure (incl. ToolchainMissing): log, leave pending
            log("responder", f"{kind}:{key}", "error", err=str(e)[:160])
        return False

    def _mark_context_limit(self, cfg: Config, kind: str, key: str, reason: str) -> None:
        """AGENT-2: park the wedged gate's source-owner with a VISIBLE degraded_reason so the operator sees WHY
        it stalls (master principle: no silent degradation). Best-effort + a breadcrumb; the gate stays pending
        (operator can shrink the source / re-request) but is now diagnosable. The moment gates key on the source id directly;
        the captions gate keys on a CLIP id, so its source is resolved clip->moment->source (else it stalls invisibly) — the context_limit breadcrumb above still fires.
        Loads + saves the ledger OUTSIDE the advance() flock (gates live outside the lock), so a fresh load+save
        is safe. Ledger imported lazily to avoid a module cycle (ledger imports widely)."""
        from fanops.ledger import Ledger
        try:
            led = Ledger.load(cfg)
            if kind in ("moments", "moment_hooks", "moment_casting"):
                sid = key.split(".", 1)[0]
            else:                                          # captions gate keys on a CLIP id -> clip.parent=moment, moment.parent=source
                clip = led.clips.get(key)
                mom = led.moments.get(clip.parent_id) if clip is not None else None
                sid = mom.parent_id if mom is not None else None
            src = led.sources.get(sid) if sid else None
            if src is not None:
                led.sources[sid] = src.model_copy(update={"degraded_reason": f"agent gate {kind} over context limit: {reason}"})
                led.save()
        except Exception as e:              # best-effort: a load/save failure must not crash the responder pass
            with contextlib.suppress(Exception):
                get_logger(cfg)("responder", f"{kind}:{key}", "mark_degraded_failed", err=str(e)[:120])

    def answer_pending(self, cfg: Config) -> int:
        log = get_logger(cfg)
        # Snapshot every pending (kind, model_cls, key) BEFORE any work — pending() is a glob over the
        # gate dir, so it MUST be read serially (never inside a worker). The flat list is the same set
        # of work the sequential loop would visit, in the same order.
        pairs = [(kind, model_cls, key) for kind, model_cls in _SCHEMA.items()
                 for key in pending(cfg, kind=kind)]
        if not cfg.concurrent_sources:
            # DEFAULT OFF — the byte-identical sequential path: answer each gate one at a time, in
            # _SCHEMA-then-pending order, exactly as before. The pool is NOT constructed.
            return sum(self._answer_one(cfg, kind, model_cls, key, log) for kind, model_cls, key in pairs)
        # ON — fan the gate calls out over a bounded pool (parallel-source pipeline). Each (kind, key)
        # is a UNIQUE response_path and the TOCTOU guard is per-key local state, so concurrent writes
        # never collide. answered = the count of True results (a fresh write); no ledger involvement.
        if not pairs:
            return 0
        with ThreadPoolExecutor(max_workers=cfg.concurrent_workers) as ex:   # bound = rate-limit guardrail for claude -p
            futs = [ex.submit(self._answer_one, cfg, kind, model_cls, key, log)
                    for kind, model_cls, key in pairs]
            return sum(fut.result() for fut in as_completed(futs))

def get_responder(cfg: Config):
    if cfg.responder_mode == "llm":
        return LlmResponder(cfg)                    # now a WORKING responder (claude -p default)
    return ManualResponder(cfg)
