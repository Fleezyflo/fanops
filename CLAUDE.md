# FanOps — project notes for Claude

MOH FLOW FAN OPS: intelligent clip + cross-post engine. Pure-Python `src/` layout
(`src/fanops/`), console script `fanops`, Python 3.12–3.13.

## Commands

- Install: `pip install -e '.[dev]'` — extras: `[studio]` (Flask cockpit, imported lazily), `[transcribe]` (whisper CLI), `[compose]` (MoviePy produced-clip compositing — `fanops compose`, lazy + fail-open)
- Fast unit suite (CI `unit` job): `python -m pytest -q -m "not integration"`
- Integration suite (CI `e2e` job): `python -m pytest -q -m integration -rs` — needs real ffmpeg/ffprobe/whisper/espeak on PATH; skips locally when absent; CI sets `FANOPS_REQUIRE_E2E=1` so a skip fails
- Lint: `ruff check .` (pyflakes F + pycodestyle E only)
- Studio dev server: `fanops studio` (localhost:8787; requires `[studio]` extra)
- Browser ingestion (no Finder): the Studio **Run** tab **Upload video** form streams raw video into `01_inbox/` (validated: video ext, traversal-safe `secure_filename`, inbox-bound resolve, atomic `.uploadpart`→`os.replace`; 2 GiB `MAX_CONTENT_LENGTH` cap) → click **Ingest inbox** to catalogue it. `actions.save_uploads` owns the contract; an oversize body re-renders the panel at HTTP 200 ("too large") since htmx 2.x drops non-2xx swaps.
- Approval lifecycle (no auto-publish): a crossposted post is BORN `PostState.awaiting_approval` — `publish_due`/`publish_now` iterate only `queued`, so NOTHING publishes (even on a live backend / daemon) until the operator approves it. The Studio **Review** tab is the approve worklist (per-surface checkboxes → batch **Approve selected** / **Reject selected**; `Ledger.approve_post` promotes awaiting→queued). Per-post-per-surface scheduling is first-class (P1): approving an UNTIMED / stale-past post no longer bumps it to **now** (which silently published it on the next `publish_due`) — it lands a deterministic **strictly-future suggestion** (`views.suggest_time` = `crosspost.surface_time` at `index=0`, never a 40-min stagger), so it sits in `queued` for the lead window instead of firing instantly; a still-future operator-set time is preserved verbatim. Every editable Review surface AND Schedule row offers **Use suggested** (one-click accept the suggestion) and **Clear time** (`actions.clear_time` — on a queued post it FIRST un-approves back to Review, THEN clears, so a post is never left `queued`-and-timeless). The **Schedule** tab is the approved-posts bucket cockpit (shows each post's Postiz integration; per-row Move / Use suggested / Clear time / Publish now / Send back to Review; **Reschedule all** re-spreads via `crosspost.surface_time` — the 40-min auto-stagger is reachable ONLY here, never imposed on an individual post). The **Posted** tab is the all-time shipped library (live URL + lift) + **Post again** (`actions.repost_post` spawns a fresh awaiting_approval repost of the same clip with an epoch-suffixed id — honors fan-accounts-repost-freely, NOT a supersede). `queued` now means "approved + scheduled"; `rejected` is an operator discard. Mirrors the M3/M4 stitch approve/release gate.
- Onboard + go live (no env vars / CLI / JSON): the Studio **Go Live** tab connects Postiz, **adds an account** (handle + platforms), maps **each (handle × platform) channel** to its own Postiz integration id (`accounts.json` `integrations` is per-platform — a handle's IG and TikTok are different integrations; a legacy single `account_id` stays the fallback), and flips dryrun↔live behind a confirm (dual-writes `.env` + `os.environ`; the API key is write-only — never rendered). `go_live` is the only setter of `FANOPS_POSTER=postiz`, gated on readiness + confirm; an unknown `FANOPS_POSTER` resolves to dryrun (never a false LIVE banner). The "Publish by hand" tab stays the zero-infra fallback.
- Unfreeze learning on Postiz: the Go-Live tab's **5 · Validate learning** step runs the Postiz cutover (`cutover.py` dispatches postiz↔blotato; postiz path in `cutover_postiz.py`) — posts ONE confirmed 2099-scheduled throwaway probe to an **operator-selected** integration, reconciles its real analytics labels against `track._W`, and writes `cutover.json metrics_confirmed` (+ the raw label set + map). That is the single freeze flag `learning_validated` reads, so it unfreezes `variant_amplify`/`ucb`/`transfer` on Postiz. Writes ONLY `cutover.json`, never the ledger; the key is never echoed; never auto-fires (operator-gated, never reachable from run/advance).

## Constraints

- NEVER mass-reformat: no `black`, no `ruff format`. The compact one-liner house style
  (E701/E702/E401/E501 ignored) is deliberate — rationale in pyproject.toml comments.
- The global 60s pytest timeout is a deadlock guardrail (ledger flock). A hanging test
  is the bug; don't raise the timeout to make it pass.
- The `fanops` CLI has live verbs that hit external services (Blotato). Don't run it
  speculatively; tests and read-only verbs only unless the operator asks.
- `.claude/workflows/*.js` are tracked, load-bearing build workflows — never delete.
