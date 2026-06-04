# Per-Account Creative Variation (A/B content learning) — Design Spec

**Date:** 2026-06-04 · **Backlog item:** (j) / the C2 reframing · **Status:** design approved, ready for implementation plan

## Problem

Today every fan account posts the **same clip** with a **near-identical caption** for a given moment. The system's whole purpose is to *learn what performs*, but with identical creative there is nothing to learn — the `track → analyzed → adjust` lift loop only sees one creative treatment per moment. This is the *valuable* reframing of the rejected C2 finding (NOT byte-perturbation for forensic evasion — the operator explicitly does not want that; see `fanops-build-deviations.md`): generate genuinely different creative per account so the existing lift loop can attribute which treatment wins per audience.

## Approved design decisions (operator, 2026-06-04)

1. **Variant axis = per-account CAPTION + per-account on-screen HOOK.**
   - Captions are *already* per-surface (`clip.meta_captions[surface]`) — the caption agent will generate genuinely distinct angles per account instead of near-duplicates.
   - The burned-in hook (shipped in PR #8) is currently one-per-moment, burned into one-shared-clip-per-aspect. v1 makes the **hook per-account**.

2. **Assignment = per-account (every active account gets its own distinct variant).** Deterministic, content-addressed seeding (NO `random` — the codebase is content-addressed; a random assignment would break idempotency and re-post duplicates, the #1 v1 bug). The variant's identity derives from the surface (`account|platform|clip`) via the existing SHA seam.

3. **Hook render = SHARED BASE + per-account hook OVERLAY (two-stage).** Render the reframed+subtitled **base clip ONCE per aspect** (unchanged from today), then a cheap **second ffmpeg pass** burns each account's hook onto a per-account output copy. Cost = 1 base render + K fast overlay passes per (moment, aspect), vs K full renders. The base clip stays the shared-media artifact; the per-account file is the base + that account's hook.

4. **Attribution = OBSERVE-ONLY (v1).** Record which variant each Post used (the hook text + a variant key) on the `Post`. The digest/track report shows **lift-by-variant** so the operator sees what wins. NO automated propagation into amplify in v1 — this deliberately adds ZERO new risk to the `amplify`/`classify_outcomes` machinery (which has a CRITICAL cascade-delete bug history, C1). Auto-biasing future creative toward winners is a documented follow-up, gated on real lift-by-variant data.

## Architecture

The variant is produced at **caption time** (the per-account caption + the per-account hook are decided together, per surface) and realized at **crosspost time** (each Post points at a per-account clip file = base + that account's hook, and carries its caption + variant metadata).

**Data flow:**
```
moment (has default hook)
  → request_captions: ask the caption agent for per-(account,platform) caption AND hook
  → ingest_captions: store per-surface {caption, hashtags, hook} in clip.meta_captions
  → crosspost_clips, per surface:
        base_clip = reframed+subtitled clip (shared per aspect, rendered once)
        variant_clip = overlay.burn_hook(base_clip, hook=meta_captions[surface].hook, account=...)  # cheap 2nd pass, per-account file
        Post(..., parent_id=variant_clip.id OR base + media points at variant file,
             caption=..., variant_key=<surface-derived>, variant_hook=<hook>)
  → publish → track → analyzed (lift_score per Post, already per-account)
  → digest: "Lift by variant" section ranks posts' lift_score grouped by variant
```

**Backward compatibility:** when variation is OFF (a new `FANOPS_CREATIVE_VARIATION` toggle, default OFF so existing behavior is unchanged until opted in) OR when the caption agent returns no per-surface hook, the system falls back to TODAY's behavior: shared clip, moment's default hook, per-surface caption as-is. Variation is purely additive.

## Units / interfaces (what changes)

- **`overlay.py`** — add `burn_hook_only(base_clip_path, out_path, hook, *, width, height, font) -> bool`: a cheap ffmpeg pass that burns ONLY a hook (top-third, same HOOK style as `build_ass`) onto an already-rendered base clip. Fail-open (no text filter → copy the base unchanged + return False). Reuses the existing ASS-build + capability-probe code.
- **`models.py`** — `CaptionItem`/`CaptionSet` gains an optional per-surface `hook` field; `Post` gains optional `variant_key: str | None` + `variant_hook: str | None` (observe-only attribution; optional → old ledgers load).
- **`prompts.py` / `caption.py`** — the caption prompt asks the model for a per-surface `hook` (distinct, punchy, ≤7 words, language-matched) in addition to the caption; `ingest_captions` stores it. If absent, fall back to the moment's `derive_hook` default (no per-account variation for that surface).
- **`crosspost.py`** — per surface, when variation is on: produce a per-account variant clip via `overlay.burn_hook_only` from the shared base clip, point the Post's media at it, and stamp `variant_key` (deterministic: `surface_key(account, platform)` + clip) + `variant_hook` on the Post.
- **`config.py`** — `FANOPS_CREATIVE_VARIATION` (default OFF).
- **`digest.py`** — a "Lift by variant" section: group `analyzed` posts by `variant_key`/`variant_hook`, show each variant's lift_score, so the operator sees what performs.

## Testing strategy

- `overlay.burn_hook_only`: unit — builds the right ffmpeg cmd (hook-only ASS, base as input, distinct output); fail-open when no text filter (returns False, output = copy of base). A real-render integration check (like the subtitles Integrate) proving the hook appears on a per-account copy and the two accounts' copies DIFFER.
- caption per-surface hook: the prompt asks for it; `ingest_captions` stores `meta_captions[surface]["hook"]`; missing hook falls back to the moment default.
- crosspost: with variation ON + 2 accounts, the 2 Posts get DIFFERENT `variant_key`/`variant_hook` and point at DIFFERENT clip files; with variation OFF, behavior is identical to today (shared clip, no variant_key). Determinism: same inputs → same variant_key across processes.
- digest: an analyzed post with a `variant_key` shows up under "Lift by variant" with its lift_score; no variants → section absent.
- Backward-compat: an old ledger Post without `variant_key` loads + renders fine; variation OFF leaves the existing 338 tests green.

## Out of scope (v1)

- Automated propagation of winning variants into amplify/caption guidance (the C1-risk path) — documented follow-up, needs real lift-by-variant data first.
- Full edit/cut variants per cohort (different moment windows) — larger scope, deferred.
- Cohort bucketing / explicit cohort config — v1 is per-account; cohorting is a later optimization if K grows large.
- Trending-audio per variant — separate (integration-gated) feature.

## Risks / guardrails

- **Determinism (the #1 v1 bug class):** `variant_key` MUST be content-addressed (SHA over `account|platform|clip`), never `random`/`hash()` — a re-run must produce the identical key + identical per-account clip path, or crosspost re-posts duplicates.
- **The amplify cascade-delete history (C1):** v1 is observe-only and touches NONE of `amplify`/`classify_outcomes`/`_delete_moment_cascade`. Keep it that way until the follow-up is designed with guards.
- **Fail-open everywhere:** no text filter, no per-surface hook, variation off → fall back to today's shared-clip behavior; never block/error a post because a variant couldn't be produced (the subtitles fail-open invariant extends here).
- **Render cost:** K overlay passes per (moment, aspect). Bounded by active-account count; the base render is still once-per-aspect. If K grows large, cohorting (deferred) caps it.
