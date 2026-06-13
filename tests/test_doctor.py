# tests/test_doctor.py — Phase 3b: `fanops doctor` read-only first-run health screen. Asserts only
# on env-controlled checks (key/claude/notes), never host-dependent toolchain presence.
from fanops.config import Config
from fanops import doctor


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
