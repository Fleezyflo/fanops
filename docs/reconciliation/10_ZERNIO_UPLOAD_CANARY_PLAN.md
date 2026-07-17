# 10 — Zernio Upload-Only Canary — Plan and Result

> **Rev 4 — 2026-07-17. EXECUTED.** Gate token `APPROVE UPLOAD CANARY` returned; the canary ran once and the
> supported claim is **`LIVE UPLOAD CONTRACT VERIFIED`** — **the result is §10, its exact boundary is
> §10.4.** §§0-9 are the plan as approved, retained verbatim as the pre-execution record.
>
> **Rev 4 corrects an overclaim in Rev 3**, which read the result as byte identity. The canary read **no
> stored bytes** and computed **no hash**; it proved *a retrievable media object of the expected declared
> length and media type at the server-returned URL* — **not byte-level identity** (§10.4). The canary is
> **not** rerun to strengthen this; the existing live proof settles the upload-contract decision.
>
> *(Rev 2's header read "NOT EXECUTED. No Zernio API call has been made." True when written, **now false** —
> superseded by §10. Left recorded rather than deleted: it is what was approved.)*

| Field | Value |
|---|---|
| **Purpose** | Prove the **presign + signed PUT** contract against the **live** Zernio API — the one thing report 09 could not: *"the contract is read from the spec and never exercised"* |
| **Scope** | **Upload only.** One disposable asset. **No post is created** — not by policy, structurally (§3.3) |
| **PR under test** | #694, branch `fix/zernio-presign-upload`, **OPEN / unmerged / MERGEABLE**, 10 changed files |
| **Code under test** | `src/fanops/post/zernio.py` — blob `acccd311effc2e441dd1b5675c3cd8b77759572c`, sha256 `7e38608d1a92e7b6de92d8ab9bc25fc3c07663bd99510911732f4523adc6a63f` (§2) |
| **Runner** | `zernio_upload_canary.py`, sha256 `ca31aaf1bfad628a9453c264e310853d4cb8594a15c9fc19c53e738eb90a1120` — outside git, outside `FANOPS_ROOT` (§7) |
| **Call ceiling** | **Exactly 3**: 1 `POST /media/presign` · 1 signed `PUT` · 1 streamed `GET` (§4) |
| **Blast radius** | 1 temporary object in Zernio's media storage. **No ledger row, no corpus file, no social post, no account touched** |
| **Reversibility** | **Partial — §8.** The object cannot be deleted by us; it expires unreferenced |

---

## 0. Rev 1 defects — retracted

Five, four of them found by building and validating the runner rather than by re-reading the plan.

| # | ⛔ RETRACTED Rev 1 claim | Correction | Found by |
|---|---|---|---|
| R1 | *"PR under test: #694 @ `0b5a7248…`"* | **Stale.** Head is `00da9f82…`; `0b5a7248` is the *previous* commit. The stamp was stale **in the commit that introduced it** — see §1.1 | operator |
| R2 | *"changed files: 9"* | **10.** Rev 1 counted before its own file existed | operator |
| R3 | *"at most 3 HTTP requests"* alongside a `HEAD` **plus** a `GET` fallback | **Contradiction — that path is 4.** Replaced by exactly one streamed `GET` (§4) | operator |
| R4 | §5 assertion 4: *"Ledger SHA-256 identical before and after"* — *"the strongest available proof of no ledger mutation"* | **Unsound, and it would have flaked.** The live ledger is SQLite in **WAL mode** and the daemon writes every ~600s. A whole-file hash is wrong in both directions: committed rows can sit in the `-wal` while the main file is byte-identical, and a checkpoint can rewrite the main file with no logical change. Replaced by a **logical posts-map digest** (§6) | building it |
| R5 | §5 assertion 5's `FANOPS_CORPUS_AUTO` gate | **Could only ever pass** — `os.getenv` on a variable this process never loads, defaulted to the value being asserted. Now read from the live `.env`, absent = abort (§6.1) | validating it |

> **R4 is the one that mattered.** Rev 1 called a byte hash *"a digest, not a promise"* — but it was a digest
> of the wrong thing. The live `ledger.sqlite` mtime moves on daemon ticks, so the Rev 1 gate would have
> aborted the canary on benign activity, and an operator who sees a safety gate cry wolf learns to bypass it.

## 1. Pinning the exact code under test

### 1.1 Why no tip SHA is hard-coded in this file

A commit SHA written into a tracked file is **invalidated by the commit that writes it**: recording head
`00da9f82…` here and committing produces a *new* head, so the field is stale on arrival. That is exactly how
Rev 1's `0b5a7248…` became wrong — it was accurate when typed and false when committed. Re-typing today's SHA
would reproduce the defect, not fix it.

So this file pins what **does not move**:

| Pin | Value | Stable because |
|---|---|---|
| Branch | `fix/zernio-presign-upload` | — |
| `zernio.py` **git blob** | `acccd311effc2e441dd1b5675c3cd8b77759572c` | content-addressed; unchanged by any *documentation* commit |
| `zernio.py` **sha256** | `7e38608d1a92e7b6de92d8ab9bc25fc3c07663bd99510911732f4523adc6a63f` | ditto — and it is what the *loaded module* is hashed against |
| Last commit touching the code under test | `0b5a7248517bd324ed115331e12dc1ebc08645a9` | moves only when the code moves |

**The tip SHA is proven at execution, not asserted here:** §1.2 re-derives `git rev-parse HEAD` and
`gh pr view 694 --json headRefOid` and **aborts unless they are equal**. Equality with the live remote is a
stronger guarantee than any transcribed constant, and it cannot go stale.

### 1.2 Preflight proofs — all before any **Zernio/media** request. Abort on any mismatch.

> ⛔ **RETRACTED:** *"all before any network."* **False.** Proof 1 runs `gh pr view`, which **is** a network
> request — a separate read-only GitHub metadata lookup (§3.4). Preflight is before any **Zernio/media**
> call, which is the claim that was meant and the only one that is true.

| # | Proof | Abort condition |
|---|---|---|
| 1 | `git rev-parse HEAD` **==** `gh pr view 694 --jq .headRefOid` | any inequality — the approved code is not what is checked out |
| 2 | branch **==** `fix/zernio-presign-upload` | any other branch |
| 3 | PR state **OPEN**, `mergedAt` **null** | merged or closed → this plan's premise is void |
| 4 | `git status --porcelain --untracked-files=no` **empty** | any uncommitted **tracked** change |
| 5 | `fanops.post.zernio.__file__` resolves to `<repo>/src/fanops/post/zernio.py` | resolves anywhere else (a site-packages copy would test the wrong bytes) |
| 6 | loaded module **sha256 == the §1.1 pin** | any drift |
| 7 | worktree blob **== HEAD blob == the §1.1 pin** | any drift |
| 8 | **re-hash of `zernio.py` immediately before the first byte leaves** (§5) | any change between proof 6 and call 1 |

> **Untracked files are disclosed, not hidden.** 16 untracked files exist (`docs/constitution/`,
> `docs/reconciliation/01`–`05`) — a different, superseded program. They are **not** in PR #694 and cannot
> alter tracked module content, so proof 4 scopes cleanliness to **tracked** files and the runner records the
> untracked list rather than asserting a blanket "clean worktree" that would be false.

## 2. Operational classification — stated honestly

> ## ⚠ UNMERGED PR CODE IS ALREADY ACTIVE IN THE RESIDENT DAEMON VIA EDITABLE INSTALL
>
> **This is not "not deployed."** No deployment *action* was performed — but **runtime adoption occurred.**

| Fact | Evidence |
|---|---|
| `fanops` is an **editable install** resolving into this worktree | `fanops.__file__` → `/Users/molhamhomsi/Moh Flow Fanops/src/fanops/__init__.py` |
| The resident daemon runs that entrypoint, live | `.venv/bin/fanops run --loop --interval 600` · `FANOPS_LIVE=1` |
| `zernio.py` was last written at **10:43:13** and has not moved since | mtime; and worktree blob == HEAD blob == the §1.1 pin |
| **Every daemon start after 10:43:13 therefore imports the PR-head presign code** | no `src/**/*.py` has been modified since the current instance started |
| **Restarts are frequent** — three observed in 50 minutes | PID 7359 @ 10:49:23 → 12773 @ 11:01:26 → 30914 @ 11:39:47 |
| The restarts are **automatic** | `com.fanops.run` plist: `KeepAlive`, `RunAtLoad`, `ThrottleInterval 60`. Last exit status `-15` (SIGTERM) — something reaps it and launchd respawns it |
| **The Studio is also resident** | `com.fanops.studio` PID 30916, same `-15` restart pattern |

> **The invariant, not the snapshot.** The PIDs and timestamps above rot; the invariant does not: *`zernio.py`
> has not changed since 10:43:13, so any start after that moment loads it, and launchd guarantees there is
> always a start after any given moment.* **Runtime adoption is continuous, not a one-off.** "Not merged" does
> **not** mean "not loaded on the operator's machine."

**The Studio being resident is what makes §2.1 a real hold rather than a formality**: the Review tab's Approve
button — the sole promoter into `queued`, and therefore the sole route from this un-canaried code to a live
Zernio call — is currently one click away and reachable.

> **Out of scope, recorded not chased:** the SIGTERM/respawn cycle on `com.fanops.run` and
> `com.fanops.studio` predates this work and is not caused by it. It does not affect containment (`queued=0`
> holds across restarts — it is ledger state, not process state). It is noted here only because it is *why*
> adoption is continuous, and it is not investigated in this frame.

**Why containment nevertheless holds — structurally, not by luck:** `publish_due` iterates `queued` only;
`Ledger.approve_post` is the sole promoter into `queued`; it fires only from the Studio Review tab. With
**`queued=0`**, `_publish_one` is never entered and `zernio_upload_media` is unreachable.

**The consequence to own:** the Studio Approve button is now the only thing between un-canaried code and a
live Zernio call.

### 2.1 The operator hold attached to `APPROVE UPLOAD CANARY`

Approving the canary **means holding all of the following** until the result is returned:

- **no Studio Approve** (`approve_post` / `approve_clip` / `approve_account`)
- **no Publish now**
- **no requeue** of the four failed records — or any record
- **no scheduling mutation** (Move, Use suggested, Clear time, Reschedule all)
- **no CLI publication action** (`fanops publish`, `fanops run` invoked by hand, `fanops resolve`)
- **no interaction that can create a `queued` post**

Any one of these makes `queued != 0`, and the runner **aborts before the next external request** (§6).

### 2.2 What the canary must not do to the daemon

**Do not stop, restart, checkout, merge, or mutate the daemon during this canary.** The runner does none of
these: it launches only `git`, `gh`, `ffmpeg`, `ffprobe`, `file` (§7, allow-listed and AST-asserted), and it
reads the ledger through a `mode=ro` URI. The daemon keeps ticking throughout; that is expected and fine.

## 3. Why upload-only, and why it cannot publish

### 3.1 The residual this closes

The 405 was a **routing verdict on `(method, path)`**. Report 09 replaced that pair **from the OpenAPI spec
alone**. *A spec is a claim about a server, not the server.* Report 09 §11.5 names this residual; no further
reading closes it.

### 3.2 Why not exercise `POST /posts` too

`POST /posts` is **unchanged by this PR** (report 09 §6.2 — post creation was already correct). Calling it
would add real publish risk while proving nothing about the fix.

### 3.3 It cannot publish — structurally

The runner never constructs a `ZernioPoster`, never builds a payload, and **every Zernio/media request**
passes through a single chokepoint that **raises on the forbidden path segment before the socket is
written** (§5). Validated against a synthetic request: **blocked, 0 sends.**

> ⛔ **RETRACTED:** *"every outbound request passes through a single chokepoint."* **False** — see §3.4.
> The scoped claim above is the true one, and it is the one the safety argument needs: the forbidden path
> is on the Zernio data plane, which the chokepoint does cover completely.

### 3.4 Network accounting — corrected

`Session.send` is patched in **this interpreter's** `requests`. It cannot see another OS process.

| Operation | Plane | Through `Session.send`? | In the ceiling? |
|---|---|---|---|
| **`gh pr view 694`** (preflight) — read-only GitHub metadata | GitHub control-plane | **NO** — `gh` is a compiled binary in a **separate process** | **NO — outside it** |
| `POST /media/presign` · `PUT <uploadUrl>` · `GET <publicUrl>` | Zernio/media data-plane | **YES, all three** | **YES** |

- **The Zernio/media data-plane ceiling is exactly 3.**
- **`gh pr view` is one separate read-only GitHub metadata request, outside that ceiling, and does not pass
  through `Session.send`.**
- **All Zernio/media requests do pass through the `Session.send` chokepoint.**

The `git` subprocesses are genuinely local — the runner's only subcommands are `rev-parse`, `hash-object`,
`status`; **network-capable subcommands (`fetch`/`pull`/`push`/`ls-remote`/`clone`/`remote`): none.**
`ffmpeg`/`ffprobe`/`file` are local.

## 4. The data plane is a 3-STAGE SEQUENCE, not a call budget

| Stage | Call | Destination — matched **exactly** | Auth |
|---|---|---|---|
| **1** | `POST` | **exactly** `https://zernio.com/api/v1/media/presign` (§4.2) | **Bearer REQUIRED** — the token's only legitimate destination |
| **2** | `PUT` | **exactly** the `uploadUrl` presign returned; https; **signed query required**; no userinfo/fragment | **NONE** — any `Authorization` aborts |
| **3** | `GET` · `stream=True` · `Range: bytes=0-0` · body never iterated · closed immediately | **exactly** the **validated** `publicUrl`; https; no query/fragment/userinfo | **NONE** — any `Authorization` aborts |
| — | **the post-creation path** | — | **0 — FORBIDDEN.** Checked first, on every request. **An assertion, not a convention** |

**Total: exactly 3 requests. No retry, no fallback, no loop.** Aborts **before the socket write** on: wrong
order · wrong destination · a redirect · a repeated stage · an unlisted method · a 4th request.

### 4.1 Why a sequence and not a permission set

The Rev 2 design granted **independent per-request permissions** — *one POST is allowed, one PUT is allowed,
one GET is allowed.* That admits **any order and any destination within those permissions**: a redirect, a
replayed stage, or a swapped target all look legal to it, because it only ever asks *"is this method still
under budget?"*

But this canary makes exactly three calls **whose order and destinations are known in advance**. The honest
contract is therefore a **sequence**, and everything off it is refused by default. **Method budgets are
retained only as a secondary guard** — behind the stage machine they are unreachable, which is the point:
the authoritative check is the one that can't be satisfied by a well-formed request to the wrong place.

### 4.2 The bearer token has exactly one destination

`zernio.py` builds the presign url — and thus the `Authorization` header's destination — from
`cfg.zernio_url`, which reads the **operator-settable `ZERNIO_API_URL`**. An inherited or edited value would
silently re-point the bearer token at another host with nothing in the output to say so. So, **before**
`Config` is constructed: the inherited variable is dropped, the live `.env` is read and required to be
**absent or exactly** `https://zernio.com/api/v1` (trailing-slash normalised), and the expected base is set
explicitly rather than relying on `Config`'s default. **After** construction, `cfg.zernio_url.rstrip("/")` is
asserted equal to it. Stage 1 then matches the **full presign url string** — not a prefix, not a hostname.

### 4.3 The credential is the one production would send

`Config.zernio_api_key` → `resolve_secret("ZERNIO_API_KEY", <env value>)` → *"Keyring wins when set; else
return fallback."* **The effective key is therefore not necessarily the `.env` value.** The runner reads the
`.env` fallback unconditionally (overwriting any inherited shell value), constructs `Config`, and resolves
`effective_key = cfg.zernio_api_key` — the credential production actually sends — requiring it non-empty.

**This is what the failure sink redacts with.** Keyed to `os.environ["ZERNIO_API_KEY"]` instead, the sink
would sail past a keyring-sourced credential and write it to disk *while reporting itself redacted*.
Demonstrated, not argued: with a distinct keyring value, the old expression emits the key verbatim and the
new one emits `***`.

Only the **source label** (`keyring` | `env-fallback`) is recorded — derived from `get_secret(...) is not
None`, the same predicate `resolve_secret` uses, so no value comparison is needed. **No prefix, no length,
no hash, no value** of any credential is printed or recorded.

### 4.4 The accessibility check — one request, replacing Rev 1's HEAD+fallback

Rev 1 allowed `HEAD`, then `GET` if `HEAD` returned 405 — a 4-request path under a "3 request" headline (R3).
Rev 2 removes `HEAD` entirely:

```python
r = requests.get(public_url, stream=True, headers={"Range": "bytes=0-0"}, timeout=30)
try:    status, ctype, clen, crange = r.status_code, r.headers.get("Content-Type"), \
                                      r.headers.get("Content-Length"), r.headers.get("Content-Range")
finally: r.close()          # release WITHOUT consuming the body
```

- **`stream=True` + never iterating + `close()` in `finally`** — the body is never downloaded.
- **`Range: bytes=0-0`** — asks for one byte. A compliant server answers **206**; one that ignores `Range`
  answers **200** and we close before reading.
- **No retry and no fallback.** A failure is a canary **failure** (§9.3), reported as-is.

### 4.5 The object is validated, not just the status code

**"Any 2xx" is not a proof of accessibility** — a defect in Rev 2's first draft, caught in review. A **204**
has no body by definition, and a CDN error page is routinely served as **200 `text/html`**. Either would have
produced a **false `UPLOAD CONTRACT VERIFIED`** from a check that *recorded* `Content-Type` and
`Content-Range` and then asserted neither.

| Check | Rule | Why this one |
|---|---|---|
| **Status** | must be **200 or 206** — not "any 2xx" | 204/205 carry no body; accepting them proves nothing |
| **Declared length** | **206** → `Content-Range: bytes 0-0/<total>`, `total` **== the PUT byte count** · **200** → `Content-Length` **== the PUT byte count** | **The load-bearing invariant** — and a **declared** one. It is what the server *reports* about the object, not a measurement of it: the body is never read, so this rules out an error page (which fails on length alone) but **cannot establish byte identity**. See §10.4 |
| **Not an error document** | `Content-Type` must not be `text/html` / `*/xml` | belt-and-braces against a 200-with-apology-page |
| **`Content-Type` is `video/*`** | **recorded DEVIATION, not a failure** | `contentType` *is* part of the presign contract, so a mismatch is a real finding — but a length-correct object served as `octet-stream` **is still our file**. Failing the canary there would answer a different question than the one it exists to ask |

> **Redirects abort — they cannot be followed.** `requests` re-enters the chokepoint per redirect hop, so a
> hop arrives as the *next stage* and fails that stage's exact-destination match (and usually its method
> too). A surprise redirect is a **finding**, not a free extra call — and, more importantly, a redirect off
> stage 1 can no longer carry the bearer token anywhere, because stage 1 is pinned to one full url string.

## 5. The chokepoint

**Every Zernio/media request** — however it is constructed — funnels through `Session.send`, which receives
the **final `PreparedRequest`**: true URL, true merged headers. Raising there happens **before the socket
write**. Scope is §3.4: this patches *this interpreter's* `requests`, so `gh` (another process) is outside it.

> ⛔ **RETRACTED:** *"Every outbound request … funnels through `Session.send`"* and *"all before any
> network."* Both **false**, both corrected above and in §3.4.

Per request, in order, all before the socket write:

| # | Check | Rationale |
|---|---|---|
| 1 | url carries the forbidden segment → **abort** | after-the-fact detection means it already happened |
| 2 | **scheme is not `https` → abort, not sent** | plaintext would expose the Bearer key (presign) or the signed query (PUT) on the wire; a downgrade is what an intercepted endpoint looks like |
| 3 | **STAGE MACHINE (§4) — authoritative**: stage = `len(sent)+1`; method, **exact destination**, and per-stage auth rule all enforced → else **abort** | order *and* destination. Catches redirects, replays, swapped targets, unlisted methods, and a 4th request — none of which a permission set can see |
| 4 | per-method count ≤ budget (default **0**) → **abort** | **secondary only.** Unreachable behind check 3; retained as defence in depth |
| 5 | `zernio.py` sha256 == the §1.1 pin → else **abort** | closes the gap between preflight and the first API call |
| 6 | **runner sha256 == the caller-supplied `FANOPS_CANARY_RUNNER_SHA256` → else abort** | binds execution to the **reviewed** bytes. Re-checked per stage, so a mid-run edit to the guard cannot let the next request through unchecked. **The expected hash is never a constant in the file** — a file cannot hold its own sha256, and an in-file pin is edited by the same hand as the code, so it could never detect the edit it exists to detect |
| 7 | live-ledger interlock (§6) → **abort** | re-read at the last possible moment before each request |

Stage 2's expected destination is captured from **presign's own response body**, so the match is against
what the server actually returned rather than a shape. Stage 3's is the **validated** `publicUrl` (§7.4).

**Validated with synthetic requests, 0 sends:** bearer to another host · bearer to a wrong path on the right
host · bearer to presign+query · presign without auth · PUT/GET first · repeated POST (a stage-1 redirect) ·
PUT to a different url · PUT unsigned · PUT with `Authorization` · GET to a different url (a redirect) · GET
with `Authorization` · repeated PUT · a 4th request · `DELETE` · `HEAD` · plaintext presign · the
post-creation path — **every one blocked before the socket write**.

### 5.1 `safe_url` never emits `netloc`

`safe_url` is what **every other redaction path calls** to make a url printable — so a leak here defeats all
of them at once, and each caller still believes it is protected. It emitted `p.netloc`, which is
`user:pass@host:port`: **userinfo would have been printed verbatim.**

The authority is now **reconstructed** from `p.hostname` (which strips userinfo by construction) plus a
**validated** port (`p.port` raises on a malformed one, reported as `:<invalid-port>` rather than echoed),
with IPv6 literals re-bracketed since `.hostname` unwraps them. Never emitted: **username · password · raw
netloc · query values · fragment.** Proven with distinct sentinels: absent from stdout, exceptions, and the
result JSON; no `@` in any output.

## 6. Ledger interlock — detection, logical, and honest about its limit

Re-read from the **live** ledger over a `file:…?mode=ro` URI (writes are *impossible*, not merely unused), at
**four** points: **before presign · before PUT · before the GET · after completion** (plus a baseline). The
first three are the chokepoint's check 5, so they land at the last instruction before each request.

> ### ⚠ This is DETECTION, not PREVENTION — the Rev 2 draft over-claimed
>
> Rev 2 first said `queued == 0` holds **"throughout"** the canary. **A read-only check cannot promise that.**
> It is a **TOCTOU** window: an operator clicking Approve in the (resident) Studio between the read and the
> socket write would be caught only by the *after* check — i.e. after the fact. Caught in review; the
> guarantee is downgraded here rather than dressed up.
>
> **The claim, corrected:** `queued == 0` is proven **at four instants**, not across the interval.

**Why no lock is taken — deliberately, not by omission.** `Ledger.approve_post` takes the ledger flock, so the
canary *could* hold it end-to-end. That would **block the live daemon across network I/O** for the canary's
duration — precisely the anti-pattern `_publish_one`'s three-phase claim/network/finalize shape exists to
avoid. Trading a live-daemon stall for a tighter safety claim about a colour-bar upload is a bad trade.

**What actually carries the guarantee**, since the check does not:

| | |
|---|---|
| The **daemon cannot self-promote** | `Ledger.approve_post` is the sole promoter into `queued`, and only the Studio calls it |
| So **only a human click can open the race** | which is exactly what the §2.1 operator hold covers — the hold *is* the mechanism, the check is the audit |
| The canary's **own 3 requests are unaffected either way** | it never publishes; a concurrent approval changes nothing about what it sends |

**The residual is a reporting one**, and it is accepted: a mid-canary approval would be reported by the next
check, and the canary aborts before its remaining requests.

| Invariant | On drift | Why |
|---|---|---|
| **`queued == 0`** | **ABORT** | a `queued` post is publishable by the live daemon — the one state that must never coexist with an un-canaried publish path |
| **failed-id set == the same 4** | **ABORT** | proves no requeue and no re-burn |
| **`FANOPS_CORPUS_AUTO == 0`**, read **from the live `.env`** | **ABORT** (also if the key is absent) | operator-pinned. **Not `os.getenv`** — see §6.1 |
| **posts-map logical digest** — sha256 over `(row_id, canonical-JSON payload)` for every `posts` row, ordered | **ABORT** | a post mutated mid-canary |
| whole-file `ledger.sqlite` sha256 | **recorded, not fatal** | **WAL mode + a live daemon** — the file moves on benign writes to *other* maps (sources/moments/clips). Gating on it is the R4 defect |

> The **logical** digest is the sound invariant: immune to WAL placement and checkpoint rewrites, and it
> changes **iff a post changes** — which is the actual question.

### 6.1 A fifth Rev 1 defect, found by validating the runner

The `FANOPS_CORPUS_AUTO` gate was written as `os.getenv("FANOPS_CORPUS_AUTO", "0") != "0"`. **It could only
ever pass.** The runner deliberately loads *only* `ZERNIO_API_KEY` from the live `.env`, so the variable is
absent from its environment, `os.getenv` returns `None`, the `"0"` default is substituted, and the comparison
is vacuously satisfied — *including in the case the gate exists to catch*, where the operator has set it to
`1` in the `.env`. Proven: `os.getenv(...)` → `None` · live `.env` → `'0'`.

**Fixed** to read the file the daemon actually reads, with an **absent key treated as an abort** rather than
a default. Third instance in this program of a check that could not fail (after the Rev 4 phrase checker and
§7.1's self-scan). **A gate whose passing carries no information is worse than no gate — it reports safety it
never measured.**

**The runner imports no ledger mutation method** — AST-asserted: the name `Ledger` is never bound or
referenced, and nothing is imported from `fanops.ledger`. (The class is unavoidably in `sys.modules` because
`zernio.py` imports it at module scope; that is a property of the code under test, so claiming "the ledger is
not imported at all" would be false. The checkable claim is that **this runner cannot call it**.)

## 7. The pinned runner

| Property | Value |
|---|---|
| **sha256 (reviewed)** | `ca31aaf1bfad628a9453c264e310853d4cb8594a15c9fc19c53e738eb90a1120` |
| **Location** | session scratchpad — **outside git**, **outside `FANOPS_ROOT`** (`/Users/molhamhomsi/FanOps`) |
| **Execution command** | `FANOPS_CANARY_RUNNER_SHA256=ca31aaf1bfad628a9453c264e310853d4cb8594a15c9fc19c53e738eb90a1120 .venv/bin/python "<RUNNER_PATH>"` — from the repo root. `<RUNNER_PATH>` is **deliberately not written here**: see §7.5 |
| **Runner byte-pin** | The reviewed hash is **supplied by the caller**, checked at preflight **and again before every one of the 3 data-plane requests**. **Unset = abort.** See §7.2 |
| **Subprocesses** | AST **allow-list**: `git`, `ffmpeg`, `ffprobe`, `file`, `gh` — **no `fanops` CLI**. An allow-list, because a negative scan only rules out the name you thought to forbid |
| **Forbidden-path self-scan** | **AST over non-docstring string literals** — see §7.1 |
| **Ledger API references** | **0** (AST-asserted) |
| **Secrets** | `.env` fallback read **by key name, always overwriting any inherited value** (§7.3), then the **effective** credential resolved via `Config` (**keyring → `.env`**, §4.3). Only the **source label** is recorded. Nothing else is read from `.env` except `FANOPS_CORPUS_AUTO` and `ZERNIO_API_URL` |
| **Never printed** | any credential **value, prefix, length, or hash** · `uploadUrl` · `uploadUrl` query values · **username / password / raw netloc** (§5.1) · signed exception request objects · `repr(e)` · traceback · request/response/headers/locals |
| **Bearer destination** | pinned to **exactly** `https://zernio.com/api/v1/media/presign` — before *and* after `Config` (§4.2) |
| **Data plane** | **3-stage sequence** (§4), authoritative over method budgets |
| **`publicUrl`** | **validated before being printed, recorded, or fetched** (§7.4) |

### 7.1 The self-scan had to be fixed to mean anything

A raw substring scan for the forbidden segment **matched this runner's own docstring prose** and aborted the
preflight. That is a defect in the *check*, not a finding about the code — a rule that parses a request path
out of English proves nothing. (Same class as the Rev 4 sweep's line-based phrase checker, which also matched
prose. **Second occurrence in this program: text scans over prose are a recurring false-positive source.**)

Rewritten to assert what it means: parse the AST, take every string constant that is **not** a docstring,
assert none carries the segment. Comments never reach the AST. The guard token is built as `"/" + "posts"`,
whose constants are `"/"` and `"posts"` — neither matches — so the check cannot self-trip.

**Negative controls — the check is real, not decorative:**

| Input | Result | Meaning |
|---|---|---|
| the real runner | `[]` | clean |
| the segment in **docstring prose** | `[]` | prose is not a request path |
| the segment in **a comment** | `[]` | comments are not request paths |
| **a real `requests.post("https://zernio.com/api/v1/posts")`** | **HIT at line 2** | **it catches the thing it exists to catch** |

### 7.2 Why the runner's expected hash is caller-supplied, never a constant

A SHA-256 in the review package **identifies** the runner; it does not **bind execution to it**. Nothing
stopped an edited runner from being run after review.

The pin is now `FANOPS_CANARY_RUNNER_SHA256`, checked at preflight and again before each of the 3
data-plane requests. **The expected value is never stored in the file**, for two reasons: a file cannot
hold its own sha256 (writing the constant changes the hash it claims — self-referential and unsatisfiable),
and an in-file pin is edited by the same hand as the code, so it could never detect the edit it exists to
detect. Only an **out-of-band** expectation — fixed at review time, passed in at run time — binds execution
to the reviewed bytes. **Unset is an abort, never a default**: a pin that silently skips when absent is not
a pin. Re-checking per request means a mid-run edit to the guard itself cannot let the next request through.

### 7.3 No ambient-key precedence

`load_key_by_name` previously began `if os.getenv("ZERNIO_API_KEY"): return`, which let the **invoking
shell** decide which credential the canary used. The result would then describe whatever key happened to be
exported rather than the configured one the daemon publishes with, and the two could differ silently. **A
canary whose credential source depends on how you launched it is measuring the wrong system.** The live
`.env` is the daemon's source, so it is now unconditionally the canary's source; an empty value aborts.

### 7.4 `publicUrl` is validated before it is printed, recorded, or fetched

`publicUrl` was recorded verbatim on the reasoning that it is "unsigned and intentionally public." That is
an assumption about a **server response** — from the very server whose documented behaviour this canary
exists to test, having already answered 405 where the docs promised otherwise. It is now checked:
**`https` scheme · non-empty hostname · no userinfo · no query string · no fragment.** A query string on a
"public" url would mean it carries credentials, and logging it verbatim would leak them. On violation the
canary aborts, emits **only `safe_url()`**, records nothing raw, and never issues the GET. The chokepoint
separately refuses any non-`https` Zernio/media request before the socket write.

### 7.5 Why the runner path is a placeholder here

The runner lives in a **session scratchpad** whose path contains a session UUID. Writing that absolute path
into a tracked file would document a location that **ceases to exist when the session ends** — the same
self-invalidating-stamp defect as R1's head SHA and §2's daemon PID. **Third occurrence in this program:
anything transient, written into a tracked file, is stale on arrival.** The rule that keeps surviving:
*record the invariant; re-derive the transient at run time.*

The invariants are the ones that matter and they are all here: the runner's **reviewed sha256**, the fact
that it sits **outside git and outside `FANOPS_ROOT`**, and the **`FANOPS_CANARY_RUNNER_SHA256`** pin the
caller must supply. The path is operator-supplied at execution and is recorded in the run's evidence output,
not in this file.

**The ephemerality is a feature, not an inconvenience.** The runner is outside git *by design*: it is a
one-shot instrument, not a repo artifact, so there is deliberately no checkout it can be run from and no
committed copy to drift out of review. A tracked path would invite exactly the "just run the one in the
repo" mistake the byte-pin exists to prevent.

## 8. The asset, and the cleanup limitation

```bash
ffmpeg -y -f lavfi -i testsrc2=size=256x256:rate=15 -f lavfi -i anullsrc=r=44100:cl=mono \
       -t 2 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$SCRATCH/zernio-canary.mp4"
```

| Property | Value |
|---|---|
| **Content** | `testsrc2` — a synthetic **colour-bar test pattern** + silent audio, generated from nothing |
| **Private / unreleased / production content** | **NONE.** No Moh Flow footage, no catalogue audio, no clip, no render, no source. Not derived from any corpus file |
| **Measured** | **54,770 bytes** · **`video/mp4`** · **2.000000 s** (validated build) |
| **Recorded at run time** | **sha256** (full hex) · **MIME** (`file --mime-type`) · **exact byte size** (`st_size`) · **duration** (`ffprobe`) |
| **Location** | scratchpad — outside `FANOPS_ROOT`, outside `01_inbox`/`02_sources`/`03_clips`, outside git |
| **Ledger** | **Never catalogued.** No `Source`, `Clip`, `Render`, or `Post` row |
| **`maybe_shrink_for_cap`** | **proven no-op**: 54,770 < 4,194,304 → the `size <= cap` short-circuit returns the path unchanged. No ffmpeg re-encode, no `04_agent_io` write. Asserted before the call |
| **`Config(root=…)`** | scratch root; `root`/`base`/`ledger_path` each **asserted outside** the live `FANOPS_ROOT`, and `ledger_path` asserted **non-existent** — no ledger is opened |

### 8.1 Cleanup — stated, not discovered

**We cannot delete the uploaded object.** No documented client-side delete for a presigned temp object exists
in the OpenAPI spec (S0) or the media guides.

| Fact | Consequence |
|---|---|
| Zernio media is **temporary (~7 days)**, made permanent **only** when referenced by a published post | The canary object is **never referenced** → never made permanent → **expires unreferenced** |
| No delete endpoint | **The object persists until Zernio's expiry.** A real, accepted, bounded residual |
| The object is a **colour-bar test pattern** | The residual is: *one 2-second test-pattern file in the operator's own Zernio media storage for up to ~7 days.* **No private, unreleased, or catalogue content is exposed at any point** |

> This is why the asset is generated rather than real. **The cleanup gap is designed around, not discovered
> afterwards.**

## 9. Result classification

### 9.1 SUCCESS — all must hold → **`UPLOAD CONTRACT VERIFIED`**

| # | Criterion |
|---|---|
| 1 | `POST /media/presign` → **2xx** returning **both** `uploadUrl` **and** `publicUrl` |
| 2 | **Signed PUT → 2xx** |
| 3 | **`publicUrl` serves the object** — **200/206**, and the **stored length equals the PUT byte count** (§4.5). Not "any 2xx" |
| 4 | **No `Authorization` on the PUT** — asserted on the outgoing request |
| 5 | **No secret in any sink** — no API key, no `X-Amz-Signature`/`-Credential`/`-Security-Token`, no full `uploadUrl` |
| 6 | **`queued == 0`** at each of the four checkpoints (§6 — proven at instants, not across the interval) |
| 7 | **The same four `failed` ids**, unchanged |
| 8 | **No post created** — the forbidden path never requested |

### 9.2 What success may NOT establish

> **`UPLOAD CONTRACT VERIFIED` is the entire claim.** It is a statement about **one PUT of one colour-bar
> file**. Specifically, it does **not** establish:

| ❌ Not established | Why not |
|---|---|
| **social posting verified** | `POST /posts` is never called. Zero evidence about post creation, TikTok settings acceptance, or whether a post appears |
| **production publishing recovered** | the publish path (`_publish_one`, claim/finalize, per-channel routing, Postiz) is never entered |
| **backlog recovery ready** | 343 awaiting + 4 failed are untouched and unproven. The **Postiz bootstrap hang is unfixed** and was excluded from Wave 1A |
| **idempotency resolved** | **`x-request-id` + `existingPost` parsing + 409 handling remains MANDATORY before the first production requeue.** Not implemented in PR #694, by instruction. A requeue without it can double-post |

### 9.3 FAILURE — any one aborts and is reported as-is

| # | Criterion |
|---|---|
| 1 | **Any non-2xx** on presign or PUT; **anything but 200/206** on the accessibility GET |
| 2 | **Malformed presign response** — missing `uploadUrl`/`publicUrl`, or non-JSON |
| 2b | **The retrievable object is not the asset** — stored length ≠ the PUT byte count, an unusable `Content-Range`/`Content-Length`, or an error document served as 2xx (§4.2) |
| 3 | **Secret exposure** in any sink |
| 4 | **Any request to the forbidden path** |
| 5 | **Ledger or queue mutation** — `queued != 0`, failed-set drift, or posts-digest drift |
| 6 | **An unexpected external object** beyond the single temporary upload |

**A failure is not retried and is not fixed forward in the same run.** It is reported with its bounded
redacted evidence, and the gate is re-presented.

> **A 405 on the PUT would be the single most valuable failure available** — it would mean the presign
> contract is *also* not what the spec says, invalidating report 09 §6 rather than confirming it.

## 10. RESULT — executed 2026-07-17, gate token `APPROVE UPLOAD CANARY`

> # ✅ LIVE UPLOAD CONTRACT VERIFIED
>
> Runner `ca31aaf1…` · exit 0 · **3 requests, no aborts, no retries.** Exact boundary: **§10.4** — this is
> *not* a byte-identity claim. (The runner's own literal stdout string is `UPLOAD CONTRACT VERIFIED`; the
> **supported claim** is the qualified one above.)

Report 09 §11.5's named residual — *"contract read from the spec, never exercised live"* — is **CLOSED**.
The presign + signed PUT contract is no longer a claim about a server; it is a measurement of one.

| # | Success criterion | Result |
|---|---|---|
| 1 | presign → 2xx with **both** `uploadUrl` and `publicUrl` | ✅ |
| 2 | signed PUT → 2xx | ✅ |
| 3 | `publicUrl` accessible | ✅ **206**, `Content-Range` total **54770** == the asset size — **declared, not measured** (§10.4) |
| 4 | no `Authorization` on the PUT | ✅ asserted on the outgoing request |
| 5 | no secret in any sink | ✅ swept: no API key, no `X-Amz-*` value, no `uploadUrl`, no `@` |
| 6 | `queued == 0` | ✅ at **all five** checkpoints |
| 7 | the same four `failed` ids | ✅ unchanged |
| 8 | no post created | ✅ the post-creation path was never requested |

**A retrievable media object of the expected declared length and media type exists at the server-returned
URL.** `Content-Range: bytes 0-0/54770` reports a total equal to the 54,770-byte asset PUT, and
`Content-Type: video/mp4` matches presign's `contentType` on read-back, so **no deviation was recorded**.
Asset sha256 `15987301315bd793…`, `video/mp4`, 54,770 bytes, 2.000000 s. **This is not byte identity —
§10.4.**

> ⛔ **RETRACTED:** *"The object round-tripped byte-exactly."* **Overclaim.** See §10.4.

Ledger posts digest `dd1677e62654a2aa…` — **identical at all five checkpoints** (baseline, before each of the
three stages, after completion). `FANOPS_CORPUS_AUTO=0` at each. Credential source: **`env-fallback`**
(keyring holds no `ZERNIO_API_KEY`), so §4.3's keyring branch was not exercised live — it is proven only by
the offline control.

### 10.1 The upload host is not the serving host

**`[OBS]` — what was observed, once, on 2026-07-17:**

| | |
|---|---|
| `uploadUrl` hostname | ended in **`r2.cloudflarestorage.com`** (`late-media.<account>.r2.cloudflarestorage.com`) |
| `publicUrl` hostname | **`media.zernio.com`** |
| Relationship | **the upload and serving hosts were different** |
| Signed params present | `X-Amz-Algorithm, X-Amz-Content-Sha256, X-Amz-Credential, X-Amz-Date, X-Amz-Expires, X-Amz-Signature, X-Amz-SignedHeaders` |

**`[INFER]` — what that supports:** the upload hostname **strongly indicates Cloudflare R2-compatible
storage**.

> **Not claimed:** Zernio's storage architecture. One hostname from one presign response is not an
> architecture. It does not establish what backs `media.zernio.com`, whether R2 is used for all media or all
> tenants, whether the hostname is stable, or whether a CDN, proxy, or migration sits behind either name.

**`[CONCLUSION]` — the engineering rule, which needs only the `[OBS]`:**

- FanOps must treat `uploadUrl` and `publicUrl` as **opaque server-returned values**;
- it must **not derive one from the other**;
- it must **not require both to use the same hostname**.

The OpenAPI spec (S0) types both as opaque strings and never says they differ in host. The shipped code
already obeys this — it returns the server's `publicUrl` verbatim and never parses the PUT target — and it
is right regardless of who runs the storage. This also vindicates §4.2's full-string destination pin over a
hostname check: a hostname rule keyed to `zernio.com` would have **refused the legitimate PUT**.

### 10.2 What this does NOT establish — unchanged

**`LIVE UPLOAD CONTRACT VERIFIED` is the entire claim**, bounded by §10.4. It remains a statement about one
PUT of one colour-bar file, whose stored bytes were never read. It does **not** establish byte identity,
social posting, production publishing recovered, backlog recovery ready, or idempotency. **`x-request-id` +
`existingPost` + 409 remains MANDATORY before the first production requeue.**

### 10.3 Residual

One 2-second colour-bar test pattern now sits at `https://media.zernio.com/temp/1784283036590_ybk7o6je_
zernio-canary.mp4` until Zernio's ~7-day expiry (§8.1). It is referenced by no post, so it is never made
permanent. No private, unreleased, or catalogue content was exposed at any point.

### 10.4 What "VERIFIED" does and does not mean — the byte-identity boundary

> ⛔ **RETRACTED, everywhere it appeared:** *"round-tripped byte-exactly"* · *"proves the retrievable object
> is the exact bytes sent"* · any equivalent byte-for-byte identity claim.

**The precise supported claim is `LIVE UPLOAD CONTRACT VERIFIED`.** The canary established:

- presign returned `uploadUrl` and `publicUrl`;
- the signed PUT succeeded;
- `publicUrl` returned **HTTP 206**;
- `Content-Range` reported a **total equal to the uploaded asset size**;
- `Content-Type` was **`video/mp4`**;
- **no body bytes were consumed**;
- **no content hash or byte-for-byte comparison was performed**.

**Therefore it proved: a retrievable media object of the expected declared length and media type exists at
the server-returned URL. It did not prove byte-level identity.**

**Why the overclaim was wrong, mechanically.** The request was `Range: bytes 0-0` with `stream=True`; the
body was never iterated and the connection was closed immediately (§4.4) — **the canary did not read even
the one byte it asked for.** `Content-Range: bytes 0-0/54770` is a **header the server emits**, i.e. a
*declaration* about the object, not a measurement of it. Length and media type are what the server *says*;
nothing compared stored bytes to sent bytes, because nothing retrieved stored bytes.

**This is sufficient for the decision it exists to inform.** The question was whether `(method, path)` is
routed and honoured — the 405 was a **routing** verdict, and routing is exactly what presign + signed PUT +
a 206 at `publicUrl` proves. Byte identity would answer a *different* question (storage corruption), one the
405 never raised. **The canary is not rerun to strengthen this claim**: a stronger claim is not needed, and
a second live call would spend real risk on a question that is not blocking.

## 11. Gate — CLOSED

**Executed under `APPROVE UPLOAD CANARY`, 2026-07-17. Result: `LIVE UPLOAD CONTRACT VERIFIED` (§10), bounded
by §10.4. Not to be rerun.**

The canary was the only remaining way to close report 09 §11.5's residual: *"contract read from the spec,
never exercised live."* It could not publish, could not touch the ledger, could not touch a parked or failed
record, and could not expose catalogue content — and it did none of those. **Result: §10.**

> ⛔ **SUPERSEDED — the gate is closed.** This section asked for one of two tokens:
> `APPROVE UPLOAD CANARY` / `DO NOT CALL ZERNIO`. **`APPROVE UPLOAD CANARY` was returned on 2026-07-17** and
> the canary executed once. The §2.1 operator hold it carried is **discharged** — the result has been
> returned, so the hold no longer binds.

**The next gate is not here.** It is the `x-request-id` + `existingPost` + 409 follow-up (§10.2), which
remains **mandatory before the first production requeue**. A verified upload contract does not authorise
re-running the four burned posts.
