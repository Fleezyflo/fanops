<!-- Edit-time rulebook for src/fanops/studio/. Line anchors are a starting point, not a promise — trust the symbol, re-find the line. This file OWNS tab + approval-lifecycle semantics; route/action/view traces = docs/CODEMAPS/subsystem-traces/C9,C10. -->
# src/fanops/studio — Flask cockpit rules

Localhost single-operator UI (`fanops studio`, `127.0.0.1:8787`, no auth by design — don't add CSRF/rate-limit
tickets; declined as out-of-scope for localhost). Template edits hot-reload on refresh; Python (`app.py`, routes,
actions, views) changes still require a Studio restart.

## Layer discipline (preserve it on every edit)

- **`app.py`** — `create_app` factory (`:247`) + the Home route `index()` (`@app.get("/")`, `:360`). Home must
  keep passing `zero_post_clips=views.zero_post_clips(cfg)` to `render_template` (`app.py:365`) — the template
  block silently renders nothing if you drop it (that was the MOL-66 bug; the wiring is now present, don't
  regress it). Blueprints `app_routes_{golive,live,personas,review,run,schedule}.py` are one-per-tab; each route
  stays thin: parse form → call ONE `actions_*` (mutate) or `views_*` (read) fn → render.
- **`actions*.py` mutate through exactly ONE `Ledger.transaction`** (the lock-safe load→mutate→save). Transactors:
  `actions.py`, `actions_approve.py`, `actions_casting.py`, `actions_run.py`. Helpers (no transaction):
  `actions_common.py`, `actions_wipe.py`. Never mutate the ledger outside a transaction.
- **`views*.py` are pure reads** — projections of `Ledger.load`, no ledger/control-file writes. Two sanctioned
  exceptions (not layering breaks): `views_common.postiz_health_for_banner` does one cached live GET;
  `views_results.lineage_stats` mutates its own transient args (that's MOL-70, an immutability nit, not a safety
  bug). `suggest_time`/`clear_time` live in `views_common.py`/`actions.py` and are re-exported by
  `views.py`/`actions.py` — the re-export is why grep for the definition and the call site land in different files.

## Approval lifecycle + tab semantics (this file owns them)

Nothing auto-publishes. A crossposted post is BORN `PostState.awaiting_approval`; `publish_due`/`publish_now`
iterate `queued` ONLY, so nothing publishes — even live, even under the daemon — until the operator approves.
`Ledger.approve_post` promotes awaiting→queued. `queued` means "approved AND scheduled"; `rejected` is an
operator discard. The tabs:

- **Review** — the approve worklist (per-surface checkboxes → batch Approve/Reject; bulk-approve by moment
  (`approve_clip`) or account (`approve_account`); cast chips; batch-grouped).
- **Schedule** — the approved-posts bucket cockpit (per-row Move / Use suggested / Clear time / Publish now /
  Send back to Review, each row's Postiz integration shown). **Reschedule all** re-spreads via
  `crosspost.surface_time`; the 40-min auto-stagger is reachable ONLY here, never imposed on one post.
- **Posted** — the all-time shipped library (live URL + lift) + **Post again** (`actions.repost_post` mints a
  fresh awaiting_approval repost with an epoch-suffixed id — a repost, explicitly NOT a supersede).

Per-post-per-surface scheduling is first-class: approving an UNTIMED or stale-past post must NOT bump it to
*now* (that silently published it on the next `publish_due`) — it gets a deterministic **strictly-future**
suggestion (`views.suggest_time` = `crosspost.surface_time` at `index=0`, never the stagger), so it waits in
`queued` for the lead window. A still-future operator-set time is preserved verbatim. `actions.clear_time` on a
queued post FIRST un-approves back to Review, THEN clears — a post is never left `queued`-and-timeless.

## Two local traps (the general pattern is in `src/fanops/CLAUDE.md`)

- **`regenerate_caption` runs the model OUTSIDE the ledger transaction** and re-guards the post inside a short
  one before writing — a `claude -p` call can take ~180s and holding the flock that long deadlocks a concurrent
  run (exactly what the 60s pytest timeout catches). Keep slow calls out of the lock. (`edit_caption` applies
  the SAME `caption.brand_risk_flag` screen its sibling does — MOL-86 closed that gap; do not "re-add" it.)
- **Read helpers here swallow WITHOUT logging** (`preview_media.py`, `app.py:_account_arg`), breaking the
  fail-open-with-breadcrumb norm (MOL-67). When touching a read helper, log before degrading.

## Gate orders you must not reorder or shortcut

- **Go-Live** (`golive.go_live`, `golive.py:581`): accounts-valid → ≥1 live-ready channel → past-due-backlog
  check → explicit `confirmed=True` → `_dual_write("FANOPS_LIVE", ...)` (`:632`, writes `.env` + `os.environ`).
  It deliberately NEVER writes `FANOPS_POSTER` (the D12 comment, `:652`) — per-channel routing via
  `set_account_backend` is the publish truth; go_live only *unsets* a stale `FANOPS_POSTER=dryrun`. `go_dryrun`
  (safe direction) needs no confirm. Do not add a `FANOPS_POSTER` write to go_live.
- **Wipe** (`actions_wipe.confirm_wipe` `:35` → `ledger_wipe.execute_wipe` `:192`): typed word
  `CONFIRM_WORD = "REMOVE"` (`:21`) → mandatory pre-wipe snapshot → `snapshot_is_restorable` → `execute_wipe`
  (its OWN re-check of snapshot+confirm). **Known gap (MOL-71):** `app_routes_live.do_wipe_confirm` (`:30`) has
  NO server-side check that `do_wipe_preview` (`:24`) ran first — "preview before confirm" is only a UI
  convention (template hides the form). The destructive typed-word/snapshot code gates are unaffected; if you
  close MOL-71, add the server check WITHOUT weakening those.

## Secrets & persona-tab boundaries

- **Every API key is write-only** (`POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN__<slug>`): set via
  `golive._dual_write`, NEVER rendered back into any template/response. Don't add a field that echoes one.
- **Persona edits are authoritative** — a blank lever field CLEARS it (`personas.py`). Niche is the exception:
  an empty niche is refused (`persona niche is required`) — a persona with none cannot discover hashtags. The
  hashtag corpus is NOT editable: it is DERIVED from platform measurements every tick
  (`persona_research.derive_corpus`), so the tab shows it read-only. The operator's hashtag lever is the
  declared niche (`/personas/niche` sets the search root).

## Where to look

- Route/action + read-projection traces: `docs/CODEMAPS/subsystem-traces/C9_studio_backend.md`, `C10_studio_views.md`.
- Go-Live onboarding, per-(handle × platform) integration ids, and the dryrun↔live flip: `docs/GOLIVE.md`
  (`accounts.json` `integrations` is per-platform; a legacy single `account_id` remains the fallback).
- The Studio **Run** tab upload contract (`actions_run.save_uploads`, traversal-safe, inbox-bound, atomic, the
  htmx-200 oversize quirk): the upload-safety audit in `C9_studio_backend.md`.
