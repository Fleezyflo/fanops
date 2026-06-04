# Creative Variation v3 — Variant-Gated Amplification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a per-account hook variant has earned a *sustained, well-evidenced* win, automatically amplify its source (reopen → mine new moments) carrying the winning hook into the request — so proven winners get more reach — without a wrong signal ever triggering a delete/retire.

**Architecture:** A new isolated module `variant_amplify.py` holds a pure gate (`amplify_candidates`), a deterministic streak tracker (`update_streaks`), and a fail-SAFE actuator (`apply_variant_amplify`) that calls the **existing** `adjust.amplify` (extended with an additive `extra_guidance` kwarg). It is **amplify-only** — it never imports/calls `retire`/`_delete_moment_cascade` (AST-proven). Gated FAR harder than v2: v2's `best_hooks` as a FLOOR + more posts (8) + bigger gap (25) + a sustained lead across ≥3 distinct evidence windows. Default OFF behind `FANOPS_VARIANT_AMPLIFY` (kill switch).

**Tech Stack:** Python 3, pydantic models, pytest (+ pytest-mock), `ast` for the isolation test, the project's content-addressed `ids._hash` for the deterministic evidence fingerprint.

**Spec:** `docs/superpowers/specs/2026-06-04-creative-variation-v3-amplify-design.md`

**Discipline:** strict TDD (RED → GREEN → VERIFY). Every task ends with the FULL suite green and ruff clean. **Baseline before Task 1: `387 passed, 1 skipped`, `ruff check src/` clean.** Always run tests as `source .venv/bin/activate && python -m pytest -q` (NOT bare `pytest` — see the build-deviations "RECURRING FALSE ALARM" note: bare pytest misses the venv).

**Naming note:** the codebase already has a *separate, merged* feature whose commits call themselves "v3" (cross-account `variant_transfer`, commits `8abc8fe`..`82f9c29`) and a bandit *spec*. To disambiguate, THIS feature is named **`variant-amplify`** everywhere (module, flags, commits). Names `variant_amplify` / `amplify_candidates` / `apply_variant_amplify` / `variant_streaks` are confirmed unused anywhere in `src/` or `tests/`.

---

## File Structure

- **Create `src/fanops/variant_amplify.py`** — the whole feature's logic (pure gate + streak tracker + fail-SAFE actuator). Isolation mirrors `variant_learning.py`/`variant_transfer.py`: pure where possible, deterministic, with a SAFETY docstring. ONE responsibility: decide+actuate variant-gated amplification.
- **Modify `src/fanops/adjust.py`** — add an additive `extra_guidance: str = ""` kwarg to `amplify`.
- **Modify `src/fanops/config.py`** — 4 new properties (master flag + 3 thresholds), mirroring the existing `variant_*` property style.
- **Modify `src/fanops/ledger.py`** — new top-level `variant_streaks: dict[str, dict]`, serialized exactly like `tag_log`.
- **Modify `src/fanops/cli.py`** — a manual `amplify-variants` verb + a gated, swallowed call in the autonomous `run` learning block.
- **Modify `src/fanops/digest.py`** — (Task 8) a "Variant amplification" observability line.
- **Create `tests/test_variant_amplify.py`** — unit tests for the gate, streaks, actuator, and the **v3 retire-isolation AST test** + the **mutation-proof** adversarial test.
- **Create `tests/integration/test_variant_amplify_real.py`** — the real on-disk end-to-end.
- **Modify `tests/test_config.py`, `tests/test_adjust.py`, `tests/test_ledger.py`, `tests/test_cli.py`, `tests/test_digest.py`** — append targeted tests.

**Execution order (dependency-correct):** Task 1 (config) → Task 2 (ledger field) → Task 3 (adjust kwarg) → Task 4 (`update_streaks`) → Task 5 (`amplify_candidates`) → Task 6 (`apply_variant_amplify` + retire-isolation AST + mutation proof) → Task 7 (CLI wiring) → Task 8 (digest) → Task 9 (real integration + docs). Tasks 4/5 depend on 1+2; Task 6 depends on 3/4/5.

---

### Task 1: Config — `FANOPS_VARIANT_AMPLIFY` master flag + 3 thresholds (default OFF / conservative)

**Files:**
- Modify: `src/fanops/config.py` (append after `variant_transfer_max_hooks`, currently ending line 197)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py — ADD (match the file's existing variant-flag test style)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -k variant_amplify -q`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'variant_amplify'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/config.py — ADD these properties right after variant_transfer_max_hooks (~line 197),
# matching the EXACT on-words + try/except-fallback style already used by variant_learning above.
    @property
    def variant_amplify(self) -> bool:
        # Creative variation v3 (variant-gated amplification): with this ON, a per-account hook
        # variant that has earned a SUSTAINED, well-evidenced win auto-amplifies its source (the
        # existing adjust.amplify path), carrying the winning hook into the moment-request guidance.
        # This is the FIRST feature to touch the amplify/cascade machinery (audit C1), so it is the
        # KILL SWITCH: DEFAULT OFF (opt-in). Only the explicit on-words enable it; unset/empty/other
        # stays OFF (today's behavior — no variant-driven amplify). Amplify-only: never feeds retire.
        v = (os.getenv("FANOPS_VARIANT_AMPLIFY") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_amplify_min_posts(self) -> int:
        # v3 trust-gate part 1 (stronger than v2's variant_min_posts=3): the winning hook must have
        # at least this many analyzed posts on the surface before its win is trusted enough to AMPLIFY
        # (a far more consequential act than v2's caption-bias). DEFAULT 8. Non-int env -> default.
        try:
            return int(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "8"))
        except ValueError:
            return 8

    @property
    def variant_amplify_min_gap(self) -> float:
        # v3 trust-gate part 2 (stronger than v2's variant_min_gap=10): the winner's mean lift must
        # beat the runner-up's by at least this margin. DEFAULT 25.0 (same lift_score scale).
        # Non-float env -> default.
        try:
            return float(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "25"))
        except ValueError:
            return 25.0

    @property
    def variant_amplify_min_streak(self) -> int:
        # v3 trust-gate part 3 (the core NEW safety property — has no v2 analogue): the SAME hook must
        # have led the gate across at least this many DISTINCT evidence windows (new analyzed-post
        # batches) before amplifying. >= 2 means "never act on a single window". DEFAULT 3.
        # Non-int env -> default.
        try:
            return int(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "3"))
        except ValueError:
            return 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -q && python -m pytest -q`
Expected: PASS; full suite `390 passed, 1 skipped` (387 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (variant-amplify 1): FANOPS_VARIANT_AMPLIFY config + thresholds (default OFF, kill switch)"
```

---

### Task 2: Ledger — persistent `variant_streaks` field (mirrors `tag_log`)

**Files:**
- Modify: `src/fanops/ledger.py` (`__init__` ~line 60; `load` ~line 77; `_save_unlocked` ~line 115)
- Test: `tests/test_ledger.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — ADD
def test_variant_streaks_roundtrips_and_defaults_empty(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    assert led.variant_streaks == {}                      # default empty on a fresh ledger
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "abc", "streak": 2}
    led.save()
    led2 = Ledger.load(cfg)
    assert led2.variant_streaks == {"@a|instagram": {"hook": "WIN", "fingerprint": "abc", "streak": 2}}

def test_old_ledger_without_variant_streaks_loads(tmp_path):
    # An older ledger.json that predates v3 has no "variant_streaks" key -> must load as {} (no crash).
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps({"sources": {}, "moments": {}, "clips": {}, "posts": {}}))
    led = Ledger.load(cfg)
    assert led.variant_streaks == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_ledger.py -k variant_streaks -q`
Expected: FAIL with `AttributeError: 'Ledger' object has no attribute 'variant_streaks'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ledger.py — three edits, mirroring tag_log EXACTLY:

# (a) in __init__, right after the self.tag_log line (~line 63):
        self.variant_streaks: dict[str, dict] = {}   # "account|platform" -> {hook, fingerprint, streak}
                                                     # (variant-amplify v3: sustained-win streak per
                                                     #  surface; deterministic, idempotent on unchanged
                                                     #  evidence; inert when FANOPS_VARIANT_AMPLIFY off)

# (b) in load(), right after `led.tag_log = raw.get("tag_log", {})` (~line 77):
                led.variant_streaks = raw.get("variant_streaks", {})

# (c) in _save_unlocked()'s doc dict, right after the "tag_log" entry (~line 115):
            "variant_streaks": self.variant_streaks,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_ledger.py -q && python -m pytest -q`
Expected: PASS; full suite `392 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ledger.py tests/test_ledger.py
git commit -m "feat (variant-amplify 2): persistent Ledger.variant_streaks (mirrors tag_log, backward-compatible)"
```

---

### Task 3: `adjust.amplify` — additive `extra_guidance` kwarg

**Files:**
- Modify: `src/fanops/adjust.py` (`amplify` signature line 31-32; guidance construction line 48-50)
- Test: `tests/test_adjust.py` (append)

- [ ] **Step 1: Write the failing tests** (grep `tests/test_adjust.py` for how it builds a led/cfg with a source+moment+clip+analyzed post; reuse that fixture style for the seed below)

```python
# tests/test_adjust.py — ADD
def test_amplify_default_guidance_unchanged_without_extra(tmp_path, monkeypatch):
    # extra_guidance defaults to "" -> the written moment-request guidance must NOT contain any
    # injected hook block; behavior byte-identical to today (the existing callers pass nothing).
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, Post, Platform, PostState, SourceState
    from fanops.adjust import amplify
    from fanops.agentstep import request_path
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=4.0, reason="hits",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id="c1", parent_id="m1", path="c1.mp4"))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 90.0}))
    amplify(led, cfg, ["p1"])
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "lean toward" not in payload["guidance"].lower()
    assert payload["guidance"].startswith("AMPLIFY:")

def test_amplify_injects_extra_guidance(tmp_path):
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, Post, Platform, PostState, SourceState
    from fanops.adjust import amplify
    from fanops.agentstep import request_path
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=4.0, reason="hits",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id="c1", parent_id="m1", path="c1.mp4"))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 90.0}))
    amplify(led, cfg, ["p1"], extra_guidance="WINNING_HOOK_TEXT")
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WINNING_HOOK_TEXT" in payload["guidance"]
    assert payload["guidance"].startswith("AMPLIFY:")     # base guidance still leads
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_adjust.py -k extra_guidance -q`
Expected: `test_amplify_injects_extra_guidance` FAILS with `TypeError: amplify() got an unexpected keyword argument 'extra_guidance'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/adjust.py — modify the amplify signature (line 31-32) to add the kwarg:
def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str], *,
            max_amplify_per_source: int = 3, extra_guidance: str = "") -> Ledger:

# ...and the guidance construction (line 48-50): append extra_guidance when non-empty.
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' ({moment.reason}) "
                    f"hit hard (lift={post.metrics.get('lift_score')}). Find MORE moments in that "
                    f"vein in this source — do not repeat the same timestamps.")
        if extra_guidance:
            guidance += f" {extra_guidance}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_adjust.py -q && python -m pytest -q`
Expected: PASS; full suite `394 passed, 1 skipped`. (Existing `amplify` tests must STILL pass — the kwarg is additive, default `""` → byte-identical.)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/adjust.py tests/test_adjust.py
git commit -m "feat (variant-amplify 3): adjust.amplify gains additive extra_guidance kwarg (callers unchanged)"
```

---

### Task 4: `variant_amplify.update_streaks` — deterministic, idempotent streak tracker

**Files:**
- Create: `src/fanops/variant_amplify.py`
- Test: `tests/test_variant_amplify.py`

- [ ] **Step 1: Write the failing tests** (the determinism + idempotency are the load-bearing properties)

```python
# tests/test_variant_amplify.py — NEW
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.variant_amplify import update_streaks

def _post(pid, acct, hook, lift, state=PostState.analyzed):
    return Post(id=pid, parent_id="c1", account=acct, account_id="1", platform=Platform.instagram,
                caption="x", state=state, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})

def _led(cfg, posts):
    led = Ledger.load(cfg)
    for p in posts:
        led.add_post(p)
    return led

def _winset(n, hook, lift, start=1):
    # n analyzed posts of `hook` at `lift` + a runner-up far below so best_hooks fires.
    posts = [_post(str(start + i), "@a", hook, lift) for i in range(n)]
    posts += [_post(str(start + n + i), "@a", "LOSE", 1.0) for i in range(3)]
    return posts

def test_first_sighting_sets_streak_one(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_POSTS default 8
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN" and e["streak"] == 1

def test_same_winner_new_evidence_increments(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    led.add_post(_post("99", "@a", "WIN", 90.0))   # NEW analyzed evidence (new post id)
    update_streaks(led, cfg)               # streak 2
    assert led.variant_streaks["@a|instagram"]["streak"] == 2

def test_same_evidence_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    snap = dict(led.variant_streaks["@a|instagram"])
    update_streaks(led, cfg)               # SAME evidence -> no change
    update_streaks(led, cfg)               # and again
    assert led.variant_streaks["@a|instagram"] == snap

def test_winner_change_resets_to_one(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # WIN streak 1
    # Now make a DIFFERENT hook the leader: add 9 strong NEW posts of "WIN2".
    for i in range(9):
        led.add_post(_post(f"2{i}", "@a", "WIN2", 95.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN2" and e["streak"] == 1     # reset, not continued

def test_winner_disappears_resets_to_zero(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    # Drop below the floor: make the gap tiny so best_hooks now returns [] (raise the losers).
    for p in led.posts.values():
        if p.variant_hook == "LOSE":
            p.metrics["lift_score"] = 89.0
    update_streaks(led, cfg)
    assert led.variant_streaks["@a|instagram"]["streak"] == 0

def test_update_streaks_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    a = dict(led.variant_streaks["@a|instagram"])
    led2 = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led2, cfg)
    b = dict(led2.variant_streaks["@a|instagram"])
    assert a == b                          # same ledger state -> identical streak entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.variant_amplify'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/variant_amplify.py — NEW (module header + update_streaks; amplify_candidates +
# apply_variant_amplify land in Tasks 5/6 in this same file).
"""Creative-variation v3: variant-gated AMPLIFICATION — the first feature to touch the amplify path
(audit C1). When a per-account hook variant has earned a SUSTAINED, well-evidenced win, it authorizes
an extra amplify of that win's source (the existing adjust.amplify), carrying the winning hook into
the moment-request guidance. Gated FAR harder than v2: variant_learning.best_hooks as a FLOOR, plus
more posts + a bigger gap + a sustained lead across >= cfg.variant_amplify_min_streak DISTINCT
evidence windows. Default OFF (FANOPS_VARIANT_AMPLIFY).

SAFETY (the whole point): this module is AMPLIFY-ONLY. It must NEVER import or call retire /
_delete_moment_cascade / set_*_state(retired). A candidate failing the gate is simply not amplified
(it is NOT retired). On ANY doubt the actuator does nothing and leaves the ledger byte-identical
(fail-SAFE). Deterministic: no random/hash()/wall-clock; the streak fingerprint is content-addressed
via ids._hash, so a re-run on the same ledger is idempotent. Enforced by the retire-isolation AST
test + the mutation-proof + wrong-signal no-op tests in tests/test_variant_amplify.py."""
from __future__ import annotations
from fanops.ids import _hash
from fanops.models import Platform, PostState
from fanops.variant_learning import best_hooks


def _surfaces(led) -> set[tuple[str, Platform]]:
    """Distinct (account, platform) surfaces that have at least one analyzed variant post — derived
    purely from the ledger (no Accounts dependency), matching how best_hooks scopes per surface."""
    return {(p.account, p.platform) for p in led.posts.values()
            if p.variant_key and p.variant_hook and p.state is PostState.analyzed
            and "lift_score" in p.metrics}


def _evidence_fingerprint(led, account: str, platform: Platform) -> str:
    """A content-addressed digest of the SORTED analyzed post-ids for this surface. A NEW analyzed
    post changes it -> a new 'window'. Deterministic (ids._hash, no wall-clock/random)."""
    pids = sorted(p.id for p in led.posts.values()
                  if p.account == account and p.platform is platform
                  and p.state is PostState.analyzed and "lift_score" in p.metrics)
    return _hash("variant_streak", account, platform.value, *pids)


def update_streaks(led, cfg):
    """Advance/reset the per-surface sustained-win streak. Deterministic + idempotent on unchanged
    evidence. This is the ONLY state-mutating helper in this module, and it mutates ONLY
    led.variant_streaks (never a unit's state, never the amplify/retire path)."""
    for account, platform in _surfaces(led):
        key = f"{account}|{platform.value}"
        winners = best_hooks(led, cfg, account, platform)   # v2 gate (the FLOOR)
        prior = led.variant_streaks.get(key)
        if not winners:
            # No trustworthy winner now -> doubt resets the streak (fail-SAFE).
            if prior is None or prior.get("streak", 0) != 0:
                led.variant_streaks[key] = {"hook": None, "fingerprint": "", "streak": 0}
            continue
        winner = winners[0]
        fp = _evidence_fingerprint(led, account, platform)
        if prior is None or prior.get("hook") != winner:
            led.variant_streaks[key] = {"hook": winner, "fingerprint": fp, "streak": 1}
        elif prior.get("fingerprint") != fp:
            # Same winner, NEW evidence batch (a real new window) -> advance.
            led.variant_streaks[key] = {"hook": winner, "fingerprint": fp,
                                        "streak": int(prior.get("streak", 0)) + 1}
        # else: same winner, SAME evidence -> no change (idempotent re-run).
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -q && python -m pytest -q`
Expected: PASS; full suite `400 passed, 1 skipped` (394 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/variant_amplify.py tests/test_variant_amplify.py
git commit -m "feat (variant-amplify 4): update_streaks — deterministic, idempotent sustained-win streak tracker"
```

---

### Task 5: `variant_amplify.amplify_candidates` — the pure, fully-gated decision

**Files:**
- Modify: `src/fanops/variant_amplify.py` (add `amplify_candidates` + a `_source_for_surface` helper)
- Test: `tests/test_variant_amplify.py` (append)

**Note on test fixtures:** these tests need the full lineage `Post → Clip → Moment → Source` so the surface→source mapping resolves. Add a shared helper at the top of the test file:

```python
# tests/test_variant_amplify.py — ADD near the top (after the existing helpers)
from fanops.models import Source, Moment, Clip, SourceState

def _seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))

def _streak_to(led, cfg, key, n):
    """Drive update_streaks across n distinct-evidence windows so the surface reaches streak n."""
    from fanops.variant_amplify import update_streaks
    base = 1000
    for _ in range(n - int(led.variant_streaks.get(key, {}).get("streak", 0))):
        update_streaks(led, cfg)
        # add one NEW analyzed WIN post to make the next window distinct
        led.add_post(_post(str(base), "@a", led.variant_streaks[key]["hook"], 90.0)); base += 1
    update_streaks(led, cfg)
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_variant_amplify.py — ADD
from fanops.variant_amplify import amplify_candidates

def test_below_floor_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0)])  # < min_posts
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    assert amplify_candidates(led, cfg) == []

def test_floor_met_but_below_min_posts(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_POSTS=8; best_hooks floor min_posts=3
    led = _led(cfg, _winset(5, "WIN", 90.0))   # 5 >= floor(3) but < amplify(8)
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    # streak alone can't rescue it — posts < 8 must veto regardless of streak
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 9}
    assert amplify_candidates(led, cfg) == []

def test_gap_too_small_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_GAP=25
    posts = [_post(str(i), "@a", "WIN", 60.0) for i in range(8)]
    posts += [_post(str(20 + i), "@a", "LOSE", 50.0) for i in range(8)]   # gap 10 < 25
    led = _led(cfg, posts)
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 9}
    assert amplify_candidates(led, cfg) == []

def test_streak_too_small_no_candidate(tmp_path):
    # ALL of best_hooks-floor + min_posts + min_gap met, but streak < min_streak -> [].
    # THIS is the single-window guard — the core new safety property.
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))   # 8 posts, gap 89 -> floor+posts+gap all met
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 2}  # < 3
    assert amplify_candidates(led, cfg) == []

def test_all_gates_met_returns_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    cands = amplify_candidates(led, cfg)
    assert len(cands) == 1
    c = cands[0]
    assert c["source_id"] == "s1" and c["winning_hook"] == "WIN"
    assert c["post_id"] in {p.id for p in led.posts.values() if p.variant_hook == "WIN"}

def test_source_at_amplify_budget_skipped(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    led.sources["s1"].meta["amplify_count"] = 3        # E1 cap reached (max_amplify_per_source=3)
    assert amplify_candidates(led, cfg) == []

def test_empty_ledger_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    assert amplify_candidates(Ledger.load(cfg), cfg) == []

def test_amplify_candidates_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    assert amplify_candidates(led, cfg) == amplify_candidates(led, cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -k candidate -q`
Expected: FAIL with `ImportError: cannot import name 'amplify_candidates'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/variant_amplify.py — ADD (uses statistics.mean; add `from statistics import mean` to
# the imports at the top of the module).
def _source_for_surface(led, account: str, platform: Platform, hook: str):
    """Map a (surface, winning hook) to ONE source + a representative post (spec's deterministic
    source-mapping rule). The winning hook's analyzed posts may trace to several sources; pick the
    source with the MOST such posts (best-evidenced), ties broken by lowest source_id; the
    representative post_id is the lowest post_id among that source's winning-hook posts. Returns
    (source_id, post_id) or (None, None) if the lineage can't be resolved."""
    by_source: dict[str, list[str]] = {}
    for p in led.posts.values():
        if not (p.account == account and p.platform is platform and p.variant_hook == hook
                and p.state is PostState.analyzed and "lift_score" in p.metrics):
            continue
        clip = led.clips.get(p.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if src is None:
            continue
        by_source.setdefault(src.id, []).append(p.id)
    if not by_source:
        return None, None
    # most posts, then lowest source_id (lexicographic) — fully deterministic.
    source_id = min(by_source, key=lambda sid: (-len(by_source[sid]), sid))
    post_id = min(by_source[source_id])
    return source_id, post_id


def amplify_candidates(led, cfg) -> list[dict]:
    """Pure, read-only. Return the list of {source_id, winning_hook, post_id, evidence} to amplify —
    one per surface that clears the FULL gate (best_hooks floor + min_posts + min_gap + min_streak +
    E1 budget). [] on any doubt. No I/O, no mutation."""
    out: list[dict] = []
    for account, platform in sorted(_surfaces(led), key=lambda s: (s[0], s[1].value)):
        winners = best_hooks(led, cfg, account, platform)        # FLOOR (v2 gate)
        if not winners:
            continue
        hook = winners[0]
        # Re-derive the winner's posts/lifts on this surface for the v3 stronger thresholds.
        lifts = [float(p.metrics["lift_score"]) for p in led.posts.values()
                 if p.account == account and p.platform is platform and p.variant_hook == hook
                 and p.state is PostState.analyzed and "lift_score" in p.metrics]
        if len(lifts) < cfg.variant_amplify_min_posts:
            continue
        # runner-up mean among OTHER hooks on this surface (best_hooks already guaranteed >= 2 hooks).
        others: dict[str, list[float]] = {}
        for p in led.posts.values():
            if (p.account == account and p.platform is platform and p.variant_hook
                    and p.variant_hook != hook and p.state is PostState.analyzed
                    and "lift_score" in p.metrics):
                others.setdefault(p.variant_hook, []).append(float(p.metrics["lift_score"]))
        runner_mean = max((mean(v) for v in others.values()), default=0.0)
        if mean(lifts) - runner_mean < cfg.variant_amplify_min_gap:
            continue
        entry = led.variant_streaks.get(f"{account}|{platform.value}", {})
        if entry.get("hook") != hook or int(entry.get("streak", 0)) < cfg.variant_amplify_min_streak:
            continue                                             # single-window guard
        source_id, post_id = _source_for_surface(led, account, platform, hook)
        if source_id is None:
            continue
        if int(led.sources[source_id].meta.get("amplify_count", 0)) >= 3:   # E1 budget (max=3)
            continue
        out.append({"source_id": source_id, "winning_hook": hook, "post_id": post_id,
                    "evidence": {"posts": len(lifts), "streak": int(entry.get("streak", 0))}})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -q && python -m pytest -q`
Expected: PASS; full suite `408 passed, 1 skipped` (400 + 8 new).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/variant_amplify.py tests/test_variant_amplify.py
git commit -m "feat (variant-amplify 5): amplify_candidates — pure fully-gated decision (floor+posts+gap+streak+budget)"
```

---

### Task 6: `apply_variant_amplify` (fail-SAFE actuator) + retire-isolation AST test + the mutation proof

**Files:**
- Modify: `src/fanops/variant_amplify.py` (add `apply_variant_amplify`)
- Test: `tests/test_variant_amplify.py` (append actuator tests + the **retire-isolation AST** test + the **mutation-proof** adversarial test)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_variant_amplify.py — ADD
import ast
import pathlib
import json
from fanops.agentstep import request_path
from fanops.models import SourceState
from fanops.variant_amplify import apply_variant_amplify

def _frozen(led):
    """A comparable snapshot of the ledger's mutable state (for byte-identical assertions)."""
    return json.dumps({
        "sources": {k: v.model_dump() for k, v in led.sources.items()},
        "moments": {k: v.model_dump() for k, v in led.moments.items()},
        "clips": {k: v.model_dump() for k, v in led.clips.items()},
        "posts": {k: v.model_dump() for k, v in led.posts.items()},
        "variant_streaks": led.variant_streaks,
    }, sort_keys=True, default=str)

def test_apply_amplifies_when_fully_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    apply_variant_amplify(led, cfg)
    # the source was amplified: state flipped + the moment-request carries the winning hook.
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WIN" in payload["guidance"]
    # G2: the winning analyzed post survives, state unchanged.
    win_posts = [p for p in led.posts.values() if p.variant_hook == "WIN"]
    assert win_posts and all(p.state is PostState.analyzed for p in win_posts)

def test_apply_noop_when_gate_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 1}  # < 3
    before = _frozen(led)
    apply_variant_amplify(led, cfg)
    assert _frozen(led) == before          # nothing changed — no amplify, no state flip
    assert not request_path(cfg, "moments", "s1").exists()

def test_apply_failsafe_on_internal_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    # Make the candidate computation raise -> the whole pass must swallow it, no partial mutation.
    monkeypatch.setattr("fanops.variant_amplify.amplify_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = _frozen(led)
    apply_variant_amplify(led, cfg)        # must NOT raise
    assert _frozen(led) == before

# --- The retire-isolation invariant (v3's C1 safety, mechanized — mirrors test_variant_learning's
#     AST approach but asserts the REVERSE direction: variant_amplify must be BLIND to the
#     retire/delete surface). -------------------------------------------------------------------
_FORBIDDEN_IN_VARIANT_AMPLIFY = ("retire", "_delete_moment_cascade", "retire_clip",
                                 "set_moment_state", "set_clip_state")

def _names_in_module(src_path: pathlib.Path, func_names: set[str]) -> set[str]:
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

def test_variant_amplify_never_touches_retire_or_cascade():
    """G1 (STRUCTURAL): variant_amplify is amplify-only. Its functions must reference NONE of
    retire / _delete_moment_cascade / retire_clip / set_moment_state / set_clip_state — so a wrong
    'this won' signal can never reach a delete/retire. A future edit wiring any of those in goes RED
    and names the offender."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    names = _names_in_module(root / "variant_amplify.py",
                             {"update_streaks", "amplify_candidates", "apply_variant_amplify",
                              "_source_for_surface", "_surfaces", "_evidence_fingerprint"})
    leaked = sorted(names & set(_FORBIDDEN_IN_VARIANT_AMPLIFY))
    assert not leaked, f"variant_amplify must never call retire/cascade; found: {leaked}"

def test_variant_amplify_module_does_not_import_retire():
    """Belt-and-braces: the module must not import retire from adjust (it imports amplify only)."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    tree = ast.parse((root / "variant_amplify.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for n in node.names:
                imported.add(n.name)
    assert "retire" not in imported

# --- The MUTATION PROOF: the streak gate must be load-bearing. With the streak requirement
#     removed, a SINGLE-window signal would amplify — this test asserts that today it does NOT,
#     so when an implementer weakens the gate the suite goes red here. --------------------------
def test_single_window_signal_does_not_amplify(tmp_path, monkeypatch):
    """ADVERSARIAL: a strong but SINGLE-window signal (streak 1) must NEVER amplify. This is the
    mutation sentinel — if amplify_candidates ever stops requiring min_streak, this goes RED."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(20, "WIN", 99.0))      # overwhelming evidence in ONE window
    _seed_lineage(led)
    for p in led.posts.values():
        p.parent_id = "c1"
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 1}
    before = _frozen(led)
    apply_variant_amplify(led, cfg)
    assert amplify_candidates(led, cfg) == []       # gate holds despite huge single-window evidence
    assert _frozen(led) == before                   # and nothing was amplified/retired/deleted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -k "apply or never_touches or single_window or import_retire" -q`
Expected: FAIL with `ImportError: cannot import name 'apply_variant_amplify'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/variant_amplify.py — ADD (imports: add `from fanops.adjust import amplify` and
# `from fanops.log import get_logger` at the top — NOTE: import `amplify` ONLY, never `retire`).
def apply_variant_amplify(led, cfg):
    """Actuator. Update streaks, then amplify each fully-gated candidate's source — injecting the
    winning hook as extra guidance. AMPLIFY-ONLY: never calls retire/_delete_moment_cascade. FAIL-SAFE:
    any exception -> log once, NO partial mutation beyond what already committed, return led. The
    caller (cli.run / cmd_amplify_variants) holds the transaction; an uncaught raise there would roll
    back, but we swallow here so an autonomous run never even sees it."""
    if not cfg.variant_amplify:
        return led                                  # kill switch / default OFF -> inert
    try:
        update_streaks(led, cfg)
        for cand in amplify_candidates(led, cfg):
            hint = (f"Recent on-screen hooks that performed best here: '{cand['winning_hook']}'. "
                    f"Lean toward this STYLE (tone, length, angle) — do not copy verbatim.")
            amplify(led, cfg, [cand["post_id"]], extra_guidance=hint)   # the existing C1-fixed path
    except Exception:
        get_logger(cfg)("variant_amplify", "-", "error")
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -q && python -m pytest -q`
Expected: PASS; full suite `415 passed, 1 skipped` (408 + 7 new).

- [ ] **Step 5: Mutation-proof the streak gate by hand (the TDD "watch the safety fail" equivalent)**

Temporarily weaken the gate in `amplify_candidates` (comment out the `min_streak` veto: change `if entry.get("hook") != hook or int(entry.get("streak", 0)) < cfg.variant_amplify_min_streak:` to `if entry.get("hook") != hook:`), then run:

Run: `source .venv/bin/activate && python -m pytest tests/test_variant_amplify.py -k single_window -q`
Expected: **FAIL** (`test_single_window_signal_does_not_amplify` goes RED — proving the streak gate is load-bearing). **Then REVERT the weakening** and confirm `git diff src/fanops/variant_amplify.py` shows no change, and re-run the file green.

- [ ] **Step 6: Commit**

```bash
git add src/fanops/variant_amplify.py tests/test_variant_amplify.py
git commit -m "feat (variant-amplify 6): fail-SAFE apply_variant_amplify + retire-isolation AST + mutation-proven streak gate"
```

---

### Task 7: CLI wiring — `fanops amplify-variants` verb + gated call in the autonomous `run` loop

**Files:**
- Modify: `src/fanops/cli.py` (new `cmd_amplify_variants` near `cmd_adjust` ~line 81; subparser ~line 121; dispatch ~line 260; the `run` learning block ~line 348-356)
- Test: `tests/test_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — ADD (grep this file for how other cmd_* tests build cfg/led on tmp_path +
# invoke main([...]); reuse that style.)
def test_amplify_variants_verb_runs_and_is_noop_below_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.cli import main
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()                      # empty ledger on disk
    rc = main(["amplify-variants"])
    assert rc == 0                               # registered verb, clean exit, no candidates

def test_amplify_variants_unknown_flag_off_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_AMPLIFY", raising=False)
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.cli import main
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    assert main(["amplify-variants"]) == 0       # flag OFF -> apply_variant_amplify is inert
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_cli.py -k amplify_variants -q`
Expected: FAIL — `argparse` errors on the unknown `amplify-variants` subcommand (SystemExit / nonzero).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — (a) ADD the import near the other adjust import (line 21):
from fanops.variant_amplify import apply_variant_amplify

# (b) ADD cmd_amplify_variants right after cmd_adjust (~line 92), mirroring cmd_adjust's transaction:
def cmd_amplify_variants(cfg: Config) -> int:
    # Variant-gated amplification (v3): one transaction wrapping apply_variant_amplify (no network —
    # like cmd_adjust). Inert unless FANOPS_VARIANT_AMPLIFY is on (the function self-guards).
    with Ledger.transaction(cfg) as led:
        before = len(led.sources_in_state(SourceState.moments_requested))
        led = apply_variant_amplify(led, cfg)
        after = len(led.sources_in_state(SourceState.moments_requested))
    write_digest(Ledger.load(cfg), cfg)
    print(f"variant-amplify: {max(0, after - before)} source(s) amplified")
    return 0

# (c) REGISTER the subparser (after the gc parser, ~line 121):
    sub.add_parser("amplify-variants")

# (d) DISPATCH (after the adjust dispatch line ~260):
    if args.cmd == "amplify-variants": return cmd_amplify_variants(cfg)

# (e) WIRE into the autonomous run learning block. The current block (~line 348-354) is:
#         if cfg.poster_backend != "dryrun" and cfg.blotato_api_key:
#             try:
#                 with Ledger.transaction(cfg) as led:
#                     led = pull_metrics(led, cfg)
#                     r = classify_outcomes(led)
#                     led = amplify(led, cfg, r["winners"])
#                     led = retire(led, r["losers"])
#             except Exception as e:
#                 get_logger(cfg)("learn", "-", "error", err=str(e)[:120])
#     ADD a SECOND, separately-guarded block immediately AFTER it (NOT inside the same try — v3 must
#     be independently gated by its own flag and must never affect the existing learn block):
        if cfg.variant_amplify and cfg.poster_backend != "dryrun" and cfg.blotato_api_key:
            try:
                with Ledger.transaction(cfg) as led:
                    led = apply_variant_amplify(led, cfg)
            except Exception as e:
                get_logger(cfg)("variant_amplify", "-", "error", err=str(e)[:120])
```

Also ensure `SourceState` is imported in cli.py (grep: it likely is via models; if not, add it to the existing models import).

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_cli.py -q && python -m pytest -q`
Expected: PASS; full suite `417 passed, 1 skipped` (415 + 2 new).

- [ ] **Step 5: Verify through the REAL CLI** (the project's "verify via the real CLI, not just pytest" rule)

```bash
cd "$(mktemp -d)" && source "/Users/molhamhomsi/Moh Flow Fanops/.venv/bin/activate"
# dryrun (default) -> v3 block is skipped by the guard; verb still runs clean:
FANOPS_VARIANT_AMPLIFY=1 fanops amplify-variants ; echo "exit=$?"
```
Expected: `variant-amplify: 0 source(s) amplified`, `exit=0` (empty ledger, dryrun — inert, no traceback). Return to the repo dir afterward.

- [ ] **Step 6: Commit**

```bash
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (variant-amplify 7): fanops amplify-variants verb + independently-gated call in run loop"
```

---

### Task 8: Digest — "Variant amplification" observability line (fail-open)

**Files:**
- Modify: `src/fanops/digest.py` (in `render_digest`, after the "Lift by variant" section ~line 110)
- Test: `tests/test_digest.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest.py — ADD (reuse this file's existing led/cfg fixture style)
def test_digest_shows_variant_amplify_streak(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for i in range(8):
        led.add_post(Post(id=str(i), parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"v{i}", variant_hook="WIN", metrics={"lift_score": 90.0}))
    for i in range(3):
        led.add_post(Post(id=f"l{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vl{i}", variant_hook="LOSE", metrics={"lift_score": 1.0}))
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 2}
    out = render_digest(led, cfg)
    assert "Variant amplification" in out
    assert "2/3" in out or "streak" in out.lower()      # building-streak state shown

def test_digest_no_amplify_section_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_AMPLIFY", raising=False)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path)
    out = render_digest(Ledger.load(cfg), cfg)
    assert "Variant amplification" not in out            # flag off -> section absent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest.py -k variant_amplif -q`
Expected: FAIL (`Variant amplification` not in output).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/digest.py — in render_digest, AFTER the "Lift by variant" block (~line 110), ADD a
# fail-open section. Reuse the same surfaces + the streak state (the gate logic has ONE home in
# variant_amplify; here we only DISPLAY).
    if cfg.variant_amplify:
        try:
            from fanops.variant_amplify import amplify_candidates, _surfaces, update_streaks
            # display-only: compute streaks on a throwaway pass over a shallow copy of the streak map
            preview = dict(led.variant_streaks)
            cand_sources = {c["source_id"] for c in amplify_candidates(led, cfg)}
            alines = []
            for account, platform in sorted(_surfaces(led), key=lambda s: (s[0], s[1].value)):
                key = f"{account}|{platform.value}"
                entry = preview.get(key, {})
                streak = int(entry.get("streak", 0))
                src_amplified = any(  # is this surface's mapped source in the candidate set?
                    True for _ in [0]) and bool(cand_sources)
                state = ("amplified" if cand_sources else
                         f"building streak ({streak}/{cfg.variant_amplify_min_streak})"
                         if streak else "gathering data")
                alines.append(f"- `{entry.get('hook') or '-'}` ({account}/{platform.value}): {state}")
            if alines:
                out.append("\n## Variant amplification (v3 — proven winners → more reach)\n"
                           + "\n".join(alines) + "\n")
        except Exception:
            logger.warning("variant-amplify digest section degraded (fail-open)", exc_info=True)
```

(Keep it simple and robust; the exact "amplified vs building" phrasing only needs to satisfy the test's `"2/3" or "streak"` assertion and the flag-off absence. If a cleaner per-surface candidate check is easy, use it — but never let this section raise.)

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest.py -q && python -m pytest -q`
Expected: PASS; full suite `419 passed, 1 skipped` (417 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat (variant-amplify 8): digest 'Variant amplification' streak/state line (fail-open)"
```

---

### Task 9: Real on-disk integration + docs (RUNTIME env table, backlog, handoff)

**Files:**
- Create: `tests/integration/test_variant_amplify_real.py`
- Modify: `MohFlow-FanOps/00_control/RUNTIME.md` (env-var table + backlog (j)); `docs/handoff.md` (§Now + §State); the memory note (via the handoff skill)

- [ ] **Step 1: Write the real integration test** (the Integrate bar — proves the loop closes on disk, no mocks of the decision/amplify path)

```python
# tests/integration/test_variant_amplify_real.py — NEW
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, SourceState
from fanops.variant_amplify import apply_variant_amplify
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration

def _win(pid, hook, lift):
    return Post(id=pid, parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.analyzed, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})

def test_sustained_winner_amplifies_source_on_disk(tmp_path, monkeypatch):
    """A REAL ledger on disk: @a's 'WIN' hook out-lifts a runner-up over >= AMPLIFY_MIN_POSTS posts;
    drive apply_variant_amplify across enough distinct-evidence windows to satisfy the streak with
    FANOPS_VARIANT_AMPLIFY=1; assert the ACTUAL moment-request file carries the winning hook AND the
    source state is moments_requested — the auto-amplify closing end-to-end, not via mocks."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
        led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=4.0, reason="r",
                              transcript_excerpt="ex"))
        led.add_clip(Clip(id="c1", parent_id="m1", path="c1.mp4"))
        for i in range(8):
            led.add_post(_win(str(i), "WIN", 95.0))
        for i in range(3):
            led.add_post(_win(f"l{i}", "LOSE", 1.0))

    # Drive >= min_streak windows, adding ONE new analyzed WIN post per window (distinct evidence).
    nid = 100
    for _ in range(cfg.variant_amplify_min_streak + 1):
        with Ledger.transaction(cfg) as led:
            led = apply_variant_amplify(led, cfg)
        with Ledger.transaction(cfg) as led:
            led.add_post(_win(str(nid), "WIN", 95.0)); nid += 1

    # Final pass once the streak is satisfied -> amplify must fire.
    with Ledger.transaction(cfg) as led:
        led = apply_variant_amplify(led, cfg)

    led = Ledger.load(cfg)
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WIN" in payload["guidance"]
    # The winning posts survive (G2 — never deleted by v3).
    assert [p for p in led.posts.values() if p.variant_hook == "WIN" and p.state is PostState.analyzed]
```

- [ ] **Step 2: Run the integration test**

Run: `source .venv/bin/activate && python -m pytest tests/integration/test_variant_amplify_real.py -q`
Expected: PASS (1 passed).

- [ ] **Step 3: Full verification sweep**

Run: `source .venv/bin/activate && python -m pytest -q && python -m pytest tests/integration -q && ruff check src/`
Expected: full unit suite green (`419 passed`-ish + the integration test), integration green, ruff clean.

- [ ] **Step 4: Docs — run `sync-docs`, then update RUNTIME + backlog**

- In `MohFlow-FanOps/00_control/RUNTIME.md` env-var table: add `FANOPS_VARIANT_AMPLIFY` (default OFF — kill switch), `FANOPS_VARIANT_AMPLIFY_MIN_POSTS` (8), `FANOPS_VARIANT_AMPLIFY_MIN_GAP` (25.0), `FANOPS_VARIANT_AMPLIFY_MIN_STREAK` (3), and document the new `fanops amplify-variants` verb in the command list.
- In RUNTIME §Backlog (j): note that v3 (variant-gated amplification) closes the auto-propagate-into-amplify follow-up — amplify-only, sustained-streak-gated, default OFF; the bandit follow-up remains open.
- Run `sync-docs` to catch README/CLAUDE.md/SPEC drift (new module → src/tests counts; new flags; new verb).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_variant_amplify_real.py MohFlow-FanOps/00_control/RUNTIME.md docs/ src/ README.md 2>/dev/null
git commit -m "feat (variant-amplify 9): real on-disk amplify integration + RUNTIME/backlog/docs"
```

- [ ] **Step 6: Handoff** — invoke the `handoff` skill to rewrite §Now (variant-amplify v3 shipped; suite count; amplify-only/streak-gated/default-OFF; bandit still open) and update the memory note `fanops-variation-v2-learning-loop.md` (mark follow-up #1 done).

---

## Self-Review

**Spec coverage:**
- Mechanism (gate existing amplify + inject hook) → Tasks 3 (kwarg) + 6 (actuator calls `amplify(..., extra_guidance=hint)`). ✓
- Trust gate: best_hooks FLOOR + min_posts(8) + min_gap(25) + streak(3) + E1 budget → Task 5 (`amplify_candidates`), tests (a)-(h). ✓
- Window = new evidence batch; deterministic, idempotent → Task 4 (`update_streaks`) + the fingerprint via `ids._hash`; idempotency test is explicit. ✓
- Safety G1 amplify-only (AST) → Task 6 `test_variant_amplify_never_touches_retire_or_cascade` + `_does_not_import_retire`. ✓
- G2 never deletes live post → Task 6 `test_apply_amplifies_when_fully_gated` asserts winning posts survive; existing cascade tests stay green. ✓
- G3 any-doubt no-op → Task 6 `test_apply_noop_when_gate_unmet` + `_failsafe_on_internal_error` (byte-identical). ✓
- G4 kill switch / default OFF → Task 1 + Task 6 self-guard (`if not cfg.variant_amplify`) + Task 7 independent guard. ✓
- G5 adversarial + mutation proof → Task 6 `test_single_window_signal_does_not_amplify` + Step 5 hand mutation. ✓
- Deterministic source-mapping rule (most posts, tie lowest id) → Task 5 `_source_for_surface`. ✓
- Ledger field mirrors tag_log, backward-compatible → Task 2 + old-ledger test. ✓
- CLI verb + gated run wiring → Task 7 + real-CLI verify. ✓
- Digest observability → Task 8. ✓
- Real integration on disk → Task 9. ✓
- Out-of-scope (no classify_outcomes/retire change) → enforced by the retire-isolation AST test (Task 6) + no task touches classify_outcomes. ✓

**Placeholder scan:** every code step shows complete code; commands have expected output; test bodies are concrete. The Task 8 digest note allows the implementer minor phrasing latitude but pins the asserted behavior (section present with streak text when on; absent when off) — acceptable (not a placeholder; the contract is test-locked). ✓

**Type/name consistency:** `variant_amplify` / `amplify_candidates` / `update_streaks` / `apply_variant_amplify` / `_source_for_surface` / `_surfaces` / `_evidence_fingerprint` used consistently across Tasks 4-8 and the AST test's `func_names` set. Config props `variant_amplify` / `variant_amplify_min_posts` / `variant_amplify_min_gap` / `variant_amplify_min_streak` consistent (Tasks 1, 4, 5). `Ledger.variant_streaks` shape `{hook, fingerprint, streak}` consistent (Tasks 2, 4, 5, 6, 8). `amplify(..., extra_guidance=...)` consistent (Tasks 3, 6). ✓

**Suite-count note:** the running totals (387 → 419) assume the literal new-test counts above; the FILE is authoritative (per the build-deviations note about prose-vs-literal counts). If a count differs by ±1, trust the green bar, not the number.
