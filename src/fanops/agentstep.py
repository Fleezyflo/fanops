# src/fanops/agentstep.py
"""File contract between deterministic code and the agent. Code writes
<kind>__<key>.request.json (stamped with a fresh request_id); a response echoes that request_id and
read_response checks it matches the latest request (FIX F21 — a stale response can never be applied).
On the LLM path the responder SELF-STAMPS the authoritative request_id and VERIFIES the model's echo,
logging a rid_mismatch breadcrumb on divergence (AGENT-1) — the echo is no longer silently trusted."""
from __future__ import annotations
import contextlib, json, os
from pathlib import Path
from typing import Type, TypeVar
from pydantic import BaseModel, ValidationError
from fanops.config import Config
from fanops.ids import _hash
from fanops.log import get_logger

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
    except Exception as e:
        # Corrupt/torn request.json: fail-closed (None) but leave ONE breadcrumb, else a stuck gate
        # is indistinguishable from "no request yet".
        get_logger(cfg)("agent_io", key, "corrupt_request", kind=kind, err=str(e)[:120])
        return None

def write_request(cfg: Config, *, kind: str, key: str, payload: dict) -> str:
    p = request_path(cfg, kind, key)
    # New id whenever the request is (re)written — old responses become stale.
    prev = latest_request_id(cfg, kind, key) or "0"
    rid = _hash(kind, key, prev, json.dumps(payload, sort_keys=True, default=str))
    payload = {**payload, "request_id": rid}
    # ATOMIC write (temp + os.replace, the ledger._save_unlocked pattern): the old plain write_text
    # left a concurrent reader exposed to a torn request — safe ONLY by the implicit "all writers
    # hold the ledger flock" invariant. os.replace makes the swap-in atomic regardless, so a reader
    # always sees either the prior request or the complete new one, never a partial. os.replace is
    # atomic ONLY when tmp and target share a filesystem (audit c2-f3); tmp = p.with_suffix(".json.tmp")
    # keeps it in the SAME directory as the target, so the same-fs precondition holds by construction.
    tmp = p.with_suffix(".json.tmp")
    assert tmp.parent == p.parent             # same-fs precondition for an atomic os.replace (audit c2-f3)
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(str(tmp), str(p))
    # a freshly (re)written request invalidates any prior response on disk. The request_id check in
    # read_response/pending remains the real safety net (a torn or stale response can never be
    # *applied*); the atomic write just removes the torn-read window on the request itself.
    rp = response_path(cfg, kind, key)
    if rp.exists():
        rp.unlink()
    clear_attempts(cfg, kind, key)
    return rid

def write_response(cfg: Config, kind: str, key: str, json_text: str) -> None:
    """Persist a gate ANSWER ATOMICALLY (temp + os.replace — the same write_request/ledger pattern), so a
    concurrent reader (pending()/read_response) never sees a TORN response. The request_id match in
    read_response stays the real safety net (a stale answer is never applied); this removes the torn-READ
    window on the answer file itself, which the old plain write_text left open under overlapping passes."""
    rp = response_path(cfg, kind, key)
    tmp = rp.with_suffix(".json.tmp")
    assert tmp.parent == rp.parent       # same-fs precondition for an atomic os.replace (audit c2-f3)
    tmp.write_text(json_text)
    try: os.chmod(tmp, 0o600)            # owner-only at rest (audit): the answer carries hook/caption/casting content
    except OSError: pass                 # best-effort — never break the gate on a perms quirk
    os.replace(str(tmp), str(rp))

def read_response(cfg: Config, kind: str, key: str, model: Type[T]) -> T | None:
    rp = response_path(cfg, kind, key)
    if not rp.exists():
        return None
    want = latest_request_id(cfg, kind, key)
    try:
        data = json.loads(rp.read_text())
    except Exception as e:
        # Corrupt/torn response.json: fail-closed (None — a corrupt answer must never be applied)
        # but log it, else it looks IDENTICAL to "still pending" and the gate silently stalls.
        get_logger(cfg)("agent_io", key, "corrupt_response", kind=kind, err=str(e)[:120])
        return None
    if want is not None and data.get("request_id") != want:
        return None                                   # stale — ignore
    try:
        return model(**data)
    except ValidationError:
        return None

def discard_gate(cfg: Config, kind: str, key: str) -> None:
    """Remove a gate's request+response files so a SUPERSEDED decision's stale answer can never be
    re-applied to a unit re-created under the same content-addressed key (the two-pass amplify hazard:
    a same-token re-pick would otherwise reuse the prior pick's hook). Idempotent — missing files are fine."""
    for p in (request_path(cfg, kind, key), response_path(cfg, kind, key)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    clear_attempts(cfg, kind, key)

def discard_gates_for(cfg: Config, kind: str, key_prefix: str) -> int:
    """Discard every gate of `kind` whose key starts with `key_prefix` — e.g. all of a source's per-pick
    `moment_hooks__{source_id}.{token}` gates when its pick decision is superseded. The trailing '.' in a
    `{source_id}.` prefix is a literal in the glob, so `source_1.` never matches `source_12.*`. Returns
    the count cleared."""
    n = 0
    for req in sorted(_dir(cfg).glob(f"{kind}__{key_prefix}*.request.json")):
        discard_gate(cfg, kind, req.name[len(kind) + 2:-len(".request.json")])
        n += 1
    return n

def _attempts_path(cfg: Config, kind: str, key: str) -> Path:
    return _dir(cfg) / f"{kind}__{key}.attempts.json"

def bump_attempts(cfg: Config, kind: str, key: str) -> int:
    p = _attempts_path(cfg, kind, key)
    try: n = json.loads(p.read_text()).get("n", 0)
    except Exception: n = 0
    n += 1
    p.write_text(json.dumps({"n": n}))
    return n

def clear_attempts(cfg: Config, kind: str, key: str) -> None:
    with contextlib.suppress(FileNotFoundError): _attempts_path(cfg, kind, key).unlink()

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
            except Exception as e:
                # Torn/corrupt response.json: keep fail-closed (still pending) but leave ONE
                # breadcrumb — read_response/latest_request_id both log corruption; pending was the
                # silent gap, so a stuck gate here is now distinguishable from "no response yet".
                get_logger(cfg)("agent_io", key, "corrupt_response_in_pending", kind=kind, err=str(e)[:120])
                ok = False
        if not ok:
            out.append(key)
    return out
