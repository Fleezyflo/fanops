# FanOps Lifecycle — Full-Picture Audit

> **Superseded for casting/routing and live fan-out arithmetic** by [fresh-ingestion-trace.md](fresh-ingestion-trace.md)
> (verified 2026-07-08 @ `0bf6ab0`, post-P11 single-owner model). This document remains a dated 2026-06-27 maximum-depth
> audit snapshot — do not treat its casting-stage claims, `account_selection_admits` findings, or `moment_casting`
> observability gaps as live (all removed in P11/MOL-152).

> Maximum-depth read-only audit of the entire production pipeline (ingest → transcribe → asset-prep → moments → hooks → ~~casting~~ → render/caption → structural-hooks → culmination + the cross-cutting spine).
> Method: 9 parallel per-cluster deep-reads → adversarial verify on every finding → cross-stage completeness critic → synthesis. Generated 2026-06-27.

**Soundness verdict: `sound-with-caveats`**  ·  9 clusters · 33 confirmed findings (30 per-cluster + 3 cross-stage, each adversarially verified)

## Executive verdict

> **Historical (pre-P11).** The casting→crosspost seam findings below describe the removed LLM casting stage
> (`account_selection_admits`, durable `AccountSelection`, `moment_casting` gate). Current routing is
> pick-stamped `Moment.affinities` + `affinity_admits` — see [fresh-ingestion-trace.md](fresh-ingestion-trace.md) §3.

The FanOps clip+cross-post lifecycle is fundamentally sound from ingest to culmination — the load-bearing invariants (content-addressing, one-writer flock transaction, no-auto-publish, no-double-post, casting-no-leak, learning-validation-as-correctness-gate, byte-identical OFF paths) all hold, and the engineering discipline is high: bounded subprocesses kept out of the ledger lock, typed fail-open/fail-closed boundaries with rationale, and per-unit quarantine so one bad source/moment/clip/post can never wedge a pass. **(Pre-P11 audit)** The single real structural weakness identified at audit time was the casting→crosspost seam: a persona-LESS but ACTIVE account is silently dropped from the casting brief (casting.py:108-109) and then DENIED on every moment of any cast source (casting.py:215-219) — **this path no longer exists**; `casting.py` is now the 22-line `affinity_admits` predicate only. Two MEDIUM observability gaps also described below are similarly historical (`moment_casting` gate removed). Everything else is LOW-severity doc drift, provenance smell, or concurrency-flag-gated (default-OFF) local correctness erosion that fails open to a valid centered crop. No CRITICAL findings, no data-loss path in the default configuration, no publish-safety hole.

## Top risks (ranked)

> **All items below are pre-P11 audit findings (2026-06-27).** Items 1–3 reference removed casting machinery.
> Item 4 (`hooks_by_persona`) is also removed. See fresh-ingestion-trace for the live model.

1. HIGH *(removed P11)* — Persona-less active account silently posts NOTHING for a cast source (xc-1/c5-f1, casting.py:108-109 + 215-219): the casting brief drops an account with no voice+levers, then account_selection_admits DENIES it on every moment because the source is 'cast', and the degraded_reason channel (casting.py:163) never fires on a subset-drop. This is the one structural correctness leak in the lifecycle and it produces zero output for a legitimate account with no operator trace. Fix at the root: either include every active account in the casting brief (even with an empty directive) so the LLM can place it, or treat a persona-less account as fan-to-all at the crosspost gate rather than DENY, and emit a degraded_reason on any subset-drop.
2. MEDIUM (compounds the HIGH) — A captioned clip is flipped to `queued` with zero posts and zero crosspost-stage breadcrumb when selection denies all surfaces on an UNBATCHED source (c8-f2, crosspost.py:271-275): the only zero-result log is gated on `if tgt:`, and `_seed_clips` never re-picks a queued clip, so the content drop is permanent and silent at the mint. Add a no-post-born breadcrumb at the unconditional set_clip_state(queued).
3. MEDIUM — A stuck `moment_casting` gate is invisible at every operator surface (xc-3/x-f2, cli.py:35 + 64-66): convergence correctly covers it but `_gates_blocked_note`, the run.log breadcrumb, and `fanops status` all omit it, so a wedged casting gate exits 0 with no signal — a silent stall in exactly the surface built to make stalls loud. Add `moment_casting` to the blocked-note tuple, the gates_blocked log event, and `cmd_status`.
4. MEDIUM — A stripped per-account hook vanishes end-to-end with no breadcrumb and no Review-restore (c4-f3, moments.py:277-279): unlike the shared hook's `hook_removed` preservation, an authored-then-killed per-account hook is unobservable (the scoreboard reads only m.hook). Mirror the shared hook's strip-and-preserve + a log line.
5. LOW-but-latent — `FANOPS_CONCURRENT_SOURCES` (default OFF) + shared `framing/tmp` keyframe filenames keyed only on (rounded-start, index) collide across sources (c2-f1/c0-f2-R+C, keyframes.py:27): a parallel worker can read or unlink another's frames, silently degrading to a centered crop. Key the tmp path on source_id+window before this flag is ever defaulted on.

---

## FanOps Lifecycle Dossier — Ingest → Culmination, Full Picture

This is the end-to-end narrated map of one `advance()` pass plus the human-gated publish/learn tail, stage by stage, with the seven dimensions (contract · briefing · process · results · resilience · invariants) woven in and `file:line` anchors throughout. The pipeline is a content-addressed, one-writer, agent-gated state machine: raw bytes become a `Source`, a `Source` becomes `Moment`s (picked → hooked → cast), a `Moment` becomes per-aspect `Clip`s, a `Clip` becomes per-surface `Post`s born `awaiting_approval`, and only an operator promotes a post to publish. Slow work (whisper, demucs, ffmpeg, MoviePy, the `claude -p` calls) runs OUTSIDE the ledger flock; the single in-lock transaction is the sole writer.

---

### Stage 0a — Intake (the fan-in)
**Contract.** Raw bytes arrive via five channels and converge on the filesystem staging area: Studio upload streams `FileStorage[]` into `cfg.inbox` (`actions_run.py:74-121`); a drop folder; a `yt-dlp` URL pull (`ingest.py:138-163`); a third-party peer-dir (`actions_run.py:145-151`); and `discover(roots)` → operator-approves → `intake()` copies approved originals in (`discover.py:80-137`). Output is validated media in `cfg.inbox`/`cfg.thirdparty_inbox` plus review manifests. **No ledger entity yet** — pure filesystem staging. No agent gate here.

**Process & resilience.** `save_uploads` layers a raw-name traversal triad + `secure_filename` + inbox-bound resolve + atomic `.uploadpart`→`os.replace` (`actions_run.py:93-106`), and never 500s — every fallible step yields a skip reason, all-rejected ⇒ `ok=False`. `download_url` runs a bounded (600s) subprocess with NO lock held; `yt-dlp` absent ⇒ typed `ToolchainMissingError`, nonzero rc ⇒ `DownloadError` with stderr tail (`ingest.py:153-163`). `discover`/`intake` are idempotent (sha256 dedup against ledger + `intaken.json` set) with atomic writes.

**Invariants upheld.** Traversal-safety (path-bound resolve), atomic write-once for staged files (`.uploadpart` is never in `MEDIA_EXT` so a leaked temp is never catalogued), third-party assets land in a PEER dir so a native ingest can't mislabel them, symlink escape blocked before any copy.

---

### Stage 0b — Catalogue to Source (`_catalogue_file` / `ingest_drops`)
**Contract.** Staged files → one `Source` per NEW sha256 (`ingest.py:116-119`) + a content-addressed copy at `cfg.sources/{sid}{ext}`. Transition: file-on-disk → `Source(state=catalogued)`. Runs INSIDE `advance()`'s first short transaction (`pipeline.py:434-435`) and the CLI ingest/pull transactions. No agent gate — deterministic.

**Process.** `ingest_drops` walks `sorted(inbox.rglob('*'))`, skips symlinks/non-files/`.gitkeep`/non-`MEDIA_EXT`, PII names, and audio-only files lacking a video stream. `_catalogue_file`: streaming `sha256_of` → `already_seen` dedup → `make_id('src',digest)` content-addressed id → `shutil.copy2` → `probe_dimensions` via bounded ffprobe → `led.add_source` (setdefault, write-once). Deliberately NO original filename (PII).

**Resilience — the dual ffprobe model is exactly right.** ffprobe ABSENT raises `ToolchainMissingError` that aborts the whole ingest CLEANLY (the transaction saves only on clean exit, so no partial catalogue; CLI exit 2, Studio catches a clean `ActionResult`). A per-file ffprobe HANG returns an empty rc=124 result so the one bad file is SKIPPED this pass and retried next — never wedging the pass (`ingest.py:41-61`).

**Invariants upheld.** Content-addressing (id + dedup on sha256); `origin_kind` and `batch_id` WRITE-ONCE with visible conflict logging (`ingest.py:106-109`); one-writer (runs inside `Ledger.transaction`).
*Finding c0-f1 (LOW): a URL pull stamps `source_origin='url'` on EVERY new source the inbox rglob catalogues, including drop-folder files placed before the pull — but `source_origin` has zero behavioral readers, so this is provenance drift only.*

---

### Stage 1 — Transcribe (`transcribe_source` + vocals + `_fwrun`)
**Contract.** `Source.source_path` of a catalogued source → `04_agent_io/transcripts/<stem>.json` (whisper-shaped) + `Source.transcript`/`language`/`meta.transcribed=True`. Transition: `catalogued → transcribed` or `→ error` (retriable). Runs lock-free in `_prewarm` AND re-runs in-lock, adopting the warm cache (`pipeline.py:184-186`). Whisper is a subprocess, not an agent gate — the agent gates begin downstream at moments.

**Process — lock-discipline is the headline.** Idempotent early-return if `meta.transcribed`. The cache-adopt path: if `<stem>.json` exists and parses, adopt it and SKIP the multi-minute subprocess + demucs (`transcribe.py:128-138`) — this is what keeps whisper OUT of the flock. Vocal isolation (`isolate_vocals` shells demucs, bounded 1800s, FAIL-OPEN to raw audio at every step including a cross-device move failure, `vocals.py:47-59`, `transcribe.py:145-155`). Engine: faster-whisper via `python -m fanops._fwrun` at a duration-aware model (`asr_model_for`; short→large-v3, long→medium) when `[asr]` present, else the legacy `whisper` CLI (turbo).

**Resilience.** Every failure mode → `Source.state=error` + typed `error_reason` with `transcribed` left UNSET so a recovered source re-runs (absent/hung/no-JSON/malformed-JSON, `transcribe.py:166-195`). A corrupt cache is NEVER adopted — it falls through to a real run that overwrites. Per-source quarantine (`pipeline.py:190-191`).

**Invariants upheld.** Deterministic stem-keyed content-addressed cache; the "ran-no-speech ([], transcribed=True)" vs "not run (None)" distinction preserved; the `_fwrun` runner fails LOUD (nonzero exit) so a source parks retriable rather than landing a fake-empty transcript.
*Finding c0-f2 (LOW): the duration-aware ASR upgrade applies only to the faster-whisper branch (`transcribe.py:160-163`); on a host without the `[asr]` extra the legacy CLI path is duration-BLIND (always turbo). Fail-open, but the documented short-source accuracy upgrade silently doesn't happen on the fallback engine.*
*Finding c0-f4 (LOW): `discover()`/`intake()` review-folder JSON writes are atomic but unsynchronized across concurrent runs — a lost-update window, theoretical under the single-operator usage model.*

---

### Stage 2a — ffmpeg signals sidecar (`detect_signals`)
**Contract.** A `transcribed` Source → `src.signal_peaks` (`{t,kind,score[,energy]}`) + `src.duration`, state `→ signalled`, plus an on-disk versioned sidecar `cfg.agent_io/signals/<source_id>.json` (`signals.py:94-138`). Consumed by `moment_pick_prompt` and `_peak_in_window` window-scoping. Deterministic, no agent.

**Process & resilience.** Two ffmpeg passes (silencedetect + scdet) parsed by regex, then an optional energy pass. The out-of-lock prewarm writes the sidecar; the in-lock commit adopts it on a `_SIDECAR_V` match and SKIPS both passes. The two REQUIRED passes FAIL-CLOSED into the per-source quarantine (`ToolchainMissingError` at `:90-92`, 600s timeout at `:78`); the optional energy pass FAILS SOFT (`windows=[]`, peaks unchanged, `:119-124`) so it can never quarantine a source. The sidecar's plain `write_text` is safe because the reader tolerates a torn file (`JSONDecodeError → recompute`, `:110-111`).

**Invariants upheld.** Content-addressing (sidecar keyed by source_id, `_SIDECAR_V` invalidates a shape change); one-writer (mutation only via the in-lock commit; prewarm sidecar is a pure artifact); never-wedge per-unit quarantine.

---

### Stage 2b — keyframe stills, the vision payload (`frames.py` / `keyframes.py`)
**Contract.** `source_path` + a `[start,end]` window → up to N jpeg paths on disk returned as `list[str]`. These ARE the briefing assets for the moments + moment_hooks gates: `_source_frames` = 6 whole-source stills for the PICK pass (`moments.py:34-43`), `_window_frames` = 3 stills over the FITTED picked window for the HOOK author (`moments.py:45-60`). Paths are written into `request.json` under `frames` and attached as images by the responder (`responder.py:47`, `llm.py:135-145`).

**Process & resilience.** One bounded ffmpeg per frame (30s). Files persist under `keyframes/<src.id>` (NOT unlinked) so the path list in `request.json` still resolves when the responder runs later. FAIL-OPEN: no source / zero duration → `[]` → text-only gate; `_window_frames` logs `hook_window_frames_empty` so a degraded text-only hook is visible. The responder re-asks once if the model never opened the frames, then proceeds text-grounded with a warning.

**Invariants upheld.** Fail-open vision (a vision gate degrades to text, never crashes); the no-misleading-frames rule — `_window_frames` REFUSES to substitute whole-source frames (`moments.py:50-52`) because the hook prompt asserts the stills ARE this clip's window.

---

### Stage 2c — smart-framing focus sidecar (`framing.subject_focus`)
**Contract.** `cfg, src, [start,end]` → `(fx,fy)` normalized subject centroid in `[0,1]` or `None`, cached per-window in `cfg.agent_io/framing/<source_id>.json` (`framing.py:63-103`). Consumed by `clip.reframe_filter` to slide the 9:16 crop onto the subject. OpenCV face detection, no agent.

**Process & resilience.** Samples 5 frames, detects the largest face per frame (Haar cascade), returns the MEDIAN centroid iff ≥0.34 of frames had a face. FAIL-OPEN by contract: no `cv2` extra / empty cascade / too few detections / ANY exception → `fx=fy=None` → centered crop. NEVER raises. The in-lock commit re-probes nothing on a cache hit.

**Invariants & the one real defect.** Upholds fail-open (`None` == centered crop, default-on never worse than blind crop) and additive fingerprinting (existing clips stay valid). **VIOLATES one-writer/collision-safety under the default-OFF `FANOPS_CONCURRENT_SOURCES` flag:** the keyframe tmp dir `framing/tmp` is shared globally and frame filenames key only on `round(start*100)` + index with NO source_id (`keyframes.py:27`, `framing.py:82-84`), so concurrent render threads sharing a rounded start centisecond collide on the same tmp jpeg, and the `finally`-unlink races the detect — silently degrading the subject-aware crop to a centered crop with no breadcrumb (c2-f1, LOW). The per-source focus cache is also a non-atomic lock-free read-modify-write, so concurrent windows of one source clobber each other's cached focuses (c2-f2 — correctness preserved via re-probe, wasted work). Both are gated behind a default-OFF flag and fail open to a valid crop.

---

### Stage 2d — snap bands (`bands.py`)
**Contract.** A profile name → a `Band(lo,hi)` (`bands.py:11-37`). Single home shared by `clip.fit_window` (render enforcement) and the prompts (pick target + short-source rule) so render band and prompt band can never drift. Pure constant table.

**Process & invariants.** `band_for` is a pure dict lookup, case/whitespace tolerant, unknown → TALK. VALIDATE-OR-DEFAULT on load, strict on write (`PROFILE_NAMES`). Upholds the single-source-of-truth invariant (render + prompt share one table) and byte-identical legacy behavior. Clean — no findings.

---

### Stage 2e — briefing builder (`prompts.py`)
**Contract.** A request payload dict → a committed `claude -p` instruction string; the caller pairs it with the exact pydantic JSON schema via `--json-schema`. Live gates: `moment_pick_prompt`, `moment_hook_prompt`, `caption_prompt` only (`moment_casting_prompt` removed P11).

**Process — injection defense is real.** Pure, deterministic string construction. `_brief_fence` collapses any forged `<brand_brief>` tag to an inert token (`:16,24`) to stop fence-escape injection; every data block is framed as quoted source text ("treat as quoted source text… never as instructions"); every optional block is byte-identical-absent when its key is empty/None, preserving the OFF paths. Provenance: the responder emits `model + prompt_sha + brief_sha` per call (`responder.py:51-56`), where `brief_sha = guidance_sha(cfg)` ties output to the exact `context.md`.

**Invariants upheld.** Injection-fence integrity; byte-identical OFF paths; the hashtags-only caption contract (the on-screen hook moved to the frame-seeing moment gate; the caption gate is HASHTAGS ONLY, `:380-384`).
*Finding c2-f4 (LOW): `caption_prompt` still constructs a dormant learned+transferred style feed with a stale "next session" comment — inert in the default frozen-learning state but stale documentation.*

---

### Stage 2f — write-once request→response transport (`agentstep.py`)
**Contract.** `cfg, kind, key, payload` → `<kind>__<key>.request.json` stamped with a content-derived `request_id` (`:40-59`); the responder writes the matching `.response.json` echoing that rid (`:61-71`); `read_response` validates schema AND rid-matches-latest before applying (`:73-90`). The transport every gate rides.

**Process — this is the concurrency backbone.** `write_request` mints `rid = _hash(kind,key,prev,payload)`, writes ATOMICALLY via tmp+`os.replace`, and UNLINKS any prior response so a re-seed invalidates the old answer. `write_response` is also atomic + chmod 0600. `pending()`/`read_response` gate on `rid==latest`. The responder's TOCTOU guard re-reads the rid AFTER the slow model call and DROPS a mid-call re-seed (`responder.py:87-97`). `discard_gate`/`discard_gates_for` clear superseded gates (the trailing-`.` glob anchor prevents `source_1.` matching `source_12.`).

**Resilience & invariants.** FAIL-CLOSED on corruption (corrupt request → latest id None; corrupt response → not-applied) with a logged breadcrumb. Upholds write-once, one-writer, rid-based stale-answer rejection, and atomic swaps.
*Finding c2-f3 (LOW): `os.replace` assumes same-filesystem tmp; correct as written (tmp shares the target dir), only a risk if `cfg.agent_io/requests` ever straddles a mount boundary — no current trigger, and the surrounding quarantine catches the `EXDEV`.*

---

### Stage 3a — request_moments (pick gate WRITE)
**Contract.** `led.sources[source_id]` (transcript, signal_peaks, duration, language) + `load_guidance(cfg)` + `cfg.clip_profile` + `_source_frames` (≤6 whole-source stills) → a write-once-by-state `moments__{source_id}.request.json` (MomentRequest, personas popped — the pick pass is windows-only) + transition `signalled → moments_requested` (`moments.py:107-125`). Gate key = source_id.

**Briefing.** Prompt = `moment_pick_prompt`: editorial-brain framing, SOURCE DURATION, hard per-pick bounds (`0<=start<end<=duration`, finite), band lo-hi target, `_target_pick_count` ceiling, short-source EXACTLY-ONE rule, reason-required, brief fence. Frames attached as images for this VISION gate. Provenance: ONE `llm` log line per call (model + prompt_sha + brief_sha). The model is opus-pinned for this creative gate (`config.py:312-323`). The transport rides the operator's existing `claude -p` subscription (no API key; `--strict-mcp-config` + `--allowedTools ''` keep it a pure generator), grants Read only for the two VISION gates, and has real jittered backoff with a typed `LlmRateLimitError` on sustained 429/503/529 so a usage spike fails LOUDLY (`llm.py:101-120`).

**Process & invariants.** Deterministic + write-once-BY-STATE (no in-function `latest_request_id` guard — relies on the `_stage_source_to_moments` `signalled`-only guard, `pipeline.py:188-189`). The responder OVERRIDES `out['source_id']` from the gate payload, not the model (`responder.py:99-100`, gate-authoritative). Per-source quarantine. Upholds write-once (by-state), one-writer, gate-authoritative source_id, provenance.
*Findings c0-f1/c0-f2/c0-f3 (LOW): an unbounded prompt brief size (full transcript json.dumped, no soft budget after STDIN removed the ARG_MAX ceiling); the write-once asymmetry vs the hook gate (currently safe — nothing returns a source to `signalled` mid-flight, and the TOCTOU guard would drop a stale answer regardless); a stale module-header docstring describing the pre-M1b single-pass behavior.*

---

### Stage 3b — ingest_moments (pick gate READ → mint Moments)
**Contract.** `read_response(...MomentDecision)` + source duration → validated, de-overlapped picks reconciled into content-addressed `Moment` entities (state=`picked`, hook empty) via `led.reconcile_moments` (`moments.py:127-189`). Transitions: `moments_requested → picks_decided` (all-valid) | `→ error` (all-invalid) | `→ moments_empty` (model returned `[]`) | unchanged (pending). Deterministic ingest.

**Process — the critical write-once protection.** Per-pick `validate_pick` (finite, end>start, EOF tolerance, `_MIN_MOMENT_S` floor). `_drop_overlaps` keeps the start-ordered first of any >50%-overlap pair, NEVER empties a valid set. Moment id = `child_id('moment', source_id, '{start:.2f}-{end:.2f}')` — content-addressed, so a same-window re-pick UPSERTS. **Before reconcile it FIRST `discard_gates_for('moment_hooks', f'{source_id}.')` and `discard_gate('moment_casting', source_id)`** (`moments.py:181-186`) so a superseded pick's stale hook/casting answer can never be re-applied — write-once correctness for the downstream gates.

**Resilience & invariants.** The three failure paths are correctly distinguished: all-invalid → source error WITHOUT cascade (prior moments preserved); empty → distinct `moments_empty` + loud log, NO cascade; pending → no-op. `reconcile_moments` never resurrects retired moments and preserves clean_awaiting/live/protected lineages (`ledger.py:445-462`). Moments born `picked` (hookless) and the render loop keys on `decided`, so a picked moment never renders hookless.
*Finding c0-f4 (LOW): `_drop_overlaps` is geometric keep-first with no merge — the dropped pick's `reason` is lost (only a count logged); minor provenance smell since `reason` is display/fallback-corpus only, not a render/selection gate.*

---

### Stage 3c — route_moments (hook-strategy router, observe-only, default-OFF)
**Contract.** `decided` moments + signal_peaks → per-Moment `hook_strategy` annotation (`text` | `clean_final` | `clean_awaiting_strategy:<key>`). Renders/persists nothing else; no lifecycle transition. Runs only when `cfg.hook_router` (default OFF). Deterministic classifier.

**Invariants.** ONE WRITER (`route_moments`) for `hook_strategy`; byte-identical OFF path; FORWARD-ONLY reservation preservation (a `clean_awaiting_strategy` reservation is never demoted). Clean.

---

### Stage 4a — request_moment_hooks (PASS 2 brief)
**Contract.** The source's `picked` moments → one write-once `moment_hooks__{source_id}.{token}` request per picked moment carrying MomentHookRequest (window, frames over the FITTED window, window-scoped signal peaks, personas, optional learned_hooks) (`moments.py:191-228`). Source stays `picks_decided`.

**Briefing — the operator's #1 ask, honestly delivered.** `frames` come from `_window_frames` over `fit_window(m.start,m.end,...,lo=band.lo,hi=band.hi)` — the SAME cut the renderer makes, so the stills match the clip opening. `signal_peaks` filtered to the window via `_peak_in_window` (fail-open per peak). Personas built from `hook_directive(a)` per account; `learned_hooks` attached as an optional KEY only when `proven_hook_styles` is non-empty (so the OFF path is byte-identical). Prompt = `moment_hook_prompt` threading `_hook_decision` (input-dependent mechanism selection reading frames+peaks) and the shared `_hook_spec` craft. Opus-pinned vision call, provenance line emitted. Honestly fail-open to text-only when no source exists (`moments.py:45-60`, breadcrumb).

**Process & invariants.** Deterministic per moment, write-ONCE (`latest_request_id` skip, `moments.py:213-214`) so an in-flight answer is never invalidated. Upholds write-once, frames-asserted-to-BE-this-window, one-writer (in-lock), source state unchanged.
*Finding c4-f1 (LOW): the persona list iterates `accounts.accounts` (ALL accounts incl. suspended) gated on `hook_directive` truthiness, not `.active()` (`moments.py:201-203`) — every other consumer of per-account intent uses `.active()`, so opus authors hooks for handles that never post.*
*Finding c4-f4 (LOW): the pass-2 window-frame ffmpeg extraction runs IN-LOCK with no lock-free prewarm counterpart (`pipeline.py:206-219`) — bounded by `extract_keyframes`' own 30s timeout, so a delay not a wedge, but it reintroduces lock-held subprocess work the rest of the pipeline was refactored to avoid.*

---

### Stage 4b — ingest_moment_hooks (PASS 2 apply)
**Contract.** The source's `picked` moments + landed MomentHookDecisions → each moment updated with `hook`/`hook_removed`/`hooks_by_persona`, state `→ decided`; source `→ moments_decided`. ATOMIC-per-source: returns early (no mutation) until EVERY pick's gate has a valid answer (`moments.py:246-251`). Consumer, not an agent gate.

**Process — the strongest part of the cluster.** ORDER-INDEPENDENT: gathers all decisions first, then authors in stable `(start,end)` order so the cross-clip + opening-template dedup is order-independent (the documented review fix, `moments.py:233-238`). The strip floor is layered: `is_weak_hook` (empty/exact-dup/opening-cluster) OR `narration_signature` (3rd-person recap meter) OR `has_artist_reference` (singular-artist-pronoun/name GATE, no viewer exemption) OR `brand_risk_flag` — a failing SHARED hook is preserved into `hook_removed` for the Studio Review restore flow and the clip still ships clean.

**Resilience & invariants.** A gate validating with `hook=null` decides that pick CLEAN. Any pending pick leaves the source `picks_decided`, VISIBLE in `awaiting.moment_hooks` — never a silent wedge. Upholds atomic-per-source promotion, deterministic dedup, hook_removed preservation (for the shared hook), one-writer, render-keys-on-decided.
*Finding c4-f3 (MEDIUM): a PER-ACCOUNT hook stripped for narration/artist/brand is silently DROPPED from `hooks_by_persona` with NO breadcrumb and NO `hook_removed`-style preservation (`moments.py:277-279`). Unlike the shared hook, an authored-then-killed per-account hook is invisible end-to-end — the `hook_quality` scoreboard measures only `m.hook`, never `hooks_by_persona`.*
*Finding c4-f5 (LOW): an imperative-opener + plural-pronoun artist recap ("watch them turn the room") escapes both deterministic strip predicates — an accepted residual the design explicitly delegates to the prompt.*

---

### Stage 4c — hookscore scoreboard + strip floor (`hookscore.py` / `hookcheck.py`)
**Contract.** `decided` moments → `log_hook_quality` writes ONE digest line `{decided,with_hook,null,viewer_pov_rate}` and returns the dict; `is_weak_hook`/`narration_signature`/`has_artist_reference` return bool reject decisions. NO ledger write, NO state transition — read-only scoreboard. Pure/deterministic, no LLM/network/flock.

**Invariants.** Read-only scoreboard takes no lock; strip floor (clean beats slop — a rejected hook → None → clean clip); determinism (pure regex). High-precision-by-design (accepts misses). `hook_quality` vacuously 1.0 when no hook shipped (no div-by-zero).

---

### Stage 4d — overlay: on-screen hook BURN (`overlay.py` + clip.py wiring)
**Contract.** `m.hook` (owner-moment hook, via `_subtitles_vf` / `burn_hook_only`) → an `.ass` file + the ffmpeg `subtitles=` fragment + the burned mp4 + the `hook_burn_failed` flag on the Clip. (`Post.variant_hook` per-account path removed P9.)

**Process & resilience.** `build_ass` is PURE/deterministic with auto-fit fontsize; `ffmpeg_has_textfilter` probes once (module-cached, never raises); `burn_hook_only` is ATOMIC (temp `.part`→`os.replace`, forced `-f mp4` muxer, `finally`-sweep). FAIL-OPEN by contract: a clip is NEVER blocked on its text — no filter / empty build → render plain + `hook_burn_failed=True`. The render fingerprint folds the `.ass` text + focus so a changed hook re-renders.

**Invariants upheld.** Atomic publish (no partial mp4); fail-open never blocks a clip; `hook_burn_failed` makes the silent text-loss VISIBLE in the run summary (`pipeline.py:411-412`); content-addressing (fingerprint includes the burned `.ass` so the mp4 is proof-of-intended-render).

---

### Stage 5 — Per-account moment casting *(removed P11/MOL-152 — historical audit text)*

> **Not live.** The LLM casting stage, durable `AccountSelection`, and `account_selection_admits` were deleted.
> Current routing: `Moment.affinities` stamped at pick + `casting.affinity_admits` at crosspost/caption scope.

**Contract (historical).** `request_moment_casting` / `ingest_moment_casting` The source's `decided`-or-stranded-`clipped` moments + each active persona-bearing account's `casting_directive` → (a) a write-once `moment_casting__<source_id>.request.json` gate; (b) on ingest, `Moment.affinities`, a durable `AccountSelection` per picked account, a `SelectionFact` per (moment,account) (`casting.py:105-172`). Writes ONLY affinities + side-records — no Source/Moment flip. Sits at `pipeline.py:468`, AFTER moment_hooks, BEFORE render+caption and crosspost.

**Briefing.** Agent gate (`moment_casting`). Payload = MomentCastingRequest carrying real moment `reason`/`hook`/`excerpt`/`signal`/window fenced as DATA + each persona's compiled `casting_directive` (voice + content_focus/energy clauses — the M1 lever-registry projection). Prompt = `moment_casting_prompt` ("for EACH account choose which moments belong on THAT account's feed"). This gate is TEXT-ONLY (not in `_VISION_GATES`) — the selector sees text, not frames. Provenance emitted (model + prompt_sha + brief_sha).

**Process & the RF1 win.** Deterministic, write-once, one gate/source, in-lock (the slow answering is out-of-lock in the responder). The RF1 redesign genuinely closed the historic silent-collapse: `account_selection_admits` (`casting.py:202-222`) DENIES an account with no record on a cast source, holds on a pending gate, and NEVER auto-writes `fan_all_default`; flag-OFF is byte-identical; fail-open routes through the VISIBLE `Source.degraded_reason` channel. Captions are scoped **owner × platform** (P10 / MOL-151) via `pipeline._owner_caption_surfaces` → `affinity_admits` — the SAME gate crosspost enforces — so caption-scope and post-minting can't drift (the old (clip × account) `scoped_caption_surfaces` is deleted).

**Invariants upheld.** Write-once gate; content-addressing (AccountSelection/SelectionFact ids `child_id` of pairs, re-cast OVERWRITES); NO-LEAK at moment granularity; byte-identical OFF; one-writer; no-auto-publish; AccountSelection sum-type enforced on every construction path incl. `model_copy` override.

**THE WEAK SEAM (HIGH — c5-f1/xc-1).** The fan-to-all fallback holds at MOMENT granularity but breaks at ACCOUNT granularity. `casting.py:108-109` builds the brief's personas via `[... for a in accounts.active() if (instr := casting_directive(a))]`. `casting_directive` returns `''` (falsy) when an active account has no voice AND no content_focus/energy levers (`persona_directives.py:62-79` — I confirmed `_join` returns `voice or body`, both empty ⇒ `''`). Such an account is silently absent from the brief, so the LLM never selects for it; ingest writes an `AccountSelection` only for picked accounts. At crosspost, `account_selection_admits` (`casting.py:215-219`) sees `sel is None` AND `selections_of_source(...)` non-empty (others picked) ⇒ returns False ⇒ that account is DENIED on EVERY moment of the source. The degraded channel does NOT fire — it is gated on `not per_account` i.e. NOBODY was picked (`casting.py:163-165`), not a subset-drop. Result: a legitimate active account posts NOTHING for the source with no `degraded_reason` and no breadcrumb naming it. Reachability depends on an active account genuinely having empty persona+levers (the migrate path usually hydrates a voice), so the trigger is a hand-built/legacy/deliberately-empty persona — plausible but not the common path.
*Finding c5-f4 (LOW): `cast_moments` (the heuristic fallback, `casting.py:48-85`) is unwired dead code that writes affinities WITHOUT an AccountSelection; if re-wired it would route through the legacy pre-v9 affinity fallback, a latent inconsistency vs the LLM path the docs imply is equivalent.*

---

### Stage 6a — Render (`render_aspects_for` / `render_moment` / `render_account_cut`)
**Contract.** A `decided` Moment + cfg flags + the target aspect set → one mp4 per distinct aspect at `cfg.clips/{cid}.mp4` (`cid=child_id('clip',moment_id,aspect)`), an optional `.ass` + `.render.json` fingerprint, a `Clip` entity, and vstart/framing sidecars. On success: `set_moment_state(MomentState.clipped)` + Clip born `rendered`; on ffmpeg failure: Clip born `error` and the moment LEFT at `decided` for retry. `render_account_cut` is the per-account variant — mints NO Clip, advances NO moment, writes an arbitrary out_path atomically. Pure ffmpeg, no agent.

**Process — deterministic + idempotent via content-address + fingerprint.** Window math: `fit_window` → `snap_window` (transcript boundaries) → `pick_visual_start` (strongest-frame entry, cached). `focus = framing.subject_focus` when smart_framing. `_render_fingerprint` hashes src/window/aspect/dims/ass-text/top_bias/focus; if dst exists with size>0 AND fp matches, ffmpeg is SKIPPED and the warm mp4 adopted. Phase-D split: the heavy ffmpeg runs OUT of the lock in `_prewarm`; the in-lock commit re-runs and adopts the warm artifact. `_FFMPEG_TIMEOUT=600s` bounds a hang so it can't hold the flock.

**Resilience & invariants.** FAIL-CLOSED per clip into an error Clip (never raises); the moment left `decided` so a re-run retries (transient-glitch safe). `render_account_cut` FAIL-OPEN to `(False,None)` → caller burns the shared clip. Per-moment quarantine. Upholds content-addressing (per-account cut writes a DISTINCT path so it can't collide with the bare clip), the stale-render guard (fingerprint includes hook+window+focus), byte-identical OFF paths (`focus=None`+`top_bias=False` identical to legacy), and one-writer (prewarm workers use a throwaway ledger).
*Finding c0-f2/c2-f1 (MEDIUM under the default-OFF concurrency flag): the shared `framing/tmp` keyframe filenames collide across sources under `FANOPS_CONCURRENT_SOURCES` — a parallel worker can read or unlink another's frames, producing a silently-wrong crop or a silent center-crop fallback. Fail-open hides it; no error surfaces.*

---

### Stage 6b — Caption gate (`request_captions` / `ingest_captions`)
**Contract.** A `rendered` Clip + its Moment (excerpt) + Source (language) + the owner × platform surface list (`pipeline._owner_caption_surfaces` → `affinity_admits`) + accounts (personas, hashtag_corpus) → `request_captions` builds a payload + write_request and sets `captions_requested`; `ingest_captions` reads the response (CaptionSet), vets each item's hashtags, writes `clip.meta_captions[surface]`, sets `captioned` — or `held` on a language/unknown-surface/brand-risk fault (`caption.py:200-322`). Output consumed by crosspost `_mint_surface_post`.

**Briefing.** Payload: `clip_id`, `transcript_excerpt`, `language`, `guidance`, `content_tags` (`content_tag_candidates`), per-surface `{surface, platform, persona(caption_directive), corpus(hashtag_corpus)}`, optional learned hints (gated, default empty). Prompt = `caption_prompt`. Write-once via `request_id`; `read_response` checks the id matches the latest so a stale answer is never applied. NO model/brief fingerprint is stored ON the caption entry (only the request_id on disk).

**Process & invariants.** Deterministic vetting; the LLM call is the only nondeterminism, isolated behind the write-once gate. `vet_hashtags` is pure: keeps model VETTED tags reach-ordered, floats corpus then content ahead of rank, reserves AR + content floor slots, hard-caps at 4. FAIL-CLOSED on language mismatch / unknown surface / brand-risk → `held`, excluded from crosspost. A failed-aspect (error) clip is NEVER captioned (`pipeline.py:260`) so it can't be laundered into a phantom post. Upholds no-phantom-post-laundering, hashtags-only (hook always None), the hard ≤4 cap, every-shipped-tag-evidence-backed, write-once, and casting-no-leak (caption scope is a superset of crosspost survivors; crosspost is the SOLE enforcement gate).
*Finding c0-f1 (LOW): the caption prompt menu is built from `vetted_menu()` with NO store/corpus (`prompts.py:338`) while ingest vets against `load_store(cfg)` where the store REPLACES VETTED (`hashtags.py:141`) — under a live reach store the model is told to pick from a set whose picks vetting then drops, wasting the selection and never surfacing store-only high-reach tags.*
*Finding c0-f3 (LOW): the brand-risk hold keeps only the first offending surface's reason and writes all `meta_captions` optimistically before holding — diagnostic-quality (a later clean re-ingest overwrites), not data-loss.*

---

### Stage 7 — Structural hooks (opt-in, default-OFF; byte-identical when both formats off)
A cleanly-layered, defensively-coded subsystem. **(7a) intro_match** is a fail-open LLM-vision matcher gate: write-once (skip-if-latest-request-exists `intro_match.py:101` to avoid the `write_request` response-invalidation wedge), keyed on an EPHEMERAL `_gate_key(moment, candidate-set, MATCHER_VERSION)` deliberately SEPARATE from the durable `stitch_plan_id`, with a validation-at-boundary filter keeping only pairings naming a real candidate id AND carrying `tease_text`. **(7b) mine_suggestions** is a deterministic, top-N-capped (`MAX_SUGGESTIONS_PER_PASS=5`), content-addressed-dedup producer whose re-route-only-when-ALL-candidates-exist rule prevents a capped-out candidate from silently dropping its moment. **(7c) prewarm_approved_stitches** keeps every heavy render (impact_cut ffmpeg, intro_tease MoviePy) OUT of the flock on a throwaway ledger, and the in-lock commit ADOPTS the warm artifact via a fingerprint both sides compute identically. **(7d) render_approved_stitches** commits in-lock with strategy-agnostic guards (base-missing → error, `base_fingerprint` drift → dismissed) and a FORWARD-ONLY kill-switch (disabled-format approved plans FREEZE, never render/vanish; `approved_disabled_count` logs the frozen count every pass). Stitches are born `stitch_draft` (structurally unpostable) and ADDITIVE-not-supersede per the fan-account-repost-freely rule; bounded intro retries. Findings are minor: c7-f1 (LOW) stale docstring claiming a removed "cannot supersede a live post" guard (contradicted by `_precheck`'s own docstring and the pinned additive tests); c7-f2 (LOW) the producer collapses `intro_matches` to index 0 leaving ranks 2..N dead; c7-f3 (LOW) the intro retry-cap counts a transient prewarm miss as a failed attempt and can prematurely park a renderable plan.

---

### Stage 8a — crosspost_clips (mint, BORN awaiting_approval)
**Contract.** `captioned`, not-held, not-retired clips + `accounts.surfaces()` + base_time → one Post per admitted surface BORN `awaiting_approval`, content-addressed `pid=child_id('post', target_clip.id, surface_key)`; the seed clip flipped `captioned → queued` (`crosspost.py:135-275`). Runs IN-LOCK. No agent gate — it READS the casting gate's answer via `account_selection_admits`/`casting_gate_pending` and the moment-author hooks.

**Process & invariants.** Deterministic + content-addressed. Schedule via `surface_time` seeded by SHA1, monotonic in index (`_JITTER_MAX<_STEP_MIN` proof). Idempotent: `add_post` setdefault first-write-wins; a re-crosspost to an EXISTING awaiting post REWRITES variant intent in place only on a real diff. Per-surface gates in order: batch-target skip → `account_selection_admits` → per-platform duration cap → on-demand aspect render → per-surface caption presence; `casting_gate_pending` defers the WHOLE clip to next pass. Upholds no-auto-publish (born `awaiting_approval`, structurally unpublishable `crosspost.py:225`), content-addressing, no-double-post (setdefault + idempotency token `fanops_<hash(pid)>`), casting-no-leak (`account_selection_admits` is the SOLE gate, shared with caption scoping), OFF byte-identical.

**THE SILENT-DROP SEAM (MEDIUM — c8-f2).** I confirmed at `crosspost.py:268-275`: the per-surface loop runs `_mint_surface_post` for each surface, then UNCONDITIONALLY `set_clip_state(clip.id, ClipState.queued)` at line 275 regardless of how many posts were born. `account_selection_admits` denial returns 0 SILENTLY (`crosspost.py:153-157`, no log). The only zero-result breadcrumb (`batch_target_summary`) fires `if tgt:` (`crosspost.py:271`) — so on an UNBATCHED source where selection denies every surface, no summary fires, the clip flips to `queued`, and `_seed_clips` never re-picks a queued clip. Result: a captioned clip permanently in `queued` with zero posts and zero crosspost-stage log lines. The cast DECISION is logged upstream at the casting stage (diagnosable there), but the mint where the content actually drops out is silent. This is the same root as the HIGH c5-f1 — together they form the casting→crosspost differentiation-leak.

---

### Stage 8b — Human approval gate (`Ledger.approve_post` / `actions_approve`)
**Contract.** Operator-selected `awaiting_approval` post ids + `now_iso` → post promoted `awaiting_approval → queued` with a strictly-future or preserved schedule (`ledger.py:390-406`); in `actions_approve` the per-account variant render is BURNED + adopted (media_urls set) BEFORE promotion (`actions_approve.py:101-108`).

**Process & invariants.** In-lock guard: only `awaiting_approval` promotes (else clean no-op). Stale/missing/unparseable schedule bumped to suggested-or-now; a still-future operator time preserved verbatim. `_adopt_render` runs FIRST (lock-free warm + in-lock adopt) and a render that can't materialize sets `RENDER_PENDING_REASON` + continue (NOT queued). Upholds no-auto-publish (the ONLY `awaiting → queued` promoter), no-empty-media (render adopted before queued), and approval-never-machine-guns-a-backlog (stale → suggested-future, not now).

---

### Stage 8c — publish_due / _publish_one (publish OUT-of-lock)
**Contract.** `queued` posts with `scheduled_time <= now` → published (`06_published/<day>/<id>.json` archive) or `failed`/`needs_reconcile`. State: `queued → submitting (persisted) → published|failed|needs_reconcile`. Runs AFTER the main txn commits (`pipeline.py:474`).

**Process — three-phase claim→network→finalize.** CLAIM (tight txn): publish ONLY if still `queued`, flip `queued → submitting` + persist BEFORE any network (`run.py:131-135`). NETWORK (no lock): ensure media upload + `poster.publish` on a throwaway ledger. FINALIZE (tight txn): merge ONLY `_NET_POST_FIELDS` into a FRESH ledger (the B4 lost-update guard, `run.py:73-75,164-171`). Only `queued` is considered; a stranded `submitting` post is NOT re-driven (reconcile's job).

**Resilience & invariants.** Fail-CLOSED on `AuthError` (halt the queue, H8); a `needs_reconcile` park is NEVER downgraded to `failed` (the double-post guard C1). Upholds crash-safe no-double-post (`submitting` persisted before network; claim re-reads `queued`), one-writer (only net fields merged into a fresh ledger), and dryrun-can-never-be-bypassed-by-a-per-channel-provider (the global `is_live` governs all).

---

### Stage 8d — reconcile_due / reconcile_posts (stranded-post recovery)
**Contract.** Posts in `{submitting, submitted, needs_reconcile}` WITH a submission_id → `published` → `PostState.published` + public_url; `failed` → failed; else left parked. Runs out-of-lock AFTER the main txn, BEFORE publish.

**Process & invariants.** Pre-poll each post's backend status lock-free (per-post backend routing), then apply the CACHED results inside ONE tight txn that re-checks each post's current state. Fail-CLOSED on `AuthError` (halt). A single poll error is CAPTURED per-post and re-raised inside apply so the post is parked (never guessed `failed`). Upholds never-guess-a-post's-fate (a poll error/unknown leaves it parked) and no-double-post (a parked ambiguous post is never auto-requeued). HONEST documented boundary: a `fanops_` idempotency token is not a real `postSubmissionId` so its poll 404s and the post stays parked until a real id overwrites it.

---

### Stage 8e — track.pull_metrics / record_metrics / _auto_validate_metrics_shape
**Contract.** `published`/`analyzed` posts with a submission_id → `post.metrics` (incl. `lift_score`); first poll flips `published → analyzed`; `metrics_series` appends one row per due cadence offset. SIDE EFFECT: `cutover.json metrics_confirmed` auto-stamped on the first real non-degraded analyzed metric (`track.py:163-179`).

**Process & invariants.** `lift_score` WHITELISTS keys against `_W` (unknown ignored, non-numeric dropped — no KeyError). `record_metrics` is wholesale-replace latest-snapshot-wins; series append is idempotent. A non-`(published|analyzed)` post is an absolute no-op. `_auto_validate` is gated on `cfg.is_live` (dryrun never reaches a live metrics row), a DEGRADED row (a primary weighted key absent OR present-but-null, the D1 `isinstance` guard) never stamps, and since every metrics client is a live backend the global gate is airtight. Upholds learning-validation-as-a-CORRECTNESS-gate (proven only by a real, non-degraded, live analyzed metric) and unknown-field safety.

---

### Stage 8f — Learning loop (adjust + variant_amplify + variant_transfer + variant_learning + p4_dim_bias)
**Contract.** ANALYZED posts carrying a real `lift_score` → AMPLIFY re-opens a moment request on the winner's source (capped `MAX_AMPLIFY_PER_SOURCE=3`); RETIRE suppresses a clip (+ its moment if no live sibling). `variant_learning`/`transfer` are PURE read-only scorers biasing the caption REQUEST; `variant_amplify`/`p4_dim_bias` are AMPLIFY-ONLY actuators.

**Process & invariants.** Deterministic (content-addressed secondary sort keys, no random/hash/wall-clock). `classify_outcomes` guards a winner is NEVER also a loser. All actuators default-OFF + VALIDATION-FROZEN (inert until `learning_validated` even with the kill switch on). FAIL-SAFE: any exception logged once, ledger CONTENT byte-identical. Upholds AMPLIFY-ONLY isolation (variant_amplify/p4 import `amplify`, NEVER `retire`/`_delete_moment_cascade` — AST-test-proven), C1 isolation (variant_learning/transfer NEVER imported by track/pipeline/adjust/ledger), the per-source amplify budget shared constant, and no-wrong-signal-deletes-a-LIVE-post.
*Findings c8-f1/c8-f3 (LOW): the actuators tell the operator to run the now-OPTIONAL `fanops cutover metrics` step (a runtime log hint and a module comment) — stale doc drift from the auto-validation de-gating; the real unfreeze path is an automatic live-metric stamp.*

---

### Stage 8g — write_digest heartbeat
**Contract.** A post-publish READ-ONLY ledger reload → `cfg.digest_path` markdown (counts, holds, failures, needs_reconcile, unmeasured, lift-by-variant, amplify streaks, reach-by-dim, pending gates) + the `RunSummary` TypedDict returned by `advance()`. No state transition.

**Process & invariants.** Pure read; reuses the SAME gated scorers as the caption bias so the digest can never disagree with the actuator. FULLY fail-open (each section degrades to a safe default; the final write swallows `OSError`). Written OUTSIDE the lock, after commit — can never roll back a publish. Upholds read-only and one-gate-home.

---

### The Cross-Cutting Spine (X1–X5) — the most carefully-engineered cluster

**X1 — Ledger transaction + flock (`ledger.py`).** The crown jewel. `transaction()` acquires the flock BEFORE load and saves ONCE on clean exit (`ledger.py:292-310`), closing the documented B4 lost-update window. flock over an O_EXCL sentinel is the right call: the kernel self-heals an orphaned lock on process death (`ledger.py:181-184`) so a `-9`'d writer wedges nothing; only genuine contention raises a typed `LockBusyError`. The whole-file atomic rewrite (tmp+`os.replace` at 0600) is a deliberate correctness-over-performance trade, bounded by GC. The schema hop-chain is copy-on-write, never-wipe, with a typed gap-error and a `_NewerSchema` refuse-to-load guard preventing forward-field drop on downgrade. Upholds one-writer, no-wipe, atomic write, content-addressed idempotent adds.

**X2 — State machines + cascade (`models.py` + reconcile).** Post is BORN `awaiting_approval` (`models.py:202-204`, the no-auto-publish spine). `_delete_moment_cascade` preserves-and-retires any live/operator/stitch-protected descendant rather than deleting it (`ledger.py:479-503`); retired moments are never resurrected. AccountSelection's sum-type is enforced on EVERY construction path incl. the `model_copy` override (`models.py:352-368`); the v8→v9 migration cannot forge a violation because it only emits a record for non-empty affinities. Immutable update pattern (`model_copy` + dict reassign). `approve_post` bumps a stale schedule to a strictly-future fallback so approval never machine-guns a backlog.

**X3 — Config flags + model pinning (`config.py`).** Uniformly validate-or-default + fail-open: unknown `FANOPS_POSTER`→dryrun+warn, unknown `FANOPS_LIVE`→not-live, negative clamps on knobs. `is_live_backend` is the single live+creds home that correctly falls through to per-channel readiness (the C1 fix). `llm_model_for` pins opus for creative gates, sonnet for mechanical. Upholds byte-identical OFF paths (every default-ON flag degrades to legacy) and no-false-LIVE-banner.

**X4 — advance() DAG + prewarm + quarantine (`pipeline.py`).** Textbook shape: ingest (short txn) → lock-free prewarm against a throwaway ledger → ONE main txn as the SOLE writer (source→moments→hooks→casting→render/caption→structural→ingest_captions→crosspost) → reconcile (out-of-lock) → publish (out-of-lock) → read-only summary. Per-unit quarantine ensures one bad unit never wedges a pass; `AuthError` is the SOLE deliberate halt. Upholds one-writer (workers pure), AuthError-halts, byte-identical concurrent-OFF path.
*Finding x-f1 (LOW): `_quarantine` mutates unit state IN-PLACE (`pipeline.py:172-174`), bypassing the immutable-setter convention — works only because Source/Moment/Clip are NOT frozen; if any were ever frozen (as AccountSelection is) it would raise inside the error path. x-f5 (LOW): the whole pass commits as one transaction, so a late `AuthError` in crosspost rolls back the pass's cheap state-flips — DELIBERATE and SAFE (artifacts persist; next pass re-flips idempotently), but a real behavior worth documenting.*

**X5 — daemon + autopilot + errors.** Defeats both launchd gotchas (`WorkingDirectory` + baked PATH), shlex-quotes every path, and CONFIRMS stop/status outcomes via `launchctl list` rather than asserting them; `set_env_var` is atomic and newline-injection-safe. The daemon never publishes (dryrun default), never edits the ledger; the `AuthError` type-hierarchy means a new backend halts identically.

**THE OBSERVABILITY GAP (MEDIUM — x-f2/xc-3).** I confirmed the full surface: the `awaiting` dict grew to FOUR gate kinds and `_build_summary` counts all four including `moment_casting` (`pipeline.py:417-420`), and the run-loop convergence test covers it (so the loop won't falsely converge). BUT every DIAGNOSTIC surface omits it: `_gates_blocked_note` computes `open_gates` over only `("moments","moment_hooks","captions")` (`cli.py:35`), so a run stuck SOLELY on `moment_casting` returns None — NO loud stderr alert; the run.log breadcrumb logs only moments/captions; and `cmd_status` prints `awaiting_moments`/`awaiting_moment_hooks`/`awaiting_captions` but NOT `awaiting_moment_casting` (`cli.py:64-66`). A casting gate that converge-fails (repeated ValidationError, rate limit) exhausts the loop and exits 0 with no signal anywhere — a silent stall in exactly the surface built to make stalls loud, on the one gate built to differentiate accounts.
*Findings x-f3/x-f4 (LOW): stale docstrings post-RF1 (casting docstrings still claim `affinities` is the gate when `AccountSelection` is); a comment citing `extra="ignore"` as if configured when it is only the pydantic-v2 default and unpinned — a future `extra="forbid"` hardening would silently break the migration hop-chain.*

---

### Cross-stage seams I traced and found SOUND (dissolved, not findings)
- **decided→clipped→casting race:** the P1 backfill (`pipeline.py:233-242`) + ingest accepting `clipped` moments (`casting.py:137`) genuinely closes it — a late casting answer still applies because `account_selection_admits` reads the AccountSelection (not moment state) and crosspost defers via `casting_gate_pending`. No stranding.
- **Gate convergence:** the run-loop break covers ALL FOUR gate kinds and the responder drains all four from `_SCHEMA`; the loop cannot falsely converge on a half-answered gate (the diagnostic omission is xc-3, an observability gap, not a convergence break).
- **moment_hooks atomic-per-source promotion** means casting always sees a complete moment set.
- **Stale-AccountSelection-after-re-pick:** a re-decision discards the casting GATE and resets affinities, but does NOT drop the durable AccountSelection — MASKED because the re-picked moments revert to `picked` (unrenderable), the re-opened gate makes `casting_gate_pending` True, and crosspost defers until casting overwrites the selection. Worth a characterization test, not a live defect.
- **prewarm vs in-lock render fingerprint:** the per-account Render burns at APPROVAL (lock-free), not in the pipeline prewarm, so there is no per-account stale-burn race in `advance()`.
- **Stitch additivity:** stitches born `stitch_draft` (unpostable) and ADDITIVE, never retiring base posts — no stitch-vs-crosspost retire race (despite the stale c7-f1 docstring).
- **OFF/flag firewalls hold across the seam:** account_casting OFF and creative_variation OFF make crosspost byte-identical fan-to-all.


---

## Confirmed findings register

_Every finding below survived an independent adversarial verifier prompted to refute it._

| id | severity | stage | title | evidence (file:line) | root cause |
|----|----------|-------|-------|----------------------|------------|
| xc-1 / c5-f1 | **HIGH** | Casting (5) → Crosspost (8a) | Persona-less ACTIVE account is dropped at the casting brief but DENIED at crosspost — silent zero-post leak, no fan-to-all rescue, no `degraded_reason` | `casting.py:108-110` (walrus brief filter), `:163-165` (degraded only on `not per_account`), `:215-219` (`sel is None` + selections non-empty → DENY); `persona_directives.py:62-79`; `crosspost.py:153-157,275` | Producer/consumer disagreement on "active": the brief filters by `casting_directive(a)` truthiness ("has a persona") but crosspost scopes by `accounts.active()` and treats a cast source's missing-record as DENY. Fan-to-all fallback holds at MOMENT granularity, breaks at ACCOUNT granularity; no `degraded_reason` on the subset-drop path. |
| c4-f3 | MEDIUM | ingest_moment_hooks (4b) | A per-account hook stripped for narration/artist/brand vanishes with no breadcrumb and no Review-restore preservation | `moments.py:270-282` (shared preserves `hook_removed`; `hooks_by_persona` comprehension drops silently); `models.py:157-162`; `crosspost.py:203-204`; `hookscore.py:72-76` | The per-account filter is a comprehension predicate (keep-if-clean) rather than the shared hook's explicit strip-and-preserve branch; no per-persona `hook_removed` analogue and no breadcrumb. The scoreboard measures only `m.hook`. |
| c8-f2 / xc-2 | MEDIUM | crosspost_clips (8a) | Captioned clip consumed to `queued` with ZERO posts and NO crosspost breadcrumb when selection denies all surfaces on an unbatched source | `crosspost.py:271-275` (unconditional `set_clip_state(queued)`; `batch_target_summary` gated on `if tgt:`), `:153-157` (silent False denial); `casting.py:163-165`; `crosspost.py:137` (seed re-picks only captioned) | `set_clip_state(queued)` is unconditional regardless of posts born, and the only zero-result breadcrumb is gated on a non-empty batch target. Selection-only denial on an unbatched source has no record at the crosspost stage (logged upstream at casting, silent at the mint). Same root as xc-1. |
| x-f2 / xc-3 | MEDIUM | advance() run loop / CLI (X4) | A stuck `moment_casting` gate is invisible at every operator surface — loud blocked-note, run.log breadcrumb, and `fanops status` all omit it | `cli.py:35` (`open_gates` tuple omits moment_casting), `:64-66` (`cmd_status` omits it), `:716,721-724` (convergence covers it but note+log omit it); `pipeline.py:417-420` (summary counts it) | The awaiting dict grew to four gate kinds but the human-facing observability (loud note, log event, status) was only partially updated past the original moments/captions pair. Convergence is correct; only the diagnostics undercount. |
| c2-f1 / c0-f2(R+C) | MEDIUM* | smart-framing (2c) / Render (6a) | Shared `framing/tmp` keyframe filenames collide across concurrent sources/windows — a parallel worker can read or unlink another's frames | `keyframes.py:27` (filename keyed only on `round(start*100)`,i); `framing.py:82-84,93-95`; `clip.py:356,474`; `pipeline.py:119-123`; `config.py:739` (concurrent_sources default OFF) | The keyframe temp path is keyed only on (rounded start, index) and shares one global dir, so it is not unique per (source, window). *MEDIUM only under the default-OFF `FANOPS_CONCURRENT_SOURCES` flag; fails open to a centered crop with no breadcrumb. |
| x-f1 | LOW | advance() DAG (X4) | `_quarantine` mutates unit state in-place, bypassing the immutable-setter convention | `pipeline.py:167-175`; `ledger.py:380-387` (immutable setters, fix #10); `models.py:116,143,178` (plain BaseModel), `:343` (AccountSelection sole frozen) | Two divergent state-mutation idioms; the in-place quarantine works only because Source/Moment/Clip are not frozen. If ever frozen, it raises inside the error path. |
| x-f3 | LOW | state machines / DAG (X2/X4) | Stale docstrings describe casting as writing ONLY `Moment.affinities` and the gate honoring affinities, after RF1 moved the gate to `AccountSelection` | `pipeline.py:222-229`; `casting.py:1-9`; `casting.py:144,159-162,193-214`; `crosspost.py:21,153,157` | RF1 introduced AccountSelection as the durable gate input and demoted affinities to a legacy fallback, but the casting-stage and module docstrings were not updated. Code is correct; prose lies about which artifact the gate reads. |
| x-f4 | LOW | load + models (X1/X2) | Comment cites pydantic `extra="ignore"` but no model sets it (relies on the v2 default) | `models.py:220,343`; `ledger.py:23,268` | The unknown-field-drop the `_NewerSchema` guard defends against is pydantic v2's DEFAULT, not a configured option; a future global `extra="forbid"` would silently break the migration hop-chain with no test pinning the dependency. |
| x-f5 | LOW | transaction + crosspost (X1/X4) | `transaction()` rolls back the entire pass's in-memory state-flips on any uncaught raise after the slow work ran | `ledger.py:302-310`; `pipeline.py:317-320,448-472,431-439` | The whole main pass commits as one transaction with a single exit-save; no intermediate checkpoint. DELIBERATE and SAFE (artifacts persist, next pass recovers cheaply), but wastes the pass's state work. |
| c4-f1 | LOW | request_moment_hooks (4a) | Per-account hook brief built from ALL accounts (incl. inactive/suspended), not active ones | `moments.py:201-203`; `moment_hook_learning.py:34` (uses `.active()`); `accounts.py:104-105,184-188`; `crosspost.py:116,203` | The persona comprehension gates on `hook_directive(a)` truthiness, not `a.status is active`; the constructor used the raw `accounts.accounts` list where every other consumer uses `.active()`. |
| c4-f4 | LOW | request_moment_hooks (4a) | Per-window keyframe ffmpeg extraction for the hook gate runs INSIDE the ledger lock | `pipeline.py:448-456,206-219`; `moments.py:55,209-223`; `keyframes.py:12,14,28-30`; `pipeline.py:144-158` | The frame-extraction cost was added to the hook gate (a later milestone) without a lock-free prewarm counterpart, unlike transcode/signal/render; timeout-bounded so a delay not a wedge. |
| c4-f5 | LOW | ingest_moment_hooks / hookscore (4b/4c) | Imperative-opener third-person-PLURAL artist narration escapes both strip predicates | `hookscore.py:24,40,50,60-63`; `moments.py:267-269`; `hookcheck.py:31-49`; `prompts.py:59-62` | The two predicates split coverage (singular → `has_artist_reference` no exemption; everything else → `narration_signature` WITH an opener exemption), leaving the intersection uncovered. The design explicitly delegates plural to the prompt — an accepted residual. |
| c5-f4 | LOW | Casting (5) | `cast_moments` heuristic is unwired dead code carrying a divergent affinities-only writer (no durable AccountSelection) | `casting.py:48-85,160,213-219`; `pipeline.py:228,244-245`; grep: only tests/docstrings reference it | A selector retained from before the RF1 redesign was never updated to the durable model; latent inconsistency on re-activation, not a live defect. |
| c0-f1 (Ingest) | LOW | Catalogue (0b) | URL-pull ingest mislabels ALL pre-existing inbox files as `source_origin='url'` | `ingest.py:116-117,125,170`; `cli.py:579`; `actions_run.py:66`; grep: `source_origin` has no behavioral reader | `download_url` and ingest are decoupled (download writes the shared inbox, ingest scans the whole inbox); origin is a pass-level arg, not a per-file attribute. Provenance drift only (write-only metadata). |
| c0-f2 (Transcribe) | LOW | Transcribe (1) | Duration-aware ASR model selection does NOT apply to the legacy whisper fallback path | `transcribe.py:160-163`; `config.py:396-397,407-414` | The duration-aware selector was added to the faster-whisper path only; the legacy CLI path retained its single-model resolution. Fail-open, but the documented short-source upgrade silently doesn't happen on the fallback engine. |
| c0-f4 (Ingest) | LOW | discover/intake (0a) | `discover()`/`intake()` manifest+intaken writes are unsynchronized across concurrent runs | `discover.py:26-33,88-103,115-136`; `ledger.py:196,307,321`; `cli.py:662,672` | Review-folder control files have no flock (unlike the ledger), relying on the single-operator assumption; atomic write protects torn reads but not lost updates. Theoretical for a single-operator tool. |
| c0-f1 (Asset) | LOW | Caption gate (6b) | Caption prompt menu ignores the live reach store + curated corpus, so the model picks from a set that vetting then drops | `prompts.py:338` (`vetted_menu()` no store); `hashtags.py:53-62,141,184`; `caption.py:293-296`; `fanops_hashtags.py:83-84` | `vetted_menu()` is parameterized on `store` but the caption-prompt caller never threads `load_store(cfg)` into it, so the model-facing menu and the ingest-time membership gate derive from two different sources of truth. |
| c0-f3 (Asset) | LOW | Caption gate (6b) | Brand-risk hold keeps only the FIRST offending surface's reason; later off-brand captions are vetted and written before the hold | `caption.py:287-288,297,314-320`; `caption.py:114,281-282`; `crosspost.py:137-139` | The brand-risk check and the `meta_captions` write are interleaved with a held flag deferred to after the loop; captions are written optimistically and only blocked at the end. Diagnostic-quality (a later clean re-ingest overwrites), not data-loss. |
| c0-f1 (Moments) | LOW | request_moments (3a) | moment_pick prompt embeds verbatim transcript/signal JSON with no size bound | `prompts.py:207-208`; `moments.py:115-116`; `llm.py:84-87` | The pick brief is raw `json.dumps` of the whole transcript/peaks with no max-length policy; STDIN removed the hard ARG_MAX failure but no soft budget replaced it. |
| c0-f2 (Moments) | LOW | request_moments (3a) | request_moments has no in-function write-once guard — unlike request_moment_hooks — so write-once depends solely on the SourceState gate | `moments.py:123` vs `:213-214`; `agentstep.py:42-44,56-58`; `pipeline.py:188-189`; `responder.py:93-97` | Write-once for the pick gate is enforced by the caller's state guard, not at the gate (asymmetry with the hook gate). Currently safe; the TOCTOU guard would drop any stale answer regardless. |
| c0-f3 (Moments) | LOW | ingest_moments (3b) | Stale module docstring describes the OLD single-pass behavior predating the M1b two-pass split | `moments.py:1-5` vs `:108-131` | The module-level docstring was not updated when the gate split into pick + hook passes; the per-function docstrings were. |
| c0-f4 (Moments) | LOW | ingest_moments (3b) | `_drop_overlaps` de-dups by window geometry only; the kept pick's reason can mismatch the merged window | `moments.py:82-91,145-158,148`; `casting.py:43,113`; `views_review.py:378,458` | Overlap de-dup is a pure geometric keep-first with no metadata merge; the count is logged but the dropped pick's reason is not surfaced. `reason` is display/fallback only, not a gate. |
| c2-f3 (Asset) | LOW | write-once transport (2f) | `agentstep` atomic write assumes same-filesystem tmp; `os.replace` raises across filesystems | `agentstep.py:50-52,66-71`; `pipeline.py:190-191`; `responder.py:106-107` | `os.replace`'s same-fs requirement is implicit; the code relies on tmp+target sharing a dir (holds today). No unguarded path — the surrounding quarantine catches `EXDEV`. No current trigger. |
| c2-f4 (Asset) | LOW | briefing builder (2e) | `caption_prompt` carries a dead/dormant learned+transferred style feed with a stale "next session" comment | `prompts.py:316-333,380-384`; `caption.py:140-141,162-166,186-216` | Leftover plumbing from the pre-root-fix caption-authored-hook era, kept as dormant code with a TODO comment after the hook authorship moved to `moment_hook_prompt`. Pure maintainability drift. |
| c7-f1 | LOW | render_approved_stitches (7d) | Stale docstring claims a removed "cannot supersede a live post" guard that `_precheck` no longer performs | `stitch_render.py:252-257` vs `:270-282`; `tests/test_stitch_render.py:131-139,141` | The function docstring was not updated when the supersede guard was ripped out (782aee6); contradicted by `_precheck`'s own docstring and the pinned additive tests. |
| c7-f2 | LOW | mine_suggestions (7b) | intro_tease producer drops all non-top matcher pairings — only `intro_matches[0]` becomes a plan | `stitch_render.py:91,98,129,138-142`; `intro_match.py:127-133,53-58` | MVP design choice — the producer collapses a ranked list to its head; ranks 2..N are stored but never produced, dead for the gate's lifetime. |
| c7-f3 | LOW | render_approved_stitches (7d) | intro retry-cap can burn an attempt on a TRANSIENT prewarm miss, prematurely parking a renderable plan | `stitch_render.py:300-319,225-248`; `pipeline.py:439,448,128-129,164-165`; `models.py:392-394` | The cap counts "commit found no warm composite" as a failed attempt without distinguishing a genuine unrenderable pair from a transient prewarm miss; `render_attempts` never resets short of a new plan id. |
| c8-f1 | LOW | Learning loop (8f) | Stale hint: learning actuators tell the operator to run the REMOVED `fanops cutover metrics` step | `variant_amplify.py:166-173`; `p4_dim_bias.py:63-66`; `track.py:163-179`; `cli.py:205-215` | The auto-validation feature de-gated learning but the operator-facing runtime hints in the AMPLIFY-ONLY actuators were never updated. |
| c8-f3 | LOW | Learning loop (8f) | `variant_amplify` docstring overclaims `fanops cutover metrics` as THE unfreeze path | `variant_amplify.py:167-172`; `track.py:163-179`; `validation_gate.py:1-9` | Same root as c8-f1 but in the module comment rather than the runtime log; names a manual step as the prerequisite when the real one is an automatic live-metric stamp. |

*Twenty-nine confirmed findings: 1 HIGH, 4 MEDIUM (one of which is concurrency-flag-gated and default-OFF), 24 LOW. No CRITICAL. The HIGH and two of the MEDIUMs share a single root (the casting→crosspost differentiation seam); fixing that seam plus its two observability gaps retires the only structurally consequential cluster.*