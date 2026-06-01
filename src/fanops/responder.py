# src/fanops/responder.py
"""Autonomous agent-gate answerer (FIX F02/F13 + AUDIT B1/H2/N1). Behind the file contract: reads
pending *.request.json, produces a schema-valid *.response.json. ManualResponder = no-op (a human
writes the files). LlmResponder = calls `claude -p` (via fanops.llm.claude_json) with the committed
prompt + the exact pydantic JSON schema, validates the output, and writes the response. Each request
is QUARANTINED (one bad gate logs + stays pending, never halts the others — mirrors advance()'s
per-unit quarantine). get_responder() picks by FANOPS_RESPONDER and returns a WORKING llm responder."""
from __future__ import annotations
import json
from typing import Callable, Optional
from pydantic import ValidationError
from fanops.config import Config
from fanops.models import MomentDecision, CaptionSet
from fanops.agentstep import pending, request_path, response_path, latest_request_id
from fanops.llm import claude_json
from fanops.prompts import moment_prompt, caption_prompt
from fanops.log import get_logger

_SCHEMA = {"moments": MomentDecision, "captions": CaptionSet}
_PROMPT = {"moments": moment_prompt, "captions": caption_prompt}

class ManualResponder:
    def __init__(self, cfg: Config): self.cfg = cfg
    def answer_pending(self, cfg: Config) -> int:
        return 0                                    # a human (or external cron) writes responses

def _default_claude_model(kind: str, payload: dict) -> dict:
    """The production model: hand claude -p the committed prompt + the gate's JSON schema."""
    schema = _SCHEMA[kind].model_json_schema()
    return claude_json(_PROMPT[kind](payload), schema)

class LlmResponder:
    """model(kind, request_payload_dict) -> response_dict. Defaults to `claude -p`; injectable for
    tests so no network/subprocess is needed."""
    def __init__(self, cfg: Config, model: Optional[Callable[[str, dict], dict]] = None):
        self.cfg = cfg
        self._model = model or _default_claude_model

    def answer_pending(self, cfg: Config) -> int:
        log = get_logger(cfg)
        answered = 0
        for kind, model_cls in _SCHEMA.items():
            for key in pending(cfg, kind=kind):
                try:                                # decision (b): quarantine per request
                    payload = json.loads(request_path(cfg, kind, key).read_text())
                    out = self._model(kind, payload)
                    rid = latest_request_id(cfg, kind, key)
                    out = {**out, "request_id": rid}
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
