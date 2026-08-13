<!-- Edit-time rulebook for src/fanops/. Line anchors are a starting point, not a promise — trust the symbol, re-find the line. Routing = root CLAUDE.md; deep reference = docs/CODEMAPS/. -->
# src/fanops — invariants to obey before editing

## Never break these (each has a test that goes red if you do)

- **No-auto-publish.** Every `Post` is born `PostState.awaiting_approval` at one of THREE mint sites — all enter
  the approval gate, none auto-publish:
  (1) pipeline crosspost: `crosspost._mint_surface_post` (`crosspost.py:235`);
  (2) Studio repost: `studio.actions.repost_post` (`actions.py:573`);
  (3) Studio cross-account reuse: `studio.actions.crosspost_to_account` (`actions.py:700`; bulk
  `crosspost_all_to_account` loops here, does NOT mint itself). Do NOT add a fourth `Post(...)` construction
  site and do NOT set a post's state to `queued` anywhere except `Ledger.approve_post` (`ledger.py:615`).
  Publish paths iterate `queued` only, so this is what makes an unapproved post structurally
  unpublishable even live.
- **Cascade protection.** `ledger._delete_moment_cascade` must keep gating deletes on
  `_PROTECTED_POST_STATES` (live-states + awaiting_approval + queued + retired). Re-ingest / reconcile must
  never drop an in-review or approved post. Don't add a delete path that skips this constant. The set — and
  every other partition of `PostState` — is DEFINED in `models.py` beside the enum, with a named complement
  and an exhaustiveness test (`test_post_state_sets`); `Ledger` only re-exports it. A new `PostState` member
  must be classified there, never left to a set's unnamed complement.
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
  default AND logs first. When adding one, log first. New degradable fail-open code uses `errors.fail_open`; silent
  `except Exception` handlers fail CI via `tests/test_swallow_ratchet.py`. On the unattended progress spine
  (responder / signals / cli / doctor / pipeline_status / escalation), breadcrumb alone is not enough —
  `tests/test_escalation_spine_ratchet.py` requires decide / escaping raise / honest fail_open (wrapping real
  work or sole-body degrade). `with fail_open: raise` then continue is theatre and fails the ratchet. Name a
  posture via `fanops.escalation` and burn shared attempts through that module's sole `ATTEMPT_CEILING`. MOL-67
  site tests remain behavioral guards for already-fixed read helpers.
  The genuinely-swallowed sites are inventoried in `anomalies.md`.
- **Atomic control-file writes** route through `controlio.write_json_atomic` / `write_text_atomic` /
  `write_bytes_atomic` (mkstemp same-dir + os.replace); ffmpeg/mpeg temps keep their own `.part` suffix (MOL-78).

## Where to look (open only what the task needs)

- A MOL-numbered task: read the Linear ticket body — it carries the `file:line` anchors. There is NO tracked
  defect register (`.reports/` is gitignored apart from `.reports/architecture/`, so anything else under it is
  one machine's local artifact and is absent from a fresh clone).
- Module→cluster split and the safety-verdict table: `docs/CODEMAPS/full-trace-index.md` (its module COUNTS are a
  frozen snapshot and have drifted — `git ls-files 'src/fanops/**/*.py'` is the live number).
- Any env-var question (default, effect, Studio-settable vs shell-only): `docs/CONFIG.md`.
- Publish / schedule / reconcile internals: `post/CLAUDE.md`. Studio routes/actions/views: `studio/CLAUDE.md`.
  Test traps: `tests/CLAUDE.md`.
