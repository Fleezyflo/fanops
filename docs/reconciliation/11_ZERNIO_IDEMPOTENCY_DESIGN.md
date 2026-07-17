# 11 — Zernio Publish Idempotency: Root Cause, Design, Test Matrix

> **REV 4 — IMPLEMENTED.** Rev 3 was approved (`APPROVE IDEMPOTENCY IMPLEMENTATION`, 2026-07-17) and built
> as one PR off merged main. **No Zernio call was made. No post was requeued, approved, scheduled or
> published. The four `failed` records are untouched** — requeueing them remains behind its own separate
> approval (§14).
>
> Rev 1 → Rev 2 → Rev 3 corrections (all operator-caught except D4, all recorded in §0, none erased): the
> 409 semantics (§3), the separate reconciliation-candidate field (§5), the transport/crash matrix (§6), the
> retry deadline (§7), the false request-ID identity proof (D6), the over-reaching protocol refactor (D7).
> **Rev 3 → Rev 4** records what was BUILT, including where the build deviated from the design (§10.1).

Evidence base: report 09 §2.1/§3/§7, and the live code at `9ca4071`.

---

## 0. Prior-revision defects (recorded, not erased)

| # | Said | Why wrong | Fixed |
|---|---|---|---|
| **D1** | *"A 409 is proof the content is live"* | Zernio is a **scheduler**; a 409 proves only that **Zernio holds a matching record** — not platform publication, not ownership, not completion | §3 (Rev 2) |
| **D2** | Outcome as state-mutation + overloaded `None` | Untyped; invariants split across files | §4 |
| **D3** | *"~35 lines in one file"* | False — `_NET_POST_FIELDS`, reconcile, models all change | §10 |
| **D4** | Assumed `submission_id` + `needs_reconcile` safe | **Disproven** — reconcile would misattribute | §5 (Rev 2) |
| **D5** | Retries unbounded w.r.t. the window | A retry past ~5 min carries a dead header and can double-post | §7 (Rev 2) |
| **D6** | *"One `post.id` ↔ one Zernio create operation"* | **FALSE — operator-caught.** `crosspost.py:227-233` **pops** a `failed`/`rejected` record and **remints under the identical `post.id`** with a fresh `created_at`. `post.account_id` is also **refreshed at publish** (`run.py:303-305`). `uuid5(ns, post.id)` alone collides across incarnations and across account remaps | §8 |
| **D7** | Changed the shared `Poster` protocol → refactor `postiz.py`/`dryrun.py` | Unjustified blast radius on a path unrelated to this incident. The typed result belongs **inside** the Zernio backend | §4, §10 |

**D6 is not academic: the four burned records are `failed`** — exactly the population `crosspost` pops and
remints. A `post.id`-only name would hand a *new* incarnation the *old* incarnation's request identity.

---

## 1. Root cause

| # | Fault | Where | Consequence |
|---|---|---|---|
| **R-1** | Two re-POST branches send a byte-identical body with **no `x-request-id`** | `zernio.py:289` (`ConnectTimeout`), `:318` (`429`) | A `429` fired after the create landed, or a slow success read as a timeout, **creates a second Zernio post** |
| **R-2** | `_extract_zernio_id` has **no `existingPost` branch** | `zernio.py:69-83` | An idempotent replay parses as "no id" → `needs_reconcile` — **a successful submission misfiled as ambiguous** |
| **R-3** | A **409 falls through `break` → `failed`** | `zernio.py:320-324` | **Live defect today.** `failed` is **re-queueable**, and the record is filed as not-submitted when Zernio explicitly said it holds a duplicate |

R-1 and R-2 are **inseparable** (`zernio.py:20-22`). The never-re-POST invariant (CLAIM is `queued`-only,
`run.py:267`; refuses a real `submission_id`, `:292`) is **not** replaced: the ~5-minute window cannot span
the 600s pass interval. Idempotency closes the *within-attempt* hole; the invariant closes the
*cross-pass* hole. **Both required.**

---

## 2. The contract

`paths./v1/posts.post.parameters[0]`: `x-request-id`, header, optional, `{type: string, format: uuid}`.
Same value → second request treated as a retry → **HTTP 200** with the original in **`existingPost`**.
**Window ~5 minutes.** Then, independently: content-hash dedup on `(platform, accountId, content + media
URLs)` over **24h** → **409** + `details.existingPostId`. Order: request-id first, then content-hash.

**Spec gaps:** `200` is **not** in the responses map (`201,400,401,403,409,429`); **`existingPost` is never
schematised** (4 prose mentions in 1.75 MB). Both shapes are **unproven** → parse tolerantly, fail to
`ReconciliationRequired`, never to terminal failure.

---

## 3. What a 409 means (ACCEPTED — unchanged)

> **A 409 means Zernio reports duplicate content within its duplicate-protection window. It does not
> prove social-platform publication, ownership by this FanOps record, or successful completion. It
> always requires reconciliation.**

Zernio is a hosted scheduler; its dedup is over **its own records**. The matched record may be queued,
failed, or rejected downstream; the key `(platform, accountId, content-hash)` matches another FanOps
record or an operator's manual post identically; and our request was *rejected*, so nothing of ours
completed. **`existingPostId` is a candidate pointer, never an identity.**

---

## 4. The private Zernio create result

**The shared `Poster` protocol is UNCHANGED:** `publish(led: Ledger, post_id: str) -> Ledger`.
`postiz.py`, `dryrun.py` and `post/__init__.py` are **untouched**. The typed result is **private to the
Zernio backend** and never escapes it.

### 4.1 `src/fanops/post/zernio_outcome.py` (NEW — Zernio-specific)

```python
"""The typed result of ONE Zernio create attempt. PRIVATE to the Zernio backend: it never crosses the
Poster protocol, which stays `publish(led, post_id) -> Ledger` for every backend.

Why a type rather than direct state mutation: it separates "what Zernio said" from "what the ledger
becomes", so the 409 rule (never `failed`) and the candidate rule (never `submission_id`) each live at
exactly one mapping site — reviewable in one place. Rev 1 let the HTTP branches set state inline, which
is how a 409 reached `failed` (R-3) with no single owner of the rule."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Created:
    """Zernio created a NEW post. post_id is a real backend id."""
    post_id: str

@dataclass(frozen=True, slots=True)
class IdempotentReplay:
    """Zernio recognised our x-request-id and returned the ORIGINAL post (HTTP 200 + existingPost).
    The SAME logical submission as Created — publication semantics are identical."""
    post_id: str

@dataclass(frozen=True, slots=True)
class ReconciliationRequired:
    """Disposition UNKNOWN. Never success, never terminal. candidate_post_id is an UNPROVEN pointer
    (§3) — evidence only, never an identity."""
    reason: str
    evidence: str
    candidate_post_id: str | None = None

@dataclass(frozen=True, slots=True)
class TerminalFailure:
    """Provably not accepted — nothing reached Zernio, or Zernio rejected it with a verdict re-sending
    cannot change. Safe to mark `failed` (re-queueable)."""
    reason: str
    evidence: str

ZernioCreateResult = Created | IdempotentReplay | ReconciliationRequired | TerminalFailure
```

### 4.2 `_create` returns the result; `publish` maps it onto the ledger

```python
class ZernioPoster:
    def publish(self, led: Ledger, post_id: str) -> Ledger:          # PROTOCOL UNCHANGED
        post = led.posts[post_id]
        result = self._create(post)                                   # private, typed; may raise ZernioAuthError
        match result:
            case Created(post_id=sid):
                post.state, post.submission_id = PostState.submitted, sid
            case IdempotentReplay(post_id=sid):
                post.state, post.submission_id = PostState.submitted, sid
                _log_event(self.cfg, "publish", post.id, "idempotent_replay",   # structured audit event
                           sub=sid, request_id=_request_id(post))
            case ReconciliationRequired(reason=r, evidence=e, candidate_post_id=c):
                post.state = PostState.needs_reconcile
                post.reconcile_candidate_id = c                        # NEVER submission_id (§5)
                cand = f" candidate={c}" if c else ""                  # mirrored for downgrade visibility
                post.error_reason = f"zernio {r}:{cand} {e}"[:400]
            case TerminalFailure(reason=r, evidence=e):
                post.state = PostState.failed
                post.error_reason = f"zernio {r}: {e}"[:400]
        return led
```

`ZernioAuthError` propagates out of `_create` unchanged and halts the run (`run.py:299`).

`Created` and `IdempotentReplay` both land `submitted` + a real `submission_id`, so the **existing**
`run.py:331-347` `public_url` gate applies unchanged: Zernio returns no permalink at create, so both park
`needs_reconcile` and `reconcile.py` back-fills — **byte-identical to today's successful path**. The
replay differs only by the audit event, never by publication semantics.

---

## 5. Candidate-ID safeguards (ACCEPTED — unchanged)

Reader audit (Rev 2 §5, 19 readers) proved **Option A false**: `_RECONCILABLE = (submitting, submitted,
needs_reconcile)` — reconcile's poll set **deliberately includes** `needs_reconcile`, so a candidate in
`submission_id` would be polled, found to exist (**of course — that is why we got a 409**), and promoted
to `published` with that post's permalink (`reconcile.py:768`). Silent misattribution.

```python
# models.py, on Post
reconcile_candidate_id: Optional[str] = None
# An UNPROVEN pointer from a backend duplicate/ambiguity signal (Zernio 409 details.existingPostId).
# EVIDENCE ONLY — never an identity. NOT submission_id; never copied into it, never used as a poll key,
# never a promotion source, without an explicit operator identity decision (report 11 §3/§5).
```

| Safeguard | Mechanism |
|---|---|
| Added to the network-finalize set | **`_NET_POST_FIELDS += "reconcile_candidate_id"`** (`run.py:120`) — else the write is discarded at finalize |
| Never auto-polled using the candidate | No reader reads it as a poll key. `reconcile._poll_targets` keys on `submission_id` (unchanged). Pinned by negative tests #39/#40 |
| Never promotes without an identity decision | `reconcile.py` gains an explicit invariant comment + a guard; it **clears** the candidate only when an explicit identity decision resolves the record |
| Operator UI | The reconciliation view renders it as **unverified reconciliation evidence**, visually distinct from `submission_id` |
| Downgrade visibility | Candidate **mirrored into `error_reason`** (existing field) — survives an older binary dropping the new key |
| Never a real submission_id | `is_real_submission_id` is never applied to it; test #26 |

**Schema:** additive `Optional[str] = None`; **no migration**; **`SCHEMA_VERSION` stays 11** (precedent:
`media_id`/`product_type`; the migration list `v3,v4,v8,v10` is all structural). **Downgrade cost, stated:**
`extra="ignore"` is deliberate pinned forward-compat (`models.py:171-176`) — an older binary drops the
field. Fail-safe (degrades to today's state) and mitigated by the `error_reason` mirror.

---

## 6. Transport / crash matrix (ACCEPTED — unchanged)

**Invariant:** *after any potentially accepted POST, no later daemon pass may issue another POST for that
record without an explicit reconciliation decision.* Structural: every "may have landed" row terminates in
`needs_reconcile` or `submitting`, **neither claimable** (CLAIM requires `queued`).

| # | Boundary | Reached Zernio? | Retry? | Request ID | State | Later POST? |
|---|---|---|---|---|---|---|
| 1 | Before first send | No | n/a | derived, unused | `submitting` (CLAIM, `run.py:297`) | **No** |
| 2 | ConnectTimeout | **No** — connection never established | **Yes**, inside deadline | **same** | loop; deadline → per §7 | **No** |
| 3 | 429 | **Yes** | **Only if `Retry-After` fits the deadline** | **same** | retry, else `needs_reconcile` | **No** |
| 4 | ReadTimeout | **Yes — sent, response lost** | **No** | same | `needs_reconcile` | **No** |
| 5 | Connection loss after bytes sent | Possibly | **No** | same | `needs_reconcile` | **No** |
| 6 | CREATED before finalize (crash) | **Yes** | n/a | same | stays `submitting` | **No** — retry ≤5 min → **200 + `existingPost`** → recognised (R-2 closed) |
| 7 | REPLAY before finalize (crash) | Yes | n/a | same | stays `submitting` | **No** — as #6 |
| 8 | 409 with candidate | Yes — rejected | **No** | same | `needs_reconcile` + candidate | **No** |
| 9 | 409 without candidate | Yes — rejected | **No** | same | `needs_reconcile`, candidate `None` | **No** |
| 10 | Malformed success body | Yes — may have created | **No** | same | `needs_reconcile` (`success_no_id`) | **No** |
| 11 | Conflicting `id` ≠ `existingPost.id` | Yes | **No** | same | `needs_reconcile` (`conflicting_ids`), **candidate `None`** | **No** |

Row 11: two ids means the response contract is not what we modelled. Adopt neither; record both in
evidence.

---

## 7. Retry bounded to the window (ACCEPTED — unchanged)

`_RETRY_DEADLINE_S = 240.0` strictly inside `_IDEMPOTENCY_WINDOW_S = 300.0`, on **`time.monotonic()`**
(immune to wall-clock steps/NTP/DST), per publish call. Every retry must satisfy
`elapsed + wait < _RETRY_DEADLINE_S` **before** sleeping. A **429 is retried only when `Retry-After` fits**
the remaining budget; no header → bounded backoff, same test. Past the deadline: **never send again** —
classify by whether any request may have reached Zernio (nothing sent → `TerminalFailure`; otherwise →
`ReconciliationRequired`). Past the window the header is no longer honoured, so a late retry *is* the
double-post R-1 exists to close.

---

## 8. Request ID — corrected formula and proof

### 8.1 Why `post.id` alone is insufficient — **proven false, operator-caught**

`crosspost.py:227-233`, verbatim:

```python
existing = led.posts.get(pid)
if existing is not None:
    if existing.state in (PostState.rejected, PostState.failed):
        led.posts.pop(pid, None)                                   # ← REMOVES the record
        existing = None
if existing is not None:
    return 0
led.add_post(Post(id=pid, ...,
    created_at=iso_z(datetime.now(timezone.utc)),                  # ← NEW birth stamp, "NOT in the pid"
```

A `failed`/`rejected` record is **popped and reminted under the identical `post.id`**. And
`run.py:303-305` **refreshes `post.account_id` at publish** ("a Go-Live integration REMAP since
crosspost"). So one `post.id` can denote **several distinct create operations, to different Zernio
accounts**. `uuid5(ns, post.id)` would give a *new* incarnation the *old* incarnation's identity.

### 8.2 The canonical name

```python
_ZERNIO_REQ_NS = uuid.UUID("<fixed constant, chosen once, NEVER changed>")
_REQ_NAME_V = "1"                       # formula version; bump => all in-flight ids change (never bump casually)

def _request_id(post: Post) -> str:
    """Stable per (record incarnation × platform × resolved Zernio account). Caller MUST have passed
    _require_request_identity(post) first — this function never invents a discriminator."""
    return str(uuid.uuid5(_ZERNIO_REQ_NS, "|".join(
        (_REQ_NAME_V, post.id, post.created_at, post.platform.value, post.account_id))))
```

| Component | Role | Immutable within an incarnation? |
|---|---|---|
| `post.id` | the logical surface (clip × account × platform) | ✅ ledger key |
| **`post.created_at`** | **per-incarnation discriminator** | ✅ **proven §8.3** — fresh on every remint |
| `post.platform.value` | platform | ✅ set at birth, never written after |
| **`post.account_id`** | **the resolved Zernio account actually receiving the request** | ✅ within an attempt — `run.py:303-305` refreshes it **before** `poster.publish` (`:329`), and `zernio.py:280` already builds the payload from this same field, so the id and the payload cannot disagree |

`platform` is already hashed into `post.id` via `skey = surface_key(account, platform)`
(`crosspost.py:194-195`), so including it is redundant-but-explicit: the name is then self-documenting and
survives any future change to `pid`'s derivation. `account_id` is **not** in `post.id` (the *handle* is;
the *Zernio integration id* is not) — it is a genuine addition.

### 8.3 Proof of per-incarnation uniqueness

**`Post.created_at` is written at BIRTH ONLY, and never mutated on an existing record.** Every write in
`src/fanops/`:

| Site | Writes | Verdict |
|---|---|---|
| `crosspost.py:241` | birth of a new incarnation (after the pop) | ✅ **this is the discriminator changing** |
| `studio/actions.py:510` | `repost_post` — a **new** `post.id` | ✅ new record entirely |
| `studio/actions.py:633` | `crosspost_to_account` — a new post | ✅ new record |
| `ledger.py:61` | `_migrate_v3_created_at` — **only when missing** ("Idempotent: an existing created_at is kept") | ✅ backfill, not mutation |
| `run.py:53` | `_archive_published` — **reads** it into a JSON archive | ✅ not a write |

No other site writes a `Post.created_at`. It is **not** in `_NET_POST_FIELDS` (`run.py:120`), so the
finalize merge cannot overwrite it. **Immutability: proven.**

Therefore:

- **Same incarnation, any retry** (inner loop, outer `run.py:325` loop, post-crash recompute): all four
  components unchanged → **identical UUID**. ✔ requirement 1
- **Popped + reminted under the same `post.id`**: `created_at` is a fresh wall-clock stamp → **different
  UUID**. ✔ requirement 2
- **Different resolved `account_id`** (Go-Live remap): → **different UUID**. ✔ requirement 3
- **Different platform**: → **different UUID**. ✔ requirement 4
- `_requeue_failed_transient` (`run.py:436-456`) requeues **without** touching `created_at` → same
  incarnation → same UUID. Correct: it is a retry of that incarnation, not a new one.

### 8.4 Missing legacy `created_at` — explicit refusal, never silent

`Post.created_at` is **`Optional[str] = None`** (`models.py:341`). In practice every row has it (three
mint sites always stamp it; `_migrate_v3_created_at` backfills any v2 row and **never raises**). But the
**type permits `None`**, so presence is a current-practice observation, **not** an invariant — and a
design may not rest on an unenforced observation.

**A missing discriminator is a hard, pre-network refusal:**

```python
def _require_request_identity(post: Post) -> ReconciliationRequired | TerminalFailure | None:
    """Return a terminal result when a request identity cannot be derived. Called BEFORE the first
    send. NEVER substitutes a default: a fabricated discriminator would make two different incarnations
    share one x-request-id — the exact collision this formula exists to prevent."""
    missing = [n for n, v in (("created_at", post.created_at), ("account_id", post.account_id),
                              ("platform", getattr(post.platform, "value", None))) if not (v or "").strip()]
    if missing:
        return TerminalFailure("missing_request_identity",
                               f"cannot derive x-request-id: {','.join(missing)} absent — refusing to POST")
    return None
```

`_create` calls it first and returns immediately on a non-`None` result. **Zero network calls occur.**
`TerminalFailure` → `failed` with a precise reason, visible in Review and re-queueable **once the data is
fixed** — and it will not silently loop, because `_requeue_failed_transient` only requeues
`is_transient_failure_reason` reasons, which this is not. Pinned by test #14 (asserts **zero** requests
issued).

**Rejected alternative:** defaulting a missing `created_at` to `""`, `post.id`, or a fresh stamp. Each
makes distinct incarnations collide on one id (or makes every attempt unique, silently disabling
idempotency). Failing loudly is the only correct behavior.

**Why not a CLAIM-minted attempt id:** it was the operator's sanctioned fallback and is unnecessary —
`created_at` satisfies both invariants (§8.3 proves immutability; §8.4 enforces presence). It would also
be **semantically wrong** for requirement 1: a CLAIM-minted id would change on every re-claim, so a
`_requeue_failed_transient` retry of the **same incarnation** would get a **new** UUID, violating "every
retry of one record incarnation produces the same UUID". It also costs a second new field, a `run.py`
CLAIM change, and the same legacy-absence problem. `created_at` is the smaller, more correct answer.
*(For completeness: the CLAIM txn already persists before any network I/O (`run.py:297`) and a failed
CLAIM returns `None` at `:267-268` → zero network calls. That guarantee already holds and is why no new
CLAIM work is needed.)*

---

## 9. State-transition table

`ZernioPoster.publish` writes these; `run.py` finalizes over `_NET_POST_FIELDS`.

| Result / event | From | To | `submission_id` | `reconcile_candidate_id` | Re-queueable? |
|---|---|---|---|---|---|
| CLAIM | `queued` | `submitting` | — | — | no |
| `Created` (Zernio: no permalink at create) | `submitting` | **`submitted`** → url gate → `needs_reconcile` | **real id** | — | **no** |
| `Created` + permalink present | `submitting` | `submitted` → `published` | real id | — | n/a |
| `IdempotentReplay` | `submitting` | **`submitted`** → url gate → `needs_reconcile` | **`existingPost.id`** | — | **no** |
| `ReconciliationRequired` (409 w/ candidate) | `submitting` | `needs_reconcile` | **unchanged** | **candidate** | **no** |
| `ReconciliationRequired` (409 no candidate) | `submitting` | `needs_reconcile` | unchanged | `None` | **no** |
| `ReconciliationRequired` (transport-after-send / 5xx / no-id / conflicting-ids / deadline-may-have-landed) | `submitting` | `needs_reconcile` | unchanged | `None` | **no** |
| `TerminalFailure` (other 4xx) | `submitting` | `failed` | unchanged | — | yes |
| `TerminalFailure` (**missing request identity**, §8.4) | `submitting` | `failed` | unchanged | — | yes, after data fix |
| `TerminalFailure` (deadline, nothing sent) | `submitting` | `failed` | unchanged | — | yes |
| `ZernioAuthError` | `submitting` | *run halts* | unchanged | — | — |
| Crash at any boundary | `submitting` | `submitting` | unchanged | unchanged | **no** — reconcile heals |
| Reconcile makes an explicit identity decision | `needs_reconcile` | `published` | real id | **cleared** | n/a |

`needs_reconcile` is never downgraded to `failed` and never auto-requeued (`_requeue_failed_transient`
reads `posts_in_state(failed)` only).

---

## 10. Production surface — narrowed

| # | File | Change |
|---|---|---|
| 1 | `post/zernio_outcome.py` **(new)** | the four private result types (§4.1) |
| 2 | `post/zernio.py` | `_ZERNIO_REQ_NS`, `_request_id`, `_require_request_identity`, header, `existingPost` parser, 409 branch, deadline, `_create` → result, `publish` maps to the ledger |
| 3 | `models.py` | `Post.reconcile_candidate_id` |
| 4 | `post/run.py` | **`_NET_POST_FIELDS += "reconcile_candidate_id"`** — finalize propagation **only** |
| 5 | `reconcile.py` | invariant guard: candidate is never a poll key / promotion source; cleared on an explicit identity decision |
| 6 | `studio/views_results.py` | render the candidate as **unverified reconciliation evidence** |
| 7 | tests | §11 |
| 8 | `docs/CODEMAPS/subsystem-traces/C6…`, `post/CLAUDE.md`, report 09 §7.5/§7.7 | owned docs |
| 9 | `.reports/architecture/derived/*`, `docs/ARCHITECTURE_GOVERNANCE.md` | regenerated (`tools.arch regen` + `docs`) — any line shift trips the drift gate |

**UNCHANGED — structurally and behaviorally:** `post/__init__.py` (`Poster.publish(led, post_id) -> Ledger`
stands), **`post/postiz.py`**, **`post/dryrun.py`**, `_is_transient_publish_error` (the 409 never reaches
it — it is a value, not an exception), the never-re-POST invariant, TikTok payload semantics, the 4 MB
cap, Postiz. **No `SCHEMA_VERSION` bump. No migration. No CLAIM change.**

### 10.1 Build deltas — where the implementation departed from this design

Recorded rather than quietly absorbed; each is a narrowing or a correction found while building.

| # | Design said | Built | Why |
|---|---|---|---|
| 1 | `_require_request_identity(post) -> ReconciliationRequired \| TerminalFailure \| None` | `-> TerminalFailure \| None` | The union was over-wide: no path returns `ReconciliationRequired`. A signature must not advertise a case that cannot occur |
| 2 | `match result: case Created(...)` | `isinstance` chain | House idiom: `match` appears **0 times** in `src/fanops/`. The design's `match` was illustrative pseudocode, not a style ruling |
| 3 | 4xx `TerminalFailure` evidence unspecified | **body deliberately WITHHELD** (as pre-fix) | **Found while building.** `error_reason` is substring-scanned by `is_transient_failure_reason` ("timeout", "network error", `\((\d{3})\)`). A 4xx body echoing `"upstream timeout"` or `"(503)"` would classify a **terminal** 4xx as transient and hand it to `_requeue_transient_failed_for_daemon` — a re-queue loop. Bodies ride only `ReconciliationRequired` reasons, which are never auto-requeued. Principled split; pinned by test 51. (Adding 4xx body evidence is a real follow-up — see §13.7 — but it is not free, and not this PR's approved surface) |
| 4 | — | `sent_any` flag in `_create` | §7 requires classifying past-deadline by *whether anything reached Zernio*. A `ConnectTimeout` on attempt 2 **after** a 429 on attempt 1 has sent something; the same timeout as attempt 1 has not. Without the flag both would read as "nothing sent" → a wrong `TerminalFailure` → re-queueable → double-post |
| 5 | §11: 61 tests | **72 tests** | 10 added while verifying (alias shapes, `Retry-After` parsing, key-redaction on the transport path, `_extract_409_candidate` shapes, the transient-classification control for §8.4) + 1 for the ratchet finding (row 9) |
| 6 | Surface item 6: `studio/views_results.py` | + `templates/_reconcile_strip.html`, + `static/studio.css` | "Render it distinctly" is not real without the template and the style. `--state-warn` (my first choice) **does not exist** in the stylesheet; `--warn` does, and is a different hue from `--state-inflight`, so the distinction holds on colour, not only on a dashed border |
| 7 | Surface item 8: C6 trace | + corrected the C6 block describing `_extract_zernio_media_url` / `/media/upload-token` | **Pre-existing staleness**: PR #694 deleted those, and the trace still documented them as live. Adjacent to the lines this change had to rewrite; a codemap documenting deleted code is worse than no codemap |
| 8 | §8.4: refuse when `created_at` is absent | unchanged — but **3 test fixtures completed** (`_post`, `_queued`, `_seed_queued`) | **Found by CI, not by planning** — 13 tests built `Post(created_at=None)` and were refused. The design's surface estimate missed this because `tests/` was never grepped for the field. The refusal is CORRECT and stays; the fixtures encoded a shape production cannot produce. Proven before touching them: all 3 mint sites stamp it; the live ledger has **0/347** rows missing it; and `ledger._migrate` is `while v < SCHEMA_VERSION`, so the v3 backfill **never runs on a current v11 ledger** — it is not a safety net, which makes this refusal the only guard. Each test's intent (401 halts, ConnectionError parks, 5xx parks, 429 retries) is unchanged and still proven |
| 9 | §4: the 409 candidate parser | + the parse failure is **logged and named in the evidence** | **The swallow ratchet caught a real defect in this change** (`zernio.py` 3 → 4 silent swallows). `except Exception: cand = None` collapsed two different facts — "Zernio named no post" vs "Zernio may have named one we could not read" — and only the second means the operator is missing a pointer that exists. Now `zernio_409_body_unparsed` + `(409 body unreadable: …)` in `error_reason`. Count back to 3, verified with the ratchet's own predicate. The new wording is pinned against `is_transient_failure_reason` (whose list contains **"unreachable"**) by test 28b |

---

## 11. Deterministic test matrix — BUILT: `tests/test_zernio_idempotency.py`

Offline, stubbed transport, no live call. CI-only per repo policy.

**Request identity (§8)** — 1 header present · 2 UUID-format parses · 3 **identical across the
`ConnectTimeout` retry** (R-1) · 4 **identical across the `429` retry** (R-1) · 5 identical after a
simulated process restart (recompute) · 6 **same record incarnation, every retry → same UUID**
(requirement 1) · 7 **pop a `failed` record + remint under the same `post.id` → different UUID**
(requirement 2, the D6 regression) · 8 **two resolved `account_id`s → different UUIDs** (requirement 3) ·
9 **two platforms → different UUIDs** (requirement 4) · 10 `_requeue_failed_transient` requeue → **same**
UUID (same incarnation) · 11 unchanged when `submission_id` is overwritten · 12 namespace + `_REQ_NAME_V`
pinned to literals · 13 `created_at` immutable through a full publish cycle · **14 legacy row with
`created_at=None` → `TerminalFailure`, `state=failed`, and ZERO requests issued** (requirement 5) · 15
missing `account_id` → same refusal · 16 the id and the payload's `accountId` always agree.

**Parsing** — 17 `201`+`{"_id"}` → `Created` · 18 `200`+`{"existingPost":{"_id"}}` → `IdempotentReplay`
(R-2) · 19 `existingPost` `id`/`postId` aliases · 20 `{"existingPost":{}}` → `ReconciliationRequired`, not
`TerminalFailure` · 21 `existingPost` not-a-dict → tolerated · 22 `200`+bare id → `Created` · 23 `{}` →
`success_no_id` · 24 non-JSON → no exception escapes · 25 **conflicting** `id` ≠ `existingPost.id` →
`conflicting_ids`, candidate `None`.

**409** — 26 `details.existingPostId` → `ReconciliationRequired`, candidate set · 27 `{"details":{}}` →
candidate `None` · 28 no `details` → no crash · 29 **409 never yields `failed`** (R-3 negative control) ·
30 **409 never raises** → `_is_transient_publish_error` never sees it · 31 **409 never writes
`submission_id`** (§5 negative control) · 32 409 → `needs_reconcile` survives `_publish_one` end-to-end.

**Deadline (§7)** — 33 `429` `Retry-After` inside deadline → retried, same id · 34 `Retry-After` beyond →
**no send**, `ReconciliationRequired` · 35 `ConnectTimeout` past deadline, nothing sent →
`TerminalFailure` · 36 `monotonic` (a wall-clock jump does not extend it) · 37 elapsed never exceeds
`_RETRY_DEADLINE_S` · 38 `_RETRY_DEADLINE_S < _IDEMPOTENCY_WINDOW_S`.

**Candidate field (§5)** — 39 round-trips through the ledger · 40 **in `_NET_POST_FIELDS`** (survives
finalize) · 41 old row without the key loads as `None` · 42 `SCHEMA_VERSION == 11` · 43 `extra="ignore"`
unbroken · 44 **reconcile never polls the candidate** · 45 **reconcile never promotes from the candidate**
· 46 candidate mirrored into `error_reason` · 47 reconcile clears it on an explicit identity decision · 48
UI renders it distinctly from `submission_id`.

**Preserved** — 49 `401` → `ZernioAuthError` halts · 50 `5xx` → `needs_reconcile` · 51 other `4xx` →
`failed` · 52 `ConnectTimeout` retries · 53 other `RequestException` → `needs_reconcile` · 54 CLAIM still
refuses a real-id post · 55 `needs_reconcile` never downgraded · 56 `_requeue_failed_transient` ignores
`needs_reconcile` · 57 **the four failed records need no migration** · 58 no secret/signed URL in any sink
· **59 `Poster` protocol signature unchanged** · **60 `postiz.py` untouched — byte-identical** · **61
`dryrun.py` untouched — byte-identical**.

**72 tests** (61 designed + 10 added while verifying + 1 for the ratchet finding, §10.1 rows 5/9). Every §6 row, every §9 transition, and all five §8 requirements have one. R-1/R-2/R-3, D6,
D7 and the §5 verdict each carry a negative control (#3/#4, #18, #29, **#7**, **#60/#61**, #31/#44/#45).

---

## 12. Invariants

| ID | Invariant | Status |
|---|---|---|
| I-1 | Publishable only from `queued`; only `approve_post` promotes | unchanged |
| I-2 | A real `submission_id` is never re-POSTed | unchanged |
| I-3 | `needs_reconcile` never → `failed`, never auto-requeued | unchanged |
| **I-4** | A 409 never yields `failed` and never raises | new |
| **I-5** | Every attempt of one **record incarnation × platform × resolved account** carries the identical valid UUID | new |
| **I-6** | A recognised replay is `submitted`, never `needs_reconcile` | new |
| **I-7** | `reconcile_candidate_id` is evidence only — never polled, promoted, attributed, or copied to `submission_id` without an explicit identity decision | new |
| **I-8** | No send after `_RETRY_DEADLINE_S`; the deadline is strictly inside the idempotency window | new |
| **I-9** | After any potentially accepted POST, no later pass POSTs again without an explicit reconciliation decision | §6 |
| **I-10** | **No request identity ⇒ no network call.** Never fabricate a discriminator | new, §8.4 |
| **I-11** | **`Poster.publish(led, post_id) -> Ledger` is unchanged; the typed result never escapes the Zernio backend** | new, §4 |
| I-12 | `AuthError` halts the run | unchanged |
| I-13 | No secret or signed URL in any sink | unchanged |

---

## 13. Residual risks

1. **`existingPost`'s shape is unproven** (prose-only, never schematised) — parser is tolerant and fails
   to `needs_reconcile`, so a miss is safe but unconfirmed. **Integration checkpoint.**
2. **409 ownership is irreducible** from the response (§3). `needs_reconcile` + evidence-only candidate is
   the correct terminal state.
3. **`_ZERNIO_REQ_NS` and `_REQ_NAME_V` are permanent.** Changing either silently re-opens R-1 for
   in-flight posts.
4. **A downgrade drops `reconcile_candidate_id`** (`extra="ignore"`). Fail-safe; mitigated by the
   `error_reason` mirror.
5. **`created_at` for v2-migrated rows is synthetic** (`scheduled_time`, else a migration stamp). It is
   still stable and per-row, so it discriminates correctly **going forward**; it is not a true birth time
   for those rows. It never needs to be — only stability and per-incarnation freshness matter.
6. **Unproven against the live API.** Derived from the OpenAPI spec + code. Per
   [[read-the-openapi-spec-not-the-guides]], one source is not proof — the first live publish remains an
   operator-gated checkpoint.

---

## 14. Implementation gate — **DISCHARGED**

`APPROVE IDEMPOTENCY IMPLEMENTATION` was returned on 2026-07-17. It authorized the §10 items and the §11
tests, landed as one PR — and **nothing else**. Held, and verified held:

| Not authorized | Evidence |
|---|---|
| A Zernio call | Every test stubs `requests.post`; no live verb was run |
| A requeue / approve / schedule / publish | No ledger of the operator's was opened; the four `failed` records are byte-unchanged |
| A Postiz change | `post/postiz.py` blob-identical to `origin/main` |
| A TikTok-payload change | `_tiktok_settings` untouched (the guide/OpenAPI conflict stays preserved, not "fixed") |
| An upload-cap change | `FANOPS_ZERNIO_MAX_UPLOAD_MB` untouched |

### The next gate — **requeueing the four burned records**

Still closed, and it is a **separate approval**. This PR makes a requeue *safe to consider*; it does not
make it approved. The remaining preconditions:

1. This PR **merged** and **deployed** (the keeper adopts on git HEAD moving; `_adopt_settle_s` ≈ 720s).
2. The **first live publish** confirms the two unproven shapes (§13.1 `existingPost`, §13.6) — an
   operator-gated checkpoint, because a spec read is not a live proof ([[read-the-openapi-spec-not-the-guides]]).
3. An explicit operator token naming the four record ids.

**Ground 4 of report 09 §7.9 — "zero live exposure, `queued = 0`" — is what a requeue spends.** That is the
whole reason this had to land first.
