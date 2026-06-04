# Content Discovery + Folder-Review Intake — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator scan a folder of their content for candidates (cheap: ffprobe metadata + one thumbnail, NO transcription/LLM), review them in Finder via a `00_review/` folder, approve by moving entries into `00_review/approved/`, then `fanops intake` sweeps the approved originals into `01_inbox/` for the existing pipeline — so only approved content is ever clipped (zero wasted cost on rejects).

**Architecture:** A new pre-ingest stage in `src/fanops/discover.py` + two CLI verbs (`discover`, `intake`). It does NOT modify the existing pipeline — it only decides what reaches `01_inbox/`. `discover` writes a thumbnail + a `manifest.json` entry per candidate into `00_review/` (originals NOT copied — least cost); the operator moves keeper thumbnails into `00_review/approved/`; `intake` reads the manifest, resolves approved entries to their original `source_path`, and copies those originals into `01_inbox/`.

**Tech Stack:** Python 3.12, ffmpeg/ffprobe (cheap metadata + thumbnail only — reuse `ingest.probe_dimensions`/`sha256_of`), pydantic-free plain dicts for the manifest (json), pytest + pytest-mock, ruff.

**Spec:** `docs/superpowers/specs/2026-06-04-content-discovery-review-design.md`

**Baseline:** `main` @ latest (`b5d5ae5` or later), suite **351 passed, 1 skipped**, ruff green. Work in a fresh worktree off `main` with its own python3.12 venv (`pip install -e ".[dev]"`). Run every test with `source .venv/bin/activate && python -m pytest ...`.

**Key reused primitives (verified present):** `ingest.scan_local(roots)->list[str]` (media-ext + `is_excluded` filter), `ingest.MEDIA_EXT`, `ingest.is_excluded(name)`, `ingest.probe_dimensions(path)->(w,h,dur)`, `ingest.sha256_of(path)->str`, `ledger.already_seen(*, sha256=)->bool`. Config `_STAGE` dirs are set in `Config.__init__`.

---

### Task 1: `Config.review` path (the `00_review/` folder)

**Files:**
- Modify: `src/fanops/config.py` (the `_STAGE` dict, ~line 14)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — ADD
def test_config_has_review_dir(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert cfg.review == cfg.base / "00_review"        # the discovery review folder
    # approved subfolder convention (used by intake) is review/approved
    assert (cfg.review / "approved").name == "approved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -k review -v`
Expected: FAIL — `Config` has no `review` attribute.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/config.py — add "review" to the _STAGE dict so it's set in the __init__ loop:
_STAGE = {
    "control": "00_control", "review": "00_review", "inbox": "01_inbox", "sources": "02_sources",
    "clips": "03_clips", "agent_io": "04_agent_io", "scheduled": "05_scheduled",
    "published": "06_published", "reports": "07_reports",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -k review -v && python -m pytest -q`
Expected: PASS; full suite still 351+1 (additive).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (discover 1): Config.review = 00_review/ (the folder-review staging dir)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `discover.py` — candidate scan + cheap metadata + thumbnail (pure helpers)

**Files:**
- Create: `src/fanops/discover.py`
- Test: `tests/test_discover.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discover.py — CREATE
import json
from pathlib import Path
import fanops.discover as discover

def _put(p, b=b"V"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_candidate_meta_uses_cheap_probe_only(tmp_path, mocker):
    f = tmp_path / "a.mp4"; _put(f, b"VIDEO")
    mocker.patch("fanops.discover.probe_dimensions", return_value=(1080, 1920, 12.5))
    m = discover.candidate_meta(f)
    assert m["bytes"] == 5 and m["width"] == 1080 and m["height"] == 1920 and m["duration"] == 12.5
    assert "mtime" in m

def test_candidate_meta_fail_soft_when_probe_fails(tmp_path, mocker):
    # ffprobe choking must NOT drop the candidate — list it with duration/dims None-ish.
    f = tmp_path / "a.mp4"; _put(f)
    mocker.patch("fanops.discover.probe_dimensions", side_effect=Exception("ffprobe boom"))
    m = discover.candidate_meta(f)
    assert m["bytes"] > 0 and m["duration"] is None and m["width"] is None

def test_make_thumbnail_builds_ffmpeg_cmd(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src)
    out = tmp_path / "a.jpg"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd; Path(cmd[-1]).write_bytes(b"JPG")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.discover.subprocess.run", side_effect=fake_run)
    ok = discover.make_thumbnail(src, out)
    assert ok is True and out.exists()
    assert captured["cmd"][0] == "ffmpeg" and "-frames:v" in captured["cmd"] and captured["cmd"][-1] == str(out)

def test_make_thumbnail_fail_open_when_ffmpeg_fails(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src); out = tmp_path / "a.jpg"
    def boom(cmd, **kw): raise FileNotFoundError(2, "no ffmpeg", "ffmpeg")
    mocker.patch("fanops.discover.subprocess.run", side_effect=boom)
    assert discover.make_thumbnail(src, out) is False     # fail-open: no raise, no thumbnail
    assert not out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -v`
Expected: FAIL — `fanops.discover` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/discover.py — CREATE
"""Pre-ingest content discovery + folder-review intake. CHEAP by design: a filesystem scan +
ONE ffprobe + ONE thumbnail frame per candidate — NO transcription, NO LLM, NO signal detection
(that expensive pipeline work happens only AFTER the operator approves, on approved items). The
operator reviews 00_review/ in Finder and moves keepers into 00_review/approved/; `intake` then
copies the approved originals into 01_inbox/ for the existing pipeline. Rejects never enter the
pipeline (no wasted clip/claude cost)."""
from __future__ import annotations
import json, os, shutil, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import scan_local, probe_dimensions, sha256_of

def candidate_meta(path: Path) -> dict:
    """Cheap metadata for one candidate: bytes + mtime always (from os.stat); width/height/duration
    via ffprobe (fail-soft — if ffprobe chokes, return them None so the candidate is still listed)."""
    st = os.stat(path)
    w = h = dur = None
    try:
        pw, ph, pdur = probe_dimensions(path)
        w, h, dur = (pw or None), (ph or None), (pdur or None)
    except Exception:
        pass                                   # fail-soft: list it anyway, dims/duration unknown
    return {"bytes": st.st_size, "mtime": st.st_mtime, "width": w, "height": h, "duration": dur}

def make_thumbnail(path: Path, out_jpg: Path, *, at_seconds: float = 1.0) -> bool:
    """One cheap thumbnail frame (320px wide). Fail-open: returns False (no raise, no file) if
    ffmpeg is absent or errors — the candidate is still listed from metadata, just without a thumb."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", str(at_seconds), "-i", str(path),
           "-frames:v", "1", "-vf", "scale=320:-1", str(out_jpg)]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return False
    if r.returncode != 0 or not out_jpg.exists():
        # a 1s seek can overshoot a <1s clip; one retry at t=0 before giving up
        cmd0 = ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1", "-vf", "scale=320:-1", str(out_jpg)]
        try:
            r0 = subprocess.run(cmd0, check=False, capture_output=True, text=True)
        except (FileNotFoundError, OSError):
            return False
        return r0.returncode == 0 and out_jpg.exists()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -v && python -m pytest -q`
Expected: PASS (the 4 helper tests; `discover`/`intake` come next). Full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/discover.py tests/test_discover.py
git commit -m "feat (discover 2): discover.candidate_meta + make_thumbnail (cheap: ffprobe + 1 frame, fail-soft/open)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `discover()` orchestrator — scan → thumbnail + manifest into `00_review/`, dedup

**Files:**
- Modify: `src/fanops/discover.py`
- Test: `tests/test_discover.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discover.py — ADD
def test_discover_writes_thumbnails_and_manifest(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    _put(src_dir / "good1.mp4"); _put(src_dir / "good2.mp4")
    _put(src_dir / "passport scan.jpg")          # PII-named -> excluded by is_excluded
    _put(src_dir / "notes.txt")                  # non-media -> ignored
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(1080, 1920, 8.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"JPG") or True))
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 2 and summary["new"] == 2          # only the 2 media, PII excluded
    manifest = json.loads((cfg.review / "manifest.json").read_text())
    assert len(manifest) == 2
    # each entry resolves back to a real source_path + carries cheap meta
    paths = {e["source_path"] for e in manifest.values()}
    assert paths == {str(src_dir / "good1.mp4"), str(src_dir / "good2.mp4")}
    assert all("bytes" in e and "duration" in e for e in manifest.values())
    assert len(list(cfg.review.glob("*.jpg"))) == 2               # a thumbnail per candidate

def test_discover_dedupes_already_seen_content(tmp_path, mocker):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "dup.mp4"; _put(f, b"SAME")
    cfg = Config(root=tmp_path)
    # pre-seed the ledger with a Source of this exact content sha
    from fanops.ingest import sha256_of
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s_dup", source_path="/x.mp4", state=SourceState.catalogued, sha256=sha256_of(f)))
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", return_value=True)
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 1 and summary["new"] == 0 and summary["skipped"] == 1   # already in ledger
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -k discover_writes -v`
Expected: FAIL — `discover.discover` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/discover.py — ADD
def _entry_id(path: Path) -> str:
    # stable, filesystem-safe review-entry id: short content hash keeps re-scans idempotent
    return sha256_of(path)[:16]

def discover(cfg: Config, roots: list[Path]) -> dict:
    """Scan roots for media candidates, write a thumbnail + manifest entry per NEW candidate into
    cfg.review. Skips content whose sha256 is already a ledger Source (no churn on re-scan) and
    entries already in the manifest. Returns {found, new, skipped}. CHEAP: stat + 1 ffprobe + 1
    thumbnail per candidate — no transcription/LLM."""
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    mpath = cfg.review / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}
    found = new = skipped = 0
    for s in scan_local(roots):                  # media-ext + is_excluded already applied
        p = Path(s); found += 1
        digest = sha256_of(p)
        eid = digest[:16]
        if led.already_seen(sha256=digest) or eid in manifest:
            skipped += 1
            continue
        meta = candidate_meta(p)
        thumb = cfg.review / f"{eid}.jpg"
        make_thumbnail(p, thumb)                 # fail-open: entry still listed if no thumb
        manifest[eid] = {"source_path": str(p), "sha256": digest, **meta}
        new += 1
    mpath.write_text(json.dumps(manifest, indent=2))
    return {"found": found, "new": new, "skipped": skipped}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -v && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/discover.py tests/test_discover.py
git commit -m "feat (discover 3): discover() — scan -> thumbnails + manifest into 00_review/, dedup vs ledger + manifest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `intake()` — sweep `00_review/approved/` → copy approved originals into `01_inbox/`

**Files:**
- Modify: `src/fanops/discover.py`
- Test: `tests/test_discover.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discover.py — ADD
def test_intake_copies_only_approved_originals_to_inbox(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    keep = src_dir / "keep.mp4"; _put(keep, b"KEEP")
    drop = src_dir / "drop.mp4"; _put(drop, b"DROP")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"J") or True))
    discover.discover(cfg, [src_dir])
    # operator approves ONLY keep.mp4 by MOVING its thumbnail entry into approved/
    from fanops.ingest import sha256_of
    keep_eid = sha256_of(keep)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{keep_eid}.jpg").rename(cfg.review / "approved" / f"{keep_eid}.jpg")
    summary = discover.intake(cfg)
    assert summary["intaken"] == 1
    inbox_files = {p.name for p in cfg.inbox.glob("*") if p.is_file()}
    assert "keep.mp4" in inbox_files and "drop.mp4" not in inbox_files   # only the approved original

def test_intake_is_idempotent_and_reports_missing(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "x.mp4"; _put(f, b"X")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"J") or True))
    discover.discover(cfg, [src_dir])
    from fanops.ingest import sha256_of
    eid = sha256_of(f)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    assert discover.intake(cfg)["intaken"] == 1
    # second run: nothing new to intake (idempotent — not re-copied)
    assert discover.intake(cfg)["intaken"] == 0
    # a missing original is reported, not a crash
    f.unlink()
    # re-approve a fresh entry pointing at the now-missing file
    discover.discover(cfg, [src_dir])   # f is gone so nothing new; simulate a stale approved entry:
    (cfg.review / "approved" / "deadbeefdeadbeef.jpg").write_bytes(b"J")
    out = discover.intake(cfg)
    assert out["missing"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -k intake -v`
Expected: FAIL — `discover.intake` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/discover.py — ADD
def intake(cfg: Config) -> dict:
    """Sweep cfg.review/approved/ : for each approved entry (a thumbnail moved there by the
    operator), resolve its original via the manifest and COPY that original into cfg.inbox so the
    existing pipeline catalogues it on the next advance. Idempotent (an entry already intaken is
    recorded in review/intaken.json and skipped). A manifest-less or vanished original is reported
    `missing`, never a crash. Returns {approved, intaken, missing}."""
    approved_dir = cfg.review / "approved"
    if not approved_dir.exists():
        return {"approved": 0, "intaken": 0, "missing": 0}
    mpath = cfg.review / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}
    donep = cfg.review / "intaken.json"
    done = set(json.loads(donep.read_text())) if donep.exists() else set()
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    approved = intaken = missing = 0
    for entry in sorted(approved_dir.glob("*.jpg")):
        eid = entry.stem
        approved += 1
        if eid in done:
            continue                              # idempotent: already intaken
        info = manifest.get(eid)
        src = Path(info["source_path"]) if info else None
        if src is None or not src.exists():
            missing += 1
            continue                              # stale/unknown entry — report, don't crash
        dest = cfg.inbox / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        done.add(eid); intaken += 1
    donep.write_text(json.dumps(sorted(done), indent=2))
    return {"approved": approved, "intaken": intaken, "missing": missing}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_discover.py -v && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/discover.py tests/test_discover.py
git commit -m "feat (discover 4): intake() — sweep approved/ -> copy approved originals into 01_inbox/ (idempotent, missing-safe)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: CLI verbs `fanops discover <folder>` and `fanops intake`

**Files:**
- Modify: `src/fanops/cli.py` (subparser block + `_dispatch`)
- Test: `tests/test_cli.py` (append)

**Context the implementer must read first:** `cli.py` `main()` builds subparsers (~L114-122 area), and `_dispatch(cfg, args)` is the if-chain (~L184+). `discover` takes a positional `folder`; `intake` takes no args. Both print a summary + return 0; an empty/nonexistent folder for `discover` → stderr message + return 2 (consistent with the other verbs).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — ADD
def test_discover_and_intake_via_cli(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    bank = tmp_path / "bank"; bank.mkdir()
    (bank / "v.mp4").write_bytes(b"VID")
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"J") or True))
    from fanops.cli import main
    assert main(["discover", str(bank)]) == 0
    cfg = Config(root=tmp_path)
    assert (cfg.review / "manifest.json").exists()
    # approve the one entry, then intake
    from fanops.ingest import sha256_of
    eid = sha256_of(bank / "v.mp4")[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    assert main(["intake"]) == 0
    assert (cfg.inbox / "v.mp4").exists()

def test_discover_unknown_folder_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    assert main(["discover", str(tmp_path / "nope")]) == 2
    assert "no such" in capsys.readouterr().err.lower() and "Traceback" not in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_cli.py -k "discover or intake" -v`
Expected: FAIL — no `discover`/`intake` subcommands.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/cli.py — in main(), add to the subparser block:
    p_disc = sub.add_parser("discover"); p_disc.add_argument("folder")
    sub.add_parser("intake")

# src/fanops/cli.py — add to _dispatch (with the other `if args.cmd ==` branches):
    if args.cmd == "discover":
        from pathlib import Path as _P
        from fanops.discover import discover as _discover
        root = _P(args.folder)
        if not root.exists() or not root.is_dir():
            print(f"no such folder: {args.folder}", file=sys.stderr); return 2
        s = _discover(cfg, [root])
        print(f"discovered {s['found']} candidate(s): {s['new']} new in 00_review/, {s['skipped']} already seen. "
              f"Review them in Finder, move keepers into 00_review/approved/, then `fanops intake`.")
        return 0
    if args.cmd == "intake":
        from fanops.discover import intake as _intake
        s = _intake(cfg)
        print(f"intake: {s['intaken']} approved original(s) copied into 01_inbox/ "
              f"({s['approved']} approved, {s['missing']} missing). Run `fanops advance`/`run` to pipeline them.")
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_cli.py -k "discover or intake" -v && python -m pytest -q`
Expected: PASS; full suite green. Confirm `--help` lists `discover` + `intake`: `python -m fanops.cli --help` (from a scratch dir).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/cli.py tests/test_cli.py
git commit -m "feat (discover 5): fanops discover <folder> + fanops intake CLI verbs (unknown folder -> exit 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Real end-to-end integration + docs

**Files:**
- Create: `tests/integration/test_discover_real.py` (marked `integration`, like the others)
- Modify: `MohFlow-FanOps/00_control/RUNTIME.md` (document the discover→review→intake flow + the 2 verbs), `README.md`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_discover_real.py — CREATE
import json, shutil, subprocess
from pathlib import Path
import pytest
import fanops.discover as discover
from fanops.config import Config

@pytest.mark.integration
def test_discover_real_thumbnails_then_intake_to_inbox(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe required for the real discovery render")
    bank = tmp_path / "bank"; bank.mkdir()
    for name, color in [("keep.mp4", "navy"), ("skip.mp4", "darkgreen")]:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s=720x1280:d=3", str(bank / name), "-y"], check=True)
    (bank / "tax return.mp4").write_bytes(b"not real but PII-named")   # excluded by name
    cfg = Config(root=tmp_path)
    summary = discover.discover(cfg, [bank])
    assert summary["new"] == 2                                # PII-named excluded
    thumbs = list(cfg.review.glob("*.jpg"))
    assert len(thumbs) == 2 and all(t.stat().st_size > 0 for t in thumbs)   # REAL viewable jpgs
    # operator approves keep.mp4
    from fanops.ingest import sha256_of
    keep_eid = sha256_of(bank / "keep.mp4")[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{keep_eid}.jpg").rename(cfg.review / "approved" / f"{keep_eid}.jpg")
    assert discover.intake(cfg)["intaken"] == 1
    assert (cfg.inbox / "keep.mp4").exists() and not (cfg.inbox / "skip.mp4").exists()
```

- [ ] **Step 2: Run test to verify it fails (or skips without ffmpeg)**

Run: `source .venv/bin/activate && python -m pytest tests/integration/test_discover_real.py -v`
Expected: with ffmpeg present (this host has ffmpeg-full) it RUNS and PASSES once Tasks 2-4 are in; on a host without ffmpeg it SKIPS cleanly.

- [ ] **Step 3: Implementation = docs (the discover/intake code already exists)**

```text
# MohFlow-FanOps/00_control/RUNTIME.md — ADD a "Content discovery + review intake" section near the
# top of the operating loop, documenting:
#   1. `fanops discover <folder>` — scans a folder for media (PII-named excluded), writes a thumbnail
#      + cheap metadata per candidate into 00_review/ (NO transcription/LLM — least cost).
#   2. You browse 00_review/ in Finder and MOVE the keeper thumbnails into 00_review/approved/.
#   3. `fanops intake` — copies the approved originals into 01_inbox/; rejects never enter the
#      pipeline. Then `fanops advance`/`run` clips + captions + schedules them.
#   Note: discover dedups against the ledger (re-scanning won't repeat already-ingested content).
# README.md — add `discover`/`intake` to the command list with the one-line review-folder flow.
```

- [ ] **Step 4: Run the full suite + ruff + the real discovery test**

Run: `source .venv/bin/activate && python -m pytest -q && ruff check src/ && python -m pytest tests/integration/test_discover_real.py -v`
Expected: full suite green (351 + the new discover tests); ruff clean; the integration test PASSES (real thumbnails written, approved-only intake).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_discover_real.py MohFlow-FanOps/00_control/RUNTIME.md README.md
git commit -m "feat (discover 6): real discovery->review->intake integration test + docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** ✅ scan a folder for media (Task 2/3, reuses `scan_local`) · cheap metadata only — ffprobe + 1 thumbnail, no transcription/LLM (Task 2, enforced by the "cheap" tests) · review folder + approve-by-moving-into-`approved/` (Task 3 writes `00_review/`, Task 4 sweeps `approved/`) · approved-only into `01_inbox/` → existing pipeline (Task 4 + 5) · rejects never pipelined (intake copies only approved) · dedup vs ledger (Task 3) · fail-soft/open (Task 2 tests) · idempotent + missing-safe intake (Task 4 tests) · CLI verbs + exit-2 (Task 5) · real-render proof (Task 6).

**Placeholder scan:** every code step has complete code. The only prose-only step is Task 6 Step 3 (docs), which lists the exact sections/verbs to document.

**Type consistency:** `Config.review` (Task 1) ← used by `discover`/`intake` (Tasks 3/4) ← CLI (Task 5). `candidate_meta`→dict, `make_thumbnail`→bool, `discover`→{found,new,skipped}, `intake`→{approved,intaken,missing} — consistent across tasks + tests. Manifest entry shape `{source_path, sha256, bytes, mtime, width, height, duration}` written in Task 3, read in Task 4. Entry id = `sha256[:16]` consistently (Task 3 `_entry_id`/inline, Task 4 lookup, tests).

**Cost guardrail honored:** `discover` does stat + `probe_dimensions` (ffprobe) + `make_thumbnail` (1 ffmpeg frame) per candidate — NO `transcribe`/`detect_signals`/`claude` anywhere in the discovery path. The expensive pipeline runs only after `intake`, on approved items.

**Out-of-scope confirmed:** no AI scoring, no GUI, no whole-machine crawl, no trim-at-review — all deferred per the spec.
