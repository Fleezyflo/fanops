# Cross-Account / Cross-Surface Learning Transfer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a hook STYLE proven on one surface act as a demoted, same-platform, persona-aware weak prior for *other cold* surfaces' caption requests, so learning compounds across the account network without collapsing per-account creative diversity.

**Architecture:** A new pure read-only scorer `variant_transfer.transferred_hooks(led, cfg, accounts, account, platform)` mirrors v2's `best_hooks`: it returns `[]` if the recipient already has its own gated winner (own-wins rule), else gathers OTHER active **same-platform** surfaces' v2-gated winners (each via `variant_learning.best_hooks` — gate reused verbatim), keeps styles that won on `≥ TRANSFER_MIN_DONORS` distinct donor surfaces, drops any the recipient already won, persona-ranks deterministically, and caps at `TRANSFER_MAX_HOOKS`. `request_captions` adds a SECOND payload key `learned_hooks_transferred` (distinct from v2's `learned_hooks`), fail-open. `caption_prompt` renders it as a weaker block BELOW the own-surface block. Observe-then-bias, caption-request side only — touches none of amplify/C1.

**Tech Stack:** Python 3, pydantic models, pytest. Run from repo root with `source .venv/bin/activate` active. Test command: `python -m pytest -q`. Lint: `ruff check src/`.

**Spec:** `docs/superpowers/specs/2026-06-04-cross-account-learning-transfer-design.md`

**Global invariants (every task honors these):**
- Default **OFF** behind `FANOPS_VARIANT_TRANSFER` (independent flag). Fail-open: any error → no prior → v2/today behavior.
- **Deterministic:** no `random`, no `hash()`, no wall-clock. Persona ranking is lowercased word-set Jaccard with a stable tiebreak (donor-surface count desc, then hook string asc).
- Each task ends with the **full suite green** (`python -m pytest -q`) and **`ruff check src/`** clean.
- Reuse v2's `best_hooks` gate verbatim on every donor — never re-implement or loosen it.

---

## Task 1: Config flags (`FANOPS_VARIANT_TRANSFER`, `_MIN_DONORS`, `_MAX_HOOKS`)

**Files:**
- Modify: `src/fanops/config.py` (add three properties after `variant_min_gap`, ~line 166)
- Test: `tests/test_config.py` (add after `test_variant_learning_env_overrides`, ~line 73)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_variant_transfer_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_TRANSFER", "FANOPS_VARIANT_TRANSFER_MIN_DONORS",
              "FANOPS_VARIANT_TRANSFER_MAX_HOOKS"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_transfer is False
    assert c.variant_transfer_min_donors == 2
    assert c.variant_transfer_max_hooks == 2


def test_variant_transfer_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "yes")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "3")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    c = Config(root=tmp_path)
    assert c.variant_transfer is True
    assert c.variant_transfer_min_donors == 3
    assert c.variant_transfer_max_hooks == 1


def test_variant_transfer_bad_ints_fall_back(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "notanint")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "")
    c = Config(root=tmp_path)
    assert c.variant_transfer_min_donors == 2          # bad int -> default, no crash
    assert c.variant_transfer_max_hooks == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_variant_transfer_defaults_off -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'variant_transfer'`

- [ ] **Step 3: Add the three config properties**

In `src/fanops/config.py`, immediately after the `variant_min_gap` property (ends ~line 166), add:

```python
    @property
    def variant_transfer(self) -> bool:
        # Cross-account / cross-surface learning transfer (the v2 follow-up): with this ON,
        # request_captions may bias a COLD recipient surface (one with no trustworthy winner of its
        # own yet) toward a hook STYLE proven on OTHER same-platform surfaces. INDEPENDENT of both
        # FANOPS_CREATIVE_VARIATION and FANOPS_VARIANT_LEARNING. DEFAULT OFF (opt-in), fail-open:
        # unset/empty/other -> today's behavior, no transferred prior injected.
        v = (os.getenv("FANOPS_VARIANT_TRANSFER") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_transfer_min_donors(self) -> int:
        # Transfer gate (stricter than v2's): a hook style transfers to a cold recipient only if it
        # is the v2-gated winner on at least this many DISTINCT other same-platform donor surfaces.
        # DEFAULT 2 — one surface's local win is not yet a platform-level signal. A non-int env
        # falls back to the default rather than crashing an autonomous run.
        try:
            return int(os.getenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "2"))
        except ValueError:
            return 2

    @property
    def variant_transfer_max_hooks(self) -> int:
        # Cap on how many borrowed styles a single caption request may carry, so even a popular
        # style-cluster cannot flood one caption (anti-homogenization). DEFAULT 2. A non-int env
        # falls back to the default.
        try:
            return int(os.getenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "2"))
        except ValueError:
            return 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS (all config tests, including the 3 new ones)

- [ ] **Step 5: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (transfer 1): FANOPS_VARIANT_TRANSFER config + donor/cap thresholds (default OFF)"
```

---

## Task 2: `variant_transfer.transferred_hooks` — the pure gated scorer

**Files:**
- Create: `src/fanops/variant_transfer.py`
- Test: `tests/test_variant_transfer.py`

This is the load-bearing unit. It reuses `variant_learning.best_hooks` (v2's gate) on every donor and on the recipient. It reads the `Accounts` registry for sibling surfaces and `persona`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_variant_transfer.py`:

```python
"""Cross-account/cross-surface transfer scorer (the v2 follow-up). transferred_hooks returns a
SAME-PLATFORM hook style proven on >= TRANSFER_MIN_DONORS distinct OTHER surfaces, as a weak prior
for a COLD recipient (one with no own gated winner). Pure/read-only/deterministic; reuses v2's
best_hooks gate on every donor. The whole anti-homogenization + stricter-gate argument lives here."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.accounts import Account, Accounts, AccountStatus
from fanops.variant_transfer import transferred_hooks


def _accounts(cfg, specs):
    """specs: list of (handle, [platforms], persona). All active so surfaces() yields them."""
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@") or h, platforms=plats,
                          status=AccountStatus.active, persona=persona)
                  for (h, plats, persona) in specs]
    return a


def _win_surface(led, account, platform, hook="WIN", *, n=3, win=90.0, lose=10.0, idprefix=""):
    """Seed `account/platform` with a comparative gated winner `hook` (n WIN posts vs n LOSE posts).
    Mirrors the v2 best_hooks gate: >= MIN_POSTS (3) and a gap (80) well over MIN_GAP (10)."""
    pid = idprefix or f"{account}_{platform.value}_"
    rows = [(hook, win)] * n + [("LOSE", lose)] * n
    for i, (h, lift) in enumerate(rows):
        led.add_post(Post(id=f"{pid}{i}", parent_id="clip_1", account=account, account_id="x",
                          platform=platform, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_{pid}{i}", variant_hook=h, metrics={"lift_score": lift}))


def test_recipient_with_own_winner_gets_nothing(tmp_path, monkeypatch):
    # own-wins rule: a surface that already has its own gated winner borrows nothing.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # donor 1
    _win_surface(led, "@b", Platform.instagram, "STYLE")     # donor 2
    _win_surface(led, "@c", Platform.instagram, "OWN")       # recipient HAS its own winner
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_single_donor_below_min_donors_returns_empty(tmp_path):
    # one donor wins STYLE but TRANSFER_MIN_DONORS default is 2 -> nothing transfers.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # only ONE donor
    # @c is cold (no posts) -> recipient. Only 1 donor won STYLE < 2 -> [].
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_two_donors_same_style_transfers(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")
    _win_surface(led, "@b", Platform.instagram, "STYLE")     # 2 distinct donors won STYLE
    # @c cold -> receives STYLE.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == ["STYLE"]


def test_other_platform_donor_does_not_contribute(tmp_path):
    # same-platform HARD gate: a tiktok winner must not inform an instagram recipient.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.tiktok], "hype"),
                            ("@b", [Platform.tiktok], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.tiktok, "STYLE")
    _win_surface(led, "@b", Platform.tiktok, "STYLE")        # both donors are TIKTOK
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_donor_below_v2_gate_contributes_nothing(tmp_path):
    # a donor whose surface fails v2's own gate (lone variant, no comparative runner-up) is not a
    # winner -> best_hooks returns [] for it -> it cannot seed transfer.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    # @a and @b each have ONLY a single "STYLE" variant (no runner-up) -> best_hooks -> [].
    for acct in ("@a", "@b"):
        for i in range(3):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id="x",
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook="STYLE",
                              metrics={"lift_score": 90.0}))
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_cap_limits_returned_styles(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # two distinct winning styles, each on 2 donors -> both qualify, but cap=1 -> only one returned.
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@d", [Platform.instagram], "hype"),
                            ("@e", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "ALPHA")
    _win_surface(led, "@b", Platform.instagram, "ALPHA")
    _win_surface(led, "@d", Platform.instagram, "BETA")
    _win_surface(led, "@e", Platform.instagram, "BETA")
    out = transferred_hooks(led, cfg, accts, "@c", Platform.instagram)
    assert len(out) == 1


def test_persona_ranking_is_deterministic_and_prefers_overlap(tmp_path, monkeypatch):
    # When more styles qualify than the cap, prefer donors whose persona token-overlaps the
    # recipient's. ALPHA donors share the recipient's "hype cinematic" words; BETA donors don't.
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype cinematic edits"),
                            ("@b", [Platform.instagram], "hype cinematic energy"),
                            ("@d", [Platform.instagram], "calm lyric reading"),
                            ("@e", [Platform.instagram], "calm lyric reading"),
                            ("@c", [Platform.instagram], "hype cinematic")])   # recipient
    _win_surface(led, "@a", Platform.instagram, "ALPHA")
    _win_surface(led, "@b", Platform.instagram, "ALPHA")
    _win_surface(led, "@d", Platform.instagram, "BETA")
    _win_surface(led, "@e", Platform.instagram, "BETA")
    out = transferred_hooks(led, cfg, accts, "@c", Platform.instagram)
    assert out == ["ALPHA"]                                  # persona-closer style wins the single slot
    # determinism: identical inputs -> identical output.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == out


def test_no_accounts_or_empty_ledger_returns_empty(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@c", [Platform.instagram], "hype")])
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []   # cold + no donors


def test_recipient_excluded_from_its_own_donor_pool(tmp_path):
    # The recipient surface must never count itself as a donor. @c has a (losing-runner-up) winner
    # of its own -> own-wins short-circuit returns [] anyway; this asserts no self-donation path.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # 1 donor
    _win_surface(led, "@c", Platform.instagram, "STYLE")     # @c also "won" STYLE itself
    # @c has its own winner -> own-wins rule returns []; STYLE is NOT double-counted via @c.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_variant_transfer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.variant_transfer'`

- [ ] **Step 3: Write the module**

Create `src/fanops/variant_transfer.py`:

```python
# src/fanops/variant_transfer.py
"""Cross-account / cross-surface learning transfer (the v2 follow-up). transferred_hooks() proposes
a SAME-PLATFORM hook STYLE proven on multiple OTHER surfaces as a weak prior for a COLD recipient
surface (one with no trustworthy winner of its own). It reuses variant_learning.best_hooks (v2's
gate) on every donor and on the recipient — never re-implementing or loosening it — and adds a
STRICTER cross-donor gate on top. Pure, read-only, deterministic (no random/hash/wall-clock).

SAFETY (C1): like variant_learning, this module must NEVER be imported/called by the amplify/
delete-cascade path (track.py/pipeline.py/adjust.py/ledger.py). It biases the caption REQUEST only;
the amplify path stays blind to it (enforced by the isolation tests in tests/test_variant_learning.py)."""
from __future__ import annotations
from fanops.models import Platform
from fanops.variant_learning import best_hooks


def _persona_tokens(persona: str | None) -> set[str]:
    """Lowercased word-set for deterministic persona overlap. None/empty -> empty set."""
    return set((persona or "").lower().split())


def transferred_hooks(led, cfg, accounts, account: str, platform: Platform) -> list[str]:
    """Borrowed hook STYLE(s) for a COLD (account, platform) recipient, or [] when transfer should
    not fire. Rules (all from the spec, in order):
      0. accounts is None  -> [] (no sibling registry -> nothing to borrow; keeps the new caption
         signature backward-compatible).
      1. own-wins: if best_hooks(led,cfg,account,platform) is non-empty, the recipient already has
         its OWN trustworthy winner -> [] (transfer only fills the cold-start gap; never overrides
         a surface's own proven style — the anti-homogenization core).
      2. donors: every OTHER active surface on the SAME platform (recipient excluded). Each donor's
         winner comes from best_hooks (v2 gate reused verbatim). Tally, per winning hook, the SET of
         distinct donor handles that won it.
      3. cross-donor gate (stricter than v2): keep only hooks won on >= cfg.variant_transfer_min_donors
         distinct donor surfaces.
      4. defensive dedupe: drop any kept hook the recipient itself already won (it can't, given rule
         1 returned [] for it, but this stays correct if rule 1 ever changes).
      5. persona rank: order survivors by (persona token-overlap with the recipient DESC, donor
         count DESC, hook string ASC) — fully deterministic — then cap at cfg.variant_transfer_max_hooks.
    """
    if accounts is None:
        return []
    # rule 1 — own winner wins.
    if best_hooks(led, cfg, account, platform):
        return []

    recipient_persona = None
    donor_personas: dict[str, set[str]] = {}        # handle -> persona tokens
    donor_handles: list[str] = []
    for acct in accounts.active():
        if platform not in acct.platforms:
            continue                                # rule 2 — SAME platform only
        if acct.handle == account:
            recipient_persona = _persona_tokens(acct.persona)
            continue                                # rule 2 — recipient is not its own donor
        donor_handles.append(acct.handle)
        donor_personas[acct.handle] = _persona_tokens(acct.persona)
    if recipient_persona is None:
        recipient_persona = set()

    # rule 2/3 — per winning hook, the set of distinct donor surfaces that won it.
    winners_by_hook: dict[str, set[str]] = {}
    for handle in donor_handles:
        for hook in best_hooks(led, cfg, handle, platform):     # v2 gate on each donor
            winners_by_hook.setdefault(hook, set()).add(handle)

    min_donors = cfg.variant_transfer_min_donors
    qualified = {h: donors for h, donors in winners_by_hook.items() if len(donors) >= min_donors}
    if not qualified:
        return []

    # rule 5 — deterministic persona-aware ranking. For each qualifying hook, its persona score is
    # the BEST token-overlap among the donors that won it (a hook is "close" if any close donor won
    # it). Ties broken by donor count desc, then hook string asc.
    def _score(item: tuple[str, set[str]]) -> tuple[int, int, str]:
        hook, donors = item
        best_overlap = max((len(recipient_persona & donor_personas[d]) for d in donors), default=0)
        return (-best_overlap, -len(donors), hook)

    ordered = sorted(qualified.items(), key=_score)
    max_hooks = cfg.variant_transfer_max_hooks
    return [hook for hook, _ in ordered[:max_hooks]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_variant_transfer.py -q`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add src/fanops/variant_transfer.py tests/test_variant_transfer.py
git commit -m "feat (transfer 2): variant_transfer.transferred_hooks — gated same-platform weak prior (pure, deterministic)"
```

---

## Task 3: `caption_prompt` renders the transferred block BELOW the own block

**Files:**
- Modify: `src/fanops/prompts.py` (`caption_prompt`, the `learned_block` region)
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prompts.py` (import `caption_prompt` if not already imported at top):

```python
def test_caption_prompt_renders_transferred_block_below_own():
    from fanops.prompts import caption_prompt
    payload = {"surfaces": [{"surface": "@c/instagram", "platform": "instagram"}],
               "language": "en", "guidance": "", "transcript_excerpt": "x",
               "learned_hooks": ["OWN"], "learned_hooks_transferred": ["BORROWED"]}
    prompt = caption_prompt(payload)
    assert "OWN" in prompt and "BORROWED" in prompt
    # the OWN (own-surface) block must appear ABOVE the borrowed (cross-surface) block.
    assert prompt.index("OWN") < prompt.index("BORROWED")
    # the borrowed block is labelled as a lighter, cross-surface nudge and still says don't copy.
    assert "elsewhere" in prompt.lower()
    assert prompt.lower().count("verbatim") >= 1


def test_caption_prompt_transferred_only_still_says_not_verbatim():
    from fanops.prompts import caption_prompt
    payload = {"surfaces": [{"surface": "@c/instagram", "platform": "instagram"}],
               "language": "en", "guidance": "", "transcript_excerpt": "x",
               "learned_hooks_transferred": ["BORROWED"]}     # cold recipient: only borrowed
    prompt = caption_prompt(payload)
    assert "BORROWED" in prompt
    assert "verbatim" in prompt.lower()


def test_caption_prompt_no_transferred_key_is_byte_identical():
    from fanops.prompts import caption_prompt
    base = {"surfaces": [{"surface": "@c/instagram", "platform": "instagram"}],
            "language": "en", "guidance": "g", "transcript_excerpt": "x"}
    # absent transferred key -> identical to a payload that never had it (no stray block).
    assert caption_prompt(dict(base)) == caption_prompt(dict(base))
    assert "elsewhere" not in caption_prompt(base).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompts.py::test_caption_prompt_renders_transferred_block_below_own -v`
Expected: FAIL — `BORROWED` not in prompt (the key is not rendered yet)

- [ ] **Step 3: Render the transferred block**

In `src/fanops/prompts.py`, in `caption_prompt`, find the existing v2 block:

```python
    learned = payload.get("learned_hooks")
    learned_block = (
        "  - What worked recently for these accounts — lean toward this STYLE (tone, length, "
        "angle), do NOT copy verbatim: "
        f"{json.dumps(learned, ensure_ascii=False)}\n"
        if learned else ""
    )
```

Immediately AFTER that assignment, add the transferred block (rendered after the own block in the return string):

```python
    # Cross-surface transfer (the v2 follow-up): a hook STYLE proven on OTHER same-platform surfaces,
    # offered to a COLD recipient as a LIGHTER nudge than its own proven style above. Separate key
    # (learned_hooks_transferred) so own-signal always reads as primary. Absent -> no block (prompt
    # stays byte-identical to v2).
    transferred = payload.get("learned_hooks_transferred")
    transferred_block = (
        "  - Also working elsewhere on this platform (a LIGHTER nudge than your own style above, "
        "if any) — lean toward this STYLE, do NOT copy verbatim: "
        f"{json.dumps(transferred, ensure_ascii=False)}\n"
        if transferred else ""
    )
```

Then in the returned f-string, find `f"{learned_block}"` and change it to render both, own first:

```python
        f"{learned_block}"
        f"{transferred_block}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prompts.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add src/fanops/prompts.py tests/test_prompts.py
git commit -m "feat (transfer 3): caption_prompt renders the cross-surface block below the own-surface block"
```

---

## Task 4: `request_captions` injects `learned_hooks_transferred` (fail-open, optional accounts)

**Files:**
- Modify: `src/fanops/caption.py` (import, new `_transferred_hooks` helper, `request_captions` signature + payload)
- Modify: `src/fanops/pipeline.py:84` (pass `accounts=accts`)
- Test: `tests/test_caption.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_caption.py` (after the v2 fail-open test, ~line 277). Note the helper `_seed_variant_posts_for_at_a` and `_clip` already exist in this file:

```python
# --- transfer: request_captions injects the cross-surface prior for a COLD recipient ----------
from fanops.accounts import Account, Accounts, AccountStatus

def _transfer_accounts(cfg, handles_personas, platform=Platform.instagram):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@") or h, platforms=[platform],
                          status=AccountStatus.active, persona=persona)
                  for (h, persona) in handles_personas]
    return a

def _win_surface_for(led, account, platform, hook, *, n=3):
    rows = [(hook, 90.0)] * n + [("LOSE", 10.0)] * n
    for i, (h, lift) in enumerate(rows):
        led.add_post(Post(id=f"{account}_{platform.value}_{i}", parent_id="clip_1", account=account,
                          account_id="x", platform=platform, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_{account}_{i}", variant_hook=h,
                          metrics={"lift_score": lift}))

def test_request_captions_injects_transferred_prior_for_cold_surface(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")   # 2 donors -> STYLE qualifies
    # request captions for the COLD recipient @c.
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload["learned_hooks_transferred"] == ["STYLE"]
    assert "learned_hooks" not in payload                      # @c has no OWN winner

def test_request_captions_no_transfer_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_VARIANT_TRANSFER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload          # OFF -> byte-identical to today

def test_request_captions_no_accounts_means_no_transfer(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)
    # no accounts arg -> backward-compatible default None -> transfer inert (no key).
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload

def test_request_captions_own_winner_takes_precedence_over_transfer(monkeypatch, tmp_path):
    # The recipient has its OWN winner -> it gets learned_hooks (v2) and NO transferred prior
    # (own-wins rule, the anti-homogenization guarantee proven through the request payload).
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    _win_surface_for(led, "@c", Platform.instagram, "OWN")     # @c has its OWN winner
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload["learned_hooks"] == ["OWN"]                 # own signal present
    assert "learned_hooks_transferred" not in payload          # borrowed signal suppressed

def test_request_captions_failopen_on_transfer_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    monkeypatch.setattr("fanops.caption.transferred_hooks",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)  # no raise
    p = request_path(cfg, "captions", "clip_1")
    assert p.exists()
    payload = json.loads(p.read_text())
    assert "learned_hooks_transferred" not in payload          # error -> no prior
    assert led.clips["clip_1"].state is ClipState.captions_requested
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_caption.py::test_request_captions_injects_transferred_prior_for_cold_surface -v`
Expected: FAIL — `TypeError: request_captions() got an unexpected keyword argument 'accounts'`

- [ ] **Step 3: Wire `request_captions`**

In `src/fanops/caption.py`:

(a) After the existing `from fanops.variant_learning import best_hooks` import (line 18), add:

```python
# Cross-surface transfer (the v2 follow-up): SAME safe side as best_hooks — imported here ONLY
# (the amplify/delete path stays blind to it; the isolation tests enforce it). Bound at module scope
# so request_captions' fail-open path is unit-patchable (tests monkeypatch fanops.caption.transferred_hooks).
from fanops.variant_transfer import transferred_hooks
```

(b) After the existing `_learned_hooks(...)` helper (ends ~line 98), add a parallel fail-open helper:

```python
def _transferred_hooks(led: Ledger, cfg: Config, accounts,
                       surfaces: list[tuple[str, Platform]]) -> list[str]:
    """Cross-surface transfer — the cold-start prior. When FANOPS_VARIANT_TRANSFER is on, ask the
    gated transfer scorer for each surface's borrowed STYLE(s) and return the de-duplicated union
    (insertion order preserved -> deterministic). Gated OFF by default, or no accounts registry -> [].
    FAIL-OPEN: any error is logged once and yields [] so a transfer failure can never block a caption."""
    if not cfg.variant_transfer or accounts is None:
        return []
    try:
        out: list[str] = []
        seen: set[str] = set()
        for acct, plat in surfaces:
            for h in transferred_hooks(led, cfg, accounts, acct, plat):
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out
    except Exception:
        logger.warning("variant transfer prior skipped (fail-open)", exc_info=True)
        return []
```

(c) Change the `request_captions` signature to accept an optional `accounts`, and add the key to the payload. Replace the existing function head + payload (lines 100-116):

```python
def request_captions(led: Ledger, cfg: Config, clip_id: str,
                     surfaces: list[tuple[str, Platform]], accounts=None) -> Ledger:
    clip = led.clips[clip_id]
    moment = led.moments[clip.parent_id]
    src = led.sources.get(moment.parent_id)
    learned = _learned_hooks(led, cfg, surfaces)
    transferred = _transferred_hooks(led, cfg, accounts, surfaces)
    payload = {
        "clip_id": clip_id,
        "transcript_excerpt": moment.transcript_excerpt,
        "language": src.language if src else None,
        "guidance": _guidance(cfg),
        "surfaces": [{"surface": _surface_str(acct, plat), "platform": plat.value}
                     for acct, plat in surfaces],
        # variation v2: only present when a surface crossed the trust gate -> OFF/below-gate keeps
        # the payload byte-identical to pre-v2 (caption_prompt renders this block when present).
        **({"learned_hooks": learned} if learned else {}),
        # transfer (v2 follow-up): a borrowed cross-surface STYLE for a COLD recipient — separate
        # key so own-signal reads as primary; absent unless the flag is on AND a donor qualifies.
        **({"learned_hooks_transferred": transferred} if transferred else {}),
    }
    write_request(cfg, kind="captions", key=clip_id, payload=payload)
    led.set_clip_state(clip_id, ClipState.captions_requested)
    return led
```

- [ ] **Step 4: Update the production caller**

In `src/fanops/pipeline.py`, change the call at line 84-85 from:

```python
                        led = request_captions(led, cfg, clip.id,
                                               [(s.account, s.platform) for s in accts.surfaces()])
```

to (pass the already-loaded registry):

```python
                        led = request_captions(led, cfg, clip.id,
                                               [(s.account, s.platform) for s in accts.surfaces()],
                                               accounts=accts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_caption.py -q`
Expected: PASS (the 5 new transfer tests + all existing v2/caption tests)

- [ ] **Step 6: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add src/fanops/caption.py src/fanops/pipeline.py tests/test_caption.py
git commit -m "feat (transfer 4): request_captions injects gated learned_hooks_transferred (fail-open, own-wins precedence)"
```

---

## Task 5: C1 isolation — extend the data-flow + positive-lock tests to cover `variant_transfer`

**Files:**
- Modify: `tests/test_variant_learning.py` (the isolation tests at ~lines 102, 134-153)

This task adds NO source — it tightens the safety net so the transfer module is held to the SAME C1 wall as `best_hooks`. **Critical interaction:** `variant_transfer.py` legitimately CALLS `best_hooks(` — so the existing `test_best_hooks_called_only_on_safe_read_or_request_side` must add `variant_transfer.py` to its `allowed` set, or it will (correctly, but undesirably) flag the new safe caller.

- [ ] **Step 1: Write/extend the failing tests**

In `tests/test_variant_learning.py`:

(a) Extend the forbidden tuple (line 102) so the amplify-path data-flow check also forbids transfer symbols:

```python
_FORBIDDEN_IN_AMPLIFY = ("variant_key", "variant_hook", "best_hooks", "variant_learning",
                         "transferred_hooks", "variant_transfer")
```

(b) Update `test_best_hooks_called_only_on_safe_read_or_request_side` to allow the new safe caller. Change its `allowed` line (line 141) from `allowed = {"caption.py", "digest.py"}` to:

```python
    allowed = {"caption.py", "digest.py", "variant_transfer.py"}   # variant_transfer is a safe read-only caller
```

(c) Add a new positive-lock test mirroring the `best_hooks` one, for `transferred_hooks`:

```python
def test_transferred_hooks_called_only_on_safe_read_or_request_side():
    """Positive lock on the transfer scorer, mirroring the best_hooks lock. transferred_hooks may be
    called only from the SAFE surfaces (caption.py — the request side; digest.py — read-only gate
    reporting). It must NEVER be called from the C1 danger files (adjust.py / track.py / pipeline.py
    / ledger.py). If a future edit calls it from the amplify/delete path, this names the file."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    allowed = {"caption.py", "digest.py"}
    danger = {"adjust.py", "track.py", "pipeline.py", "ledger.py"}
    callers = set()
    for py in root.rglob("*.py"):
        if py.name == "variant_transfer.py":        # the definition site
            continue
        if "transferred_hooks(" in py.read_text():  # an actual call (not just the import line)
            callers.add(py.name)
    leaked_into_danger = sorted(callers & danger)
    assert not leaked_into_danger, \
        f"C1 violation: transferred_hooks called from the amplify/delete path: {leaked_into_danger}"
    assert callers <= allowed, \
        f"transferred_hooks called from an unexpected file (review for safety): {sorted(callers - allowed)}"
```

- [ ] **Step 2: Run the isolation tests to verify the NEW assertions pass and nothing regressed**

Run: `python -m pytest tests/test_variant_learning.py -q`
Expected: PASS. (If `test_best_hooks_called_only_on_safe_read_or_request_side` fails complaining `variant_transfer.py` is unexpected, the `allowed` update in step 1b was missed.)

- [ ] **Step 3: (No implementation needed — these are guard tests over existing source.)**

Confirm the data-flow test still passes — `adjust.py`/`ledger.py` reference none of the (now-extended) forbidden symbols. If it fails, a real C1 leak exists and must be fixed before proceeding.

- [ ] **Step 4: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add tests/test_variant_learning.py
git commit -m "test (transfer 5): extend C1 isolation (data-flow + positive-lock) to cover variant_transfer"
```

---

## Task 6: Digest annotation + real on-disk integration test

**Files:**
- Modify: `src/fanops/digest.py` (`_gate_state` → also report "borrowing platform signal")
- Modify: `tests/test_digest.py` (assert the new label)
- Create: `tests/integration/test_variant_transfer_real.py`

- [ ] **Step 1: Write the failing digest test**

Add to `tests/test_digest.py`:

```python
def test_digest_marks_cold_surface_borrowing(monkeypatch, tmp_path):
    # A cold recipient receiving a transferred prior is annotated "borrowing platform signal" — the
    # operator sees transfer is active for that surface (distinct from its own "learning ACTIVE").
    import json
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState, Clip, Moment, Source, MomentState, ClipState
    from fanops.accounts import Account, Accounts, AccountStatus
    from fanops.digest import render_digest
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c.mp4", state=ClipState.rendered))
    # @a,@b win STYLE (2 donors); @c is a cold recipient with a single analyzed post (so it APPEARS
    # in the "Lift by variant" section) but no own winner.
    def win(acct, hook):
        rows = [(hook, 90.0)] * 3 + [("LOSE", 10.0)] * 3
        for i, (h, lift) in enumerate(rows):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id="x",
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook=h, metrics={"lift_score": lift}))
    win("@a", "STYLE"); win("@b", "STYLE")
    led.add_post(Post(id="c0", parent_id="clip_1", account="@c", account_id="x",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_c0", variant_hook="COLD", metrics={"lift_score": 50.0}))
    accts = Accounts(cfg)
    accts.accounts = [Account(handle=h, account_id="x", platforms=[Platform.instagram],
                              status=AccountStatus.active, persona="hype") for h in ("@a", "@b", "@c")]
    out = render_digest(led, cfg, accounts=accts)
    assert "borrowing platform signal" in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_digest.py::test_digest_marks_cold_surface_borrowing -v`
Expected: FAIL — `render_digest()` has no `accounts` param (TypeError) OR the label is absent.

- [ ] **Step 3: Extend the digest**

In `src/fanops/digest.py`:

(a) After the `from fanops.variant_learning import best_hooks` import (line 15), add:

```python
# Transfer (v2 follow-up): the SAME read-only safe side. Used to annotate a COLD surface that is
# receiving a borrowed cross-surface prior. Fail-open like best_hooks; does NOT touch the C1 path.
from fanops.variant_transfer import transferred_hooks
```

(b) Change `_gate_state` to optionally consider transfer. Replace the function (lines 23-42) with a version that takes `accounts`:

```python
def _gate_state(led: Ledger, cfg: Config, account: str, platform: Platform,
                _cache: dict[tuple[str, str], str] | None = None, accounts=None) -> str:
    """The learning-loop state for one (account, platform) surface, for the "Lift by variant" digest
    section. "learning ACTIVE" iff the surface has its OWN gated winner (variant_learning.best_hooks
    — the SAME scorer request_captions biases on). Else, if transfer is on and the surface would
    receive a borrowed cross-surface prior, "borrowing platform signal". Otherwise "gathering data"
    (the loop is still open here). FAIL-OPEN: any error degrades to "gathering data" (the safe
    default). Memoised per render via the optional _cache."""
    key = (account, platform.value)
    if _cache is not None and key in _cache:
        return _cache[key]
    try:
        if best_hooks(led, cfg, account, platform):
            state = "learning ACTIVE"
        elif cfg.variant_transfer and accounts is not None and \
                transferred_hooks(led, cfg, accounts, account, platform):
            state = "borrowing platform signal"
        else:
            state = "gathering data"
    except Exception:
        logger.warning("variant gate-state degraded to 'gathering data' (fail-open)", exc_info=True)
        state = "gathering data"
    if _cache is not None:
        _cache[key] = state
    return state
```

(c) Update `render_digest`'s signature and the one `_gate_state(...)` call inside it. Find the signature `def render_digest(led: Ledger, cfg: Config) -> str:` (line 44) and change it to:

```python
def render_digest(led: Ledger, cfg: Config, accounts=None) -> str:
```

Then find the `_gate_state(led, cfg, p.account, p.platform, gate_cache)` call (~line 100) and change it to pass `accounts`:

```python
                 f"— {_gate_state(led, cfg, p.account, p.platform, gate_cache, accounts)}"
```

- [ ] **Step 4: Have `write_digest` self-load `Accounts` (no caller plumbing)**

`render_digest` has exactly ONE production caller — `write_digest` at `digest.py:120-122` — which in turn has 7 call sites across `pipeline.py`/`cli.py`, several with NO `Accounts` in scope (e.g. CLI recovery verbs). Rather than thread an `accounts` argument through all 7 (invasive, and pointless for a cosmetic label), `write_digest` loads the registry itself — cheap, fail-open, and consistent with how the digest already self-serves its data. The explicit `render_digest(..., accounts=...)` param stays for the unit test and any caller that already has a registry.

Replace `write_digest` (lines 120-122):

```python
def write_digest(led: Ledger, cfg: Config) -> None:
    cfg.digest_path.parent.mkdir(parents=True, exist_ok=True)
    # Self-load the account registry so the "borrowing platform signal" annotation works for every
    # write_digest caller (pipeline + all CLI verbs) WITHOUT threading accounts through 7 call sites.
    # Only needed when transfer is on; FAIL-OPEN — a missing/corrupt registry must never blank or
    # crash the digest, so degrade to None (no borrowing label, exactly v2 behavior).
    accounts = None
    if cfg.variant_transfer:
        try:
            from fanops.accounts import Accounts
            accounts = Accounts.load(cfg)
        except Exception:
            logger.warning("digest accounts load skipped (fail-open)", exc_info=True)
            accounts = None
    cfg.digest_path.write_text(render_digest(led, cfg, accounts=accounts))
```

(The 7 `write_digest(Ledger.load(cfg), cfg)` callers in `pipeline.py`/`cli.py` are UNCHANGED — they get the borrowing label automatically when the flag is on.)

- [ ] **Step 5: Run the digest tests**

Run: `python -m pytest tests/test_digest.py -q`
Expected: PASS (new test + all existing digest tests, including the existing gate-state agreement test)

- [ ] **Step 6: Write the real on-disk integration test**

Create `tests/integration/test_variant_transfer_real.py`:

```python
"""Cross-surface transfer — proven END-TO-END ON DISK (the project's Integrate bar). A real
ledger.json where a hook STYLE clearly out-lifts on TWO distinct same-platform donor surfaces, a
THIRD cold recipient surface with no own winner, reloaded FROM DISK, then real request_captions with
transfer ON — asserting the borrowed STYLE reached the on-disk caption request under
learned_hooks_transferred (NOT learned_hooks), and the committed caption_prompt rendered it. A
companion case raises TRANSFER_MIN_DONORS above the donor count and asserts NO transfer (the
stricter gate, proven on disk)."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, MomentState, ClipState, PostState, Platform
from fanops.accounts import Account, Accounts, AccountStatus
from fanops.caption import request_captions
from fanops.prompts import caption_prompt
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _accounts(cfg):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@"), platforms=[Platform.instagram],
                          status=AccountStatus.active, persona="hype cinematic")
                  for h in ("@a", "@b", "@c")]
    return a


def _seed_on_disk(cfg: Config) -> None:
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    # @a and @b: STYLE (90 x3) vs LOSE (10 x3) -> each a trustworthy gated winner of STYLE.
    for acct in ("@a", "@b"):
        rows = [("STYLE", 90.0)] * 3 + [("LOSE", 10.0)] * 3
        for i, (hook, lift) in enumerate(rows):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id=acct.strip("@"),
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook=hook, metrics={"lift_score": lift}))
    # @c: COLD recipient — no analyzed variant posts of its own.
    led.save()


def test_transferred_prior_reaches_caption_request_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk(cfg)
    led = Ledger.load(cfg)                                   # round-trip from disk
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=_accounts(cfg))
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload.get("learned_hooks_transferred") == ["STYLE"], \
        f"the borrowed style must reach the on-disk request; got {payload.get('learned_hooks_transferred')!r}"
    assert "learned_hooks" not in payload                    # @c is cold — no OWN winner
    prompt = caption_prompt(payload)
    assert "STYLE" in prompt and "verbatim" in prompt.lower()


def test_stricter_min_donors_blocks_transfer_on_disk(tmp_path, monkeypatch):
    # Raise the gate above the donor count: 2 donors, MIN_DONORS=3 -> NO transfer reaches disk.
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "3")
    cfg = Config(root=tmp_path)
    _seed_on_disk(cfg)
    led = Ledger.load(cfg)
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=_accounts(cfg))
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload        # stricter gate held
    assert "STYLE" not in json.dumps(payload)
```

- [ ] **Step 7: Run the integration test**

Run: `python -m pytest tests/integration/test_variant_transfer_real.py -q`
Expected: PASS (both cases)

- [ ] **Step 8: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add src/fanops/digest.py tests/test_digest.py tests/integration/test_variant_transfer_real.py
git commit -m "feat (transfer 6): digest 'borrowing platform signal' annotation + real on-disk transfer integration"
```

---

## Task 7: Docs sync (RUNTIME.md env table + backlog (j))

**Files:**
- Modify: `MohFlow-FanOps/00_control/RUNTIME.md` (env-var table ~line 47; backlog (j) ~line 703)

- [ ] **Step 1: Add the env-var row**

In `MohFlow-FanOps/00_control/RUNTIME.md`, after the `FANOPS_VARIANT_LEARNING` row (line 47), add:

```markdown
| `FANOPS_VARIANT_TRANSFER` | `1`/`true`/`yes`/`on` (default **OFF**) \| unset/`0`/`false`/… | Cross-account / cross-surface learning **transfer** (v2 follow-up, backlog j). When ON, `request_captions` may bias a **COLD** recipient surface (no trustworthy winner of its own yet) toward a hook STYLE proven on **other same-platform** surfaces — fed in as `learned_hooks_transferred` (a SEPARATE, weaker payload key than v2's `learned_hooks`; `caption_prompt` renders it BELOW the own-surface block as a lighter "working elsewhere on this platform — don't copy verbatim" nudge). Gate is STRICTER than v2's: a style must be the v2-gated winner on `≥ FANOPS_VARIANT_TRANSFER_MIN_DONORS` (default **2**) distinct donor surfaces, capped at `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` (default **2**). INDEPENDENT of `FANOPS_CREATIVE_VARIATION`/`FANOPS_VARIANT_LEARNING`. **DEFAULT OFF** — opt-in. **Fail-open**: flag off / no qualifying donor / no accounts registry / any error / old ledger ⇒ no prior, today's behavior. **Anti-homogenization**: a surface with its OWN winner borrows nothing (own-wins); transfer is STYLE not verbatim; persona-ranked (deterministic). Touches **none** of the amplify/`_delete_moment_cascade` path (C1) — enforced by the isolation tests. The digest's "Lift by variant" section shows a cold surface as "borrowing platform signal". |
```

- [ ] **Step 2: Update backlog (j)**

In the backlog (j) entry (~line 703), append after the v2 paragraph:

```markdown
  **v3 — cross-account/cross-surface transfer** (`FANOPS_VARIANT_TRANSFER=1`, independent flag,
  default OFF): a hook STYLE proven (v2-gated) on `≥ FANOPS_VARIANT_TRANSFER_MIN_DONORS` distinct
  same-platform surfaces is offered to a COLD recipient surface (no own winner) as a demoted,
  persona-ranked weak prior (`learned_hooks_transferred`, rendered below the own-surface block).
  Same-platform is a HARD gate; cross-platform is out of scope. Anti-homogenization: own-winner-wins,
  style-not-verbatim, MAX_HOOKS cap. Stays on the caption-request side; the amplify/C1 path remains
  blind (isolation tests extended to `variant_transfer`). Still out of scope: auto-amplify, cross-PLATFORM
  transfer, bandit/decay scheduling.
```

- [ ] **Step 3: Verify docs reference real symbols**

Run: `grep -rn "FANOPS_VARIANT_TRANSFER\|variant_transfer\|learned_hooks_transferred\|transferred_hooks" src/fanops/config.py src/fanops/caption.py src/fanops/variant_transfer.py src/fanops/prompts.py`
Expected: every symbol named in the docs exists in source (no drift).

- [ ] **Step 4: Full suite + lint, then commit**

```bash
python -m pytest -q && ruff check src/
git add "MohFlow-FanOps/00_control/RUNTIME.md"
git commit -m "docs (transfer 7): RUNTIME env table + backlog (j) — cross-account transfer (v3)"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint clean: `ruff check src/`
- [ ] Determinism spot-check: the transfer scorer uses no `random`/`hash`/wall-clock — `grep -nE "random|hash\(|datetime\.now|time\.time" src/fanops/variant_transfer.py` returns nothing.
- [ ] C1 wall intact: `python -m pytest tests/test_variant_learning.py -q` (both data-flow + positive-lock tests pass for `best_hooks` AND `transferred_hooks`).
- [ ] Default-OFF proven: with no `FANOPS_VARIANT_TRANSFER` set, `learned_hooks_transferred` never appears in any caption payload (covered by `test_request_captions_no_transfer_when_flag_off`).
- [ ] Branch + PR: this work lands on a feature branch (not `main`) and goes up as a PR for human merge (repo convention — human owns merge).
