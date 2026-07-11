# src/fanops/artifacts.py
"""Advisory per-source artifact manifest + warm-artifact inference/adoption.

The manifest at ``04_agent_io/manifests/{source_id}.json`` records which pipeline stages
completed and where their artifacts live. It is ADVISORY — existing sidecar adopt paths in
transcribe/signals/framing/clip remain authoritative; the manifest answers "what stages
completed for source X?" and powers auto-resume heuristics."""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState

_MANIFEST_V = 1
_STAGE_ORDER = ("transcribe", "signals", "framing", "keyframes", "clip")
_RESUME_AT = {"transcribe": "transcribed", "signals": "signalled", "framing": "signalled",
              "keyframes": "signalled", "clip": "signalled"}


def manifest_path(cfg: Config, source_id: str) -> Path:
    return cfg.agent_io / "manifests" / f"{source_id}.json"


def _write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert tmp.parent == path.parent
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


def _load_manifest(cfg: Config, source_id: str) -> dict:
    p = manifest_path(cfg, source_id)
    if not p.exists():
        return {"v": _MANIFEST_V, "source_id": source_id, "sha256": None, "stages": {}}
    try:
        d = json.loads(p.read_text())
        if not isinstance(d.get("stages"), dict):
            d["stages"] = {}
        return d
    except (OSError, json.JSONDecodeError):
        return {"v": _MANIFEST_V, "source_id": source_id, "sha256": None, "stages": {}}


def stamp_stage(cfg: Config, source_id: str, stage: str, *, artifact: str, schema: int,
                sha256: str | None = None) -> None:
    """Record a completed stage in the advisory manifest (atomic tmp + os.replace)."""
    if stage not in _STAGE_ORDER:
        return
    d = _load_manifest(cfg, source_id)
    if sha256:
        d["sha256"] = sha256
    d["source_id"] = source_id
    d["v"] = _MANIFEST_V
    d.setdefault("stages", {})[stage] = {"at": datetime.now(timezone.utc).isoformat(),
                                         "artifact": artifact, "schema": schema}
    _write_json_atomic(manifest_path(cfg, source_id), d)


def _transcript_cache(cfg: Config, source_path: str) -> Path:
    return cfg.agent_io / "transcripts" / f"{Path(source_path).stem}.json"


def _signals_sidecar(cfg: Config, source_id: str) -> Path:
    return cfg.agent_io / "signals" / f"{source_id}.json"


def _valid_transcript_json(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
        return isinstance(d.get("segments"), list)
    except (OSError, json.JSONDecodeError):
        return False


def _valid_signals_sidecar(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        from fanops.signals import _SIDECAR_V
        d = json.loads(path.read_text())
        return d.get("v") == _SIDECAR_V and isinstance(d.get("peaks"), list)
    except (OSError, json.JSONDecodeError, ImportError):
        return False


def _disk_stages(cfg: Config, source_id: str, source_path: str) -> list[str]:
    """Derive completed stages from on-disk artifacts (authoritative over manifest)."""
    done: list[str] = []
    if _valid_transcript_json(_transcript_cache(cfg, source_path)):
        done.append("transcribe")
    if _valid_signals_sidecar(_signals_sidecar(cfg, source_id)):
        done.append("signals")
    if (cfg.agent_io / "framing" / f"{source_id}.detect.json").exists():
        done.append("framing")
    kf = cfg.agent_io / "keyframes" / source_id
    if kf.exists() and any(kf.rglob("*.jpg")):
        done.append("keyframes")
    return done


def infer_resume_stage(cfg: Config, led: Ledger, source_id: str) -> str | None:
    """Return the ledger SourceState.value to resume at from warm artifacts, or None."""
    s = led.sources.get(source_id)
    if s is None:
        return None
    done = _disk_stages(cfg, source_id, s.source_path)
    if not done:
        return None
    last = done[-1]
    return _RESUME_AT.get(last, "transcribed")


def adopt_warm_artifacts(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    """Adopt warm transcript JSON when ledger is stale (catalogued/error, empty transcript)."""
    from fanops.transcribe import _adopt_cached_transcript, _segment
    s = led.sources.get(source_id)
    if s is None or bool(s.transcript):
        return led
    cached = _transcript_cache(cfg, s.source_path)
    if not cached.exists():
        return led
    if s.state in (SourceState.error, SourceState.moments_empty):
        try:
            data = json.loads(cached.read_text())
            s.transcript = [_segment(seg) for seg in data.get("segments", [])]
            s.language = data.get("language")
            s.meta["transcribed"] = True
        except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass
        return led
    if _adopt_cached_transcript(led, source_id, cached):
        return led
    return led


def artifact_summary(cfg: Config, source_id: str) -> str | None:
    """Compact manifest summary for status rows, e.g. ``transcribe+signals``."""
    stages = sorted(_load_manifest(cfg, source_id).get("stages", {}).keys(),
                    key=lambda k: _STAGE_ORDER.index(k) if k in _STAGE_ORDER else 99)
    return "+".join(stages) if stages else None


def is_transient_error(reason: str | None) -> bool:
    """Hybrid failure policy: auto-resume transient errors; stay manual for toolchain/corrupt/ceiling."""
    if not reason:
        return True
    r = reason.lower()
    if "toolchain missing" in r or "corrupt gate" in r:
        return False
    if "deterministic ceiling" in r or "attempt 3/3" in r:
        return False
    if "timeoutexpired" in r or "timed out" in r or "stagebusyerror" in r:
        return True
    if "rolled back" in r:
        return True
    return True
