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


def test_variant_learning_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_LEARNING", "FANOPS_VARIANT_MIN_POSTS", "FANOPS_VARIANT_MIN_GAP"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_learning is False
    assert c.variant_min_posts == 3
    assert c.variant_min_gap == 10.0


def test_variant_learning_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_MIN_POSTS", "5")
    monkeypatch.setenv("FANOPS_VARIANT_MIN_GAP", "25")
    c = Config(root=tmp_path)
    assert c.variant_learning is True and c.variant_min_posts == 5 and c.variant_min_gap == 25.0


def test_config_has_review_dir(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert cfg.review == cfg.base / "00_review"        # the discovery review folder
    # approved subfolder convention (used by intake) is review/approved
    assert (cfg.review / "approved").name == "approved"


def test_variant_amplify_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_AMPLIFY", "FANOPS_VARIANT_AMPLIFY_MIN_POSTS",
              "FANOPS_VARIANT_AMPLIFY_MIN_GAP", "FANOPS_VARIANT_AMPLIFY_MIN_STREAK"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_amplify is False
    assert c.variant_amplify_min_posts == 8
    assert c.variant_amplify_min_gap == 25.0
    assert c.variant_amplify_min_streak == 3


def test_variant_amplify_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "12")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "40")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "5")
    c = Config(root=tmp_path)
    assert c.variant_amplify is True
    assert c.variant_amplify_min_posts == 12
    assert c.variant_amplify_min_gap == 40.0
    assert c.variant_amplify_min_streak == 5


def test_variant_amplify_bad_env_falls_back(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "nope")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "nan-ish?")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "x")
    c = Config(root=tmp_path)
    assert c.variant_amplify_min_posts == 8
    assert c.variant_amplify_min_gap == 25.0
    assert c.variant_amplify_min_streak == 3


def test_variant_ucb_defaults_off_and_sqrt2(monkeypatch, tmp_path):
    from fanops.config import Config
    import math
    for k in ("FANOPS_VARIANT_UCB", "FANOPS_VARIANT_UCB_C"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_ucb is False                      # default OFF -> v2 greedy stays the allocator
    assert c.variant_ucb_c == math.sqrt(2)             # UCB1 standard exploration weight

def test_variant_ucb_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "0.5")
    c = Config(root=tmp_path)
    assert c.variant_ucb is True and c.variant_ucb_c == 0.5

def test_variant_ucb_c_bad_or_negative_falls_back(monkeypatch, tmp_path):
    from fanops.config import Config
    import math
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "abc")          # unparseable -> default
    assert Config(root=tmp_path).variant_ucb_c == math.sqrt(2)
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "-1")           # negative -> default (no anti-exploration)
    assert Config(root=tmp_path).variant_ucb_c == math.sqrt(2)
