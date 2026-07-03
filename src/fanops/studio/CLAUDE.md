<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# src/fanops/studio — Flask cockpit map & invariants

Localhost single-operator web UI (`fanops studio`, `127.0.0.1:8787`, no auth). Deep reference:
`docs/CODEMAPS/subsystem-traces/C9_studio_backend.md` (routes+actions, 892 lines) + `C10_studio_views.md`
(read projections). Product tab semantics live in the ROOT `CLAUDE.md`.

## App factory + module layout

- **`app.py`** — the factory (`create_app`) + the Home route. Registers the `app_routes_*` blueprints,
  Jinja filters (`timeutil.to_local_display`/`to_local_input`), and `FANOPS_CFG` app-config key.
- **`app_routes_{golive,live,personas,review,run,schedule}.py`** — one blueprint per tab; each route is
  thin: parse form → call ONE `actions_*` (mutation) or `views_*` (read) fn → render a template/partial.
- **`actions*.py` = mutations.** Each writes through exactly ONE `Ledger.transaction` (the lock-safe
  load→mutate→save cycle) — `actions.py`, `actions_approve.py`, `actions_casting.py`, `actions_run.py`
  transact; `actions_common.py`/`actions_wipe.py` are helpers/gates. Never mutate the ledger outside a
  transaction. `actions_run.save_uploads` owns the traversal-safe streamed-upload contract (root CLAUDE.md).
- **`views*.py` = pure reads.** `views.py`, `views_common.py`, `views_live.py`, `views_results.py`,
  `views_review.py` — projections of `Ledger.load` into template context; **no ledger/control-file writes**.
  Two documented exceptions (neither a layering break): `views_common.postiz_health_for_banner` does one
  live GET behind a 30s module cache; `views_results.lineage_stats` mutates its OWN transient args in place
  (violates the immutability house rule — style follow-up, not a safety issue).

## Gate orders a coder must preserve

- **Go-Live** (`golive.go_live`, confirm-gated): accounts-valid → live-ready channels →
  past-due-backlog check → explicit `confirmed=True`. It sets `FANOPS_LIVE` via `_dual_write` (`.env` +
  `os.environ`). NOTE: `go_live` deliberately **NEVER writes `FANOPS_POSTER`** ("D12" comment) — per-channel
  live routing is set by `set_account_backend` (also creds+confirm gated). `go_dryrun` (safe direction)
  needs no confirm.
- **Wipe** (`actions_wipe.confirm_wipe` → `ledger_wipe.execute_wipe`): typed word `REMOVE` (UI gate) →
  mandatory pre-wipe snapshot → `snapshot_is_restorable` check → `execute_wipe` (its OWN code gate re-checks
  snapshot+confirm). **Known caveat:** `app_routes_live.do_wipe_confirm` has NO server-side check that
  `do_wipe_preview` ran first — "preview before confirm" is a UI convention (template hides the form), NOT a
  server-enforced invariant. The destructive code gates are unaffected.

## Boundaries

- **Persona tab write boundary** — `personas.py` create/edit/delete/link + corpus add/remove; the edit form
  is AUTHORITATIVE (blank clears a lever). Discovery/recommend PROPOSE tags; the operator ACCEPTS into the
  corpus — discovery never auto-writes a caption tag (curation gate).
- **Secrets discipline** — every API key (`POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN__<slug>`) is
  WRITE-ONLY: written via `golive._dual_write`, never rendered back to any template/response.

## Fail-open blind spots (see `docs/CODEMAPS/anomalies.md` C9/C10)

Read-helper swallows without a log trail: `preview_media.py:31-38` (WYSIWYG preview ladder),
`app.py:_account_arg`. `views.build_system_strip` (runs every page load) had 4 silent try/excepts — the one
real C10 legibility gap; the current working tree adds `get_logger` breadcrumbs there. `views.zero_post_clips`
was referenced by `home.html` but never passed by the Home route — the current working tree wires it into
`app.py`'s Home `render_template` call.
