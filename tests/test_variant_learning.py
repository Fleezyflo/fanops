# tests/test_variant_learning.py
"""Creative-variation v2, Task 1: the gated pure scorer best_hooks. The gate IS the whole
safety argument (acting on thin/noisy lift data is the early-noise trap v1 deliberately avoided),
so it is tested hardest: below-min-posts -> [], enough-posts-but-gap-too-small -> [] (noise guard),
clear-winner -> [hook], other-surface isolated, empty, deterministic."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.variant_learning import best_hooks


def _post(pid, acct, hook, lift):
    return Post(id=pid, parent_id="c1", account=acct, account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.analyzed, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})


def _led(cfg, posts):
    led = Ledger.load(cfg)
    for p in posts:
        led.add_post(p)
    return led


def test_below_min_posts_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)          # MIN_POSTS default 3
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0)])  # only 2
    assert best_hooks(led, cfg, "@a", Platform.instagram) == []


def test_enough_posts_but_gap_too_small_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)          # MIN_GAP default ~10
    led = _led(cfg, [_post("1", "@a", "WIN", 51.0), _post("2", "@a", "WIN", 51.0), _post("3", "@a", "WIN", 51.0),
                     _post("4", "@a", "LOSE", 50.0), _post("5", "@a", "LOSE", 50.0), _post("6", "@a", "LOSE", 50.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == []   # 1.0 gap < MIN_GAP -> noise guard


def test_clear_winner_over_threshold_returned(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0), _post("3", "@a", "WIN", 90.0),
                     _post("4", "@a", "LOSE", 10.0), _post("5", "@a", "LOSE", 10.0), _post("6", "@a", "LOSE", 10.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == ["WIN"]


def test_other_surface_isolated(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0), _post("3", "@a", "WIN", 90.0)])
    assert best_hooks(led, cfg, "@b", Platform.instagram) == []   # no data for @b


def test_empty_and_no_variant_posts(tmp_path):
    cfg = Config(root=tmp_path)
    assert best_hooks(Ledger.load(cfg), cfg, "@a", Platform.instagram) == []


def test_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0), _post("3", "@a", "WIN", 90.0),
                     _post("4", "@a", "LOSE", 10.0), _post("5", "@a", "LOSE", 10.0), _post("6", "@a", "LOSE", 10.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == best_hooks(led, cfg, "@a", Platform.instagram)


# --- variation v2 (Task 5): amplify-isolation invariant (C1), mechanized -------------------------
# The whole safety case for v2 is that it closes the loop on the CHEAP/REVERSIBLE caption-request
# side and NEVER touches the amplify/classify_outcomes/_delete_moment_cascade machinery (the C1
# cascade-delete-bug path). This mirrors v1's invariant: the amplify path must stay BLIND to the
# learner, so a noisy "variant A is winning" signal can never reach the code that could delete real
# rendered content. A grep over the source is the cheapest enforceable proof — if a future edit
# wires `variant_learning` into track.py/pipeline.py, this test goes red and names the offender.
def test_learning_never_imported_by_amplify_path():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    for f in ("track.py", "pipeline.py"):
        assert "variant_learning" not in (root / f).read_text(), \
            f"{f} must stay blind to variant_learning (C1: the amplify/delete-cascade path)"
