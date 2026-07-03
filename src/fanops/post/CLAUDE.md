<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# src/fanops/post — the publish path & invariants

The sole network egress of the pipeline. Deep reference: `docs/CODEMAPS/subsystem-traces/C6_crosspost_publish_post.md`.
Publishing SEMANTICS (approval lifecycle, dryrun→live) live in the ROOT `CLAUDE.md`; this file = the
publish-path structure + the traps a coder must know.

## The one publish path (queued-only)

- **`publish_due`** (`run.py`) — the daemon/CLI path; filters `posts_in_state(PostState.queued)`.
- **`publish_now`** (`studio/actions.py`) — the Studio path; drives the SAME queued-only path via
  `run.publish_post`.
- Both funnel into **`_publish_one`** (`run.py:213`) — the SOLE caller of a real network POST
  (`PostizPoster.publish` / `ZernioPoster.publish`). It does claim→`submitting`→network→finalize.
- **Invariant:** a Post is unpublishable unless `state is queued`, and only `Ledger.approve_post` promotes
  `awaiting_approval`→`queued`. No approval → no network POST, even on a live backend. (Verdict: HOLDS,
  C6 audit.)

## Provider resolution ladder (a false dead-code source)

`providers.py` resolves the backend via a dict of **lazy in-function `import`-then-return lambdas**
(`_postiz_poster`/`_zernio_poster`/`_dryrun_poster`, `_postiz_uploader`/`_zernio_uploader`/`_dryrun_uploader`).
The name-based call graph in `.reports/` CANNOT see these lazy imports — every one flags "zero callers" and
is a FALSE POSITIVE. Same for `post/compress.persist_post_shrink` (lazy-imported at `studio/actions.py:396`).
Sweep for lazy imports before ever declaring a `post/` function dead.

- `_post_provider`→`"dryrun"` unconditionally when `not cfg.is_live`; `get_poster` separately refuses to
  build a `DryRunPoster` when live — the two independent dryrun/live gates.
- `DryRunPoster.publish` is effectively unreached post-M1 (`write_preview` is called directly) but is
  RETAINED as the `Poster`-protocol fallback — intentional, not dead.

## Two traps

- **`_postiz_permalink` (`postiz.py:73`) always returns `None` by design** — Postiz's API returns no URL at
  publish time. So `_publish_one`'s `submitted→published` promotion CANNOT fire for a fresh Postiz publish on
  its own; it necessarily waits for `reconcile.py` to backfill `public_url` later (a two-phase dependency).
  A post that publishes without a URL parks in `needs_reconcile`, never `failed` (R1 terminal-URL invariant).
- **`_publish_throttle_last` (`run.py:83`) is a plain module-level dict** — the one piece of true global
  mutable state here, enforcing `postiz_publish_per_min`. In-process ONLY by design; it would need
  revisiting if `fanops` ever ran as multiple concurrent processes. `reset_publish_throttle` is test-only.

## Failure posture

`AuthError` HALTS the run (never burns the queue); every other publish error → per-post `failed`
(re-queueable) except `needs_reconcile` (never downgraded). No bare `except:` and no unlogged
`except Exception: pass` anywhere in the 17 C6 files.
