# src/fanops/artifacts.py
"""Pipeline artifact manifest + resume helpers. Manifest at 04_agent_io/manifests/{source_id}.json
is ADVISORY — existing sidecar adopt paths (transcript JSON, signals sidecar) remain authoritative
for infer/adopt; the manifest tags stages for operator visibility and extended purge."""
from __future__ import annotations
import contextlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState

_MANIFEST_V = 1
_SIGNALS_V = 3   # mirrors signals._SIDECAR_V


def _manifest_path(cfg: Config, source_id: str) -> Path:
    return cfg.agent_io / "manifests" / f"{source_id}.json"


def _rel_artifact(cfg: Config, path: Path | str) -> str:
    p = Path(path)
    try: return str(p.relative_to(cfg.root))
    except ValueError: return str(p)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_stage(cfg: Config, source_id: str, stage: str, artifact_path: str | Path, schema_version: int) -> None:
    """Atomic manifest write after a successful artifact landing. Best-effort — a write failure never crashes."""
    path = _manifest_path(cfg, source_id)
    try:
        d: dict = {"v": _MANIFEST_V, "source_id": source_id, "sha256": None, "stages": {}}
        if path.exists():
            try: d = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError): pass
        d.setdefault("stages", {})[stage] = {"at": _iso_now(), "artifact": _rel_artifact(cfg, artifact_path) if isinstance(artifact_path, Path) else str(artifact_path), "schema": schema_version}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(d, default=str))
        os.replace(str(tmp), str(path))
    except OSError:
        pass


def _valid_transcript(path: Path) -> bool:
    if not path.exists(): return False
    try:
        data = json.loads(path.read_text())
        return isinstance(data.get("segments"), list)
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _valid_signals(path: Path) -> bool:
    if not path.exists(): return False
    try:
        data = json.loads(path.read_text())
        return data.get("v") == _SIGNALS_V and isinstance(data.get("peaks"), list)
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return False


def infer_resume_stage(cfg: Config, source_id: str, ledger_source) -> SourceState | None:
    """Read manifest + verify on-disk artifacts exist -> return coarse SourceState to resume at."""
    stem = Path(ledger_source.source_path).stem
    tpath = cfg.agent_io / "transcripts" / f"{stem}.json"
    spath = cfg.agent_io / "signals" / f"{source_id}.json"
    if _valid_signals(spath):
        return SourceState.signalled
    if _valid_transcript(tpath):
        return SourceState.transcribed
    return None


def adopt_warm_artifacts(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    """Adopt transcript/signals from disk when the ledger row is empty but artifacts exist."""
    from fanops.transcribe import _adopt_cached_transcript
    src = led.sources.get(source_id)
    if src is None: return led
    stem = Path(src.source_path).stem
    cached = cfg.agent_io / "transcripts" / f"{stem}.json"
    if not src.transcript and cached.exists():
        _adopt_cached_transcript(led, source_id, cached)
    if not src.signal_peaks:
        sidecar = cfg.agent_io / "signals" / f"{source_id}.json"
        if sidecar.exists():
            try:
                d = json.loads(sidecar.read_text())
                if d.get("v") == _SIGNALS_V:
                    s = led.sources[source_id]
                    s.signal_peaks = d["peaks"]
                    if d.get("duration"): s.duration = d["duration"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
    return led


def purge_all_source_artifacts(cfg: Config, source_id: str, source_path: str, *, led: Ledger | None = None) -> None:
    """Extended purge: transcribe/signals caches + framing/keyframes/manifests + render fingerprints."""
    import shutil
    from fanops.transcribe import purge_source_artifacts
    purge_source_artifacts(cfg, source_id, source_path)
    for p in (cfg.agent_io / "framing" / f"{source_id}.detect.json", _manifest_path(cfg, source_id)):
        with contextlib.suppress(FileNotFoundError): p.unlink()
    kf = cfg.agent_io / "keyframes" / source_id
    if kf.exists(): shutil.rmtree(kf, ignore_errors=True)
    if led is not None:
        for c in led.clips.values():
            mom = led.moments.get(c.parent_id)
            if mom is not None and mom.parent_id == source_id:
                fp = cfg.clips / f"{c.id}.render.json"
                with contextlib.suppress(FileNotFoundError): fp.unlink()


def artifact_summary(cfg: Config, source_id: str, source_path: str) -> str | None:
    """Compact manifest summary for backlog rows, e.g. 'transcribe+signals'."""
    stem = Path(source_path).stem
    stages: list[str] = []
    if _valid_transcript(cfg.agent_io / "transcripts" / f"{stem}.json"): stages.append("transcribe")
    if _valid_signals(cfg.agent_io / "signals" / f"{source_id}.json"): stages.append("signals")
    if (cfg.agent_io / "framing" / f"{source_id}.detect.json").exists(): stages.append("framing")
    if (cfg.agent_io / "keyframes" / source_id).exists(): stages.append("keyframes")
    return "+".join(stages) if stages else None
