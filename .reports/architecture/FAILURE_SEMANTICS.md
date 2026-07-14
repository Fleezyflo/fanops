# FanOps — Failure Semantics

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73`** · Machine-readable twin: [`failures.json`](failures.json)

Produced by an **AST pass over all 127 modules** (not a grep — Cycle-2's method note), plus targeted manual
tracing of every handler on the publish, reconcile, gate, and recovery paths.

---

## 1. The census

| Metric | Count |
|---|---|
| Exception handlers total | **721** |
| **Bare `except:`** | **0** ✅ |
| Broad `except Exception` / `BaseException` | **331** (45.9 %) |
| Typed handlers | **390** (54.1 %) |
| Broad **and silent** (no log) **and** failure-shaped return | **49** |
| Broad **and silent** returning a non-empty value | **80** — but **~60 of these are `ActionResult(ok=False, …)`**, i.e. *explicit* failures, not silent ones |
| Handlers that re-raise / escalate | **62** |
| Retry loops | **10** |
| Subprocess call sites | **32** |
| Network call sites (`requests`) | **15** |
| `Ledger.transaction` sites | **74** |

**Broad-handler density (top 5):** `studio/views.py` 38 · `studio/actions.py` 24 · `studio/golive.py` 16 ·
`studio/actions_run.py` 14 · `pipeline.py` 12.

**The house norm is explicit and mostly honoured:** *fail-open with a logged breadcrumb.* CI enforces it —
`tests/test_swallow_ratchet.py` fails a **new** silent broad `except`. The 49 silent ones are the grandfathered
baseline plus contract-correct exceptions.

> **I did not classify all 331 broad catches as defects.** Each was judged by its **contract** and its
> **downstream interpretation**. The overwhelming majority are correct fail-open degradation on a read path.
> The findings below are the ones where the *caller cannot distinguish failure from a legitimate result*, or
> where the failure creates a later poison pill.

---

## 2. The failure-normalisation ladder on the publish path

This is the most carefully-engineered failure surface in the codebase, and it is **sound**. Recording it in
full because a future edit that breaks it causes a double-publish.

```
                     ┌── AuthError ────────────► RE-RAISE. Halt the run. Never burn the queue.  (run.py:320)
                     │                            Matched by TYPE, not substring (the H8 fix).
poster.publish ──────┤
                     ├── ConnectTimeout ───────► RETRY (≤4, exp backoff). SAFE: the connection was never
                     │                            established, so the body was NEVER SENT.  (postiz.py:394)
                     ├── 429 ──────────────────► RETRY (≤4, exp backoff). SAFE: the body was REJECTED.
                     │                                                              (postiz.py:427)
                     ├── any OTHER RequestException ─► needs_reconcile. NEVER re-POST.  (postiz.py:398)
                     │        "the response, not the request, was lost — ambiguous"
                     ├── 5xx ──────────────────► needs_reconcile. NEVER re-POST.       (postiz.py:424)
                     ├── 2xx with no post id ──► needs_reconcile.                       (postiz.py:409)
                     ├── 2xx + id ─────────────► submitted (+ submission_id).           (postiz.py:412)
                     └── other 4xx ────────────► failed (re-queueable).                 (postiz.py:434)
```

**`ZernioPoster.publish` is byte-for-byte symmetric** ([zernio.py:236-273](src/fanops/post/zernio.py:236)).
Sibling parity **holds here** — I checked it specifically because `src/fanops/CLAUDE.md` warns that this is
where the bugs live.

**The load-bearing consequence:** `poster.publish` **never lets a transient escape**. The only exception that
escapes it is `AuthError`, and `_publish_one`'s handler re-raises that first. **Therefore `_publish_one`'s
outer retry loop ([run.py:292](src/fanops/post/run.py:292)) can only ever re-run `_ensure_media` — the media
UPLOAD — and never the publish POST.** The backends have no idempotency key; this asymmetry is the entire
double-publish defence and it is correct.

**`needs_reconcile` is never downgraded to `failed`** — guarded at both layers
([run.py:325](src/fanops/post/run.py:325), [postiz.py:433](src/fanops/post/postiz.py:433),
[zernio.py:271](src/fanops/post/zernio.py:271)). `failed` is re-queueable, so downgrading an ambiguous park
would risk a double-publish. **`INV-10` upheld.**

---

## 3. Where the ladder breaks: the terminal gap (`C3-F1` / `C3-F2`)

The publish side never guesses. **The reconcile side has a hole the publish side hands posts into.**

```python
# reconcile_posts, reconcile.py:620-635
try:
    info = poll(post.submission_id) or {}
except AuthError:
    raise                                                     # :622  correct
except Exception as exc:
    led.posts[post.id] = post.model_copy(update={
        "error_reason": f"reconcile poll error: {str(exc)[:200]}"})
    log("reconcile", post.id, "poll-error", err=…)
    continue                                                  # :635  ◄── BAILS OUT OF THE LADDER
# ─────────────────── everything below is reachable ONLY on a SUCCESSFUL poll ───────────────────
else:                                                         # :739
    if _is_fake_token(post) and submitting      and age > 24h: → needs_reconcile      # :746
    if _is_fake_token(post) and needs_reconcile and age > 72h: → "GAVE UP:"           # :757
    if post.error_reason: continue                                                    # :767
```

**Two independent defects, both proven by execution:**

### `C3-F2` — the terminal machinery is DEAD CODE on the Zernio backend

The escalation/give-up sit on the **successful-poll** branch. The two status clients disagree on what an
unknown submission id *is*:

| Client | Unknown id | Reaches `:746`/`:757`? |
|---|---|---|
| `PostizStatusClient` | row absent from the ±35 d page → **`return {"status": "unknown"}`** | ✅ |
| `ZernioStatusClient` | `GET /posts/{id}` → 404 → **`raise RuntimeError`** | ❌ **never** |

`reconcile.py:617`'s own comment says *"Polling it 404s → the status client's `get_status` raises
RuntimeError"* — the author **knew** it raises, and still placed the escalation where a raise cannot reach.
**EXP-4/H5:** a `fanops_`-token post on the Zernio shape held `submitting` at +6 h, +25 h, +73 h, +1 000 h and
**+100 000 h**. Never escalated. Never gave up.

### `C3-F1` — a REAL `submission_id` is excluded from BOTH terminals, on BOTH backends

`_is_fake_token` ([reconcile.py:77](src/fanops/reconcile.py:77)) gates `:746` **and** `:757`. The
justification is a comment: *"A post carrying a real id is left to its normal poll (**its status WILL
resolve**), never escalated."* **That is an assumption, not a guarantee.** It is false whenever the platform
deleted the post, the integration was removed, or the backend reports a non-terminal state forever (Postiz's
own `QUEUE` maps to `"scheduled"`).

**EXP-4/H6**, on the *working* backend: one `stuck …` breadcrumb at 25 h, then
[reconcile.py:767](src/fanops/reconcile.py:767) (`if post.error_reason: continue`) silently skipped it at
+73 h and **+100 000 h**. It never left `submitting`.

> **Brief §9 claims #1 ("every claimed retry has a finite terminal behavior") and #2 ("every `submitting` Post
> can eventually leave `submitting`") are FALSIFIED.**

---

## 4. Failures that appear SUCCESSFUL to the caller

The brief asks for these specifically. There are **four**, ranked.

| # | Site | The lie | Severity |
|---|---|---|---|
| 1 | **`get_poster`** ([post/\_\_init\_\_.py:19-29](src/fanops/post/__init__.py:19)) | An **unrecognised** backend string returns a **`DryRunPoster` on a LIVE system** without raising. The guard fires only on the literal `dryrun` (case-insensitively); the `PROVIDERS` lookup is case-**sensitive**. They disagree. The caller gets a `Poster` and believes it published. **Cycle-2 `F-A` — upheld.** | **HIGH** |
| 2 | **`_publish_one` skip-resubmit** ([run.py:287](src/fanops/post/run.py:287)) | Returns `"submitting"` — a *success-shaped* state value — after **doing nothing at all**. `publish_due` counts it as neither published nor failed; it simply vanishes from the tally. **`C3-F1`.** | **HIGH** |
| 3 | **`_requeue_transient_failed_for_daemon`** ([run.py:428-429](src/fanops/post/run.py:428)) | `except Exception: return requeued` — `requeued` is incremented **inside** the txn, so a commit failure returns a **non-zero count while nothing persisted**. The **only** handler in `post/run.py` with no log line. *Blast radius today: `publish_due` discards the return value* ([run.py:440](src/fanops/post/run.py:440)) — so **no caller is currently lied to.** | **LOW** (latent) |
| 4 | **`framing._detect_faces`** ([framing.py:151](src/fanops/framing.py:151)) | `except Exception: return out` returns the **partially accumulated** face list. A crash after 3 of 5 faces is indistinguishable from "there were 3 faces" — which can classify a 2-shot as a 1-shot and pick the wrong crop. Deliberate (*"a single bad frame never sinks the window"*), and the render still succeeds. | **LOW** |

---

## 5. Failures that become a default / centred / dry-run behaviour

| Site | Degrades to | Distinguishable? |
|---|---|---|
| `framing._cv2` / `_detector` ([framing.py:30,48](src/fanops/framing.py:30)) | `None` → centre-crop | **Yes, now** — `require_cv2` raises `ToolchainMissingError` (exit 2) when `smart_framing` is ON. **The only fail-CLOSED dependency.** (`fcffa73`) |
| `Config.poster_backend` ([config.py:241-245](src/fanops/config.py:241)) | warns → `dryrun` | Yes — warns |
| `compress.publish_backend_for_post` ([compress.py:73-76](src/fanops/post/compress.py:73)) | `cfg.poster_backend or "dryrun"` + breadcrumb | Yes — logs. **A second provider resolver that never checks `is_live`** (`COUP-16`) — *not* a publish bypass; it only decides whether to **shrink**. |
| `timeutil.publish_buckets` | **fails CLOSED to UTC** | Yes |
| `accounts.load_accounts_safe` ([accounts.py:350](src/fanops/accounts.py:350)) | `(Accounts(cfg), err)` — an **empty** registry + an error string | Yes — **and `reconcile.py:466-467` checks it.** Correct. |
| `validation_gate.learning_validated` ([validation_gate.py:28](src/fanops/validation_gate.py:28)) | `False` — **fails closed** | Correct direction |
| `reconcile._ig_rest_verdict` ([reconcile.py:196](src/fanops/reconcile.py:196)) | `_GATE_FAILOPEN` → `continue` (retry next tick) | Yes — logs `ig_confirm_transport_failopen` |

---

## 6. Retry counters hidden in strings (`COUP-03`, upheld and extended)

`Post.error_reason` is a **structured control channel**, not a message. Three parsers read machine state out
of one free-text field written by ~14 sites:

| Parser | Extracts | Site |
|---|---|---|
| `transient_daemon_retry_count` | `transient_daemon_retry=n/3` | written [run.py:336](src/fanops/post/run.py:336), read [:333/:405/:419](src/fanops/post/run.py:333) |
| `_is_giveup` | the `GAVE UP:` prefix | [reconcile.py:85](src/fanops/reconcile.py:85) |
| REST-gate quarantine | the `unverified:` sentinel | [reconcile.py:99](src/fanops/reconcile.py:99) |

**Any writer that overwrites `error_reason` with free text silently resets the retry budget and clears the
give-up marker.** [reconcile.py:714](src/fanops/reconcile.py:714) does exactly this **deliberately** on a
successful publish (`"error_reason": None`).

**Cycle-3 extension — `reconcile.py:767` makes the field a *latch*.** `if post.error_reason: continue` means
**any** non-empty `error_reason` permanently suppresses further reconcile attention. So the field is
simultaneously: a retry counter, a terminal marker, a quarantine sentinel, **and a do-not-look-at-me-again
flag**. Four semantics, one free-text string, ~14 writers.

---

## 7. Silent swallows that are CONTRACT-CORRECT (explicitly *not* defects)

Recorded so a later cycle does not re-file them:

| Site | Why it is correct |
|---|---|
| `audit.write_audit` ([audit.py:46](src/fanops/audit.py:46)) `except Exception: pass` | Its docstring is explicit: *"NEVER raises: the caller's action MUST complete even if the audit write fails … The log is a tail-and-grep surface, **not load-bearing for correctness**."* Losing an audit line is strictly better than failing an approve. **Consequence recorded in [`OBSERVABILITY.md`](OBSERVABILITY.md), not here.** |
| `_archive_published` ([run.py:63-65](src/fanops/post/run.py:63)) | Fail-open by design and **outside the finalize txn** — a full disk must never roll back a committed live publish. |
| `pipeline._quarantine` ([pipeline.py:142-152](src/fanops/pipeline.py:142)) | This *is* the failure mechanism, not a swallow: it converts a per-unit exception into an `error` state + a typed reason so one bad source never wedges the pass (F03). Uses an **immutable `model_copy`** so a future `frozen=True` cannot raise *inside the handler*. |
| `_reconcile_safe` / `_publish_safe` ([pipeline.py:311-338](src/fanops/pipeline.py:311)) | Both **re-raise `AuthError`** and swallow-with-log everything else — correct: a status-API hiccup must not wedge the pass. |
| `pipeline_run._read_body` ([pipeline_run.py:35](src/fanops/pipeline_run.py:35)) | The lock **body** is advisory; the **flock** is the authority and is kernel-released on death. A torn body degrades a breadcrumb, never the lease. |

---

## 8. Exception → state-change conversions (the complete set)

| Exception context | Becomes | Site |
|---|---|---|
| any per-unit stage raise | `SourceState.error` / `MomentState.error` / `ClipState.error` | [pipeline.py:151](src/fanops/pipeline.py:151) |
| gate ceiling `3/3` on `moments` | `SourceState.error` (**fail-closed**) | [responder.py:182-184](src/fanops/responder.py:182) |
| gate ceiling `3/3` on `moment_hooks`/`captions` | a **synthesised clean fail-open response** (fail-open) | [responder.py:159-174](src/fanops/responder.py:159) |
| unparseable `scheduled_time` | `PostState.failed` | [run.py:389](src/fanops/post/run.py:389) |
| publish transient, no real sid, retries exhausted | `PostState.failed` + `transient_daemon_retry=n/3` | [run.py:334-337](src/fanops/post/run.py:334) |
| publish transient, **real** sid | `PostState.needs_reconcile` | [run.py:329](src/fanops/post/run.py:329) |
| any non-transient publish error | `PostState.failed` | [run.py:339](src/fanops/post/run.py:339) |
| poll error | **no state change** — `error_reason` only, then `continue` | [reconcile.py:633-635](src/fanops/reconcile.py:633) |
| poller reports `failed` | `PostState.failed` | [reconcile.py:735](src/fanops/reconcile.py:735) |
| `cv2` absent + `smart_framing` ON | `ToolchainMissingError` → **exit 2** | `framing.require_cv2` |

---

## 9. Subprocess return-code handling

32 call sites. Two shapes:

- **Checked** — `r.returncode != 0` → fail-open with a logged breadcrumb (e.g.
  `compress.maybe_shrink_for_cap` [compress.py:32](src/fanops/post/compress.py:32) tries CRF 28→32→36→40 and
  returns the **original path** if all fail).
- **`timeout=`** — present on the heavy calls (`subprocess.run(cmd, capture_output=True, timeout=600)`).

The whisper/ffmpeg subprocesses run **lock-free** in `produce.run_all`, never under the ledger flock — this is
enforced by the `in_lock=True` adopt-or-defer contract ([pipeline.py:162,164](src/fanops/pipeline.py:162)).

**One subprocess side effect inside the publish path:** `publish_due` calls `postiz_lifecycle.ensure_up(cfg)`
when `due` is non-empty ([run.py:447-449](src/fanops/post/run.py:447)) — it **shells Docker** to start the
local Postiz stack.

---

## 10. Network ambiguity — the complete decision table

| Signal | Body sent? | Decision | Rationale |
|---|---|---|---|
| `ConnectTimeout` | **No** | RETRY (≤4) | connection never established |
| `429` | **No** (rejected) | RETRY (≤4) | server refused to accept |
| `ReadTimeout` / `ConnectionError` mid-stream | **MAYBE** | `needs_reconcile` — **never re-POST** | no idempotency key |
| `5xx` | **Yes** | `needs_reconcile` — **never re-POST** | the request landed; the response failed |
| `2xx`, no recognisable id | **Yes** | `needs_reconcile` | it may be live; we cannot address it |
| `401` | n/a | **`AuthError` → halt the run** | every post will fail |
| other `4xx` | **Yes**, rejected | `failed` (re-queueable) | permanent; retrying won't help |

This table is the system's best work. It is correct on both backends.
