# Content Discovery + Folder-Review Intake — Design Spec

**Date:** 2026-06-04 · **Status:** design approved (operator), ready for implementation plan

## Problem (operator's brief, verbatim intent)

> "Find content on my machine that fits the bill at the least cost possible, for me — in the easiest way possible — to choose yes/no what I want to put into the pipeline. Whatever I do want then goes to clipping and all that."

Today FanOps only ingests what's already dropped in `01_inbox/` (or a URL pull). There is **no discovery step** (scan my disk for candidates) and **no human yes/no intake gate** — content goes straight into the pipeline. The operator wants a cheap, Finder-native review: the system surfaces candidate media with light metadata + a thumbnail; the operator approves by **moving files into an `approved/` subfolder**; only approved content enters the pipeline.

## Approved design decisions (operator, 2026-06-04)

1. **Discovery = scan a source folder for media + CHEAP metadata.** Filesystem walk (reuse `ingest.scan_local`'s logic) + the existing `is_excluded()` PII/legal name filter. For each candidate, compute **cheap** metadata only: duration + dimensions (ffprobe, already wrapped as `probe_dimensions`), file size, mtime — and a **single thumbnail frame** (one `ffmpeg -frames:v 1`). NO transcription, NO LLM, NO signal detection at this stage (that's the expensive pipeline work, deferred until AFTER the operator chooses). "Least cost" = only a filesystem walk + one ffprobe + one frame-grab per candidate.
2. **Choose = a folder you review in Finder; approve by MOVING into `approved/`.** No app, no CLI prompt, no JSON editing. The system writes candidates into a **review folder**; the operator drags keepers into an `approved/` subfolder; everything else is left/ignored. This is the "easiest way possible" — just files + Finder.
3. **Route = sweep `approved/` into the pipeline.** A command (and/or the start of `run`) copies the approved originals into `01_inbox/` so the existing pipeline (transcribe → moments → clip → caption → hook → variant → schedule) picks them up. Rejected candidates never enter the pipeline → no wasted clipping/claude cost.

## Architecture

A new stage **before** ingest, in its own module `discover.py`, surfaced by two new CLI verbs. It does NOT touch the existing pipeline — it just decides *what reaches `01_inbox/`*.

**Folders (under `MohFlow-FanOps/`, beside the existing stage dirs):**
- `00_review/` — the review folder the operator browses. The scan writes one **review entry per candidate** here: a small sidecar (the thumbnail `.jpg` + a `.txt`/`.md` line of metadata), named to sort sensibly. The ORIGINAL files are NOT copied here (cheap — we only write thumbnails + a manifest); the manifest maps each review entry back to its absolute source path.
- `00_review/approved/` — the operator MOVES (or copies) the thumbnail/entry of anything they want into here. (Design choice locked in the plan: approve by moving the *thumbnail/entry*, since originals aren't copied to review — the sweep reads the manifest to resolve the original path. Alternative considered: copy originals into review so the operator moves the actual file — rejected as not "least cost" for large video banks.)

**Flow:**
```
fanops discover <source-folder>   # scan + cheap metadata + thumbnails -> 00_review/ (+ a manifest.json)
   -> operator browses 00_review/ in Finder, drags keeper thumbnails into 00_review/approved/
fanops intake                     # read 00_review/approved/ + manifest -> copy the approved ORIGINALS into 01_inbox/
   -> existing pipeline (advance/run) clips + captions + schedules them
```

**Manifest (`00_review/manifest.json`):** maps a review entry id (the thumbnail basename) → {source_path, bytes, duration, width, height, mtime}. `discover` writes it; `intake` reads it to resolve which originals the operator approved (by which entries landed in `approved/`). Re-running `discover` reconciles (skips already-reviewed/already-ingested by content; see dedup below).

## Units / interfaces (what changes)

- **`src/fanops/discover.py`** (new):
  - `scan_candidates(roots: list[Path]) -> list[Path]` — thin wrapper over the existing `scan_local` logic (media ext + `is_excluded`). Reuse, don't duplicate.
  - `candidate_meta(path) -> dict` — `{bytes, mtime, width, height, duration}` via `os.stat` + `ingest.probe_dimensions` (cheap; ffprobe only). Fail-soft: if ffprobe can't read it, still list it with bytes/mtime and `duration=None` (never drop a candidate just because probe failed).
  - `make_thumbnail(path, out_jpg) -> bool` — one `ffmpeg -ss <~10% in> -frames:v 1 -vf scale=320:-1 <out>.jpg`. Fail-open: returns False + no thumbnail if ffmpeg fails (still list the candidate from metadata). Images: copy/downscale directly.
  - `discover(cfg, roots) -> dict` — orchestrates: scan → for each candidate write thumbnail + manifest entry into `cfg.review` (a new `Config.review` path = `00_review`); skip candidates whose content (sha256) is already a Source in the ledger OR already in a prior manifest (no churn on re-scan). Returns a summary {found, new, skipped}.
  - `intake(cfg) -> dict` — read `cfg.review / "approved"` for approved entries, resolve each to its source_path via the manifest, copy the ORIGINAL into `cfg.inbox` (so the existing `ingest_drops` catalogues it on the next advance), and record which were intaken (clear/aside the approved entry so a re-run doesn't double-intake). Returns {approved, intaken, missing}.
- **`src/fanops/config.py`** — add `Config.review` = `self.base / "00_review"` (mirrors the `_STAGE` dirs).
- **`src/fanops/cli.py`** — two verbs: `fanops discover <folder>` (the scan) and `fanops intake` (sweep approved → inbox). Both wrapped like the other commands (no ledger lock needed for `discover` since it doesn't touch the ledger except a read for dedup; `intake` only copies files into inbox, the ledger ingest happens later in `advance`). Unknown/empty folder → clean message, exit 2 (consistent with the other verbs).

## Testing strategy

- `scan_candidates`: finds media, excludes PII-named + non-media; covered by reusing `scan_local`'s contract.
- `candidate_meta`: returns bytes/mtime always; duration/dims from a mocked `probe_dimensions`; ffprobe-fail → still returns an entry with `duration=None` (fail-soft).
- `make_thumbnail`: builds the right ffmpeg cmd (mock subprocess), writes the jpg; ffmpeg-absent/fail → returns False, no raise (fail-open).
- `discover`: writes a thumbnail + manifest entry per candidate into `00_review/`; a candidate whose sha256 is already a ledger Source is SKIPPED (dedup); re-running `discover` doesn't duplicate entries. Summary counts correct.
- `intake`: an approved entry → its ORIGINAL copied into `01_inbox/`; a non-approved candidate is NOT copied; an approved entry whose source_path no longer exists → reported `missing`, not a crash; re-running `intake` doesn't double-copy.
- End-to-end (integration, real ffmpeg): point `discover` at a temp folder with 2 real videos + 1 PII-named + 1 non-media → review folder gets 2 thumbnails + a manifest; simulate the operator (move 1 entry to `approved/`) → `intake` → exactly that 1 original lands in `01_inbox/` → `advance` (dryrun) clips it. Visual: the thumbnails are real viewable jpgs.
- Backward-compat: the existing pipeline is untouched; `discover`/`intake` are additive verbs; default behavior (drop straight into `01_inbox/`) still works.

## Out of scope (v1)

- AI "fits-the-bill" scoring/ranking of candidates (operator chose cheap metadata, not LLM scoring). Deferred — could rank later.
- A GUI/web review surface (operator chose Finder folders).
- Auto-discovery of *where* content is (operator points `discover` at a folder; no crawling the whole machine).
- Editing/trimming at review time (the pipeline's moment-picker still chooses the slice post-intake).

## Risks / guardrails

- **Least-cost guarantee:** `discover` must do NO transcription/LLM/signal work — only stat + one ffprobe + one frame per candidate. If that creeps, it violates the brief. (Pipeline cost only happens AFTER intake, on approved items only.)
- **Dedup / no churn:** re-running `discover` must skip content already in the ledger (by sha256) and already-manifested, so the review folder doesn't fill with repeats.
- **Fail-open / fail-soft:** a candidate must never be silently dropped because ffprobe/ffmpeg choked — list it from stat alone (thumbnail optional). PII filter (`is_excluded`) is the ONLY intentional exclusion.
- **No accidental double-intake:** `intake` must be idempotent — an already-intaken approved entry is not re-copied (track by content/manifest).
- **Originals are the source of truth:** review writes only thumbnails + a manifest (cheap); `intake` copies the real original from its `source_path` into `01_inbox/`. If the operator moved/deleted the original between discover and intake → reported missing, not a crash.
