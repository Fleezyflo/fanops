# FanOps — Execution Paths (W1–W10)

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73`** · Machine-readable twin: [`workflows.json`](workflows.json)

What the system **actually does at runtime**, in order, with every lock, every side effect, every durability
point, and what survives a crash at each one. Companion documents:
[`FAILURE_SEMANTICS.md`](FAILURE_SEMANTICS.md) · [`CRASH_RECOVERY.md`](CRASH_RECOVERY.md) ·
[`OBSERVABILITY.md`](OBSERVABILITY.md) · [`SIDE_EFFECT_GRAPH.md`](SIDE_EFFECT_GRAPH.md) ·
[`CYCLE3_CORRECTIONS.md`](CYCLE3_CORRECTIONS.md).

**Line references in this document were read at HEAD `fcffa73` and are current.** Cycle-2 `INV-20` established
that every `CLAUDE.md` citation is stale; do not cross-reference them.

---

## 0. The five-phase shape every pass has

`advance()` ([pipeline.py:466-546](src/fanops/pipeline.py:466)) is the whole engine. Everything else is an
entry point into it. Its shape is the single most important runtime fact in the system:

```
 ┌─ LOCK-FREE ────────────────────────────────────────────────────────────────┐
 │ 1. stage_inbox_candidates(cfg)          hash + copy + ffprobe   pipeline:478│
 └────────────────────────────────────────────────────────────────────────────┘
 ┌─ TXN #1 (short) ───────────────────────────────────────────────────────────┐
 │ 2. ingest_staged(led, cfg, staged)      mint Source rows        pipeline:480│
 └────────────────────────────────────────────────────────────────────────────┘
 │ 3. _archive_staged(cfg, staged)         AFTER commit            pipeline:481│
 ┌─ LOCK-FREE PRODUCER ───────────────────────────────────────────────────────┐
 │ 4. produce.run_all(cfg, aspects, log)   whisper / ffmpeg / renders          │
 │                                         warms artifacts, SAVES NOTHING      │
 └────────────────────────────────────────────────────────────────────────────┘
 ┌─ TXN #2 (the main reduce) ─────────────────────────────────────────────────┐
 │ 5. heal_corrupt_gates → reconcile_source_progress → snapshot `before`       │
 │    → source_to_moments → ingest_moments → moment_hooks → hookscore          │
 │    → [router] → render_and_caption → structural_hooks                       │
 │    → refresh_caption_requests → ingest_captions → crosspost                 │
 │                                                 pipeline:504-541             │
 │  ⚠ an UNCAUGHT raise here rolls back the ENTIRE pass, BY DESIGN (:495-503)  │
 └────────────────────────────────────────────────────────────────────────────┘
 ┌─ OUT OF LOCK ──────────────────────────────────────────────────────────────┐
 │ 6. _reconcile_safe(cfg, log)   GATED on cfg.is_live_backend     pipeline:318│
 │ 7. _publish_safe(cfg, log)     ** NOT GATED **                  pipeline:334│
 │ 8. _build_summary(cfg, before) read-only reload + write_digest  pipeline:546│
 └────────────────────────────────────────────────────────────────────────────┘
```

**Three structural facts fall straight out of this diagram:**

1. **The heavy work is warmed lock-free and the reduce only flips state.** A rolled-back pass therefore loses
   only cheap in-memory transitions; the next pass fingerprint-skips onto the warm artifacts. This is good
   design and is pinned by `test_advance_rollback_recovers_warm_artifacts`.
2. **Step 6 is gated and step 7 is not.** Reconcile — the *only* reader of `submitting` — can be switched off
   while publish keeps claiming posts into `submitting`. This is the mechanism behind Cycle-2 `F-A` and is
   **proven by EXP-11**.
3. **Publishing is outside the lock and owns its own per-post locking.** Network I/O never holds the ledger
   flock.

---

## W1 — Daemon tick

**Trigger.** `launchd` `com.fanops.run` (`RunAtLoad` + `KeepAlive`; interval rides
`EnvironmentVariables.FANOPS_DAEMON_INTERVAL`, [daemon.py:151](src/fanops/daemon.py:151)). There is **no
`StartInterval`**. A sibling `com.fanops.keeper` polls every 120 s.

| # | Step | `file:line` | Lock | Effects |
|---|---|---|---|---|
| W1.1 | `main()` → `load_dotenv(root/.env, override=True)` | [cli.py:795](src/fanops/cli.py:795) | — | **env write** |
| W1.2 | `Config(root)` | [config.py:144](src/fanops/config.py:144) | — | none (side-effect-free; **74 uncached `os.getenv`**) |
| W1.3 | **loop top:** `load_dotenv(override=True)` + `cfg = Config(cfg.root)` | [cli.py:1303-1304](src/fanops/cli.py:1303) | — | **env write; re-read EVERY tick** |
| W1.4 | `_cmd_run_pass(cfg, base_time)` | [cli.py:931](src/fanops/cli.py:931) | **`run_lease`** (flock `LOCK_NB` on `.run.lock`) | — |
| W1.5 | ↳ `for _ in range(10):` converge loop | [cli.py:943](src/fanops/cli.py:943) | run lease held | — |
| W1.6 | ↳ `get_responder(cfg).answer_pending(cfg)` | [cli.py:945](src/fanops/cli.py:945) | run lease | **subprocess `claude -p`**, fs writes (gate responses) |
| W1.7 | ↳ `advance(cfg, base_time=…)` | [cli.py:946](src/fanops/cli.py:946) | run lease + ledger txns | everything (see §0) |
| W1.8 | ↳ `except Exception: print("run halted: …"); return None` | [cli.py:947-949](src/fanops/cli.py:947) | — | **swallows AuthError** |
| W1.9 | `_learn_pass(cfg)` — gated on `cfg.is_live_backend` | [cli.py:965](src/fanops/cli.py:965) | own txn | **network** (metrics), **irreversible `retire`** |
| W1.10 | `_heartbeat(cfg, s, origin="loop")` — **only if `s is not None`** | [cli.py:1306](src/fanops/cli.py:1306) | — | stdout + `run.log` |
| W1.11 | `except Exception: print("run halted: …")` | [cli.py:1311-1312](src/fanops/cli.py:1311) | — | **swallow; loop continues** |
| W1.12 | `time.sleep(interval)` | [cli.py:1313](src/fanops/cli.py:1313) | — | no jitter, no backoff |

**Config refreshed every tick:** `.env` → `os.environ` → a **new `Config`**. `Config` properties are
`os.getenv` per access, uncached — so a Studio `go_live` reaches the daemon within one tick.
**Process-local state that stays stale:** `run._publish_throttle_last`
([run.py:123](src/fanops/post/run.py:123)) — in-process only; and `pipeline_run._note_stage_warned`
([pipeline_run.py:17](src/fanops/pipeline_run.py:17)) — a warn-once latch.

**Shutdown / exception behaviour.** There is **no signal handler**. `kill -9` is safe by construction: the
`run_lease` flock and every ledger flock are kernel-released on process death, and the SQLite WAL rolls back an
open transaction. **A persistently failing pass (rotated key → `AuthError`) is re-driven every tick forever,
writes no heartbeat, and changes no ledger state** — see `C3-F8`.

---

## W2 — Source ingestion → catalogued Source

| # | Step | `file:line` | Lock | Durability |
|---|---|---|---|---|
| W2.1 | `stage_inbox_candidates(cfg)` — rglob `01_inbox` | [pipeline.py:478](src/fanops/pipeline.py:478) | **none** | — |
| W2.2 | sha256 of the file → content-addressed `src_<sha1[:12]>` | [ids.py:7](src/fanops/ids.py:7) | none | — |
| W2.3 | `ffprobe` the media | ingest.py | none | **subprocess** |
| W2.4 | copy/stage into the pipeline dir | ingest.py | none | **fs write** |
| W2.5 | `ingest_staged` → `add_source` (`setdefault`) | [pipeline.py:480](src/fanops/pipeline.py:480), [ledger.py:559](src/fanops/ledger.py:559) | **ledger txn** | **commit** |
| W2.6 | `_archive_staged(cfg, staged)` | [pipeline.py:481](src/fanops/pipeline.py:481) | **none — AFTER commit** | fs move |

**Birth state** = `pending` when `cfg.queue_gate` is ON (**default ON**), else `catalogued`
([ingest.py:321-323](src/fanops/ingest.py:321)). A `pending` source is **invisible to every pipeline reducer**
— they key on `catalogued`+. This is the U4 queue gate and it is **by design**, not a stall.

**Idempotency: TRUE, by content address.** Re-ingesting the same bytes yields the same `src_<sha>`;
`add_source` is `setdefault` → a replay is a **no-op**. `Ledger.already_seen(sha256=…)`
([ledger.py:~601](src/fanops/ledger.py:601)) is the explicit dedup read.

**Crash points.** Between W2.4 and W2.5 → a staged file exists with **no ledger row**. Recovery:
`rebuild_catalog` ([ledger.py:746-758](src/fanops/ledger.py:746)) adopts orphans matching
`_SID_RE = ^src_[0-9a-f]{12}$` as `SourceState.discovered` — **inert until an operator promotes them**
(`promote_source`, [pipeline.py:125](src/fanops/pipeline.py:125)). Between W2.5 and W2.6 → the row exists and
the inbox copy is not archived; the next `stage_inbox_candidates` re-stages it, `add_source` no-ops. **Safe.**

---

## W3 — Source analysis → moments (the agent-gate machine)

The **only queue in the system is a filesystem queue**: `04_agent_io/requests/{kind}__{key}.request.json`
paired with `.response.json`, correlated by a stamped `request_id`.

```
catalogued ──transcribe_source (in_lock=True: ADOPT-OR-DEFER)──> transcribed
transcribed ──detect_signals   (in_lock=True: ADOPT-OR-DEFER)──> signalled
signalled ──request_moments───> moments_requested   [writes gate: moments__<sid>]
                                        │
                     responder.answer_pending  (subprocess `claude -p`)
                                        │
moments_requested ──ingest_moments────> picks_decided   (Moment born `picked`)
picks_decided ──request_moment_hooks──> [gate: moment_hooks__<sid>.<token>]  (one PER PICK)
              ──ingest_moment_hooks───> moments_decided  (ATOMIC-PER-SOURCE: `if dec is None: return led`)
```

**`in_lock=True` is the load-bearing detail.** `transcribe_source` and `detect_signals` **never shell whisper
or ffmpeg under the flock** ([pipeline.py:162,164](src/fanops/pipeline.py:162)) — they *adopt* a warm artifact
or *defer* one tick. The lock-free `produce.run_all` pass warms it. A cold cache costs one tick of latency,
never a held lock.

**Gate writes are ATOMIC** — `write_request` and `write_response` both use tmp + `os.replace`
([agentstep.py:63-66](src/fanops/agentstep.py:63), [:82-87](src/fanops/agentstep.py:82)). **`bump_attempts` is
NOT** ([agentstep.py:147](src/fanops/agentstep.py:147)) — see `C3-F6`.

**Stale-answer guard.** `read_response` compares the response's `request_id` to `latest_request_id`; a
mismatch → `None` ([agentstep.py:101-102](src/fanops/agentstep.py:101)). A re-written request invalidates any
prior response on disk ([:70-72](src/fanops/agentstep.py:70)).

**Escalation (live).** `_on_deterministic_fail` burns a per-gate counter; at `_GATE_DETERMINISTIC_MAX = 3`
([responder.py:53](src/fanops/responder.py:53)) `_terminate_gate_source` fires:
- `moment_hooks` / `captions` → **synthesize a clean fail-open response** → ingest proceeds.
- `moments` → **promote the source to `SourceState.error`** (fail-closed) → `discard_gate`.

**`intro_match` is structurally unanswerable** (Cycle-2 `INV-04`, upheld): it is written
([intro_match.py:108](src/fanops/intro_match.py:108)) but is absent from `responder._SCHEMA` — and
`answer_pending` iterates `_SCHEMA` only. Dormant behind `FANOPS_INTRO_TEASE` (**default OFF**).

**Quarantine.** Every per-unit stage is wrapped: `_quarantine` flips the unit to its `error` state via an
**immutable `model_copy`** ([pipeline.py:150-152](src/fanops/pipeline.py:150)) so one bad source never wedges
the pass.

---

## W4 — Moment decision → rendered Clip

Render eligibility is `MomentState.decided` ([pipeline.py:214](src/fanops/pipeline.py:214)). The heavy render
runs **lock-free** in `produce.run_all`; the in-lock `render_aspects_for` **adopts** the warm mp4 by
fingerprint.

**`_render_fingerprint`** ([clip.py:619](src/fanops/clip.py:619)) — the cache key. Its conditional-inclusion
rule is the whole game: `geom = bool(track) or (focus is not None and len(focus) > 2)`
([clip.py:633](src/fanops/clip.py:633)); `content_type` + `_REFRAME_GEOM_V` (**= 4**) are hashed **only when
`geom` is true**, so centred clips keep their historic fingerprint and never needlessly re-render.

**Atomic replacement.** ffmpeg writes `<dst>.part.mp4` → `os.replace`. **The `.mp4` suffix is required** — it
selects the muxer (`COUP-07` / MOL-78).

**cv2 fail-CLOSED (newly landed, `fcffa73`).** With `smart_framing` ON (the **default**) and the `[framing]`
extra absent, `framing.require_cv2` raises `ToolchainMissingError` → **exit 2**. This is the **only**
fail-closed optional dependency in the system; every other extra fails open. `FANOPS_SMART_FRAMING=0` restores
the centre-crop. A clip that fails to render is **born `ClipState.error`**
([clip.py:830-860](src/fanops/clip.py:830)) — never laundered into a captioned post with a dangling mp4
([pipeline.py:218](src/fanops/pipeline.py:218)).

---

## W5 — Caption → Post minting

```
rendered ──request_captions──> captions_requested   [gate: captions__<clip_id>]
                                        │  responder
captions_requested ──ingest_captions──> captioned | held
captioned ──crosspost_clips──> Post(state=awaiting_approval) ×(owner surfaces)   +  clip → queued
```

**Caption scope = the moment OWNER × its platforms**, gated by `affinity_admits` — **the same predicate
crosspost enforces** ([pipeline.py:199-206](src/fanops/pipeline.py:199)), so caption scope can never drift from
post minting.

**Post identity** is content-addressed off `surface_key = "{account}|{platform}"`. `add_post` is `setdefault`
→ **replay is a no-op**. **One deliberate exception:** `_mint_surface_post` **deletes and re-mints** a post
whose prior state is `rejected` or `failed` ([crosspost.py:229-231](src/fanops/crosspost.py:229)) — so a re-run
*can* resurrect a rejected post.

**The approval boundary.** Every post is born `awaiting_approval`
([models.py:298](src/fanops/models.py:298)); `publish_due` iterates `queued` **only**
([run.py:442](src/fanops/post/run.py:442)); only `approve_post` — guarded on `awaiting_approval`
([ledger.py:579](src/fanops/ledger.py:579)) — promotes. **`INV-08` upheld.** Note
[crosspost.py:286](src/fanops/crosspost.py:286): the clip is set `queued` **unconditionally** after the surface
loop, *even when zero posts were born* (logged `no_post_born`).

---

## W6 — Approval → network publication

**This is the highest-consequence path in the system.** `_publish_one`
([run.py:242](src/fanops/post/run.py:242)) is the **sole** network-POST caller (`INV-09`, upheld). Three
phases:

### CLAIM — tight txn, [run.py:264-272](src/fanops/post/run.py:264)
```python
with Ledger.transaction(cfg) as led:
    post = led.posts.get(post_id)
    if post is None or post.state is not PostState.queued: return None     # :266  lost race → clean no-op
    if due_cutoff is not None and not is_scheduled_due(post, due_cutoff): return None   # :268 re-check under lock
    if is_real_submission_id(post.submission_id):
        get_logger(cfg)("publish", post_id, "republish_with_real_id", …)   # :270-271 LOGS AND PROCEEDS
    post.state = PostState.submitting                                       # :272
```
**Durability point: the txn exit.** `submitting` is persisted **before any network I/O**. A crash mid-network
therefore leaves `submitting`, which `publish_due` **never re-drives** — the F11 guarantee.

### NETWORK — **lock-free**, on a throwaway `Ledger.load(cfg)` ([run.py:274](src/fanops/post/run.py:274))

```python
if is_real_submission_id(post.submission_id):        # :287
    log("skip_resubmit_existing_id")                 # <-- SKIPS THE POST ENTIRELY. state stays `submitting`.
else:
    poster = get_poster(cfg, backend)                # :290   ⚠ GATE-2, INCOMPLETE (Cycle-2 INV-03)
    for attempt in range(_PUBLISH_TRANSIENT_MAX):    # :292   = 3, jittered exp backoff capped at 8 s
        _ensure_media(...)                           # :294   UPLOAD (network)  ← the retry re-runs THIS
        _publish_throttle_wait(...)                  # :295
        led = poster.publish(led, post.id)           # :296   THE POST
```

**The double-publish story, settled.** `PostizPoster.publish` and `ZernioPoster.publish` **never let a
transient escape**: each retries **only** `ConnectTimeout` (connection never established ⇒ body never sent) and
`429` (body rejected), and converts *every other* network exception and *every* 5xx into `needs_reconcile`
without re-POSTing ([postiz.py:393-428](src/fanops/post/postiz.py:393),
[zernio.py:236-268](src/fanops/post/zernio.py:236)). The only exception that escapes is `AuthError`, and
`_is_fatal_auth_error` re-raises it first ([run.py:320-321](src/fanops/post/run.py:320)). **Therefore
`_publish_one`'s outer retry can only ever re-run the media UPLOAD — never the publish POST.** The backends
have **no idempotency key**; this is precisely why `needs_reconcile` exists as a distinct state.

**The promotion gate.** `submitted` + non-empty `public_url` → `published` (+ `published_at`, `publish_hour`,
`publish_dow`) ([run.py:305-311](src/fanops/post/run.py:305)); `submitted` + **empty** url →
`needs_reconcile` ([run.py:312-317](src/fanops/post/run.py:312)). **`_postiz_permalink` always returns `None`
by design** ([postiz.py:78-90](src/fanops/post/postiz.py:78)), so **the steady-state Postiz happy path is
`submitting → submitted → needs_reconcile → published`, never `→ published` directly.**

### FINALIZE — tight txn, [run.py:351-367](src/fanops/post/run.py:351)
Merges **only** `_NET_POST_FIELDS = (state, submission_id, error_reason, public_url, media_urls, published_at,
account_id)` ([run.py:120](src/fanops/post/run.py:120)) into a **freshly loaded** ledger — the B4 lost-update
fix. `account_id` is written **only when changed** ("in-flight wins", [:356](src/fanops/post/run.py:356)).

`_archive_published` runs **OUTSIDE** the txn ([run.py:371-372](src/fanops/post/run.py:371)) — fail-open, so a
full disk can never roll back a committed publish.

### ⚠ The strand (`C3-F1`) — proven by EXP-1/EXP-4

A post carrying a **real** `submission_id` that re-enters `queued` is **claimed to `submitting`, has its POST
skipped, and is persisted `submitting`** — with **no terminal path**. Door: `bulk_send_to_review`
*deliberately* preserves `submission_id` (*"keep the lineage"*), and `approve_post` does not clear it. The five
requeue paths **do** clear it — that hypothesis was tested and disconfirmed.

### Other W6 entry points
- **`publish_now` / `publish_post`** ([run.py:477](src/fanops/post/run.py:477)) — same three phases, **no
  due-gate**, scoped to one post.
- **`mark_published`** (`actions.py:280`) — operator force; rejects a blank url up front; **writes
  `studio_audit.log`**.
- **`cli.cmd_resolve`** ([cli.py:394-398](src/fanops/cli.py:394)) — the **unguarded force-anything escape
  hatch**; `--url` required for terminal states; the **sole writer of `PostState.retired`**.
- **`ensure_up(cfg)`** ([run.py:447-449](src/fanops/post/run.py:447)) — `publish_due` **shells Docker** to
  start the local Postiz stack when `due` is non-empty. A subprocess side effect inside the publish path.

---

## W7 — Reconciliation and metrics

`_RECONCILABLE = (submitting, submitted, needs_reconcile)`
([reconcile.py:433](src/fanops/reconcile.py:433)).

**`reconcile_due`** ([reconcile.py:534](src/fanops/reconcile.py:534)) — polls **out of lock**, applies **in
one txn**:

```
snapshot = Ledger.load(cfg)                      # lock-free
heal_stranded_submitting(cfg)                    # own txn: submitting + NO sid + >15min → needs_reconcile
routing  = _reconcilable_routing(cfg, snapshot)  # per-post backend
for p in reconcilable:  results[sid] = poll(sid) or the captured Exception   # NETWORK, NO LOCK
with Ledger.transaction(cfg) as led:             # apply cached results in ONE txn
    reconcile_posts(led, cfg, get_status=cached, polled_as=polled_as)
```

`polled_as` is a **stale-poll guard**: a post whose `submission_id` changed between poll and apply is skipped
([reconcile.py:610-612](src/fanops/reconcile.py:610)).

### The terminal ladder — and where it is broken

```python
try:    info = poll(post.submission_id) or {}
except AuthError: raise                                    # :622  halt
except Exception as exc:
    …stamp "reconcile poll error: …"; continue             # :627-635  ← BAILS OUT HERE

status = (info.get("status") or "").lower()                # :636
if   status == "published": …promote…                      # :637
elif status == "failed":    …fail…                         # :734
else:                                                      # :739
    if _is_fake_token(post) and state is submitting     and age > 24h: → needs_reconcile   # :746
    if _is_fake_token(post) and state is needs_reconcile and age > 72h: → "GAVE UP:"       # :757
    if post.error_reason: continue                                                          # :767
    if age > 6h: …stamp "stuck …"…                                                          # :772
```

**Two proven defects, both in this ladder:**

| | Finding | Proof |
|---|---|---|
| `C3-F2` | **The escalation + give-up are DEAD CODE on Zernio.** They sit on the *successful-poll* branch. `ZernioStatusClient.get_status` **raises** `RuntimeError` on a 404; `PostizStatusClient.get_status` **returns `{"status":"unknown"}`**. A raising poll `continue`s at `:635` and never reaches `:746`/`:757`. | EXP-4/H5: a `fanops_` post held `submitting` at **+100 000 h** |
| `C3-F1` | **A REAL `submission_id` is excluded from BOTH terminals** — `_is_fake_token` gates `:746` **and** `:757`. It gets ONE `stuck …` breadcrumb, then `:767` silently skips it every later pass. | EXP-4/H6: `submitting` at **+100 000 h**, no give-up |

The code's own justification — *"A post carrying a real id is left to its normal poll (**its status WILL
resolve**), never escalated"* ([reconcile.py:76](src/fanops/reconcile.py:76)) — is an **assumption**, and it is
the load-bearing one.

**Timers pinned (correcting Cycle 2, `C3-COR-01`):** `_STUCK_AFTER = 6 h`, `_SUBMITTING_HEAL_AFTER = 15 min`,
**`_SUBMITTING_ESCALATE_AFTER = 24 h`**, `_RECONCILE_GIVEUP_AFTER = 72 h` — **both ages measured from
`scheduled_time`, not chained.**

**Metrics.** `track.pull_metrics` is the sole writer of `published → analyzed`
([track.py:193](src/fanops/track.py:193)); `metrics_series` is **append-only**, idempotent per cadence offset.
IG reach is read from the **Meta Graph** (the sole IG metric reader).

---

## W8 — Studio mutation workflow

**Route→handler→action attribution is DONE** (Cycle-2 `FIND-013`, all 149). Cycle 3 adds the **durability
semantics** of the mutation families.

| Cross-cutting property | Value |
|---|---|
| Authorization | **NONE** — 0/149 authenticated; all 108 mutating routes CSRF-exposed. Boundary = the network interface (`app.run(host=…)`, default `127.0.0.1:8787`). **Recorded decision.** |
| Ledger writes | **always** via `Ledger.transaction` inside the *action*, **never** in the handler |
| Error contract | actions return `ActionResult(ok, error, detail)`; handlers render a partial |
| htmx constraint | an oversize upload re-renders at **HTTP 200** with a "too large" message — htmx 2.x drops non-2xx swaps |

**Does the browser ever see success before persistence completes? No.** Every mutating action commits its
`Ledger.transaction` **before** constructing its `ActionResult`; the handler renders from the returned result.
The exception is not the browser but the **audit log**: `write_audit` runs **after** the transaction commits
(e.g. [actions.py:1008](src/fanops/studio/actions.py:1008), [:1040](src/fanops/studio/actions.py:1040),
[:1070](src/fanops/studio/actions.py:1070)), so a crash between commit and audit **loses the audit line while
keeping the state change**. Audit is a *record*, not a *journal*.

**Idempotency by family:**

| Family | Guard | Double-click safe? |
|---|---|---|
| approve / reject / unapprove | in-lock source-state guard ([ledger.py:579/594/598](src/fanops/ledger.py:579)) | ✅ 2nd call no-ops |
| requeue ×4 | `state in (failed, error)` re-checked under lock; **all clear `submission_id`** | ✅ |
| `publish_now` | the CLAIM (`state is queued`) | ✅ 2nd call is a clean no-op |
| `publish_due_bucket` | per-post CLAIM | ✅ |
| repost / crosspost | content-addressed id + `setdefault` (repost adds an **epoch suffix** ⇒ a new id **every click**) | ⚠ **repost is NOT idempotent by design** — "fan accounts repost freely" |
| `mark_published` | rejects blank url; refuses an already-terminal post ([actions.py:274](src/fanops/studio/actions.py:274)) | ✅ |
| `bulk_send_to_review` | `state not in _REVIEW_REVERT_BLOCKED` | ✅ — but **preserves `submission_id`** ⇒ `C3-F1` |
| `save_uploads` | video-ext check, traversal-safe `secure_filename`, inbox-bound resolve, **atomic `.uploadpart` → `os.replace`**, 2 GiB cap | ✅ content-addressed downstream |
| wipe preview / confirm | typed word `REMOVE` → **mandatory verified snapshot** → `execute_wipe`'s own re-check | ⚠ **MOL-71**: no server-side check that *preview* ran |

**`execute_wipe` is fully inside ONE `Ledger.transaction`** ([ledger_wipe.py:252-275](src/fanops/ledger_wipe.py:252))
and gated in code: `WipeNotConfirmed` unless `confirmed`; `SnapshotRequired` unless `snapshot_is_restorable`
(which **actually opens the SQLite file and reads `schema_version`**,
[ledger_wipe.py:218-235](src/fanops/ledger_wipe.py:218)). **A wipe that raises halfway rolls back entirely.**
It writes a `.files.txt` manifest of media paths but **does not unlink media** — the files survive the wipe.

---

## W9 — Break-glass and recovery

| Operation | Entry | Exclusion | Verdict |
|---|---|---|---|
| `Ledger.snapshot` | `actions_wipe.confirm_wipe` ([actions_wipe.py:61](src/fanops/studio/actions_wipe.py:61)) | — | ✅ mandatory before a wipe |
| **`Ledger.restore_snapshot`** | **no production caller** — advertised by [ledger_wipe.py:246](src/fanops/ledger_wipe.py:246) as *the* wipe rollback | **flock on `ledger.lock` — excludes NOTHING** | ❌ **`F-B`** |
| `rebuild_catalog` | `ledger.py:746` | ledger txn | ✅ **adds orphans only**; never retires a missing-file source |
| `resume_source` | `pipeline.py:77` | caller's txn | ✅ single owner of the recovery transition; refuses a healthy source |
| `_force_reset_to_catalogued` | `pipeline.py:63` | caller's txn | ⚠ purges disk caches + discards gates + `reconcile_moments(sid, {})` — the T0 reset |
| `fanops resolve <id> <state>` | `cli.py:394` | own txn | ⚠ **force-anything**; `--url` required for terminal states |
| `heal_stranded_submitting` | `reconcile.py:512` | own txn | ✅ but **only for posts with NO `submission_id`** |
| daemon install / uninstall | `daemon.py` | — | shells `launchctl`; bakes a full `PATH` |
| `paths_rebase` | `paths_rebase.py:75` | ledger txn | rewrites `Render.path` after a root move |

**`F-B` re-confirmed (Cycle 2 executed it; Cycle 3 does not re-run a destructive path).** `restore_snapshot`
takes an `fcntl.flock` on `00_control/ledger.lock` ([ledger.py:551](src/fanops/ledger.py:551)) and then
`os.replace`s the database file ([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)). **Every other ledger
writer serializes on the SQLite `BEGIN IMMEDIATE` transaction** — the two primitives are **mutually
invisible**. Cycle 2 proved by execution that a concurrent writer's `commit()` **succeeds** and its data is
**silently discarded**. The operator-facing hazard is that the *documented* recovery procedure hits this **while
the daemon is running**.

**The missing recovery path.** There is **no autonomous or operator recovery for `C3-F1`/`C3-F2`.** A post
stranded in `submitting` with a real token has: no escalation, no give-up, no Studio button that clears it (the
requeue family only accepts `failed`/`error`), and no CLI verb short of `fanops resolve <id> failed` — which
requires the operator to *know* it is stranded, and **nothing tells them**.

---

## W10 — Learning, variants, personas, selection

**Where learning data originates.** `track.pull_metrics` (sole writer of `Post.metrics`, `metrics_series`,
`published → analyzed`). IG reach: **Meta Graph** (`GraphInsightsClient`) — the sole IG metric reader. Lift is
`track._W` = `{saves 4.0, shares 4.0, retention 3.0, reach 0.001, likes 0.05}`; **any weight ≥ 1.0 is
primary**, and a missing primary stamps `lift_degraded` rather than trusting a partial scalar.

**The actuators, and their gates:**

| Actuator | Reversible? | Gate | Validation-frozen? |
|---|---|---|---|
| `adjust.amplify` | ✅ (re-opens a moment request; capped `MAX_AMPLIFY_PER_SOURCE = 3`) | `_learn_pass` ⇒ `cfg.is_live_backend` | no (but harmless) |
| `p4_dim_bias` | ✅ bias-only | DEFAULT-OFF + `learning_validated` + `p4_unlocked` | ✅ |
| `variant_amplify` | ✅ bias-only | DEFAULT-OFF + `learning_validated` | ✅ |
| `timing_bias` | ✅ writes an isolated control file | DEFAULT-OFF + `learning_validated` | ✅ |
| `variant_ucb` | ✅ scorer swap on the caption-bias read path | statistics only | **no — self-declared** |
| **`adjust.retire`** | ❌ **IRREVERSIBLE** (`reconcile_moments` refuses to un-retire, [ledger.py:636-642](src/fanops/ledger.py:636)) | **`cfg.is_live_backend` ONLY** | ❌ **no** |

**`C3-F10`: the destructive actuator has the weakest gate.** `_learn_pass` calls
`retire(led, r["losers"])` directly. It fires as soon as `round(n * 0.2) >= 1` ⇒ **n = 3 analyzed posts** —
while the *reversible* bias actuators wait for `learning_validated` **and** ≥ 8 attributed posts across ≥ 2
values ([validation_gate.py:52](src/fanops/validation_gate.py:52)).

**The guards that *do* exist are real** (which is why this is a hazard, not a reachable defect): a loser must be
in the bottom 20 %, **and** below `lift_floor = 20.0`, **and** not a winner, **and** **not `lift_degraded`**
([adjust.py:50-52](src/fanops/adjust.py:50)).

**Inert / reserved, confirmed:**
- **`intro_match`** — live-wired, structurally unanswerable (`INV-04`).
- **`Ledger.set_post_state`** ([ledger.py:572](src/fanops/ledger.py:572)) — **zero callers**.
- **`_LIVE_CLIP_STATES`** ([ledger.py:649](src/fanops/ledger.py:649)) — a **dead guard**; nothing writes
  `ClipState.published`/`analyzed`, so `clip_live` is always `False`.
- **`RenderState.*`** beyond `rendered` — driverless (Cycle-1 `FIND-001`, upheld).
- **`_materialize_variant_media`** ([run.py:178-180](src/fanops/post/run.py:178)) — a **`return` with no body**:
  the P9 teardown left the call site in place. Harmless, but it is a live call to a no-op.
