"""Autonomous agent-gate answerer (FIX F02/F13). Behind the same file contract: reads pending
*.request.json, produces a schema-valid *.response.json. ManualResponder = no-op (a human/cron
writes the files). LlmResponder = calls a model callable (wire to the Anthropic SDK in prod)
and validates output against MomentDecision/CaptionSet before writing. get_responder() picks by
FANOPS_RESPONDER."""
from __future__ import annotations
import json
from typing import Callable, Optional
from fanops.config import Config
from fanops.models import MomentDecision, CaptionSet
from fanops.agentstep import pending, request_path, response_path, latest_request_id

_SCHEMA = {"moments": MomentDecision, "captions": CaptionSet}

class ManualResponder:
    def __init__(self, cfg: Config): self.cfg = cfg
    def answer_pending(self, cfg: Config) -> int:
        return 0                                    # a human (or external cron) writes responses

class LlmResponder:
    """model(kind, request_payload_dict) -> response_dict. In production this wraps an LLM
    call with a committed prompt template; here it is injected so tests need no network."""
    def __init__(self, cfg: Config, model: Optional[Callable[[str, dict], dict]] = None):
        self.cfg = cfg
        self._model = model or self._default_model

    def _default_model(self, kind: str, payload: dict) -> dict:
        raise RuntimeError("LlmResponder needs a model callable wired (e.g. Anthropic SDK). "
                           "See RUNTIME.md 'wiring the LLM responder'.")

    def answer_pending(self, cfg: Config) -> int:
        answered = 0
        for kind, model_cls in _SCHEMA.items():
            for key in pending(cfg, kind=kind):
                payload = json.loads(request_path(cfg, kind, key).read_text())
                out = self._model(kind, payload)
                rid = latest_request_id(cfg, kind, key)
                out = {**out, "request_id": rid}
                obj = model_cls(**out)              # validate or raise
                response_path(cfg, kind, key).write_text(obj.model_dump_json(indent=2))
                answered += 1
        return answered

def get_responder(cfg: Config):
    if cfg.responder_mode == "llm":
        return LlmResponder(cfg)
    return ManualResponder(cfg)
