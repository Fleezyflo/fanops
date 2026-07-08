"""MOL-296: config-doc drift guard — Settings.model_fields must match docs/CONFIG.md table vars."""
import re
from pathlib import Path

from fanops.settings import Settings

_CONFIG = Path(__file__).resolve().parents[1] / "docs" / "CONFIG.md"
# Infrastructure vars documented for operators but set via certifi setdefault, not Settings-owned.
_DOC_EXEMPT = frozenset({"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "META_GRAPH_TOKEN__<SLUG>"})
# Dynamic per-handle keys are documented as a pattern, not a Settings field.
_SETTINGS_EXEMPT = frozenset()


def _doc_table_vars() -> set[str]:
    out: set[str] = set()
    for line in _CONFIG.read_text().splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if m:
            out.add(m.group(1))
    return out


def test_config_doc_matches_settings():
    doc = _doc_table_vars()
    settings = set(Settings.model_fields.keys()) - _SETTINGS_EXEMPT
    undocumented = settings - doc
    assert not undocumented, f"Settings fields missing from docs/CONFIG.md: {sorted(undocumented)}"
    phantom = (doc - settings) - _DOC_EXEMPT
    assert not phantom, f"CONFIG.md documents vars absent from Settings (phantoms): {sorted(phantom)}"


def test_no_casting_bias_phantom():
    assert "FANOPS_CASTING_BIAS" not in _doc_table_vars()
