<!-- Edit-time rulebook for src/fanops/. Anchors verified against source 2026-07-03. Product semantics = root CLAUDE.md; deep reference = docs/CODEMAPS/. -->
# src/fanops — invariants to obey before editing

## Never break these (each has a test that goes red if you do)

- **No-auto-publish.** Every `Post` is born `PostState.awaiting_approval` at one of THREE mint sites — all enter
  the approval gate, none auto-publish:
  (1) pipeline crosspost: `crosspost._mint_surface_post` (`crosspost.py:228`);
  (2) Studio repost: `studio.actions.repost_post` (`actions.py:491`);
  (3) Studio cross-account reuse: `studio.actions.crosspost_to_account` (`actions.py:570`; bulk
  `crosspost_all_to_account` loops here, does NOT mint itself). Do NOT add a fourth `Post(...)` construction
  site and do NOT set a post's state to `queued` anywhere except `Ledger.approve_post` (`ledger.py:503`, promotes
  at `:519`). Publish paths iterate `queued` only, so this is what makes an unapproved post structurally
  unpublishable even live.
- **Cascade protection.** `ledger._delete_moment_cascade` (`ledger.py:614`) must keep gating deletes on
  `_PROTECTED_POST_STATES` (`ledger.py:612` = live-states + awaiting_approval + queued + retired). Re-ingest /
  reconcile must never drop an in-review or approved post. Don't add a delete path that skips this constant.
- **Dryrun/live has TWO independent gates** — keep both: `_post_provider` returns `"dryrun"` whenever
  `not cfg.is_live` (`post/run.py:120`), AND `get_poster` raises rather than build a `DryRunPoster` when live
  (`post/__init__.py:19`). `FANOPS_LIVE=1` may be set ONLY by `studio/golive.go_live`. Never add a code path
  that flips it. (These gates are in run.py / post/__init__.py — NOT providers.py; providers.py only holds the
  lazy provider lambdas.)
- **Bias actuators amplify-only, validation-frozen.** `p4_dim_bias`, `variant_amplify`, `timing_bias`
  may only call `adjust.amplify` (p4/variant) or write an isolated prior file (timing). They must NEVER
  retire, state-set, or publish, and every one is default-OFF + gated by
  `validation_gate.learning_validated(cfg)` (`validation_gate.py:22`). (`casting_bias` was removed with the
  P11 casting teardown.) Adding a new learning signal = same shape: off by default, frozen until validated,
  generation/schedule only.
- **Never mass-reformat** (no `black`/`ruff format`; compact one-liner style is deliberate — pyproject comments).
  Never run live `fanops` verbs speculatively (they hit Postiz/Meta Graph).

## Traps that waste an edit or a deletion

- **"Zero callers" is a LEAD, not a verdict.** The `.reports/` call graph is name-based and CANNOT see: aliased
  imports (`from x import f as _y`), lazy in-function imports (the `post/providers.py` lambdas are ALL flagged
  dead and are ALL live), dict-of-lambdas dispatch, Jinja filters, or argparse `type=` callbacks. Before deleting
  anything, grep the whole `src/fanops/` for `<name> as`, `import <name>`, and lazy-import call sites. The
  10 genuinely-dead functions enumerated in the issue register (R-028 / MOL-68) were re-verified and REMOVED
  (test-only callers removed, or converted to the live siblings — e.g. `is_due_or_past`, `download_url`).
- **Sibling parity is where the real bugs live.** Several defects are "one function guards the input, its twin
  doesn't." When you touch one, check its sibling: `Accounts.load` (`accounts.py:98`) has a broad `except` with
  no per-row guard while `Personas.load` (`personas.py:66`) is defensive (MOL-79); `_catalogue_file` in
  `ingest.py` uses `shutil.copy2` while `render_account_cut` does temp+`os.replace` (MOL-74/78). (Studio-side
  sibling gaps — `edit_caption` vs `regenerate_caption` — are in `studio/CLAUDE.md`.)
- **Fail-open with a logged breadcrumb is the house norm** — a subprocess/parse failure degrades to a safe
  default AND logs first. When adding one, log first. The read-helper layer has unlogged swallows that break this
  discipline (MOL-67); don't add more. The genuinely-swallowed sites are inventoried in `anomalies.md`.

## Where to look (open only what the task needs)

- Every open defect with exact `file:line`, class, severity, and its MOL-* id: `.reports/issue-register-2026-07-03.md`.
  Read this FIRST for any MOL-numbered task — it saves the re-derivation.
- Full 108-module map / 10-cluster split / safety-verdict table: `docs/CODEMAPS/full-trace-index.md`.
- Any env-var question (default, effect, Studio-settable vs shell-only): `docs/CONFIG.md`.
- Publish / schedule / reconcile internals: `post/CLAUDE.md`. Studio routes/actions/views: `studio/CLAUDE.md`.
  Test traps: `tests/CLAUDE.md`.
