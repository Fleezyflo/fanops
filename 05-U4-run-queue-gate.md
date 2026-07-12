# U4 — Add & Run: explicit queue (pending → tick accounts → add to queue → make clips)

**One PR. The deepest behavioral change in the program — read the red flag first.**

Operator brief §Add and Run: *"When I choose a file, or a file lands in the inbox folder, it appears here as pending (not yet queued or processed). I add pending items to the queue stage, tick the accounts I want to process them for, then click 'add to queue.' … I can then click 'make clips' on the entire queue, or on a single line."* Plus: *"'review' and 'learn' should not live on this page."*

---

## Red flag — this deliberately ends hands-off auto-processing for new footage

Today `pipeline.advance()` ingests the whole inbox **first thing every daemon tick** (`pipeline.py:478` → `ingest_staged`), and `ingest_staged` auto-batches every new drop via `resolve_or_mint_drop_batch` (`ingest.py:407–411`) with `target_accounts=[]` (all-active). `_run_panel.html` still says a Finder drop is picked up on the next Make press (`_run_panel.html:4–5, :93`). A drop is therefore processed with **no** account targeting and **no** operator step.

The brief reverses that: **nothing processes until you tick accounts, queue it, and click Make clips.** After U4, drop-and-walk-away stops producing clips — the file waits in *Pending* until released. The daemon still advances everything already released (`catalogued`+); schedule, publish, and metrics are untouched.

**Escape hatch:** `FANOPS_QUEUE_GATE=0` restores today's auto-flow byte-identically (catalogued birth, auto-batch, at-upload targeting, `kick_prepare` on ingest).

---

## What exists (anchors @ workspace HEAD)

| Symbol | Site | Role |
|---|---|---|
| `SourceState` | `models.py:61` | gains `pending` (pre-pipeline hold) |
| Birth state | `ingest._mint_candidate` `:321` | **the single birth hook** — today always `SourceState.catalogued` |
| Auto-batch | `ingest.ingest_staged` `:407–411` | today mints `drop-YYYY-MM-DD` when `batch_id` absent |
| Daemon ingest | `pipeline.advance` `:478–480` | every tick: stage → mint → archive |
| `run_ingest` | `actions_run.py:52` | today: catalogue + optional named batch + `kick_prepare` |
| Intake chain | `save_uploads_and_ingest` `:301`, `upload_finalize` `:211`, `run_pull` `:95` | today chain catalogue + batch + kick |
| `kick_prepare` | `actions_run.py:27` | debounced by `run_held` / run lease |
| `Batch` + `Source.batch_id` | `models.py:481` / `:183` | queue line = one Batch (sources × `target_accounts`) |
| `run_prepare` | `actions_run.py:398` | Make-clips loop (gates + advance) |
| Stage selectors | `pipeline._stage_source_to_moments` `:161` etc. | iterate **specific** post-`catalogued` states only |
| `produce.run_all` | `produce.py:130` | warms transcribe only when `state is catalogued` (`:68`) — pending is a no-op |
| Backlog bucket | `pipeline_status._source_bucket` `:134` | pending today would fall through to `actionable` — **must fix** |
| Run idle label | `_run_panel.html:153`, `test_truth_surfaces.py:131` | S10 fix 2: "in progress" only when `run_chip` (lease) set |
| `_run_panel.html` | 230 lines | rebuild surface: Add → Queue → Make; drop Review/Learn cards |

---

## Exact minimal design

### State model (the whole trick is one new state)

- `SourceState.pending` — catalogued-on-arrival (id, sha, duration, thumbnail all real) but untouched by every pipeline stage.
- *Pending, unbound* = `state=pending`, `batch_id=None` (just arrived).
- *Queued, held* = `state=pending`, `batch_id=<batch>` (accounts ticked, **Add to queue** clicked).
- *Released* = `state=catalogued` (**Make clips** clicked) — from here the existing pipeline owns it, unchanged.

**Why no stage edits:** every reducer stage keys on `catalogued` / `transcribed` / … (`pipeline.py:161+`). `produce.run_all` only warms transcribe at `catalogued` (`produce.py:68`). `pending` is invisible to them **by construction**.

### Two surgical backend hooks (everything else hangs off these)

1. **`ingest._mint_candidate`** — when `cfg.queue_gate`: mint `SourceState.pending`, `batch_id=None` (ignore any staged `batch_id` on birth). When gate OFF: today's `SourceState.catalogued` path unchanged.
2. **`ingest.ingest_staged`** — when `cfg.queue_gate`: **skip** `resolve_or_mint_drop_batch` (`:407–411`); never stamp `batch_id` on birth. When gate OFF: today's auto-batch byte-identical.

All intake surfaces (`advance`, `run_ingest`, `run_pull`, CLI `ingest`/`pull`, upload finalize) already funnel through these two functions — **do not fork birth logic per caller.**

### `cfg.queue_gate` (new)

- Property on `Config` (`config.py`, same shape as `smart_framing`: default **ON**, off-words `0/false/no/off`).
- `FANOPS_QUEUE_GATE=0` → gate OFF.
- Document in `docs/CONFIG.md` (Pipeline: ingest section, `.env`/shell-only).
- Add `FANOPS_QUEUE_GATE` to `tests/conftest.py` `_LEAKY_ENV`.

### Operator verbs (gate ON only — gate OFF keeps today's `run_ingest` contract)

| Verb | Implementation | Notes |
|---|---|---|
| **Catalogue** (implicit) | existing `stage_inbox_candidates` + `ingest_staged` | Upload/link/drop/Finder/daemon-tick; births `pending`; **no** `kick_prepare` |
| **Add to queue** | new `bind_queue(cfg, *, source_ids, batch_name, target_accounts, burn_subs)` in `actions_run.py` | One `Ledger.transaction`: `create_batch` when `source_ids` non-empty; stamp `batch_id` on selected unbound pending sources only; echo batch + accounts. Repeatable → multiple open batch lines. |
| **Make clips** (per line) | new `release_batch(cfg, batch_id, *, confirmed)` | Flip that batch's held `pending→catalogued`; `kick_prepare(cfg)` when ≥1 released. |
| **Make clips** (all) | new `release_all_held(cfg, *, confirmed)` | Flip every held pending source; one kick. |
| **Make clips** (pipeline) | existing `run_prepare` | Unchanged; operates on `catalogued`+ only. Live confirm ladder preserved. |

Gate OFF: `run_ingest` / `save_uploads_and_ingest` / `upload_finalize` / `do_run_ingest` route stay byte-identical (catalogued birth, optional batch at upload, `kick_prepare` on add).

Gate ON intake decoupling:
- Upload form sheds `batch_name` / `target_accounts` (file picker + skip-subtitles stay).
- `save_uploads_and_ingest` → `save_uploads` then thin `catalogue_inbox` (stage+ingest, no batch, no kick) — **not** `run_ingest`.
- `upload_finalize`: `trigger_ingest=True` chains `catalogue_inbox`, not `run_ingest`.
- `run_pull`: catalogue-as-pending, no kick.
- Remove misleading copy: *"the next Make press picks it up"* (`_run_panel.html:93`) → pending/queue wording.

### Views / backlog (required for truthful UI — not optional polish)

`pipeline_status` (`views.py:163`) must expose:
- `pending_unbound` — count + rows (name, duration, thumb path/id) for the Pending panel.
- `queue_lines` — one dict per open `Batch` that still has ≥1 held pending source: `{batch_id, name, sources, target_accounts, burn_subs}`.
- `held_pending` — total held count (queue meter).

`pipeline_status._source_bucket` (`pipeline_status.py:134`): classify `SourceState.pending` as bucket **`held`** (not `actionable`). Held pending must **not** inflate `status.sources` (the in-pipeline actionable count used by the Make meter). Released `catalogued`+ sources keep today's bucket rules.

`run_next_step` (`views.py:235`): when gate ON, ladder is **add → queue → make** (not add → gate → review on this page). Gate/review steps remain on their own tabs; the Make banner nudges queue/release, not Review.

### Page layout (`_run_panel.html`)

1. **Stage rail:** Add → Queue → Make (drop Review/Learn steps).
2. **① Add:** upload + link only (no batch/target fields). Copy: files land as *pending*.
3. **Pending panel:** unbound sources; multi-select + account tick-boxes with explicit copy (*"ticking queues clipping for these accounts"*) → **Add to queue** → `bind_queue`.
4. **② Queue:** one line per `queue_lines` entry; per-line **Make clips** → `release_batch`; queue-wide **Make clips** → `release_all_held`. Existing live ⚠ confirm on the prepare/release path that triggers `run_prepare`.
5. **③ Make:** existing prepare form + queue meter (released/in-flight only for "in progress" label).
6. **Errored card:** **stays** (MOL-123; U5 library links here).
7. **Review card (03) + Learn card (04):** **removed** (Review → `/review`; metrics → Results/daemon).
8. **S10 fix 2:** carry verbatim — `{% if status.run_chip %}in progress{% else %}queued{% endif %}` (`_run_panel.html:153`); pinned by `test_truth_surfaces.py::test_run_idle_shows_queued_not_in_progress`. Held pending without a lease reads *queued/waiting*, never actively processing.

### Grandfathering

Existing sources are already `catalogued`+ — untouched. No ledger migration; no `SCHEMA_VERSION` bump (enum addition only). Old ledgers load unchanged.

---

## Non-goals (binding)

- No change to any pipeline stage reducer (`moments` / `clip` / `caption` / `crosspost` / `publish`).
- No `produce.run_all` filter (pending is already a transcribe no-op; optional perf skip is out of scope).
- No queue reordering / priority / per-source line-splitting inside a batch.
- No removal of Finder-drop or link-pull channels (they birth `pending`, that's all).
- No third-party path change (`origin_kind=third_party` stays inert; birth state irrelevant).
- No daemon cadence change.
- No new durable queue table — `Batch` + `Source.batch_id` + `SourceState.pending` is the queue.

---

## File manifest (ordered, minimal)

| File | Change |
|---|---|
| `src/fanops/models.py` | `SourceState.pending` |
| `src/fanops/config.py` | `queue_gate` property |
| `src/fanops/ingest.py` | `_mint_candidate` birth; `ingest_staged` auto-batch guard |
| `src/fanops/studio/actions_run.py` | `catalogue_inbox`, `bind_queue`, `release_batch`, `release_all_held`; gate-aware upload/pull/ingest; kick only on release |
| `src/fanops/pipeline_status.py` | `held` bucket for `pending` |
| `src/fanops/studio/views.py` | `pending_unbound`, `queue_lines`, `held_pending`; gate-aware `run_next_step` |
| `src/fanops/studio/app_routes_run.py` | routes: `do_bind_queue`, `do_release_batch`, `do_release_all`; upload/pull route threading |
| `src/fanops/studio/templates/_run_panel.html` | 3-stage console rebuild |
| `src/fanops/studio/templates/_run_next.html` | queue-step hints when gate ON |
| `docs/CONFIG.md` | `FANOPS_QUEUE_GATE` row |
| `tests/conftest.py` | `_LEAKY_ENV` entry |
| `tests/test_queue_gate.py` | **new** — core gate contract |
| `tests/test_truth_surfaces.py` | adjust only if meter selectors change; S10 idle-label tests must stay green |
| `tests/test_studio_run.py` | gate-OFF byte-identity: existing `run_ingest` tests pass with `FANOPS_QUEUE_GATE=0` (explicit in test or autouse fixture for that module's ingest suite) |

**Hot-file note:** touches `models.py` — one ticket, one branch; no parallel lane on `crosspost.py` / `ledger.py`.

---

## Acceptance (binary)

1. Gate ON: file in `01_inbox` → after `advance()` ingest phase, source is `pending` / `batch_id=None`; a second full `advance()` produces **no** transcript / moments / clips artifacts; state unchanged.
2. Gate ON: upload/link catalogues as `pending` without waiting for daemon tick.
3. Tick two accounts → Add to queue → one queue line shows source×accounts; second add with different accounts → second line.
4. Per-line Make releases **only** that batch's sources (`pending→catalogued`); other held line stays `pending` after `advance()`.
5. Queue-wide Make releases all held sources.
6. Gate OFF: `tests/test_studio_run.py` ingest suite + `tests/test_ingest_auto_batch.py` pass byte-identical (birth `catalogued`, auto-batch, kick on ingest).
7. Page: no Review/Learn cards; errored card still renders; rail is Add → Queue → Make.
8. `crosspost_clips` per-surface SKIP tests stay green (batch targeting unchanged downstream).
9. S10: with no run lease, no element reads "in progress" for held or released-but-waiting sources; with lease + `run_chip`, active label renders.
10. `status.sources` (actionable) excludes held `pending`; queue meter shows held count separately.

---

## Tests

### `tests/test_queue_gate.py` (new)

- `test_advance_holds_unbound_pending` — gate ON, inbox drop, `advance()`, assert `pending`, no transcript file, state unchanged on second `advance()`.
- `test_bind_queue_stamps_batch` — two accounts, assert `batch_id` + `Batch.target_accounts`.
- `test_two_binds_two_lines` — different account ticks → two batches.
- `test_release_batch_only_that_line` — per-line release + `advance()` isolation.
- `test_release_all_held` — queue-wide release.
- `test_gate_off_byte_identical_birth_and_autobatch` — `FANOPS_QUEUE_GATE=0`, mirrors `test_run_ingest_blank_batch_name_falls_back_to_drop_batch`.
- `test_grandfather_catalogued_untouched` — pre-seeded `catalogued` source unaffected when gate ON.

### Existing suites (must stay green)

- `tests/test_account_first_e2e.py` — batch targeting (gate OFF or released-before-crosspost setup).
- `tests/test_truth_surfaces.py` — S10 idle/active run labels.
- `tests/test_studio_run.py` — gate OFF ingest/kick contract.

---

## Implementation order (TDD)

1. **RED** — `tests/test_queue_gate.py` failures for hold/bind/release/gate-off.
2. **GREEN** — `models` + `config` + `ingest` hooks (smallest backend slice).
3. **GREEN** — `actions_run` verbs + kick timing.
4. **GREEN** — `pipeline_status` held bucket + `views` projections.
5. **GREEN** — routes + `_run_panel.html` / `_run_next.html`.
6. **Verify** — `./scripts/check.sh`; gate-OFF regression on `test_studio_run.py` + `test_ingest_auto_batch.py`.

---

## Risks / notes

- **Lane:** `models.py` is hot — serial merge; no sibling PR touching it.
- **Operator surprise:** default ON ends drop-and-walk-away; red flag + `FANOPS_QUEUE_GATE=0` documented up front.
- **CLI parity:** `fanops ingest` / `fanops pull` inherit gate via `_mint_candidate` — no separate CLI fork.
- **S10 overlap:** if S10 merges first, import its idle-label rule verbatim; if U4 lands first, S10 fix 2 drops from S10 residual scope.
