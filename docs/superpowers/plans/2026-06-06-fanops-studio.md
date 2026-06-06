# FanOps Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local, single-operator web "content cockpit" (`fanops studio`) over the FanOps ledger — three tabs (Review · Schedule · Lift) to watch rendered clips, tweak captions, reschedule/snooze the upcoming queue, and read per-variant lift — mutating the ledger ONLY through the existing lock-safe `Ledger.transaction` path, plus one default-off pipeline knob (`FANOPS_PUBLISH_LEAD_MINUTES`) that creates the editable window.

**Architecture:** One Flask + Jinja2 + HTMX app in a new package `src/fanops/studio/` (server-rendered partials, vendored HTMX, native `<video>`, no JS build step). Reads use lock-free `Ledger.load`; the two write actions (`reschedule_post`, `edit_caption`, plus the `snooze_clip` helper) each open one `Ledger.transaction` and do existence + `queued` + not-imminent guards inside the lock. The Flask import is LAZY (inside the CLI dispatch branch) so the core install stays Flask-free. The only core-code change is a constant lead-time offset on the deterministic schedule.

**Tech Stack:** Python 3.12, Flask 3 (new optional extra `[studio]`), Jinja2, HTMX (vendored), pydantic models, pytest + pytest-mock + pytest-timeout, ruff. Determinism via SHA1 content-addressed schedule (`crosspost.surface_time`).

**Spec:** `docs/superpowers/specs/2026-06-06-fanops-studio-design.md`

**Baseline:** `feat-studio-ui` @ `d6d2653` (spec commit). Suite **471 passed, 1 skipped** (`python -m pytest --co -q` → 471 collected). `ruff check src/` green. Work on `feat-studio-ui` (a feature branch — NOT main). Use the repo venv: `source .venv/bin/activate && python -m pytest ...` (if `.venv` is absent, create it: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'`). Every test command below assumes the venv is active.

**Verified-against-code notes (from the adversarial Understand pass — trust these over the spec's line numbers):**
- `config.py` ends at line 265 (`variant_transfer_max_hooks`). Int-knob pattern is `try: int(getenv) except ValueError: default`. Only `variant_ucb_c` guards negatives — so `publish_lead_minutes` MUST add an explicit `>= 0` guard (Task 1).
- `crosspost.surface_time(base, account, platform, date_str, index, *, clip_id="")` at `crosspost.py:35`; anchor computed at line 41; called inside `crosspost_clips` at `crosspost.py:100` as `surface_time(base, surf.account, surf.platform.value, date_str, i, clip_id=clip.id)`. Constants: `_STEP_MIN=40`, `_JITTER_MAX=30`, `_ANCHOR_SPAN=50`.
- `Ledger.transaction(cls, cfg, *, timeout=None)` is a `@classmethod @contextmanager` at `ledger.py:91`; `Ledger.load(cls, cfg)` at `ledger.py:70` (lock-free). Recovery-verb idiom (existence-check + mutate inside `with`): `cli.py:285` (resolve), `cli.py:298` (unhold).
- `timeutil.parse_iso(ts)` accepts a NAIVE string and returns naive; `timeutil.iso_z(dt)` calls `dt.astimezone(utc)` which silently treats naive as LOCAL time → `reschedule_post` must coerce naive→UTC via `.replace(tzinfo=timezone.utc)` BEFORE `iso_z`.
- `publish_due` (`post/run.py:28`) compares `parse_iso(post.scheduled_time) > cutoff` (aware) inside `try/except (ValueError, TypeError)`; a naive time raises `TypeError` and marks the post `failed`. This is the failure `reschedule_post` must prevent.
- Variant playback: `post.media_urls[0]` (a `file://…` path) is the per-account overlay that actually ships when `cfg.creative_variation` was ON at crosspost; else `media_urls` is `[]` and `led.clips[post.parent_id].path` (base clip) is the media. Codec invariant: base render `clip.py:45-47` (`-c:v libx264 -c:a aac -movflags +faststart`); overlay `overlay.py:209-210` (`-c:v libx264 -c:a copy -movflags +faststart`).
- `cfg.burn_subs` (default ON), `cfg.subtitle_font`, `cfg.clips` (the `03_clips` dir), `cfg.creative_variation`, `cfg.variant_amplify` all exist. `Accounts.load(cfg)` → `.accounts: list[Account]` (`handle`, `account_id`, `platforms`, `status`, `persona`), `.active()`, `.surfaces()`.
- `cli.py` registers subparsers in `main()` (`cli.py:126-144`, `sub.add_parser("intake")` at 143, `p_run` at 144) and routes in `_dispatch` (`cli.py:243-...`, `discover` lazy import at 325, `intake` at 334, `run` at 339). No Flask import at module top — keep it that way.
- `digest._gate_state(led, cfg, account, platform, _cache=None, accounts=None) -> str` (fail-open → `"gathering data"`) is reusable for the Lift loop-state column. `variant_amplify.amplify_candidates(led, cfg) -> list[dict]` returns `{source_id, winning_hook, post_id, evidence}` and never raises.

---

## File structure (decomposition lock)

```
src/fanops/config.py        # + publish_lead_minutes property (default 0; non-int/negative -> 0)
src/fanops/crosspost.py     # surface_time gains keyword-only lead_minutes:int=0; crosspost_clips passes cfg.publish_lead_minutes
src/fanops/cli.py           # + `studio` subparser + dispatch branch (LAZY `from fanops.studio.app import create_app`)
pyproject.toml              # + [project.optional-dependencies] studio = ["flask>=3.0"]
src/fanops/studio/
  __init__.py               # EMPTY (must NOT import app.py — keeps Flask out of `import fanops.studio`)
  views.py                  # pure read-model builders (no HTTP, no Flask): dataclasses + _imminent + review_buckets / schedule_rows / lift_rows
  actions.py                # lock-safe mutations (no Flask): ActionResult + reschedule_post / edit_caption / snooze_clip
  app.py                    # create_app(cfg) -> Flask; tab routes + /media/<post_id> + /clips/<clip_id> + POST mutation routes
  templates/base.html
  templates/review.html
  templates/_card.html
  templates/schedule.html
  templates/lift.html
  templates/_result.html    # mutation-result fragment (ok/error + new value)
  static/studio.css
  static/htmx.min.js        # vendored, pinned
tests/test_config.py            # (extend) publish_lead_minutes: default 0, non-int -> 0, negative -> 0
tests/test_crosspost.py         # (extend) lead-time: lead=0 byte-identical; lead>0 constant shift; monotonicity preserved
tests/test_studio_views.py      # read models: imminent flag, editable/recent/held buckets, variant media_url, schedule rows, empty + populated Lift
tests/test_studio_actions.py    # mutations + queued/not-imminent guards + naive-time normalization + single-lock reuse + snooze
tests/test_studio_app.py        # Flask test-client routes + /media variant resolution + 404 + mutation round-trip + flask-absent core-CLI guard
tests/integration/test_studio_real.py  # real ffmpeg render -> queue a post -> Review serves real H.264/AAC mp4 bytes (codec invariant)
```

**Locked read-model types (`views.py`) — names/fields are load-bearing; later tasks and templates reference them verbatim:**

```python
IMMINENT_THRESHOLD_MINUTES = 5
RECENT_WINDOW_HOURS = 24

@dataclass
class SurfacePost:
    post_id: str
    account: str
    platform: str            # Platform.value, e.g. "instagram"
    persona: Optional[str]
    caption: str
    hashtags: list[str]      # read-only display ("stored, not posted")
    scheduled_time: Optional[str]
    media_url: str           # "/media/<post_id>"
    state: str               # PostState.value
    imminent: bool
    editable: bool           # state == "queued" and not imminent

@dataclass
class ReviewCard:
    clip_id: str
    preview_url: str         # "/clips/<clip_id>"
    source_name: str
    moment_window: str       # "start–end" (en dash)
    reason: str
    language: Optional[str]
    subtitles_burned: bool   # cfg.burn_subs (render-time setting; honest approximation — Clip has no per-clip flag)
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str              # "editable" | "recent" | "held"

@dataclass
class ScheduleRow:
    post_id: str
    scheduled_time: Optional[str]
    account: str
    platform: str
    clip_id: str
    state: str
    imminent: bool
    editable: bool

@dataclass
class LiftRow:
    variant_hook: Optional[str]
    account: str
    platform: str
    lift_score: float
    loop_state: str
    amplify_state: Optional[str] = None

@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool          # mirrors cfg.variant_amplify
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]
```

**Locked action type (`actions.py`):**

```python
@dataclass
class ActionResult:
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None   # success payload, e.g. {"post_id":..., "scheduled_time":...} or {"clip_id":..., "count": n}
```

**Locked builder/action signatures (referenced across tasks):**

```python
# views.py
def _imminent(scheduled_time: Optional[str], now: datetime, threshold_min: int = IMMINENT_THRESHOLD_MINUTES) -> bool
def review_buckets(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime) -> list[ReviewCard]
def schedule_rows(led: Ledger, cfg: Config, *, now: datetime) -> list[ScheduleRow]
def lift_rows(led: Ledger, cfg: Config, accounts: Optional[Accounts] = None) -> LiftView

# actions.py
def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult
def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult
def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult
```

---

### Task 1: `Config.publish_lead_minutes` — the editable-window knob

**Files:**
- Modify: `src/fanops/config.py` (append a property after `variant_transfer_max_hooks`, currently ending at line 265)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — ADD
def test_publish_lead_minutes_default_zero(monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)
    assert Config().publish_lead_minutes == 0

def test_publish_lead_minutes_reads_env(monkeypatch):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "120")
    assert Config().publish_lead_minutes == 120

def test_publish_lead_minutes_non_int_falls_back_to_zero(monkeypatch):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "not-a-number")
    assert Config().publish_lead_minutes == 0

def test_publish_lead_minutes_negative_clamps_to_zero(monkeypatch):
    # A negative lead would shift the anchor BEFORE base and could invert the editable window;
    # unlike the other int knobs, this one MUST guard negatives (mirrors variant_ucb_c).
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "-30")
    assert Config().publish_lead_minutes == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -k publish_lead -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'publish_lead_minutes'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/config.py — append as the last property of class Config (after variant_transfer_max_hooks, ~line 265):

    @property
    def publish_lead_minutes(self) -> int:
        # The editorial window (spec §4): a CONSTANT offset added to every post's deterministic
        # scheduled_time at CROSSPOST time, so a freshly-queued post sits in `queued` for ~lead
        # minutes before publish_due ships it. DEFAULT 0 == today's exact behavior (every post due
        # immediately under a past base-time). A non-int OR negative env -> 0: unlike the other int
        # knobs, a negative lead would shift the anchor before `base` and corrupt the window, so it
        # is explicitly clamped (the variant_ucb_c precedent), not merely caught.
        try:
            v = int(os.getenv("FANOPS_PUBLISH_LEAD_MINUTES", "0"))
        except ValueError:
            return 0
        return v if v >= 0 else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -k publish_lead -v && python -m pytest -q`
Expected: 4 new PASS; full suite still green (475 passed, 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (studio 1): Config.publish_lead_minutes (default 0; non-int/negative -> 0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Lead-time offset in the deterministic schedule

**Files:**
- Modify: `src/fanops/crosspost.py` (`surface_time` signature + anchor at `crosspost.py:35-46`; call site at `crosspost.py:100`)
- Test: `tests/test_crosspost.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crosspost.py — ADD (datetime/timezone/parse_iso already importable; add if missing)
from datetime import timedelta
from fanops.timeutil import parse_iso

def test_surface_time_lead_zero_is_byte_identical_to_no_lead():
    # The default lead=0 must produce the EXACT same string as today (determinism regression guard).
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    for i in range(6):
        a = surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1")
        b = surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=0)
        assert a == b

def test_surface_time_lead_shifts_every_time_by_exactly_the_constant():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    lead = 120
    for i in range(6):
        t0 = parse_iso(surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1"))
        tl = parse_iso(surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=lead))
        assert tl - t0 == timedelta(minutes=lead)   # constant shift, identical per index

def test_surface_time_lead_preserves_monotonicity():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    times = [surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=200)
             for i in range(12)]
    assert times == sorted(times) and len(set(times)) == len(times)

def test_crosspost_clips_applies_publish_lead_minutes(tmp_path, mocker, monkeypatch):
    # End-to-end: crosspost_clips must read cfg.publish_lead_minutes and pass it through, so a
    # post's scheduled_time is shifted by exactly the lead vs the no-lead run.
    base_time = "2026-06-02T18:00:00Z"
    def _run(lead_env):
        cfg = Config(root=tmp_path / lead_env)   # isolated root per run
        _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432",
                              "platforms": ["instagram"], "status": "active"}])
        led = Ledger.load(cfg); _captioned(led, cfg, mocker)
        if lead_env:
            monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", lead_env)
        else:
            monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)
        led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time=base_time)
        ig = [p for p in led.posts.values() if p.platform.value == "instagram"][0]
        return parse_iso(ig.scheduled_time)
    t_no = _run("")
    t_lead = _run("90")
    assert t_lead - t_no == timedelta(minutes=90)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_crosspost.py -k "lead" -v`
Expected: FAIL — `surface_time() got an unexpected keyword argument 'lead_minutes'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/crosspost.py — replace surface_time signature + anchor (lines 35-46):
def surface_time(base: datetime, account: str, platform: str, date_str: str, index: int,
                 *, clip_id: str = "", lead_minutes: int = 0) -> str:
    seed = _seed(account, platform, date_str, clip_id)
    rng = random.Random(seed)                        # ONE stable stream per (surface,clip) — NOT
                                                     # reseeded per index (that made the step a
                                                     # fresh draw each call -> non-monotonic).
    # lead_minutes is a CONSTANT editorial offset (spec §4): it shifts every surface/index equally,
    # so the schedule stays content-addressed + byte-deterministic and the jitter<step monotonicity
    # proof is untouched (a constant translation preserves ordering). Default 0 == today's behavior.
    anchor = base + timedelta(minutes=lead_minutes + (seed % _ANCHOR_SPAN))
    # Draw the jitter sequence deterministically up to `index` so each index has its own nudge,
    # but the dominant term is the FIXED step -> strictly increasing in index.
    jitter = [rng.randint(0, _JITTER_MAX - 1) for _ in range(index + 1)][index]
    t = anchor + timedelta(minutes=index * _STEP_MIN + jitter)
    return iso_z(t)
```

```python
# src/fanops/crosspost.py — at the surface_time call inside crosspost_clips (line 100), add the lead:
            sched = surface_time(base, surf.account, surf.platform.value, date_str, i,
                                 clip_id=clip.id, lead_minutes=cfg.publish_lead_minutes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_crosspost.py -v && python -m pytest -q`
Expected: new lead tests PASS; ALL existing `test_crosspost.py` tests still pass (they call `surface_time` without `lead_minutes` → default 0 → byte-identical); full suite green (479 passed, 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/crosspost.py tests/test_crosspost.py
git commit -m "feat (studio 2): constant publish-lead offset on surface_time (determinism-safe; default 0 byte-identical)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `[studio]` optional dependency (Flask) + install it

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`, currently has `dev`, `transcribe`)

- [ ] **Step 1: Add the optional extra**

```toml
# pyproject.toml — under [project.optional-dependencies], add a new line after `transcribe = [...]`:
# Local web cockpit (spec §10). Flask is OPTIONAL: `pip install -e .` (core CLI) stays Flask-free;
# only `pip install -e '.[studio]'` pulls it. The CLI imports it LAZILY so a no-[studio] install runs.
studio = ["flask>=3.0"]
```

- [ ] **Step 2: Install it into the venv**

Run: `pip install -e '.[studio]'`
Expected: Flask (>=3.0) and its deps (Jinja2, Werkzeug, etc.) install successfully.

- [ ] **Step 3: Verify Flask imports and the core CLI still works**

Run: `python -c "import flask; print(flask.__version__)" && python -m pytest -q`
Expected: prints a 3.x version; full suite green (479 passed, 1 skipped — no test changes).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat (studio 3): add [studio] optional extra (flask>=3.0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Studio package skeleton + `_imminent` + read-model dataclasses

**Files:**
- Create: `src/fanops/studio/__init__.py` (EMPTY — must not import `app`)
- Create: `src/fanops/studio/views.py`
- Test: `tests/test_studio_views.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_views.py — CREATE
from datetime import datetime, timezone, timedelta
from fanops.studio.views import (
    _imminent, IMMINENT_THRESHOLD_MINUTES,
    SurfacePost, ReviewCard, ScheduleRow, LiftRow, LiftView,
)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def test_imminent_none_is_true():
    assert _imminent(None, NOW) is True

def test_imminent_unparseable_is_true():
    assert _imminent("garbage", NOW) is True

def test_imminent_naive_is_true():
    # naive time can't be safely compared / would fail publish_due -> treat as non-editable
    assert _imminent("2026-06-06T13:00:00", NOW) is True

def test_imminent_past_is_true():
    assert _imminent(_z(NOW - timedelta(minutes=1)), NOW) is True

def test_imminent_within_threshold_is_true():
    assert _imminent(_z(NOW + timedelta(minutes=IMMINENT_THRESHOLD_MINUTES - 1)), NOW) is True

def test_not_imminent_when_far_future():
    assert _imminent(_z(NOW + timedelta(hours=2)), NOW) is False

def test_dataclasses_construct():
    sp = SurfacePost(post_id="p1", account="@a", platform="instagram", persona="hype",
                     caption="x", hashtags=["#a"], scheduled_time=_z(NOW), media_url="/media/p1",
                     state="queued", imminent=False, editable=True)
    assert sp.editable is True and sp.media_url == "/media/p1"
    card = ReviewCard(clip_id="c1", preview_url="/clips/c1", source_name="s.mp4",
                      moment_window="0–7", reason="r", language="en", subtitles_burned=True,
                      held=False, held_reason=None, transcript_excerpt="hi", surfaces=[sp],
                      bucket="editable")
    assert card.bucket == "editable" and card.surfaces[0] is sp
    LiftView(variant_rows=[], variant_empty_reason="none", amplify_present=False,
             amplify_rows=[], amplify_empty_reason=None)
    ScheduleRow(post_id="p1", scheduled_time=_z(NOW), account="@a", platform="instagram",
                clip_id="c1", state="queued", imminent=False, editable=True)
    LiftRow(variant_hook="WATCH", account="@a", platform="instagram", lift_score=42.0,
            loop_state="learning ACTIVE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fanops.studio'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/__init__.py — CREATE EMPTY (a single comment is fine):
# FanOps Studio — local content-cockpit web UI. Import app.py LAZILY (it pulls Flask); keeping
# this package init Flask-free lets `import fanops.studio` (and the views/actions read models) work
# on a core, no-[studio] install.
```

```python
# src/fanops/studio/views.py — CREATE
"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.timeutil import parse_iso

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window


@dataclass
class SurfacePost:
    post_id: str
    account: str
    platform: str
    persona: Optional[str]
    caption: str
    hashtags: list[str]
    scheduled_time: Optional[str]
    media_url: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class ReviewCard:
    clip_id: str
    preview_url: str
    source_name: str
    moment_window: str
    reason: str
    language: Optional[str]
    subtitles_burned: bool
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str


@dataclass
class ScheduleRow:
    post_id: str
    scheduled_time: Optional[str]
    account: str
    platform: str
    clip_id: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class LiftRow:
    variant_hook: Optional[str]
    account: str
    platform: str
    lift_score: float
    loop_state: str
    amplify_state: Optional[str] = None


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]


def _imminent(scheduled_time: Optional[str], now: datetime,
              threshold_min: int = IMMINENT_THRESHOLD_MINUTES) -> bool:
    """True (edit-disabled) when the time is missing, unparseable, naive, already due, or within
    `threshold_min` of `now`. Fail-safe: any doubt -> imminent (read-only), never editable. `now`
    must be timezone-aware UTC."""
    if not scheduled_time:
        return True
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        return True
    return dt <= now + timedelta(minutes=threshold_min)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_views.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/__init__.py src/fanops/studio/views.py tests/test_studio_views.py
git commit -m "feat (studio 4): studio package skeleton + _imminent + read-model dataclasses

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `review_buckets` — editable / recent / held cards

**Files:**
- Modify: `src/fanops/studio/views.py` (add `review_buckets` + private lineage/media helpers)
- Test: `tests/test_studio_views.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_views.py — ADD
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.views import review_buckets

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _lineage(led):
    led.add_source(Source(id="src_1", source_path="/videos/show.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="big drop", transcript_excerpt="here we go", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/clips/clip_1.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))

def test_review_buckets_editable_recent_held(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active", "persona": "hype"}])
    led = Ledger.load(cfg); _lineage(led)
    # held clip (never crossposted)
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/clips/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk: foo"))
    # editable post (far-future queued)
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="EDIT ME", hashtags=["#x"],
                      state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=3))))
    # imminent post (queued but ~1 min out) -> shown, not editable
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPING", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=1))))
    # recent published post (within 24h)
    led.add_post(Post(id="p_recent", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="SHIPPED", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=2))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    by_bucket = {}
    for c in cards:
        by_bucket.setdefault(c.bucket, []).append(c)
    # held bucket present with reason
    assert any(c.held and c.held_reason == "brand risk: foo" for c in by_bucket.get("held", []))
    # editable card carries clip_1 with both queued surfaces; only the far-future one is editable
    ed = [c for c in by_bucket.get("editable", []) if c.clip_id == "clip_1"][0]
    sp = {s.post_id: s for s in ed.surfaces}
    assert sp["p_edit"].editable is True and sp["p_edit"].imminent is False
    assert sp["p_imm"].editable is False and sp["p_imm"].imminent is True
    assert ed.source_name == "show.mp4" and ed.moment_window == "0–7" and ed.reason == "big drop"
    assert sp["p_edit"].media_url == "/media/p_edit" and sp["p_edit"].persona == "hype"
    # recent bucket holds the published post, read-only
    rc = [c for c in by_bucket.get("recent", []) if c.clip_id == "clip_1"][0]
    assert all(not s.editable for s in rc.surfaces)
    assert any(s.post_id == "p_recent" for s in rc.surfaces)

def test_review_buckets_variant_media_url_is_post_scoped(tmp_path):
    # media_url is always /media/<post_id> (route resolves variant vs base); not the clip path.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_v", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="v", state=PostState.queued,
                      media_urls=["file:///clips/clip_1_variant.mp4"],
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    sp = [s for c in cards for s in c.surfaces if s.post_id == "p_v"][0]
    assert sp.media_url == "/media/p_v"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_views.py -k review_buckets -v`
Expected: FAIL — `ImportError: cannot import name 'review_buckets'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/views.py — ADD (after _imminent)

def _personas(accounts: Accounts) -> dict:
    return {a.handle: a.persona for a in accounts.accounts}

def _lineage_for_clip(led: Ledger, clip):
    """Return (source_name, moment_window, reason, language, transcript_excerpt) for a clip,
    walking clip -> moment -> source. Missing links degrade to safe '—'/None."""
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    source_name = Path(src.source_path).name if (src and src.source_path) else "—"
    moment_window = f"{mom.start}–{mom.end}" if mom is not None else "—"   # en dash
    reason = mom.reason if (mom and mom.reason) else "—"
    language = src.language if src else None
    excerpt = mom.transcript_excerpt if mom else None
    return source_name, moment_window, reason, language, excerpt

def _surface(post, *, persona, now: datetime) -> SurfacePost:
    imm = _imminent(post.scheduled_time, now)
    state = post.state.value
    return SurfacePost(
        post_id=post.id, account=post.account, platform=post.platform.value, persona=persona,
        caption=post.caption, hashtags=list(post.hashtags or []),
        scheduled_time=post.scheduled_time, media_url=f"/media/{post.id}",
        state=state, imminent=imm, editable=(state == PostState.queued.value and not imm))

def _card(led: Ledger, clip, posts, bucket: str, cfg: Config, personas: dict, now: datetime) -> ReviewCard:
    source_name, window, reason, language, excerpt = _lineage_for_clip(led, clip)
    surfaces = [_surface(p, persona=personas.get(p.account), now=now)
                for p in sorted(posts, key=lambda p: (p.account, p.platform.value))]
    return ReviewCard(
        clip_id=clip.id, preview_url=f"/clips/{clip.id}", source_name=source_name,
        moment_window=window, reason=reason, language=language, subtitles_burned=cfg.burn_subs,
        held=bool(clip.held), held_reason=clip.held_reason, transcript_excerpt=excerpt,
        surfaces=surfaces, bucket=bucket)

def review_buckets(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime) -> list[ReviewCard]:
    """Three buckets (spec §6): editable (queued posts grouped by clip), recent (published/analyzed
    within RECENT_WINDOW_HOURS), held (clips with held=True, no posts). A clip may appear in both
    editable and recent (different posts)."""
    personas = _personas(accounts)
    cards: list[ReviewCard] = []
    queued_by_clip: dict[str, list] = {}
    recent_by_clip: dict[str, list] = {}
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    for p in led.posts.values():
        if p.state is PostState.queued:
            queued_by_clip.setdefault(p.parent_id, []).append(p)
        elif p.state in (PostState.published, PostState.analyzed):
            keep = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    keep = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    keep = True   # unparseable but shipped -> still show it
            if keep:
                recent_by_clip.setdefault(p.parent_id, []).append(p)
    for clip_id, posts in queued_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None:
            cards.append(_card(led, clip, posts, "editable", cfg, personas, now))
    for clip_id, posts in recent_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None:
            cards.append(_card(led, clip, posts, "recent", cfg, personas, now))
    for clip in led.clips.values():
        if clip.held:
            cards.append(_card(led, clip, [], "held", cfg, personas, now))
    return cards
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_views.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/views.py tests/test_studio_views.py
git commit -m "feat (studio 5): review_buckets (editable/recent/held; imminent-gated editability)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `schedule_rows` — the upcoming timeline + recent history

**Files:**
- Modify: `src/fanops/studio/views.py` (add `schedule_rows`)
- Test: `tests/test_studio_views.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_views.py — ADD
from fanops.studio.views import schedule_rows

def test_schedule_rows_sorted_with_recent_and_imminent_flags(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_far", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="far", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=5))))
    led.add_post(Post(id="p_soon", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="soon", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=1))))
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="imm", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.add_post(Post(id="p_done", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="done", state=PostState.published,
                      scheduled_time=_z(NOW - timedelta(hours=1))))
    rows = schedule_rows(led, cfg, now=NOW)
    ids = [r.post_id for r in rows]
    # chronological by scheduled_time (recent published first since it is earliest)
    assert ids == ["p_done", "p_imm", "p_soon", "p_far"]
    by_id = {r.post_id: r for r in rows}
    assert by_id["p_far"].editable is True and by_id["p_far"].imminent is False
    assert by_id["p_imm"].editable is False and by_id["p_imm"].imminent is True
    assert by_id["p_done"].editable is False   # published -> read-only
    assert by_id["p_far"].clip_id == "clip_1" and by_id["p_far"].platform == "instagram"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_views.py -k schedule_rows -v`
Expected: FAIL — `ImportError: cannot import name 'schedule_rows'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/views.py — ADD

def schedule_rows(led: Ledger, cfg: Config, *, now: datetime) -> list[ScheduleRow]:
    """Queued posts (the editable timeline) plus recent published/analyzed posts (read-only past),
    sorted chronologically by scheduled_time. Rows with no/naive/unparseable time sort last."""
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    rows: list[ScheduleRow] = []
    for p in led.posts.values():
        if p.state is PostState.queued:
            include = True
        elif p.state in (PostState.published, PostState.analyzed):
            include = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    include = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    include = True
        else:
            include = False
        if not include:
            continue
        imm = _imminent(p.scheduled_time, now)
        state = p.state.value
        rows.append(ScheduleRow(
            post_id=p.id, scheduled_time=p.scheduled_time, account=p.account,
            platform=p.platform.value, clip_id=p.parent_id, state=state, imminent=imm,
            editable=(state == PostState.queued.value and not imm)))

    def _key(r: ScheduleRow):
        if not r.scheduled_time:
            return (1, "")
        try:
            dt = parse_iso(r.scheduled_time)
            if dt.tzinfo is None:
                return (1, r.scheduled_time)
            return (0, dt.isoformat())
        except (ValueError, TypeError):
            return (1, r.scheduled_time)
    rows.sort(key=_key)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_views.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/views.py tests/test_studio_views.py
git commit -m "feat (studio 6): schedule_rows (chronological queue + recent history, imminent flags)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `lift_rows` — per-variant lift + amplify section + honest empty states

**Files:**
- Modify: `src/fanops/studio/views.py` (add `lift_rows`, reusing `digest._gate_state` + `variant_amplify.amplify_candidates`)
- Test: `tests/test_studio_views.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_views.py — ADD
from fanops.studio.views import lift_rows

def test_lift_empty_no_analyzed_posts(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued))
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert "No analyzed posts yet" in view.variant_empty_reason
    assert view.amplify_present is False   # cfg.variant_amplify default OFF -> section absent

def test_lift_analyzed_but_no_variant_key(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 50.0}))   # analyzed but no variant_key
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_rows == []
    assert "Creative variation" in view.variant_empty_reason

def test_lift_ranks_variants_by_lift_score(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    led.add_post(Post(id="p_lo", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="lo", state=PostState.analyzed,
                      variant_key="vk_lo", variant_hook="CALM", metrics={"lift_score": 10.0}))
    led.add_post(Post(id="p_hi", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="hi", state=PostState.analyzed,
                      variant_key="vk_hi", variant_hook="HYPE", metrics={"lift_score": 90.0}))
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.variant_empty_reason is None
    assert [r.variant_hook for r in view.variant_rows] == ["HYPE", "CALM"]   # desc by lift_score
    assert view.variant_rows[0].lift_score == 90.0
    assert isinstance(view.variant_rows[0].loop_state, str) and view.variant_rows[0].loop_state

def test_lift_amplify_section_present_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"],
                          "status": "active"}])
    led = Ledger.load(cfg); _lineage(led)
    view = lift_rows(led, cfg, Accounts.load(cfg))
    assert view.amplify_present is True
    assert view.amplify_rows == [] and view.amplify_empty_reason is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_views.py -k lift -v`
Expected: FAIL — `ImportError: cannot import name 'lift_rows'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/views.py — ADD

def _loop_state(led: Ledger, cfg: Config, accounts: Optional[Accounts], post) -> str:
    """Per-surface learning-loop annotation, reusing the digest's fail-open gate computation."""
    try:
        from fanops.digest import _gate_state
        return _gate_state(led, cfg, post.account, post.platform, accounts=accounts)
    except Exception:
        return "gathering data"

def lift_rows(led: Ledger, cfg: Config, accounts: Optional[Accounts] = None) -> LiftView:
    """Per-variant lift (spec §8): analyzed posts carrying a variant_key + lift_score, ranked desc.
    Honest, reason-bearing empty states per sub-view; amplify section mirrors digest's
    `if cfg.variant_amplify:` gate (absent, not blank, when off)."""
    variant_posts = [p for p in led.posts.values()
                     if p.variant_key and p.state is PostState.analyzed and "lift_score" in p.metrics]
    variant_rows: list[LiftRow] = []
    variant_empty_reason: Optional[str] = None
    if not variant_posts:
        any_analyzed = any(p.state is PostState.analyzed for p in led.posts.values())
        if not any_analyzed:
            variant_empty_reason = ("No analyzed posts yet — a live metrics backend "
                                    "(FANOPS_POSTER ≠ dryrun and BLOTATO_API_KEY) or fed "
                                    "metrics is required.")
        else:
            variant_empty_reason = ("Creative variation (FANOPS_CREATIVE_VARIATION) was off when "
                                    "these posts were crossposted — no per-variant lift.")
    else:
        for p in sorted(variant_posts, key=lambda p: p.metrics.get("lift_score", 0.0), reverse=True):
            variant_rows.append(LiftRow(
                variant_hook=p.variant_hook or p.variant_key, account=p.account,
                platform=p.platform.value, lift_score=float(p.metrics.get("lift_score", 0.0)),
                loop_state=_loop_state(led, cfg, accounts, p)))

    amplify_present = cfg.variant_amplify
    amplify_rows: list[LiftRow] = []
    amplify_empty_reason: Optional[str] = None
    if amplify_present:
        try:
            from fanops.variant_amplify import amplify_candidates
            cands = amplify_candidates(led, cfg)
            for c in cands:
                p = led.posts.get(c.get("post_id"))
                if p is None:
                    continue
                amplify_rows.append(LiftRow(
                    variant_hook=c.get("winning_hook"), account=p.account,
                    platform=p.platform.value, lift_score=float(p.metrics.get("lift_score", 0.0)),
                    loop_state="amplify candidate", amplify_state=str(c.get("evidence", ""))))
            if not amplify_rows:
                amplify_empty_reason = "No sustained amplification streaks yet."
        except Exception:
            amplify_empty_reason = "Amplify state unavailable (fail-open)."
    return LiftView(variant_rows=variant_rows, variant_empty_reason=variant_empty_reason,
                    amplify_present=amplify_present, amplify_rows=amplify_rows,
                    amplify_empty_reason=amplify_empty_reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_views.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/views.py tests/test_studio_views.py
git commit -m "feat (studio 7): lift_rows (rank by lift_score, reuse digest gate, honest empty states)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `actions.py` — lock-safe reschedule / edit-caption / snooze

**Files:**
- Create: `src/fanops/studio/actions.py`
- Test: `tests/test_studio_actions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_actions.py — CREATE
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.actions import reschedule_post, edit_caption, snooze_clip, ActionResult

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    return led

def test_reschedule_persists_tz_aware_z(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == _z(NOW + timedelta(hours=8))

def test_reschedule_naive_input_never_persists_naive(tmp_path):
    # spec §9 fix #5: a naive time would later mark the post failed in publish_due. Must be coerced
    # to tz-aware UTC Z before it touches the ledger.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "2026-06-06T20:00:00", now=NOW)   # NAIVE (no Z/offset)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == "2026-06-06T20:00:00Z"   # coerced to UTC Z

def test_reschedule_garbage_time_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "not-a-time", now=NOW)
    assert res.ok is False and res.error
    assert Ledger.load(cfg).posts["p_edit"].scheduled_time == _z(NOW + timedelta(hours=3))  # unchanged

def test_reschedule_unknown_post_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "nope", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "no such post" in res.error.lower()

def test_reschedule_non_queued_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].state = PostState.published; led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "queued" in res.error.lower()

def test_reschedule_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW + timedelta(minutes=1)); led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "imminent" in res.error.lower()

def test_edit_caption_persists(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = edit_caption(cfg, "p_edit", "BRAND NEW CAPTION", now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).posts["p_edit"].caption == "BRAND NEW CAPTION"

def test_edit_caption_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW - timedelta(minutes=1)); led.save()  # already due
    res = edit_caption(cfg, "p_edit", "TOO LATE", now=NOW)
    assert res.ok is False
    assert Ledger.load(cfg).posts["p_edit"].caption == "OLD"

def test_snooze_pushes_all_clip_posts_far_out(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.add_post(Post(id="p2", parent_id="clip_1", account="@b", account_id="2",
                      platform=Platform.youtube, caption="y", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=4))))
    # one imminent post on the same clip should be left alone
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@c", account_id="3",
                      platform=Platform.tiktok, caption="t", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.save()
    res = snooze_clip(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["count"] == 2   # p_edit + p2 (not p_imm)
    out = Ledger.load(cfg)
    from fanops.timeutil import parse_iso
    assert parse_iso(out.posts["p_edit"].scheduled_time) >= NOW + timedelta(days=364)
    assert parse_iso(out.posts["p2"].scheduled_time) >= NOW + timedelta(days=364)
    assert out.posts["p_imm"].scheduled_time == _z(NOW + timedelta(minutes=2))   # untouched

def test_actions_use_single_transaction(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    spy = mocker.spy(Ledger, "transaction")
    reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert spy.call_count == 1   # exactly one lock acquisition per mutation (no lock-free load+save)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_actions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fanops.studio.actions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the CLI recovery verbs (cli.py:285,298) so it cannot lose-update
against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent

SNOOZE_DAYS = 365


@dataclass
class ActionResult:
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


def _guard_editable_post(led: Ledger, post_id: str, now: datetime):
    """Return (post, None) if post exists, is queued, and is not imminent; else (None, error)."""
    if post_id not in led.posts:
        return None, f"no such post: {post_id}"
    p = led.posts[post_id]
    if p.state is not PostState.queued:
        return None, f"post {post_id} is not queued (state={p.state.value}); only queued posts are editable"
    if _imminent(p.scheduled_time, now):
        return None, f"post {post_id} is imminent/already due — shipping now, cannot edit"
    return p, None


def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    try:
        z = _normalize_z(new_time)                 # OUTSIDE the lock: reject bad input early
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=f"bad time {new_time!r}: {str(exc)[:120]}")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.scheduled_time = z
    return ActionResult(ok=True, detail={"post_id": post_id, "scheduled_time": z})


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.caption = caption
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": caption})


def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Push every non-imminent queued post of a clip ~SNOOZE_DAYS into the future, in ONE
    transaction (atomic — never a partial snooze). Inherits the same guard + normalization."""
    now = _now(now)
    z = iso_z(now + timedelta(days=SNOOZE_DAYS))
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips:
            return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        count = 0
        for p in led.posts.values():
            if p.parent_id == clip_id and p.state is PostState.queued and not _imminent(p.scheduled_time, now):
                p.scheduled_time = z
                count += 1
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": count, "scheduled_time": z})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_actions.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/actions.py tests/test_studio_actions.py
git commit -m "feat (studio 8): lock-safe actions (reschedule/edit-caption/snooze; naive-time coercion; in-lock guards)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Templates + vendored static assets

**Files:**
- Create: `src/fanops/studio/templates/base.html`, `review.html`, `_card.html`, `schedule.html`, `lift.html`, `_result.html`
- Create: `src/fanops/studio/static/studio.css`
- Create: `src/fanops/studio/static/htmx.min.js` (vendored, pinned)

This task has no unit test of its own (templates are exercised by Task 10's route tests). Verification is a Jinja2 syntax compile check.

- [ ] **Step 1: Vendor HTMX (pinned)**

Run: `curl -fsSL https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js -o src/fanops/studio/static/htmx.min.js && wc -c src/fanops/studio/static/htmx.min.js`
Expected: a non-empty file (~48 KB). If offline/unavailable, write a minimal stub `src/fanops/studio/static/htmx.min.js` containing `/* htmx vendored placeholder — replace with htmx.org@2.0.3 before real use */` (routes/tests only check the file is served, not that JS executes), and note the gap in the PR.

- [ ] **Step 2: Write `studio.css`**

```css
/* src/fanops/studio/static/studio.css */
:root { --bg:#0f1115; --panel:#171a21; --ink:#e6e9ef; --muted:#8b93a7; --ok:#3fb950; --warn:#d29922; --line:#262b36; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }
header.nav { display:flex; gap:1rem; align-items:center; padding:.75rem 1.25rem; background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; }
header.nav a { color:var(--muted); text-decoration:none; padding:.25rem .6rem; border-radius:6px; }
header.nav a.active, header.nav a:hover { color:var(--ink); background:var(--line); }
main { padding:1.25rem; max-width:1100px; margin:0 auto; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:1rem; margin-bottom:1.25rem; }
.card video { width:100%; max-height:420px; background:#000; border-radius:8px; }
.meta { color:var(--muted); font-size:.85rem; margin:.4rem 0; }
.surface { border-top:1px solid var(--line); padding:.6rem 0; }
.badge { font-size:.7rem; padding:.1rem .45rem; border-radius:99px; border:1px solid var(--line); color:var(--warn); }
.badge.shipped { color:var(--muted); }
.ro { color:var(--muted); }
textarea, input[type=text], input[type=datetime-local] { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--line); border-radius:6px; padding:.4rem; font:inherit; }
button { background:var(--line); color:var(--ink); border:1px solid var(--line); border-radius:6px; padding:.35rem .7rem; cursor:pointer; }
button:hover { border-color:var(--muted); }
.ok { color:var(--ok); } .err { color:#f85149; }
table { width:100%; border-collapse:collapse; } td, th { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); }
.bar { height:14px; background:linear-gradient(90deg,#2f81f7,#3fb950); border-radius:7px; }
.empty { color:var(--muted); font-style:italic; padding:.8rem; border:1px dashed var(--line); border-radius:8px; }
h2 { margin:.2rem 0 1rem; } h3 { color:var(--muted); font-weight:600; margin:1.2rem 0 .5rem; }
```

- [ ] **Step 3: Write `base.html`**

```html
{# src/fanops/studio/templates/base.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FanOps Studio — {% block title %}{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='studio.css') }}">
  <script src="{{ url_for('static', filename='htmx.min.js') }}" defer></script>
</head>
<body>
  <header class="nav">
    <strong>FanOps Studio</strong>
    <a href="{{ url_for('review') }}" class="{{ 'active' if tab=='review' else '' }}">Review</a>
    <a href="{{ url_for('schedule') }}" class="{{ 'active' if tab=='schedule' else '' }}">Schedule</a>
    <a href="{{ url_for('lift') }}" class="{{ 'active' if tab=='lift' else '' }}">Lift</a>
  </header>
  <main>{% block body %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 4: Write `_card.html` and `review.html`**

```html
{# src/fanops/studio/templates/_card.html #}
<div class="card" id="card-{{ card.clip_id }}">
  {% if card.bucket == 'held' %}
    <div class="meta"><span class="badge">HELD</span> {{ card.held_reason or 'held for review' }}</div>
    <video controls preload="metadata" src="{{ card.preview_url }}"></video>
  {% else %}
    <video controls preload="metadata" src="{{ card.preview_url }}"></video>
  {% endif %}
  <div class="meta">
    {{ card.source_name }} · {{ card.moment_window }} · {{ card.language or '—' }}
    · subs: {{ 'on' if card.subtitles_burned else 'off' }} · <em>{{ card.reason }}</em>
  </div>
  {% if card.transcript_excerpt %}<div class="meta">“{{ card.transcript_excerpt }}”</div>{% endif %}
  {% for s in card.surfaces %}
    <div class="surface">
      <div class="meta">{{ s.account }}/{{ s.platform }}{% if s.persona %} · {{ s.persona }}{% endif %}
        · {{ s.scheduled_time or 'unscheduled' }}
        {% if s.imminent %}<span class="badge {{ 'shipped' if s.state != 'queued' else '' }}">{{ 'shipped' if s.state != 'queued' else 'shipping now' }}</span>{% endif %}
      </div>
      <video controls preload="none" src="{{ s.media_url }}"></video>
      {% if s.editable %}
        <form hx-post="{{ url_for('do_caption', post_id=s.post_id) }}" hx-target="#res-{{ s.post_id }}" hx-swap="innerHTML">
          <textarea name="caption" rows="2">{{ s.caption }}</textarea>
          <button type="submit">Save caption</button>
        </form>
        <form hx-post="{{ url_for('do_reschedule', post_id=s.post_id) }}" hx-target="#res-{{ s.post_id }}" hx-swap="innerHTML">
          <input type="text" name="new_time" value="{{ s.scheduled_time }}" placeholder="2026-06-08T14:00:00Z">
          <button type="submit">Reschedule</button>
        </form>
      {% else %}
        <div class="ro">{{ s.caption }}</div>
      {% endif %}
      {% if s.hashtags %}<div class="meta ro">{{ s.hashtags|join(' ') }} <em>(stored, not posted)</em></div>{% endif %}
      <div id="res-{{ s.post_id }}"></div>
    </div>
  {% endfor %}
  {% if card.bucket == 'editable' and card.surfaces %}
    <form hx-post="{{ url_for('do_snooze', clip_id=card.clip_id) }}" hx-target="#card-{{ card.clip_id }}" hx-swap="afterend">
      <button type="submit">Snooze clip (+{{ 365 }}d)</button>
    </form>
  {% endif %}
</div>
```

```html
{# src/fanops/studio/templates/review.html #}
{% extends "base.html" %}
{% block title %}Review{% endblock %}
{% block body %}
  <h2>Review</h2>
  {% set buckets = {'editable':'Editable (upcoming)','held':'Held for review','recent':'Recently shipped'} %}
  {% for key, label in buckets.items() %}
    {% set group = cards | selectattr('bucket','equalto',key) | list %}
    {% if group %}
      <h3>{{ label }}</h3>
      {% for card in group %}{% include "_card.html" %}{% endfor %}
    {% endif %}
  {% endfor %}
  {% if not cards %}<div class="empty">Nothing in the ledger yet. Run `fanops advance` to produce clips.</div>{% endif %}
{% endblock %}
```

- [ ] **Step 5: Write `schedule.html`, `lift.html`, `_result.html`**

```html
{# src/fanops/studio/templates/schedule.html #}
{% extends "base.html" %}
{% block title %}Schedule{% endblock %}
{% block body %}
  <h2>Schedule</h2>
  {% if rows %}
  <table>
    <tr><th>Time</th><th>Account/Platform</th><th>Clip</th><th>State</th><th></th></tr>
    {% for r in rows %}
    <tr>
      <td>{{ r.scheduled_time or 'unscheduled' }}{% if r.imminent %} <span class="badge {{ 'shipped' if r.state != 'queued' else '' }}">{{ 'shipped' if r.state != 'queued' else 'now' }}</span>{% endif %}</td>
      <td>{{ r.account }}/{{ r.platform }}</td>
      <td>{{ r.clip_id }}</td>
      <td>{{ r.state }}</td>
      <td>
        {% if r.editable %}
        <form hx-post="{{ url_for('do_reschedule', post_id=r.post_id) }}" hx-target="#res-s-{{ r.post_id }}" hx-swap="innerHTML">
          <input type="text" name="new_time" value="{{ r.scheduled_time }}">
          <button type="submit">Move</button>
        </form>
        <span id="res-s-{{ r.post_id }}"></span>
        {% else %}<span class="ro">—</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">No queued or recent posts.</div>{% endif %}
{% endblock %}
```

```html
{# src/fanops/studio/templates/lift.html #}
{% extends "base.html" %}
{% block title %}Lift{% endblock %}
{% block body %}
  <h2>Lift</h2>
  <h3>Lift by variant</h3>
  {% if view.variant_rows %}
    <table>
      <tr><th>Hook</th><th>Account/Platform</th><th>Lift</th><th>Loop state</th></tr>
      {% for r in view.variant_rows %}
      <tr>
        <td>{{ r.variant_hook }}</td>
        <td>{{ r.account }}/{{ r.platform }}</td>
        <td><div class="bar" style="width: {{ [r.lift_score, 100]|min }}%"></div> {{ '%.1f'|format(r.lift_score) }}</td>
        <td class="ro">{{ r.loop_state }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}<div class="empty">{{ view.variant_empty_reason }}</div>{% endif %}

  {% if view.amplify_present %}
    <h3>Amplification streaks</h3>
    {% if view.amplify_rows %}
      <table>
        <tr><th>Hook</th><th>Account/Platform</th><th>Lift</th><th>Evidence</th></tr>
        {% for r in view.amplify_rows %}
        <tr><td>{{ r.variant_hook }}</td><td>{{ r.account }}/{{ r.platform }}</td>
            <td>{{ '%.1f'|format(r.lift_score) }}</td><td class="ro">{{ r.amplify_state }}</td></tr>
        {% endfor %}
      </table>
    {% else %}<div class="empty">{{ view.amplify_empty_reason }}</div>{% endif %}
  {% endif %}
{% endblock %}
```

```html
{# src/fanops/studio/templates/_result.html #}
{% if result.ok %}
  <span class="ok">✓ {{ result.detail.get('scheduled_time') or result.detail.get('caption') or 'saved' }}</span>
{% else %}
  <span class="err">✗ {{ result.error }}</span>
{% endif %}
```

- [ ] **Step 6: Verify all templates compile (Jinja2 syntax)**

Run:
```bash
python -c "
from jinja2 import Environment, FileSystemLoader
e = Environment(loader=FileSystemLoader('src/fanops/studio/templates'))
for t in ['base.html','review.html','_card.html','schedule.html','lift.html','_result.html']:
    e.get_template(t); print('ok', t)
"
```
Expected: `ok base.html` … `ok _result.html` (no `TemplateSyntaxError`). Note: `url_for` is undefined at parse time but only fails at render (inside Flask), so parsing succeeds.

- [ ] **Step 7: Commit**

```bash
git add src/fanops/studio/templates src/fanops/studio/static
git commit -m "feat (studio 9): templates (Review/Schedule/Lift + card/result partials) + vendored htmx + css

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `app.py` — Flask factory, tab routes, media/clip serving, mutation routes

**Files:**
- Create: `src/fanops/studio/app.py`
- Test: `tests/test_studio_app.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_app.py — CREATE
import json
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg, tmp_path):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    base = tmp_path / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    variant = tmp_path / "variant.mp4"; variant.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANT!")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_base", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="BASE", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_var", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="VAR", state=PostState.queued,
                      media_urls=[f"file://{variant}"], scheduled_time=_z(NOW + timedelta(hours=4))))
    led.save()
    return base, variant

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()

def test_tabs_return_200(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    c = _client(cfg)
    for path, needle in [("/review", b"Review"), ("/schedule", b"Schedule"), ("/lift", b"Lift")]:
        r = c.get(path); assert r.status_code == 200 and needle in r.data

def test_root_redirects_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).get("/")
    assert r.status_code in (301, 302) and "/review" in r.headers["Location"]

def test_media_serves_variant_when_present(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    r = _client(cfg).get("/media/p_var")
    assert r.status_code == 200 and r.data == variant.read_bytes()   # variant file, not base

def test_media_falls_back_to_base_clip(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    r = _client(cfg).get("/media/p_base")
    assert r.status_code == 200 and r.data == base.read_bytes()

def test_media_404_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    assert _client(cfg).get("/media/nope").status_code == 404

def test_media_404_missing_file(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    variant.unlink()   # stale path
    assert _client(cfg).get("/media/p_var").status_code == 404

def test_clips_serves_base_and_404(tmp_path):
    cfg = Config(root=tmp_path); base, _ = _seed(cfg, tmp_path)
    c = _client(cfg)
    assert c.get("/clips/clip_1").status_code == 200
    assert c.get("/clips/nope").status_code == 404

def test_reschedule_route_roundtrips_to_ledger(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    new = _z(NOW + timedelta(days=2))
    r = _client(cfg).post("/reschedule/p_base", data={"new_time": new})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p_base"].scheduled_time == new

def test_caption_route_roundtrips_to_ledger(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/caption/p_base", data={"caption": "EDITED VIA HTTP"})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p_base"].caption == "EDITED VIA HTTP"

def test_snooze_route_roundtrips(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/snooze/clip_1")
    assert r.status_code == 200
    from fanops.timeutil import parse_iso
    assert parse_iso(Ledger.load(cfg).posts["p_base"].scheduled_time) > NOW + timedelta(days=300)

def test_core_cli_imports_with_flask_absent(monkeypatch):
    # spec §10/§15: a no-[studio] install must still import fanops.cli and run non-studio verbs.
    import sys, builtins, importlib
    real_import = builtins.__import__
    def blocked(name, *a, **k):
        if name == "flask" or name.startswith("flask."):
            raise ImportError("flask blocked for test")
        return real_import(name, *a, **k)
    for m in list(sys.modules):
        if m == "flask" or m.startswith("flask.") or m.startswith("fanops.studio.app"):
            sys.modules.pop(m, None)
    monkeypatch.setattr(builtins, "__import__", blocked)
    importlib.reload(importlib.import_module("fanops.cli"))   # must NOT raise
    import fanops.cli as cli
    assert cli.main(["status"]) in (0, 1, 2)   # a real verb dispatches without Flask
    # ...and ONLY the studio verb needs Flask: this proves the import is lazy AND inside _dispatch
    # (a module-top import would have already failed the reload above; this catches a top-of-app
    # import that somehow still let the reload pass). The studio branch hits `from fanops.studio.app
    # import create_app` -> blocked flask -> ImportError, which main() does not swallow.
    with pytest.raises(ImportError, match="flask blocked"):
        cli.main(["studio"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_studio_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fanops.studio.app'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/studio/app.py — CREATE
"""Flask app factory for FanOps Studio (spec §10). Imports Flask at MODULE TOP — that is fine
because this module is only imported LAZILY from the CLI dispatch branch (never at cli.py top), so a
core no-[studio] install never touches it. Reads use lock-free Ledger.load (atomic os.replace
guarantees a complete file); writes go through studio.actions (one Ledger.transaction each)."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.studio import views, actions

_HERE = Path(__file__).resolve().parent


def _media_path_for_post(led: Ledger, post_id: str):
    """Resolve the local file to serve for a post: the variant overlay (media_urls[0], stripped of
    file://) when it is a local file, else the base clip path. Returns None if nothing resolvable.
    The id is only a dict-key lookup and the path comes from the trusted ledger (never the URL), so
    there is no path traversal."""
    post = led.posts.get(post_id)
    if post is None:
        return None
    candidate = None
    if post.media_urls:
        raw = post.media_urls[0]
        if raw.startswith("file://"):
            candidate = raw[len("file://"):]
        elif not raw.startswith(("http://", "https://")):
            candidate = raw            # a bare local path
        # http(s) publicUrl -> not locally servable; fall through to base clip
    if candidate is None:
        clip = led.clips.get(post.parent_id)
        candidate = clip.path if clip else None
    return candidate


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder=str(_HERE / "templates"), static_folder=str(_HERE / "static"))

    @app.get("/")
    def index():
        return redirect(url_for("review"))

    @app.get("/review")
    def review():
        led = Ledger.load(cfg)
        accounts = Accounts.load(cfg)
        cards = views.review_buckets(led, accounts, cfg, now=datetime.now(timezone.utc))
        return render_template("review.html", cards=cards, tab="review")

    @app.get("/schedule")
    def schedule():
        led = Ledger.load(cfg)
        rows = views.schedule_rows(led, cfg, now=datetime.now(timezone.utc))
        return render_template("schedule.html", rows=rows, tab="schedule")

    @app.get("/lift")
    def lift():
        led = Ledger.load(cfg)
        view = views.lift_rows(led, cfg, Accounts.load(cfg))
        return render_template("lift.html", view=view, tab="lift")

    @app.get("/media/<post_id>")
    def media(post_id):
        path = _media_path_for_post(Ledger.load(cfg), post_id)
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/clips/<clip_id>")
    def clip_media(clip_id):
        clip = Ledger.load(cfg).clips.get(clip_id)
        if clip is None or not clip.path or not os.path.exists(clip.path):
            abort(404)
        return send_file(clip.path)

    @app.post("/reschedule/<post_id>")
    def do_reschedule(post_id):
        result = actions.reschedule_post(cfg, post_id, request.form.get("new_time", ""))
        return render_template("_result.html", result=result)

    @app.post("/caption/<post_id>")
    def do_caption(post_id):
        result = actions.edit_caption(cfg, post_id, request.form.get("caption", ""))
        return render_template("_result.html", result=result)

    @app.post("/snooze/<clip_id>")
    def do_snooze(clip_id):
        result = actions.snooze_clip(cfg, clip_id)
        return render_template("_result.html", result=result)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_studio_app.py -v && python -m pytest -q`
Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/studio/app.py tests/test_studio_app.py
git commit -m "feat (studio 10): Flask app factory — tabs, variant-aware /media, /clips, mutation routes, flask-absent guard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `fanops studio` CLI subcommand (lazy Flask import)

**Files:**
- Modify: `src/fanops/cli.py` (subparser registration at `cli.py:143`; dispatch branch before `run` at `cli.py:339`)
- Test: `tests/test_cli.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — ADD
def test_studio_subcommand_parses_and_lazy_imports(tmp_path, monkeypatch, mocker):
    # `fanops studio` must build the app via a LAZY import and call app.run with the bound host/port,
    # without actually serving. We patch create_app so no socket is opened.
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    fake_app = mocker.Mock()
    create_app = mocker.Mock(return_value=fake_app)
    # the module is imported lazily inside the dispatch branch, so patch the source symbol
    mocker.patch("fanops.studio.app.create_app", create_app)
    rc = cli.main(["studio", "--host", "127.0.0.1", "--port", "9999"])
    assert rc == 0
    create_app.assert_called_once()
    fake_app.run.assert_called_once()
    _, kwargs = fake_app.run.call_args
    assert kwargs.get("host") == "127.0.0.1" and kwargs.get("port") == 9999

def test_studio_defaults_host_port(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    import fanops.cli as cli
    fake_app = mocker.Mock()
    mocker.patch("fanops.studio.app.create_app", mocker.Mock(return_value=fake_app))
    assert cli.main(["studio"]) == 0
    _, kwargs = fake_app.run.call_args
    assert kwargs.get("host") == "127.0.0.1" and kwargs.get("port") == 8787
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -k studio -v`
Expected: FAIL — argparse exits with code 2 (`invalid choice: 'studio'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — in main(), AFTER `sub.add_parser("intake")` (line 143), BEFORE p_run (line 144):
    p_studio = sub.add_parser("studio", help="local content-cockpit web UI (Review/Schedule/Lift)")
    p_studio.add_argument("--host", default="127.0.0.1")   # localhost only; no auth in v1
    p_studio.add_argument("--port", type=int, default=8787)
```

```python
# src/fanops/cli.py — in _dispatch(), add a branch BEFORE `if args.cmd == "run":` (line 339):
    if args.cmd == "studio":
        # LAZY import (spec §10): Flask is an optional extra; importing create_app here — never at
        # module top — keeps `import fanops.cli` (hence every other verb) working on a core,
        # no-[studio] install. Mirrors the discover/intake lazy-import idiom (cli.py:325,334).
        from fanops.studio.app import create_app
        app = create_app(cfg)
        print(f"FanOps Studio on http://{args.host}:{args.port}  (Ctrl-C to stop)")
        app.run(host=args.host, port=args.port)
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -k studio -v && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Manual smoke (optional, real server)**

Run (background, then curl, then kill):
```bash
fanops studio --port 8788 &  SRV=$!; sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8788/review
kill $SRV
```
Expected: `200`.

- [ ] **Step 6: Commit**

```bash
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (studio 11): `fanops studio` subcommand (lazy Flask import, default 127.0.0.1:8787)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Integration — Review serves a real H.264/AAC clip (codec invariant)

**Files:**
- Create: `tests/integration/test_studio_real.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_studio_real.py — CREATE
import json, shutil, subprocess
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.clip import ffmpeg_clip_cmd
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="needs real ffmpeg/ffprobe")
def test_review_serves_real_h264_aac_mp4(tmp_path):
    # 1) make a real source with ffmpeg's test sources, then render a base clip via the SAME
    #    ffmpeg_clip_cmd the pipeline uses (asserting the H.264/AAC/+faststart codec invariant).
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(src)], check=True, capture_output=True)
    clip_path = tmp_path / "clip_1.mp4"
    cmd = ffmpeg_clip_cmd(str(src), str(clip_path), 0.0, 2.0, Fmt.r9x16.value, src_w=320, src_h=240)
    assert "-c:v" in cmd and "libx264" in cmd and "+faststart" in cmd   # codec invariant pinned
    subprocess.run(cmd, check=True, capture_output=True)
    assert clip_path.exists() and clip_path.stat().st_size > 0

    # 2) queue a post over that real clip
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(src), language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-2", start=0, end=2,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(clip_path), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="real", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()

    # 3) Studio serves the real bytes and ffprobe confirms H.264 video + AAC audio
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/media/p1")
    assert r.status_code == 200 and r.data == clip_path.read_bytes()

    out = tmp_path / "served.mp4"; out.write_bytes(r.data)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                            "-of", "json", str(out)], check=True, capture_output=True, text=True)
    codecs = {s["codec_name"] for s in json.loads(probe.stdout)["streams"]}
    assert "h264" in codecs and "aac" in codecs
```

- [ ] **Step 2: Run test to verify it fails (or skips cleanly)**

Run: `python -m pytest tests/integration/test_studio_real.py -v`
Expected (ffmpeg present): the test runs end-to-end and PASSES once Tasks 1–11 are done (it only needs `create_app` + `ffmpeg_clip_cmd`, which already exist). If `ffmpeg`/`ffprobe` are absent: SKIPPED cleanly.

> If this is run before Task 10, it fails on `ModuleNotFoundError: fanops.studio.app` — which is the expected red. After Task 10/11 it should pass.

- [ ] **Step 3: Run the full suite including integration**

Run: `python -m pytest -q` (integration included) and `python -m pytest -q -m "not integration"`
Expected: both green. Confirm the integration test is collected (and runs or skips based on ffmpeg).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_studio_real.py
git commit -m "test (studio 12): integration — Review serves a real H.264/AAC mp4 end-to-end

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Lint + docs sync

**Files:**
- Possibly modify: any new `src/fanops/studio/*.py` (ruff fixes)
- Modify: `README.md` (add a Studio section) — and `CLAUDE.md` only if it enumerates commands/extras

- [ ] **Step 1: Run ruff and fix any findings**

Run: `ruff check src/fanops/studio src/fanops/config.py src/fanops/crosspost.py src/fanops/cli.py`
Expected: green. Fix any `F`/`E` issues (unused imports, undefined names) the new code introduces; do NOT mass-reformat.

- [ ] **Step 2: Add a Studio section to README**

Add (adapt to the README's existing structure):
```markdown
## FanOps Studio (local web cockpit)

`pip install -e '.[studio]'` then `fanops studio` serves a localhost UI at http://127.0.0.1:8787
with three tabs:

- **Review** — upcoming clips grouped by clip; tweak caption, reschedule, or snooze (queued + not
  within 5 min of due). Held clips and recently-shipped posts show read-only.
- **Schedule** — the upcoming queue on a chronological timeline + recent history.
- **Lift** — per-variant `lift_score` ranking (when analyzed posts with variants exist).

The Studio edits the ledger ONLY through the lock-safe `Ledger.transaction` path; it never blocks
publishing. To get an editable window, run the pipeline with a now/future `--base-time` and set
`FANOPS_PUBLISH_LEAD_MINUTES` (e.g. `120`) — a constant, determinism-safe offset (default `0` is
byte-identical to today). Bind is `127.0.0.1` only; no auth (single-operator, local).
```

- [ ] **Step 3: Verify docs match behavior + full suite**

Run: `python -m pytest -q && ruff check src/`
Expected: full suite green (≈ 471 + ~40 new ≈ 511 passed, 1 skipped — exact count re-derived live); ruff green.

- [ ] **Step 4: Commit**

```bash
git add README.md $(git ls-files -m src/fanops/studio)
git commit -m "docs (studio 13): README Studio section + ruff clean

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (run by the plan author, completed)

**Spec coverage:** §4 lead-time → Task 1+2; §6 Review (editable/recent/held, variant media, no-hashtag-edit, held review-only) → Task 5 + templates; §7 Schedule → Task 6; §8 Lift + empty states → Task 7; §9 write surface (reschedule/edit/snooze, naive normalization, in-lock guards) → Task 8; §10 architecture (lazy Flask, /media variant resolution, 404 not 500, 127.0.0.1, optional extra) → Tasks 3,10,11; §11 file structure → all; §12 security/concurrency → Task 8+10; §15 testing strategy (all five test files + integration) → Tasks 1,2,5–8,10,12; §16 success criteria → covered by Tasks 11 (serves), 5/10 (variant-aware play), 8/10 (persist + guard), 7 (lift+empty), 1/2 (default byte-identical), 10 (flask-absent CLI).

**Deliberate deviations from the spec (documented):**
- `snooze_clip` runs in ONE transaction (atomic), not N `reschedule_post` calls — same guard+normalization, but never a partial snooze. (Spec §9 wording "calls reschedule_post on each" is behavioral, not literal.)
- `reschedule_post` COERCES naive→UTC (`replace(tzinfo=utc)` before `iso_z`) rather than rejecting, because `iso_z` would otherwise treat naive as local time. Garbage (unparseable) input IS rejected. A test pins both.
- `ReviewCard.subtitles_burned` is `cfg.burn_subs` (render-time setting) — the Clip model has no per-clip subtitles flag; this is an honest approximation, labelled "subs: on/off".

**Placeholder scan:** none — every code/test step carries full content (the only conditional is the htmx vendoring fallback, which is an explicit documented branch).

**Type consistency:** dataclass field names + builder/action signatures in the "Locked types" block are used verbatim in Tasks 4–8, the templates (Task 9), and the routes (Task 10); `ActionResult.detail` keys (`scheduled_time`/`caption`/`count`) match `_result.html`.
