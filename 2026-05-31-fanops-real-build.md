# MOH FLOW FAN OPS — Real Build Implementation Plan (clean-slate, new repo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **This plan assumes an EMPTY repository.** It does not reference, migrate, or depend on any prior code. Build it from zero in a fresh directory.

**Goal:** Build an autonomous fan-account engine that ingests Moh Flow's videos, **intelligently decides which moments are worth posting** (transcript + audio/scene signals → an agent decision with a recorded reason), cuts those moments into platform-ready clips with agent-written hooks/captions, and **cross-posts every clip to every fan account on every platform** (staggered for opsec) via Blotato — then pulls real performance back to make more of what works.

**Architecture:** A Python package (`src/fanops/`) of stage modules over one git-versioned JSON ledger with the unit chain **`Source → Moment → Clip → Post`**. The novel core is the **clip framework**: `transcribe` (local Whisper, free, EN/AR) + `signals` (ffmpeg silence/scene/loudness) produce a *moment-decision request*; an **agent step** (file-contract: code writes a request, the agent writes back a decision, code resumes — testable with mocked agent output) returns `Moment`s each carrying `{start, end, reason, transcript_excerpt}`. Clips render via ffmpeg. **Cross-posting is first-class**: one clip fans out to the full `accounts × platforms` matrix, each post with its own jittered time and its own caption variation. Posting is Blotato (dry-run / REST / MCP). No tiers, no lanes — quantity of moments is the agent's judgment, and content goes to all active accounts.

**Tech Stack:** Python 3.14, `pytest` + `pytest-mock`, `pydantic` 2.x, `requests`, `python-dotenv`, **`ffmpeg` 8.0** (cut/reframe/`silencedetect`/`scdet`/`ebur128`), **`openai-whisper`** (local transcription, `whisper` CLI on PATH), git. Blotato v2 REST (`https://backend.blotato.com/v2`) + official MCP. Connected media MCP (`video_analysis_create`, `virality_predictor`) is an OPTIONAL paid escalation, never required.

**Key decisions (locked):**
1. **No tiers, no lanes.** A `Source` yields as many `Moment`s as the agent judges worth posting (0..N). Accounts are a flat active list; every clip targets every active account × its platforms.
2. **The clip decision is the product.** A `Moment` is a recorded judgment (`reason`, `transcript_excerpt`, `signal_score`), not a hand-fed timestamp. Code supplies transcript+signals deterministically; the agent supplies the pick via a file contract; the contract is what makes it testable.
3. **Cross-posting is the spine.** `fan_out(clip) → [Post per (account, platform)]`, each with an independent jittered schedule and a platform-appropriate caption variation (never word-identical across surfaces).
4. **Agent steps via file contract.** Each generative step (`decide_moments`, `write_captions`) reads a request JSON the code wrote and writes a response JSON the code validates against a pydantic schema. Works with a human-agent, cron Claude, or an LLM API behind the same interface. Tests inject the response file directly — no live model needed.
5. **Local Whisper is the default transcription** (free, offline, EN/AR). Connected-media MCP `video_analysis`/`virality_predictor` is optional paid escalation for visually-driven clips only.
6. **Keep what was genuinely real:** git-versioned JSON ledger, Blotato poster (dry-run/REST/MCP + media upload + per-platform fields), subtle non-synchronized @-tagging, PII exclusion at ingest, opsec staggering. Brand-risk stays a **hold, not a gate**.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `.gitignore`, `.env.example` | Project config; secrets + media-bank ignored |
| `src/fanops/__init__.py` | Package marker |
| `src/fanops/ids.py` | Deterministic IDs for units |
| `src/fanops/models.py` | Pydantic: `Source, Moment, Clip, Post` + `MomentRequest/MomentDecision`, `CaptionRequest/CaptionSet`, enums |
| `src/fanops/config.py` | Paths + `.env` (`BLOTATO_API_KEY`, `FANOPS_POSTER`) |
| `src/fanops/ledger.py` | Load/save/query one JSON ledger; idempotent add; state transitions |
| `src/fanops/digest.py` | Human-readable Markdown digest (counts + holds + pending agent steps) |
| `src/fanops/accounts.py` | Flat active-account registry (non-secret metadata only) |
| `src/fanops/ingest.py` | Catalogue videos (drop/url/scan), sha256 de-dupe, **PII exclusion** |
| `src/fanops/transcribe.py` | Local Whisper → timestamped segments JSON (cached on the Source) |
| `src/fanops/signals.py` | ffmpeg `silencedetect`+`scdet`+`ebur128` → candidate moment timestamps |
| `src/fanops/moments.py` | Build `MomentRequest` (transcript+signals); ingest `MomentDecision` → `Moment`s |
| `src/fanops/clip.py` | Render a `Moment` → cut clip(s) via ffmpeg; reframe to target aspects |
| `src/fanops/caption.py` | Build `CaptionRequest`; ingest `CaptionSet`; **brand-risk hold** (anti-pattern logic) |
| `src/fanops/tagging.py` | Subtle, non-synchronized artist @mention (ledger `tag_log`) |
| `src/fanops/crosspost.py` | **Fan-out**: clip × active accounts × platforms → `Post`s, staggered, per-surface caption |
| `src/fanops/post/__init__.py` | `Poster` interface + factory (dryrun/rest/mcp) |
| `src/fanops/post/payload.py` | Blotato nested REST body + flat MCP args; per-platform required target fields |
| `src/fanops/post/media.py` | Upload local file → Blotato public URL; dry-run `file://` |
| `src/fanops/post/dryrun.py` | Write intended payload, post nothing |
| `src/fanops/post/blotato_rest.py` | Verified v2 REST client |
| `src/fanops/post/blotato_mcp.py` | MCP adapter (`blotato_create_post`, flat args) |
| `src/fanops/post/run.py` | Publish queue; upload media; advance/terminal |
| `src/fanops/track.py` | Pull metrics (injected `list_posts`), lift-weight saves/shares/retention |
| `src/fanops/adjust.py` | Classify winners/losers by lift; amplify = re-request moments/captions in winner's vein; retire |
| `src/fanops/agentstep.py` | File-contract helpers: write request / read+validate response / pending-list |
| `src/fanops/cli.py` | Stage commands + `run` orchestrator (pauses at agent steps) |
| `tests/test_*.py` | One test module per source module |
| `MohFlow-FanOps/00_control/` | `ledger.json`, `ledger_digest.md`, `accounts.json`, `context.md`, `RUNTIME.md` |
| `MohFlow-FanOps/{01_inbox,02_sources,03_clips,04_agent_io,05_scheduled,06_published,07_reports}/` | Working dirs |

**Module boundary rule:** each stage exposes one primary function taking `(ledger, config, ...)` and returning the ledger. Stage code imports only `ledger`, `models`, `config`, `ids`, `agentstep` (plus named helpers). The agent never appears *inside* a deterministic function — generative work always crosses the `agentstep` file boundary.

**State machine (on every unit):** `Source: catalogued → transcribed → signalled → moments_requested → moments_decided`; `Moment: decided → clipped`; `Clip: rendered → captions_requested → captioned → queued → published → analyzed`; plus `held` (brand-risk, first-class on Clip).

---

## Task 1: Project skeleton, git, venv, gitignore

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/fanops/__init__.py`
- Create: `MohFlow-FanOps/{00_control,01_inbox,02_sources,03_clips,04_agent_io,05_scheduled,06_published,07_reports}/.gitkeep`

- [ ] **Step 1: Init git + dirs**

```bash
git init
mkdir -p src/fanops tests docs/superpowers/plans
cd MohFlow-FanOps 2>/dev/null || mkdir -p MohFlow-FanOps
```
Then from repo root:
```bash
for d in 00_control 01_inbox 02_sources 03_clips 04_agent_io 05_scheduled 06_published 07_reports; do
  mkdir -p "MohFlow-FanOps/$d" && touch "MohFlow-FanOps/$d/.gitkeep"
done
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "fanops"
version = "0.1.0"
description = "MOH FLOW FAN OPS — intelligent clip + cross-post engine"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.7", "requests>=2.31", "python-dotenv>=1.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]
# Local transcription. Installed into the venv in Task 6. (whisper CLI lands on PATH.)
transcribe = ["openai-whisper>=20231117"]

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
# secrets
.env
*.key
*-credentials.json
.mcp-credentials/

# media bank + agent IO — large/private/regenerated, never committed
MohFlow-FanOps/01_inbox/*
MohFlow-FanOps/02_sources/*
MohFlow-FanOps/03_clips/*
MohFlow-FanOps/04_agent_io/*
!MohFlow-FanOps/*/.gitkeep

# python / venv
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
.venv/
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Moh provides at Blotato-connection time. Keep trailing "=" padding (stripping → 401).
BLOTATO_API_KEY=
# Backend: dryrun | rest | mcp  (defaults to dryrun until a key exists)
FANOPS_POSTER=dryrun
```

- [ ] **Step 5: `src/fanops/__init__.py`**

```bash
touch src/fanops/__init__.py
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold fanops (clean slate) — dirs, gitignore, pyproject"
```

---

## Task 2: venv + deps (Homebrew Python is PEP 668-managed)

**Files:** none (environment)

- [ ] **Step 1: Create venv + install**

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
```
Expected: installs pydantic, requests, python-dotenv, pytest, pytest-mock.

- [ ] **Step 2: Install Whisper into the venv**

```bash
./.venv/bin/python -m pip install -e ".[transcribe]"
./.venv/bin/whisper --help | head -1
```
Expected: a `usage: whisper ...` line. (If `whisper` is already on PATH system-wide, the pipeline still shells out to it — see Task 8.)

- [ ] **Step 3: Verify ffmpeg filters exist**

```bash
ffmpeg -hide_banner -filters | grep -E "silencedetect|scdet|ebur128" | wc -l
```
Expected: `3`.

> All subsequent `pytest`/`python`/`fanops` invocations use `./.venv/bin/...`.

---

## Task 3: Deterministic IDs

**Files:** Create `src/fanops/ids.py`; Test `tests/test_ids.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_ids.py
from fanops.ids import make_id, child_id

def test_make_id_deterministic():
    assert make_id("src", "/in/a.mov") == make_id("src", "/in/a.mov")
    assert make_id("src", "/in/a.mov").startswith("src_")

def test_make_id_differs():
    assert make_id("src", "a") != make_id("src", "b")

def test_child_id_embeds_parent_and_index():
    p = make_id("src", "x")
    a = child_id("moment", p, 0)
    b = child_id("moment", p, 1)
    assert a != b and a.startswith("moment_")
    assert a == child_id("moment", p, 0)
```

- [ ] **Step 2: Run — expect fail**

Run: `./.venv/bin/pytest tests/test_ids.py -v`
Expected: `ModuleNotFoundError: No module named 'fanops.ids'`

- [ ] **Step 3: Implement**

```python
# src/fanops/ids.py
"""Deterministic, collision-resistant IDs so re-running any stage is idempotent."""
import hashlib

def _hash(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:12]

def make_id(kind: str, source: str) -> str:
    return f"{kind}_{_hash(kind, source)}"

def child_id(kind: str, parent_id: str, index: int) -> str:
    return f"{kind}_{_hash(kind, parent_id, str(index))}"
```

- [ ] **Step 4: Run — expect pass**

Run: `./.venv/bin/pytest tests/test_ids.py -v` → PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ids.py tests/test_ids.py
git commit -m "feat: deterministic id generation"
```

---

## Task 4: Models — units + agent-step contracts

**Files:** Create `src/fanops/models.py`; Test `tests/test_models.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError
from fanops.models import (
    Source, Moment, Clip, Post, State, Platform,
    MomentRequest, MomentDecision, MomentPick,
    CaptionRequest, CaptionSet, CaptionItem,
)

def test_source_defaults_catalogued():
    s = Source(id="src_1", source_path="/s/x.mp4")
    assert s.state is State.catalogued and s.transcript is None

def test_unit_parent_chain():
    s = Source(id="src_1", source_path="/s/x.mp4")
    m = Moment(id="mom_1", parent_id=s.id, start=1.0, end=8.0,
               reason="punchline + beat drop", transcript_excerpt="they slept on me")
    c = Clip(id="clip_1", parent_id=m.id, path="/c/clip_1.mp4")
    p = Post(id="post_1", parent_id=c.id, account="@a", platform=Platform.instagram,
             caption="x")
    assert m.parent_id == s.id and c.parent_id == m.id and p.parent_id == c.id

def test_moment_requires_reason():
    with pytest.raises(ValidationError):
        Moment(id="m", parent_id="src", start=0.0, end=5.0)  # no reason

def test_clip_hold_is_first_class():
    c = Clip(id="c", parent_id="m", path="/c.mp4", held=True, held_reason="begging")
    assert c.held is True and c.held_reason == "begging"

def test_post_carries_media_urls():
    p = Post(id="p", parent_id="c", account="@a", platform=Platform.tiktok,
             caption="x", media_urls=["https://h/v.mp4"])
    assert p.media_urls == ["https://h/v.mp4"]

def test_moment_request_and_decision_roundtrip():
    req = MomentRequest(source_id="src_1", duration=42.0,
                        transcript=[{"start": 0.0, "end": 3.0, "text": "intro"}],
                        signal_peaks=[{"t": 16.0, "kind": "loudness"}])
    assert req.source_id == "src_1"
    dec = MomentDecision(source_id="src_1", picks=[
        MomentPick(start=14.0, end=21.0, reason="bar lands, beat drops",
                   transcript_excerpt="they slept on me")])
    assert dec.picks[0].end == 21.0

def test_caption_request_and_set():
    req = CaptionRequest(clip_id="clip_1", platform=Platform.instagram,
                         transcript_excerpt="they slept on me", surface="@a/instagram")
    cs = CaptionSet(items=[CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                                       hashtags=["#mohflow"])])
    assert cs.items[0].surface == "@a/instagram"

def test_state_order_canonical():
    assert State.order()[:4] == ["catalogued", "transcribed", "signalled", "moments_requested"]
```

- [ ] **Step 2: Run — expect fail** (`No module named 'fanops.models'`)

Run: `./.venv/bin/pytest tests/test_models.py -v`

- [ ] **Step 3: Implement**

```python
# src/fanops/models.py
"""Units (Source→Moment→Clip→Post) + agent-step request/response contracts."""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class State(str, Enum):
    catalogued = "catalogued"
    transcribed = "transcribed"
    signalled = "signalled"
    moments_requested = "moments_requested"
    moments_decided = "moments_decided"
    clipped = "clipped"
    rendered = "rendered"
    captions_requested = "captions_requested"
    captioned = "captioned"
    queued = "queued"
    published = "published"
    analyzed = "analyzed"

    @staticmethod
    def order() -> list[str]:
        return [s.value for s in State]


class Platform(str, Enum):
    instagram = "instagram"
    tiktok = "tiktok"
    youtube = "youtube"
    facebook = "facebook"
    twitter = "twitter"


class Fmt(str, Enum):
    r9x16 = "9:16"; r1x1 = "1:1"; r16x9 = "16:9"


# ---- units ----
class Source(BaseModel):
    id: str
    state: State = State.catalogued
    source_path: str
    source_origin: str = "drop"           # drop | url | scan
    sha256: Optional[str] = None
    duration: Optional[float] = None
    transcript: Optional[list[dict]] = None     # [{start,end,text}]
    signal_peaks: Optional[list[dict]] = None   # [{t,kind,score}]
    meta: dict = Field(default_factory=dict)

class Moment(BaseModel):
    id: str
    parent_id: str                        # source id
    state: State = State.moments_decided
    start: float
    end: float
    reason: str                           # WHY this moment is worth posting (required)
    transcript_excerpt: str = ""
    signal_score: float = 0.0

class Clip(BaseModel):
    id: str
    parent_id: str                        # moment id
    state: State = State.rendered
    path: str
    aspect: Fmt = Fmt.r9x16
    held: bool = False                    # brand-risk hold — first-class
    held_reason: Optional[str] = None
    tagged_artist: bool = False

class Post(BaseModel):
    id: str
    parent_id: str                        # clip id
    state: State = State.queued
    account: str
    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    scheduled_time: Optional[str] = None
    status: str = "queued"                # queued|submitted|published|failed
    submission_id: Optional[str] = None
    public_url: Optional[str] = None
    metrics: dict = Field(default_factory=dict)


# ---- agent-step contracts ----
class MomentRequest(BaseModel):
    source_id: str
    duration: float
    transcript: list[dict] = Field(default_factory=list)
    signal_peaks: list[dict] = Field(default_factory=list)
    guidance: str = ""                    # filled from context.md by the code

class MomentPick(BaseModel):
    start: float
    end: float
    reason: str
    transcript_excerpt: str = ""
    signal_score: float = 0.0

class MomentDecision(BaseModel):
    source_id: str
    picks: list[MomentPick] = Field(default_factory=list)

class CaptionRequest(BaseModel):
    clip_id: str
    platform: Platform
    surface: str                          # "<account>/<platform>"
    transcript_excerpt: str = ""
    guidance: str = ""

class CaptionItem(BaseModel):
    surface: str
    caption: str
    hashtags: list[str] = Field(default_factory=list)

class CaptionSet(BaseModel):
    items: list[CaptionItem] = Field(default_factory=list)
```

- [ ] **Step 4: Run — expect pass** (8)

Run: `./.venv/bin/pytest tests/test_models.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/fanops/models.py tests/test_models.py
git commit -m "feat: units + agent-step request/response contracts"
```

---

## Task 5: Config + paths

**Files:** Create `src/fanops/config.py`; Test `tests/test_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py
from fanops.config import Config

def test_dirs(tmp_path):
    c = Config(root=tmp_path)
    assert c.inbox == tmp_path / "MohFlow-FanOps" / "01_inbox"
    assert c.sources == tmp_path / "MohFlow-FanOps" / "02_sources"
    assert c.agent_io == tmp_path / "MohFlow-FanOps" / "04_agent_io"
    assert c.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"

def test_poster_default_dryrun(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert Config(root=tmp_path).poster_backend == "dryrun"

def test_poster_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "abc=")
    c = Config(root=tmp_path)
    assert c.poster_backend == "rest" and c.blotato_api_key == "abc="
```

- [ ] **Step 2: Run — expect fail**

Run: `./.venv/bin/pytest tests/test_config.py -v`

- [ ] **Step 3: Implement**

```python
# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

_STAGE = {
    "control": "00_control", "inbox": "01_inbox", "sources": "02_sources",
    "clips": "03_clips", "agent_io": "04_agent_io", "scheduled": "05_scheduled",
    "published": "06_published", "reports": "07_reports",
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
        self.context_path = self.control / "context.md"

    @property
    def blotato_api_key(self) -> str | None:
        return os.getenv("BLOTATO_API_KEY") or None

    @property
    def poster_backend(self) -> str:
        return os.getenv("FANOPS_POSTER") or "dryrun"
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat: config + filesystem layout, dryrun-safe default"
```

---

## Task 6: Ledger

**Files:** Create `src/fanops/ledger.py`; Test `tests/test_ledger.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_ledger.py
from fanops.config import Config
from fanops.models import Source, Moment, Clip, Post, State, Platform
from fanops.ledger import Ledger

def test_empty(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    assert led.sources == {} and led.moments == {} and led.clips == {} and led.posts == {}

def test_roundtrip(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/x.mp4", sha256="d"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=5, reason="r"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4"))
    led.add_post(Post(id="post_1", parent_id="clip_1", account="@a",
                      platform=Platform.instagram, caption="x"))
    led.save()
    again = Ledger.load(cfg)
    assert again.sources["src_1"].sha256 == "d"
    assert again.moments["mom_1"].reason == "r"
    assert again.posts["post_1"].platform is Platform.instagram

def test_add_idempotent(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    assert len(led.sources) == 1

def test_already_seen(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4", sha256="d"))
    assert led.already_seen(sha256="d") and not led.already_seen(sha256="e")

def test_set_state(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    led.set_state("src_1", State.transcribed)
    assert led.sources["src_1"].state is State.transcribed

def test_in_state_filters(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="a", source_path="/1", state=State.catalogued))
    led.add_source(Source(id="b", source_path="/2", state=State.transcribed))
    assert [s.id for s in led.sources_in_state(State.catalogued)] == ["a"]
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/ledger.py
"""Single source of truth: one JSON doc, four id->unit maps, git-versioned."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.models import Source, Moment, Clip, Post, State


class Ledger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sources: dict[str, Source] = {}
        self.moments: dict[str, Moment] = {}
        self.clips: dict[str, Clip] = {}
        self.posts: dict[str, Post] = {}
        self.tag_log: dict[str, str] = {}     # account -> ISO time of last artist tag
        self.retired: set[str] = set()        # clip ids whose lineage is retired

    @classmethod
    def load(cls, cfg: Config) -> "Ledger":
        led = cls(cfg)
        p = cfg.ledger_path
        if p.exists():
            raw = json.loads(p.read_text())
            led.sources = {k: Source(**v) for k, v in raw.get("sources", {}).items()}
            led.moments = {k: Moment(**v) for k, v in raw.get("moments", {}).items()}
            led.clips = {k: Clip(**v) for k, v in raw.get("clips", {}).items()}
            led.posts = {k: Post(**v) for k, v in raw.get("posts", {}).items()}
            led.tag_log = raw.get("tag_log", {})
            led.retired = set(raw.get("retired", []))
        return led

    def save(self) -> None:
        self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "sources": {k: v.model_dump() for k, v in self.sources.items()},
            "moments": {k: v.model_dump() for k, v in self.moments.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
            "tag_log": self.tag_log,
            "retired": sorted(self.retired),
        }
        self.cfg.ledger_path.write_text(json.dumps(doc, indent=2, default=str))

    def add_source(self, s: Source) -> None: self.sources.setdefault(s.id, s)
    def add_moment(self, m: Moment) -> None: self.moments.setdefault(m.id, m)
    def add_clip(self, c: Clip) -> None: self.clips.setdefault(c.id, c)
    def add_post(self, p: Post) -> None: self.posts.setdefault(p.id, p)

    def set_state(self, unit_id: str, state: State) -> None:
        for store in (self.sources, self.moments, self.clips, self.posts):
            if unit_id in store:
                store[unit_id].state = state
                return
        raise KeyError(unit_id)

    def already_seen(self, *, sha256: str | None = None) -> bool:
        return any(s.sha256 == sha256 for s in self.sources.values()) if sha256 else False

    def sources_in_state(self, st: State) -> list[Source]:
        return [s for s in self.sources.values() if s.state is st]
    def clips_in_state(self, st: State) -> list[Clip]:
        return [c for c in self.clips.values() if c.state is st]
    def posts_in_state(self, st: State) -> list[Post]:
        return [p for p in self.posts.values() if p.state is st]
    def moments_of(self, source_id: str) -> list[Moment]:
        return [m for m in self.moments.values() if m.parent_id == source_id]
```

- [ ] **Step 4: Run — expect pass** (6)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ledger.py tests/test_ledger.py
git commit -m "feat: git-versioned json ledger (Source/Moment/Clip/Post)"
```

---

## Task 7: Ingest (drop/url/scan, sha256 de-dupe, PII exclusion)

**Files:** Create `src/fanops/ingest.py`; Test `tests/test_ingest.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_ingest.py
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State
from fanops.ingest import ingest_drops, sha256_of, is_excluded, scan_local

def _put(p, b):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_sha256_stable(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"hi")
    assert sha256_of(f) == sha256_of(f)

def test_catalogues(tmp_path):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    s = next(iter(led.sources.values()))
    assert s.state is State.catalogued and s.source_origin == "drop" and s.sha256

def test_dedupe_and_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "a.mp4", b"S"); _put(cfg.inbox / "b.mp4", b"S")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    led = ingest_drops(led, cfg)
    assert len(led.sources) == 1

def test_ignores_non_media(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "n.txt", b"x"); _put(cfg.inbox / "v.mp4", b"V")
    assert len(ingest_drops(Ledger.load(cfg), cfg).sources) == 1

def test_is_excluded():
    assert is_excluded("Moh Flow passport & ID.zip")
    assert is_excluded("Agreement - Accelerator.pdf")
    assert not is_excluded("adidas - day 01 moh flow.MOV")

def test_skips_pii(tmp_path):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "passport scan.jpg", b"S"); _put(cfg.inbox / "perf.mp4", b"V")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert next(iter(led.sources.values())).meta["original_name"] == "perf.mp4"

def test_scan_excludes_pii(tmp_path):
    d = tmp_path / "D"; d.mkdir()
    (d / "passport.jpg").write_bytes(b"x"); (d / "clip.mp4").write_bytes(b"y")
    assert {Path(c).name for c in scan_local([d])} == {"clip.mp4"}
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/ingest.py
"""Ingest Moh's OWN videos: drop (01_inbox), url (yt-dlp), local scan. Dedupe by
content hash; exclude PII/legal/financial by name (never a posting surface)."""
from __future__ import annotations
import hashlib, re, shutil, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, State
from fanops.ids import make_id

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
             ".jpg", ".jpeg", ".png", ".heic", ".mp3", ".wav", ".m4a"}
_PII = re.compile(r"passport|\bid\b|\bvisa\b|licen[cs]e|agreement|contract|invoice|"
                  r"\bnda\b|tax|bank|ssn|emirates.?id|national.?id", re.IGNORECASE)

def is_excluded(name: str) -> bool:
    return bool(_PII.search(name))

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def ingest_drops(led: Ledger, cfg: Config, *, origin: str = "drop") -> Ledger:
    cfg.sources.mkdir(parents=True, exist_ok=True)
    for f in sorted(cfg.inbox.rglob("*")):
        if not f.is_file() or f.name == ".gitkeep" or f.suffix.lower() not in MEDIA_EXT:
            continue
        if is_excluded(f.name):
            continue
        digest = sha256_of(f)
        if led.already_seen(sha256=digest):
            continue
        sid = make_id("src", digest)
        dest = cfg.sources / f"{sid}{f.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(f, dest)
        led.add_source(Source(id=sid, state=State.catalogued, source_path=str(dest),
                              source_origin=origin, sha256=digest,
                              meta={"original_name": f.name, "bytes": f.stat().st_size}))
    return led

def download_source(led: Ledger, cfg: Config, url: str) -> Ledger:
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    subprocess.run(["yt-dlp", "-o", str(cfg.inbox / "%(title).80s.%(ext)s"),
                    "--no-playlist", "--merge-output-format", "mp4", url],
                   check=False, capture_output=True, text=True)
    return ingest_drops(led, cfg, origin="url")

def scan_local(roots: list[Path]) -> list[str]:
    out: list[str] = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if f.is_file() and f.suffix.lower() in MEDIA_EXT and not is_excluded(f.name):
                out.append(str(f))
    return sorted(out)
```

- [ ] **Step 4: Run — expect pass** (7)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/ingest.py tests/test_ingest.py
git commit -m "feat: ingest (drop/url/scan), sha256 dedupe, PII exclusion"
```

---

## Task 8: Transcribe (local Whisper → timestamped segments)

**Files:** Create `src/fanops/transcribe.py`; Test `tests/test_transcribe.py`

- [ ] **Step 1: Failing test** (mocks the whisper subprocess — no real model run in tests)

```python
# tests/test_transcribe.py
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, State
from fanops.transcribe import whisper_cmd, transcribe_source

def test_whisper_cmd_shape():
    cmd = whisper_cmd("/s/x.mp4", "/out")
    assert cmd[0] == "whisper"
    assert "--output_format" in cmd and "json" in cmd
    assert "--output_dir" in cmd

def test_transcribe_parses_segments_and_advances(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    src = Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                 state=State.catalogued)
    led.add_source(src)
    # fake whisper: write <stem>.json into the out dir like the CLI does
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[-1]).stem
        (outdir / f"{stem}.json").write_text(json.dumps({
            "segments": [{"start": 0.0, "end": 3.0, "text": " they slept on me"},
                         {"start": 3.0, "end": 6.5, "text": " not anymore"}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)

    led = transcribe_source(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is State.transcribed
    assert s.transcript[0]["text"].strip() == "they slept on me"
    assert s.transcript[1]["end"] == 6.5

def test_transcribe_is_idempotent(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=State.transcribed,
                          transcript=[{"start": 0, "end": 1, "text": "x"}]))
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    led = transcribe_source(led, cfg, "src_1")
    spy.assert_not_called()        # already transcribed -> no re-run
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/transcribe.py
"""Local Whisper transcription (free, offline, EN/AR). Shells out to the `whisper` CLI
and parses its JSON output into [{start,end,text}] cached on the Source. Idempotent."""
from __future__ import annotations
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State

def whisper_cmd(src: str, out_dir: str, model: str = "turbo") -> list[str]:
    # --language auto-detected (handles EN + AR). turbo = fast, good enough for timestamps.
    return ["whisper", "--model", model, "--output_format", "json",
            "--output_dir", out_dir, "--task", "transcribe", src]

def transcribe_source(led: Ledger, cfg: Config, source_id: str,
                      *, model: str = "turbo") -> Ledger:
    src = led.sources[source_id]
    if src.transcript is not None:
        return led                                   # idempotent
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(whisper_cmd(src.source_path, str(out_dir), model),
                   check=False, capture_output=True, text=True)
    js = out_dir / f"{Path(src.source_path).stem}.json"
    segments = []
    if js.exists():
        data = json.loads(js.read_text())
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                    for s in data.get("segments", [])]
    src.transcript = segments
    led.set_state(source_id, State.transcribed)
    return led
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/transcribe.py tests/test_transcribe.py
git commit -m "feat: local whisper transcription -> timestamped segments (EN/AR)"
```

---

## Task 9: Signals (ffmpeg silence/scene/loudness → candidate timestamps)

**Files:** Create `src/fanops/signals.py`; Test `tests/test_signals.py`

- [ ] **Step 1: Failing test** (parse real ffmpeg stderr formats from fixture strings; mock subprocess)

```python
# tests/test_signals.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, State
from fanops.signals import parse_silences, parse_scene_changes, detect_signals

SILENCE_STDERR = """
[silencedetect @ 0x] silence_start: 2.5
[silencedetect @ 0x] silence_end: 4.0 | silence_duration: 1.5
[silencedetect @ 0x] silence_start: 9.2
[silencedetect @ 0x] silence_end: 10.0 | silence_duration: 0.8
"""
SCENE_STDERR = """
[Parsed_showinfo_1 @ 0x] n:0 pts_time:1.20 ... scene_score:0.45
[Parsed_showinfo_1 @ 0x] n:1 pts_time:6.80 ... scene_score:0.62
"""

def test_parse_silences():
    s = parse_silences(SILENCE_STDERR)
    # speech resumes at silence_end -> candidate moment starts
    assert {round(x["t"], 1) for x in s} == {4.0, 10.0}
    assert all(x["kind"] == "speech_resume" for x in s)

def test_parse_scene_changes():
    sc = parse_scene_changes(SCENE_STDERR)
    assert {round(x["t"], 1) for x in sc} == {1.2, 6.8}
    assert all(x["kind"] == "scene_cut" for x in sc)

def test_detect_signals_merges_and_advances(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=State.transcribed, duration=12.0,
                          transcript=[{"start": 0, "end": 1, "text": "x"}]))
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stderr = SILENCE_STDERR if "silencedetect" in " ".join(cmd) else SCENE_STDERR
            stdout = ""
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    led = detect_signals(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is State.signalled
    kinds = {p["kind"] for p in s.signal_peaks}
    assert "speech_resume" in kinds and "scene_cut" in kinds
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/signals.py
"""Free, local signal pass: ffmpeg silencedetect (speech onsets), scdet/showinfo
(scene cuts). Produces candidate moment timestamps the agent reasons over alongside
the transcript. (Loudness via ebur128 can be added later; silence+scene cover beat
drops and visual cuts well.)"""
from __future__ import annotations
import re, subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State

_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SCENE = re.compile(r"pts_time:([0-9.]+).*?scene_score:([0-9.]+)", re.DOTALL)

def parse_silences(stderr: str) -> list[dict]:
    # speech RESUMES at each silence_end -> a likely hook/line onset
    return [{"t": float(m), "kind": "speech_resume", "score": 0.5}
            for m in _SIL_END.findall(stderr)]

def parse_scene_changes(stderr: str) -> list[dict]:
    out = []
    for line in stderr.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        s = re.search(r"scene_score:([0-9.]+)", line)
        if m and s:
            out.append({"t": float(m.group(1)), "kind": "scene_cut",
                        "score": float(s.group(1))})
    return out

def _silence_cmd(src: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-i", src, "-af",
            "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]

def _scene_cmd(src: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-i", src, "-vf",
            "select='gt(scene,0.3)',showinfo", "-f", "null", "-"]

def detect_signals(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    sil = subprocess.run(_silence_cmd(src.source_path), check=False,
                         capture_output=True, text=True)
    sc = subprocess.run(_scene_cmd(src.source_path), check=False,
                        capture_output=True, text=True)
    peaks = parse_silences(sil.stderr) + parse_scene_changes(sc.stderr)
    peaks.sort(key=lambda p: p["t"])
    src.signal_peaks = peaks
    led.set_state(source_id, State.signalled)
    return led
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/signals.py tests/test_signals.py
git commit -m "feat: ffmpeg signal pass (silence onsets + scene cuts) -> candidates"
```

---

## Task 10: Agent-step file contract

**Files:** Create `src/fanops/agentstep.py`; Test `tests/test_agentstep.py`

> The hinge of the whole design. The code writes a `*.request.json`; the agent (human/cron/LLM) writes a `*.response.json`; the code validates it against a pydantic model. `pending()` lists requests awaiting a response so the orchestrator and digest can surface them.

- [ ] **Step 1: Failing test**

```python
# tests/test_agentstep.py
import json, pytest
from fanops.config import Config
from fanops.models import MomentDecision
from fanops.agentstep import write_request, read_response, pending, response_path

def test_write_request_creates_file(tmp_path):
    cfg = Config(root=tmp_path)
    p = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    assert p.exists() and p.name == "moments__src_1.request.json"
    assert json.loads(p.read_text())["source_id"] == "src_1"

def test_pending_lists_requests_without_responses(tmp_path):
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    write_request(cfg, kind="moments", key="src_2", payload={"source_id": "src_2"})
    # answer src_1
    response_path(cfg, "moments", "src_1").write_text(
        json.dumps({"source_id": "src_1", "picks": []}))
    pend = pending(cfg, kind="moments")
    assert pend == ["src_2"]

def test_read_response_validates_against_model(tmp_path):
    cfg = Config(root=tmp_path)
    response_path(cfg, "moments", "src_1").write_text(json.dumps({
        "source_id": "src_1",
        "picks": [{"start": 1.0, "end": 8.0, "reason": "bar lands",
                   "transcript_excerpt": "they slept on me"}]}))
    dec = read_response(cfg, "moments", "src_1", MomentDecision)
    assert isinstance(dec, MomentDecision) and dec.picks[0].end == 8.0

def test_read_response_missing_returns_none(tmp_path):
    cfg = Config(root=tmp_path)
    assert read_response(cfg, "moments", "nope", MomentDecision) is None
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/agentstep.py
"""File contract between deterministic code and the agent. Code writes <kind>__<key>.
request.json; the agent writes <kind>__<key>.response.json; code validates it against a
pydantic model. This keeps generative steps OUT of the deterministic functions while
remaining fully testable (tests drop the response file directly)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Type, TypeVar
from pydantic import BaseModel
from fanops.config import Config

T = TypeVar("T", bound=BaseModel)

def _dir(cfg: Config) -> Path:
    d = cfg.agent_io / "requests"
    d.mkdir(parents=True, exist_ok=True)
    return d

def request_path(cfg: Config, kind: str, key: str) -> Path:
    return _dir(cfg) / f"{kind}__{key}.request.json"

def response_path(cfg: Config, kind: str, key: str) -> Path:
    return _dir(cfg) / f"{kind}__{key}.response.json"

def write_request(cfg: Config, *, kind: str, key: str, payload: dict) -> Path:
    p = request_path(cfg, kind, key)
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p

def read_response(cfg: Config, kind: str, key: str, model: Type[T]) -> T | None:
    p = response_path(cfg, kind, key)
    if not p.exists():
        return None
    return model(**json.loads(p.read_text()))

def pending(cfg: Config, *, kind: str) -> list[str]:
    out = []
    for req in sorted(_dir(cfg).glob(f"{kind}__*.request.json")):
        key = req.name[len(kind) + 2:-len(".request.json")]
        if not response_path(cfg, kind, key).exists():
            out.append(key)
    return out
```

- [ ] **Step 4: Run — expect pass** (4)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/agentstep.py tests/test_agentstep.py
git commit -m "feat: agent-step file contract (request/response/pending)"
```

---

## Task 11: Moments — request from transcript+signals, ingest decision

**Files:** Create `src/fanops/moments.py`; Test `tests/test_moments.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_moments.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, State, MomentDecision, MomentPick
from fanops.agentstep import read_response, response_path
from fanops.moments import request_moments, ingest_moments

def _src(led, cfg):
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=State.signalled, duration=20.0,
                          transcript=[{"start": 0, "end": 3, "text": "intro"},
                                      {"start": 14, "end": 18, "text": "they slept on me"}],
                          signal_peaks=[{"t": 16.0, "kind": "scene_cut", "score": 0.6}]))

def test_request_moments_writes_request_with_transcript_and_signals(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    from fanops.agentstep import request_path
    import json
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["duration"] == 20.0
    assert payload["transcript"][1]["text"] == "they slept on me"
    assert payload["signal_peaks"][0]["t"] == 16.0
    assert led.sources["src_1"].state is State.moments_requested

def test_ingest_moments_creates_moment_units_with_reason(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    # agent answers
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1",
        picks=[MomentPick(start=14.0, end=18.5, reason="punchline + scene cut at 16",
                          transcript_excerpt="they slept on me", signal_score=0.6)]
    ).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    moms = led.moments_of("src_1")
    assert len(moms) == 1
    assert moms[0].reason.startswith("punchline")
    assert moms[0].start == 14.0 and moms[0].end == 18.5
    assert led.sources["src_1"].state is State.moments_decided

def test_ingest_moments_noop_without_response(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = ingest_moments(led, cfg, "src_1")     # no response yet
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is State.moments_requested
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/moments.py
"""The clip DECISION stage. request_moments() packages transcript+signals (+ guidance
from context.md) into an agent request. ingest_moments() turns the agent's MomentDecision
into Moment units, each carrying the REASON it was chosen. No tiers, no quotas — the agent
returns as many picks as are worth posting."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentRequest, MomentDecision, State
from fanops.ids import child_id
from fanops.agentstep import write_request, read_response

def _guidance(cfg: Config) -> str:
    return cfg.context_path.read_text() if cfg.context_path.exists() else ""

def request_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    req = MomentRequest(source_id=source_id, duration=src.duration or 0.0,
                        transcript=src.transcript or [],
                        signal_peaks=src.signal_peaks or [],
                        guidance=_guidance(cfg))
    write_request(cfg, kind="moments", key=source_id, payload=req.model_dump())
    led.set_state(source_id, State.moments_requested)
    return led

def ingest_moments(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    dec = read_response(cfg, "moments", source_id, MomentDecision)
    if dec is None:
        return led                                  # still pending
    for i, pick in enumerate(dec.picks):
        mid = child_id("moment", source_id, i)
        led.add_moment(Moment(id=mid, parent_id=source_id, state=State.moments_decided,
                              start=pick.start, end=pick.end, reason=pick.reason,
                              transcript_excerpt=pick.transcript_excerpt,
                              signal_score=pick.signal_score))
    led.set_state(source_id, State.moments_decided)
    return led
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/moments.py tests/test_moments.py
git commit -m "feat: moments — agent decides clip-worthy moments WITH reasons"
```

---

## Task 12: Clip render (ffmpeg cut + reframe per Moment)

**Files:** Create `src/fanops/clip.py`; Test `tests/test_clip.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_clip.py
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, State, Fmt
from fanops.clip import ffmpeg_clip_cmd, render_moment

def test_clip_cmd_cuts_and_reframes():
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "9:16")
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "1.5" in cmd and "-to" in cmd and "8.0" in cmd
    assert any("crop" in p or "scale" in p for p in cmd)
    assert cmd[-1] == "/o/c.mp4"

def test_render_moment_creates_clip(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4")))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7,
                          reason="r", state=State.moments_decided))
    def fake_run(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.parent_id == "mom_1" and clip.state is State.rendered
    assert clip.aspect is Fmt.r9x16 and clip.id in led.clips
    assert led.moments["mom_1"].state is State.clipped
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/clip.py
"""Render a Moment into a platform-ready clip: frame-accurate ffmpeg cut + reframe to
target aspect. Default 9:16 (IG Reels / TikTok)."""
from __future__ import annotations
import subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, State, Fmt
from fanops.ids import child_id

_ASPECT_VF = {
    "9:16": "crop=ih*9/16:ih,scale=1080:1920",
    "1:1":  "crop=ih:ih,scale=1080:1080",
    "16:9": "scale=1920:1080",
}

def ffmpeg_clip_cmd(src: str, dst: str, start: float, end: float, aspect: str) -> list[str]:
    return ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", src,
            "-vf", _ASPECT_VF[aspect], "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart", dst]

def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16) -> tuple[Ledger, Clip]:
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    idx = sum(1 for c in led.clips.values() if c.parent_id == moment_id)
    cid = child_id("clip", moment_id, idx)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    subprocess.run(ffmpeg_clip_cmd(src.source_path, str(dst), m.start, m.end, aspect.value),
                   check=False, capture_output=True, text=True)
    clip = Clip(id=cid, parent_id=moment_id, state=State.rendered, path=str(dst), aspect=aspect)
    led.add_clip(clip)
    led.set_state(moment_id, State.clipped)
    return led, clip
```

- [ ] **Step 4: Run — expect pass** (2)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/clip.py tests/test_clip.py
git commit -m "feat: clip render — frame-accurate cut + reframe per Moment"
```

---

## Task 13: Accounts (flat active registry, no lanes)

**Files:** Create `src/fanops/accounts.py`, seed `MohFlow-FanOps/00_control/accounts.json`; Test `tests/test_accounts.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_accounts.py
import json
from fanops.config import Config
from fanops.accounts import Accounts, Account

def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def test_load_and_active(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "platforms": ["instagram"], "status": "planned"},
    ])
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.active()] == ["@a"]

def test_no_secret_fields(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    dumped = Accounts.load(cfg).accounts[0].model_dump()
    assert not any(k in dumped for k in ("password", "token", "secret", "credential"))

def test_surfaces_matrix(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "platforms": ["tiktok"], "status": "active"},
    ])
    accts = Accounts.load(cfg)
    # every (account, platform) pair for active accounts
    pairs = {(s.account, s.platform.value) for s in accts.surfaces()}
    assert pairs == {("@a", "instagram"), ("@a", "tiktok"), ("@b", "tiktok")}
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/accounts.py
"""Flat active-account registry — non-secret metadata only. No lanes: every active
account participates in cross-posting. surfaces() yields each (account, platform) pair."""
from __future__ import annotations
import json
from enum import Enum
from typing import Optional, NamedTuple
from pydantic import BaseModel, Field
from fanops.config import Config
from fanops.models import Platform

class AccountStatus(str, Enum):
    planned = "planned"; warming = "warming"; active = "active"; retired = "retired"

class Account(BaseModel):
    handle: str
    platforms: list[Platform] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.planned
    access: str = "blotato"               # METHOD, never a credential
    persona: Optional[str] = None         # free-text personality hint for caption variation

class Surface(NamedTuple):
    account: str
    platform: Platform

class Accounts:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.accounts: list[Account] = []

    @classmethod
    def load(cls, cfg: Config) -> "Accounts":
        a = cls(cfg)
        if cfg.accounts_path.exists():
            raw = json.loads(cfg.accounts_path.read_text())
            a.accounts = [Account(**x) for x in raw.get("accounts", [])]
        return a

    def active(self) -> list[Account]:
        return [a for a in self.accounts if a.status is AccountStatus.active]

    def surfaces(self) -> list[Surface]:
        return [Surface(a.handle, p) for a in self.active() for p in a.platforms]
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Seed accounts.json**

Write `MohFlow-FanOps/00_control/accounts.json`:
```json
{
  "accounts": [
    {"handle": "@TBD-1", "platforms": ["instagram", "tiktok"], "status": "planned", "access": "blotato", "persona": "fast cinematic edits, hype energy"},
    {"handle": "@TBD-2", "platforms": ["instagram", "tiktok"], "status": "planned", "access": "blotato", "persona": "raw studio + lyric-forward"}
  ]
}
```
(`@TBD-*` + `planned` keep them out of rotation until Moh creates real accounts.)

- [ ] **Step 6: Commit**

```bash
git add src/fanops/accounts.py tests/test_accounts.py MohFlow-FanOps/00_control/accounts.json
git commit -m "feat: flat active-account registry + (account,platform) surfaces"
```

---

## Task 14: Captions — request per surface, ingest set, brand-risk hold

**Files:** Create `src/fanops/caption.py`; Test `tests/test_caption.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_caption.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, State, Platform, CaptionSet, CaptionItem
from fanops.agentstep import response_path
from fanops.caption import brand_risk_flag, request_captions, ingest_captions

def _clip(led, cfg):
    led.add_source(Source(id="src_1", source_path="/s.mp4"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=State.rendered))

def test_brand_risk_flags_offbrand():
    assert brand_risk_flag("sorry pls stream my song 🥺") is not None
    assert brand_risk_flag("link in bio, official drop from the label") is not None
    assert brand_risk_flag("no warning. just impact. 🔥") is None

def test_request_captions_writes_one_request_per_surface(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    surfaces = [("@a", Platform.instagram), ("@a", Platform.tiktok)]
    led = request_captions(led, cfg, "clip_1", surfaces)
    import json
    from fanops.agentstep import request_path
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert {s["surface"] for s in payload["surfaces"]} == {"@a/instagram", "@a/tiktok"}
    assert payload["transcript_excerpt"] == "they slept on me"
    assert led.clips["clip_1"].state is State.captions_requested

def test_ingest_captions_clean_advances(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    hashtags=["#mohflow"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    assert led.clips["clip_1"].state is State.captioned
    assert led.clips["clip_1"].held is False
    # stored for crosspost to consume
    assert led.clips["clip_1"].meta_captions["@a/instagram"]["caption"].startswith("no warning")

def test_ingest_captions_offbrand_holds_first_class(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(items=[
        CaptionItem(surface="@a/instagram", caption="pls stream 🥺 sorry")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and "bravado" in c.held_reason
    assert c.state is State.rendered             # held clips do not advance
```

> Note: `meta_captions` is a per-clip dict on the Clip model. Add it in Step 3a.

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3a: Add `meta_captions` to the Clip model**

In `src/fanops/models.py`, add to `Clip` (after `tagged_artist`):
```python
    meta_captions: dict = Field(default_factory=dict)   # surface -> {caption, hashtags}
```

- [ ] **Step 3b: Implement caption.py**

```python
# src/fanops/caption.py
"""Caption stage. request_captions() asks the agent for a per-surface caption set
(different wording per surface — opsec + platform fit). ingest_captions() validates each,
runs the brand-risk HOLD (anti-pattern logic, not a gate), stores clean captions on the
clip for crosspost, and advances only if nothing is held."""
from __future__ import annotations
import re
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State, Platform, CaptionRequest, CaptionSet
from fanops.agentstep import write_request, read_response

# Off-brand / breaks-bravado / main-brand-linkage anti-patterns (from the social skills,
# adapted for the fan-op: begging, victimhood, "official"/label framing, "link in bio").
_OFFBRAND = [r"\bsorry\b", r"\bpls\b", r"\bplease stream\b", r"🥺", r"\bbeg(ging)?\b",
             r"\bofficial (drop|release)\b", r"\bfrom the label\b", r"\blink in bio\b"]
_RE = re.compile("|".join(_OFFBRAND), re.IGNORECASE)

def brand_risk_flag(caption: str) -> str | None:
    m = _RE.search(caption or "")
    return (f"off-brand / breaks bravado guardrail: matched '{m.group(0)}'") if m else None

def _guidance(cfg: Config) -> str:
    return cfg.context_path.read_text() if cfg.context_path.exists() else ""

def request_captions(led: Ledger, cfg: Config, clip_id: str,
                     surfaces: list[tuple[str, Platform]]) -> Ledger:
    clip = led.clips[clip_id]
    moment = led.moments[clip.parent_id]
    payload = {
        "clip_id": clip_id,
        "transcript_excerpt": moment.transcript_excerpt,
        "guidance": _guidance(cfg),
        "surfaces": [{"surface": f"{acct}/{plat.value}", "platform": plat.value}
                     for acct, plat in surfaces],
    }
    write_request(cfg, kind="captions", key=clip_id, payload=payload)
    led.set_state(clip_id, State.captions_requested)
    return led

def ingest_captions(led: Ledger, cfg: Config, clip_id: str) -> Ledger:
    cs = read_response(cfg, "captions", clip_id, CaptionSet)
    if cs is None:
        return led
    clip = led.clips[clip_id]
    held_reason = None
    for item in cs.items:
        reason = brand_risk_flag(item.caption)
        if reason and held_reason is None:
            held_reason = reason
        clip.meta_captions[item.surface] = {"caption": item.caption,
                                            "hashtags": item.hashtags}
    if held_reason:
        clip.held = True
        clip.held_reason = held_reason
        return led                                   # stays rendered; surfaced as hold
    clip.held = False
    led.set_state(clip_id, State.captioned)
    return led
```

- [ ] **Step 4: Run — expect pass** (4)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/models.py src/fanops/caption.py tests/test_caption.py
git commit -m "feat: per-surface captions + first-class brand-risk hold"
```

---

## Task 15: Tagging — subtle, non-synchronized artist @mention

**Files:** Create `src/fanops/tagging.py`; Test `tests/test_tagging.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_tagging.py
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.tagging import should_tag, decide_tag, ARTIST_HANDLE

def test_should_tag_minority_and_deterministic():
    n = sum(should_tag(f"clip{i}", "@a", rate=0.25) for i in range(100))
    assert 10 <= n <= 45
    assert should_tag("c", "@a", rate=0.25) == should_tag("c", "@a", rate=0.25)

def test_decide_tag_respects_no_sync_window(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    t0 = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    ok1 = decide_tag(led, account="@a", when=t0, force=True, min_gap_minutes=120)
    assert ok1 is True and "@a" in led.tag_log
    ok2 = decide_tag(led, account="@b", when=t0 + timedelta(minutes=30),
                     force=True, min_gap_minutes=120)
    assert ok2 is False         # another account tagged within the window
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/tagging.py
"""Subtle, NON-SYNCHRONIZED artist tagging. A minority of posts carry a buried @mohflow
(decided deterministically), and never two accounts within min_gap_minutes (tracked on
ledger.tag_log). decide_tag() returns whether THIS post may tag; the caption that lands
the tag is appended by crosspost on its own line, never in the hook."""
from __future__ import annotations
import hashlib
from datetime import datetime
from fanops.ledger import Ledger

ARTIST_HANDLE = "@mohflow"

def should_tag(clip_id: str, account: str, *, rate: float = 0.25) -> bool:
    h = int(hashlib.sha1(f"{clip_id}|{account}".encode()).hexdigest()[:8], 16)
    return (h % 1000) / 1000.0 < rate

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def decide_tag(led: Ledger, *, account: str, when: datetime,
               rate: float = 0.25, min_gap_minutes: int = 120, force: bool = False) -> bool:
    if not force and not should_tag("", account, rate=rate):
        return False
    for _, ts in led.tag_log.items():
        if abs((when - _parse(ts)).total_seconds()) / 60.0 < min_gap_minutes:
            return False
    led.tag_log[account] = when.isoformat().replace("+00:00", "Z")
    return True
```

- [ ] **Step 4: Run — expect pass** (2)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/tagging.py tests/test_tagging.py
git commit -m "feat: subtle non-synchronized artist tagging (ledger tag_log)"
```

---

## Task 16: Crosspost — fan-out clip × every account × platform (staggered)

**Files:** Create `src/fanops/crosspost.py`; Test `tests/test_crosspost.py`

> The spine. One captioned, non-held clip → one Post per (active account, platform), each
> with its own jittered time and its own per-surface caption. Stable post id (idempotent).

- [ ] **Step 1: Failing test**

```python
# tests/test_crosspost.py
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, State, Platform
from fanops.accounts import Accounts
from fanops.crosspost import surface_time, crosspost_clips

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _captioned_clip(led, cfg, cid="clip_1"):
    led.add_source(Source(id="src_1", source_path="/s.mp4"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r"))
    clip = Clip(id=cid, parent_id="mom_1", path=f"/{cid}.mp4", state=State.captioned)
    clip.meta_captions = {
        "@a/instagram": {"caption": "ig cap", "hashtags": ["#x"]},
        "@a/tiktok": {"caption": "tt cap", "hashtags": ["#y"]},
    }
    led.add_clip(clip)

def test_surface_time_distinct_per_surface():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    t1 = surface_time(base, "@a", "instagram", "2026-06-02", index=0)
    t2 = surface_time(base, "@a", "tiktok", "2026-06-02", index=0)
    assert t1 != t2 and (t1.endswith("Z") and t2.endswith("Z"))

def test_crosspost_fans_out_to_every_surface(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram", "tiktok"],
                          "status": "active"}])
    led = Ledger.load(cfg); _captioned_clip(led, cfg)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = [p for p in led.posts.values() if p.parent_id == "clip_1"]
    assert len(posts) == 2                          # IG + TikTok
    surfaces = {(p.account, p.platform.value) for p in posts}
    assert surfaces == {("@a", "instagram"), ("@a", "tiktok")}
    # per-surface caption used
    ig = next(p for p in posts if p.platform is Platform.instagram)
    tt = next(p for p in posts if p.platform is Platform.tiktok)
    assert ig.caption == "ig cap" and tt.caption == "tt cap"
    assert ig.scheduled_time != tt.scheduled_time   # staggered, not synchronized
    assert led.clips["clip_1"].state is State.queued

def test_crosspost_skips_held_clip(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned_clip(led, cfg)
    led.clips["clip_1"].held = True
    led.clips["clip_1"].state = State.captioned
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values() if p.parent_id == "clip_1"] == []

def test_crosspost_idempotent_no_dup(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned_clip(led, cfg)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.clips["clip_1"].state = State.captioned       # simulate sloppy re-run
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len([p for p in led.posts.values() if p.parent_id == "clip_1"]) == 1
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/crosspost.py
"""Cross-post fan-out: one captioned, non-held clip -> one Post per (active account,
platform). Each surface gets its OWN jittered time (per-surface anchor + seed, so the
network never posts in a synchronized burst) and its OWN caption variation. Stable post
id keyed on (clip, surface) -> idempotent."""
from __future__ import annotations
import hashlib, random
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import Post, State, Platform
from fanops.ids import child_id

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def _seed(account: str, platform: str, date_str: str) -> int:
    h = hashlib.sha1(f"{account}|{platform}|{date_str}".encode()).hexdigest()
    return int(h[:8], 16)

def surface_time(base: datetime, account: str, platform: str, date_str: str,
                 index: int) -> str:
    rng = random.Random(_seed(account, platform, date_str) ^ index)
    anchor = base + timedelta(minutes=_seed(account, platform, date_str) % 50)
    t = anchor + timedelta(minutes=index * rng.randint(35, 95) + rng.randint(-7, 7))
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def crosspost_clips(led: Ledger, cfg: Config, accounts: Accounts, *,
                    base_time: str) -> Ledger:
    base = _parse(base_time)
    date_str = base.date().isoformat()
    surfaces = accounts.surfaces()
    for clip in [c for c in led.clips_in_state(State.captioned) if not c.held]:
        for i, surf in enumerate(surfaces):
            key = f"{surf.account}|{surf.platform.value}"
            pid = child_id("post", clip.id, abs(int(hashlib.sha1(key.encode()).hexdigest()[:6], 16)))
            cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}",
                                         clip.meta_captions.get("default", {"caption": "", "hashtags": []}))
            led.add_post(Post(
                id=pid, parent_id=clip.id, state=State.queued,
                account=surf.account, platform=surf.platform,
                caption=cap["caption"], hashtags=cap.get("hashtags", []),
                scheduled_time=surface_time(base, surf.account, surf.platform.value, date_str, i),
                status="queued",
            ))
        led.set_state(clip.id, State.queued)
    return led
```

- [ ] **Step 4: Run — expect pass** (4)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/crosspost.py tests/test_crosspost.py
git commit -m "feat: cross-post fan-out to every account x platform, staggered"
```

---

## Task 17: Blotato payload builders + per-platform target fields

**Files:** Create `src/fanops/post/__init__.py`, `src/fanops/post/payload.py`; Test `tests/test_payload.py`

- [ ] **Step 1: Create the post package dir + failing test**

```bash
mkdir -p src/fanops/post
```

```python
# tests/test_payload.py
from fanops.post.payload import (build_blotato_payload, build_blotato_mcp_args,
                                 default_target_fields)

def test_nested_rest_minimal():
    p = build_blotato_payload(account_id="1", platform="twitter", text="hi",
                              media_urls=[], scheduled_time=None)
    assert p["post"]["accountId"] == "1"
    assert p["post"]["content"]["platform"] == p["post"]["target"]["targetType"] == "twitter"

def test_schedule_is_root_level():
    p = build_blotato_payload(account_id="1", platform="instagram", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time="2026-06-01T18:00:00Z")
    assert p["scheduledTime"] == "2026-06-01T18:00:00Z" and "scheduledTime" not in p["post"]

def test_target_fields_per_platform():
    tk = default_target_fields("tiktok")
    for k in ("privacyLevel", "disabledComments", "disabledDuet", "disabledStitch",
              "isBrandedContent", "isYourBrand", "isAiGenerated"):
        assert k in tk
    yt = default_target_fields("youtube", title="T")
    assert yt["title"] == "T" and "privacyStatus" in yt
    assert default_target_fields("twitter") == {}

def test_tiktok_payload_has_required_fields():
    p = build_blotato_payload(account_id="1", platform="tiktok", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time=None,
                              extra_target=default_target_fields("tiktok"))
    assert p["post"]["target"]["privacyLevel"]

def test_mcp_args_flat():
    a = build_blotato_mcp_args(account_id="1", platform="instagram", text="hi",
                               media_urls=["https://h/v.mp4"], scheduled_time="2026-06-02T18:00:00Z",
                               media_type="reel")
    assert a["accountId"] == "1" and a["mediaUrls"] == ["https://h/v.mp4"]
    assert a["mediaType"] == "reel"
    assert "post" not in a and "content" not in a
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement `payload.py` + `__init__.py`**

```python
# src/fanops/post/payload.py
"""Blotato request bodies. REST POST /posts is NESTED (post.content/post.target);
official MCP blotato_create_post is FLAT. content.platform == target.targetType.
scheduledTime is a ROOT sibling of post. Per-platform target fields required or 422
(TikTok x7, YouTube title+privacyStatus+notify, Facebook pageId). Verified 2026-05-31."""
from __future__ import annotations

def default_target_fields(platform: str, *, title: str | None = None,
                          page_id: str | None = None, media_type: str | None = None) -> dict:
    if platform == "tiktok":
        return {"privacyLevel": "PUBLIC_TO_EVERYONE", "disabledComments": False,
                "disabledDuet": False, "disabledStitch": False, "isBrandedContent": False,
                "isYourBrand": False, "isAiGenerated": False}
    if platform == "youtube":
        return {"title": title or "Moh Flow", "privacyStatus": "public",
                "shouldNotifySubscribers": False}
    if platform == "facebook":
        out: dict = {}
        if page_id: out["pageId"] = page_id
        if media_type: out["mediaType"] = media_type
        return out
    if platform == "instagram" and media_type:
        return {"mediaType": media_type}
    return {}

def build_blotato_payload(*, account_id: str, platform: str, text: str,
                          media_urls: list[str], scheduled_time: str | None,
                          media_type: str | None = None, use_next_free_slot: bool = False,
                          extra_target: dict | None = None) -> dict:
    target: dict = {"targetType": platform}
    if media_type and platform in ("instagram", "facebook"):
        target["mediaType"] = media_type
    if extra_target:
        target.update(extra_target)
    payload: dict = {"post": {"accountId": account_id,
                              "content": {"text": text, "mediaUrls": media_urls, "platform": platform},
                              "target": target}}
    if scheduled_time:
        payload["scheduledTime"] = scheduled_time
    elif use_next_free_slot:
        payload["useNextFreeSlot"] = True
    return payload

def build_blotato_mcp_args(*, account_id: str, platform: str, text: str,
                           media_urls: list[str], scheduled_time: str | None,
                           media_type: str | None = None, extra: dict | None = None) -> dict:
    args: dict = {"accountId": account_id, "platform": platform, "text": text,
                  "mediaUrls": media_urls}
    if scheduled_time: args["scheduledTime"] = scheduled_time
    if media_type: args["mediaType"] = media_type
    if extra: args.update(extra)
    return args
```

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

- [ ] **Step 4: Run — expect pass** (5)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/__init__.py src/fanops/post/payload.py tests/test_payload.py
git commit -m "feat: blotato payload builders (nested REST + flat MCP) + target fields"
```

---

## Task 18: Media upload + dry-run poster

**Files:** Create `src/fanops/post/media.py`, `src/fanops/post/dryrun.py`; Test `tests/test_media.py`, `tests/test_dryrun.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_media.py
from fanops.config import Config
from fanops.post.media import upload_media, dryrun_media_url

def test_dryrun_url(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    assert dryrun_media_url(f).startswith("file://") and "v.mp4" in dryrun_media_url(f)

def test_upload_presign_then_put(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k=")
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    class _R:
        def __init__(s, c, b=None): s.status_code = c; s._b = b or {}; s.text = str(s._b)
        def json(s): return s._b
    pm = mocker.patch("fanops.post.media.requests.post",
                      return_value=_R(200, {"presignedUrl": "https://up/a", "publicUrl": "https://cdn/c.mp4"}))
    put = mocker.patch("fanops.post.media.requests.put", return_value=_R(200))
    assert upload_media(cfg, f) == "https://cdn/c.mp4"
    assert pm.call_args.kwargs["json"]["filename"] == "c.mp4"
    assert pm.call_args.kwargs["headers"]["blotato-api-key"] == "k="
    assert put.call_args.args[0] == "https://up/a"
```

```python
# tests/test_dryrun.py
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, Platform
from fanops.post import get_poster
from fanops.post.dryrun import DryRunPoster

def test_factory_defaults_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert isinstance(get_poster(Config(root=tmp_path)), DryRunPoster)

def test_dryrun_writes_payload_with_media(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="98432", platform=Platform.instagram,
                      caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=State.queued))
    led = DryRunPoster(cfg).publish(led, "p1")
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    assert body["post"]["content"]["text"] == "hello"
    assert body["post"]["content"]["mediaUrls"] == ["https://h/v.mp4"]
    assert led.posts["p1"].status == "submitted"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/post/media.py
"""Upload a local file to Blotato -> public URL (POST /media/uploads -> presignedUrl/
publicUrl; PUT binary). dryrun returns file:// so the pipeline runs without network."""
from __future__ import annotations
import mimetypes
from pathlib import Path
import requests
from fanops.config import Config

BASE_URL = "https://backend.blotato.com/v2"

def dryrun_media_url(path: Path) -> str:
    return f"file://{Path(path).resolve()}"

def upload_media(cfg: Config, path: Path) -> str:
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

```python
# src/fanops/post/dryrun.py
"""Dry-run poster: writes the exact payload it WOULD send (with media + target fields),
posts nothing. Active until Blotato is connected."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.post.payload import build_blotato_payload, default_target_fields

class DryRunPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value))
        self.cfg.scheduled.mkdir(parents=True, exist_ok=True)
        (self.cfg.scheduled / f"{post_id}.json").write_text(json.dumps(payload, indent=2))
        post.status = "submitted"
        return led
```

- [ ] **Step 4: Run — expect pass** (media 2, dryrun 2)

Run: `./.venv/bin/pytest tests/test_media.py tests/test_dryrun.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/media.py src/fanops/post/dryrun.py tests/test_media.py tests/test_dryrun.py
git commit -m "feat: blotato media upload + dry-run poster with media"
```

---

## Task 19: Blotato REST + MCP backends

**Files:** Create `src/fanops/post/blotato_rest.py`, `src/fanops/post/blotato_mcp.py`; Test `tests/test_blotato_rest.py`, `tests/test_blotato_mcp.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_blotato_rest.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, Platform
from fanops.post.blotato_rest import BlotatoRestPoster

class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def test_header_url_and_submission(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "secret==")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="98432", platform=Platform.twitter,
                      caption="hi", scheduled_time="2026-06-01T18:00:00Z", state=State.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      return_value=_R(200, {"postSubmissionId": "s_1"}))
    led = BlotatoRestPoster(cfg).publish(led, "p1")
    assert pm.call_args.args[0] == "https://backend.blotato.com/v2/posts"
    assert pm.call_args.kwargs["headers"]["blotato-api-key"] == "secret=="
    assert led.posts["p1"].status == "submitted" and led.posts["p1"].submission_id == "s_1"

def test_4xx_marks_failed(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k=")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="c", account="1", platform=Platform.tiktok,
                      caption="x", media_urls=["https://h/v.mp4"], state=State.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(422, {"e": "bad"}))
    led = BlotatoRestPoster(cfg).publish(led, "p2")
    assert led.posts["p2"].status == "failed"
    # tiktok required fields were present in the attempt
    assert pm.call_args.kwargs["json"]["post"]["target"]["privacyLevel"]
```

```python
# tests/test_blotato_mcp.py
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, Platform
from fanops.post.blotato_mcp import BlotatoMcpPoster

def test_flat_args(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="98432", platform=Platform.instagram,
                      caption="the one", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=State.queued))
    calls = []
    poster = BlotatoMcpPoster(cfg, tool_caller=lambda n, a: calls.append((n, a)) or {"postSubmissionId": "s9"})
    led = poster.publish(led, "p1")
    n, a = calls[0]
    assert n == "blotato_create_post" and a["accountId"] == "98432"
    assert a["mediaUrls"] == ["https://h/v.mp4"] and "post" not in a
    assert led.posts["p1"].submission_id == "s9"

def test_raises_without_caller(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="c", account="1", platform=Platform.twitter,
                      caption="x", state=State.queued))
    with pytest.raises(RuntimeError):
        BlotatoMcpPoster(cfg, tool_caller=None).publish(led, "p2")
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/post/blotato_rest.py
"""Blotato v2 REST backend (fallback). Verified 2026-05-31. Reads post.media_urls,
fills per-platform target fields so TikTok/YouTube/FB don't 422."""
from __future__ import annotations
import requests
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.post.payload import build_blotato_payload, default_target_fields

BASE_URL = "https://backend.blotato.com/v2"

class BlotatoRestPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise RuntimeError("BLOTATO_API_KEY missing — cannot use REST backend.")
        self.headers = {"blotato-api-key": key, "Content-Type": "application/json"}

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value))
        resp = requests.post(f"{BASE_URL}/posts", headers=self.headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            post.status = "submitted"
            try: post.submission_id = resp.json().get("postSubmissionId")
            except Exception: pass
        else:
            post.status = "failed"
            post.metrics["error"] = f"blotato {resp.status_code}: {resp.text[:200]}"
        return led
```

```python
# src/fanops/post/blotato_mcp.py
"""Blotato MCP backend (primary). Maps a Post to FLAT blotato_create_post args.
tool_caller(name, args)->dict is injected (the runtime agent wires the real MCP tool);
unit-tested with a fake. No caller -> raises."""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.post.payload import build_blotato_mcp_args, default_target_fields

ToolCaller = Callable[[str, dict], dict]

class BlotatoMcpPoster:
    def __init__(self, cfg: Config, tool_caller: ToolCaller | None = None):
        self.cfg = cfg
        self._call = tool_caller

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        if self._call is None:
            raise RuntimeError("BlotatoMcpPoster needs a tool_caller wired to blotato_create_post.")
        args = build_blotato_mcp_args(
            account_id=post.account, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra=default_target_fields(post.platform.value) or None)
        result = self._call("blotato_create_post", args)
        post.status = "submitted"
        post.submission_id = (result or {}).get("postSubmissionId")
        return led
```

- [ ] **Step 4: Run — expect pass** (rest 2, mcp 2)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/blotato_rest.py src/fanops/post/blotato_mcp.py tests/test_blotato_rest.py tests/test_blotato_mcp.py
git commit -m "feat: blotato REST + MCP backends"
```

---

## Task 20: Post run — upload media, publish queue, advance

**Files:** Create `src/fanops/post/run.py`; Test `tests/test_post_run.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_post_run.py
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, State, Platform
from fanops.post.run import publish_due

def _queued(led, cfg, pid="p1", cid="clip_1"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=State.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="98432", platform=Platform.instagram,
                      caption="ship it", scheduled_time="2026-06-02T18:00:00Z", state=State.queued))

def test_publish_uploads_media_and_advances(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)  # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _queued(led, cfg)
    led = publish_due(led, cfg)
    assert led.posts["p1"].status == "submitted" and led.posts["p1"].state is State.published
    assert led.posts["p1"].media_urls[0].startswith("file://")
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    assert body["post"]["content"]["mediaUrls"][0].startswith("file://")

def test_publish_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _queued(led, cfg)
    led = publish_due(led, cfg)
    led = publish_due(led, cfg)
    assert led.posts["p1"].state is State.published
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/post/run.py
"""Publish stage: for each queued post, ensure media is uploaded (file:// in dry-run,
real under rest/mcp), publish via the configured backend, advance survivors to published.
A failed submit goes terminal (analyzed) — no infinite retry. Held clips can't reach here
(crosspost skips them)."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State
from fanops.post import get_poster
from fanops.post.media import upload_media, dryrun_media_url

def _ensure_media(led: Ledger, cfg: Config, post) -> None:
    if post.media_urls:
        return
    clip = led.clips.get(post.parent_id)
    if clip is None or not clip.path:
        return
    path = Path(clip.path)
    post.media_urls = ([dryrun_media_url(path)] if cfg.poster_backend == "dryrun"
                       else [upload_media(cfg, path)])

def publish_due(led: Ledger, cfg: Config) -> Ledger:
    poster = get_poster(cfg)
    for post in led.posts_in_state(State.queued):
        _ensure_media(led, cfg, post)
        led = poster.publish(led, post.id)
        if post.status == "submitted":
            post.state = State.published
        elif post.status == "failed":
            post.state = State.analyzed
    return led
```

- [ ] **Step 4: Run — expect pass** (2)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/post/run.py tests/test_post_run.py
git commit -m "feat: publish stage — upload media, publish, advance, no infinite retry"
```

---

## Task 21: Track — pull metrics, lift weighting

**Files:** Create `src/fanops/track.py`; Test `tests/test_track.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_track.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, State, Platform
from fanops.track import lift_score, record_metrics, pull_metrics

def test_lift_weights_saves_shares_over_likes():
    hi = lift_score({"likes": 10, "saves": 50, "shares": 40, "retention": 0.8, "reach": 1000})
    lo = lift_score({"likes": 500, "saves": 1, "shares": 0, "retention": 0.1, "reach": 1000})
    assert hi > lo

def test_record_advances(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", platform=Platform.instagram,
                      caption="x", state=State.published))
    led = record_metrics(led, "p1", {"saves": 20, "shares": 12, "retention": 0.7})
    assert led.posts["p1"].metrics["saves"] == 20 and "lift_score" in led.posts["p1"].metrics
    assert led.posts["p1"].state is State.analyzed

def test_pull_matches_by_submission_id(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", platform=Platform.instagram,
                      caption="x", state=State.published, submission_id="s_A"))
    led.add_post(Post(id="p2", parent_id="c", account="@a", platform=Platform.tiktok,
                      caption="y", state=State.published, submission_id="s_B"))
    rows = [{"postSubmissionId": "s_A", "metrics": {"saves": 30, "shares": 25, "retention": 0.8}},
            {"postSubmissionId": "s_B", "metrics": {"likes": 50, "saves": 1, "retention": 0.1}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    assert led.posts["p1"].metrics["saves"] == 30
    assert led.posts["p1"].metrics["lift_score"] > led.posts["p2"].metrics["lift_score"]
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/track.py
"""Track stage: pull + record per-post performance. saves/shares/retention = algorithmic
lift; likes ~ noise. The runtime agent injects list_posts bound to Blotato blotato_list_posts;
rows match ledger posts by submission_id."""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State

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

def pull_metrics(led: Ledger, cfg: Config, *, list_posts: ListPosts, window: str = "30d") -> Ledger:
    by_sub = {p.submission_id: p for p in led.posts.values() if p.submission_id}
    for row in list_posts(window):
        post = by_sub.get(row.get("postSubmissionId"))
        if post is not None:
            record_metrics(led, post.id, row.get("metrics", {}))
    return led
```

- [ ] **Step 4: Run — expect pass** (3)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/track.py tests/test_track.py
git commit -m "feat: track — metric pull + lift weighting"
```

---

## Task 22: Adjust — amplify winners (re-request moments in vein), retire

**Files:** Create `src/fanops/adjust.py`; Test `tests/test_adjust.py`

> Amplify = take the winning post's SOURCE and re-open a fresh moment request with the
> winner's signature as guidance ("more like the moment at 0:14 that hit"). No canned
> phrases — it sends the system back to find more of what worked.

- [ ] **Step 1: Failing test**

```python
# tests/test_adjust.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, Moment, Source, State, Platform
from fanops.adjust import classify_outcomes, amplify, retire

def _chain(led, lift, pid="p1", cid="clip_1", mid="mom_1", sid="src_1"):
    led.add_source(Source(id=sid, source_path="/s.mp4", state=State.moments_decided))
    led.add_moment(Moment(id=mid, parent_id=sid, start=14, end=21,
                          reason="punchline + beat drop", transcript_excerpt="they slept on me"))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c.mp4", state=State.analyzed))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", platform=Platform.instagram,
                      caption="x", state=State.analyzed, metrics={"lift_score": lift}))

def test_classify_splits(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    for pid, l in [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1)]:
        led.add_post(Post(id=pid, parent_id="c", account="@a", platform=Platform.instagram,
                          caption="x", state=State.analyzed, metrics={"lift_score": l}))
    r = classify_outcomes(led, winner_pct=0.5)
    assert set(r["winners"]) == {"p1", "p3"} and set(r["losers"]) == {"p2", "p4"}

def test_classify_empty(tmp_path):
    assert classify_outcomes(Ledger.load(Config(root=tmp_path))) == {"winners": [], "losers": []}

def test_amplify_reopens_moment_request_in_vein(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _chain(led, 400)
    led = amplify(led, cfg, ["p1"])
    # the winner's source is sent back for another moment pass, with the winning signature
    import json
    from fanops.agentstep import request_path
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert "they slept on me" in payload["guidance"]
    assert led.sources["src_1"].state is State.moments_requested

def test_retire_marks_lineage(tmp_path):
    led = Ledger.load(Config(root=tmp_path)); _chain(led, 1, pid="pL", cid="cL")
    led = retire(led, ["pL"])
    assert "cL" in led.retired
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/adjust.py
"""Adjust stage: rank analyzed posts by lift. AMPLIFY = re-open a moment request on the
winner's SOURCE, injecting the winning moment's signature (transcript excerpt + reason)
as guidance so the agent finds more in that vein. RETIRE = flag the loser clip lineage."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentRequest, State
from fanops.agentstep import write_request

def classify_outcomes(led: Ledger, *, winner_pct: float = 0.3) -> dict:
    analyzed = [p for p in led.posts.values() if p.state is State.analyzed]
    if not analyzed:
        return {"winners": [], "losers": []}
    ranked = sorted(analyzed, key=lambda p: p.metrics.get("lift_score", 0.0), reverse=True)
    cut = max(1, round(len(ranked) * winner_pct))
    return {"winners": [p.id for p in ranked[:cut]], "losers": [p.id for p in ranked[cut:]]}

def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str]) -> Ledger:
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        clip = led.clips.get(post.parent_id)
        if clip is None:
            continue
        moment = led.moments.get(clip.parent_id)
        if moment is None:
            continue
        src = led.sources.get(moment.parent_id)
        if src is None:
            continue
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' "
                    f"({moment.reason}) hit hard (lift={post.metrics.get('lift_score')}). "
                    f"Find more moments in that vein in this source.")
        req = MomentRequest(source_id=src.id, duration=src.duration or 0.0,
                            transcript=src.transcript or [], signal_peaks=src.signal_peaks or [],
                            guidance=guidance)
        write_request(cfg, kind="moments", key=src.id, payload=req.model_dump())
        led.set_state(src.id, State.moments_requested)
    return led

def retire(led: Ledger, loser_post_ids: list[str]) -> Ledger:
    for pid in loser_post_ids:
        post = led.posts.get(pid)
        if post is not None:
            led.retired.add(post.parent_id)
    return led
```

- [ ] **Step 4: Run — expect pass** (4)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/adjust.py tests/test_adjust.py
git commit -m "feat: adjust — amplify reopens moment search in winner's vein, retire"
```

---

## Task 23: Digest — counts, holds, pending agent steps

**Files:** Create `src/fanops/digest.py`; Test `tests/test_digest.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_digest.py
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Clip, State
from fanops.agentstep import write_request
from fanops.digest import render_digest

def test_counts_and_holds(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x", state=State.transcribed))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c", held=True, held_reason="begging"))
    md = render_digest(led, cfg)
    assert "# FAN OPS Ledger Digest" in md
    assert "Sources" in md and "transcribed" in md
    assert "Brand-risk holds" in md and "begging" in md

def test_lists_pending_agent_steps(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_request(cfg, kind="moments", key="s1", payload={"source_id": "s1"})
    write_request(cfg, kind="captions", key="c1", payload={"clip_id": "c1"})
    md = render_digest(led, cfg)
    assert "Awaiting agent" in md
    assert "moments: s1" in md and "captions: c1" in md
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/digest.py
"""Human-readable digest: unit counts by state, brand-risk holds, and the agent steps
awaiting a response (so a human/cron knows what the pipeline is blocked on)."""
from __future__ import annotations
from collections import Counter
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.agentstep import pending

def _counts(units) -> str:
    c = Counter(u.state.value for u in units)
    return "".join(f"  - {s}: {n}\n" for s, n in sorted(c.items())) or "  (none)\n"

def render_digest(led: Ledger, cfg: Config) -> str:
    out = ["# FAN OPS Ledger Digest\n"]
    out.append(f"\n**Sources** ({len(led.sources)}):\n" + _counts(led.sources.values()))
    out.append(f"\n**Moments** ({len(led.moments)}):\n" + _counts(led.moments.values()))
    out.append(f"\n**Clips** ({len(led.clips)}):\n" + _counts(led.clips.values()))
    out.append(f"\n**Posts** ({len(led.posts)}):\n" + _counts(led.posts.values()))

    holds = [f"- clip `{c.id}` (moment {c.parent_id}): {c.held_reason}"
             for c in led.clips.values() if c.held]
    if holds:
        out.append("\n## Brand-risk holds (need Moh)\n" + "\n".join(holds) + "\n")

    awaiting = ([f"- moments: {k}" for k in pending(cfg, kind="moments")] +
                [f"- captions: {k}" for k in pending(cfg, kind="captions")])
    if awaiting:
        out.append("\n## Awaiting agent (request written, no response yet)\n"
                   + "\n".join(awaiting) + "\n")
    return "".join(out)

def write_digest(led: Ledger, cfg: Config) -> None:
    cfg.digest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.digest_path.write_text(render_digest(led, cfg))
```

- [ ] **Step 4: Run — expect pass** (2)

- [ ] **Step 5: Commit**

```bash
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat: digest — counts, holds, pending agent steps"
```

---

## Task 24: CLI — stage commands + orchestrator that pauses at agent steps

**Files:** Create `src/fanops/cli.py`; Test `tests/test_cli.py`

> The orchestrator runs the deterministic chain as far as it can, then STOPS at each agent
> gate (moments, captions) — writing the request and reporting what's pending. When the
> agent's responses are present, re-running advances past the gate. Tests drive both halves
> by dropping response files (no live agent).

- [ ] **Step 1: Failing test**

```python
# tests/test_cli.py
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentDecision, MomentPick, CaptionSet, CaptionItem, Platform
from fanops.agentstep import response_path
from fanops.cli import advance, main

def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def _ff(mocker):
    def fake(cmd, **kw):
        # whisper writes json; ffmpeg writes a file; signals return via stderr
        joined = " ".join(cmd)
        if cmd[0] == "whisper":
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffmpeg" and "-f" in cmd and "null" in cmd:
            class R: returncode=0; stdout=""
            R.stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                        else "pts_time:16.0 scene_score:0.6")
            return R()
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)

def test_advance_stops_at_moment_gate_then_continues(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    _put(cfg.inbox / "raw.mp4", b"V")
    _ff(mocker)

    # pass 1: ingest->transcribe->signals->REQUEST moments, then stop
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["sources"] == 1
    assert s["awaiting"]["moments"] == 1            # blocked on the moment decision
    assert s["posts"] == 0

    # agent answers the moment request
    src_id = next(iter(Ledger.load(cfg).sources))
    response_path(cfg, "moments", src_id).write_text(MomentDecision(
        source_id=src_id, picks=[MomentPick(start=14.0, end=18.0, reason="punchline",
                                            transcript_excerpt="they slept on me")]
    ).model_dump_json())

    # pass 2: ingest moments -> render clip -> REQUEST captions, then stop
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["moments"] == 1 and s["clips"] == 1
    assert s["awaiting"]["captions"] == 1

    # agent answers captions for both surfaces
    led = Ledger.load(cfg)
    clip_id = next(iter(led.clips))
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="@a/tiktok", caption="wait for it.")]).model_dump_json())

    # pass 3: ingest captions -> crosspost (IG+TikTok) -> publish (dryrun)
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["posts"] == 2 and s["published"] == 2
    # two surfaces, distinct captions, staggered
    led = Ledger.load(cfg)
    caps = {p.caption for p in led.posts.values()}
    assert caps == {"no warning. just impact.", "wait for it."}
    assert len(list(cfg.scheduled.glob("*.json"))) == 2

def test_main_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

```python
# src/fanops/cli.py
"""CLI + orchestrator. advance() runs the deterministic chain as far as it can and PAUSES
at each agent gate (moments, captions): it writes the request and returns an 'awaiting'
count. Re-run after the agent drops responses to pass the gate. No agent logic lives here —
only the file handshake."""
from __future__ import annotations
import argparse, sys
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import State, Fmt, Platform
from fanops.accounts import Accounts
from fanops.ingest import ingest_drops
from fanops.transcribe import transcribe_source
from fanops.signals import detect_signals
from fanops.moments import request_moments, ingest_moments
from fanops.clip import render_moment
from fanops.caption import request_captions
from fanops.caption import ingest_captions
from fanops.crosspost import crosspost_clips
from fanops.post.run import publish_due
from fanops.digest import write_digest
from fanops.agentstep import pending

def _probe_duration(path: str) -> float:
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except Exception: return 0.0

def advance(cfg: Config, *, base_time: str) -> dict:
    led = Ledger.load(cfg)
    accts = Accounts.load(cfg)

    # 1) ingest
    led = ingest_drops(led, cfg)

    # 2) transcribe + 3) signals + 4) request moments — for each source not yet decided
    for s in list(led.sources.values()):
        if s.state is State.catalogued:
            if s.duration is None:
                s.duration = _probe_duration(s.source_path)
            led = transcribe_source(led, cfg, s.id)
        if led.sources[s.id].state is State.transcribed:
            led = detect_signals(led, cfg, s.id)
        if led.sources[s.id].state is State.signalled:
            led = request_moments(led, cfg, s.id)

    # 5) ingest moment decisions (if the agent answered) -> render clips -> request captions
    for s in list(led.sources.values()):
        if s.state is State.moments_requested:
            led = ingest_moments(led, cfg, s.id)
    surfaces = accts.surfaces()
    for m in list(led.moments.values()):
        if m.state is State.moments_decided:
            led, clip = render_moment(led, cfg, m.id, aspect=Fmt.r9x16)
            led = request_captions(led, cfg, clip.id,
                                   [(s.account, s.platform) for s in surfaces])

    # 6) ingest captions (if answered) -> crosspost -> publish
    for c in list(led.clips.values()):
        if c.state is State.captions_requested:
            led = ingest_captions(led, cfg, c.id)
    led = crosspost_clips(led, cfg, accts, base_time=base_time)
    led = publish_due(led, cfg)

    led.save()
    write_digest(led, cfg)
    return {
        "sources": len(led.sources),
        "moments": len(led.moments),
        "clips": len(led.clips),
        "posts": len(led.posts),
        "published": len(led.posts_in_state(State.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "awaiting": {"moments": len(pending(cfg, kind="moments")),
                     "captions": len(pending(cfg, kind="captions"))},
    }

def cmd_status(cfg: Config) -> int:
    led = Ledger.load(cfg)
    print(f"sources={len(led.sources)} moments={len(led.moments)} clips={len(led.clips)} "
          f"posts={len(led.posts)} backend={cfg.poster_backend} "
          f"awaiting_moments={len(pending(cfg, kind='moments'))} "
          f"awaiting_captions={len(pending(cfg, kind='captions'))}")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fanops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("ingest")
    sub.add_parser("digest")
    p_adv = sub.add_parser("advance")
    p_adv.add_argument("--base-time", default="2026-06-02T18:00:00Z")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cfg = Config()
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "ingest":
        led = ingest_drops(Ledger.load(cfg), cfg); led.save(); write_digest(led, cfg)
        print(f"ingested -> {len(led.sources)} sources"); return 0
    if args.cmd == "digest":
        write_digest(Ledger.load(cfg), cfg); print(f"wrote {cfg.digest_path}"); return 0
    if args.cmd == "advance":
        print(advance(cfg, base_time=args.base_time)); return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run — expect pass** (2)

Run: `./.venv/bin/pytest tests/test_cli.py -v`

- [ ] **Step 5: Run the FULL suite**

Run: `./.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat: CLI + orchestrator pausing at agent gates (moments, captions)"
```

---

## Task 25: Fan-ops context file + RUNTIME doc

**Files:** Create `MohFlow-FanOps/00_control/context.md`, `MohFlow-FanOps/00_control/RUNTIME.md`

> No tests (prose); verify by checklist in Step 3. `context.md` is the fan-op adaptation of
> the social skills' context file — it encodes bravado + opsec ("never sound coordinated")
> so caption/moment guidance serves THIS mission.

- [ ] **Step 1: Write `context.md`**

Write `MohFlow-FanOps/00_control/context.md` covering:
- **Who:** independent fan/enthusiast accounts amplifying Moh Flow (a bilingual EN/AR rapper). NOT the main account; NEVER state or imply central coordination.
- **Voice:** bravado through-line — unapologetic, authority, scrappy-OK, never embarrassing. Per-account persona varies (see `accounts.json` `persona`) so two accounts never read as one operation.
- **Moment guidance (drives `request_moments`):** prize the bar/punchline, the line before the beat drop, a quotable EN or AR phrase, a hard visual cut. Return as many moments as are genuinely strong — no quota. Each must have a real reason.
- **Caption guidance (drives `request_captions`):** IG — hook in first 125 chars, save/share CTA ("save this", "send this to…"), 3–10 hashtags. TikTok — first line extends the on-screen hook, conversational/low-polish, 3–5 hashtags. **Different wording per surface** (opsec + platform fit). Bravado, no begging, no "official/label" framing, no "link in bio."
- **Opsec:** staggered times (built in), one persona per account, subtle non-synchronized @mohflow tag (a minority of posts), real content only.

- [ ] **Step 2: Write `RUNTIME.md`**

Write `MohFlow-FanOps/00_control/RUNTIME.md` covering: the daily loop (`fanops advance` → answer any pending moment/caption requests in `04_agent_io/requests/` → `fanops advance` again → repeat); the three human-only steps (create accounts, connect Blotato, sign-off); how the agent answers a moment request (read transcript+signals+guidance, write `<kind>__<key>.response.json`); the no-gate rule + the single brand-risk hold; signal weighting (saves/shares/retention > likes); pointers (ledger, digest, accounts, context).

- [ ] **Step 3: Verify by checklist**

```bash
test -f MohFlow-FanOps/00_control/context.md && test -f MohFlow-FanOps/00_control/RUNTIME.md && echo OK
grep -qi "never sound coordinated\|non-synchronized\|opsec" MohFlow-FanOps/00_control/context.md && echo "opsec present"
grep -qi "125\|save this\|hashtag" MohFlow-FanOps/00_control/context.md && echo "caption rules present"
```
Expected: `OK`, `opsec present`, `caption rules present`.

- [ ] **Step 4: Commit**

```bash
git add MohFlow-FanOps/00_control/context.md MohFlow-FanOps/00_control/RUNTIME.md
git commit -m "docs: fan-op context (bravado+opsec) + runtime operating doc"
```

---

## Task 26: End-to-end real dry-run on a generated sample + README

**Files:** Create `README.md`

- [ ] **Step 1: Generate a real sample + activate one account**

```bash
ffmpeg -y -f lavfi -i testsrc=duration=12:size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=440:duration=12 -c:v libx264 -c:a aac -shortest \
  MohFlow-FanOps/01_inbox/sample_source.mp4
```
Edit `MohFlow-FanOps/00_control/accounts.json`: set `@TBD-1` `status` to `active`, handle to `@mohflow.edits`.

- [ ] **Step 2: Pass 1 — ingest/transcribe/signals/request moments**

```bash
FANOPS_POSTER=dryrun ./.venv/bin/fanops advance --base-time 2026-06-02T18:00:00Z
```
Expected: prints a summary with `awaiting.moments == 1`. A `moments__<src>.request.json` exists in `MohFlow-FanOps/04_agent_io/requests/` containing the transcript (may be empty for the synthetic tone — fine) and signal peaks.

> NOTE: the synthetic `testsrc`/`sine` clip has no speech; Whisper yields an empty transcript and the agent decides moments from signals/duration alone. With a real Moh Flow video the transcript is populated. This step proves the *plumbing*, not the creativity.

- [ ] **Step 3: Answer the moment request (acting as the agent), then Pass 2**

Read the request file, then write the response. Example (adjust the `src` id + timings to the real file):
```bash
REQ=$(ls MohFlow-FanOps/04_agent_io/requests/moments__*.request.json | head -1)
SRC=$(basename "$REQ" | sed 's/^moments__//; s/.request.json$//')
cat > "MohFlow-FanOps/04_agent_io/requests/moments__${SRC}.response.json" <<JSON
{"source_id": "${SRC}", "picks": [
  {"start": 0.0, "end": 7.0, "reason": "opening hit", "transcript_excerpt": ""}
]}
JSON
FANOPS_POSTER=dryrun ./.venv/bin/fanops advance --base-time 2026-06-02T18:00:00Z
```
Expected: `clips == 1`, `awaiting.captions == 1`; a real clip mp4 in `03_clips/`; a `captions__<clip>.request.json` listing the surface(s).

- [ ] **Step 4: Answer captions, then Pass 3**

```bash
CREQ=$(ls MohFlow-FanOps/04_agent_io/requests/captions__*.request.json | head -1)
CLIP=$(basename "$CREQ" | sed 's/^captions__//; s/.request.json$//')
cat > "MohFlow-FanOps/04_agent_io/requests/captions__${CLIP}.response.json" <<JSON
{"items": [
  {"surface": "@mohflow.edits/instagram", "caption": "no warning. just impact.", "hashtags": ["#mohflow"]},
  {"surface": "@mohflow.edits/tiktok", "caption": "wait for it.", "hashtags": ["#mohflow"]}
]}
JSON
FANOPS_POSTER=dryrun ./.venv/bin/fanops advance --base-time 2026-06-02T18:00:00Z
```
Expected: `posts == 2`, `published == 2` (IG + TikTok, the cross-post matrix).

- [ ] **Step 5: Verify the artifacts**

```bash
echo "=== clip is real, vertical, valid ==="; ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height -of csv=p=0 "$(ls MohFlow-FanOps/03_clips/*.mp4 | head -1)"
echo "=== 2 scheduled payloads (the cross-post) ==="; ls MohFlow-FanOps/05_scheduled/*.json | wc -l
echo "=== distinct captions across surfaces ==="; for j in MohFlow-FanOps/05_scheduled/*.json; do jq -r '.post.content.text' "$j"; done | sort -u | wc -l
echo "=== one payload ==="; cat "$(ls MohFlow-FanOps/05_scheduled/*.json | head -1)"
```
Expected: width=1080 height=1920; payload count `2`; distinct-caption count `2`; each payload has matching `platform`/`targetType`, non-empty `mediaUrls` (file://), root-level `scheduledTime`, and the two `scheduledTime`s differ (staggered).

- [ ] **Step 6: Write README + final full suite**

Write `README.md` covering: what it is (intelligent clip + cross-post engine), the unit chain, the agent-gate model (code pauses at moment/caption decisions; the agent answers via files in `04_agent_io/`), install (venv + whisper), the dry-run walkthrough above, the three human-only steps (create accounts → connect Blotato → set `FANOPS_POSTER`), and the opsec/PII guardrails (content bank git-ignored).

```bash
./.venv/bin/pytest -q
git add README.md MohFlow-FanOps/00_control/accounts.json
git commit -m "test: end-to-end real dry-run (cross-post matrix) + README"
```

---

## Self-Review

**Spec coverage (the user's directives):**
- **No tiers / no N-per-tier** → no `Tier`/`variant_budget` anywhere; moment count is the agent's pick (Task 11). ✓
- **No lanes** → `accounts.py` is a flat active list; `surfaces()` is the matrix; routing is "every active surface," not lane-match (Tasks 13, 16). ✓
- **Clipping framework (the core)** → Tasks 8 (transcript), 9 (signals), 10 (agent contract), 11 (agent decides moments WITH reasons). The decision is a recorded `Moment.reason` — real and inspectable, not hand-fed. ✓
- **Cross-posting first-class** → Task 16 `crosspost_clips` fans a clip out to every (account, platform), each with its own time + caption; CLI Task 24 proves IG+TikTok = 2 posts from 1 clip. ✓
- **Nothing is a stub** → every creative step is a real agent handshake (`agentstep`), not a placeholder; brand-risk is real anti-pattern logic; amplify re-opens a real moment search. The only "fed-in" content is the agent's response file, which is the intended seam. ✓
- **Kept the genuinely-real parts** → ledger, Blotato poster (dry-run/REST/MCP + media + target fields), subtle tagging, PII exclusion, opsec staggering (Tasks 6, 7, 15, 17–20). ✓

**Placeholder scan:** every code step shows complete code; no "implement later." `@TBD-*` handles + `context.md`/`RUNTIME.md` prose bodies are intentional, authored in their tasks. ✓

**Type consistency:** `Moment` requires `reason` (models Task 4) and `moments.ingest_moments` always sets it (Task 11). `Clip.meta_captions` added in Task 14 Step 3a, consumed by `crosspost` Task 16 and asserted in both. `Platform` enum used consistently across models/accounts/crosspost/payload/posters. `advance()` (Task 24) calls match every primary function's signature: `transcribe_source(led,cfg,id)`, `detect_signals(led,cfg,id)`, `request_moments/ingest_moments(led,cfg,id)`, `render_moment(led,cfg,id,aspect=)`, `request_captions(led,cfg,clip_id,surfaces)`, `ingest_captions(led,cfg,clip_id)`, `crosspost_clips(led,cfg,accounts,base_time=)`, `publish_due(led,cfg)`. Ledger gained `tag_log`+`retired` in `__init__`/`load`/`save` (Task 6). Post `status` is a plain str ("queued/submitted/published/failed") consistently across poster + run + track. ✓

**Honest limitations (flagged, not hidden):**
- The synthetic `testsrc` sample has no speech, so Task 26's transcript is empty — it proves plumbing, not moment *quality*. Real moment-decisioning needs a real Moh Flow video (and the agent actually reasoning over the transcript).
- The agent steps assume *something* answers the request files (a human in-session, a cron Claude, or an LLM wired to read `04_agent_io/requests/`). Wiring a specific autonomous answerer (e.g. an LLM API loop) is deliberately out of scope — the file contract is the seam that makes it pluggable and testable.
- `video_analysis`/`virality_predictor` (paid escalation) are referenced in `context.md` as optional but not wired into `advance()` — they're an opt-in upgrade for visually-driven clips, not part of the free default path.
