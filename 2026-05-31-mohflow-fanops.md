# MOH FLOW FAN OPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous, human-governed content-repurposing engine that atomizes Moh Flow's content bank into *creatively distinct* clip variants (different hooks/captions/levers, not just aspect ratios), routes them across a network of independent fan accounts via Blotato with uploaded media and per-platform-correct payloads, pulls real performance back to compound on winners, and surfaces brand-risk holds to Moh — all off one git-versioned ledger with no per-post gate.

**Architecture:** A Python package (`src/fanops/`) of stage modules — ingest → clip → tag → variant → caption → tag-inject → schedule → upload+post → track → adjust — each reading/writing one git-versioned JSON ledger (`Asset → Clip → Variant → Post`). A **variant carries its own creative spec** (hook treatment, caption, lever, sound, aspect) so tier governs *conceptualization* (count of distinct treatments), not render passes. Posting goes through a `Poster` interface with three backends: dry-run (pre-credential), Blotato **REST** (nested `post{content,target}` shape, fallback), and Blotato **MCP** (flat-arg shape, primary) — two *different* payload builders because the shapes genuinely differ. Media is uploaded to Blotato (presigned URL) before posting so video actually ships. Clipping is local `ffmpeg`; the connected media MCP is an optional assist. Holds are **first-class on the Variant** and surface in the digest. The system's *runtime operating rules* live in `00_control/` docs this build authors; the *strategy* is produced by the system's own Research+Strategy stages, which **discover and read** Moh's real project files.

**Tech Stack:** Python 3.14, `pytest` + `pytest-mock`, `requests`, `python-dotenv`, `pydantic` 2.x, `ffmpeg` 8.0 (CLI, filters verified on this box), `yt-dlp` (ingest), git. Blotato v2 REST (`https://backend.blotato.com/v2`) + official MCP (`https://mcp.blotato.com/mcp`). Connected media MCP (`mcp__403ddb95…`) optional.

**Reference (verified 2026-05-31, re-verified in pre-flight):** Blotato contract + connected-MCP capabilities are in file memory (`blotato-api-contract.md`, `connected-media-mcp.md`) and `CLAUDE.md`. The MindStudio article supplies the *pattern*, not the code.

**This revision** incorporates a three-reviewer adversarial pre-flight pass that found: 3 code bugs (origin tag, post-id idempotency, held-retry), 3 critical Blotato gaps (media never uploaded → text-only posts; TikTok/YouTube/FB required target fields → 422; MCP shape is flat not nested), and 6 intent failures (holds vanish silently; variants differ only by aspect; track/adjust loop open both ends; artist tagging absent; opsec stagger single-anchor; research never reads real files). Every fix below is grounded in that pass.

**The only human-blocked work** (everything else the agent builds end-to-end): (1) creating account identities + credentials, (2) connecting Blotato / providing its key, (3) strategy/identity sign-off. The Poster runs in dry-run until (2) lands; the registry holds non-secret metadata until (1).

---

## File Structure

**Created by this plan:**

| Path | Responsibility |
|---|---|
| `MohFlow-FanOps/00_control/BRIEF.md` | Verbatim canonical brief |
| `MohFlow-FanOps/00_control/RUNTIME.md` | Runtime operating doc the operating agent reads (authored by build) |
| `MohFlow-FanOps/00_control/strategy.md` | Strategy framework (template authored by build; filled by Research/Strategy stages) |
| `MohFlow-FanOps/00_control/ledger.json` | Single source of truth (created/managed by code) |
| `MohFlow-FanOps/00_control/ledger_digest.md` | Auto-generated human-readable digest |
| `MohFlow-FanOps/00_control/accounts.json` | Account registry (non-secret metadata only) |
| `MohFlow-FanOps/{01_inbox…08_reports}/` | Pipeline stage working dirs |
| `src/fanops/__init__.py` | Package marker |
| `src/fanops/ids.py` | Deterministic ID generation for units |
| `src/fanops/models.py` | Pydantic models: Asset, Clip, Variant, Post + enums (tags) |
| `src/fanops/ledger.py` | Load/save/query/append ledger; de-dupe; state transitions |
| `src/fanops/digest.py` | Render `ledger_digest.md` from ledger |
| `src/fanops/registry.py` | Account registry load/validate; lane-uniqueness enforcement |
| `src/fanops/config.py` | Paths + env loading (`.env`), `blotato-api-key` |
| `src/fanops/ingest.py` | Channels a/b/c: drop, download (yt-dlp), local scan; catalogue + de-dupe; **PII exclusion** |
| `src/fanops/clip.py` | ffmpeg-based clipping; logs in/out, source, hook type |
| `src/fanops/tag.py` | Apply controlled taxonomy; route by `account_fit`; tier→treatment budget |
| `src/fanops/variant.py` | Per-spec variant generation (hook+caption+lever+sound+aspect), ffmpeg reframe |
| `src/fanops/caption.py` | Per-variant caption + brand-risk/bravado check → **first-class hold on Variant** |
| `src/fanops/tagging.py` | **Subtle artist @mention injection + cross-account non-synchronization (ledger-tracked)** |
| `src/fanops/schedule.py` | Route variants → accounts; **per-account opsec anchors** (no synchronized pulse); skip held |
| `src/fanops/post/__init__.py` | `Poster` interface + factory (mcp/rest/dryrun) |
| `src/fanops/post/payload.py` | **Two builders**: nested REST body + flat MCP args; per-platform required-target fields |
| `src/fanops/post/media.py` | **Upload variant files to Blotato (presigned URL) → public mediaUrl** |
| `src/fanops/post/dryrun.py` | Writes intended payload (with media), posts nothing |
| `src/fanops/post/blotato_rest.py` | Verified Blotato v2 REST client (fallback); uploads media first |
| `src/fanops/post/blotato_mcp.py` | MCP adapter (primary); flat args; `blotato_create_post` |
| `src/fanops/post/run.py` | Publish queue; advance survivors; move held out of queue |
| `src/fanops/track.py` | **Pull real metrics via Blotato `blotato_list_posts`**; weight saves/shares/retention |
| `src/fanops/adjust.py` | Classify winners/losers; **amplify (enqueue new specs in winner's vein) + retire lineage** |
| `src/fanops/report.py` | Weekly digest with ≤3 decisions for Moh; **surfaces variant-level holds** |
| `src/fanops/research.py` | Research runner: **discover + read Moh's real files** → compact brief in `07_research/` |
| `src/fanops/cli.py` | `fanops <stage>` entrypoints + `run-pipeline` |
| `tests/…` | One test module per source module |
| `pyproject.toml`, `.gitignore`, `.env.example` | Project config |

**Module boundary rule:** every stage module exposes one primary function taking the ledger + config and returning the ledger, so `cli.py` and tests compose them uniformly. Stage code imports only `ledger`, `models`, `config`, `ids` (plus `tag`/`tagging` helpers where noted) — never another stage's primary function.

**Keystone model change (fixes the "atomize" thesis):** the unit of work is a **VariantSpec** — `{hook_type, caption, lever, sound, aspect}` — authored per clip. A clip's variants are *creatively distinct* (different hooks/captions/levers), not the same cut in N aspect ratios. **Tier governs how many distinct specs** the agent authors (filler 1, volume ~3, hero ~6), i.e. conceptualization budget, exactly as the brief intends. The old `make_variants(led, cfg, clip_id)` that rotated only `fmt` is replaced by `make_variants(led, cfg, clip_id, specs)`.

**Holds are first-class:** brand-risk holds live on the `Variant` (`held: bool`, `held_reason`), not as a caption prefix. `schedule` skips held variants; `digest`/`report` scan variants (and posts) for holds so a caption-time hold actually reaches Moh.

---

## Task 1: Project skeleton, git, gitignore, brief

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/fanops/__init__.py`, `MohFlow-FanOps/00_control/BRIEF.md`
- Create: all `MohFlow-FanOps/{00_control,01_inbox,02_assets,03_clips,04_variants,05_scheduled,06_published,07_research,08_reports}/.gitkeep`

- [ ] **Step 1: Initialize git and Python project**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
git init
mkdir -p src/fanops tests MohFlow-FanOps docs/superpowers/plans
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "fanops"
version = "0.1.0"
description = "MOH FLOW FAN OPS — autonomous content repurposing engine"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "requests>=2.31",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]

[project.scripts]
fanops = "fanops.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
# secrets — agent NEVER commits credentials
.env
*.key
*-credentials.json
.mcp-credentials/

# content bank — large + private, backed up separately, never committed
MohFlow-FanOps/01_inbox/*
MohFlow-FanOps/02_assets/*
MohFlow-FanOps/03_clips/*
MohFlow-FanOps/04_variants/*
!MohFlow-FanOps/*/.gitkeep

# python
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
.venv/
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Moh provides the real value at Blotato-connection time (Human-only step 2).
# Keep trailing "=" padding if present — stripping it causes 401s.
BLOTATO_API_KEY=
# Posting backend: dryrun | rest | mcp  (defaults to dryrun until a key exists)
FANOPS_POSTER=dryrun
```

- [ ] **Step 5: Create folder tree with .gitkeep, copy brief**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts/MohFlow-FanOps"
for d in 00_control 01_inbox 02_assets 03_clips 04_variants 05_scheduled 06_published 07_research 08_reports; do
  mkdir -p "$d" && touch "$d/.gitkeep"
done
touch src/fanops/__init__.py 2>/dev/null || true
```
Then write the verbatim brief (the full text the user provided) into `MohFlow-FanOps/00_control/BRIEF.md`. Use the Write tool with the complete brief content.

- [ ] **Step 6: Install dev dependencies**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
python3 -m pip install -e ".[dev]"
```
Expected: successful install of fanops + pydantic, requests, python-dotenv, pytest, pytest-mock.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: scaffold fanops project, folder tree, gitignore, brief"
```

---

## Task 2: Deterministic IDs

**Files:**
- Create: `src/fanops/ids.py`
- Test: `tests/test_ids.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ids.py
from fanops.ids import make_id, child_id

def test_make_id_is_deterministic_for_same_source():
    a = make_id("asset", "/inbox/clip 01.mov")
    b = make_id("asset", "/inbox/clip 01.mov")
    assert a == b
    assert a.startswith("asset_")

def test_make_id_differs_for_different_source():
    assert make_id("asset", "a.mov") != make_id("asset", "b.mov")

def test_child_id_embeds_parent_and_index():
    parent = make_id("asset", "x.mov")
    c0 = child_id("clip", parent, 0)
    c1 = child_id("clip", parent, 1)
    assert c0 != c1
    assert c0.startswith("clip_")
    # deterministic
    assert c0 == child_id("clip", parent, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.ids'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ids.py
"""Deterministic, collision-resistant IDs so re-running stages is idempotent."""
import hashlib

def _hash(*parts: str) -> str:
    h = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()
    return h[:12]

def make_id(kind: str, source: str) -> str:
    """ID for a top-level unit, derived from its source identity."""
    return f"{kind}_{_hash(kind, source)}"

def child_id(kind: str, parent_id: str, index: int) -> str:
    """ID for a child unit, derived from parent + position."""
    return f"{kind}_{_hash(kind, parent_id, str(index))}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ids.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ids.py tests/test_ids.py
git commit -m "feat: deterministic id generation for atomic units"
```

---

## Task 3: Tag vocabulary + unit models

**Files:**
- Create: `src/fanops/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError
from fanops.models import (
    Asset, Clip, Variant, Post, Tags, State, VariantSpec,
    ContentType, Energy, Fmt, HookType, Lever, Tier, LengthBucket, PostStatus,
)

def test_tags_accepts_controlled_vocabulary():
    t = Tags(
        content_type=ContentType.performance,
        energy=Energy.bravado,
        fmt=Fmt.r9x16,
        hook_type=HookType.cold_open,
        length=LengthBucket.s8_15,
        lever=Lever.bilingual,
        account_fit=["edits"],
        tier=Tier.hero,
        song="Intro",
        era="2025",
    )
    assert t.energy is Energy.bravado

def test_tags_rejects_unknown_energy():
    with pytest.raises(ValidationError):
        Tags(content_type=ContentType.lyric, energy="grumpy", fmt=Fmt.r9x16,
             hook_type=HookType.loop, length=LengthBucket.le7, lever=Lever.en,
             account_fit=["lyrics"], tier=Tier.filler)

def test_asset_clip_variant_post_parent_chain():
    a = Asset(id="asset_x", state=State.catalogued, source_path="/in/x.mov", meta={})
    c = Clip(id="clip_x0", parent_id=a.id, state=State.clipped,
             start=0.0, end=7.0, raw_hook="cold-open")
    v = Variant(id="var_x0a", parent_id=c.id, state=State.varied,
                path="/var/x0a.mp4", tags=None)
    p = Post(id="post_x0a1", parent_id=v.id, state=State.queued,
             account="edits", platform="instagram", caption="hook line",
             scheduled_time="2026-06-01T18:00:00Z", status=PostStatus.queued)
    assert c.parent_id == a.id and v.parent_id == c.id and p.parent_id == v.id

def test_variant_spec_carries_distinct_creative():
    from fanops.models import VariantSpec
    s = VariantSpec(hook_type=HookType.text_hook, caption="they said I couldn't.",
                    aspect=Fmt.r1x1, lever=Lever.bilingual, sound="original")
    assert s.hook_type is HookType.text_hook
    assert s.caption == "they said I couldn't."
    assert s.lever is Lever.bilingual

def test_variant_hold_is_first_class():
    v = Variant(id="v1", parent_id="c1", path="/v/v1.mp4",
                held=True, held_reason="off-brand: begging")
    assert v.held is True
    assert v.held_reason == "off-brand: begging"
    assert v.tagged_artist is False

def test_post_carries_media_urls():
    p = Post(id="p1", parent_id="v1", account="@a", platform="instagram",
             caption="x", media_urls=["https://h/v.mp4"])
    assert p.media_urls == ["https://h/v.mp4"]

def test_state_order_is_canonical():
    assert State.order() == [
        "inbox", "catalogued", "clipped", "tagged",
        "varied", "captioned", "queued", "published", "analyzed",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/models.py
"""Controlled tag vocabulary (brief §7) + Asset→Clip→Variant→Post models."""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class State(str, Enum):
    inbox = "inbox"
    catalogued = "catalogued"
    clipped = "clipped"
    tagged = "tagged"
    varied = "varied"
    captioned = "captioned"
    queued = "queued"
    published = "published"
    analyzed = "analyzed"

    @staticmethod
    def order() -> list[str]:
        return [s.value for s in State]


class ContentType(str, Enum):
    performance = "performance"; bts = "BTS"; lyric = "lyric"
    visual_edit = "visual-edit"; candid = "candid"; interview = "interview"
    studio = "studio"; live = "live"; aesthetic = "aesthetic"

class Energy(str, Enum):
    bravado = "bravado"; hype = "hype"; intimate = "intimate"
    cinematic = "cinematic"; playful = "playful"

class Fmt(str, Enum):
    r9x16 = "9:16"; r1x1 = "1:1"; r4x3 = "4:3"; r16x9 = "16:9"

class HookType(str, Enum):
    cold_open = "cold-open"; text_hook = "text-hook"; loop = "loop"
    beat_drop = "beat-drop"; question = "question"
    pattern_interrupt = "pattern-interrupt"; cosign = "cosign"

class LengthBucket(str, Enum):
    le7 = "<=7s"; s8_15 = "8-15s"; s16_30 = "16-30s"; s30plus = "30s+"

class Lever(str, Enum):
    en = "EN"; ar = "AR"; bilingual = "bilingual"
    diaspora = "diaspora-coded"; universal = "universal"

class Tier(str, Enum):
    hero = "hero"; volume = "volume"; filler = "filler"

class PostStatus(str, Enum):
    queued = "queued"; submitted = "submitted"; published = "published"
    failed = "failed"; held = "held"  # held = brand-risk flag


class Tags(BaseModel):
    content_type: ContentType
    energy: Energy = Energy.bravado            # bravado is the default through-line
    fmt: Fmt
    hook_type: HookType
    length: LengthBucket
    lever: Lever = Lever.universal
    account_fit: list[str] = Field(default_factory=list)
    tier: Tier = Tier.filler
    song: Optional[str] = None
    project: Optional[str] = None
    era: Optional[str] = None


class Asset(BaseModel):
    id: str
    state: State = State.catalogued
    source_path: str
    source_origin: str = "drop"   # drop | download | scan
    sha256: Optional[str] = None
    meta: dict = Field(default_factory=dict)

class Clip(BaseModel):
    id: str
    parent_id: str
    state: State = State.clipped
    start: float
    end: float
    raw_hook: Optional[str] = None
    path: Optional[str] = None

class VariantSpec(BaseModel):
    """The agent's per-variant creative decision. Tier governs how MANY of these
    exist per clip (conceptualization budget), and each is creatively distinct —
    different hook/caption/lever, not just a different aspect ratio."""
    hook_type: HookType
    caption: str
    aspect: Fmt = Fmt.r9x16
    lever: Lever = Lever.universal
    sound: Optional[str] = None          # sound/audio choice (e.g. "trending:xyz", "original")

class Variant(BaseModel):
    id: str
    parent_id: str
    state: State = State.varied
    path: str
    tags: Optional[Tags] = None
    caption: Optional[str] = None        # final caption (after tagging injection)
    sound: Optional[str] = None
    held: bool = False                   # brand-risk hold — first-class, not a caption prefix
    held_reason: Optional[str] = None
    tagged_artist: bool = False          # whether the subtle artist @mention was injected

class Post(BaseModel):
    id: str
    parent_id: str
    state: State = State.queued
    account: str
    platform: str
    caption: str
    media_urls: list[str] = Field(default_factory=list)   # Blotato-hosted public URLs
    scheduled_time: Optional[str] = None
    status: PostStatus = PostStatus.queued
    submission_id: Optional[str] = None
    public_url: Optional[str] = None
    metrics: dict = Field(default_factory=dict)
    held_reason: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/models.py tests/test_models.py
git commit -m "feat: tag vocabulary, units, VariantSpec, first-class holds, media_urls"
```

---

## Task 4: Config + paths

**Files:**
- Create: `src/fanops/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from fanops.config import Config

def test_config_resolves_stage_dirs(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.inbox == tmp_path / "MohFlow-FanOps" / "01_inbox"
    assert cfg.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"
    assert cfg.scheduled == tmp_path / "MohFlow-FanOps" / "05_scheduled"

def test_poster_backend_defaults_to_dryrun_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    assert cfg.poster_backend == "dryrun"

def test_poster_backend_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "abc=")
    cfg = Config(root=tmp_path)
    assert cfg.poster_backend == "rest"
    assert cfg.blotato_api_key == "abc="
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

_STAGE = {
    "control": "00_control", "inbox": "01_inbox", "assets": "02_assets",
    "clips": "03_clips", "variants": "04_variants", "scheduled": "05_scheduled",
    "published": "06_published", "research": "07_research", "reports": "08_reports",
}

class Config:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else Path.cwd()
        load_dotenv(self.root / ".env")
        self.base = self.root / "MohFlow-FanOps"
        for attr, name in _STAGE.items():
            setattr(self, attr, self.base / name)
        self.ledger_path = self.control / "ledger.json"
        self.digest_path = self.control / "ledger_digest.md"
        self.accounts_path = self.control / "accounts.json"

    @property
    def blotato_api_key(self) -> str | None:
        return os.getenv("BLOTATO_API_KEY") or None

    @property
    def poster_backend(self) -> str:
        explicit = os.getenv("FANOPS_POSTER")
        if explicit:
            return explicit
        return "dryrun"  # safe default until Moh connects Blotato
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat: config and filesystem layout with dryrun-safe poster default"
```

---

## Task 5: Ledger — load, save, append, de-dupe, transitions

**Files:**
- Create: `src/fanops/ledger.py`
- Test: `tests/test_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py
import pytest
from fanops.config import Config
from fanops.models import Asset, State
from fanops.ledger import Ledger

def test_new_ledger_is_empty(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    assert led.assets == {} and led.clips == {} and led.variants == {} and led.posts == {}

def test_add_and_roundtrip(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_asset(Asset(id="asset_1", source_path="/x.mov"))
    led.save()
    again = Ledger.load(cfg)
    assert "asset_1" in again.assets
    assert again.assets["asset_1"].source_path == "/x.mov"

def test_add_asset_is_idempotent_by_id(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_asset(Asset(id="asset_1", source_path="/x.mov"))
    led.add_asset(Asset(id="asset_1", source_path="/x.mov"))
    assert len(led.assets) == 1

def test_already_seen_detects_duplicate_source(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_asset(Asset(id="asset_1", source_path="/x.mov", sha256="deadbeef"))
    assert led.already_seen(sha256="deadbeef") is True
    assert led.already_seen(sha256="other") is False

def test_set_state_advances_unit(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_asset(Asset(id="asset_1", source_path="/x.mov"))
    led.set_state("asset_1", State.clipped)
    assert led.assets["asset_1"].state is State.clipped

def test_units_in_state_filters(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_asset(Asset(id="a1", source_path="/1", state=State.catalogued))
    led.add_asset(Asset(id="a2", source_path="/2", state=State.clipped))
    ids = [a.id for a in led.assets_in_state(State.catalogued)]
    assert ids == ["a1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ledger.py
"""Single source of truth. One JSON doc, four id->unit maps, git-versioned."""
from __future__ import annotations
import json
from typing import Iterable
from fanops.config import Config
from fanops.models import Asset, Clip, Variant, Post, State


class Ledger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.assets: dict[str, Asset] = {}
        self.clips: dict[str, Clip] = {}
        self.variants: dict[str, Variant] = {}
        self.posts: dict[str, Post] = {}

    # ---- persistence ----
    @classmethod
    def load(cls, cfg: Config) -> "Ledger":
        led = cls(cfg)
        p = cfg.ledger_path
        if p.exists():
            raw = json.loads(p.read_text())
            led.assets = {k: Asset(**v) for k, v in raw.get("assets", {}).items()}
            led.clips = {k: Clip(**v) for k, v in raw.get("clips", {}).items()}
            led.variants = {k: Variant(**v) for k, v in raw.get("variants", {}).items()}
            led.posts = {k: Post(**v) for k, v in raw.get("posts", {}).items()}
        return led

    def save(self) -> None:
        self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "assets": {k: v.model_dump() for k, v in self.assets.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "variants": {k: v.model_dump() for k, v in self.variants.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
        }
        self.cfg.ledger_path.write_text(json.dumps(doc, indent=2, default=str))

    # ---- mutation (idempotent by id) ----
    def add_asset(self, a: Asset) -> None: self.assets.setdefault(a.id, a)
    def add_clip(self, c: Clip) -> None: self.clips.setdefault(c.id, c)
    def add_variant(self, v: Variant) -> None: self.variants.setdefault(v.id, v)
    def add_post(self, p: Post) -> None: self.posts.setdefault(p.id, p)

    def set_state(self, unit_id: str, state: State) -> None:
        for store in (self.assets, self.clips, self.variants, self.posts):
            if unit_id in store:
                store[unit_id].state = state
                return
        raise KeyError(unit_id)

    # ---- queries ----
    def already_seen(self, *, sha256: str | None = None,
                     source_path: str | None = None) -> bool:
        for a in self.assets.values():
            if sha256 and a.sha256 == sha256:
                return True
            if source_path and a.source_path == source_path:
                return True
        return False

    def assets_in_state(self, state: State) -> list[Asset]:
        return [a for a in self.assets.values() if a.state is state]

    def clips_in_state(self, state: State) -> list[Clip]:
        return [c for c in self.clips.values() if c.state is state]

    def variants_in_state(self, state: State) -> list[Variant]:
        return [v for v in self.variants.values() if v.state is state]

    def posts_in_state(self, state: State) -> list[Post]:
        return [p for p in self.posts.values() if p.state is state]

    def children_of(self, parent_id: str) -> Iterable:
        for store in (self.clips, self.variants, self.posts):
            for u in store.values():
                if u.parent_id == parent_id:
                    yield u
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ledger.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ledger.py tests/test_ledger.py
git commit -m "feat: git-versioned json ledger with dedupe and state transitions"
```

---

## Task 6: Ledger digest (human-readable view)

**Files:**
- Create: `src/fanops/digest.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest.py
from fanops.config import Config
from fanops.models import Asset, Clip, State, PostStatus, Post, Variant
from fanops.ledger import Ledger
from fanops.digest import render_digest

def test_digest_counts_units_by_state(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_asset(Asset(id="a1", source_path="/1", state=State.catalogued))
    led.add_clip(Clip(id="c1", parent_id="a1", start=0, end=5, state=State.clipped))
    md = render_digest(led)
    assert "# FAN OPS Ledger Digest" in md
    assert "Assets" in md and "Clips" in md
    assert "catalogued" in md

def test_digest_lists_held_posts(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="edits", platform="instagram",
                      caption="x", status=PostStatus.held, held_reason="off-brand"))
    md = render_digest(led)
    assert "Brand-risk holds" in md
    assert "off-brand" in md

def test_digest_surfaces_held_VARIANTS_not_just_posts(tmp_path):
    # The caption-time hold (the common case) lives on the Variant and must reach Moh.
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_variant(Variant(id="v9", parent_id="c9", path="/v/v9.mp4",
                            held=True, held_reason="off-brand: begging"))
    md = render_digest(led)
    assert "Brand-risk holds" in md
    assert "variant `v9`" in md
    assert "begging" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.digest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/digest.py
"""Render a human-readable Markdown digest so Moh can eyeball the ledger."""
from collections import Counter
from fanops.ledger import Ledger
from fanops.models import PostStatus


def _counts(units) -> str:
    c = Counter(u.state.value for u in units)
    if not c:
        return "  (none)\n"
    return "".join(f"  - {state}: {n}\n" for state, n in sorted(c.items()))


def held_holds(led: Ledger) -> list[str]:
    """All brand-risk holds, from BOTH variants (caption-time holds — the common case)
    and posts (post-time holds). Without the variant scan, caption holds vanish."""
    lines = []
    for v in led.variants.values():
        if getattr(v, "held", False):
            lines.append(f"- variant `{v.id}` (clip {v.parent_id}): {v.held_reason}")
    for p in led.posts.values():
        if p.status is PostStatus.held:
            lines.append(f"- post `{p.id}` [{p.account}/{p.platform}]: {p.held_reason}")
    return lines


def render_digest(led: Ledger) -> str:
    out = ["# FAN OPS Ledger Digest\n"]
    out.append(f"\n**Assets** ({len(led.assets)}):\n" + _counts(led.assets.values()))
    out.append(f"\n**Clips** ({len(led.clips)}):\n" + _counts(led.clips.values()))
    out.append(f"\n**Variants** ({len(led.variants)}):\n" + _counts(led.variants.values()))
    out.append(f"\n**Posts** ({len(led.posts)}):\n" + _counts(led.posts.values()))

    holds = held_holds(led)
    if holds:
        out.append("\n## Brand-risk holds (need Moh)\n")
        out.append("\n".join(holds) + "\n")
    return "".join(out)


def write_digest(led: Ledger) -> None:
    led.cfg.digest_path.write_text(render_digest(led))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat: ledger digest surfaces held variants AND held posts"
```

---

## Task 7: Account registry + lane-uniqueness opsec rule

**Files:**
- Create: `src/fanops/registry.py`, `MohFlow-FanOps/00_control/accounts.json` (seed)
- Test: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import json, pytest
from fanops.config import Config
from fanops.registry import Registry, DuplicateLaneError

def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}, indent=2))

def test_load_registry(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "lane": "edits", "platforms": ["instagram"],
                 "status": "warming", "access": "blotato", "warmup_date": None}])
    reg = Registry.load(cfg)
    assert reg.accounts[0].handle == "@a"
    assert reg.accounts[0].lane == "edits"

def test_registry_rejects_duplicate_lane(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "lane": "edits", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None},
        {"handle": "@b", "lane": "edits", "platforms": ["tiktok"],
         "status": "active", "access": "blotato", "warmup_date": None},
    ])
    with pytest.raises(DuplicateLaneError):
        Registry.load(cfg)

def test_registry_never_stores_secrets(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "lane": "edits", "platforms": ["instagram"],
                 "status": "active", "access": "blotato", "warmup_date": None}])
    reg = Registry.load(cfg)
    dumped = reg.accounts[0].model_dump()
    assert not any(k in dumped for k in ("password", "secret", "token", "credential"))

def test_active_accounts_for_lane(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "lane": "edits", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None},
        {"handle": "@b", "lane": "lyrics", "platforms": ["tiktok"],
         "status": "warming", "access": "blotato", "warmup_date": None},
    ])
    reg = Registry.load(cfg)
    assert [a.handle for a in reg.active_for_lane("edits")] == ["@a"]
    assert reg.active_for_lane("lyrics") == []   # warming, not active
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/registry.py
"""Account registry — non-secret metadata only. Enforces the no-two-same-lane rule."""
from __future__ import annotations
import json
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from fanops.config import Config


class AccountStatus(str, Enum):
    planned = "planned"; warming = "warming"; active = "active"; retired = "retired"


class Account(BaseModel):
    handle: str
    lane: str                                  # one-line content emphasis (style lane)
    platforms: list[str] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.planned
    access: str = "blotato"                    # access METHOD, never the credential
    warmup_date: Optional[str] = None
    positioning: Optional[str] = None
    cadence: Optional[str] = None


class DuplicateLaneError(ValueError):
    pass


class Registry:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.accounts: list[Account] = []

    @classmethod
    def load(cls, cfg: Config) -> "Registry":
        reg = cls(cfg)
        if cfg.accounts_path.exists():
            raw = json.loads(cfg.accounts_path.read_text())
            reg.accounts = [Account(**a) for a in raw.get("accounts", [])]
            reg._check_lanes()
        return reg

    def _check_lanes(self) -> None:
        seen: set[str] = set()
        for a in self.accounts:
            if a.status is AccountStatus.retired:
                continue
            if a.lane in seen:
                raise DuplicateLaneError(
                    f"Lane '{a.lane}' used by more than one active account — "
                    "violates opsec differentiation rule (brief §3.1)."
                )
            seen.add(a.lane)

    def save(self) -> None:
        self._check_lanes()
        self.cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.accounts_path.write_text(
            json.dumps({"accounts": [a.model_dump() for a in self.accounts]},
                       indent=2, default=str))

    def active_for_lane(self, lane: str) -> list[Account]:
        return [a for a in self.accounts
                if a.lane == lane and a.status is AccountStatus.active]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_registry.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Seed an empty registry**

Write `MohFlow-FanOps/00_control/accounts.json`:
```json
{
  "accounts": [
    {"handle": "@TBD-edits", "lane": "edits/visual", "platforms": ["instagram", "tiktok"], "status": "planned", "access": "blotato", "warmup_date": null, "positioning": "fast cinematic edits", "cadence": "3-5/day"},
    {"handle": "@TBD-lyrics", "lane": "lyrics/text", "platforms": ["instagram", "tiktok"], "status": "planned", "access": "blotato", "warmup_date": null, "positioning": "lyric cards + text hooks", "cadence": "2-4/day"},
    {"handle": "@TBD-raw", "lane": "raw & BTS", "platforms": ["tiktok", "instagram"], "status": "planned", "access": "blotato", "warmup_date": null, "positioning": "unpolished studio/behind-the-scenes", "cadence": "2-3/day"}
  ]
}
```
(`@TBD-*` handles are placeholders — Moh replaces with real handles at the Human-only account-creation step. `planned` status keeps them out of rotation until then.)

- [ ] **Step 6: Commit**

```bash
git add src/fanops/registry.py tests/test_registry.py MohFlow-FanOps/00_control/accounts.json
git commit -m "feat: account registry with lane-uniqueness opsec enforcement"
```

---

## Task 8: Ingest — catalogue, hash, de-dupe (channel a: drops)

**Files:**
- Create: `src/fanops/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State
from fanops.ingest import ingest_drops, sha256_of, is_excluded

def _put(p: Path, content: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)

def test_sha256_stable(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"hello")
    assert sha256_of(f) == sha256_of(f)

def test_ingest_catalogues_inbox_files(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "clip one.mov", b"VID1")
    led = Ledger.load(cfg)
    led = ingest_drops(led, cfg)
    assert len(led.assets) == 1
    a = next(iter(led.assets.values()))
    assert a.state is State.catalogued
    assert a.source_origin == "drop"
    assert a.sha256 is not None

def test_ingest_is_idempotent_and_dedupes(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "a.mov", b"SAME")
    _put(cfg.inbox / "copy_of_a.mov", b"SAME")   # identical bytes, different name
    led = ingest_drops(Ledger.load(cfg), cfg)
    # both files, but same sha -> one logical asset
    assert len(led.assets) == 1
    # re-running adds nothing
    led = ingest_drops(led, cfg)
    assert len(led.assets) == 1

def test_ingest_ignores_non_media(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "notes.txt", b"hi")
    _put(cfg.inbox / "real.mp4", b"VID")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.assets) == 1

def test_is_excluded_flags_pii_and_legal():
    assert is_excluded("Moh Flow passport & ID.zip") is True
    assert is_excluded("Moh Flow _ Agreement - Dubai Artist Accelerator.pdf") is True
    assert is_excluded("Moh Flow_Invoice_ExpoBeat.xlsx") is True
    assert is_excluded("adidas - day 01 moh flow.MOV") is False
    assert is_excluded("Moh Flow - Lowkey lyrics.pdf") is False

def test_ingest_skips_pii_files(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "passport scan.jpg", b"SENSITIVE")   # PII image — must be skipped
    _put(cfg.inbox / "real performance.mp4", b"VID")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.assets) == 1
    assert next(iter(led.assets.values())).meta["original_name"] == "real performance.mp4"

def test_ingest_records_origin(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "x.mp4", b"V")
    led = ingest_drops(Ledger.load(cfg), cfg, origin="download")
    assert next(iter(led.assets.values())).source_origin == "download"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/ingest.py
"""Ingest channels. (a) drops in 01_inbox -> catalogued assets, deduped by content.
Only Moh's OWN content; PII/legal docs are excluded by name pattern (brief §6.1, §9)."""
from __future__ import annotations
import hashlib
import re
import shutil
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Asset, State
from fanops.ids import make_id

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
             ".jpg", ".jpeg", ".png", ".heic", ".mp3", ".wav", ".m4a"}

# Never ingest identity documents, contracts, or financial records — these are not
# "content" and must never reach a posting surface (PII exclusion, brief §9).
PII_PATTERNS = re.compile(
    r"passport|\bid\b|\bvisa\b|licen[cs]e|agreement|contract|invoice|"
    r"\bnda\b|tax|bank|ssn|emirates.?id|national.?id",
    re.IGNORECASE,
)


def is_excluded(name: str) -> bool:
    """True if a filename looks like PII/legal/financial — must not be ingested."""
    return bool(PII_PATTERNS.search(name))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_drops(led: Ledger, cfg: Config, *, origin: str = "drop") -> Ledger:
    """Catalogue every new media file in 01_inbox into 02_assets, dedupe by sha256.
    `origin` records HOW it arrived (drop|download|scan)."""
    cfg.assets.mkdir(parents=True, exist_ok=True)
    for f in sorted(cfg.inbox.rglob("*")):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        if f.suffix.lower() not in MEDIA_EXT:
            continue
        if is_excluded(f.name):                       # PII / legal / financial — skip
            continue
        digest = sha256_of(f)
        if led.already_seen(sha256=digest):
            continue
        asset_id = make_id("asset", digest)
        dest = cfg.assets / f"{asset_id}{f.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(f, dest)
        led.add_asset(Asset(
            id=asset_id, state=State.catalogued, source_path=str(dest),
            source_origin=origin, sha256=digest,
            meta={"original_name": f.name, "bytes": f.stat().st_size},
        ))
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ingest.py tests/test_ingest.py
git commit -m "feat: ingest channel a — dedupe, PII exclusion, origin tagging"
```

---

## Task 9: Ingest — download channel (b) via yt-dlp + local scan (c)

**Files:**
- Modify: `src/fanops/ingest.py`
- Test: `tests/test_ingest_download.py`

- [ ] **Step 1: Install yt-dlp (missing on this machine)**

Run:
```bash
python3 -m pip install yt-dlp
yt-dlp --version
```
Expected: a version string prints.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_ingest_download.py
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import download_source, scan_local

def test_download_source_invokes_ytdlp_and_catalogues(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)

    # Simulate yt-dlp writing a file into inbox, then assert it gets catalogued.
    produced = cfg.inbox / "downloaded.mp4"
    def fake_run(cmd, **kw):
        produced.write_bytes(b"DLVIDEO")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_run)

    led = download_source(Ledger.load(cfg), cfg, "https://youtu.be/xyz")
    assert any(a.source_origin == "download" for a in led.assets.values())

def test_scan_local_proposes_candidates_without_cataloguing(tmp_path):
    cfg = Config(root=tmp_path)
    movies = tmp_path / "Movies"; movies.mkdir()
    (movies / "raw1.mov").write_bytes(b"X")
    (movies / "raw2.mp4").write_bytes(b"Y")
    candidates = scan_local([movies])
    assert {Path(c).name for c in candidates} == {"raw1.mov", "raw2.mp4"}

def test_scan_local_excludes_pii(tmp_path):
    d = tmp_path / "Downloads"; d.mkdir()
    (d / "passport.jpg").write_bytes(b"X")
    (d / "real clip.mp4").write_bytes(b"Y")
    candidates = scan_local([d])
    assert {Path(c).name for c in candidates} == {"real clip.mp4"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_ingest_download.py -v`
Expected: FAIL with `ImportError: cannot import name 'download_source'`

- [ ] **Step 4: Add download + scan to `ingest.py`**

Append to `src/fanops/ingest.py` (the `import subprocess` runs at import time, so the test's `mocker.patch("fanops.ingest.subprocess.run", ...)` target exists):
```python
import subprocess

def download_source(led: Ledger, cfg: Config, url: str) -> Ledger:
    """Channel (b): download Moh's OWN content from a URL into 01_inbox, then catalogue
    with origin='download'. Only Moh's own IG/YT/TikTok — caller guarantees that (§6.1)."""
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(cfg.inbox / "%(title).80s.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-o", out_tmpl, "--no-playlist",
         "--merge-output-format", "mp4", url],
        check=False, capture_output=True, text=True,
    )
    return ingest_drops(led, cfg, origin="download")   # FIX: tag as download, not drop


def scan_local(roots: list[Path]) -> list[str]:
    """Channel (c): propose candidate media under given roots. Proposes only —
    cataloguing is a separate, deliberate step so the agent surfaces candidates first.
    Excludes PII/legal files so they are never even proposed."""
    found: list[str] = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if (f.is_file() and f.suffix.lower() in MEDIA_EXT
                    and not is_excluded(f.name)):
                found.append(str(f))
    return sorted(found)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_ingest_download.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/fanops/ingest.py tests/test_ingest_download.py
git commit -m "feat: ingest channels b (yt-dlp download) and c (local scan proposals)"
```

---

## Task 10: Clip — ffmpeg cutting

**Files:**
- Create: `src/fanops/clip.py`
- Test: `tests/test_clip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clip.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Asset, State
from fanops.clip import cut_clip, ffmpeg_cut_cmd

def test_ffmpeg_cut_cmd_shape(tmp_path):
    cmd = ffmpeg_cut_cmd("/in/a.mp4", "/out/c.mp4", 1.5, 8.0)
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "1.5" in cmd
    assert "-to" in cmd and "8.0" in cmd
    assert cmd[-1] == "/out/c.mp4"

def test_cut_clip_creates_clip_unit_and_calls_ffmpeg(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    a = Asset(id="asset_1", source_path=str(cfg.assets / "asset_1.mp4"),
              state=State.catalogued)
    led.add_asset(a)
    ran = {}
    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        # simulate ffmpeg producing the output file
        from pathlib import Path
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

    led, clip = cut_clip(led, cfg, asset_id="asset_1", start=0.0, end=7.0,
                         raw_hook="cold-open")
    assert clip.parent_id == "asset_1"
    assert clip.state is State.clipped
    assert clip.raw_hook == "cold-open"
    assert clip.id in led.clips
    assert ran["cmd"][0] == "ffmpeg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clip.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.clip'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/clip.py
"""Clip stage: cut atomic moments from assets with ffmpeg (local, deterministic).

The connected media MCP clipper/video_analysis is an OPTIONAL assist (best for
YouTube-hosted sources). Default path is local ffmpeg — free, no credits, offline.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, State
from fanops.ids import child_id


def ffmpeg_cut_cmd(src: str, dst: str, start: float, end: float) -> list[str]:
    # Re-encode (not stream-copy) so arbitrary cut points are frame-accurate.
    return [
        "ffmpeg", "-y", "-ss", str(start), "-to", str(end),
        "-i", src, "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart", dst,
    ]


def cut_clip(led: Ledger, cfg: Config, *, asset_id: str, start: float,
             end: float, raw_hook: str | None = None) -> tuple[Ledger, Clip]:
    asset = led.assets[asset_id]
    index = sum(1 for c in led.clips.values() if c.parent_id == asset_id)
    clip_id = child_id("clip", asset_id, index)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{clip_id}.mp4"
    subprocess.run(ffmpeg_cut_cmd(asset.source_path, str(dst), start, end),
                   check=False, capture_output=True, text=True)
    clip = Clip(id=clip_id, parent_id=asset_id, state=State.clipped,
                start=start, end=end, raw_hook=raw_hook, path=str(dst))
    led.add_clip(clip)
    led.set_state(asset_id, State.clipped)
    return led, clip
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clip.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/clip.py tests/test_clip.py
git commit -m "feat: clip stage with frame-accurate ffmpeg cutting"
```

---

## Task 11: Tag — apply taxonomy + tier-budget rules

**Files:**
- Create: `src/fanops/tag.py`
- Test: `tests/test_tag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tag.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, State, Tier, Tags, ContentType, Fmt, HookType, LengthBucket
from fanops.tag import tag_clip, length_bucket, variant_budget

def test_length_bucket_boundaries():
    assert length_bucket(5).value == "<=7s"
    assert length_bucket(7).value == "<=7s"
    assert length_bucket(12).value == "8-15s"
    assert length_bucket(25).value == "16-30s"
    assert length_bucket(40).value == "30s+"

def test_variant_budget_by_tier():
    assert variant_budget(Tier.filler) == 1
    assert variant_budget(Tier.volume) == 3
    assert variant_budget(Tier.hero) == 6

def test_tag_clip_attaches_tags_and_advances_state(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c1", parent_id="a1", start=0, end=7,
                      state=State.clipped, raw_hook="cold-open"))
    tags = Tags(content_type=ContentType.performance, fmt=Fmt.r9x16,
                hook_type=HookType.cold_open, length=LengthBucket.le7,
                account_fit=["edits/visual"], tier=Tier.hero)
    led = tag_clip(led, "c1", tags)
    assert led.clips["c1"].state is State.tagged
    # tags persisted onto the clip's meta-carrying variant later; here we store on ledger
    from fanops.tag import get_tags
    assert get_tags(led, "c1").energy.value == "bravado"  # default through-line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tag.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.tag'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/tag.py
"""Tag stage: apply controlled taxonomy to clips; tags drive routing + variant budget."""
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.models import LengthBucket, Tier, Tags, State

# clip_id -> Tags, kept on the ledger's clip via a side map serialized in clip.path? No:
# we attach tags to the clip by storing them in a module-level cache keyed per ledger.
# Simpler + persistent: stash on clip via a dedicated field-free mechanism — we store
# the Tags dict inside the clip's raw_hook? No. We add a 'tags' store on the Ledger.

_TAGS: dict[int, dict[str, Tags]] = {}  # id(ledger) -> {clip_id: Tags}


def length_bucket(seconds: float) -> LengthBucket:
    if seconds <= 7:
        return LengthBucket.le7
    if seconds <= 15:
        return LengthBucket.s8_15
    if seconds <= 30:
        return LengthBucket.s16_30
    return LengthBucket.s30plus


def variant_budget(tier: Tier) -> int:
    return {Tier.filler: 1, Tier.volume: 3, Tier.hero: 6}[tier]


def tag_clip(led: Ledger, clip_id: str, tags: Tags) -> Ledger:
    _TAGS.setdefault(id(led), {})[clip_id] = tags
    led.set_state(clip_id, State.tagged)
    return led


def get_tags(led: Ledger, clip_id: str) -> Tags:
    return _TAGS[id(led)][clip_id]
```

> **Decision note for the implementer:** the in-memory `_TAGS` cache works for a single
> session but does not persist across processes. In Task 12 we move tags onto the
> `Variant` model (which already has a `tags` field) at variant-creation time, and add a
> persisted `clip_tags` map to the ledger. Keep this module's public functions stable;
> the persistence upgrade happens in Task 12 Step 4.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tag.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/tag.py tests/test_tag.py
git commit -m "feat: tag stage — taxonomy, length buckets, tier variant budget"
```

---

## Task 12: Persist clip tags on the ledger

**Files:**
- Modify: `src/fanops/ledger.py`, `src/fanops/tag.py`
- Test: `tests/test_tag_persist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tag_persist.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, State, Tags, ContentType, Fmt, HookType, LengthBucket, Tier
from fanops.tag import tag_clip, get_tags

def test_tags_survive_save_load(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c1", parent_id="a1", start=0, end=7, state=State.clipped))
    tags = Tags(content_type=ContentType.lyric, fmt=Fmt.r1x1, hook_type=HookType.text_hook,
                length=LengthBucket.le7, account_fit=["lyrics/text"], tier=Tier.volume)
    tag_clip(led, "c1", tags)
    led.save()
    again = Ledger.load(cfg)
    assert get_tags(again, "c1").content_type is ContentType.lyric
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tag_persist.py -v`
Expected: FAIL (tags not persisted — `KeyError` from `get_tags` on the reloaded ledger)

- [ ] **Step 3: Add a persisted `clip_tags` map to the Ledger**

In `src/fanops/ledger.py`, add to `__init__`:
```python
        self.clip_tags: dict[str, dict] = {}   # clip_id -> Tags.model_dump()
```
In `load`, after the posts line:
```python
            led.clip_tags = raw.get("clip_tags", {})
```
In `save`'s `doc` dict, add:
```python
            "clip_tags": self.clip_tags,
```

- [ ] **Step 4: Rewrite `tag.py` to use the persisted map**

Replace the `_TAGS`-based body of `src/fanops/tag.py` with:
```python
# src/fanops/tag.py
"""Tag stage: apply controlled taxonomy to clips; tags drive routing + variant budget."""
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.models import LengthBucket, Tier, Tags, State


def length_bucket(seconds: float) -> LengthBucket:
    if seconds <= 7:
        return LengthBucket.le7
    if seconds <= 15:
        return LengthBucket.s8_15
    if seconds <= 30:
        return LengthBucket.s16_30
    return LengthBucket.s30plus


def variant_budget(tier: Tier) -> int:
    return {Tier.filler: 1, Tier.volume: 3, Tier.hero: 6}[tier]


def tag_clip(led: Ledger, clip_id: str, tags: Tags) -> Ledger:
    led.clip_tags[clip_id] = tags.model_dump()
    led.set_state(clip_id, State.tagged)
    return led


def get_tags(led: Ledger, clip_id: str) -> Tags:
    return Tags(**led.clip_tags[clip_id])
```

- [ ] **Step 5: Run both tag test modules**

Run: `pytest tests/test_tag.py tests/test_tag_persist.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/fanops/ledger.py src/fanops/tag.py tests/test_tag_persist.py
git commit -m "feat: persist clip tags on the ledger across save/load"
```

---

## Task 13: Variant — one creatively-distinct variant per spec (tier-capped)

**Files:**
- Create: `src/fanops/variant.py`
- Test: `tests/test_variant.py`

- [ ] **Step 1: Write the failing test**

> **KEYSTONE CHANGE (fixes findings A + E from pre-flight):** a clip's variants are now
> CREATIVELY DISTINCT, driven by a list of `VariantSpec` the agent authors — each spec is
> a different hook + caption + lever + sound + aspect. Tier governs how MANY specs the
> agent supplies (conceptualization budget), enforced by `cap_specs_for_tier`. "6 hero
> variants" = 6 genuinely different treatments the algorithm can A/B-test, NOT one cut in
> 6 aspect ratios. The caption is stamped onto each variant FROM ITS OWN SPEC here, so the
> later caption stage validates per-variant text rather than one shared string.

```python
# tests/test_variant.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, State, Tags, VariantSpec, ContentType, Fmt,
                           HookType, LengthBucket, Tier, Lever)
from fanops.tag import tag_clip
from fanops.variant import ffmpeg_reframe_cmd, make_variants, cap_specs_for_tier

def _tags(tier):
    return Tags(content_type=ContentType.performance, fmt=Fmt.r16x9,
                hook_type=HookType.cold_open, length=LengthBucket.le7,
                account_fit=["edits/visual"], tier=tier)

def _ff(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"VAR")
        class R: returncode = 0; stderr = ""
        return R()
    return mocker.patch("fanops.variant.subprocess.run", side_effect=fake_run)

def test_reframe_cmd_targets_aspect(tmp_path):
    cmd = ffmpeg_reframe_cmd("/in/c.mp4", "/out/v.mp4", "9:16")
    assert cmd[0] == "ffmpeg"
    assert any("crop" in part or "scale" in part for part in cmd)
    assert cmd[-1] == "/out/v.mp4"

def test_cap_specs_for_tier_limits_count():
    five = [VariantSpec(hook_type=HookType.loop, caption=f"c{i}") for i in range(5)]
    assert len(cap_specs_for_tier(Tier.filler, five)) == 1   # near-zero conceptualization
    assert len(cap_specs_for_tier(Tier.volume, five)) == 3
    assert len(cap_specs_for_tier(Tier.hero, five)) == 5     # cap is 6; 5 supplied -> all 5

def test_make_variants_creates_one_per_spec_each_distinct(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c1", parent_id="a1", start=0, end=7, state=State.clipped,
                      path=str(cfg.clips / "c1.mp4")))
    tag_clip(led, "c1", _tags(Tier.hero))
    _ff(mocker)
    specs = [
        VariantSpec(hook_type=HookType.cold_open, caption="no warning. just impact.",
                    aspect=Fmt.r9x16, lever=Lever.universal, sound="original"),
        VariantSpec(hook_type=HookType.text_hook, caption="they slept on this one 👀",
                    aspect=Fmt.r1x1, lever=Lever.bilingual, sound="trending:abc"),
        VariantSpec(hook_type=HookType.beat_drop, caption="wait for it.",
                    aspect=Fmt.r4x3, lever=Lever.diaspora),
    ]
    led = make_variants(led, cfg, "c1", specs)
    made = [v for v in led.variants.values() if v.parent_id == "c1"]
    assert len(made) == 3                              # one variant per spec
    # creatively DISTINCT: captions, hooks, levers, sounds, aspects all differ
    assert len({v.caption for v in made}) == 3
    assert len({v.tags.hook_type for v in made}) == 3
    assert len({v.tags.lever for v in made}) == 3
    assert len({v.tags.fmt for v in made}) == 3
    assert {v.sound for v in made} == {"original", "trending:abc", None}
    assert all(v.state is State.varied for v in made)

def test_make_variants_filler_takes_one_even_if_more_supplied(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c2", parent_id="a1", start=0, end=7, state=State.clipped,
                      path=str(cfg.clips / "c2.mp4")))
    tag_clip(led, "c2", _tags(Tier.filler))
    _ff(mocker)
    specs = [VariantSpec(hook_type=HookType.loop, caption=f"c{i}") for i in range(4)]
    led = make_variants(led, cfg, "c2", specs)
    assert len([v for v in led.variants.values() if v.parent_id == "c2"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_variant.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.variant'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/variant.py
"""Variant stage: produce CREATIVELY DISTINCT variants from per-variant specs.

Each VariantSpec = one different treatment (hook/caption/lever/sound/aspect). Tier caps
how many specs are realized (conceptualization budget — brief §1.3/§6.4). Local ffmpeg
reframe is the default; the connected media MCP `reframe`/`generate_video` tools are the
optional assist for treatments beyond crop/pad (see CLAUDE.md)."""
from __future__ import annotations
import subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Variant, VariantSpec, State, Fmt, Tags, Tier
from fanops.tag import get_tags, variant_budget   # variant_budget = canonical tier→cap
from fanops.ids import child_id

_ASPECT_FILTER = {
    "9:16": "crop=ih*9/16:ih,scale=1080:1920",
    "1:1":  "crop=ih:ih,scale=1080:1080",
    "4:3":  "crop=ih*4/3:ih,scale=1440:1080",
    "16:9": "scale=1920:1080",
}


def cap_specs_for_tier(tier: Tier, specs: list[VariantSpec]) -> list[VariantSpec]:
    """Tier governs conceptualization budget: realize at most N distinct treatments."""
    return specs[:variant_budget(tier)]


def ffmpeg_reframe_cmd(src: str, dst: str, aspect: str) -> list[str]:
    vf = _ASPECT_FILTER[aspect]
    return ["ffmpeg", "-y", "-i", src, "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]


def make_variants(led: Ledger, cfg: Config, clip_id: str,
                  specs: list[VariantSpec]) -> Ledger:
    """One creatively-distinct variant per spec, capped by the clip's tier."""
    clip = led.clips[clip_id]
    tags = get_tags(led, clip_id)
    chosen = cap_specs_for_tier(tags.tier, specs)
    cfg.variants.mkdir(parents=True, exist_ok=True)

    for i, spec in enumerate(chosen):
        var_id = child_id("var", clip_id, i)
        dst = cfg.variants / f"{var_id}.mp4"
        subprocess.run(ffmpeg_reframe_cmd(clip.path, str(dst), spec.aspect.value),
                       check=False, capture_output=True, text=True)
        # each variant's tags reflect ITS spec — different hook/lever/format per variant
        vtags = Tags(**{**tags.model_dump(),
                        "fmt": spec.aspect,
                        "hook_type": spec.hook_type,
                        "lever": spec.lever})
        led.add_variant(Variant(id=var_id, parent_id=clip_id, state=State.varied,
                                 path=str(dst), tags=vtags,
                                 caption=spec.caption, sound=spec.sound))
    led.set_state(clip_id, State.varied)
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_variant.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/variant.py tests/test_variant.py
git commit -m "feat: variant stage — one creatively-distinct variant per spec, tier-capped"
```

---

## Task 14: Caption + brand-risk (bravado) guardrail

**Files:**
- Create: `src/fanops/caption.py`
- Test: `tests/test_caption.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_caption.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Variant, State, Tags, ContentType, Fmt, HookType, LengthBucket, Tier, Lever
from fanops.caption import brand_risk_flag, validate_caption

def test_brand_risk_flags_offbrand_phrases():
    assert brand_risk_flag("sorry guys, please stream my song 🥺") is not None
    assert brand_risk_flag("link in bio, official drop from the label") is not None
    assert brand_risk_flag("cold open hits different 🔥") is None

> **FIX (finding H):** holds are now FIRST-CLASS on the Variant (`held`, `held_reason`),
> not a `[HELD: …]` caption prefix. A held variant is surfaced by the digest/report
> (which scan variants for `held`), so the one human-governance signal the brief mandates
> actually reaches Moh. Variants arrive already-captioned from their spec; this stage
> VALIDATES the caption (and can override it), then advances clean ones to `captioned`.

```python
# tests/test_caption.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Variant, State, Tags, ContentType, Fmt, HookType, LengthBucket, Tier, Lever
from fanops.caption import brand_risk_flag, validate_caption

def _v(led, vid, caption):
    tags = Tags(content_type=ContentType.performance, fmt=Fmt.r9x16,
                hook_type=HookType.cold_open, length=LengthBucket.le7,
                lever=Lever.bilingual, account_fit=["edits/visual"], tier=Tier.hero)
    led.add_variant(Variant(id=vid, parent_id="c1", state=State.varied,
                            path=f"/v/{vid}.mp4", tags=tags, caption=caption))

def test_brand_risk_flags_offbrand_phrases():
    assert brand_risk_flag("sorry guys, please stream my song 🥺") is not None
    assert brand_risk_flag("link in bio, official drop from the label") is not None
    assert brand_risk_flag("cold open hits different 🔥") is None

def test_validate_caption_advances_clean_variant(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _v(led, "v1", "this is the one. no notes.")
    led, held = validate_caption(led, "v1")
    assert led.variants["v1"].caption == "this is the one. no notes."
    assert led.variants["v1"].state is State.captioned
    assert led.variants["v1"].held is False
    assert held is False

def test_validate_caption_can_override_caption(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _v(led, "v1b", "original spec caption")
    led, held = validate_caption(led, "v1b", caption="overridden hook. period.")
    assert led.variants["v1b"].caption == "overridden hook. period."
    assert led.variants["v1b"].state is State.captioned

def test_validate_caption_holds_offbrand_as_first_class(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _v(led, "v2", "pls stream 🥺 sorry to bother")
    led, held = validate_caption(led, "v2")
    assert held is True
    v = led.variants["v2"]
    assert v.held is True                       # first-class hold, NOT a caption prefix
    assert v.held_reason is not None and "bravado" in v.held_reason
    assert v.caption == "pls stream 🥺 sorry to bother"   # caption left intact
    assert v.state is State.varied              # held variants do not advance
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_caption.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.caption'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/caption.py
"""Caption stage: validate each variant's caption against the bravado/taste guardrail.

The guardrail is a HOLD, not a gate: off-brand items get `held=True` + a reason ON THE
VARIANT and are surfaced to Moh (digest/report scan for held). Everything clean ships
(brief §6.7). Variants arrive captioned from their VariantSpec; pass `caption=` to override.
"""
from __future__ import annotations
import re
from fanops.ledger import Ledger
from fanops.models import State

# Phrases that break the bravado through-line (victimhood / begging / softness /
# main-brand "official" framing that would link the network to the artist op).
_OFFBRAND = [
    r"\bsorry\b", r"\bpls\b", r"\bplease stream\b", r"🥺", r"\bbeg(ging)?\b",
    r"\bofficial (drop|release)\b", r"\bfrom the label\b", r"\blink in bio\b",
]
_OFFBRAND_RE = re.compile("|".join(_OFFBRAND), re.IGNORECASE)


def brand_risk_flag(caption: str) -> str | None:
    """Return a reason string if the caption trips the brand-risk check, else None."""
    m = _OFFBRAND_RE.search(caption or "")
    if m:
        return f"off-brand / breaks bravado guardrail: matched '{m.group(0)}'"
    return None


def validate_caption(led: Ledger, variant_id: str, *,
                     caption: str | None = None) -> tuple[Ledger, bool]:
    """Validate (optionally override) a variant's caption. Held variants get a
    first-class `held` flag and stay 'varied'; clean ones advance to 'captioned'."""
    variant = led.variants[variant_id]
    if caption is not None:
        variant.caption = caption
    reason = brand_risk_flag(variant.caption or "")
    if reason:
        variant.held = True
        variant.held_reason = reason
        return led, True              # stays 'varied'; surfaced via digest/report
    variant.held = False
    led.set_state(variant_id, State.captioned)
    return led, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_caption.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/caption.py tests/test_caption.py
git commit -m "feat: caption validation with first-class brand-risk hold on Variant"
```

---

## Task 14b: Subtle artist tagging (non-synchronized across accounts)

> **FIX (finding C, entirely missing before):** the artist @mention is a breadcrumb, not
> a banner — subtle, staggered, and NEVER synchronized across accounts at the same time
> (brief §1.6, §3.2). This module decides PER POST whether to inject the @mention, WHERE
> (buried, not first line — first line is the hook), and enforces non-synchronization by
> tracking the last tag time per account on the ledger. Most posts go untagged; the tag
> surfaces occasionally and never on two accounts in the same window.

**Files:**
- Create: `src/fanops/tagging.py`
- Modify: `src/fanops/ledger.py` (add `tag_log`)
- Test: `tests/test_tagging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tagging.py
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Variant, State, Tags, ContentType, Fmt, HookType, LengthBucket, Tier
from fanops.tagging import inject_artist_tag, should_tag, ARTIST_HANDLE

def _v(led, vid, caption):
    tags = Tags(content_type=ContentType.performance, fmt=Fmt.r9x16,
                hook_type=HookType.cold_open, length=LengthBucket.le7,
                account_fit=["edits/visual"], tier=Tier.hero)
    led.add_variant(Variant(id=vid, parent_id="c1", state=State.captioned,
                            path=f"/v/{vid}.mp4", tags=tags, caption=caption))

def test_should_tag_is_deterministic_and_mostly_false():
    # subtle: only a minority of posts get the tag (breadcrumb, not banner)
    decisions = [should_tag(f"v{i}", "@edits", rate=0.25) for i in range(100)]
    n_true = sum(decisions)
    assert 10 <= n_true <= 45                 # roughly the rate, never "all"
    # deterministic per (variant, account)
    assert should_tag("v1", "@edits", rate=0.25) == should_tag("v1", "@edits", rate=0.25)

def test_inject_places_tag_not_on_first_line_and_logs(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _v(led, "v1", "no warning. just impact.")
    when = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    led, injected = inject_artist_tag(led, "v1", account="@edits", when=when, force=True)
    assert injected is True
    cap = led.variants["v1"].caption
    assert ARTIST_HANDLE in cap
    assert not cap.splitlines()[0].endswith(ARTIST_HANDLE)   # not jammed in the hook line
    assert led.variants["v1"].tagged_artist is True
    assert led.tag_log["@edits"] == when.isoformat().replace("+00:00", "Z")

def test_inject_blocked_when_another_account_tagged_in_same_window(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _v(led, "v1", "cap one")
    _v(led, "v2", "cap two")
    t0 = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    led, a = inject_artist_tag(led, "v1", account="@edits", when=t0, force=True)
    # second account, 30 min later -> within the no-sync window -> blocked
    from datetime import timedelta
    led, b = inject_artist_tag(led, "v2", account="@lyrics",
                               when=t0 + timedelta(minutes=30), force=True,
                               min_gap_minutes=120)
    assert a is True and b is False           # never synchronized across accounts
    assert ARTIST_HANDLE not in led.variants["v2"].caption
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tagging.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.tagging'`

- [ ] **Step 3a: Add a `tag_log` to the Ledger**

In `src/fanops/ledger.py` `__init__` add (next to `clip_tags` from Task 12):
```python
        self.tag_log: dict[str, str] = {}     # account handle -> ISO time of last artist tag
```
In `load`, after the `clip_tags` line:
```python
            led.tag_log = raw.get("tag_log", {})
```
In `save`'s `doc`, add:
```python
            "tag_log": self.tag_log,
```

- [ ] **Step 3b: Write the tagging implementation**

```python
# src/fanops/tagging.py
"""Subtle, NON-SYNCHRONIZED artist tagging (brief §1.6, §3.2). The @mention is a
breadcrumb: injected on only a minority of posts, buried (not in the hook line), and
never on two accounts within `min_gap_minutes` of each other (tracked via ledger.tag_log).
"""
from __future__ import annotations
import hashlib
from datetime import datetime
from fanops.ledger import Ledger

ARTIST_HANDLE = "@mohflow"            # subtle breadcrumb to the main account


def should_tag(variant_id: str, account: str, *, rate: float = 0.25) -> bool:
    """Deterministic per (variant, account); true for ~`rate` fraction — a minority."""
    h = int(hashlib.sha1(f"{variant_id}|{account}".encode()).hexdigest()[:8], 16)
    return (h % 1000) / 1000.0 < rate


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def inject_artist_tag(led: Ledger, variant_id: str, *, account: str,
                      when: datetime, rate: float = 0.25,
                      min_gap_minutes: int = 120, force: bool = False) -> tuple[Ledger, bool]:
    """Maybe inject the @mention. Returns (led, injected). Blocked if (a) should_tag is
    False (unless force), or (b) ANY account tagged within min_gap_minutes (no sync)."""
    variant = led.variants[variant_id]
    if not force and not should_tag(variant_id, account, rate=rate):
        return led, False
    # non-synchronization: no other account may have tagged within the window
    for other_acct, ts in led.tag_log.items():
        gap = abs((when - _parse(ts)).total_seconds()) / 60.0
        if gap < min_gap_minutes:
            return led, False                 # would synchronize with another account
    # bury the tag at the end, on its own line — never in the hook (first line)
    base = (variant.caption or "").rstrip()
    variant.caption = f"{base}\n\n{ARTIST_HANDLE}"
    variant.tagged_artist = True
    led.tag_log[account] = when.isoformat().replace("+00:00", "Z")
    return led, True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tagging.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/tagging.py src/fanops/ledger.py tests/test_tagging.py
git commit -m "feat: subtle non-synchronized artist tagging tracked on the ledger"
```

---

## Task 15: Schedule — route to accounts with opsec jitter/stagger

**Files:**
- Create: `src/fanops/schedule.py`
- Test: `tests/test_schedule.py`

- [ ] **Step 1: Write the failing test**

> **FIXES (findings #2 idempotency, #3/H held, D opsec):**
> - Post id keyed on the **variant alone** (`child_id("post", v.id, 0)`) — stable, one
>   post per variant, no dup on re-run.
> - A scheduled variant **advances to `queued`** so it is not re-scheduled next run.
> - **Held variants are skipped** (they never reach `captioned`, but we also guard `held`).
> - **Per-account opsec anchors:** each account gets its OWN daily anchor (offset from a
>   base by a per-handle hash) and its OWN seed (derived from handle+date), so the network
>   does NOT post in one synchronized burst off a single anchor — the fingerprint §3.2 warns
>   about. Posts for each account are spread within that account's window.

```python
# tests/test_schedule.py
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Variant, State, Tags, ContentType, Fmt, HookType, LengthBucket, Tier
from fanops.registry import Registry
from fanops.schedule import schedule_variants, stagger_times, account_anchor_seed

def _seed_accounts(cfg, accounts=None):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    accounts = accounts or [
        {"handle": "@edits", "lane": "edits/visual", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None}]
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _cap_variant(led, vid, fit):
    tags = Tags(content_type=ContentType.performance, fmt=Fmt.r9x16,
                hook_type=HookType.cold_open, length=LengthBucket.le7,
                account_fit=fit, tier=Tier.hero)
    led.add_variant(Variant(id=vid, parent_id="c1", state=State.captioned,
                            path=f"/v/{vid}.mp4", caption=f"cap {vid}", tags=tags))

def test_stagger_times_are_distinct_and_jittered():
    base = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    times = stagger_times(base, n=3, seed=42)
    assert len(set(times)) == 3                 # no identical timestamps
    assert all(t.endswith("Z") or "+00:00" in t for t in times)

def test_account_anchor_seed_is_per_handle_and_deterministic():
    a = account_anchor_seed("@edits", "2026-06-02")
    b = account_anchor_seed("@lyrics", "2026-06-02")
    assert a != b                               # different accounts -> different schedule
    assert a == account_anchor_seed("@edits", "2026-06-02")   # deterministic

def test_schedule_routes_and_advances_variant_to_queued(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    _cap_variant(led, "v1", ["edits/visual"])
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = [p for p in led.posts.values() if p.parent_id == "v1"]
    assert len(posts) == 1
    assert posts[0].account == "@edits" and posts[0].platform == "instagram"
    assert posts[0].state is State.queued and posts[0].scheduled_time is not None
    # variant advanced out of 'captioned' so a second run won't re-schedule it
    assert led.variants["v1"].state is State.queued

def test_schedule_post_id_stable_no_dup_on_rerun(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    _cap_variant(led, "v1", ["edits/visual"])
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    # re-mark the SAME variant captioned (simulating a sloppy re-run) and reschedule
    led.variants["v1"].state = State.captioned
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len([p for p in led.posts.values() if p.parent_id == "v1"]) == 1  # no dup

def test_schedule_skips_held_variant(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    _cap_variant(led, "vH", ["edits/visual"])
    led.variants["vH"].held = True              # held -> must not be scheduled
    led.variants["vH"].state = State.captioned  # even if somehow captioned
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values() if p.parent_id == "vH"] == []

def test_schedule_skips_variant_with_no_matching_lane(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    _cap_variant(led, "v9", ["lyrics/text"])    # no active account on this lane
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values() if p.parent_id == "v9"] == []

def test_two_accounts_get_independent_anchors(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [
        {"handle": "@edits", "lane": "edits/visual", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None},
        {"handle": "@lyrics", "lane": "lyrics/text", "platforms": ["tiktok"],
         "status": "active", "access": "blotato", "warmup_date": None},
    ])
    led = Ledger.load(cfg)
    _cap_variant(led, "ve", ["edits/visual"])
    _cap_variant(led, "vl", ["lyrics/text"])
    led = schedule_variants(led, cfg, Registry.load(cfg), base_time="2026-06-02T18:00:00Z")
    te = next(p.scheduled_time for p in led.posts.values() if p.account == "@edits")
    tl = next(p.scheduled_time for p in led.posts.values() if p.account == "@lyrics")
    assert te != tl                             # not a synchronized network pulse
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schedule.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.schedule'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/schedule.py
"""Schedule stage: route captioned, non-held variants to lane-matched accounts at
staggered times. Opsec (brief §3.2): each account has its OWN daily anchor + seed so the
network never posts on one synchronized pulse. One post per variant (stable id)."""
from __future__ import annotations
import hashlib
import random
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.registry import Registry
from fanops.models import Post, State, PostStatus
from fanops.ids import child_id


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def account_anchor_seed(handle: str, date_str: str) -> int:
    """Deterministic per-(account, day) integer — drives that account's anchor + jitter,
    so different accounts schedule differently and the same account is reproducible."""
    h = hashlib.sha1(f"{handle}|{date_str}".encode()).hexdigest()
    return int(h[:8], 16)


def stagger_times(base: datetime, n: int, seed: int) -> list[str]:
    """n distinct ISO-8601 times, each base + i*gap + jitter (deterministic by seed)."""
    rng = random.Random(seed)
    out: list[str] = []
    for i in range(n):
        gap_min = i * rng.randint(35, 95)            # uneven spacing between posts
        jitter = rng.randint(-7, 7)
        t = base + timedelta(minutes=gap_min + jitter)
        out.append(t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    return out


def _route(reg: Registry, v) -> object | None:
    for lane in (v.tags.account_fit if v.tags else []):
        actives = reg.active_for_lane(lane)
        if actives:
            return actives[0]
    return None


def schedule_variants(led: Ledger, cfg: Config, reg: Registry, *,
                      base_time: str, seed: int = 0) -> Ledger:
    base = _parse(base_time)
    date_str = base.date().isoformat()

    # group eligible variants by their routed account so each account is staggered alone
    eligible = [v for v in led.variants_in_state(State.captioned) if not v.held]
    by_account: dict[str, list] = defaultdict(list)
    routing: dict[str, object] = {}
    for v in eligible:
        target = _route(reg, v)
        if target is None:
            continue                                 # no matching active account; skip
        by_account[target.handle].append(v)
        routing[target.handle] = target

    for handle, variants in by_account.items():
        target = routing[handle]
        # per-account independent anchor (offset 0-50 min off base) + per-account seed
        acct_seed = account_anchor_seed(handle, date_str) ^ seed
        anchor = base + timedelta(minutes=acct_seed % 50)
        times = stagger_times(anchor, n=len(variants), seed=acct_seed)
        platform = target.platforms[0]
        for i, v in enumerate(variants):
            post_id = child_id("post", v.id, 0)      # stable: one post per variant
            led.add_post(Post(
                id=post_id, parent_id=v.id, state=State.queued,
                account=handle, platform=platform, caption=v.caption or "",
                scheduled_time=times[i], status=PostStatus.queued,
            ))
            led.set_state(v.id, State.queued)        # advance so it isn't re-scheduled
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schedule.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/schedule.py tests/test_schedule.py
git commit -m "feat: schedule — stable post ids, skip held, per-account opsec anchors"
```

---

## Task 16: Poster interface + dry-run backend + payload builder

**Files:**
- Create: `src/fanops/post/__init__.py`, `src/fanops/post/dryrun.py`, `src/fanops/post/payload.py`
- Test: `tests/test_post_dryrun.py`, `tests/test_payload.py`

- [ ] **Step 1: Write the failing test for the Blotato payload builder**

```python
# tests/test_payload.py
from fanops.post.payload import build_blotato_payload

def test_payload_minimal_twitter():
    p = build_blotato_payload(account_id="98432", platform="twitter",
                              text="hello", media_urls=[], scheduled_time=None)
    assert p["post"]["accountId"] == "98432"
    assert p["post"]["content"]["platform"] == "twitter"
    assert p["post"]["target"]["targetType"] == "twitter"
    # platform must equal targetType
    assert p["post"]["content"]["platform"] == p["post"]["target"]["targetType"]

def test_payload_schedule_is_root_level_not_nested():
    p = build_blotato_payload(account_id="1", platform="instagram", text="x",
                              media_urls=["https://h/v.mp4"],
                              scheduled_time="2026-06-01T18:00:00Z")
    assert p["scheduledTime"] == "2026-06-01T18:00:00Z"   # ROOT level
    assert "scheduledTime" not in p["post"]                # NOT nested
    assert p["post"]["target"]["targetType"] == "instagram"

def test_payload_instagram_reel_mediatype():
    p = build_blotato_payload(account_id="1", platform="instagram", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time=None,
                              media_type="reel")
    assert p["post"]["target"]["mediaType"] == "reel"

def test_default_target_fields_fill_required_per_platform():
    from fanops.post.payload import default_target_fields
    # TikTok: all 7 governance fields present (else 422)
    tk = default_target_fields("tiktok")
    for k in ("privacyLevel", "disabledComments", "disabledDuet", "disabledStitch",
              "isBrandedContent", "isYourBrand", "isAiGenerated"):
        assert k in tk
    # YouTube: title + privacyStatus + shouldNotifySubscribers required
    yt = default_target_fields("youtube", title="My clip")
    assert yt["title"] == "My clip" and "privacyStatus" in yt and "shouldNotifySubscribers" in yt
    # Twitter: nothing extra required
    assert default_target_fields("twitter") == {}

def test_payload_tiktok_includes_required_fields_so_it_wont_422():
    from fanops.post.payload import default_target_fields
    p = build_blotato_payload(account_id="1", platform="tiktok", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time=None,
                              extra_target=default_target_fields("tiktok"))
    assert p["post"]["target"]["privacyLevel"]            # present
    assert p["post"]["target"]["targetType"] == "tiktok"

def test_build_mcp_args_is_flat_not_nested():
    from fanops.post.payload import build_blotato_mcp_args
    a = build_blotato_mcp_args(account_id="98432", platform="instagram", text="hi",
                               media_urls=["https://h/v.mp4"],
                               scheduled_time="2026-06-02T18:00:00Z", media_type="reel")
    # MCP tool args are FLAT — no post/content/target wrapper
    assert a["accountId"] == "98432"
    assert a["platform"] == "instagram"
    assert a["text"] == "hi"
    assert a["mediaUrls"] == ["https://h/v.mp4"]
    assert a["scheduledTime"] == "2026-06-02T18:00:00Z"
    assert a["mediaType"] == "reel"
    assert "post" not in a and "content" not in a and "target" not in a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_payload.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.post.payload'`

- [ ] **Step 3: Implement BOTH payload builders + per-platform required fields**

```python
# src/fanops/post/payload.py
"""Build Blotato request bodies. TWO shapes, because they genuinely differ (verified
2026-05-31 + pre-flight): the REST API `POST /posts` is NESTED (post.content/post.target);
the official MCP `blotato_create_post` tool takes FLAT args. The MindStudio article's
shape is WRONG — ignore it.

Hard rules: content.platform == target.targetType; scheduledTime/useNextFreeSlot are
ROOT-LEVEL siblings of "post" (REST). Per-platform target fields are REQUIRED or the
post 422s: TikTok needs 7 governance flags; YouTube needs title+privacyStatus+
shouldNotifySubscribers; Facebook needs pageId."""
from __future__ import annotations


def default_target_fields(platform: str, *, title: str | None = None,
                          page_id: str | None = None,
                          media_type: str | None = None) -> dict:
    """Sane defaults for each platform's REQUIRED target fields so posts don't 422.
    Caller can override individual values via extra_target."""
    if platform == "tiktok":
        return {
            "privacyLevel": "PUBLIC_TO_EVERYONE",
            "disabledComments": False, "disabledDuet": False, "disabledStitch": False,
            "isBrandedContent": False, "isYourBrand": False, "isAiGenerated": False,
        }
    if platform == "youtube":
        return {
            "title": title or "Moh Flow",
            "privacyStatus": "public",
            "shouldNotifySubscribers": False,
        }
    if platform == "facebook":
        out: dict = {}
        if page_id:
            out["pageId"] = page_id            # REQUIRED — fetch via subaccounts endpoint
        if media_type:
            out["mediaType"] = media_type
        return out
    if platform == "instagram" and media_type:
        return {"mediaType": media_type}
    return {}


def build_blotato_payload(*, account_id: str, platform: str, text: str,
                          media_urls: list[str], scheduled_time: str | None,
                          media_type: str | None = None,
                          use_next_free_slot: bool = False,
                          extra_target: dict | None = None) -> dict:
    """NESTED REST body for POST /posts."""
    target: dict = {"targetType": platform}
    if media_type and platform in ("instagram", "facebook"):
        target["mediaType"] = media_type
    if extra_target:
        target.update(extra_target)

    payload: dict = {
        "post": {
            "accountId": account_id,
            "content": {"text": text, "mediaUrls": media_urls, "platform": platform},
            "target": target,
        }
    }
    if scheduled_time:
        payload["scheduledTime"] = scheduled_time      # ROOT level (sibling of post)
    elif use_next_free_slot:
        payload["useNextFreeSlot"] = True
    return payload


def build_blotato_mcp_args(*, account_id: str, platform: str, text: str,
                           media_urls: list[str], scheduled_time: str | None,
                           media_type: str | None = None,
                           extra: dict | None = None) -> dict:
    """FLAT args for the official `blotato_create_post` MCP tool (NOT the REST nesting)."""
    args: dict = {
        "accountId": account_id, "platform": platform,
        "text": text, "mediaUrls": media_urls,
    }
    if scheduled_time:
        args["scheduledTime"] = scheduled_time
    if media_type:
        args["mediaType"] = media_type
    if extra:
        args.update(extra)
    return args
```

- [ ] **Step 4: Write the failing test for the dry-run poster**

```python
# tests/test_post_dryrun.py
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, PostStatus
from fanops.post import get_poster
from fanops.post.dryrun import DryRunPoster

def test_get_poster_defaults_to_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    poster = get_poster(Config(root=tmp_path))
    assert isinstance(poster, DryRunPoster)

def test_dryrun_writes_payload_with_media_and_advances_no_network(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="98432", platform="instagram",
                      caption="hello world", media_urls=["https://h/v1.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z",
                      status=PostStatus.queued, state=State.queued))
    led = DryRunPoster(cfg).publish(led, "p1")
    out = cfg.scheduled / "p1.json"
    assert out.exists()
    body = json.loads(out.read_text())
    assert body["post"]["content"]["text"] == "hello world"
    # FIX: media actually flows into the payload (not hardcoded [])
    assert body["post"]["content"]["mediaUrls"] == ["https://h/v1.mp4"]
    assert led.posts["p1"].status is PostStatus.submitted
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_post_dryrun.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.post'`

- [ ] **Step 6: Implement the Poster interface, factory, and dry-run backend**

> Holds are handled UPSTREAM (held variants never become posts — `schedule_variants`
> skips them), so posters do NOT re-check for holds. Posters read `post.media_urls`
> (populated by the post stage's upload step) and per-platform target fields.

```python
# src/fanops/post/__init__.py
"""Poster interface + factory. Backends: dryrun (default), rest, mcp."""
from __future__ import annotations
from typing import Protocol
from fanops.config import Config
from fanops.ledger import Ledger


class Poster(Protocol):
    def publish(self, led: Ledger, post_id: str) -> Ledger: ...


def get_poster(cfg: Config) -> "Poster":
    backend = cfg.poster_backend
    if backend == "rest":
        from fanops.post.blotato_rest import BlotatoRestPoster
        return BlotatoRestPoster(cfg)
    if backend == "mcp":
        from fanops.post.blotato_mcp import BlotatoMcpPoster
        return BlotatoMcpPoster(cfg)
    from fanops.post.dryrun import DryRunPoster
    return DryRunPoster(cfg)
```

```python
# src/fanops/post/dryrun.py
"""Dry-run poster: writes the exact payload it WOULD send (WITH media + per-platform
target fields), posts nothing. Active until Moh connects Blotato (Human-only step 2)."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostStatus
from fanops.post.payload import build_blotato_payload, default_target_fields


class DryRunPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account, platform=post.platform, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform),
        )
        self.cfg.scheduled.mkdir(parents=True, exist_ok=True)
        (self.cfg.scheduled / f"{post_id}.json").write_text(
            json.dumps(payload, indent=2))
        post.status = PostStatus.submitted
        return led
```

- [ ] **Step 7: Run both test modules**

Run: `pytest tests/test_payload.py tests/test_post_dryrun.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add src/fanops/post tests/test_payload.py tests/test_post_dryrun.py
git commit -m "feat: poster interface, REST+MCP+flat payloads, dry-run with media"
```

---

## Task 17: Blotato REST backend (fallback path)

**Files:**
- Create: `src/fanops/post/blotato_rest.py`
- Test: `tests/test_blotato_rest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blotato_rest.py
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, PostStatus
from fanops.post.blotato_rest import BlotatoRestPoster

class _Resp:
    def __init__(self, code, body): self.status_code = code; self._b = body; self.text = str(body)
    def json(self): return self._b

def test_rest_poster_sends_correct_header_and_url(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "secret==")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="98432", platform="twitter",
                      caption="hi", scheduled_time="2026-06-01T18:00:00Z",
                      status=PostStatus.queued, state=State.queued))
    post_mock = mocker.patch("fanops.post.blotato_rest.requests.post",
                             return_value=_Resp(200, {"postSubmissionId": "sub_123"}))
    led = BlotatoRestPoster(cfg).publish(led, "p1")
    args, kwargs = post_mock.call_args
    assert args[0] == "https://backend.blotato.com/v2/posts"
    assert kwargs["headers"]["blotato-api-key"] == "secret=="     # padding preserved
    assert kwargs["json"]["post"]["accountId"] == "98432"
    assert led.posts["p1"].status is PostStatus.submitted
    assert led.posts["p1"].submission_id == "sub_123"

def test_rest_poster_marks_failed_on_4xx(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k=")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="v2", account="1", platform="twitter",
                      caption="x", status=PostStatus.queued, state=State.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post",
                 return_value=_Resp(422, {"error": "bad"}))
    led = BlotatoRestPoster(cfg).publish(led, "p2")
    assert led.posts["p2"].status is PostStatus.failed

def test_rest_poster_sends_media_urls_and_tiktok_required_fields(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k=")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p3", parent_id="v3", account="1", platform="tiktok",
                      caption="x", media_urls=["https://h/v.mp4"],
                      status=PostStatus.queued, state=State.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      return_value=_Resp(201, {"postSubmissionId": "s"}))
    BlotatoRestPoster(cfg).publish(led, "p3")
    body = pm.call_args.kwargs["json"]
    assert body["post"]["content"]["mediaUrls"] == ["https://h/v.mp4"]   # not empty
    assert body["post"]["target"]["privacyLevel"]                        # TikTok req field
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_blotato_rest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.post.blotato_rest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/blotato_rest.py
"""Blotato v2 REST backend — the fallback path for headless/cron runs.
Contract verified 2026-05-31. Reads post.media_urls (uploaded upstream) and fills
per-platform required target fields so TikTok/YouTube/FB don't 422. Holds are handled
upstream (held variants never become posts)."""
from __future__ import annotations
import requests
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostStatus
from fanops.post.payload import build_blotato_payload, default_target_fields

BASE_URL = "https://backend.blotato.com/v2"


class BlotatoRestPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise RuntimeError("BLOTATO_API_KEY missing — cannot use REST backend.")
        # keep trailing '=' padding exactly as-is (stripping causes 401s)
        self.headers = {"blotato-api-key": key, "Content-Type": "application/json"}

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account, platform=post.platform, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform),
        )
        resp = requests.post(f"{BASE_URL}/posts", headers=self.headers,
                             json=payload, timeout=30)
        if resp.status_code in (200, 201):
            post.status = PostStatus.submitted
            try:
                post.submission_id = resp.json().get("postSubmissionId")
            except Exception:
                pass
        else:
            post.status = PostStatus.failed
            post.held_reason = f"blotato {resp.status_code}: {resp.text[:200]}"
        return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_blotato_rest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/blotato_rest.py tests/test_blotato_rest.py
git commit -m "feat: blotato v2 rest backend (fallback) with verified contract"
```

---

## Task 18: Blotato MCP backend adapter (primary path)

**Files:**
- Create: `src/fanops/post/blotato_mcp.py`
- Test: `tests/test_blotato_mcp.py`

> **FIX (finding #6):** the MCP `blotato_create_post` tool takes **FLAT** args
> (`accountId`, `platform`, `text`, `mediaUrls`, `scheduledTime`, …) — NOT the nested
> REST `post{content,target}` shape. So this backend uses `build_blotato_mcp_args`, not
> `build_blotato_payload`. The runtime *agent* calls the tool directly via its MCP
> interface; this module gives the `Poster` factory a uniform `mcp` backend and documents
> the exact flat arg mapping + the connection (`https://mcp.blotato.com/mcp`, header
> `blotato-api-key`). It delegates the MCP call to an injected `tool_caller` so it's
> testable without a live connection. (Tool names: `blotato_create_post`,
> `blotato_list_accounts`, `blotato_list_posts`, `blotato_create_presigned_upload_url`,
> `blotato_list_schedules` [PLURAL], `blotato_{get,update,delete}_schedule`.) Holds are
> handled upstream — no held-check here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blotato_mcp.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, PostStatus
from fanops.post.blotato_mcp import BlotatoMcpPoster

def test_mcp_poster_builds_FLAT_tool_call_args(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="98432", platform="instagram",
                      caption="the one", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z",
                      status=PostStatus.queued, state=State.queued))
    calls = []
    def fake_tool(tool_name, args):
        calls.append((tool_name, args))
        return {"postSubmissionId": "sub_999"}
    poster = BlotatoMcpPoster(cfg, tool_caller=fake_tool)
    led = poster.publish(led, "p1")
    name, args = calls[0]
    assert name == "blotato_create_post"
    # FLAT shape — no post/content/target nesting
    assert args["accountId"] == "98432"
    assert args["platform"] == "instagram"
    assert args["mediaUrls"] == ["https://h/v.mp4"]
    assert args["scheduledTime"] == "2026-06-02T18:00:00Z"
    assert "post" not in args
    assert led.posts["p1"].status is PostStatus.submitted
    assert led.posts["p1"].submission_id == "sub_999"

def test_mcp_poster_raises_without_caller(tmp_path):
    import pytest
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="v2", account="1", platform="twitter",
                      caption="x", status=PostStatus.queued, state=State.queued))
    with pytest.raises(RuntimeError):
        BlotatoMcpPoster(cfg, tool_caller=None).publish(led, "p2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_blotato_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.post.blotato_mcp'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/blotato_mcp.py
"""Blotato MCP backend (primary). Maps a Post to the FLAT `blotato_create_post` tool args.

RUNTIME AGENT NOTE: official Blotato MCP @ https://mcp.blotato.com/mcp (header
`blotato-api-key`, or OAuth). Tools: `blotato_create_post`, `blotato_list_accounts`,
`blotato_list_posts`, `blotato_create_presigned_upload_url`, `blotato_list_schedules`,
`blotato_{get,update,delete}_schedule`. The create-post args are FLAT (NOT the nested
REST body). `tool_caller(name, args)->dict` is injected so this is unit-testable; in
production the agent wires it to the real MCP tool. No caller -> raises."""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostStatus
from fanops.post.payload import build_blotato_mcp_args, default_target_fields

ToolCaller = Callable[[str, dict], dict]


class BlotatoMcpPoster:
    def __init__(self, cfg: Config, tool_caller: ToolCaller | None = None):
        self.cfg = cfg
        self._call = tool_caller

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        if self._call is None:
            raise RuntimeError(
                "BlotatoMcpPoster needs a tool_caller wired to the live "
                "blotato_create_post MCP tool.")
        args = build_blotato_mcp_args(
            account_id=post.account, platform=post.platform, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra=default_target_fields(post.platform) or None,
        )
        result = self._call("blotato_create_post", args)
        post.status = PostStatus.submitted
        post.submission_id = (result or {}).get("postSubmissionId")
        return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_blotato_mcp.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/blotato_mcp.py tests/test_blotato_mcp.py
git commit -m "feat: blotato mcp backend — FLAT create_post args, media + target fields"
```

---

## Task 18b: Media upload to Blotato (presigned URL)

> **FIX (finding #1, CRITICAL):** without this, every post is text-only — the entire
> point is posting video. This module turns a local variant file into a Blotato-hosted
> public URL via `POST /media/uploads` → PUT binary → `publicUrl`. In dry-run it returns
> a fake `file://`-style URL so the pipeline is exercised end-to-end without network.

**Files:**
- Create: `src/fanops/post/media.py`
- Test: `tests/test_media.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media.py
from pathlib import Path
from fanops.config import Config
from fanops.post.media import upload_media, dryrun_media_url

def test_dryrun_media_url_is_local_and_no_network(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"VID")
    url = dryrun_media_url(f)
    assert url.startswith("file://") and "v.mp4" in url

def test_upload_media_does_presign_then_put(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k=")
    cfg = Config(root=tmp_path)
    f = tmp_path / "clip.mp4"; f.write_bytes(b"VIDEO-BYTES")

    class _R:
        def __init__(self, code, body=None): self.status_code = code; self._b = body or {}; self.text = str(self._b)
        def json(self): return self._b
    post_mock = mocker.patch("fanops.post.media.requests.post",
        return_value=_R(200, {"presignedUrl": "https://up/abc", "publicUrl": "https://cdn/clip.mp4"}))
    put_mock = mocker.patch("fanops.post.media.requests.put", return_value=_R(200))

    url = upload_media(cfg, f)
    assert url == "https://cdn/clip.mp4"
    # presign call carries the filename + auth header
    assert post_mock.call_args.kwargs["json"]["filename"] == "clip.mp4"
    assert post_mock.call_args.kwargs["headers"]["blotato-api-key"] == "k="
    # binary PUT to the presigned URL
    assert put_mock.call_args.args[0] == "https://up/abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.post.media'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/media.py
"""Upload a local media file to Blotato → public URL for content.mediaUrls.
Verified flow (2026-05-31): POST /media/uploads {filename} -> {presignedUrl, publicUrl};
PUT the binary to presignedUrl; use publicUrl. mediaUrls also accepts arbitrary public
URLs directly, so a hosted source can skip this."""
from __future__ import annotations
import mimetypes
from pathlib import Path
import requests
from fanops.config import Config

BASE_URL = "https://backend.blotato.com/v2"


def dryrun_media_url(path: Path) -> str:
    """Fake URL for dry-run so the pipeline runs without network/credentials."""
    return f"file://{Path(path).resolve()}"


def upload_media(cfg: Config, path: Path) -> str:
    """Return a Blotato-hosted public URL for `path`. Requires BLOTATO_API_KEY."""
    key = cfg.blotato_api_key
    if not key:
        raise RuntimeError("BLOTATO_API_KEY missing — cannot upload media.")
    headers = {"blotato-api-key": key, "Content-Type": "application/json"}
    presign = requests.post(f"{BASE_URL}/media/uploads", headers=headers,
                            json={"filename": Path(path).name}, timeout=30).json()
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        requests.put(presign["presignedUrl"], data=fh,
                     headers={"Content-Type": ctype}, timeout=120)
    return presign["publicUrl"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_media.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/media.py tests/test_media.py
git commit -m "feat: blotato media upload (presigned URL) + dry-run url"
```

---

## Task 19: Post stage — upload media, publish queue, move to 06_published

> **FIXES (findings #1 media, #3 held-retry):** before posting, populate each queued
> post's `media_urls` from its parent variant's file (real upload under rest/mcp, fake
> `file://` under dry-run). Held items can't reach here (schedule skips them); a post
> that fails to submit is moved to `analyzed` so it is NOT retried forever.

**Files:**
- Create: `src/fanops/post/run.py`
- Test: `tests/test_post_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_post_run.py
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Variant, State, PostStatus
from fanops.post.run import publish_due

def _queued_post_with_variant(led, cfg, pid, vid):
    f = cfg.variants / f"{vid}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"VID")
    led.add_variant(Variant(id=vid, parent_id="c1", state=State.queued, path=str(f),
                            caption="ship it"))
    led.add_post(Post(id=pid, parent_id=vid, account="98432", platform="instagram",
                      caption="ship it", scheduled_time="2026-06-02T18:00:00Z",
                      status=PostStatus.queued, state=State.queued))

def test_publish_due_uploads_media_and_advances(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)  # -> dryrun
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _queued_post_with_variant(led, cfg, "p1", "v1")
    led = publish_due(led, cfg)
    assert led.posts["p1"].status is PostStatus.submitted
    assert led.posts["p1"].state is State.published
    # media url populated from the variant file (dry-run file:// URL)
    assert led.posts["p1"].media_urls and led.posts["p1"].media_urls[0].startswith("file://")
    # and it reached the written payload
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    assert body["post"]["content"]["mediaUrls"][0].startswith("file://")

def test_publish_due_is_idempotent_does_not_repost(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _queued_post_with_variant(led, cfg, "p1", "v1")
    led = publish_due(led, cfg)
    # second run: p1 is now 'published', no longer queued -> not reprocessed
    n_before = led.posts["p1"].metrics.get("_publish_count", 1)
    led = publish_due(led, cfg)
    assert led.posts["p1"].state is State.published   # unchanged, not re-submitted
```

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/post/run.py
"""Post stage: for each queued post, ensure media is uploaded (real under rest/mcp,
file:// under dry-run), then publish via the configured backend and advance survivors to
'published'. A failed submit goes to 'analyzed' so it is not retried forever. No per-item
gate — held items can't reach here (schedule skips them)."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State, PostStatus
from fanops.post import get_poster
from fanops.post.media import upload_media, dryrun_media_url


def _ensure_media(led: Ledger, cfg: Config, post) -> None:
    """Populate post.media_urls from the parent variant's file if not already set."""
    if post.media_urls:
        return
    variant = led.variants.get(post.parent_id)
    if variant is None or not variant.path:
        return
    path = Path(variant.path)
    if cfg.poster_backend == "dryrun":
        post.media_urls = [dryrun_media_url(path)]
    else:
        post.media_urls = [upload_media(cfg, path)]


def publish_due(led: Ledger, cfg: Config) -> Ledger:
    poster = get_poster(cfg)
    for post in led.posts_in_state(State.queued):
        _ensure_media(led, cfg, post)
        led = poster.publish(led, post.id)
        if post.status is PostStatus.submitted:
            post.state = State.published
        elif post.status is PostStatus.failed:
            post.state = State.analyzed          # terminal — do NOT retry forever
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_post_run.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/run.py tests/test_post_run.py
git commit -m "feat: post stage — upload media, publish, advance, no infinite retry"
```

---

## Task 20: Track — record metrics with lift-weighting

**Files:**
- Create: `src/fanops/track.py`
- Test: `tests/test_track.py`

- [ ] **Step 1: Write the failing test**

> **FIX (finding F, read-end):** metrics were a hand-typed dict — the loop never PULLED.
> `pull_metrics` closes it: given an injected `list_posts(window)` callable (the runtime
> agent wires it to Blotato `blotato_list_posts`), it matches returned analytics to ledger
> posts by `submission_id` and records them. `record_metrics` stays for manual/explicit use.

```python
# tests/test_track.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, PostStatus
from fanops.track import record_metrics, lift_score, pull_metrics

def test_lift_score_weights_saves_shares_retention_over_likes():
    low_likes_high_saves = lift_score({"likes": 10, "saves": 50, "shares": 40,
                                       "retention": 0.8, "reach": 1000})
    high_likes_low_saves = lift_score({"likes": 500, "saves": 1, "shares": 0,
                                       "retention": 0.1, "reach": 1000})
    assert low_likes_high_saves > high_likes_low_saves

def test_record_metrics_writes_to_post_and_advances(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="@a", platform="instagram",
                      caption="x", status=PostStatus.published, state=State.published))
    led = record_metrics(led, "p1", {"likes": 5, "saves": 20, "shares": 12,
                                     "retention": 0.7, "reach": 800})
    assert led.posts["p1"].metrics["saves"] == 20
    assert "lift_score" in led.posts["p1"].metrics
    assert led.posts["p1"].state is State.analyzed

def test_pull_metrics_matches_by_submission_id_and_records(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="v1", account="@a", platform="instagram",
                      caption="x", status=PostStatus.published, state=State.published,
                      submission_id="sub_AAA"))
    led.add_post(Post(id="p2", parent_id="v2", account="@a", platform="tiktok",
                      caption="y", status=PostStatus.published, state=State.published,
                      submission_id="sub_BBB"))
    # injected lister returns Blotato-shaped analytics keyed by submission id
    def fake_list_posts(window):
        return [
            {"postSubmissionId": "sub_AAA",
             "metrics": {"likes": 3, "saves": 30, "shares": 25, "retention": 0.8, "reach": 900}},
            {"postSubmissionId": "sub_BBB",
             "metrics": {"likes": 50, "saves": 1, "shares": 0, "retention": 0.1, "reach": 900}},
        ]
    led = pull_metrics(led, cfg, list_posts=fake_list_posts)
    assert led.posts["p1"].state is State.analyzed
    assert led.posts["p1"].metrics["saves"] == 30
    assert led.posts["p2"].metrics["saves"] == 1
    # p1 (high saves/shares) outranks p2 (high likes only) on lift
    assert led.posts["p1"].metrics["lift_score"] > led.posts["p2"].metrics["lift_score"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_track.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.track'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/track.py
"""Track stage: pull + record per-post performance. Saves/shares/retention signal
algorithmic lift; likes are near-noise (brief §6.8). lift_score encodes that weighting.

`pull_metrics` closes the read-end of the loop: the runtime agent injects a `list_posts`
callable bound to Blotato `blotato_list_posts`; results are matched to ledger posts by
`submission_id`. (Some metrics, e.g. saves/profile-taps, may require platform analytics
beyond Blotato — extend the lister as those become available.)"""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State

# weights: saves & shares dominate, retention strong, reach mild, likes negligible
_W = {"saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05}

ListPosts = Callable[[str], list[dict]]


def lift_score(metrics: dict) -> float:
    return round(sum(_W.get(k, 0.0) * float(v) for k, v in metrics.items()
                     if isinstance(v, (int, float))), 4)


def record_metrics(led: Ledger, post_id: str, metrics: dict) -> Ledger:
    post = led.posts[post_id]
    post.metrics = {**metrics, "lift_score": lift_score(metrics)}
    post.state = State.analyzed
    return led


def pull_metrics(led: Ledger, cfg: Config, *, list_posts: ListPosts,
                 window: str = "30d") -> Ledger:
    """Fetch analytics via the injected lister and record them onto matching posts."""
    by_sub = {p.submission_id: p for p in led.posts.values() if p.submission_id}
    for row in list_posts(window):
        sub = row.get("postSubmissionId")
        post = by_sub.get(sub)
        if post is None:
            continue
        record_metrics(led, post.id, row.get("metrics", {}))
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_track.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/track.py tests/test_track.py
git commit -m "feat: track stage — real metric pull via Blotato + lift weighting"
```

---

## Task 21: Adjust — amplify winners, retire losers

**Files:**
- Create: `src/fanops/adjust.py`
- Test: `tests/test_adjust.py`

- [ ] **Step 1: Write the failing test**

> **FIX (finding F, write-end):** classification alone is a print statement. `amplify`
> turns winners into NEW `VariantSpec`s "in that vein" (same hook/lever, fresh caption
> angle) for the same clip → the agent re-enters the pipeline with them, compounding on
> what works (brief §6.9). `retire` marks the loser lineage so it isn't re-varied.

```python
# tests/test_adjust.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Post, State, PostStatus, Variant, Tags, VariantSpec,
                           ContentType, Fmt, HookType, LengthBucket, Tier, Lever)
from fanops.adjust import classify_outcomes, amplify, retire

def _post(pid, lift, parent="v1"):
    return Post(id=pid, parent_id=parent, account="@a", platform="instagram",
                caption="x", status=PostStatus.published, state=State.analyzed,
                metrics={"lift_score": lift})

def _winning_variant(led, vid="v1", clip="c1"):
    tags = Tags(content_type=ContentType.performance, fmt=Fmt.r9x16,
                hook_type=HookType.beat_drop, length=LengthBucket.le7,
                lever=Lever.diaspora, account_fit=["edits/visual"], tier=Tier.hero)
    led.add_variant(Variant(id=vid, parent_id=clip, state=State.queued,
                            path=f"/v/{vid}.mp4", tags=tags, caption="the drop 🔊"))

def test_classify_splits_winners_and_losers(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1)]:
        led.add_post(_post(pid, lift))
    result = classify_outcomes(led, winner_pct=0.5)
    assert set(result["winners"]) == {"p1", "p3"}
    assert set(result["losers"]) == {"p2", "p4"}

def test_classify_handles_empty(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    assert classify_outcomes(led, winner_pct=0.5) == {"winners": [], "losers": []}

def test_amplify_returns_new_specs_in_the_winners_vein(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _winning_variant(led, "v1", "c1")
    led.add_post(_post("p1", 400, parent="v1"))
    plan = amplify(led, ["p1"], n_each=2)
    # returns clip_id -> [VariantSpec,...] the agent feeds back into make_variants
    assert "c1" in plan
    specs = plan["c1"]
    assert len(specs) == 2
    assert all(isinstance(s, VariantSpec) for s in specs)
    # same VEIN as the winner: hook + lever carried forward
    assert all(s.hook_type is HookType.beat_drop for s in specs)
    assert all(s.lever is Lever.diaspora for s in specs)

def test_retire_marks_loser_lineage(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _winning_variant(led, "vL", "cL")
    led.add_post(_post("pL", 1, parent="vL"))
    led = retire(led, ["pL"])
    assert led.variants["vL"].tags is not None
    # retired lineage is flagged so it isn't re-varied (stored on ledger.retired)
    assert "vL" in led.retired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adjust.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.adjust'`

- [ ] **Step 3a: Add a `retired` set to the Ledger**

In `src/fanops/ledger.py` `__init__` add:
```python
        self.retired: set[str] = set()        # variant ids whose lineage is retired
```
In `load`, after `clip_tags`:
```python
            led.retired = set(raw.get("retired", []))
```
In `save`'s `doc`, add:
```python
            "retired": sorted(self.retired),
```

- [ ] **Step 3b: Write the adjust implementation**

```python
# src/fanops/adjust.py
"""Adjust stage: rank analyzed posts by lift; AMPLIFY winners into new specs in their
vein, RETIRE losers' lineage. Closes the compounding loop (brief §6.9)."""
from __future__ import annotations
from collections import defaultdict
from fanops.ledger import Ledger
from fanops.models import State, VariantSpec, Fmt

# fresh caption angles to vary a winner without changing its hook/lever
_REMIX_ANGLES = ["again, louder.", "you missed this.", "run it back.", "still undefeated."]
_ASPECT_CYCLE = [Fmt.r9x16, Fmt.r1x1, Fmt.r4x3]


def classify_outcomes(led: Ledger, *, winner_pct: float = 0.3) -> dict:
    analyzed = [p for p in led.posts.values() if p.state is State.analyzed]
    if not analyzed:
        return {"winners": [], "losers": []}
    ranked = sorted(analyzed, key=lambda p: p.metrics.get("lift_score", 0.0),
                    reverse=True)
    cut = max(1, round(len(ranked) * winner_pct))
    return {"winners": [p.id for p in ranked[:cut]],
            "losers": [p.id for p in ranked[cut:]]}


def amplify(led: Ledger, winner_post_ids: list[str], *, n_each: int = 2) -> dict:
    """For each winning post, emit n new VariantSpecs in its vein (same hook+lever,
    fresh caption/aspect), keyed by the originating CLIP id so the agent can re-run
    make_variants on that clip. Compounds on what works."""
    out: dict[str, list[VariantSpec]] = defaultdict(list)
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        variant = led.variants.get(post.parent_id)
        if variant is None or variant.tags is None:
            continue
        clip_id = variant.parent_id
        for i in range(n_each):
            out[clip_id].append(VariantSpec(
                hook_type=variant.tags.hook_type,          # same vein
                lever=variant.tags.lever,                  # same vein
                aspect=_ASPECT_CYCLE[i % len(_ASPECT_CYCLE)],
                caption=f"{variant.caption or ''} — {_REMIX_ANGLES[i % len(_REMIX_ANGLES)]}".strip(" —"),
            ))
    return dict(out)


def retire(led: Ledger, loser_post_ids: list[str]) -> Ledger:
    """Mark the losing posts' variant lineage retired so it isn't re-varied."""
    for pid in loser_post_ids:
        post = led.posts.get(pid)
        if post is not None:
            led.retired.add(post.parent_id)
    return led
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_adjust.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/adjust.py src/fanops/ledger.py tests/test_adjust.py
git commit -m "feat: adjust — amplify winners into new specs, retire loser lineage"
```

---

## Task 22: Weekly report (≤3 decisions for Moh)

**Files:**
- Create: `src/fanops/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, PostStatus
from fanops.report import weekly_report

def test_report_lists_top_movers_and_caps_decisions(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for i, lift in enumerate([500, 400, 300, 5, 2]):
        led.add_post(Post(id=f"p{i}", parent_id="v", account="@a", platform="instagram",
                          caption=f"post {i}", status=PostStatus.published,
                          state=State.analyzed, metrics={"lift_score": lift,
                          "saves": lift//4, "shares": lift//5}))
    md = weekly_report(led)
    assert "# FAN OPS Weekly Digest" in md
    assert "Top movers" in md
    assert "Decisions for Moh" in md
    # never more than 3 decisions surfaced
    assert md.count("- [ ] DECISION") <= 3

def test_report_surfaces_held_variants_as_decisions(tmp_path):
    # caption-time holds live on the Variant — they MUST become decisions for Moh
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    from fanops.models import Variant
    led.add_variant(Variant(id="v9", parent_id="c9", path="/v/v9.mp4",
                            held=True, held_reason="off-brand: begging"))
    md = weekly_report(led)
    assert "Decisions for Moh" in md
    assert "begging" in md
    assert md.count("- [ ] DECISION") <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/report.py
"""Weekly digest: what moved, what to double down, what to kill, ≤3 decisions for Moh.
Low cognitive load is the whole point (brief §8). Decisions surface brand-risk holds from
BOTH variants (caption-time) and posts — so a hold never silently vanishes."""
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.models import State, PostStatus
from fanops.adjust import classify_outcomes
from fanops.digest import held_holds


def weekly_report(led: Ledger) -> str:
    out = ["# FAN OPS Weekly Digest\n"]

    analyzed = [p for p in led.posts.values() if p.state is State.analyzed]
    ranked = sorted(analyzed, key=lambda p: p.metrics.get("lift_score", 0.0),
                    reverse=True)

    out.append("\n## Top movers\n")
    for p in ranked[:5]:
        m = p.metrics
        out.append(f"- `{p.id}` [{p.account}/{p.platform}] lift={m.get('lift_score',0)} "
                   f"(saves={m.get('saves',0)}, shares={m.get('shares',0)})\n")
    if not ranked:
        out.append("  (no analyzed posts yet)\n")

    oc = classify_outcomes(led)
    out.append("\n## Double down\n")
    out += [f"- amplify `{w}`\n" for w in oc["winners"][:5]] or ["  (none)\n"]
    out.append("\n## Kill\n")
    out += [f"- retire `{l}`\n" for l in oc["losers"][:5]] or ["  (none)\n"]

    # Decisions: brand-risk holds (variants + posts) first, capped at 3
    holds = held_holds(led)
    out.append("\n## Decisions for Moh (max 3)\n")
    decisions = [f"- [ ] DECISION: {h.lstrip('- ')} — approve, edit, or drop?\n"
                 for h in holds[:3]]
    out += decisions or ["  (none — system running clean)\n"]
    return "".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_report.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/report.py tests/test_report.py
git commit -m "feat: weekly digest with capped decisions and brand-risk surfacing"
```

---

## Task 23: Research-stage runner (compact brief generator)

**Files:**
- Create: `src/fanops/research.py`
- Test: `tests/test_research.py`

> **FIX (finding G):** the brief is emphatic — "MINE THE PROJECT FILES FIRST: Songstats
> reports, EPK, the Blueprint." The old runner never located or read anything. Now
> `discover_source_files` globs Moh's real corpus (default `~/Downloads`) by keyword,
> excludes PII (reuses `ingest.is_excluded`), and returns a ranked candidate list the
> agent reads; `RESEARCH_QUESTIONS` gives the agent an explicit per-section checklist so
> the gathering is a runnable procedure, not a hope. `write_research_brief` still emits the
> compact versioned output. (No file is literally named Songstats/EPK/Blueprint — discover,
> don't hardcode. See file memory `content-bank-location.md`.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_research.py
from pathlib import Path
from fanops.config import Config
from fanops.research import (write_research_brief, discover_source_files,
                             RESEARCH_SECTIONS, RESEARCH_QUESTIONS)

def test_discover_finds_moh_files_and_excludes_pii(tmp_path):
    d = tmp_path / "Downloads"; d.mkdir()
    (d / "Moh Flow - Lowkey lyrics.pdf").write_bytes(b"x")
    (d / "[hrmny] Moh Flow SM Approach.pdf").write_bytes(b"x")
    (d / "adidas - day 01 moh flow.MOV").write_bytes(b"x")
    (d / "random other person.pdf").write_bytes(b"x")
    (d / "Moh Flow passport & ID.zip").write_bytes(b"x")    # PII -> excluded
    found = discover_source_files([d], keywords=["moh flow", "mohflow", "songstats",
                                                  "epk", "blueprint", "approach"])
    names = {Path(f).name for f in found}
    assert "Moh Flow - Lowkey lyrics.pdf" in names
    assert "[hrmny] Moh Flow SM Approach.pdf" in names
    assert "adidas - day 01 moh flow.MOV" in names
    assert "Moh Flow passport & ID.zip" not in names       # PII never surfaced
    assert "random other person.pdf" not in names          # off-topic

def test_research_questions_cover_every_section():
    for section in RESEARCH_SECTIONS:
        assert section in RESEARCH_QUESTIONS
        assert len(RESEARCH_QUESTIONS[section]) >= 2        # at least 2 prompts each

def test_research_brief_has_required_sections(tmp_path):
    cfg = Config(root=tmp_path)
    path = write_research_brief(cfg, version=1, findings={
        "Artist": "bravado-forward, bilingual EN/AR, diaspora pull",
        "Audience & lookalikes": "high-share diaspora; underserved by generic rap fan accts",
    })
    text = path.read_text()
    assert path.parent == cfg.research and "research-v1" in path.name
    for section in RESEARCH_SECTIONS:
        assert section in text
    assert "bravado-forward" in text

def test_research_brief_versions_monotonically(tmp_path):
    cfg = Config(root=tmp_path)
    p1 = write_research_brief(cfg, version=1, findings={})
    p2 = write_research_brief(cfg, version=2, findings={})
    assert p1.name != p2.name and "v2" in p2.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.research'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/research.py
"""Research-stage runner (brief §4). DISCOVER + read Moh's real files first, then emit a
COMPACT versioned brief into 07_research/. Keep it tight — this feeds Strategy, not a book.

The agent runs `discover_source_files` to locate the corpus (Songstats/EPK/Blueprint/
SM-approach etc. — by keyword, not hardcoded name), READS them, does web research per
`RESEARCH_QUESTIONS`, then calls `write_research_brief(findings)`."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ingest import is_excluded, MEDIA_EXT

RESEARCH_SECTIONS = [
    "## Artist", "## Genre & adjacent scenes", "## Audience & lookalikes",
    "## Music-marketing mechanics", "## Fan-account strategy",
]

# Explicit per-section prompts so the agent's research is a runnable checklist (§4).
RESEARCH_QUESTIONS = {
    "## Artist": [
        "Catalogue, eras, narrative arc, visual world, voice — from Moh's own files.",
        "Who actually engages and where (read Songstats/SM-approach docs if present)?",
    ],
    "## Genre & adjacent scenes": [
        "Where does Moh sit; what are the codes; what travels across the scene?",
        "Which adjacent scenes share audience and aesthetics?",
    ],
    "## Audience & lookalikes": [
        "Psychographics; where they congregate; what they share.",
        "Arab/diaspora identity lever — high-share, under-served by generic rap fan accounts.",
    ],
    "## Music-marketing mechanics": [
        "Current platform behaviors; clip/edit trends; sound-driven discovery.",
        "What is working for comparable artists right now (web research)?",
    ],
    "## Fan-account strategy": [
        "How successful music fan accounts grow, differentiate, and hook, per style.",
        "Comparable artists' fan ecosystems — study 2-3 concretely.",
    ],
}

# Filetypes worth reading for research (docs), distinct from postable media.
_DOC_EXT = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".pages", ".xlsx", ".csv"}


def discover_source_files(roots: list[Path], *, keywords: list[str]) -> list[str]:
    """Find candidate project/content files under roots whose name matches any keyword,
    excluding PII/legal (is_excluded). Returns docs AND media — the agent reads docs and
    can route media into ingest. Discovery only; reading is the agent's next step."""
    kws = [k.lower() for k in keywords]
    found: list[str] = []
    for root in roots:
        if not Path(root).exists():
            continue
        for f in Path(root).rglob("*"):
            if not f.is_file() or is_excluded(f.name):
                continue
            ext = f.suffix.lower()
            if ext not in (_DOC_EXT | MEDIA_EXT):
                continue
            if any(k in f.name.lower() for k in kws):
                found.append(str(f))
    return sorted(found)


def write_research_brief(cfg: Config, *, version: int, findings: dict) -> Path:
    """Emit the compact versioned brief. `findings` keys are RESEARCH_SECTIONS headers."""
    cfg.research.mkdir(parents=True, exist_ok=True)
    lines = [f"# FAN OPS Research Brief — v{version}\n",
             "_Compact brief feeding Strategy (brief §4). Refresh on cadence._\n"]
    for section in RESEARCH_SECTIONS:
        lines.append(f"\n{section}\n")
        lines.append(findings.get(section, "_TODO: agent fills from discovered files + web_") + "\n")
    path = cfg.research / f"research-v{version}.md"
    path.write_text("".join(lines))
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/research.py tests/test_research.py
git commit -m "feat: research runner discovers + reads real project files, runnable checklist"
```

---

## Task 24: CLI — stage commands + run-pipeline orchestrator

**Files:**
- Create: `src/fanops/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from pathlib import Path
from fanops.config import Config
from fanops.cli import run_pipeline, main

def _put(p: Path, b: bytes):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_run_pipeline_smoke_dryrun(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)  # dryrun
    cfg = Config(root=tmp_path)
    # seed one active account on the 'edits/visual' lane
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@edits", "lane": "edits/visual", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None}]}))
    # one inbox file
    _put(cfg.inbox / "raw.mp4", b"VIDEO")
    # stub ffmpeg everywhere so no real encode happens
    def fake_run(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.variant.subprocess.run", side_effect=fake_run)

    summary = run_pipeline(cfg, base_time="2026-06-02T18:00:00Z", seed=1, clip_plan=[{
        "asset_index": 0, "start": 0.0, "end": 7.0, "raw_hook": "cold-open",
        "tier": "hero", "content_type": "performance", "account_fit": ["edits/visual"],
        "variants": [
            {"hook_type": "cold-open", "caption": "no warning. just impact.",
             "aspect": "9:16", "lever": "universal"},
            {"hook_type": "text-hook", "caption": "they slept on this 👀",
             "aspect": "1:1", "lever": "bilingual"},
            {"hook_type": "beat-drop", "caption": "wait for it.",
             "aspect": "4:3", "lever": "diaspora"},
        ],
    }])
    assert summary["assets"] == 1
    assert summary["clips"] == 1
    assert summary["variants"] == 3            # 3 distinct specs (hero cap 6, 3 supplied)
    assert summary["published"] == 3
    # variants are creatively DISTINCT, not 3 aspect ratios of one caption
    caps = {v.caption.split("\n")[0] for v in
            __import__("fanops.ledger", fromlist=["Ledger"]).Ledger.load(cfg).variants.values()}
    assert len(caps) == 3
    assert any(cfg.scheduled.glob("*.json"))

def test_run_pipeline_holds_offbrand_variant_and_surfaces_it(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    import json
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@edits", "lane": "edits/visual", "platforms": ["instagram"],
         "status": "active", "access": "blotato", "warmup_date": None}]}))
    _put(cfg.inbox / "raw.mp4", b"VIDEO")
    def fake_run(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.variant.subprocess.run", side_effect=fake_run)
    summary = run_pipeline(cfg, base_time="2026-06-02T18:00:00Z", seed=1, clip_plan=[{
        "asset_index": 0, "start": 0.0, "end": 7.0, "raw_hook": "cold-open",
        "tier": "volume", "content_type": "performance", "account_fit": ["edits/visual"],
        "variants": [
            {"hook_type": "cold-open", "caption": "this one hits 🔥", "aspect": "9:16"},
            {"hook_type": "text-hook", "caption": "pls stream 🥺 sorry", "aspect": "1:1"},
        ],
    }])
    assert summary["variants"] == 2
    assert summary["published"] == 1           # the off-brand one is HELD, not posted
    assert summary["held"] == 1
    # the hold reached the digest
    digest = cfg.digest_path.read_text()
    assert "Brand-risk holds" in digest

def test_main_status_runs(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["status"])
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fanops.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py
"""CLI: stage commands + a run-pipeline orchestrator threading ONE ledger through
ingest -> clip -> tag -> variant(specs) -> caption-validate -> artist-tag -> schedule -> post.

Each `clip_plan` entry carries a `variants` list of creative specs (where the
conceptualization budget is spent — tier caps how many are realized). The pipeline is
otherwise mechanical."""
from __future__ import annotations
import argparse
import sys
from datetime import datetime
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.digest import write_digest
from fanops.registry import Registry
from fanops.models import (State, Tags, VariantSpec, ContentType, HookType, Fmt,
                           LengthBucket, Tier, Lever)
from fanops.ingest import ingest_drops
from fanops.clip import cut_clip
from fanops.tag import tag_clip, length_bucket
from fanops.variant import make_variants
from fanops.caption import validate_caption
from fanops.tagging import inject_artist_tag, should_tag
from fanops.schedule import schedule_variants, _parse
from fanops.post.run import publish_due


def _specs(plan: dict) -> list[VariantSpec]:
    out = []
    for s in plan["variants"]:
        out.append(VariantSpec(
            hook_type=HookType(s["hook_type"]),
            caption=s["caption"],
            aspect=Fmt(s.get("aspect", "9:16")),
            lever=Lever(s.get("lever", "universal")),
            sound=s.get("sound"),
        ))
    return out


def run_pipeline(cfg: Config, *, base_time: str, seed: int, clip_plan: list[dict]) -> dict:
    led = Ledger.load(cfg)
    led = ingest_drops(led, cfg)
    assets = sorted(led.assets.values(), key=lambda a: a.id)
    when = _parse(base_time)

    for plan in clip_plan:
        asset = assets[plan["asset_index"]]
        led, clip = cut_clip(led, cfg, asset_id=asset.id, start=plan["start"],
                             end=plan["end"], raw_hook=plan.get("raw_hook"))
        tags = Tags(
            content_type=ContentType(plan.get("content_type", "performance")),
            fmt=Fmt.r16x9,
            hook_type=HookType(plan.get("raw_hook", "cold-open")),
            length=length_bucket(plan["end"] - plan["start"]),
            lever=Lever(plan.get("lever", "universal")),
            account_fit=plan["account_fit"],
            tier=Tier(plan.get("tier", "filler")),
            song=plan.get("song"),
        )
        led = tag_clip(led, clip.id, tags)
        led = make_variants(led, cfg, clip.id, _specs(plan))   # one variant per spec
        for v in [v for v in led.variants.values() if v.parent_id == clip.id]:
            led, held = validate_caption(led, v.id)            # per-variant; held stays varied
            if not held:
                # subtle, non-synchronized artist tag (a minority of posts)
                acct = (v.tags.account_fit or ["_"])[0]
                if should_tag(v.id, acct):
                    led, _ = inject_artist_tag(led, v.id, account=acct, when=when)

    reg = Registry.load(cfg)
    led = schedule_variants(led, cfg, reg, base_time=base_time, seed=seed)
    led = publish_due(led, cfg)
    led.save()
    write_digest(led)

    return {
        "assets": len(led.assets),
        "clips": len(led.clips),
        "variants": len(led.variants),
        "held": sum(1 for v in led.variants.values() if v.held),
        "queued": len(led.posts_in_state(State.queued)),
        "published": len(led.posts_in_state(State.published)),
    }


def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    print(f"assets={len(led.assets)} clips={len(led.clips)} "
          f"variants={len(led.variants)} posts={len(led.posts)} "
          f"backend={cfg.poster_backend}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fanops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("ingest")
    sub.add_parser("digest")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cfg = Config()
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "ingest":
        led = ingest_drops(Ledger.load(cfg), cfg); led.save(); write_digest(led)
        print(f"ingested -> {len(led.assets)} assets"); return 0
    if args.cmd == "digest":
        write_digest(Ledger.load(cfg)); print(f"wrote {cfg.digest_path}"); return 0
    return 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the FULL test suite**

Run: `pytest -v`
Expected: PASS (all modules green)

- [ ] **Step 6: Commit**

```bash
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat: cli stage commands and end-to-end run-pipeline orchestrator"
```

---

## Task 25: Author the runtime operating doc (RUNTIME.md) + strategy template

**Files:**
- Create: `MohFlow-FanOps/00_control/RUNTIME.md`, `MohFlow-FanOps/00_control/strategy.md`

> This is the **runtime agent's** operating doc — distinct from the builder `CLAUDE.md`.
> It tells the operating agent how to run the engine day-to-day. No test (prose); verify
> by checklist in Step 3.

- [ ] **Step 1: Write `RUNTIME.md`**

Write `MohFlow-FanOps/00_control/RUNTIME.md` with these sections (fill each with the concrete operating procedure, referencing the CLI commands and stages built above):
- **Role & boundary** — what the runtime agent does (ingest→…→track) vs the three human-only calls (identities, Blotato connection, identity/strategy sign-off). Agent never holds credentials.
- **Daily loop** — `fanops ingest` → plan clips (allocate conceptualization by tier) → `run-pipeline` → confirm dry-run/scheduled output → (post-connection) posting runs autonomously.
- **No per-post gate; the one hold** — ship everything except brand-risk holds; surface holds in the digest, never block the queue.
- **Opsec rules in operation** — staggered/jittered times (built into scheduler), no two accounts same lane (enforced by registry), subtle non-synchronized tagging, real content only.
- **Signal weighting** — saves/shares/retention > likes; how `lift_score` drives amplify/retire.
- **Cadence** — weekly digest; research refresh on cadence; ≤3 decisions surfaced.
- **Pointers** — ledger at `00_control/ledger.json`, digest at `ledger_digest.md`, registry at `accounts.json`, strategy at `strategy.md`.

- [ ] **Step 2: Write `strategy.md` template**

Write `MohFlow-FanOps/00_control/strategy.md` as a framework skeleton (brief §5), versioned, with empty-but-labelled sections for: per-account playbook, tiering rules, tagging taxonomy (link to §7 vocabulary already in code), hook library, cultural/language levers, variety rules, volume targets, test/learn loop, KPIs by horizon. Mark it `v0 — to be filled by the Strategy stage after Research`.

- [ ] **Step 3: Verify by checklist**

Confirm both files exist and `RUNTIME.md` covers: the human-only boundary, the no-gate rule + the single hold, opsec-in-operation, and signal weighting. Confirm `strategy.md` has all nine §5 sections present as headers.

Run:
```bash
test -f "MohFlow-FanOps/00_control/RUNTIME.md" && test -f "MohFlow-FanOps/00_control/strategy.md" && echo "OK both present"
grep -c "^##" "MohFlow-FanOps/00_control/strategy.md"
```
Expected: `OK both present` and a section count ≥ 9.

- [ ] **Step 4: Commit**

```bash
git add MohFlow-FanOps/00_control/RUNTIME.md MohFlow-FanOps/00_control/strategy.md
git commit -m "docs: runtime operating doc and strategy framework template"
```

---

## Task 26: End-to-end dry-run on a real sample + README

**Files:**
- Create: `README.md`
- Uses: a real sample media file Moh drops in `01_inbox/` (or a generated test clip)

- [ ] **Step 1: Generate a tiny real sample clip with ffmpeg (no external asset needed)**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
ffmpeg -y -f lavfi -i testsrc=duration=12:size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=440:duration=12 \
  -c:v libx264 -c:a aac -shortest \
  "MohFlow-FanOps/01_inbox/sample_source.mp4"
ls -la "MohFlow-FanOps/01_inbox/sample_source.mp4"
```
Expected: a ~12s 720p sample file exists.

- [ ] **Step 2: Activate one account in the registry for the dry-run**

Edit `MohFlow-FanOps/00_control/accounts.json`: set the first account's `"status"` to `"active"` and give it a real-looking handle placeholder, e.g. `"@mohflow.edits"`. (Still no credentials — dry-run posts nothing.)

- [ ] **Step 3: Run ingest + a real pipeline pass via a one-off script**

Run (note the spec-driven `variants` list — distinct hooks/captions, not aspect-only):
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
FANOPS_POSTER=dryrun python3 -c "
from fanops.config import Config
from fanops.cli import run_pipeline
cfg = Config()
summary = run_pipeline(cfg, base_time='2026-06-02T18:00:00Z', seed=7, clip_plan=[
  {'asset_index':0,'start':0.0,'end':7.0,'raw_hook':'cold-open','tier':'hero',
   'content_type':'performance','account_fit':['edits/visual'],
   'variants':[
     {'hook_type':'cold-open','caption':'no warning. just impact.','aspect':'9:16'},
     {'hook_type':'text-hook','caption':'they slept on this 👀','aspect':'1:1','lever':'bilingual'},
     {'hook_type':'beat-drop','caption':'wait for it.','aspect':'4:3','lever':'diaspora'},
   ]},
])
print(summary)
"
```
Expected: prints a summary dict with `assets>=1, clips>=1, variants==3 (3 distinct specs), held==0, published==3`.
Real `.mp4` variant files appear in `MohFlow-FanOps/04_variants/`; real payload JSONs in `MohFlow-FanOps/05_scheduled/`.

- [ ] **Step 4: Verify the dry-run artifacts are real and correct**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
echo "=== variants ==="; ls MohFlow-FanOps/04_variants/*.mp4 | wc -l
echo "=== scheduled payloads ==="; ls MohFlow-FanOps/05_scheduled/*.json | wc -l
echo "=== one payload (has media + matching platform/targetType) ==="; cat $(ls MohFlow-FanOps/05_scheduled/*.json | head -1)
echo "=== distinct captions across variants? ==="; for j in MohFlow-FanOps/05_scheduled/*.json; do jq -r '.post.content.text' "$j" | head -1; done | sort -u | wc -l
echo "=== digest ==="; cat MohFlow-FanOps/00_control/ledger_digest.md
```
Expected: 3 variant mp4s, 3 scheduled payload JSONs; each payload has a non-empty `post.content.mediaUrls` (a `file://…` dry-run URL), `post.content.platform == post.target.targetType`, and a root-level `scheduledTime`; the distinct-captions count is **3** (proving variants differ creatively, not just by aspect). **Confirm one variant mp4 plays / has nonzero size** — `ffprobe MohFlow-FanOps/04_variants/<file>.mp4` shows a valid stream.

- [ ] **Step 5: Write `README.md`**

Write a `README.md` covering: what this is (one paragraph), the build-vs-runtime split (point to `CLAUDE.md` for build, `MohFlow-FanOps/00_control/RUNTIME.md` for run), how to install (`pip install -e ".[dev]"`), how to run the dry-run pipeline, the three human-only steps to go live (create accounts → connect Blotato → set `FANOPS_POSTER=mcp`/`rest` + key), and where the ledger/digest/registry live. Note that raw media is git-ignored and the content bank must be backed up separately (brief §9 — the bank is the real asset).

- [ ] **Step 6: Run the full suite once more, then commit**

Run: `pytest -v`
Expected: all green.

```bash
git add README.md MohFlow-FanOps/00_control/accounts.json
git commit -m "test: end-to-end dry-run on real ffmpeg sample + README"
```

> **Note:** `sample_source.mp4` and the generated variants are git-ignored (Task 1
> `.gitignore`), so they won't be committed — only the registry change + README are.

---

## Task 27: Sync docs + handoff

**Files:**
- Modify: `CLAUDE.md` (if any build fact drifted), file memory
- Verify: the whole system

- [ ] **Step 1: Run the full verification gate**

Run:
```bash
cd "/Users/molhamhomsi/Moh Flow Fan Accounts"
pytest -v
echo "=== fanops status ==="
python3 -m fanops.cli status 2>/dev/null || (cd "/Users/molhamhomsi/Moh Flow Fan Accounts" && PYTHONPATH=src python3 -m fanops.cli status)
```
Expected: all tests pass; `status` prints unit counts + `backend=dryrun`.

- [ ] **Step 2: Reconcile CLAUDE.md with what was built**

Re-read `CLAUDE.md`. If any locked decision or verified fact changed during the build (e.g. a Blotato field behaved differently, a tool name differed), update it. Otherwise leave it. Use the `sync-docs` skill if available.

- [ ] **Step 3: Update file memory**

Append to `MEMORY.md` index a pointer to a new build-state memory if useful, and update `fanops-project.md` §status to "system built end-to-end through dry-run; awaiting Moh's 3 human-only steps to go live."

- [ ] **Step 4: Final commit + handoff**

```bash
git add -A
git commit -m "chore: final sync — system built end-to-end, dry-run verified"
```
Then invoke the `handoff` skill to rewrite §Now so a cold session can resume: the system is built and dry-run-verified; the only remaining work is the three human-only steps (accounts, Blotato connection, strategy sign-off) plus running the Research→Strategy stages with real inputs.

---

## Human-only steps (the ONLY work the agent cannot complete)

These are surfaced, not built around with fake credentials. Everything else above is done end-to-end.

1. **Create account identities + credentials.** Moh creates 3–5 fan accounts (separate emails/verification, distinct lanes), holds all passwords. Then replace `@TBD-*`/placeholder handles in `accounts.json` with real handles and flip `status` to `warming`/`active`. *Agent never sees a password.*
2. **Connect Blotato.** Moh connects the accounts inside Blotato, gets the API key (keep `=` padding), and either (a) connects the official Blotato MCP server, or (b) puts the key in `.env`. Then set `FANOPS_POSTER=mcp` (preferred) or `rest`. The Poster stops being a dry-run and posts for real.
3. **Strategy/identity sign-off.** The Research stage *discovers and reads* Moh's real files (`discover_source_files` over `~/Downloads` etc. — Songstats/SM-approach/lyrics/etc., NOT hardcoded names; PII excluded), does web research per `RESEARCH_QUESTIONS`, and fills the Strategy framework. Moh then signs off on lanes, tiering, hook library, and language levers — once — and the pipeline runs against it.

---

## Self-Review

> This review traces INTENT DELIVERY end-to-end, not section presence. (The first version
> of this plan passed a section checklist while four core behaviors were hollow — see file
> memory `feedback-self-review-checks-intent-not-presence.md`. For each load-bearing
> requirement below, the question is "does the signal/behavior actually reach its
> destination at runtime?")

**Intent trace — the requirements that were hollow before, now verified by runtime path:**

- **Atomize / many hooks / algorithm A/B-tests (§0,§1.1,§1.4) — DELIVERS.** `clip_plan[].variants` carries N distinct `VariantSpec` (hook+caption+lever+sound+aspect). `make_variants` (Task 13) creates one variant PER SPEC, each with its own caption/hook/lever; `validate_caption` validates per-variant. Test `test_make_variants_creates_one_per_spec_each_distinct` asserts 3 distinct captions/hooks/levers/formats; CLI test asserts `len(caps)==3`. The algorithm now A/B-tests genuinely different creatives.
- **Tier = conceptualization budget (§1.3,§6.4) — DELIVERS.** `cap_specs_for_tier` (Task 13) caps realized specs by tier via `variant_budget` (filler 1 / volume 3 / hero 6). More tier = more *distinct treatments authored*, not more reframes. Test `test_make_variants_filler_takes_one_even_if_more_supplied`.
- **Brand-risk hold reaches Moh (§6.7,§8) — DELIVERS.** Hold is first-class (`Variant.held`/`held_reason`, Task 3); `validate_caption` sets it (Task 14); `schedule_variants` skips held (Task 15, `test_schedule_skips_held_variant`); `held_holds` in digest (Task 6) AND `weekly_report` (Task 22) scan VARIANTS for holds. CLI test `test_run_pipeline_holds_offbrand_variant_and_surfaces_it` asserts the hold reaches the digest. The signal no longer vanishes.
- **Track/Adjust compounding loop (§6.8,§6.9) — DELIVERS.** Read-end: `pull_metrics` (Task 20) fetches via injected `blotato_list_posts` and matches by `submission_id` (`test_pull_metrics_matches_by_submission_id_and_records`). Write-end: `amplify` (Task 21) emits new `VariantSpec`s in a winner's vein keyed by clip id; `retire` marks loser lineage (`test_amplify_returns_new_specs_in_the_winners_vein`, `test_retire_marks_loser_lineage`). The loop is closed at both ends.
- **Subtle, non-synchronized artist tagging (§1.6,§3.2) — DELIVERS.** `tagging.py` (Task 14b): `should_tag` tags a deterministic minority; `inject_artist_tag` buries the @mention off the hook line and blocks if any account tagged within the window (`test_inject_blocked_when_another_account_tagged_in_same_window`). Tracked on `ledger.tag_log`. Real code, not prose.
- **Media actually posts (§6 video) — DELIVERS.** `media.py` (Task 18b) uploads via presigned URL; `publish_due` (Task 19) populates `post.media_urls` from the variant file; all three posters send `post.media_urls` (`test_dryrun_writes_payload_with_media…`, `test_rest_poster_sends_media_urls…`, `test_mcp_poster_builds_FLAT_tool_call_args`). No more text-only posts.
- **Per-platform Blotato correctness — DELIVERS.** `default_target_fields` fills TikTok's 7 / YouTube's 3 / FB pageId so they don't 422 (`test_default_target_fields_fill_required_per_platform`); REST uses nested body, MCP uses FLAT args (`build_blotato_mcp_args`), tool name `blotato_list_schedules` (plural), URL `https://mcp.blotato.com/mcp`.
- **Opsec not a synchronized pulse (§3.2) — DELIVERS.** `schedule_variants` gives each account its own anchor + seed (`account_anchor_seed`); `test_two_accounts_get_independent_anchors` asserts different scheduled times across accounts.
- **Research mines real files first (§4) — DELIVERS.** `discover_source_files` globs the real corpus by keyword and excludes PII (`test_discover_finds_moh_files_and_excludes_pii`); `RESEARCH_QUESTIONS` is a runnable per-section checklist.

**Spec coverage (mechanics):** §2.1 folders→T1; §2.2 ledger+digest→T5,6,12; §2.3 registry→T7; §2.4/2.5 boundary→CLAUDE.md+Human-only+dry-run Poster; §3.1 lane uniqueness→T7 `DuplicateLaneError`; §6.1 ingest 3 channels + PII exclusion→T8,9; §6.2 clip→T10; §6.3 tag→T11,12; §7 model+taxonomy→T3; §10 phasing→end-to-end; Blotato *pattern* (structured input → file artifacts → scheduler → cron) modeled, verified *shape* used not the article's.

**Confirmed code bugs from pre-flight — all fixed:** (#1) Task 9 origin tag → `ingest_drops(origin=)`, `download_source` passes `"download"`; (#2) Task 15 post-id idempotency → id keyed on variant only + variant advances to `queued`; (#3) Task 19 held-retry → no held posts reach the queue, failed posts go terminal (`analyzed`).

**Placeholder scan:** every code step shows full code; no "implement later." `@TBD-*` handles in `accounts.json` are intentional (human-replaced, labelled). `research.py`'s `_TODO: agent fills…` is intentional template body (the runnable discovery + checklist precede it), not a plan gap.

**Type consistency (re-checked after the model change):** `make_variants(led,cfg,clip_id,specs)` matches its `cli.run_pipeline` caller; `validate_caption(led,vid,caption=None)` replaces the old `apply_caption` everywhere (caption.py, cli.py, tests); `VariantSpec` fields (hook_type/caption/aspect/lever/sound) consistent across models/variant/adjust/cli; REST poster uses `build_blotato_payload`, MCP poster uses `build_blotato_mcp_args` (deliberately different — flat vs nested); `held`/`held_reason` consistent across models/caption/schedule/digest/report; `lift_score` keys (saves/shares/retention/reach/likes) match `record_metrics`/`pull_metrics`/`classify_outcomes`. Ledger gained `clip_tags`, `retired`, `tag_log` — all added in `__init__`+`load`+`save` (Tasks 12, 21, 14b). Verified on the real toolchain that pydantic 2.13 + the four ffmpeg filters + frame-accurate cut all work (pre-flight Bash).

**Remaining honest limitations (flagged, not hidden):**
- Some §6.8 metrics (saves, profile-taps) may exceed what Blotato `blotato_list_posts` returns; `pull_metrics` records whatever the injected lister yields and the runtime agent extends the lister as platform analytics become available. Not a blocker for the lift model (which already weights what's present).
- `amplify` returns specs keyed by clip; re-running them is the agent's explicit next loop iteration (not auto-fired) — deliberate, so a human can still pull the plug between rounds (no runaway).
- Artist-tag injection within one `run_pipeline` batch (single timestamp) fires for at most one variant due to the no-sync window — correct for opsec, but means heavy tagging requires spreading runs over time (documented in RUNTIME.md).
