# Implementation Plan — Creative Variation v2: Closing the Learning Loop

**Spec:** `docs/superpowers/specs/2026-06-04-creative-variation-v2-feedback-design.md`
**Builds on:** v1 (PR #9, observe-only). **Backlog:** (j) follow-up.
**Discipline:** strict TDD (RED → GREEN → VERIFY), default-OFF flag, fail-open, amplify-isolation
proven by grep. Each task ends green on the FULL suite. New baseline: 363 passed, 1 skipped.

**The one-line summary:** add a pure read-only scorer (`variant_learning.best_hooks`) that, once a
variant has *enough* data and a *real* lead, feeds the winning hook style back into the next caption
request — closing the A/B loop on the cheap/reversible side and never touching the amplify cascade.

---

### Task 1: `variant_learning.best_hooks` — the gated, pure scorer (the load-bearing safety unit)

**Files:**
- Create: `src/fanops/variant_learning.py`
- Create: `tests/test_variant_learning.py`

- [ ] **Step 1: Write the failing tests** (the gate is the whole safety argument — test it first and hardest)

```python
# tests/test_variant_learning.py — NEW
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
    for p in posts: led.add_post(p)
    return led

def test_below_min_posts_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)          # MIN_POSTS default 3
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0)])  # only 2
    assert best_hooks(led, cfg, "@a", Platform.instagram) == []

def test_enough_posts_but_gap_too_small_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)          # MIN_GAP default ~10
    led = _led(cfg, [_post("1","@a","WIN",51.0), _post("2","@a","WIN",51.0), _post("3","@a","WIN",51.0),
                     _post("4","@a","LOSE",50.0), _post("5","@a","LOSE",50.0), _post("6","@a","LOSE",50.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == []   # 1.0 gap < MIN_GAP → noise guard

def test_clear_winner_over_threshold_returned(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1","@a","WIN",90.0), _post("2","@a","WIN",90.0), _post("3","@a","WIN",90.0),
                     _post("4","@a","LOSE",10.0), _post("5","@a","LOSE",10.0), _post("6","@a","LOSE",10.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == ["WIN"]

def test_other_surface_isolated(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1","@a","WIN",90.0), _post("2","@a","WIN",90.0), _post("3","@a","WIN",90.0)])
    assert best_hooks(led, cfg, "@b", Platform.instagram) == []   # no data for @b

def test_empty_and_no_variant_posts(tmp_path):
    cfg = Config(root=tmp_path)
    assert best_hooks(Ledger.load(cfg), cfg, "@a", Platform.instagram) == []

def test_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1","@a","WIN",90.0), _post("2","@a","WIN",90.0), _post("3","@a","WIN",90.0),
                     _post("4","@a","LOSE",10.0), _post("5","@a","LOSE",10.0), _post("6","@a","LOSE",10.0)])
    assert best_hooks(led, cfg, "@a", Platform.instagram) == best_hooks(led, cfg, "@a", Platform.instagram)
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_variant_learning.py -q` → FAIL (no module).

- [ ] **Step 3: Minimal implementation**

```python
# src/fanops/variant_learning.py — NEW
"""Creative-variation v2: the SAFE half of the A/B loop. Pure, read-only scoring of which
per-account hook variant has earned a trustworthy win, so request_captions can bias the next
caption toward it. Touches NONE of amplify/classify_outcomes/_delete_moment_cascade (C1)."""
from __future__ import annotations
from statistics import mean
from fanops.models import Platform, PostState

def best_hooks(led, cfg, account: str, platform: Platform) -> list[str]:
    """Return the winning hook(s) for this surface IFF the leader has >= min_posts analyzed
    posts AND beats the runner-up's mean lift by >= min_gap. Else []. No I/O, no mutation."""
    min_posts = cfg.variant_min_posts
    min_gap = cfg.variant_min_gap
    by_hook: dict[str, list[float]] = {}
    for p in led.posts.values():
        if (p.variant_key and p.variant_hook and p.account == account and p.platform is platform
                and p.state is PostState.analyzed and "lift_score" in p.metrics):
            by_hook.setdefault(p.variant_hook, []).append(float(p.metrics["lift_score"]))
    if not by_hook:
        return []
    ranked = sorted(by_hook.items(), key=lambda kv: mean(kv[1]), reverse=True)
    leader_hook, leader_lifts = ranked[0]
    if len(leader_lifts) < min_posts:
        return []
    runner_mean = mean(ranked[1][1]) if len(ranked) > 1 else 0.0
    if mean(leader_lifts) - runner_mean < min_gap:
        return []
    return [leader_hook]
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_variant_learning.py -q`. (Config fields land in Task 2; until then add temporary defaults or land Task 2 first — see note.) **NOTE:** this task DEPENDS on Task 2's config fields. Land Task 2's config additions FIRST, or stub `cfg.variant_min_posts`/`variant_min_gap` in the test via a Config subclass. Cleanest: do Task 2 config first, then this. Plan order below reflects that.

- [ ] **Step 5: Commit** — `feat (variation v2 1): variant_learning.best_hooks — gated pure scorer (min-posts + min-gap)`

---

### Task 2: Config — `FANOPS_VARIANT_LEARNING` + thresholds (default OFF / conservative)

**Files:** Modify `src/fanops/config.py`; Modify `tests/test_config.py` (append).

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py — ADD (match the file's existing toggle-test style)
def test_variant_learning_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_LEARNING","FANOPS_VARIANT_MIN_POSTS","FANOPS_VARIANT_MIN_GAP"):
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
```

- [ ] **Step 2: Verify fail** — `python -m pytest tests/test_config.py -k variant -q` → FAIL.

- [ ] **Step 3: Implement** (grep `config.py` for how `FANOPS_CREATIVE_VARIATION` / `_bool` is done; MATCH it exactly)

```python
# src/fanops/config.py — add properties alongside the existing creative_variation toggle:
    @property
    def variant_learning(self) -> bool:
        return _truthy(os.getenv("FANOPS_VARIANT_LEARNING"))   # reuse the existing truthy helper
    @property
    def variant_min_posts(self) -> int:
        try: return int(os.getenv("FANOPS_VARIANT_MIN_POSTS", "3"))
        except ValueError: return 3
    @property
    def variant_min_gap(self) -> float:
        try: return float(os.getenv("FANOPS_VARIANT_MIN_GAP", "10"))
        except ValueError: return 10.0
```

- [ ] **Step 4: Verify pass** — `python -m pytest tests/test_config.py -q && python -m pytest -q`.
- [ ] **Step 5: Commit** — `feat (variation v2 2): FANOPS_VARIANT_LEARNING config + thresholds (default OFF)`

---

### Task 3: `caption_prompt` renders the learned-hint block when present

**Files:** Modify `src/fanops/prompts.py`; Modify `tests/test_prompts.py` (append).

- [ ] **Step 1: Failing test**

```python
# tests/test_prompts.py — ADD
def test_caption_prompt_renders_learned_hint():
    from fanops.prompts import caption_prompt
    p = caption_prompt({"clip_id":"c1","surfaces":[{"surface":"@a|instagram","platform":"instagram"}],
                        "learned_hooks":["WIN HOOK"]})
    assert "WIN HOOK" in p
    assert "verbatim" in p.lower() or "copy" in p.lower()   # the "lean toward, don't copy" instruction

def test_caption_prompt_no_hint_when_absent():
    from fanops.prompts import caption_prompt
    base = {"clip_id":"c1","surfaces":[{"surface":"@a|instagram","platform":"instagram"}]}
    assert "WIN HOOK" not in caption_prompt(base)            # absent → unchanged
```

- [ ] **Step 2: Verify fail** → FAIL.
- [ ] **Step 3: Implement** — in `caption_prompt`, if `payload.get("learned_hooks")`, append a labelled block: e.g. `"\n  - What worked recently for these accounts (lean toward this STYLE — tone/length/angle — do NOT copy verbatim): {json.dumps(learned)}\n"`. Absent → no change (byte-identical).
- [ ] **Step 4: Verify pass** — `python -m pytest tests/test_prompts.py -q && python -m pytest -q`.
- [ ] **Step 5: Commit** — `feat (variation v2 3): caption_prompt renders learned-hook style hint`

---

### Task 4: `request_captions` injects the gated hint per surface (the loop CLOSES) — fail-open

**Files:** Modify `src/fanops/caption.py`; Modify `tests/test_caption.py` (append).

- [ ] **Step 1: Failing tests** (the heart of v2 — assert the request payload on disk carries the hint, and OFF/below-gate is byte-identical to today)

```python
# tests/test_caption.py — ADD (grep this file for how it builds led/cfg + a clip with a parent moment/source;
# reuse that fixture style. Then seed analyzed variant posts so best_hooks fires.)
def test_request_captions_injects_learned_hint_when_gate_met(monkeypatch, tmp_path, <existing fixtures>):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    # ... build led+cfg+clip for surface @a|instagram, seed 3 analyzed @a "WIN" posts lift 90 + 3 "LOSE" lift 10 ...
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    import json; from fanops.agentstep import request_path
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "WIN" in payload["guidance"]                      # the learned hint reached the agent request

def test_request_captions_no_hint_when_learning_off(monkeypatch, tmp_path, <existing fixtures>):
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    # ... same seeded ledger ...
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "WIN" not in payload["guidance"]                  # OFF → today's behavior

def test_request_captions_failopen_on_learning_error(monkeypatch, tmp_path, <existing fixtures>):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setattr("fanops.caption.best_hooks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    request_captions(led, cfg, clip_id, [("@a", Platform.instagram)])   # must NOT raise
    assert (request_path(cfg, "captions", clip_id)).exists()            # request still written, clip advances
```

- [ ] **Step 2: Verify fail** → FAIL.
- [ ] **Step 3: Implement** — in `request_captions`, after building `guidance = _guidance(cfg)`:

```python
    learned: list[str] = []
    if cfg.variant_learning:
        try:
            from fanops.variant_learning import best_hooks
            seen = set()
            for acct, plat in surfaces:
                for h in best_hooks(led, cfg, acct, plat):
                    if h not in seen: seen.add(h); learned.append(h)
        except Exception:
            logger.warning("variant_learning hint skipped (fail-open)", exc_info=True)
            learned = []
    payload = {... , "guidance": guidance, "surfaces": [...], **({"learned_hooks": learned} if learned else {})}
```

- [ ] **Step 4: Verify pass** — `python -m pytest tests/test_caption.py -q && python -m pytest -q`.
- [ ] **Step 5: Commit** — `feat (variation v2 4): request_captions injects gated learned-hook hint — loop closes, fail-open`

---

### Task 5: Amplify-isolation proof + digest gate-state line + real integration + docs

**Files:** `tests/test_variant_learning.py` (isolation grep test); `src/fanops/digest.py` (+ test); `tests/integration/test_variant_learning_real.py` (NEW); `MohFlow-FanOps/00_control/RUNTIME.md`; `docs/handoff.md`; backlog (j) update.

- [ ] **Step 1: Amplify-isolation test** (mirrors v1's C1 invariant — the safety claim, mechanized)

```python
# tests/test_variant_learning.py — ADD
def test_learning_never_imported_by_amplify_path():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    for f in ("track.py", "pipeline.py"):
        assert "variant_learning" not in (root / f).read_text(), f"{f} must stay blind to variant_learning (C1)"
```

- [ ] **Step 2: Digest gate-state line** (optional but cheap; reuse `best_hooks` so gate logic has ONE home) — in the existing "Lift by variant" section, annotate each surface "learning ACTIVE" vs "gathering data" via `best_hooks(...)` non-empty. Test: a past-gate surface shows "ACTIVE"; a below-gate one shows "gathering". Keep it fail-open.

- [ ] **Step 3: Real integration** (`tests/integration/test_variant_learning_real.py`) — build a REAL ledger on disk where @a's "WIN" hook out-lifts @b's "LOSE" over ≥ MIN_POSTS analyzed posts; set `FANOPS_VARIANT_LEARNING=1`; call `request_captions`; read the ACTUAL request file from `04_agent_io/requests/` and assert its `guidance`/`learned_hooks` carries "WIN". This proves the loop closing end-to-end on disk, not via mocks (the project's Integrate bar).

- [ ] **Step 4: Verify everything** — `python -m pytest -q && python -m pytest tests/integration -q && ruff check src/`. Expect full suite green (363 + new), integration green, ruff clean.

- [ ] **Step 5: Docs + backlog** — run `sync-docs`. In `RUNTIME.md`: add `FANOPS_VARIANT_LEARNING` (+ thresholds) to the env-var table; flip backlog **(j)** from "v1 DONE (observe-only) … auto-propagating winners is a documented follow-up" to note v2 closes the loop on the **caption-bias** side (amplify auto-propagation still out of scope / C1). Update `docs/handoff.md` §Now + §State (suite count, new module → 39 src / 45 tests). Commit.

- [ ] **Step 6: Final commit** — `feat (variation v2 5): amplify-isolation proof + digest gate-state + real integration + docs`

---

## Self-Review

- **Does it actually close the loop?** Yes — Task 4 + the Task 5 integration prove a winning hook
  reaches the next caption request on disk. That is the open loop, closed.
- **Is the safety real, not asserted?** The gate (Task 1 tests a+b) blocks acting on thin/noisy
  data; the isolation test (Task 5 Step 1) mechanically forbids the amplify path from importing the
  learner. Fail-open is tested (Task 4). Determinism is tested (Task 1).
- **Could it delete/retire anything?** No — the only write is the caption *request* payload (an
  agent-input file). It cannot mutate the ledger, a unit's state, or the delete cascade.
- **Reversible?** Flip `FANOPS_VARIANT_LEARNING` off → the next request reverts. Nothing persisted.
- **Dependency order:** Task 2 (config) before Task 1's green (Task 1 reads `cfg.variant_*`). Execute
  **2 → 1 → 3 → 4 → 5**. (Listed 1-first for narrative; the checklist note + this line fix the order.)
