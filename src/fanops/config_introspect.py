# src/fanops/config_introspect.py — MOL-294: generated config surface from Settings.model_fields
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fanops.config import Config
from fanops.secret_provider import get_secret, is_secret_env_key
from fanops.settings import Settings, _enriched_env

# Studio-settable via Go-Live tab (_dual_write) — mirrors docs/CONFIG.md §Set=S (static keys only).
_STUDIO_SETTABLE = frozenset({
    "FANOPS_LIVE", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "FANOPS_RESPONDER",
    "FANOPS_CLIP_PROFILE", "FANOPS_ACCOUNT_CASTING", "FANOPS_VARIANT_LEARNING",
    "FANOPS_VARIANT_AMPLIFY", "FANOPS_VARIANT_UCB", "FANOPS_VARIANT_TRANSFER",
    "META_GRAPH_TOKEN",
})
_SECRET_SUFFIX = ("_API_KEY", "_SECRET", "_TOKEN", "_ACCESS_KEY", "_SECRET_ACCESS_KEY")


def _is_secret(name: str) -> bool:
    return any(name.endswith(s) or s in name for s in _SECRET_SUFFIX)


def _dotenv_keys(path: Path) -> set[str]:
    if not path.exists(): return set()
    keys: set[str] = set()
    try:
        for ln in path.read_text().splitlines():
            stripped = ln.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in ln: continue
            raw_key = ln.split("=", 1)[0].strip()
            if raw_key.startswith("export "): raw_key = raw_key[len("export "):].strip()
            if raw_key: keys.add(raw_key)
    except OSError:
        pass
    return keys


def _source_layer(name: str, cfg: Config, *, dotenv_keys: set[str]) -> str:
    if is_secret_env_key(name) and get_secret(name) is not None: return "keychain"
    if name in os.environ: return "os.environ"
    if name in dotenv_keys: return ".env"
    return "default"


def _display_value(name: str, value: Any) -> str:
    if value is None: return "(none)"
    if _is_secret(name) and str(value).strip():
        return "(set)"
    s = str(value)
    return s if s else "(empty)"


def _field_type_name(field) -> str:
    ann = getattr(field, "annotation", None)
    if ann is None: return "unknown"
    if getattr(ann, "__origin__", None) is not None:
        args = getattr(ann, "__args__", ())
        if args:
            parts = [getattr(a, "__name__", str(a)) for a in args]
            return f"{getattr(ann.__origin__, '__name__', str(ann.__origin__))}[{' | '.join(parts)}]"
    return getattr(ann, "__name__", str(ann))


def _validation_error_rows(exc: ValidationError) -> list[dict]:
    rows: list[dict] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        name = str(loc[0]) if loc else "?"
        rows.append({"name": name, "type": "error", "default": "", "effective": err.get("msg", ""),
                     "source": "validation", "studio": False, "validation_error": True})
    return rows


def config_rows(cfg: Config) -> list[dict]:
    """One row per Settings field: name, type, default, effective, source, studio_settable."""
    enriched = _enriched_env(dict(os.environ))
    try:
        s = Settings.model_validate(enriched)
    except ValidationError as exc:
        return _validation_error_rows(exc)
    dotenv_keys = _dotenv_keys(cfg.root / ".env")
    rows: list[dict] = []
    for name, field in Settings.model_fields.items():
        default = field.get_default(call_default_factory=True)
        effective = getattr(s, name)
        rows.append({
            "name": name,
            "type": _field_type_name(field),
            "default": _display_value(name, default),
            "effective": _display_value(name, effective),
            "source": _source_layer(name, cfg, dotenv_keys=dotenv_keys),
            "studio": name in _STUDIO_SETTABLE,
        })
    return rows


def config_has_validation_errors(cfg: Config) -> bool:
    return any(r.get("validation_error") for r in config_rows(cfg))


def format_config_report(cfg: Config) -> str:
    rows = config_rows(cfg)
    lines = ["fanops config", f"{'NAME':<36} {'TYPE':<12} {'DEFAULT':<14} {'EFFECTIVE':<14} {'SOURCE':<12} STUDIO"]
    for r in rows:
        studio = "yes" if r["studio"] else "no"
        lines.append(f"{r['name']:<36} {r['type']:<12} {r['default']:<14} {r['effective']:<14} {r['source']:<12} {studio}")
    return "\n".join(lines)
