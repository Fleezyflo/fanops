# src/fanops/init_flow.py — MOL-303: thin orchestrator reusing doctor checks + golive setters
from __future__ import annotations
from fanops.config import Config
from fanops.doctor import doctor_report, setup_state, setup_next_action, _brief_ok

_CONTEXT_TEMPLATE = """# Brand brief (context.md)
# Edit this file — it steers every moment and caption decision.

Artist / project: (your artist name)
Voice: (how clips should sound — tone, energy, taboo topics)
Audience: (who stops scrolling)
Goal: (what a winning clip proves — not praise, retention)
"""


def write_context_template(cfg: Config) -> bool:
    """The ONE new writer: filled context.md template when absent/empty. Returns True if written."""
    if _brief_ok(cfg):
        return False
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text(_CONTEXT_TEMPLATE)
    return True


def run_init(cfg: Config, *, postiz_url: str = "", postiz_key: str = "",
             go_live: bool = False, validate_learning: bool = False) -> dict:
    """Walk a fresh checkout toward doctor-clean ready-to-go-live. Idempotent + resumable."""
    steps: list[str] = []
    if write_context_template(cfg):
        steps.append("wrote context.md template")
    if postiz_url.strip():
        from fanops.studio.golive import set_postiz_config
        res = set_postiz_config(cfg, postiz_url.strip(), postiz_key)
        steps.append(f"postiz: {'ok' if res.ok else res.error}")
    state = setup_state(cfg)
    if state in ("CONNECTED", "VALIDATED", "LIVE") and cfg.postiz_api_key:
        from fanops.studio.golive import discover_channels
        res = discover_channels(cfg)
        steps.append(f"discover_channels: {'ok' if res.ok else res.error}")
    report = doctor_report(cfg)
    failed = [c for c in report["checks"] if not c["ok"]]
    result = {"state": setup_state(cfg), "next": setup_next_action(cfg), "steps": steps,
              "failed_checks": len(failed), "doctor_clean": not failed}
    if go_live:
        from fanops.studio.golive import go_live
        gl = go_live(cfg, confirmed=True)
        result["go_live"] = gl.ok
        result["state"] = setup_state(cfg)
        result["next"] = setup_next_action(cfg)
    if validate_learning:
        from fanops.studio.golive import validate_learning
        vl = validate_learning(cfg, confirmed=True)
        result["validate_learning"] = vl.ok
        result["state"] = setup_state(cfg)
        result["next"] = setup_next_action(cfg)
    return result
