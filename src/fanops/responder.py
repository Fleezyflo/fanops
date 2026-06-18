# src/fanops/responder.py
"""Autonomous agent-gate answerer (FIX F02/F13 + AUDIT B1/H2/N1). Behind the file contract: reads
pending *.request.json, produces a schema-valid *.response.json. ManualResponder = no-op (a human
writes the files). LlmResponder = calls `claude -p` (via fanops.llm.claude_json) with the committed
prompt + the exact pydantic JSON schema, validates the output, and writes the response. Each request
is QUARANTINED (one bad gate logs + stays pending, never halts the others — mirrors advance()'s
per-unit quarantine). get_responder() picks by FANOPS_RESPONDER and returns a WORKING llm responder."""
from __future__ import annotations
import hashlib
import json
from typing import Callable, Optional
from pydantic import ValidationError
from fanops.config import Config
from fanops.models import MomentDecision, CaptionSet, HookEditDecision, HookJudgeDecision
from fanops.agentstep import pending, request_path, response_path, latest_request_id
from fanops.llm import claude_json_meta, LlmTimeoutError
from fanops.prompts import moment_prompt, caption_prompt, hookedit_prompt, hookjudge_prompt
from fanops.control import guidance_sha
from fanops.log import get_logger

# hookedit (feed-aware hook editor) + hookjudge (specificity critic) ride the same gate contract: when
# no request of that kind is pending the inner loop is empty, so registering them is inert unless
# cfg.hook_editor is on. BOTH are vision calls (Task 6) — _default_claude_model attaches each item's
# frames as images for hookedit AND hookjudge, so the critic SEES the footage it judges.
_SCHEMA = {"moments": MomentDecision, "captions": CaptionSet, "hookedit": HookEditDecision,
           "hookjudge": HookJudgeDecision}
_PROMPT = {"moments": moment_prompt, "captions": caption_prompt, "hookedit": hookedit_prompt,
           "hookjudge": hookjudge_prompt}

class ManualResponder:
    def __init__(self, cfg: Config): self.cfg = cfg
    def answer_pending(self, cfg: Config) -> int:
        return 0                                    # a human (or external cron) writes responses

def _default_claude_model(kind: str, payload: dict, *, cfg: Config | None = None, log=None) -> dict:
    """The production model: hand claude -p the committed prompt + the gate's JSON schema, PINNED to
    cfg.llm_model (V2 M1/F1 — an unpinned `claude -p` drifts with whatever the CLI defaults to). For
    the hookedit AND hookjudge gates, also hand it the clip frames (collected from the payload items)
    as images so the editor/critic SEES each clip and grounds its rewrite/verdict in the footage;
    moments/captions stay text-only. When cfg is given, emit ONE provenance line per call (the model
    that ANSWERED, the prompt fingerprint, and the brief fingerprint) so every creative output is
    traceable to the exact model + brief that produced it (M1/F10). cfg=None (the legacy test path)
    keeps the old behavior: no pin, no provenance."""
    schema = _SCHEMA[kind].model_json_schema()
    images = None
    if kind in ("hookedit", "hookjudge"):
        images = [f for it in payload.get("items", []) for f in (it.get("frames") or [])] or None
    prompt = _PROMPT[kind](payload)
    out, answered = claude_json_meta(prompt, schema, images=images,
                                     model=(cfg.llm_model if cfg else None))
    if cfg is not None:
        emit = log or get_logger(cfg)
        uid = str(payload.get("source_id") or payload.get("clip_id") or kind)
        emit("llm", uid, "call", model=answered or cfg.llm_model,
             prompt_sha=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
             brief_sha=guidance_sha(cfg))
    return out

class LlmResponder:
    """model(kind, request_payload_dict) -> response_dict. Defaults to `claude -p`; injectable for
    tests so no network/subprocess is needed."""
    def __init__(self, cfg: Config, model: Optional[Callable[[str, dict], dict]] = None):
        self.cfg = cfg
        # the default model binds cfg so it pins cfg.llm_model + emits the provenance line; an injected
        # test model keeps the bare (kind, payload) -> dict contract.
        self._model = model or (lambda kind, payload: _default_claude_model(kind, payload, cfg=cfg))

    def answer_pending(self, cfg: Config) -> int:
        log = get_logger(cfg)
        answered = 0
        for kind, model_cls in _SCHEMA.items():
            for key in pending(cfg, kind=kind):
                try:                                # decision (b): quarantine per request
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
                        continue                    # do not write a stale-payload answer
                    out = {**out, "request_id": rid_before}   # == rid_after (the still-latest rid)
                    if kind == "moments":           # MomentDecision requires source_id; the GATE is
                        out["source_id"] = payload.get("source_id")   # authoritative (review Issue A) — gate wins, not the model
                    obj = model_cls(**out)          # decision (a): validate; ValidationError -> pending + log
                    response_path(cfg, kind, key).write_text(obj.model_dump_json(indent=2))
                    answered += 1
                except ValidationError as e:        # present-but-invalid: log "invalid", gate stays pending
                    log("responder", f"{kind}:{key}", "invalid", err=str(e)[:160])
                except Exception as e:              # transient model/CLI failure (incl. ToolchainMissing): log, leave pending
                    log("responder", f"{kind}:{key}", "error", err=str(e)[:160])
        return answered

def get_responder(cfg: Config):
    if cfg.responder_mode == "llm":
        return LlmResponder(cfg)                    # now a WORKING responder (claude -p default)
    return ManualResponder(cfg)
