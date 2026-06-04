# Creative Variation v3 — Deterministic UCB Bandit Allocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace v2's gated-greedy own-surface caption-bias with a deterministic UCB1 multi-armed bandit (`ucb_rank`) that balances exploiting proven hooks against exploring under-sampled ones, behind a default-OFF flag, touching only the caption-request payload (never amplify/C1).

**Architecture:** Add one pure read-only scorer `variant_learning.ucb_rank(led, cfg, account, platform)` computing `score(hook) = mean_lift + c·sqrt(ln N / n_hook)` over a surface's OWN analyzed variant posts (no RNG → structurally deterministic). A new flag `FANOPS_VARIANT_UCB` selects it over `best_hooks` inside the existing `caption._learned_hooks` helper; everything downstream (payload key `learned_hooks`, `caption_prompt`, digest) is reused. v2's `best_hooks` stays as the off-path fallback (one env flip to roll back). The orthogonal `variant_transfer` path is untouched.

**Tech Stack:** Python 3, pytest, ruff. Pure stdlib (`math.log`, `math.sqrt`, `statistics.mean`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-04-creative-variation-v3-ucb-bandit-design.md`

**Prereqs (verified):** v2 merged (`5f275fd`); cross-account transfer merged (`8abc8fe`…`82f9c29`); spec committed (`6777216`). Baseline suite **green** (v2 era 387/1; re-confirm the live count in Step 0 below — transfer added tests).

**Discipline:** strict TDD (RED → GREEN → VERIFY), default-OFF flag, fail-open, amplify-isolation proven by the existing AST data-flow test (extended to `ucb_rank`). Each task ends green on the FULL suite. Run from repo root with the venv active: `source .venv/bin/activate`.

**Dependency order:** Task 1 (config) → Task 2 (`ucb_rank` + shared helper) → Task 3 (caption scorer-select) → Task 4 (digest strategy-aware line) → Task 5 (isolation extension + real integration + docs). Config first because `ucb_rank` reads `cfg.variant_ucb_c`.

---

### Task 0: Establish the green baseline (no code change)

**Files:** none (measurement only).

- [ ] **Step 1: Record the current suite count**

Run: `source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3`
Expected: all pass (e.g. `NNN passed, 1 skipped`). **Write the NNN down** — every later task must end at NNN-plus-the-new-tests, never below. If anything is RED here, STOP and report (do not build on a red baseline).

- [ ] **Step 2: Confirm ruff is clean**

Run: `ruff check src/`
Expected: `All checks passed!` (or no output). If dirty, STOP and report.

---

### Task 1: Config — `FANOPS_VARIANT_UCB` (strategy select) + `FANOPS_VARIANT_UCB_C` (exploration weight)

**Files:**
- Modify: `src/fanops/config.py` (add two properties after `variant_min_gap`, ~line 166)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Grep `tests/test_config.py` for an existing `variant`-prefixed test to match its style (monkeypatch env, `Config(root=tmp_path)`), then append:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_config.py -k variant_ucb -q`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'variant_ucb'`.

- [ ] **Step 3: Minimal implementation**

In `src/fanops/config.py`, add `import math` to the top imports if absent (grep first: `grep -n "^import math" src/fanops/config.py`). Then add these two properties immediately after the `variant_min_gap` property (ends ~line 166), matching the surrounding `variant_*` style:

```python
    @property
    def variant_ucb(self) -> bool:
        # Creative variation v3 (the bandit): with this ON, the OWN-surface caption bias is chosen
        # by a deterministic UCB1 multi-armed bandit (variant_learning.ucb_rank) instead of v2's
        # gated-greedy best_hooks — balancing exploit (proven hooks) against explore (under-sampled
        # ones), and never silent once any variant data exists. DEFAULT OFF (opt-in), INDEPENDENT of
        # FANOPS_VARIANT_LEARNING (which is still the master gate — UCB is inert if learning is off).
        # Only the explicit on-words enable it; unset/empty/other stays OFF (v2 greedy behavior).
        v = (os.getenv("FANOPS_VARIANT_UCB") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_ucb_c(self) -> float:
        # The UCB1 exploration weight `c` in score = mean_lift + c*sqrt(ln N / n). DEFAULT sqrt(2)
        # (the UCB1 literature standard — balanced). Larger c => more exploration of under-sampled
        # hooks; c == 0 => pure greedy (degenerates to v2-greedy's "highest mean wins"). A negative
        # c would INVERT exploration into anti-exploration (always pick the most-sampled) — guard it:
        # a non-float OR negative env falls back to the default rather than crashing an autonomous run.
        try:
            v = float(os.getenv("FANOPS_VARIANT_UCB_C", ""))
        except ValueError:
            return math.sqrt(2)
        return v if v >= 0 else math.sqrt(2)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_config.py -k variant_ucb -q && python -m pytest -q 2>&1 | tail -2`
Expected: the 3 new tests PASS; full suite at NNN+3.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (variation v3 1): FANOPS_VARIANT_UCB + FANOPS_VARIANT_UCB_C config (default OFF, sqrt2)"
```

---

### Task 2: `variant_learning.ucb_rank` — the deterministic bandit scorer (+ shared `_collect_lifts`)

**Files:**
- Modify: `src/fanops/variant_learning.py` (extract `_collect_lifts`, add `ucb_rank`)
- Test: `tests/test_variant_learning.py` (append `ucb_rank` tests)

The math (hand-computable for the tests). For a surface, group its analyzed variant posts by `variant_hook`; let `n_h` = count per hook, `mean_h` = mean lift per hook, `N = sum(n_h)`. Score `s_h = mean_h + c*sqrt(ln(N)/n_h)`. Return `[argmax s_h]`; ties on equal score broken by the sorted-lower hook string. `N == 0` → `[]`. (`N == 1` → `ln 1 = 0` → bonus 0 → bare mean, falls out naturally.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_variant_learning.py` (reuse the file's existing `_post` / `_led` helpers — do NOT redefine them):

```python
import math
from fanops.variant_learning import ucb_rank


def test_ucb_exploits_clear_settled_winner(tmp_path):
    # Both hooks well-sampled; WIN's mean dominates -> exploit it (bonuses ~equal, mean decides).
    cfg = Config(root=tmp_path)              # c = sqrt(2)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0), _post("3", "@a", "WIN", 90.0),
                     _post("4", "@a", "LOSE", 10.0), _post("5", "@a", "LOSE", 10.0), _post("6", "@a", "LOSE", 10.0)])
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["WIN"]


def test_ucb_explores_undersampled_challenger_over_thin_lead(tmp_path):
    # LEAD: mean 60 over 8 posts (small bonus). NEW: mean 55 over 1 post (large bonus).
    # N = 9, ln 9 ≈ 2.19722. c = sqrt(2) ≈ 1.41421.
    #   s_LEAD = 60 + 1.41421*sqrt(2.19722/8) = 60 + 1.41421*0.52408 ≈ 60.741
    #   s_NEW  = 55 + 1.41421*sqrt(2.19722/1) = 55 + 1.41421*1.48230 ≈ 57.097
    # LEAD still wins here — sanity that a BIG mean gap is NOT overridden. (Guards over-exploration.)
    cfg = Config(root=tmp_path)
    posts = [_post(str(i), "@a", "LEAD", 60.0) for i in range(1, 9)] + [_post("9", "@a", "NEW", 55.0)]
    led = _led(cfg, posts)
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["LEAD"]


def test_ucb_challenger_wins_when_mean_gap_is_small(tmp_path):
    # Now make the lead THIN so exploration flips it. LEAD: mean 60 over 8. NEW: mean 59 over 1.
    #   s_LEAD ≈ 60.741 (as above);  s_NEW = 59 + 1.41421*1.48230 ≈ 61.097  -> NEW wins.
    # This is the v2-lock-in fix, mechanized: an under-sampled near-peer overtakes the stale leader.
    cfg = Config(root=tmp_path)
    posts = [_post(str(i), "@a", "LEAD", 60.0) for i in range(1, 9)] + [_post("9", "@a", "NEW", 59.0)]
    led = _led(cfg, posts)
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["NEW"]


def test_ucb_c_zero_is_pure_greedy(tmp_path, monkeypatch):
    # c == 0 kills the bonus -> highest mean ALWAYS wins, no exploration (degenerates to greedy).
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "0")
    cfg = Config(root=tmp_path)
    posts = [_post(str(i), "@a", "LEAD", 60.0) for i in range(1, 9)] + [_post("9", "@a", "NEW", 59.0)]
    led = _led(cfg, posts)
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["LEAD"]   # no bonus -> mean decides


def test_ucb_large_c_forces_exploration(tmp_path, monkeypatch):
    # A large c makes the fewest-sampled arm win regardless of a modest mean deficit.
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "50")
    cfg = Config(root=tmp_path)
    posts = [_post(str(i), "@a", "LEAD", 60.0) for i in range(1, 9)] + [_post("9", "@a", "NEW", 55.0)]
    led = _led(cfg, posts)
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["NEW"]    # huge bonus on n=1 arm


def test_ucb_single_post_surface_returns_that_hook(tmp_path):
    # N == 1: ln 1 == 0 -> bonus 0 -> bare mean -> return that one hook (no ln(0), no crash).
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "SOLO", 42.0)])
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["SOLO"]


def test_ucb_single_hook_many_posts_returns_it(tmp_path):
    # One distinct hook, several posts: only one arm -> argmax is it (less strict than v2 by design).
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "SOLO", 70.0), _post("2", "@a", "SOLO", 70.0), _post("3", "@a", "SOLO", 70.0)])
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["SOLO"]


def test_ucb_empty_and_other_surface_and_no_variant(tmp_path):
    cfg = Config(root=tmp_path)
    assert ucb_rank(Ledger.load(cfg), cfg, "@a", Platform.instagram) == []          # no posts
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0)])
    assert ucb_rank(led, cfg, "@b", Platform.instagram) == []                       # other surface empty


def test_ucb_tie_broken_by_sorted_hook_string(tmp_path):
    # Two arms with identical (n, mean) -> identical scores -> deterministic: sorted-lower hook wins.
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "ZZZ", 50.0), _post("2", "@a", "ZZZ", 50.0),
                     _post("3", "@a", "AAA", 50.0), _post("4", "@a", "AAA", 50.0)])
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ["AAA"]   # "AAA" < "ZZZ"


def test_ucb_deterministic_repeat(tmp_path):
    cfg = Config(root=tmp_path)
    posts = [_post(str(i), "@a", "LEAD", 60.0) for i in range(1, 9)] + [_post("9", "@a", "NEW", 59.0)]
    led = _led(cfg, posts)
    assert ucb_rank(led, cfg, "@a", Platform.instagram) == ucb_rank(led, cfg, "@a", Platform.instagram)


def test_variant_learning_module_has_no_nondeterminism():
    # The content-addressed invariant: the scorer module must import no random/hash/clock source.
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops" / "variant_learning.py").read_text()
    for bad in ("import random", "from random", "import time", "from time", "import datetime",
                "from datetime", "hashlib", "uuid"):
        assert bad not in src, f"variant_learning.py must stay deterministic — found {bad!r}"
    assert "hash(" not in src, "variant_learning.py must not use the builtin hash() (salted per-process)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_variant_learning.py -k ucb -q`
Expected: FAIL with `ImportError: cannot import name 'ucb_rank'`.

- [ ] **Step 3: Minimal implementation**

In `src/fanops/variant_learning.py`: (a) add `from math import log, sqrt` to the imports; (b) extract the gather predicate from `best_hooks` into a private `_collect_lifts` so v2 and v3 read the ledger identically; (c) make `best_hooks` use it (behavior-preserving — its tests must stay green); (d) add `ucb_rank`. Final module:

```python
# src/fanops/variant_learning.py
"""Creative-variation v2/v3: the SAFE half of the A/B loop. Pure, read-only scoring of which
per-account hook variant the next caption should lean toward, so request_captions can bias it.
v2 = best_hooks (gated-greedy); v3 = ucb_rank (deterministic UCB1 bandit — explore vs exploit).
Both read the SAME data via _collect_lifts. Touches NONE of amplify/classify_outcomes/
_delete_moment_cascade (C1) — this module must NEVER be called by track.py/pipeline.py (the
amplify/delete path stays blind to the learner; enforced by the isolation tests). No I/O, no
mutation, no random/hash/wall-clock -> a re-run yields the identical result (content-addressed)."""
from __future__ import annotations
from math import log, sqrt
from statistics import mean
from fanops.models import Platform, PostState


def _collect_lifts(led, account: str, platform: Platform) -> dict[str, list[float]]:
    """Group this (account, platform) surface's ANALYZED variant posts by hook -> their lift_scores.
    The single gather predicate both scorers share (so v2/v3 can never disagree on what data exists).
    An 'arm' only appears here once it has >= 1 analyzed post carrying a lift_score."""
    by_hook: dict[str, list[float]] = {}
    for p in led.posts.values():
        if (p.variant_key and p.variant_hook and p.account == account and p.platform is platform
                and p.state is PostState.analyzed and "lift_score" in p.metrics):
            by_hook.setdefault(p.variant_hook, []).append(float(p.metrics["lift_score"]))
    return by_hook


def best_hooks(led, cfg, account: str, platform: Platform) -> list[str]:
    """v2 gated-greedy: the winning hook IFF the leader has >= cfg.variant_min_posts analyzed posts
    AND beats a REAL runner-up's mean lift by >= cfg.variant_min_gap. Else []. (Comparative: a lone
    variant with no runner-up -> [].) Pure function of ledger state — deterministic."""
    min_posts = cfg.variant_min_posts
    min_gap = cfg.variant_min_gap
    by_hook = _collect_lifts(led, account, platform)
    if not by_hook:
        return []
    ranked = sorted(by_hook.items(), key=lambda kv: mean(kv[1]), reverse=True)
    leader_hook, leader_lifts = ranked[0]
    if len(leader_lifts) < min_posts:
        return []
    if len(ranked) < 2:                                  # no runner-up -> not comparative -> []
        return []
    runner_mean = mean(ranked[1][1])
    if mean(leader_lifts) - runner_mean < min_gap:
        return []
    return [leader_hook]


def ucb_rank(led, cfg, account: str, platform: Platform) -> list[str]:
    """v3 deterministic UCB1 bandit over this surface's OWN hook arms. For each arm: score =
    mean_lift + c*sqrt(ln N / n), where n = that arm's analyzed-post count, N = total across arms,
    c = cfg.variant_ucb_c (default sqrt 2). Returns [argmax score]; ties on equal score broken by the
    sorted-lower hook string (deterministic — never insertion order). N == 0 -> []. Every arm has
    n >= 1 by construction (_collect_lifts only yields hooks with an analyzed post), so ln N / n is
    always defined (N == 1 -> ln 1 = 0 -> bonus 0 -> bare mean). No random/hash/clock — a re-run is
    byte-identical. Balances exploiting proven hooks against exploring under-sampled ones; never
    silent once any variant data exists (the v2 weakness this replaces)."""
    by_hook = _collect_lifts(led, account, platform)
    if not by_hook:
        return []
    total = sum(len(lifts) for lifts in by_hook.values())   # N >= 1 here
    c = cfg.variant_ucb_c
    ln_n = log(total)                                       # total >= 1 -> ln defined (ln 1 = 0)
    # Score every arm; pick max score, breaking ties by the lexicographically smaller hook.
    # Sorting the items by hook string FIRST makes max() return the sorted-lower hook on a score tie
    # (max keeps the first-seen maximum; iterating in sorted-hook order makes that deterministic).
    scored = sorted(
        ((hook, mean(lifts) + c * sqrt(ln_n / len(lifts))) for hook, lifts in by_hook.items()),
        key=lambda hs: hs[0],                              # stable, sorted by hook string
    )
    best_hook = max(scored, key=lambda hs: hs[1])[0]
    return [best_hook]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_variant_learning.py -q && python -m pytest -q 2>&1 | tail -2`
Expected: ALL `ucb` tests PASS, the existing `best_hooks` tests STILL pass (the `_collect_lifts` extraction is behavior-preserving), full suite NNN+3 (config) +13 (ucb).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/variant_learning.py tests/test_variant_learning.py
git commit -m "feat (variation v3 2): variant_learning.ucb_rank — deterministic UCB1 bandit + shared _collect_lifts"
```

---

### Task 3: `caption._learned_hooks` selects the scorer behind the flag (the allocation swap) — fail-open

**Files:**
- Modify: `src/fanops/caption.py` (import `ucb_rank`; scorer-select in `_learned_hooks`)
- Test: `tests/test_caption.py` (append)

The change is surgical: `_learned_hooks` ([caption.py:83](src/fanops/caption.py)) keeps its master-gate (`if not cfg.variant_learning: return []`), per-surface dedup loop, and fail-open try/except EXACTLY as-is; only the function it calls per surface changes from `best_hooks` to `ucb_rank if cfg.variant_ucb else best_hooks`. The payload key stays `learned_hooks`; `caption_prompt` and the transfer path are untouched.

- [ ] **Step 1: Write the failing tests**

Grep `tests/test_caption.py` for an existing `request_captions` test that seeds a clip + ledger and reads the request payload (look for `request_path(cfg, "captions"` and how it builds `led`, `cfg`, a clip with a parent moment/source). Reuse that exact fixture pattern. Append:

```python
def test_request_captions_ucb_picks_challenger_when_flag_on(monkeypatch, tmp_path, <existing fixtures>):
    # learning ON + UCB ON: the OWN-surface bias is the UCB pick (challenger), not greedy's leader.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    # ... build led+cfg+clip for surface @a|instagram (reuse the file's helper) ...
    # seed 8 analyzed @a "LEAD" lift 60 + 1 analyzed @a "NEW" lift 59 (UCB -> NEW; greedy -> LEAD/[]):
    for i in range(1, 9):
        led.add_post(Post(id=f"p{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="p9", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk9", variant_hook="NEW", metrics={"lift_score": 59.0}))
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    import json
    from fanops.agentstep import request_path
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "NEW" in payload.get("learned_hooks", [])          # UCB exploration pick reached the request
    assert "LEAD" not in payload.get("learned_hooks", [])     # greedy's pick did NOT (UCB replaced it)

def test_request_captions_greedy_when_ucb_off(monkeypatch, tmp_path, <existing fixtures>):
    # learning ON, UCB OFF: v2 greedy is the allocator -> the SAME seeded surface yields greedy's verdict.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.delenv("FANOPS_VARIANT_UCB", raising=False)
    # ... same seeded ledger as above ...
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    # greedy: LEAD has no >=min_gap lead over NEW (gap 1 < 10) -> best_hooks returns [] -> no hint:
    assert "learned_hooks" not in payload                     # OFF -> v2 behavior, no UCB pick

def test_request_captions_no_hint_when_learning_off(monkeypatch, tmp_path, <existing fixtures>):
    # master gate: learning OFF -> neither scorer runs, no hint, regardless of FANOPS_VARIANT_UCB.
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    # ... same seeded ledger ...
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "learned_hooks" not in payload

def test_request_captions_failopen_on_ucb_error(monkeypatch, tmp_path, <existing fixtures>):
    # fail-open: a raising ucb_rank must be swallowed -> request still written, clip advances.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setattr("fanops.caption.ucb_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # ... seeded ledger ...
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])     # must NOT raise
    from fanops.agentstep import request_path
    assert request_path(cfg, "captions", clip_id).exists()                # written anyway
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_caption.py -k "ucb or greedy_when" -q`
Expected: FAIL — `learned_hooks` carries the greedy/empty result, or `AttributeError` on `fanops.caption.ucb_rank` (not yet imported).

- [ ] **Step 3: Minimal implementation**

In `src/fanops/caption.py`, after the existing `from fanops.variant_learning import best_hooks` line (caption.py:18), add the `ucb_rank` import with a matching comment:

```python
# Creative-variation v3 (the bandit): the alternative OWN-surface allocator, selected by
# FANOPS_VARIANT_UCB inside _learned_hooks. SAME safe caption-request side as best_hooks (the
# amplify/delete path stays blind to it; isolation tests enforce it). Bound at module scope so the
# fail-open path is unit-patchable (tests monkeypatch fanops.caption.ucb_rank to prove a raising
# scorer is swallowed).
from fanops.variant_learning import ucb_rank
```

Then in `_learned_hooks` ([caption.py:83](src/fanops/caption.py)), change ONLY the scorer call inside the loop. Replace:

```python
        for acct, plat in surfaces:
            for h in best_hooks(led, cfg, acct, plat):
```

with:

```python
        scorer = ucb_rank if cfg.variant_ucb else best_hooks   # v3 bandit vs v2 gated-greedy
        for acct, plat in surfaces:
            for h in scorer(led, cfg, acct, plat):
```

Everything else in `_learned_hooks` (the `if not cfg.variant_learning: return []` master gate, `seen`/`learned` dedup, the `except Exception: ... return []` fail-open) stays byte-identical.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_caption.py -q && python -m pytest -q 2>&1 | tail -2`
Expected: the 4 new tests PASS, all existing caption tests STILL pass (UCB off → greedy path unchanged), full suite up by 4.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/caption.py tests/test_caption.py
git commit -m "feat (variation v3 3): request_captions selects ucb_rank behind FANOPS_VARIANT_UCB (fail-open)"
```

---

### Task 4: Digest — strategy-aware gate-state line (operator sees the ACTIVE allocator's verdict)

**Files:**
- Modify: `src/fanops/digest.py` (`_gate_state`, ~line 26-50)
- Test: `tests/test_digest.py` (append)

When `cfg.variant_ucb` is on, the per-surface "Lift by variant" annotation should reflect the UCB pick (so the operator isn't misled by the v2 "learning ACTIVE / gathering data" wording, which is greedy's gate). Keep it fail-open and reuse the scorer (one logic home). The string for the UCB case: `UCB -> "<hook>"`.

- [ ] **Step 1: Write the failing tests**

Grep `tests/test_digest.py` for `test_digest_variant_shows_gate_state` (line ~130) and mirror its fixture style. Append:

```python
def test_digest_variant_ucb_shows_pick(tmp_path, monkeypatch):
    # With UCB on, the per-surface line reports the bandit's pick, not the greedy gate wording.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    # thin lead so UCB picks the under-sampled NEW (8x LEAD@60 + 1x NEW@59):
    for i in range(1, 9):
        led.add_post(Post(id=f"p{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="p9", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk9", variant_hook="NEW", metrics={"lift_score": 59.0}))
    from fanops.digest import render_digest
    section = render_digest(led, cfg).split("Lift by variant")[1]
    assert "UCB" in section and 'NEW' in section            # the bandit verdict is surfaced

def test_digest_variant_ucb_failopen(tmp_path, monkeypatch):
    # A raising ucb_rank must not lose the "Lift by variant" section -> degrade to "gathering data".
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setattr("fanops.digest.ucb_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk1", variant_hook="HOOK A", metrics={"lift_score": 80.0}))
    from fanops.digest import render_digest
    out = render_digest(led, cfg)
    assert "Lift by variant" in out and "HOOK A" in out      # rows survive
    assert "gathering data" in out.split("Lift by variant")[1]   # safe default on error
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_digest.py -k "ucb" -q`
Expected: FAIL — no "UCB" string in the section, or `AttributeError` on `fanops.digest.ucb_rank` (not imported).

- [ ] **Step 3: Minimal implementation**

In `src/fanops/digest.py`, add the import after the existing `from fanops.variant_learning import best_hooks` (digest.py:15):

```python
# Creative-variation v3: when FANOPS_VARIANT_UCB is on, the digest reports the bandit's pick for
# the surface instead of the greedy gate wording. SAME read-only safe side; fail-open. Bound at
# module scope so the fail-open path is unit-patchable.
from fanops.variant_learning import ucb_rank
```

Then modify `_gate_state` (digest.py:26-50) to branch on `cfg.variant_ucb` FIRST (the active allocator wins the annotation), keeping the existing greedy/transfer branches for the UCB-off path, and keeping the outer try/except fail-open. Replace the body's `try:` block:

```python
    try:
        if cfg.variant_ucb:                                # v3: report the bandit's actual pick
            picked = ucb_rank(led, cfg, account, platform)
            state = f'UCB -> "{picked[0]}"' if picked else "gathering data"
        elif best_hooks(led, cfg, account, platform):
            state = "learning ACTIVE"
        elif cfg.variant_transfer and accounts is not None and \
                transferred_hooks(led, cfg, accounts, account, platform):
            state = "borrowing platform signal"
        else:
            state = "gathering data"
    except Exception:
        logger.warning("variant gate-state degraded to 'gathering data' (fail-open)", exc_info=True)
        state = "gathering data"
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_digest.py -q && python -m pytest -q 2>&1 | tail -2`
Expected: the 2 new tests PASS, existing digest tests STILL pass (UCB off → wording unchanged), full suite up by 2.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat (variation v3 4): digest reports the UCB pick per surface when FANOPS_VARIANT_UCB on (fail-open)"
```

---

### Task 5: Amplify-isolation extension + real on-disk integration + docs

**Files:**
- Modify: `tests/test_variant_learning.py` (extend `_FORBIDDEN_IN_AMPLIFY`; add a `ucb_rank` caller-lock)
- Create: `tests/integration/test_variant_ucb_real.py`
- Modify: `MohFlow-FanOps/00_control/RUNTIME.md` (env table) — confirm exact path in Step 5
- Modify: `docs/handoff.md` (§Now + §State)

- [ ] **Step 1: Extend the C1 isolation invariant to `ucb_rank` (the safety claim, mechanized)**

In `tests/test_variant_learning.py`, add `"ucb_rank"` and `"variant_ucb"` to the `_FORBIDDEN_IN_AMPLIFY` tuple (line ~102) so the existing `test_amplify_path_never_acts_on_variant_signal` also forbids the amplify path from referencing the bandit:

```python
_FORBIDDEN_IN_AMPLIFY = ("variant_key", "variant_hook", "best_hooks", "variant_learning",
                         "transferred_hooks", "variant_transfer", "ucb_rank", "variant_ucb")
```

Then add a positive caller-lock for `ucb_rank`, mirroring `test_best_hooks_called_only_on_safe_read_or_request_side` exactly (allowed = caption.py + digest.py; danger = adjust/track/pipeline/ledger):

```python
def test_ucb_rank_called_only_on_safe_read_or_request_side():
    """Positive lock on the UCB scorer, mirroring the best_hooks lock. ucb_rank may be called only
    from the SAFE surfaces: caption.py (the request side) and digest.py (read-only gate reporting).
    NEVER from the C1 danger files. If a future edit calls it from the amplify/delete path, this
    names the offender."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    allowed = {"caption.py", "digest.py"}
    danger = {"adjust.py", "track.py", "pipeline.py", "ledger.py"}
    callers = set()
    for py in root.rglob("*.py"):
        if py.name == "variant_learning.py":        # the definition site
            continue
        if "ucb_rank(" in py.read_text():           # an actual call (not just the import line)
            callers.add(py.name)
    leaked_into_danger = sorted(callers & danger)
    assert not leaked_into_danger, \
        f"C1 violation: ucb_rank called from the amplify/delete path: {leaked_into_danger}"
    assert callers <= allowed, \
        f"ucb_rank called from an unexpected file (review for safety): {sorted(callers - allowed)}"
```

- [ ] **Step 2: Verify the isolation tests pass**

Run: `python -m pytest tests/test_variant_learning.py -k "amplify or called_only" -q`
Expected: PASS (the amplify path references none of the variant signal; `ucb_rank` is called only from caption.py + digest.py).

- [ ] **Step 3: MUTATION-PROOF the isolation (prove the test bites)**

Temporarily add a line referencing `ucb_rank` inside `amplify` in `src/fanops/adjust.py` (e.g. `_ = ucb_rank` at the top of the function body), run `python -m pytest tests/test_variant_learning.py -k amplify -q` → it must go **RED** naming the leak. Then **revert** the mutation (`git checkout src/fanops/adjust.py`) and re-run → GREEN. Document in the commit that the mutation was verified.

- [ ] **Step 4: Real on-disk integration — the loop closes with the UCB pick, deterministically**

Grep `tests/integration/test_variant_learning_real.py` (the v2 integration) for how it builds a REAL ledger on disk + reads the actual request file, and mirror it. Create `tests/integration/test_variant_ucb_real.py`:

```python
# tests/integration/test_variant_ucb_real.py
"""v3 real integration: a surface engineered so UCB's pick DIFFERS from greedy's, exercised through
the actual request_captions -> on-disk request file, then re-run to prove byte-identical output (the
content-addressed determinism invariant). No mocks — the project's Integrate bar."""
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.caption import request_captions
from fanops.agentstep import request_path
# reuse whatever clip/moment/source seeding helper the v2 real test uses; if it has none, build the
# minimal clip+moment+source inline exactly as that file does.


def _seed_clip(led):
    # ... mirror the v2 real integration's clip+moment(+source) construction for clip_id "c1" ...
    ...


def test_ucb_real_request_carries_bandit_pick_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _seed_clip(led)
    # thin lead -> UCB explores the under-sampled NEW; greedy would emit nothing (gap < min_gap):
    for i in range(1, 9):
        led.add_post(Post(id=f"p{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="p9", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk9", variant_hook="NEW", metrics={"lift_score": 59.0}))
    request_captions(led, cfg, "c1", [("@a", Platform.instagram)])
    path = request_path(cfg, "captions", "c1")
    first = path.read_text()
    payload = json.loads(first)
    assert payload.get("learned_hooks") == ["NEW"]          # UCB pick on disk (not greedy's [])
    # determinism: a re-run over the same ledger writes a byte-identical request:
    request_captions(led, cfg, "c1", [("@a", Platform.instagram)])
    assert path.read_text() == first
```

If the v2 real test exposes a reusable clip-seeding fixture (e.g. a `conftest.py` helper), import it instead of re-deriving `_seed_clip`.

- [ ] **Step 5: Verify everything — full suite + integration + ruff**

Run: `python -m pytest -q 2>&1 | tail -3 && python -m pytest tests/integration -q 2>&1 | tail -3 && ruff check src/`
Expected: full suite green (NNN + 3 + 13 + 4 + 2 + integration), integration green, `ruff check src/` clean. If ruff flags the new code, fix and re-run.

- [ ] **Step 6: Docs**

First find the runtime env-var doc: `grep -rln "FANOPS_VARIANT_LEARNING" MohFlow-FanOps/ docs/ 2>/dev/null`. In whichever file holds the env table (likely `MohFlow-FanOps/00_control/RUNTIME.md`), add rows for `FANOPS_VARIANT_UCB` (default OFF — "select the deterministic UCB1 bandit as the own-surface caption allocator instead of v2 gated-greedy") and `FANOPS_VARIANT_UCB_C` (default √2 — "UCB exploration weight; 0 = pure greedy, larger = more exploration"). Run the `sync-docs` skill to catch any other drift. Then update `docs/handoff.md` §Now (v3 UCB bandit shipped) and §State (suite count, `variant_learning` now exports `ucb_rank`). Commit.

```bash
git add tests/test_variant_learning.py tests/integration/test_variant_ucb_real.py <doc files>
git commit -m "feat (variation v3 5): C1 isolation extended to ucb_rank (mutation-proven) + real on-disk determinism integration + docs"
```

---

## Self-Review

- **Spec coverage:** Task 1 = the two config flags (spec "Units: config.py"). Task 2 = `ucb_rank` + `_collect_lifts` with the full cold-start/degenerate/tie rules (spec "The fix", "Cold-start and degenerate surfaces", "Units: variant_learning.py"). Task 3 = the scorer-select in `_learned_hooks` (spec "Units: caption.py", "Architecture" data flow). Task 4 = the strategy-aware digest line (spec "Units: digest.py"). Task 5 = the C1 isolation extension + mutation proof + real on-disk determinism integration + docs (spec "Testing strategy" isolation/integration bullets, "Risks: determinism/C1"). `prompts.py` is correctly absent — spec says UNCHANGED (same `learned_hooks` key). Transfer untouched — spec note + out-of-scope. Every spec section maps to a task.
- **No placeholders:** every code step shows real code. The two integration `<existing fixtures>` / `_seed_clip` markers are explicit "grep the v2 real/caption test and mirror its clip-seeding" instructions, not vague TODOs — the seeding is the only project-specific fixture the worker must read from the adjacent existing test (named precisely).
- **Type/name consistency:** `ucb_rank(led, cfg, account, platform) -> list[str]` is used identically in Tasks 2/3/4/5; `_collect_lifts(led, account, platform) -> dict[str, list[float]]` defined and consumed in Task 2; `variant_ucb`/`variant_ucb_c` defined in Task 1, read in Tasks 2/3/4; payload key `learned_hooks` consistent with the existing caption.py. Flag on-words match the existing `variant_learning` property exactly.
- **Determinism guarded three ways:** the no-nondeterminism source scan (Task 2), the repeat-equality unit test (Task 2), and the byte-identical on-disk re-run (Task 5). No `random`/`hash`/clock anywhere in `ucb_rank`.
- **Safety:** C1 isolation extended to `ucb_rank` AND mutation-proven (Task 5 Steps 1-3); fail-open tested in caption (Task 3) and digest (Task 4); default-OFF (Task 1); rollback is one env flip (UCB off → v2 greedy).
- **Dependency order is correct:** config (1) before the scorer that reads it (2) before its caller (3) before the digest reporting it (4) before the isolation/integration/docs that exercise the whole (5).
