<!-- Edit-time rulebook for src/fanops/studio/. Anchors verified 2026-07-03. Tab semantics = root CLAUDE.md; route/action/view traces = docs/CODEMAPS/subsystem-traces/C9,C10. -->
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

## Two local traps (the general pattern is in `src/fanops/CLAUDE.md`)

- **`edit_caption` (`actions.py:87`) skips the brand-risk guard** that its sibling `regenerate_caption`
  (`actions.py:97`) enforces via `caption.brand_risk_flag` — a manual caption edit ships unscreened (MOL-86). If
  you add caption-editing paths, apply `brand_risk_flag`, matching `regenerate_caption`.
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
- **Persona edits are authoritative** — a blank lever field CLEARS it (`personas.py`). Discovery/recommend only
  PROPOSE tags; the operator ACCEPTS into the corpus. Discovery must never auto-write a caption tag (curation gate).

## Where to look

- Route/action + read-projection traces: `docs/CODEMAPS/subsystem-traces/C9_studio_backend.md`, `C10_studio_views.md`.
- Studio-touching defects with lines: `.reports/issue-register-2026-07-03.md` (MOL-66/70/71/82/83/86).
