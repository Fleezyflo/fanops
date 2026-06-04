# tests/test_config.py
from fanops.config import Config

def test_dirs(tmp_path):
    c = Config(root=tmp_path)
    assert c.inbox == tmp_path / "MohFlow-FanOps" / "01_inbox"
    assert c.agent_io == tmp_path / "MohFlow-FanOps" / "04_agent_io"
    assert c.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"
    assert c.reports == tmp_path / "MohFlow-FanOps" / "07_reports"

def test_poster_default_dryrun(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert Config(root=tmp_path).poster_backend == "dryrun"

def test_poster_env_and_key_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "  abc123\n")   # surrounding ws only
    c = Config(root=tmp_path)
    assert c.poster_backend == "rest" and c.blotato_api_key == "abc123"

def test_budget_and_responder_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_ESCALATION_BUDGET_USD", raising=False)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    c = Config(root=tmp_path)
    assert c.escalation_budget_usd == 0.0 and c.responder_mode == "manual"

def test_burn_subs_defaults_on_and_respects_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_BURN_SUBS", raising=False)
    assert Config(root=tmp_path).burn_subs is True            # default ON
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    assert Config(root=tmp_path).burn_subs is False
    monkeypatch.setenv("FANOPS_BURN_SUBS", "false")
    assert Config(root=tmp_path).burn_subs is False
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    assert Config(root=tmp_path).burn_subs is True

def test_subtitle_font_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_SUBTITLE_FONT", raising=False)
    assert Config(root=tmp_path).subtitle_font == "Arial Unicode MS"
    monkeypatch.setenv("FANOPS_SUBTITLE_FONT", "X")
    assert Config(root=tmp_path).subtitle_font == "X"


def test_creative_variation_defaults_off_and_respects_env(tmp_path, monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_CREATIVE_VARIATION", raising=False)
    assert Config(root=tmp_path).creative_variation is False           # default OFF (opt-in)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "true")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    assert Config(root=tmp_path).creative_variation is False


def test_config_has_review_dir(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert cfg.review == cfg.base / "00_review"        # the discovery review folder
    # approved subfolder convention (used by intake) is review/approved
    assert (cfg.review / "approved").name == "approved"
