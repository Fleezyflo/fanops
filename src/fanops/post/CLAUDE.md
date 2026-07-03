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
