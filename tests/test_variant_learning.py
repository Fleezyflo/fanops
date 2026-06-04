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


# --- HARDENING (post-adversarial-review): a "winner" must be COMPARATIVE -----------------------
# The claim the gate makes is "the leader beats the RUNNER-UP by >= min_gap". A single variant with
# no runner-up is not a comparative A/B winner — it's an absolute-performance reading against an
# implicit zero. Acting on it biases creative with NOTHING to compare against (and silently kills
# the per-account exploration that variation exists to create). So: no runner-up -> [] (still
# exploring). This makes the code match the stated comparative guarantee and is strictly MORE
# conservative — the right direction for a noise guard. (Adversarial skeptic finding, 2026-06-04.)
def test_single_variant_no_runner_up_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "SOLO", 90.0), _post("2", "@a", "SOLO", 90.0),
                     _post("3", "@a", "SOLO", 90.0)])   # enough posts, high lift, but NO competitor
    assert best_hooks(led, cfg, "@a", Platform.instagram) == []   # not comparative -> no bias


def test_two_variants_clear_winner_still_returned(tmp_path):
    # Guard the fix doesn't over-correct: a genuine A/B with a real gap MUST still win.
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0), _post("3", "@a", "WIN", 90.0),
                     _post("4", "@a", "LOSE", 10.0)])   # a real runner-up exists
    assert best_hooks(led, cfg, "@a", Platform.instagram) == ["WIN"]


# --- variation v2 (Task 5): amplify-isolation invariant (C1), mechanized -------------------------
# The safety case for v2 is that a noisy "variant A is winning" signal can NEVER reach the code that
# could delete/retire real rendered content (the C1 cascade-delete-bug path: amplify /
# classify_outcomes / retire in adjust.py, _delete_moment_cascade in ledger.py).
#
# HARDENED after adversarial review (2026-06-04): the original test grepped track.py/pipeline.py for
# the *string* "variant_learning". That guards a FALSE guarantee — `import fanops.pipeline` DOES
# transitively pull in variant_learning (pipeline -> caption -> variant_learning, a legitimate
# import: caption.py is where the loop closes), so "no transitive import" was never true or even
# desirable. The real, substantive invariant is a DATA-FLOW one: the amplify/retire/cascade
# functions must never READ variant attribution (variant_key/variant_hook) or CALL the learner
# (best_hooks). We assert that against the actual source of those functions via AST, so a future
# edit that wires variant signal INTO the delete-cascade path goes red and names the offender —
# while the benign transitive import (caption closing the loop) is correctly allowed.
import ast
import pathlib

_FORBIDDEN_IN_AMPLIFY = ("variant_key", "variant_hook", "best_hooks", "variant_learning")


def _names_in(src_path: pathlib.Path, func_names: set[str]) -> set[str]:
    """All attribute/name identifiers referenced inside the named top-level functions of a module."""
    tree = ast.parse(src_path.read_text())
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    found.add(sub.attr)
                elif isinstance(sub, ast.Name):
                    found.add(sub.id)
    return found


def test_amplify_path_never_acts_on_variant_signal():
    """The C1 cascade-delete path must be BLIND to variant attribution (the real invariant — a
    data-flow check, not a string grep on imports). amplify/classify_outcomes/retire in adjust.py
    and _delete_moment_cascade in ledger.py must reference none of variant_key/variant_hook/
    best_hooks/variant_learning."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    adjust_names = _names_in(root / "adjust.py", {"classify_outcomes", "amplify", "retire"})
    cascade_names = _names_in(root / "ledger.py", {"_delete_moment_cascade"})
    leaked = sorted((adjust_names | cascade_names) & set(_FORBIDDEN_IN_AMPLIFY))
    assert not leaked, (
        f"C1 violation: the amplify/delete-cascade path references variant signal {leaked} — a "
        f"noisy variant 'winner' could now reach code that deletes/retires real content. v2 must "
        f"stay on the caption-request side only.")


def test_best_hooks_called_only_on_safe_read_or_request_side():
    """Positive lock on WHERE the learner is invoked. best_hooks may be called only from the SAFE
    surfaces: caption.py (the request side, where the loop legitimately closes), digest.py (read-only
    gate-state reporting), and variant_amplify.py (creative-variation v3 — the FIRST caller that
    bridges best_hooks -> amplify to give a SUSTAINED proven winner more reach). It must NEVER be
    called from the C1 danger files
    (adjust.py / track.py / pipeline.py / ledger.py). If a future edit calls it from the amplify/
    delete path, this names the offending file.

    Why variant_amplify is a SAFE caller despite reaching amplify (reviewed, not rubber-stamped):
    the C1 invariant is that the DELETE/RETIRE machinery stays blind to the variant signal — that is
    guarded SEPARATELY and still holds (test_amplify_path_never_acts_on_variant_signal: adjust's
    amplify/classify_outcomes/retire + ledger._delete_moment_cascade reference no variant_* / best_hooks).
    variant_amplify itself is AMPLIFY-ONLY — it never calls retire/_delete_moment_cascade/set_*_state
    (proven by tests/test_variant_amplify.py::test_variant_amplify_never_touches_retire_or_cascade),
    so a noisy 'winner' can at worst trigger an extra (hard-gated) amplify, never a delete/retire."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    allowed = {"caption.py", "digest.py", "variant_amplify.py"}
    danger = {"adjust.py", "track.py", "pipeline.py", "ledger.py"}
    callers = set()
    for py in root.rglob("*.py"):
        if py.name == "variant_learning.py":       # the definition site
            continue
        if "best_hooks(" in py.read_text():        # an actual call (not just the import line)
            callers.add(py.name)
    leaked_into_danger = sorted(callers & danger)
    assert not leaked_into_danger, \
        f"C1 violation: best_hooks called from the amplify/delete path: {leaked_into_danger}"
    assert callers <= allowed, \
        f"best_hooks called from an unexpected file (review for safety): {sorted(callers - allowed)}"
