<!-- Edit-time rulebook for src/fanops/post/ (the sole network egress). Anchors verified 2026-07-03. Approval/dryrun semantics = root CLAUDE.md; full trace = docs/CODEMAPS/subsystem-traces/C6. -->
# src/fanops/post — the publish path

The only place FanOps POSTs to a network. Get the invariants below wrong and you either publish an unapproved
post or break the reconcile handshake.

## One publish path, queued-only

- `publish_due` (`run.py:337`, daemon/CLI) and `publish_now` (`studio/actions.py:361`, Studio) both funnel into
  `_publish_one` (`run.py:213`) — the SOLE caller of a real network POST (`PostizPoster.publish` /
  `ZernioPoster.publish`), doing claim→`submitting`→network→finalize. Do not add a second network-POST caller;
  route new publish entry points through `_publish_one`.
- A post is unpublishable unless `state is queued`, and only `Ledger.approve_post` promotes it there. No approval
  → no POST, even live. (Verdict HOLDS, C6.)

## Publish handshake (`_publish_one`, `run.py:227`)

`_publish_one` is the SOLE network-POST caller. Every publish entry point (`publish_due`, `publish_now`) funnels
here in three phases:

- **CLAIM (tight txn, `run.py:241-247`):** re-read the post under the ledger flock; publish ONLY if still
  `queued` (double-post guard — a lost race or already-`submitting` post is a clean no-op). Flip
  `queued→submitting` and persist **before** any network I/O (F11 crash-safety: a crash mid-network leaves the
  post `submitting`, healed by reconcile/`fanops resolve`).
- **NETWORK (lock-free, `run.py:248-321`):** on a throwaway loaded ledger, `_ensure_media` then
  `poster.publish`. On `submitted`, gate the `submitted→published` promotion on `public_url` (`run.py:284-285`):
  a real URL → `published`; **no URL** (the normal Postiz async-permalink case) → `needs_reconcile`
  (`run.py:292`), **never** `failed`. A FATAL `AuthError` **re-raises** (`run.py:299-300`) to halt the run —
  never burn the queue. Empty integration id on a live backend un-claims `submitting→queued` (`run.py:256-265`).
- **FINALIZE (tight txn, `run.py:322+`):** merge only the network-determined post fields (+ clip/render media
  cache) into a **freshly loaded** ledger — never persist the stale in-memory snapshot (B4 lost-update).

**Postiz permalink trap:** `_postiz_permalink` (`postiz.py:73`) ALWAYS returns `None` by design, so a fresh
Postiz publish CANNOT self-promote to `published` — it parks in `needs_reconcile` and `reconcile.py` back-fills
the URL on the next pass. See also the traps section below.

## Zernio idempotency — five things that look wrong and are not (report 11)

Anchored by SYMBOL, not line: these move. Full record `docs/reconciliation/11_ZERNIO_IDEMPOTENCY_DESIGN.md`;
tests `tests/test_zernio_idempotency.py`.

- **`_request_id` is NOT `uuid5(ns, post.id)` and must not be "simplified" to it.** It hashes
  `post.id | created_at | platform | account_id`. `crosspost` **pops** a `failed`/`rejected` record and
  **remints it under the identical `post.id`** with a fresh `created_at`, and `_publish_one` refreshes
  `account_id` at publish — so one `post.id` denotes SEVERAL create operations. Dropping `created_at` hands a
  new incarnation the old one's identity and Zernio replays the dead post instead of creating the new one.
  `_ZERNIO_REQ_NS` and `_REQ_NAME_V` are **permanent**: changing either makes a retry derive a different key
  than the send it retries, silently re-opening the double-post hole for every in-flight post.
- **A 409 is NEVER `failed`.** It is duplicate-content, and `failed` is **re-queueable** — filing it there is a
  licence to post it again. Zernio is a hosted *scheduler*, so a 409 proves only that Zernio holds a matching
  record: not platform publication, not ownership by this post, not completion. It parks `needs_reconcile`.
- **`reconcile_candidate_id` is NOT a `submission_id` and must never be copied into one.** `_RECONCILABLE`
  includes `needs_reconcile`, so a candidate parked in `submission_id` would be polled, found live (of course —
  that is *why* Zernio rejected us as a duplicate) and promoted to `published` with **another post's
  permalink**. Never a poll key, never a promotion source, without an explicit operator identity decision.
- **A 4xx `TerminalFailure` withholds the response body ON PURPOSE.** `error_reason` is substring-scanned by
  `is_transient_failure_reason` (`"timeout"`, `"network error"`, `\((\d{3})\)`). A 4xx body echoing
  `"upstream timeout"` would classify a terminal failure as transient and hand it to the daemon re-queue — a
  loop. Bodies ride only `ReconciliationRequired` reasons, which are never auto-requeued.
- **Idempotency does NOT replace the queued-only claim.** The ~5-min window cannot span the 600s daemon
  interval. The header closes the *within-attempt* hole; the claim closes the *cross-pass* hole. Both required.

`zernio_outcome.py`'s types are **private to the Zernio backend** — they must never cross the `Poster`
protocol (`publish(led, post_id) -> Ledger`). `postiz.py` / `dryrun.py` do not import them, by design.

## Two traps that look like bugs but are by design

- **`_postiz_permalink` (`postiz.py:73`) ALWAYS returns `None`** — Postiz returns no URL at publish time. So
  `_publish_one` CANNOT promote `submitted→published` on its own for a fresh Postiz publish; it parks the post in
  `needs_reconcile` (never `failed`), and `reconcile.py` backfills `public_url` later. This two-phase dependency
  is intentional — do not "fix" `_postiz_permalink` to fabricate a URL, and do not downgrade a `needs_reconcile`
  post to `failed`.
- **`_publish_throttle_last` (`run.py:83`) is a module-level dict** — the one piece of true global mutable state,
  enforcing `postiz_publish_per_min`. In-process ONLY by design; it would need rework only if `fanops` ran as
  multiple concurrent processes. `reset_publish_throttle` (`:88`) is test-only.

## Dryrun/live gates + the false-dead-code source

- Two independent gates (both must stay): `_post_provider` returns `"dryrun"` when `not cfg.is_live`
  (`run.py:113`, returns at `:120`); `get_poster` (`post/__init__.py:13`) RAISES rather than build a
  `DryRunPoster` when live (`:19`). These are NOT in `providers.py`.
- **`providers.py` is a false dead-code source.** It resolves backends via lazy in-function import lambdas
  (`_postiz_poster` `:19`, `_zernio_poster` `:20`, `_dryrun_poster` `:21`, and the three uploaders `:23-27`). The
  name-based call graph flags every one "zero callers" — all FALSE POSITIVES. Same for
  `post/compress.persist_post_shrink` (lazy-imported at `studio/actions.py`). Never declare a `post/` function
  dead without grepping for its lazy-import site. `DryRunPoster.publish` is effectively unreached post-M1 but is
  RETAINED as the `Poster`-protocol fallback — keep it.

## Failure posture

`AuthError` HALTS the run (never burns the queue); every other publish error → per-post `failed` (re-queueable)
EXCEPT `needs_reconcile` (never downgraded). No bare `except:` in C6 — keep it that way.

Full function-by-function detail: `docs/CODEMAPS/subsystem-traces/C6_crosspost_publish_post.md`.
