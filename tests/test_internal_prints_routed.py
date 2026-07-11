# tests/test_internal_prints_routed.py — MOL-358: internal prints route through get_logger, not stdout.
from __future__ import annotations
import ast
import json
from pathlib import Path

from fanops.config import Config

# The nine internal modules scoped by MOL-358 (cli.py's 118 user-facing prints stay untouched).
_INTERNAL_MODULES = (
    "learn_doctor.py",
    "fanops_hashtags.py",
    "stitch_render.py",
    "clip.py",
    "variant_amplify.py",
    "pipeline.py",
    "compose.py",
    "lever_docs.py",
    "ledger.py",
)
_CLI_PRINT_COUNT = 142  # +B11 cmd_doctor dep lines (+2); +studio --install/--uninstall + resident-loaded guard + MOL-223 wipe verb
_SRC = Path(__file__).resolve().parents[1] / "src" / "fanops"


def _print_call_nodes(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text())
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(getattr(n.func, "id", None), str) and n.func.id == "print"]


def test_internal_modules_have_no_print_calls():
    offenders: list[str] = []
    for name in _INTERNAL_MODULES:
        nodes = _print_call_nodes(_SRC / name)
        if nodes:
            offenders.append(f"{name}:{nodes[0].lineno}")
    assert offenders == [], f"internal print() remain: {offenders}"


def test_cli_print_count_unchanged():
    assert len(_print_call_nodes(_SRC / "cli.py")) == _CLI_PRINT_COUNT


def _log_records(cfg: Config) -> list[dict]:
    if not cfg.log_path.exists():
        return []
    return [json.loads(line) for line in cfg.log_path.read_text().splitlines() if line.strip()]


def test_learn_doctor_emits_structured_report(tmp_path, monkeypatch):
    from fanops.learn_doctor import cmd_learn_doctor
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-test")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published,
                      submission_id="s_A", public_url="dryrun://p1"))
    led.save()
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 42}, "_raw_labels": ["Reach"]}]
    assert cmd_learn_doctor(cfg, list_posts=lambda w: rows) == 0
    recs = _log_records(cfg)
    assert any(r["stage"] == "learn_doctor" and r["outcome"] == "report" and r["verdict"] == "PASS" for r in recs)


def test_hashtags_refresh_aborted_logs_structured(tmp_path, monkeypatch):
    from fanops.fanops_hashtags import cmd_hashtags_refresh
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok")
    monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text('{"personas": [oops]}')
    assert cmd_hashtags_refresh(cfg) == 2
    recs = _log_records(cfg)
    assert any(r["stage"] == "hashtags" and r["outcome"] == "refresh_aborted" and r["level"] == "error" for r in recs)


def test_ledger_cascade_unlink_failure_logs(tmp_path, monkeypatch):
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, Post, PostState, ClipState, Platform
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    led.add_moment(Moment(id="m", parent_id="s", content_token="A", start=0, end=2, reason="a"))
    f = tmp_path / "bad.mp4"
    f.write_bytes(b"x")
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.rejected,
                      public_url="dryrun://p"))
    monkeypatch.setattr("os.remove", lambda p: (_ for _ in ()).throw(OSError("perm denied")))
    led.reconcile_moments("s", {})
    led.save()                                                  # M22: unlink + logging happen at post-commit drain
    recs = _log_records(cfg)
    assert any(r["stage"] == "ledger" and r["outcome"] == "cascade_unlink_failed" for r in recs)
