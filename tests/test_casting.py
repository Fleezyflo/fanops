# tests/test_casting.py — Face 3: per-account moment casting. The token-overlap heuristic (cast_moments /
# persona_fit_score) was REMOVED in WS-M1/MOM-7 — it wrote Moment.affinities WITHOUT a durable AccountSelection,
# the exact divergence MOM-3 collapsed by making affinities a derived view. The live selectors are the LLM gate
# (tests/test_moment_casting.py) and the operator override cast_add/cast_remove (tests/test_actions_casting.py).
from fanops.config import Config


def test_config_casting_flag_defaults_on(tmp_path):
    c = Config(root=tmp_path)
    assert c.account_casting is True            # per-account selection defaults ON (no per-account budget knob)
