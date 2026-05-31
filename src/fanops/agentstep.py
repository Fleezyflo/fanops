# src/fanops/agentstep.py
"""File contract between deterministic code and the agent. Code writes
<kind>__<key>.request.json (stamped with a fresh request_id); the agent writes
<kind>__<key>.response.json echoing that request_id; code validates it AND checks the id
matches the latest request (FIX F21 — a stale response can never be applied)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Type, TypeVar
from pydantic import BaseModel, ValidationError
from fanops.config import Config
from fanops.ids import _hash

T = TypeVar("T", bound=BaseModel)

def _dir(cfg: Config) -> Path:
    d = cfg.agent_io / "requests"
    d.mkdir(parents=True, exist_ok=True)
    return d

def request_path(cfg: Config, kind: str, key: str) -> Path:
    return _dir(cfg) / f"{kind}__{key}.request.json"

def response_path(cfg: Config, kind: str, key: str) -> Path:
    return _dir(cfg) / f"{kind}__{key}.response.json"

def latest_request_id(cfg: Config, kind: str, key: str) -> str | None:
    p = request_path(cfg, kind, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("request_id")
    except Exception:
        return None

def write_request(cfg: Config, *, kind: str, key: str, payload: dict) -> str:
    p = request_path(cfg, kind, key)
    # New id whenever the request is (re)written — old responses become stale.
    prev = latest_request_id(cfg, kind, key) or "0"
    rid = _hash(kind, key, prev, json.dumps(payload, sort_keys=True, default=str))
    payload = {**payload, "request_id": rid}
    p.write_text(json.dumps(payload, indent=2, default=str))
    # a freshly (re)written request invalidates any prior response on disk
    # Single-writer, small JSON: a plain write + unlink is sufficient here. The
    # request_id check in read_response/pending is the real safety net (a torn or
    # stale response can never be *applied*), so we intentionally skip the
    # temp-file+os.replace+lock machinery the ledger needs for its multi-stage state.
    rp = response_path(cfg, kind, key)
    if rp.exists():
        rp.unlink()
    return rid

def read_response(cfg: Config, kind: str, key: str, model: Type[T]) -> T | None:
    rp = response_path(cfg, kind, key)
    if not rp.exists():
        return None
    want = latest_request_id(cfg, kind, key)
    try:
        data = json.loads(rp.read_text())
    except Exception:
        return None
    if want is not None and data.get("request_id") != want:
        return None                                   # stale — ignore
    try:
        return model(**data)
    except ValidationError:
        return None

def pending(cfg: Config, *, kind: str) -> list[str]:
    out = []
    for req in sorted(_dir(cfg).glob(f"{kind}__*.request.json")):
        key = req.name[len(kind) + 2:-len(".request.json")]
        rp = response_path(cfg, kind, key)
        want = latest_request_id(cfg, kind, key)
        ok = False
        if rp.exists():
            try:
                ok = json.loads(rp.read_text()).get("request_id") == want
            except Exception:
                ok = False
        if not ok:
            out.append(key)
    return out
