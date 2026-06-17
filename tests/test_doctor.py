# tests/test_doctor.py — Phase 3b: `fanops doctor` read-only first-run health screen. Asserts only
# on env-controlled checks (key/claude/notes), never host-dependent toolchain presence.
import json
from fanops.config import Config
from fanops import doctor

_KEY = "sk-postiz-LEAK-CANARY-1234567890"          # a recognizable sentinel: must never reach the report

def _postiz_cfg(tmp_path, *, mapped=True, validated=True):
    """A Config for the M4 Postiz-learning-ready check: one ACTIVE account (fully mapped, or with an
    unmapped instagram channel) + cutover.json (metrics_confirmed or not). Writes via cfg paths so the
    data-root layout (MohFlow-FanOps/00_control) is never hand-guessed (mirrors test_doctor_notes_review_queue_count)."""
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    integ = {"instagram": "ig_1"} if mapped else {}
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@probe", "platforms": ["instagram"], "status": "active", "access": "postiz", "integrations": integ}]}))
    cfg.cutover_path.write_text(json.dumps({"metrics_confirmed": bool(validated)}))
    return cfg

def _learning_check(rep):
    return next((c for c in rep["checks"] if "learning" in c["label"].lower() and "postiz" in c["label"].lower()), None)


def test_doctor_flags_missing_brand_brief(tmp_path):
    # context.md is the #1 output lever; its ABSENCE used to be silent. doctor must surface it as a
    # readiness failure so an operator never runs an ungrounded engine without knowing.
    cfg = Config(root=tmp_path)                          # no context.md written
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is False and "context.md" in bc["hint"]


def test_doctor_passes_with_brand_brief(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("BRAND: confident, bilingual. Pick the bars.")
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is True


def test_doctor_flags_empty_brand_brief(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("   \n\t  ")             # present but blank -> still ungrounded
    rep = doctor.doctor_report(cfg)
    bc = next((c for c in rep["checks"] if "brand brief" in c["label"].lower()), None)
    assert bc is not None and bc["ok"] is False


def test_doctor_flags_missing_blotato_key_when_live(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    kc = [c for c in rep["checks"] if "BLOTATO_API_KEY" in c["label"]][0]
    assert kc["ok"] is False and "BLOTATO_API_KEY" in kc["hint"]

def test_doctor_passes_key_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    rep = doctor.doctor_report(Config(root=tmp_path))
    kc = [c for c in rep["checks"] if "BLOTATO_API_KEY" in c["label"]][0]
    assert kc["ok"] is True

def test_doctor_claude_check_only_when_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert not any("claude" in c["label"].lower() for c in rep["checks"])
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    rep2 = doctor.doctor_report(Config(root=tmp_path))
    assert any("claude" in c["label"].lower() for c in rep2["checks"])

def test_doctor_notes_learning_unvalidated(tmp_path, monkeypatch):
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("cutover" in n.lower() for n in rep["notes"])    # points at the go-live harness

def test_doctor_notes_review_queue_count(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "a.jpg").write_bytes(b"J"); (cfg.review / "b.jpg").write_bytes(b"J")
    rep = doctor.doctor_report(cfg)
    assert any("2" in n and "review" in n.lower() for n in rep["notes"])

def test_cli_doctor_runs_and_prints(tmp_path, monkeypatch, capsys):
    from fanops.cli import main
    monkeypatch.chdir(tmp_path)
    rc = main(["doctor"])
    assert rc in (0, 1)
    assert "doctor" in capsys.readouterr().out.lower()

# --- M4: Postiz-learning readiness check + Blotato-string fixes ---

def test_doctor_postiz_learning_ready_all_green(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is True            # key set + every channel mapped + cutover confirmed

def test_doctor_postiz_learning_not_ready_key_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is False and "Connect Postiz" in c["hint"]

def test_doctor_postiz_learning_not_ready_channel_unmapped(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, mapped=False))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is False and "map" in c["hint"].lower()

def test_doctor_postiz_learning_not_ready_cutover_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, validated=False))
    c = _learning_check(rep)
    assert c is not None and c["ok"] is False and "Validate learning" in c["hint"]

def test_doctor_report_never_leaks_postiz_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path))
    assert _KEY not in json.dumps(rep)                  # the key VALUE must never reach a label/hint/note

def test_doctor_accounts_hint_names_studio_not_blotato(tmp_path, monkeypatch):
    # line-38 Blotato-string fix: the accounts-mapping hint must name the real post-PR#22 path (Studio), not Blotato
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", _KEY)
    rep = doctor.doctor_report(_postiz_cfg(tmp_path, mapped=False))     # @probe instagram unmapped -> problem surfaced
    ac = [c for c in rep["checks"] if "accounts.json" in c["label"]][0]
    assert ac["ok"] is False and "Blotato" not in ac["hint"] and "Studio" in ac["hint"]

def test_doctor_learning_note_names_studio_validate(tmp_path, monkeypatch):
    # line-57 Blotato-string fix: the unvalidated-learning note names the Studio Validate learning step
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("Validate learning" in n for n in rep["notes"])
