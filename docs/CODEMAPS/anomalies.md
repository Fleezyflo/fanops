> 🧊 **Frozen 2026-07-11 snapshot — historical, NOT current invariant state.** Invariants map, not auto-synced. When prose and code disagree, the code is right.
> **This file does not own the invariant verdict.** Current invariant state is carried by `tools/arch` (policy + `ARCH-GATE`), `.reports/architecture/INVARIANT_AUDIT.md`, and the tests — never by this snapshot. See `docs/ARCHITECTURAL_LAWS.md` and `docs/governance/EVIDENCE_RECONCILIATION.md` R8.
> **Superseded on the wipe/restore path (RC-4/RC-5).** The §Summary "all HOLD / none CRITICAL" verdict below was **false when frozen**: `restore_snapshot` took no ledger lock, so a live writer's commit could be silently discarded — a real CRITICAL data-loss defect on the very wipe-confirmation invariant this file recorded as holding. Found after the freeze; **fixed** by #653 (restore serializes on the ledger lock) + #654 (`fanops restore` exposed). Authority: `docs/ARCHITECTURAL_LAWS.md` `LAW-PERSIST-02` — "Residual: none (defect discharged)."
> **C1 re-verified against live source 2026-07-14** (4/6 entries were stale — see the C1 note); other clusters remain at the 2026-07-11 snapshot and likely carry similar rot until re-verified.

<!-- Generated: 2026-07-03 | Source: 10 exhaustive Sonnet-agent subsystem traces (docs/CODEMAPS/subsystem-traces/C1-C10) cross-referenced against deterministic AST/call-graph analysis (.reports/) | Token estimate: ~1400 -->
# FanOps Anomaly Ledger

Every anomaly, dead-code lead, and silent-failure site found across the full 108-module trace
(see [full-trace-index.md](full-trace-index.md) for the trace methodology and coverage proof).
Grouped by cluster, in file:line order. This is the flat, complete ledger — the index file
summarizes and ranks; this file is exhaustive.

None of the entries below are CRITICAL/blocking findings — the codebase's core safety invariants
(no-auto-publish, wipe-confirmation, dryrun/live boundary, ledger cascade protection, bias-scope
isolation) all independently HOLD, verified per-cluster (see the verdict table in
full-trace-index.md). These are code-quality/legibility findings: dead code, unlogged swallows,
one wiring bug, one docs-staleness item.
**⚠ CORRECTION (2026-07-16) — the paragraph above was FALSE for `wipe-confirmation` when written.**
Recorded, not rewritten, per this repo's "correct the record, don't quietly patch" rule. The trace
checked that a wipe *asks* (snapshot + typed confirm) but never that its **restore** was safe under
concurrency: `restore_snapshot` took no ledger lock and `os.replace`d the DB, so a live writer's
committed transaction could be silently discarded — a CRITICAL data-loss defect on this very
invariant. It was invisible to a per-cluster read because the defect lives in the *interaction*
between two correct-looking units. Fixed by #653/#654. **Method lesson:** "the invariant HOLDS"
here meant "the guard exists," not "the guard is sufficient under concurrent access" — an
invariant is only as strong as the axis the trace thought to test. Current verdict authority:
`tools/arch` + `INVARIANT_AUDIT.md` + `LAW-PERSIST-02`, never this file. **Taxonomy note (added by W5e / trace-remediation,
MOL-254):** the invariants above measure *doesn't-publish-wrong / doesn't-crash / doesn't-cascade*;
they had **no axis for fail-silently-and-forever** (a gate that fails deterministically, logs, and
re-requests every tick with no operator-visible terminal). That class is now named below under C6.
`logging ≠ surfacing`.

## C1 — Core data model & persistence

> **Re-verified against live source 2026-07-14.** 4 of the 6 original entries were stale: 1 docstring genuinely fixed (`ledger.py:2`), 1 flatly false (`RenderState` is live at `views_results.py:112`), 1 anchor moved + only PARTIALLY fixed (`ledger_wipe` — silent→logged but mis-surfaced / wrong-level / over-broad catch; see its entry), 2 unchanged-and-correct. Re-verification judged not just whether a swallow now logs, but whether it logs to the SURFACED channel (`get_logger`→`run.log`, not stdlib logging), at a level alerting sees, catching only the real failure — **"it logs now" ≠ "fixed the least-defective way."** Corrections + evidence inline.

- `log.py:15` `get_logger` — `except OSError: pass` around best-effort file-creation/chmod. Intentional fail-open, inconsequential. *(Re-verified 2026-07-14: unchanged, still correct.)*
- `ledger.py:404` `_save_unlocked` — `except OSError: pass` around `os.chmod(tmp, 0o600)`. Intentional; atomic replace unaffected. *(Re-verified 2026-07-14: unchanged, still correct.)*
- `ledger_wipe.py:218` `snapshot_is_restorable` (was `:188`) — **PARTIALLY fixed, not least-defective.** The `except Exception` at `:233` now logs before `return False` (`:234`), so it is no longer *silent* — but the fix is weak on the system's most safety-critical path (the pre-wipe restorability gate that guards against irreversible loss), for three reasons: **(1) mis-surfaced** — it logs via stdlib `logging.getLogger(__name__)` (`:44`), NOT the house `get_logger` that writes the surfaced `07_reports/run.log`+stderr stream; per this file's own `logging ≠ surfacing` note, a run-tooling-invisible breadcrumb on the wipe path is a weak breadcrumb. `cfg` is one frame up in the sole caller `execute_wipe(cfg, …)` (`:238`, calls at `:249`), so threading it to emit a structured `("wipe","-","snapshot_unrestorable",level="error",err=…)` record is ~2 lines. **(2) level too low** — `warning` understates "your rollback point failed to verify"; the degradation-honesty standard is `error` for a production-halting/refusing condition so alerting keys on it. **(3) over-broad catch** — the real failures are `sqlite3.Error` (corrupt/non-SQLite image) + `OSError` (unreadable path); bare `Exception` launders an unexpected programming error into "unrestorable" and silently refuses the wipe for the wrong reason. Narrow to `(sqlite3.Error, OSError)` and let the unexpected propagate. (Sibling manifest-write swallow at `:261`/`:262` shares residual issues 1–2 but is lower-stakes — the wipe proceeds regardless.)
- ~~`ledger.py:2` — stale module docstring ("SQLite WAL ledger") — schema now has 10+ maps.~~ **FIXED.** The docstring now reads "Persists 8 id->unit (model) maps … plus 2 non-unit scalar/plain-dict maps" — accurate to the current schema. No drift remains.
- `models.py:42-52` — `RenderState` members — **original "no writer, dead-by-design" is FALSE for 3 of 4.** `RenderState.queued`/`published`/`analyzed` ARE read: `studio/views_results.py:112` `_SHIPPABLE_RENDER = (RenderState.rendered, RenderState.queued, RenderState.published, RenderState.analyzed)`. Only `RenderState.retired` remains writerless (genuinely reserved). Correction: not dead — consumed by the render-shippability gate.
- `models.py:484-485` — `BatchState.closed`/`BatchState.error` — reserved, no writer found. *(Re-verified 2026-07-14: still writerless — accurate. Reserved-by-design surface per `src/fanops/CLAUDE.md` "'zero callers' is a LEAD"; do NOT delete without checking for old serialized ledger rows in these states.)*

## C2 — Ingest & source acquisition

- `vocals.py:35-36` `_demucs_env` — `except Exception: pass` around an `import certifi`. Swallows any exception, not just `ImportError`; low-risk but should be narrowed.
- `discover.py:59-62` `candidate_meta` — `except Exception as e: logger.debug(...)`. Intentionally broad per module docstring ("fail-soft — list it anyway"), but logs only at debug level — a real bug could go unnoticed in production logs.
- `ingest.py:download_source` — dead code, zero callers; own docstring says "kept for any direct caller/test." Real CLI `pull` composes `download_url`+`ingest_drops` separately.
- `discover.py:discover`/`intake` — zero-caller per call graph; likely a CLI-dispatch-table blind spot, not genuine dead code.
- `transcribe.py:real_transcript_signal` — zero-caller; documented test-support helper.

## C3 — Clip production & framing

- No genuinely dead functions found — every zero-caller flag in this cluster (`compose.py:_moviepy_prepend_render`, `_moviepy_render`, `_probe`) is a default-parameter-value false positive, confirmed real via the call sites that pass them as `render=`/`probe_duration=` defaults.
- No unlogged silent swallow found — all ~15 `except Exception` fail-open sites across `clip.py`, `framing.py`, `stitch_render.py`, `compose.py`, `produce.py` log before continuing (contrast with C2's `vocals.py` and C4's `persona_directives.py`, which don't).
- Cost note (not a bug): every real 2-shot (multi-speaker) clip pays for two independent ffmpeg grid-extraction passes over the same window (`framing._compute_track`, different fps needs) — acknowledged in-docstring as deliberate and bounded.

## C4 — Moments, casting & personas

- `accounts.py:347` `set_backend` — **NOT dead (corrected on validation).** Called via an aliased import (`set_backend as _accounts_set_backend`) at `studio/golive.py:188` and `:506`. The name-based call graph could not resolve the alias.
- `accounts.py:383` `set_channel_routing` — dead code, zero callers (confirmed by alias-and-lazy-import sweep). Notable: own docstring frames it as "the documented fix for the cisumwolfhom incident" (a real production drift bug), never actually wired into any route.
- `accounts.py:469` `ensure_channel` — **NOT dead (corrected on validation).** Called via `ensure_channel as _accounts_ensure_channel` at `studio/golive.py:501` (the discover→adopt flow it was built for).
- `accounts.py:509` `set_status` — **NOT dead (corrected on validation).** Called via `set_status as _accounts_set_status` at `studio/golive.py:543` and `:558` (planned/active handle transitions).
- `accounts.py:553` `set_framing` — dead code, zero callers (confirmed by sweep; sibling `set_clip_profile` IS wired via Studio go-live routes — asymmetric).
- `accounts.py:576` `set_ig_user_id` — **NOT dead (corrected on validation).** Called via `set_ig_user_id as _accounts_set_ig_user_id` at `studio/golive.py:381`.
- `persona_levers.py:87` `is_exempt` — dead code, zero callers (confirmed by sweep).
- `persona_levers.py:107` `channels` — dead code, zero callers; own docstring claim ("the M4 manifest reads it") is inaccurate — `manifest` actually calls `channels_of`.
- `casting.py:40` `_record_fact` — `except Exception: pass`. Deliberate best-effort audit-trail write ("must never lose the casting"), documented.
- `persona_directives.py:287` `persona_facts` — `except Exception: store = None`. **Silently swallows any hashtag-store load error with no logging** — the one unlogged handler in this cluster; every sibling fail-open path logs via `get_logger` first.
- `persona_research.py:56` `discover_corpus` — `except Exception: cands = []`. Documented fail-open.
- `accounts.py:250` `_hydrate_from_personas` — `except Exception: return`. Documented fail-open, leaves inline values untouched.
- Retired-field documentation trap: `persona_directives.py`/`persona_levers.py` still reference 6 fields deliberately removed from `Persona`/`Account` in M3/M3e (`tag_lean`, per-persona `clip_profile`/`framing` pins, 3 freeform directive overrides). A reader grepping "casting_directive" alone could conflate the retired field override with the live compiler function.

## C5 — Caption, hooks & hashtags

- `caption.py:45` `normalize_variation_axis` — dead code; dormant P2 creative-variation-axis machinery, a tracked follow-up.
- `caption.py:51` `coherent_variation` — dead code; T2 coherence gate for the same dormant loop.
- `llm.py:180` `claude_json` — flagged zero-caller by the call graph; **false positive**, actually called via `studio/actions.py:138-139` (`from fanops.llm import claude_json; model = claude_json`), a name-alias the graph missed.
- `prompts.py:166,242,361` (`moment_pick_prompt`, `moment_hook_prompt`, `caption_prompt`) — all flagged zero-caller; false positives, invoked via `responder.py`'s `_PROMPT[kind](payload)` dict-dispatch. (`moment_casting_prompt` removed P11.)
- `hashtags.py:179-193` `vet_hashtags` reserved-floor logic evaluates against `kept[:max_tags]` (the cap window) rather than the full `kept` list — deliberate per inline comment, but subtle enough that a naive re-implementation could silently break the floor guarantee. Design note, not a bug.
- 9 `except Exception` sites across `caption.py`, `fanops_hashtags.py`, `llm.py` — all either log, return a documented fail-open sentinel, or re-raise a typed error. None silent.

## C6 — Crosspost, publish & post

- `post/compress.py:114-131` `persist_post_shrink` — **NOT dead (corrected on validation).** Called via a lazy in-function import at `studio/actions.py:395-396` (`from fanops.post.compress import persist_post_shrink; persist_post_shrink(cfg, led, post_id)`). The name-based call graph cannot see lazy imports — the first pass wrongly labeled this genuinely dead.
- False-positive zero-caller flags (all real, reachable via lazy-import dict-of-lambdas the AST tool can't trace): `post/providers.py:_postiz_poster`, `_zernio_poster`, `_dryrun_poster`, `_postiz_uploader`, `_zernio_uploader`; `post/postiz.py:postiz_upload_media`; `post/zernio.py:zernio_upload_media`; `post/run.py:reset_publish_throttle` (own docstring: "test-only").
- `crosspost.py:28-29` — the `_JITTER_MAX < _STEP_MIN` monotonicity invariant is enforced **only by a code comment, not a runtime assertion**. A future edit to either constant without re-reading the comment would silently break monotonic scheduling. Low risk today (hardcoded literals); a module-level `assert` would make it self-enforcing.
- `post/dryrun.py:DryRunPoster.publish` — effectively dead in the current call graph but intentionally retained as the `Poster`-protocol fallback; post-M1, `publish_due`/`publish_post` call `write_preview` directly and never construct a `DryRunPoster`.
- `post/postiz.py:73-86` `_postiz_permalink` — **always returns `None` by design**. The `submitted → published` promotion in `_publish_one` can therefore never fire for a fresh Postiz publish inside `_publish_one` alone — it necessarily waits for `reconcile.py` to backfill the URL later. A real, intentional two-phase-commit-style dependency, flagged for visibility, not a bug.
- `post/run.py:_publish_throttle_last` — a plain module-level dict, the one piece of true global mutable state in this cluster. In-process-only by design; would need revisiting if `fanops` ever ran as multiple concurrent processes.
- No bare `except:` and no untraced `except Exception: pass` anywhere in the 17 files — every broad except logs, sets a typed reason, or is a documented best-effort decoration.
- `responder.py:131,133` `_answer_one` — **SILENT INFINITE-PENDING GATE (new class, was mis-blessed).** The `ValidationError` and generic-`Exception` branches log and leave the gate pending — no `degraded_reason`, no source mark, no ceiling. A DETERMINISTIC gate failure (prose-not-JSON picker answer) is therefore re-requested forever, invisible in status/digest/Studio (the reducer stages' `_quarantine` terminal path does NOT reach the responder gate path). Distinct from an 'unlogged swallow' (this LOGS) — the taxonomy blind spot was conflating logged with surfaced. Parked `src_90d3c565022f` at `moments_requested`. Fixed by W1–W3; guarded by `test_responder.py` (W5b) + `test_llm.py` (W5a/W5c/W5d). See MOL-221 (parent) / MOL-254 (this entry).

## C7 — Metrics, reconcile & learning

- `learn_doctor.py:70-80` `load_verdict` — zero in-repo callers; either dead or M4 reads the sidecar file directly, bypassing it. Candidate for removal or wiring into `doctor_report`.
- `timing_bias.py:113-122` `timing_prior_hour` — docstring claims "the schedule seam calls this" but zero in-repo callers found; likely orphaned, worth a repo-wide grep to confirm.
- `track.py:291-294` `pull_metrics` — broad `except Exception` around the `resolve_media` call; intentional fail-open with a logged breadcrumb, but a genuine bug inside `resolve_media_ids` would be masked as a soft skip rather than surfacing.
- `variant_amplify.py:177`, `p4_dim_bias.py:70,79`, `moment_hook_learning.py:47` — broad outer-guard excepts in every bias actuator; all deliberately broad ("fail-safe, not fail-silent"), all log before swallowing.
- `meta_graph.py:337-338` `_read_queries` — `except (OSError, JSONDecodeError, ValueError, TypeError): return None` with **no logging**, unlike sibling `insights_blocked_signal` which does log. A corrupt hashtag-budget file is undetectable from the log stream (fail-closed direction is safe, but silent).
- Asymmetric daemon-tick coverage: `reconcile.resolve_media_ids` runs automatically inside `track.pull_metrics`, but its documented "inverse" sibling `reconcile.project_imported_media` is called only from the manual `cli.cmd_map_media` CLI verb. May be an intentional scope choice or a gap — worth confirming with the author.
- No bias-scope violations found; no TODO/FIXME/XXX anywhere in the cluster.

## C8 — Ops, CLI & daemon

- `timeutil.py:70` `is_past_due` — dead code; sibling `is_due_or_past` is used instead.
- `timeutil.py:110,124` `to_local_display`/`to_local_input` — flagged zero-caller; likely Jinja-filter registration blind spots (not verified dead).
- `cli.py:522` `_http_url` — flagged zero-caller; registered as an argparse `type=` callback, confirmed NOT dead.
- `audit.py:29-47` `write_audit` — entire body wrapped in a top-level `except Exception: pass`, the broadest swallow in the cluster, but explicitly contracted: "the action must complete even if the audit write fails (audit is observability, never a blocker)."
- `audit.py:59-60` `read_audit_tail` — same "never raises" contract.
- `cutover.py:30-31` `_load_state` — "corrupt scratch file → start clean, never crash."
- `daemon.py:134-135` `installed_interval` — "a corrupt plist must never crash `daemon status`."
- `doctor.py:74-75` — **the one place in `doctor.py` where a real config error could silently under-report a genuinely half-live/broken state**, since the fallback is "everything's fine" (`half_live = False`) rather than "flag it." Noted as low risk since the accounts-validity check above would likely already have caught the underlying corruption.
- No TODO/FIXME/XXX anywhere. No bare `except:` anywhere. No HIGH-severity live/destructive CLI verb found without a confirmation gate (`cutover post` is triple-gated; `gc` refuses `--keep-days < 1`).

## C9 — Studio backend (Flask app layer)

- `app_routes_live.py:29-34` `do_wipe_confirm` — **no server-side check that `do_wipe_preview` ran first**; "preview before confirm" is enforced only by the template hiding the confirm form, not by the server. Does not bypass the typed-word/snapshot/restorability code gates, but means "operator sees preview before confirming" is a UI convention, not a server-enforced invariant.
- `golive.py:452-478` `discover_channels` — an unsupported platform is silently downgraded to a note rather than surfaced as an error. Documented fail-soft, produces a silently-smaller `channels` list with only a best-effort textual note as the trail.
- `preview_media.py:31-32,36-38` — **two bare `except Exception: pass` blocks with zero logging** in the WYSIWYG preview resolution ladder — the one place in the cluster where an exception is swallowed with zero logging at all. Low severity (read-only), but a silently-failing `render_account_file` call here means Review could keep showing a stale/wrong preview with no trail to debug why.
- `app.py:158-160` `_account_arg` — `except Exception: pass` around handle resolution. Intentional/low-risk, but same unlogged-swallow pattern as `preview_media.py` — this cluster logs its swallows in the mutation layer but not consistently in the read-helper layer.
- `golive.py:652-657` — CLAUDE.md's claim that `FANOPS_POSTER=postiz` is set through the Go-Live path is stale relative to current code (`go_live` explicitly never writes `FANOPS_POSTER`, per its own comment "D12: go_live NEVER writes FANOPS_POSTER"). The real live-routing setter is `set_account_backend`. Documentation/reality drift, not a code defect — the underlying safety property still holds under the current per-channel design.
- No TODO/FIXME found. No dead/unreachable functions found — every public function in this cluster has at least one route caller.

## C10 — Studio views

- `views.py:173` `run_next_step`, `:528` `zero_post_clips`, `:547` `metrics_stale_hint` — zero-caller flags. `zero_post_clips` confirmed as a **real bug** (see full-trace-index.md "Real bugs found" — referenced by `home.html` but never passed by the view). The other two likely template-only, not separately confirmed.
- `views_common.py:74` `accounts_in`, `:68` `term_def` — zero-caller flags; `term_def` explicitly documented as a Jinja-macro-only consumer.
- `views_results.py:422` `operator_error`, `:621` `bar_pct` — zero-caller flags; plausibly template-only (`bar_pct` almost certainly is), `operator_error` possibly superseded by `failure_label`.
- `views_review.py:162` `provenance_chips`, `:663` `group_review_by_account_surface`, `:684` `group_review_by_batch` — zero-caller flags; `provenance_chips` documented template-macro-consumed. The two groupers' docstrings claim mutual mirroring but neither has a confirmed live caller — worth verifying whether the batch-grouped display was refactored away, leaving these genuinely dead.
- `call_graph.json` lists `fanops.compose._moviepy_render` as a caller of `views_review._card` — almost certainly a name-collision false positive (no plausible reason `compose.py` would call a Studio Review-card builder).
- **`views.py:build_system_strip`** — runs on every page load; 4 of its 5 internal try/except blocks are silent (no `get_logger` call): `pipeline_status` failure → `blocked=0`; posts-scan failure → `failed=0`; `insights_blocked` failure → `False`; `half_live` computation failure → `False,""`. Sibling read-models earlier in the same file (`asset_catalog`, `golive_accounts`, `home_status`) all log on failure. **The one real legibility gap in C10** — a persistent bug in any of the 5 sub-computations would degrade the nav strip's warning badges invisibly, with no log breadcrumb. (The 5th, `postiz_down`, is silent too but is an acceptable double-guard since `postiz_health_for_banner` is already internally fail-open.)
- `views.py:523,585` (`review_handoff`, `account_work_counts`) — undocumented-as-logged `except Exception: pass`, each with a sensible partial-result fallback.
- `views_results.py:605` `lineage_stats` — silent except, but only risks leaving additive fields at `None` defaults. Separately, this function **mutates its own argument objects in place** (`r.sibling_count = n`, etc.) — the one function in the cluster doing in-place mutation, violating the project's stated immutability coding-style rule (never a ledger/control-file mutation, so not a safety issue — a style follow-up).

## Summary counts

Counts reflect the post-validation corrections (5 functions moved from "dead" to "false positive"
after an alias-and-lazy-import sweep — see the note below the table).

| Category | Count |
|---|---|
| Confirmed dead code (real, re-verified by source sweep) | 10 |
| Confirmed false-positive dead-code flags (dispatch-table / default-param / Jinja-filter / **aliased-or-lazy-import** blind spots) | 16 |
| Plausible-but-unconfirmed (likely template-only) | 10 |
| Unlogged silent exception swallows | 9 |
| Real logic/wiring bugs | 1 (`views.zero_post_clips`) |
| Documentation staleness items | 1 (Go-Live `FANOPS_POSTER` claim in CLAUDE.md) |
| Design notes flagged for visibility (not bugs) | 3 (hashtag cap-window floor logic, `_JITTER_MAX` comment-only invariant, `_postiz_permalink` two-phase-commit dependency) |
| Safety-critical invariants checked | 10 — **"all HOLD" as of 2026-07-11; SUPERSEDED** — wipe-confirmation did *not* hold (RC-4/RC-5 restore race, fixed #653/#654). See the correction above; current verdict lives in `tools/arch` + `INVARIANT_AUDIT.md` |

**C1 re-verification deltas (2026-07-14), not yet propagated into the whole-doc totals above (C2–C10 unverified):** the "Unlogged silent exception swallows: 9" total counted `ledger_wipe.py:188` — it now logs (`:234`), but via **stdlib `logging`, not the surfaced `get_logger`/`run.log` stream**, so by this file's own `logging ≠ surfacing` axis it is not truly "0" — reclassify as **logged-but-mis-surfaced**, not resolved. The "dead code" and "documentation staleness" rows each counted a C1 entry that is now false/fixed (`RenderState.queued/published/analyzed` are live at `views_results.py:112`; `ledger.py:2` docstring is accurate). Treat the table as a 2026-07-11 snapshot; only C1 has been re-verified.

**Cross-cluster fix-QUALITY spot-check (2026-07-14) — separate from staleness: does a "now logs" fix log the LEAST-DEFECTIVE way?** Four previously-flagged silent sites were re-read against a 3-part bar (surfaced channel? catch only the real failure? level alerting sees?). Result is uneven, and inversely to risk: **`build_system_strip` (C10 `views.py:733`) — well-fixed** (all 5 sub-reads log via `get_logger`, documented MOL-123). **`meta_graph._read_queries` (C7 `:485`) — best** (surfaced + NARROW catch `(OSError, JSONDecodeError, ValueError, TypeError)`). **`persona_directives.persona_facts` (C4 `:309`) — adequate** (surfaced; broad catch, low stakes). **`ledger_wipe.snapshot_is_restorable` (C1 `:234`) — WEAKEST, on the most dangerous path** (stdlib not surfaced; bare `except Exception`; `warning` on a wipe-guard). The pattern: cosmetic paths got the cleanest fixes, the destructive-wipe path got the sloppiest. C2/C3/C5/C6/C8/C9 fix-quality NOT yet audited.

**Validation correction (2026-07-03):** the deterministic call graph is name-based and cannot
resolve aliased imports (`from x import f as _y` then `_y(...)`) or lazy in-function imports. The
first-pass trace trusted `called_by=[]` for these cases and wrongly labeled 5 LIVE functions as
genuinely dead: `persist_post_shrink` (lazy import at `studio/actions.py:396`) and `set_backend` /
`ensure_channel` / `set_status` / `set_ig_user_id` (aliased `_accounts_*` imports in
`studio/golive.py`). All are corrected above. The remaining 10 dead-code entries were re-verified
by grepping for both the bare name and every `<name> as <alias>` binding across the tree.
