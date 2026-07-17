# 10 — Zernio Upload-Only Canary Plan

> **Rev 2 — 2026-07-17.** Amends Rev 1 after operator rejection of the Rev 1 gate. Rev 1's defects are
> retracted in §0 and corrected in place below. **NOT EXECUTED. No Zernio API call has been made.**
> Gate tokens at §10: **`APPROVE UPLOAD CANARY`** / **`DO NOT CALL ZERNIO`**.

| Field | Value |
|---|---|
| **Purpose** | Prove the **presign + signed PUT** contract against the **live** Zernio API — the one thing report 09 could not: *"the contract is read from the spec and never exercised"* |
| **Scope** | **Upload only.** One disposable asset. **No post is created** — not by policy, structurally (§3.3) |
| **PR under test** | #694, branch `fix/zernio-presign-upload`, **OPEN / unmerged / MERGEABLE**, 10 changed files |
| **Code under test** | `src/fanops/post/zernio.py` — blob `acccd311effc2e441dd1b5675c3cd8b77759572c`, sha256 `7e38608d1a92e7b6de92d8ab9bc25fc3c07663bd99510911732f4523adc6a63f` (§2) |
| **Runner** | `zernio_upload_canary.py`, sha256 `18987e293e690e717777af612097997543f3a507aea767edb7606a784a51f779` — outside git, outside `FANOPS_ROOT` (§7) |
| **Call ceiling** | **Exactly 3**: 1 `POST /media/presign` · 1 signed `PUT` · 1 streamed `GET` (§4) |
| **Blast radius** | 1 temporary object in Zernio's media storage. **No ledger row, no corpus file, no social post, no account touched** |
| **Reversibility** | **Partial — §8.** The object cannot be deleted by us; it expires unreferenced |

---

## 0. Rev 1 defects — retracted

Four, three of them found by building and validating the runner rather than by re-reading the plan.

| # | ⛔ RETRACTED Rev 1 claim | Correction | Found by |
|---|---|---|---|
| R1 | *"PR under test: #694 @ `0b5a7248…`"* | **Stale.** Head is `00da9f82…`; `0b5a7248` is the *previous* commit. The stamp was stale **in the commit that introduced it** — see §1.1 | operator |
| R2 | *"changed files: 9"* | **10.** Rev 1 counted before its own file existed | operator |
| R3 | *"at most 3 HTTP requests"* alongside a `HEAD` **plus** a `GET` fallback | **Contradiction — that path is 4.** Replaced by exactly one streamed `GET` (§4) | operator |
| R4 | §5 assertion 4: *"Ledger SHA-256 identical before and after"* — *"the strongest available proof of no ledger mutation"* | **Unsound, and it would have flaked.** The live ledger is SQLite in **WAL mode** and the daemon writes every ~600s. A whole-file hash is wrong in both directions: committed rows can sit in the `-wal` while the main file is byte-identical, and a checkpoint can rewrite the main file with no logical change. Replaced by a **logical posts-map digest** (§6) | building it |

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

### 1.2 Preflight proofs — all before any network. Abort on any mismatch.

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

The runner never constructs a `ZernioPoster`, never builds a payload, and every outbound request passes
through a single chokepoint that **raises on the forbidden path segment before the socket is written** (§5).
Validated against a synthetic request: **blocked, 0 network requests made.**

## 4. Exact call budget — hard ceiling of 3

| # | Call | Auth | Count | Enforced by |
|---|---|---|---|---|
| 1 | `POST https://zernio.com/api/v1/media/presign` | **Bearer** | **exactly 1** | chokepoint (§5) raises on call 2 |
| 2 | `PUT <uploadUrl>` | **NONE** — the url is signed | **exactly 1** | chokepoint raises on call 2 **and on any `Authorization` header** |
| 3 | `GET <publicUrl>` · `stream=True` · `Range: bytes=0-0` · body never iterated · closed immediately | **NONE** | **exactly 1** | chokepoint raises on call 2 |
| — | **any other method** (`HEAD`, `DELETE`, `PATCH`, …) | — | **0** | budget defaults to 0 → raises on call 1 |
| — | **the post-creation path** | — | **0 — FORBIDDEN** | chokepoint raises on any url carrying the segment. **An assertion, not a convention** |

**Total: exactly 3 HTTP requests. No retry, no fallback, no loop.**

### 4.1 The accessibility check — one request, replacing Rev 1's HEAD+fallback

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
  answers **200** and we close before reading. **Both are 2xx → both accepted.**
- **No retry and no fallback.** A non-2xx is a canary **failure** (§9.2), reported as-is.

> **Redirects consume budget, deliberately.** `requests` re-enters the chokepoint per redirect hop, so a
> redirected PUT would abort at call 2. The ceiling counts **real HTTP requests**; a surprise redirect is a
> finding, not a free extra call.

## 5. The chokepoint

Every outbound request — however it is constructed — funnels through `Session.send`, which receives the
**final `PreparedRequest`**: true URL, true merged headers. Raising there happens **before the socket write**.

Per request, in order, all before any network:

| # | Check | Rationale |
|---|---|---|
| 1 | url carries the forbidden segment → **abort** | after-the-fact detection means it already happened |
| 2 | per-method count ≤ budget (default **0**) → **abort** | hard ceiling; unlisted methods are refused |
| 3 | `PUT` carries `Authorization` → **abort, not sent** | inspected on the **outgoing** request. Handing the key to third-party storage is not undoable |
| 4 | `zernio.py` sha256 == the §1.1 pin → else **abort** | closes the gap between preflight and the first API call |
| 5 | live-ledger interlock (§6) → **abort** | re-read at the last possible moment before each request |

**Validated with synthetic requests, no network:** post-creation url **blocked**; `PUT`+`Authorization`
**blocked**; `HEAD` **blocked**; `DELETE` **blocked**; requests actually sent: **0**.

## 6. Ledger interlock — logical, not byte-wise

Re-read from the **live** ledger over a `file:…?mode=ro` URI (writes are *impossible*, not merely unused), at
**four** points: **before presign · before PUT · before the GET · after completion** (plus a baseline). The
first three are the chokepoint's check 5, so they land at the last instruction before each request.

| Invariant | On drift | Why |
|---|---|---|
| **`queued == 0`** | **ABORT** | a `queued` post is publishable by the live daemon — the one state that must never coexist with an un-canaried publish path |
| **failed-id set == the same 4** | **ABORT** | proves no requeue and no re-burn |
| **`FANOPS_CORPUS_AUTO == 0`** | **ABORT** | operator-pinned |
| **posts-map logical digest** — sha256 over `(row_id, canonical-JSON payload)` for every `posts` row, ordered | **ABORT** | a post mutated mid-canary |
| whole-file `ledger.sqlite` sha256 | **recorded, not fatal** | **WAL mode + a live daemon** — the file moves on benign writes to *other* maps (sources/moments/clips). Gating on it is the R4 defect |

> The **logical** digest is the sound invariant: immune to WAL placement and checkpoint rewrites, and it
> changes **iff a post changes** — which is the actual question.

**The runner imports no ledger mutation method** — AST-asserted: the name `Ledger` is never bound or
referenced, and nothing is imported from `fanops.ledger`. (The class is unavoidably in `sys.modules` because
`zernio.py` imports it at module scope; that is a property of the code under test, so claiming "the ledger is
not imported at all" would be false. The checkable claim is that **this runner cannot call it**.)

## 7. The pinned runner

| Property | Value |
|---|---|
| **sha256** | `18987e293e690e717777af612097997543f3a507aea767edb7606a784a51f779` |
| **Location** | session scratchpad — **outside git**, **outside `FANOPS_ROOT`** (`/Users/molhamhomsi/FanOps`) |
| **Subprocesses** | AST **allow-list**: `git`, `ffmpeg`, `ffprobe`, `file`, `gh` — **no `fanops` CLI**. An allow-list, because a negative scan only rules out the name you thought to forbid |
| **Forbidden-path self-scan** | **AST over non-docstring string literals** — see below |
| **Ledger API references** | **0** (AST-asserted) |
| **Secrets** | `ZERNIO_API_KEY` loaded into `os.environ` **from the live `.env` by key name only**; never bound to a printed name, never logged, never written to the result file. Nothing else is read from `.env` |
| **Never printed** | `ZERNIO_API_KEY` · `uploadUrl` · `uploadUrl` query values · signed exception request objects |

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
| 3 | **`publicUrl` accessible** — 2xx on the single streamed `GET` |
| 4 | **No `Authorization` on the PUT** — asserted on the outgoing request |
| 5 | **No secret in any sink** — no API key, no `X-Amz-Signature`/`-Credential`/`-Security-Token`, no full `uploadUrl` |
| 6 | **`queued == 0`** throughout and after |
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
| 1 | **Any non-2xx** on presign, PUT, or the accessibility GET |
| 2 | **Malformed presign response** — missing `uploadUrl`/`publicUrl`, or non-JSON |
| 3 | **Secret exposure** in any sink |
| 4 | **Any request to the forbidden path** |
| 5 | **Ledger or queue mutation** — `queued != 0`, failed-set drift, or posts-digest drift |
| 6 | **An unexpected external object** beyond the single temporary upload |

**A failure is not retried and is not fixed forward in the same run.** It is reported with its bounded
redacted evidence, and the gate is re-presented.

> **A 405 on the PUT would be the single most valuable failure available** — it would mean the presign
> contract is *also* not what the spec says, invalidating report 09 §6 rather than confirming it.

## 10. Gate

**Nothing in this document has been executed. No Zernio API call has been made.**

The canary is the only remaining way to close report 09 §11.5's residual: *"contract read from the spec, never
exercised live."* It cannot publish, cannot touch the ledger, cannot touch a parked or failed record, and
cannot expose catalogue content.

`APPROVE UPLOAD CANARY` carries the §2.1 operator hold.

**Reply with exactly one:**

```
APPROVE UPLOAD CANARY
DO NOT CALL ZERNIO
```
