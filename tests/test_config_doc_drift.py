"""MOL-296: config-doc drift guard — Settings.model_fields must match docs/CONFIG.md table vars."""
import re
from pathlib import Path

from fanops.settings import Settings, env_registry

_CONFIG = Path(__file__).resolve().parents[1] / "docs" / "CONFIG.md"
# Documented as a pattern or certifi setdefault — not a Settings field.
_DOC_EXEMPT = frozenset({"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "META_GRAPH_TOKEN__<SLUG>"})
_SETTINGS_EXEMPT = frozenset()


def _doc_table_vars() -> set[str]:
    out: set[str] = set()
    for line in _CONFIG.read_text().splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if m:
            out.add(m.group(1))
    return out


def _doc_set_column() -> dict[str, str]:
    """var -> Set column for simple `| `NAME` | default | effect | Set |` rows."""
    out: dict[str, str] = {}
    for line in _CONFIG.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 6:
            continue
        m = re.fullmatch(r"`([^`]+)`", cols[1])
        if m:
            out[m.group(1)] = cols[4]
    return out


def _expected_set(name: str) -> str:
    meta = env_registry().get(name)
    if meta is None:
        return ".env"
    if meta.deprecated:
        return "deprecated"
    if meta.kind == "bootstrap":
        return "shell"
    if meta.kind == "process":
        return "process"
    if meta.studio:
        return "S"
    return ".env"


def test_config_doc_matches_settings():
    doc = _doc_table_vars()
    settings = set(Settings.model_fields.keys()) - _SETTINGS_EXEMPT
    undocumented = settings - doc
    assert not undocumented, f"Settings fields missing from docs/CONFIG.md: {sorted(undocumented)}"
    phantom = (doc - settings) - _DOC_EXEMPT
    assert not phantom, f"CONFIG.md documents vars absent from Settings (phantoms): {sorted(phantom)}"


def test_config_doc_set_column_matches_registry():
    sets = _doc_set_column()
    mismatch = {n: (sets.get(n), _expected_set(n))
                for n in Settings.model_fields
                if n not in _SETTINGS_EXEMPT and sets.get(n) != _expected_set(n)}
    assert not mismatch, f"CONFIG.md Set column ≠ registry class: {mismatch}"


def test_no_casting_bias_phantom():
    assert "FANOPS_CASTING_BIAS" not in _doc_table_vars()
