# 08 — Dual-Backend Incident Frame (Read-Only)

> **Status: EVIDENCE COMPLETE. No code modified. No backend called. No post requeued.**
> Zernio is **CONFIRMED DOWN** at the upload layer, and the cause class is now **positively proven
> server-side** (§3.4) rather than inferred by elimination. Postiz is **CONFIRMED DOWN at the service
> layer** but its **publish path is UNTESTED** (§4) — it is *not* a second instance of the Zernio incident.
> Per the operator's §5 instruction, Postiz is therefore carved out as a **separate Wave 1B scope** and
> implementation stops at the §6 gate.

---

## 1. Document Control

| Field | Value |
|---|---|
| **Title** | 08 — Dual-Backend Incident Frame (Read-Only) |
| **Path** | `docs/reconciliation/08_DUAL_BACKEND_INCIDENT_FRAME.md` |
| **Purpose** | Independently classify each publish backend before any root-cause implementation. Establish, per backend: last success, first failure, routing, service state, auth/contract, whether a publish was actually attempted, and the exact failure layer. |
| **Governing baseline** | `docs/reconciliation/07_WAVE_0A_CONTAINMENT_RECORD.md` (its live claims re-measured, not trusted) |
| **Frame open** | **2026-07-16T22:07:57Z** |
| **Frame close** | **2026-07-16T22:16Z** |
| **Host TZ** | `Asia/Dubai` (UTC+04) — local date reads `2026-07-17`; **all times in this document are UTC** |
| **Scope** | Read-only classification of the Zernio and Postiz backends. Nothing else. |
| **Mutations performed** | **NONE.** This document is the only file created. |
| **Backends called** | **NONE.** No request was issued to Zernio (`zernio.com`) or to the Postiz API (`:4007`) within this frame. |

### 1.1 Correction to a claim made earlier in this session

An earlier turn asserted **"both publish backends are down"** and that approving the Instagram backlog
"would just burn it a second way." **That was an overclaim and is retracted.** A 502 on Postiz's
`/api/public/v1/integrations` route proves the *service* is unreachable; it does **not** prove a *publish
attempt* fails, because **no Postiz publish has been attempted since 2026-07-04T23:52:27Z**. The two
backends are in materially different evidentiary states and are classified separately below. The
correction is the operator's, not this document's.

### 1.2 Scope note on the containment record

Report 07 §3.3 records the Postiz observation as *"attempting to start a Postiz container that never comes
up"* and marks it **out of Wave 0A scope**. The out-of-scope call was correct. The **characterisation is
factually wrong**: the container comes up fine and is `healthy`; the **Node backend inside it never binds
its port** (§4.4). Report 07's conclusions are unaffected — the claim is incidental to it — but the row
should not be relied on as-is.

---

## 2. Dual-Backend Evidence Matrix

| # | Dimension | **Zernio** (TikTok) | **Postiz** (Instagram) |
|---|---|---|---|
| — | **CLASSIFICATION** | **CONFIRMED DOWN** — publish attempted, failed 4× | **Service: CONFIRMED DOWN**<br>**Publish path: UNTESTED** |
| 1 | **Last confirmed successful publish** | **2026-07-05T02:44:50.642621Z**<br>`hrmny-blog`, `public_url` present | **2026-07-04T23:52:27.992810Z**<br>`markmakmouly`, `public_url` present |
| 2 | **First confirmed failure** | **2026-07-16T13:31:00.272298Z**<br>`post_04b29c9f7f2d` — HTTP 405 | **NONE for publish.**<br>First *lifecycle* failure: **2026-07-16T13:37:35Z** |
| 3 | **Routing + config source** | `accounts.json` `backends.tiktok = zernio`.<br>`FANOPS_POSTER` **absent** → D12: `accounts.json` is sole truth.<br>`ZERNIO_URL` **absent** → in-code default `https://zernio.com/api/v1` | `accounts.json` `backends.instagram = postiz` (3 handles, 210 posts).<br>`POSTIZ_URL` present; **proven to target `localhost:4007`** (§4.3) — value never read |
| 4 | **Service / process / container state** | **NOT OBSERVABLE** — hosted third-party SaaS. No local process. Not probed (operator constraint) | **OBSERVABLE AND BAD.** Container `postiz` running since `2026-07-14T12:55:37Z`, `Health=healthy`, `RestartCount=0`. pm2 `backend` = `online`, pid 250, 2D uptime, **0 restarts**, 0% CPU. **Nothing listening on `:3000`** |
| 5 | **Auth + endpoint contract** | **AUTH PROVEN VALID.** Step 1 returns 2xx + a token; a bad key raises `ZernioAuthError` at `zernio.py:144`. No 401 anywhere. Contract = two-step upload, `zernio.py:124-131` | **NOT ESTABLISHED.** API unreachable, so `POSTIZ_API_KEY` is untested. The 502 is nginx's, not the app's |
| 6 | **Did an actual publish attempt fail?** | **YES — 4×** (13:31, 16:03, 18:57, 21:27 on 2026-07-16). The 4th observed live in report 07's frame | **NO — 0 attempts since 2026-07-04T23:52Z.** All 210 IG posts are `awaiting_approval`; `publish_due` iterates `queued` only (`post/run.py:475`) |
| 7 | **Exact failure layer** | **UPLOAD** — step 2 of the two-step media upload. Not lifecycle, not network, not auth, not post-creation, not ledger (§3.5) | **LIFECYCLE + SERVICE BOOTSTRAP.** Publish layer **untested**; its failure is *inferred* from service state, never observed (§4.5) |

### 2.1 The two backends are not symmetric — why this matters

Both lanes last succeeded within three hours of each other (07-04T23:52Z / 07-05T02:44Z) and both have shipped
nothing since. That symmetry is **coincidental and misleading**:

- **Zernio was tested and failed.** Four posts were due, four were attempted, four burned. The evidence is a
  publish attempt reaching a live server and being rejected.
- **Postiz was never tested.** Zero posts were due, zero attempted. Its backend is independently broken, but
  that is a *service* fact established by local process inspection — not by any publish.

Treating them as one incident would fuse a **proven client/server contract break** with an **unproven
infrastructure hang**. They share no code, no failure layer, and no evidence class.

### 2.2 Why nothing was attempted between 07-05 and 07-16

Neither backend was exercised for eleven days. This is not a gap in the evidence — it is the explanation for
it: **no post was `queued` and due** in that window. The current schedule wave begins 2026-07-16T13:31Z, which
is why *both* the first Zernio 405 and the first Postiz lifecycle timeout appear within six minutes of each
other on 07-16. The daemon ticked throughout (458 `corpora_refresh_skipped` lines); it simply had nothing due
to publish.

**Consequence:** the Zernio breakage and the Postiz backend hang could each have begun at any point in an
11-day window. Neither has a narrowable onset from FanOps' own logs.

---

## 3. Zernio Contract Reconstruction

### 3.1 The contract as the client implements it

Two-step upload, docstring-marked **"DISCOVERED LIVE 2026-06-29"** (`zernio.py:124-131`):

| Step | Request | Documented response | Status now |
|---|---|---|---|
| **1** | `POST {base}/media/upload-token`<br>`Authorization: Bearer <key>`, JSON `{"accountId": <id>}` | `{"token": <single-use>, "uploadUrl": ...}` | ✅ **SUCCEEDS** — proven by elimination (§3.2) |
| **2** | `POST {base}/media/upload?token=<token>`<br>multipart field **`files`** (plural) | `{"success": true, "files": [{"url": <hosted>}]}` | ❌ **HTTP 405** |
| **3** | `POST {base}/posts`<br>`{content, publishNow:true, platforms:[…], media:[…]}` | `{_id \| id \| postId}` | **NEVER REACHED** |

`{base}` = `cfg.zernio_url or "https://zernio.com/api/v1"` (`zernio.py:42-43`). `ZERNIO_URL` is absent, so
`{base}` is the **in-code default**.

### 3.2 Step 1 succeeds — proven, not assumed

The ledger's `error_reason` is `publish failed: Zernio upload failed (405) — body withheld`. That string is
produced at **exactly one line** — [`zernio.py:161`](../../src/fanops/post/zernio.py) — which is reachable
**only after step 1 has returned a token**. Had step 1 failed, the error would instead read
`Zernio upload-token mint failed (…)` (`:146`) or `Zernio 401 on upload-token mint` (`:144`).

**Therefore, at 2026-07-16T21:27Z:**

| Property | Verdict |
|---|---|
| `https://zernio.com/api/v1` reachable | ✅ **YES** |
| `ZERNIO_API_KEY` valid | ✅ **YES** — no 401 at `:144` |
| `POST /media/upload-token` accepts POST | ✅ **YES** |
| Server returned a usable `token` | ✅ **YES** — else `:152` |
| `POST /media/upload` accepts POST | ❌ **NO — 405 Method Not Allowed** |

This eliminates network, DNS, TLS, auth, and account-configuration causes **as a class**. The API is up and
answering; one specific `(method, path)` pair is rejected.

### 3.3 The client discards the server's own `uploadUrl`

Step 1's response is documented at `:125` as `{"token": <single-use>, "uploadUrl": ...}`. The client reads
**only** `token`:

```python
token = r.json().get("token")            # :148  — uploadUrl is never read
...
resp = requests.post(f"{_base(cfg)}/media/upload",   # :155 — path is HARDCODED
                     headers=headers, params={"token": token},
                     files={"files": (Path(path).name, fh, "video/mp4")}, timeout=120)
```

The server tells the client where to upload. The client ignores it and hardcodes the path it discovered on
2026-06-29. **This is the highest-probability remedy hypothesis** — see §3.6 for its evidentiary status.

### 3.4 The cause class is **server-side** — positively proven

Report 07 §5.2 reached "server-side drift" by *eliminating* a stale `ZERNIO_URL`. That is sound but negative.
The positive proof:

| Test | Result |
|---|---|
| Last commit touching the **`zernio_upload_media` function body** (`git log -L`) | **`0e6c2b4`, 2026-06-30T20:49:41Z** |
| Commits touching that function between last success (07-05) and first failure (07-16) | **ZERO** |
| Commits touching `zernio.py` *at all* in that window | **2** — `6681cbc` (07-06), `e556c5d` (07-11) |
| What those 2 commits changed | **Only `ZernioPoster.publish`'s `/posts` retry branch.** Neither touches `zernio_upload_media`, its URL, or its method. Diffs reviewed in full |
| **`(method, path)` at `d0391d7`** (07-05 state, **succeeded**) | `requests.post(f"{_base(cfg)}/media/upload")` |
| **`(method, path)` at HEAD `6d21749`** (**405s now**) | `requests.post(f"{_base(cfg)}/media/upload")` — **byte-identical** |
| `{base}` changed? | **No** — `ZERNIO_URL` absent both then and now → same in-code default |

**A 405 is a routing-layer verdict on the `(method, path)` pair alone.** Payload, size, encoding, filename,
and content-type faults produce 400 / 413 / 415 / 422 — **never 405**. Our `(method, path)` is provably
unchanged across the success→failure boundary.

> **Conclusion: `POST https://zernio.com/api/v1/media/upload` returned 2xx on 2026-07-05 and returns 405 on
> 2026-07-16, from byte-identical client code. The server's routing for that pair changed. This is now
> established positively, not by elimination.**

This also **clears the 07-06 → 07-15 publish-path commits** (`MOL-115`, `B02`, `RC-1`, `RC-3b`, `R1`,
`RC-10`) of causing the 405 — a necessary check, since nine commits landed on the publish path inside the
failure window and none had been ruled out.

### 3.5 Failure layer — precise

| Layer | Implicated? | Evidence |
|---|---|---|
| Lifecycle | **No** — Zernio is hosted; there is no lifecycle step | — |
| Network | **No** | An HTTP response was received and parsed |
| Authentication | **No** | Step 1 returned 2xx; no 401 raised at `:144` |
| **Upload** | ✅ **YES — this is the layer** | `:161`, step 2, HTTP 405 |
| Post creation | **No** | `/posts` never reached — step 2 raises first |
| Ledger handling | **No** | The ledger behaved **correctly**: post → `failed`, `error_reason` set, `media_id`/`public_url`/`published_at` all `None`. No double-post, no phantom success |

### 3.6 What is proven vs. what is hypothesis — stated plainly

| Claim | Status |
|---|---|
| Step 1 succeeds; auth valid; API reachable | **PROVEN** (§3.2) |
| Failure is step 2, HTTP 405, at `zernio.py:161` | **PROVEN** |
| Client `(method, path)` unchanged since before the last success | **PROVEN** (§3.4) |
| **Cause class is server-side** | **PROVEN** (§3.4) |
| The client discards a server-supplied `uploadUrl` | **PROVEN** — code read (§3.3) |
| **Zernio moved uploads to the `uploadUrl` and retired POST on the legacy path** | **HYPOTHESIS — the leading one, not confirmed.** Requires exactly one live observation of step 1's response body to confirm or kill |
| The correct remedy is "honour `uploadUrl`" | **UNPROVEN** — follows only if the hypothesis holds |

### 3.7 Why four burns produced zero diagnostic information — a sibling-parity defect

`zernio.py:161` discards the response body:

```python
if resp.status_code >= 300:
    raise RuntimeError(f"Zernio upload failed ({resp.status_code}) — body withheld")
```

Its sibling **26 lines away**, `zernio_list_accounts`, does the opposite (`:187`):

```python
raise RuntimeError(f"Zernio accounts failed ({resp.status_code}): {redact(resp.text, cfg.zernio_api_key)}")
```

`redact()` **already exists and is already applied to this exact API's key** in the same module, so there is
**no secret-safety justification** for the asymmetry — the two paths are simply inconsistent. This is the
precise trap `src/fanops/CLAUDE.md` names: *"Sibling parity is where the real bugs live — one function guards
the input, its twin doesn't."*

Compounding it: **RFC 9110 requires a 405 response to carry an `Allow` header** naming the permitted methods.
The server has been *telling us the answer on every one of the four failures*. The client discards the header
and the body, and logs nothing (report 07 `CAN-027`: the terminal branch sets `error_reason` and breaks
without logging). **Four production posts were burned to produce one integer.**

---

## 4. Postiz Classification

### 4.1 Verdict

| Dimension | Classification |
|---|---|
| **Service (HTTP API)** | **CONFIRMED DOWN** |
| **Publish path** | **UNTESTED** — zero attempts since 2026-07-04T23:52:27Z |
| **Lifecycle (`ensure_up`)** | **CONFIRMED FAILING** — 8 occurrences, all 2026-07-16 |
| **Root cause of the hang** | **NOT ESTABLISHED** — but the one known candidate (mastra 1600-column) is **positively excluded** by measurement (§4.6) |
| **Remediation** | **UNTESTED** — a restart may or may not clear it. The stored `DROP TABLE` fix is a **no-op** (§4.6) |

### 4.2 The container is healthy; the backend never bound its port

| Probe | Result |
|---|---|
| `docker inspect postiz` | `Status: running` · `Health: healthy` · `RestartCount: 0` · `StartedAt: 2026-07-14T12:55:37Z` |
| `pm2 list` (inside container) | `backend` **`online`**, pid 250, uptime **2D**, restarts **0**, cpu 0%, mem 104.9mb |
| Listening sockets (inside container) | `nginx :5000` (→ host `4007`) · `next-server :4200` (frontend) · docker-DNS `:43337` |
| **Listener on `:3000`** | **NONE** |
| nginx error log | `connect() failed (111: Connection refused) while connecting to upstream: http://127.0.0.1:3000` |
| `backend-out.log` | The npm start banner — **then silence.** No bootstrap completion, no listen line |
| `backend-error.log` | **EMPTY** |

**This is a silent bootstrap hang, not a crash-loop.** The process is alive (104.9mb resident, 0% CPU) and
has **never restarted**, so pm2 reports `online`; but it never reached `listen(3000)`. nginx faithfully
returns 502 because its upstream was never there. The container's `healthy` status is nginx-only and
therefore meaningless as an app signal — which the on-demand script's own v4 comment already documents:
*"the container health-check … LIES while the Node backend crash-loops."*

### 4.3 Routing linkage — proven without reading a secret

The publish client resolves its base from `cfg.postiz_url` (`postiz.py:48-49`) and posts to
`{postiz_url}/public/v1/…`. Establishing that this is the *same* endpoint that 502s would normally require
reading `POSTIZ_URL`'s value — which report 07 §5 correctly withheld.

**It is unnecessary.** The daemon's own log already discloses the target:

```
postiz_metrics  …  fetch_failed  err=HTTPConnectionPool(host='localhost', port=4007): …
```

`POSTIZ_URL` targets **`localhost:4007`** — the exact origin returning 502. The publish route
(`{postiz_url}/public/v1/posts`) traverses the same nginx to the same absent upstream. **Linkage proven; no
secret read.**

> Corollary: `postiz_lifecycle.ensure_up` requires `_is_local(url)` (`postiz_lifecycle.py:29`) and does
> nothing for a remote Postiz. That it fires at all independently corroborates a local `POSTIZ_URL`.

### 4.4 Why "since 07-04" is NOT the right onset — and what is

It is tempting to date the Postiz outage to its last success (2026-07-04T23:52Z). **That is unsupported.**
The stack is *on-demand*: a launchd reaper stops it after `IDLE_MIN` and `ensure_up` restarts it per publish.
Between 07-05 and 07-14 it was stopped and started an unknown number of times with **no publish attempted**.

The only defensible statement: **the currently-running instance has had a hung backend since
`2026-07-14T12:55:37Z`** (container start; pm2 uptime 2D; 0 restarts). Whether earlier instances were healthy
is **not observable** from available evidence.

### 4.5 Failure layer — and the limit of the inference

| Layer | Status |
|---|---|
| **Lifecycle** | ✅ **CONFIRMED FAILING** — `ensure_up` `TimeoutExpired` ×8 |
| **Service bootstrap** | ✅ **CONFIRMED FAILING** — backend never bound `:3000` |
| Network / auth / upload / post-creation / ledger | **UNTESTED** — unreachable to test |

**The honest limit:** a publish through Postiz *would* fail — but that is **inferred** from the service being
down, **not observed**. No Postiz publish attempt exists in the frame. Should the backend be revived, the
publish path would still be **unproven since 07-04** and would need its own verification.

### 4.6 The known root cause is positively EXCLUDED — the prior fix worked

Project memory records this failure as a **`mastra_ai_spans` 1600-column crash-loop** (Mastra runs an
additive `ALTER TABLE … ADD COLUMN` each boot; Postgres counts dropped columns toward its hard 1600 cap, so
the ADD eventually throws and the backend exits). The documented fix is to `DROP` the empty telemetry table.

**That cause is not merely unmatched — it is excluded by measurement:**

| Predicted by that diagnosis | Measured 2026-07-16 |
|---|---|
| Crash-**loop** → `↺` climbing | **`RestartCount: 0`**, pm2 `↺ 0`, uptime **2D** |
| `backend-error.log` ends with `MastraError: tables can have at most 1600 columns` | **`backend-error.log` is EMPTY**; no `mastra` / `1600` / column-limit string in any pm2 log |
| `mastra_ai_spans` at/near the cap (`max_attnum` ≈ 1600) | **`live_cols=21`, `max_attnum=43`** — nowhere near 1600 |
| Core publish tables separate and intact | **Confirmed** — `Integration` and `Post` both present |

**Conclusion: the documented fix was already applied — that is what the `2026-07-14T12:55:37Z` container
start is — and it worked.** The crash-loop is genuinely gone (`↺ 0` is its success signature). What replaced
it is a **different failure**: a silent bootstrap hang with no error output at all.

**Operational consequence for Wave 1B: re-running the `DROP TABLE` fix is a no-op.** The table is already
clean. A session that pattern-matches "Postiz 502" to the stored fix will spend its effort on a table that
is 43 columns from a 1600 limit and conclude nothing. **The triage discriminator is the restart count:**
`↺` climbing → the mastra bug; **`↺ 0` + `online` + no `:3000` listener + empty error log → this hang,
cause unknown.**

**The cause of the hang remains NOT ESTABLISHED.** Excluding the one known candidate is progress, not a
diagnosis.

### 4.7 Two bounded lifecycle defects (separate from the hang)

Both are real, both are narrow, and **neither is the outage** — the backend hang is. Recorded so they are not
rediscovered:

1. **The caller's timeout is shorter than the script's wait.** `postiz_lifecycle._WAIT_S = 150`
   (`postiz_lifecycle.py:25`) kills `postiz-ondemand.sh` at 150 s, but the script's own
   `WAIT_S=180` (raised from 120 on 2026-07-05). **The script can never complete its own wait loop** — the
   caller always kills it first. Cosmetic while the backend is hung (no wait would ever succeed); a genuine
   false-negative once it is fixed and a cold boot legitimately needs >150 s.

2. **`ensure_up` fires for non-Postiz due posts.** `publish_due` calls it unconditionally when work exists
   (`post/run.py:481-482`), and `_backend_is_postiz` (`postiz_lifecycle.py:34-45`) returns `True` if **any**
   live-ready channel is Postiz — not whether **this** due post is. So a **Zernio**-routed due post spends
   150 s trying to start Postiz. This is report 07 §3.3's 159 s pass (vs the usual ~0.8 s).
   **Currently dormant:** Containment A left 0 posts `queued`, so `publish_due` has no due work and never
   calls `ensure_up`. It returns the moment anything is requeued.

---

## 5. Scope Split (per operator instruction)

The operator's instruction: *"if Postiz is independently proven broken, stop before implementation and present
a separate Wave 1B scope rather than combining both fixes into one change."*

**Postiz is independently proven broken** (§4.2). The condition is met. The scopes are therefore split:

| | **Wave 1A — Zernio** | **Wave 1B — Postiz** |
|---|---|---|
| **Track** | Primary root-cause + implementation | Separate, deferred |
| **Evidence state** | Cause class **proven**; remedy **hypothesis** | Cause **not established** |
| **Blocks** | 133 TikTok posts, 4 burned | 210 Instagram posts, 0 burned |
| **Shared code** | none | none |
| **Shared failure layer** | none | none |
| **Ready to implement?** | **Only after §6.1 resolves the remedy hypothesis** | **No** — needs its own diagnostic frame first |

**They must not be combined into one change.** Beyond the operator's instruction, the engineering reason is
that Wave 1B has **no established root cause** — bundling it would attach an unproven fix to a proven one and
make the result unverifiable.

---

## 6. Prompt 08 Implementation Approval Gate

**No code has been modified. Implementation stops here and requires explicit approval.**

> **Note on provenance:** the "Prompt 08" text was not provided to this session. This gate is constructed
> from the operator's instruction in-session, not from a Prompt 08 document. If a written Prompt 08 exists,
> this gate should be reconciled against it before approval.

### 6.1 The blocking decision — the remedy hypothesis is unresolved

§3.4 proves the cause is server-side. It does **not** prove the remedy. The `uploadUrl` hypothesis (§3.3) is
the leading candidate and is **one observation away** from resolution — but that observation requires calling
Zernio, which §1 forbids without approval.

Two paths, and they are not equivalent:

| | **Path A — Probe first** | **Path B — Instrument first** |
|---|---|---|
| **Action** | One `POST /media/upload-token` (mints a token, **uploads nothing, publishes nothing, creates no post**), read `uploadUrl` from the response | Fix `zernio.py:161` to `redact()`-and-include the body + capture the `Allow` header; ship it; let the next real attempt self-diagnose |
| **Calls a backend?** | **Yes** — one call to Zernio | **No** |
| **Answers the hypothesis** | **Immediately and directly** | Only on the next real publish attempt |
| **Requires a requeue to learn** | **No** | **Yes** — a post must be requeued and burned to learn |
| **Cost** | 1 token mint (single-use, ~60 s lifetime, discarded) | **A 5th burned production post** |
| **Risk** | Consumes one throwaway token. Reveals the current contract without touching the queue | Zero live exposure now; buys the answer with a real post later |

**Recommendation: Path A, then Path B's fix regardless.** Path A is strictly cheaper — it converts a
hypothesis into a fact for the price of one discarded token, with no post at risk. Path B's instrumentation
fix should land **either way**: the sibling-parity gap at `:161` is a defect on its own terms, and without it
the *next* failure is as blind as the last four. But shipping B *as the diagnostic strategy* means paying for
the answer with a fifth burned post — when a single free call already has it.

### 6.2 What is requested for approval

| # | Action | Backend called? | Mutation? | Status |
|---|---|---|---|---|
| **1** | **Path A probe** — one `POST /media/upload-token`, read `uploadUrl`, discard token | **Yes** — Zernio, 1 call | No | **AWAITING APPROVAL** |
| **2** | **Instrumentation fix** — `zernio.py:161` redact-and-include body + `Allow` header, matching `:187` | No | Code + PR | **AWAITING APPROVAL** |
| **3** | **Contract fix** — honour step 1's `uploadUrl` | No | Code + PR | **BLOCKED on #1** — do not write until the hypothesis is confirmed |
| **4** | **Wave 1B** — Postiz backend diagnostic frame | No (local only) | No | **DEFERRED** — separate scope |
| **5** | **Requeue any parked post** | — | — | **NOT REQUESTED.** Nothing may be requeued until a publish is proven to work |

### 6.3 Standing constraints — unchanged by this frame

- **Containment A holds.** 0 `queued`, 4 `failed` (same IDs), 343 parked. Re-verified this frame.
- **Containment B holds.** Daemon logs `reason:"disabled"`; all three control-file hashes byte-identical.
- **Do not revert Containment B** before F-A/F-C are fixed — report 07 §14.1: the 12 h throttle marker is
  frozen, so a revert fires the corpora refresh on the **very next tick**, and after **2026-07-19T17:25:18Z**
  the budget has refilled and the F-A/F-B/F-C chain fires immediately.
- **The 2026-07-19T17:25:18Z rollover is unaffected by anything in this document.** It remains the hard
  deadline on the hashtag track.

---

## 7. Evidence Ledger

| ID | Evidence | Method | Location |
|---|---|---|---|
| `DBF-001` | Containment A + B still hold; ledger `queued 0 / failed 4 / awaiting 343`; same 4 failed IDs | `sqlite3` `mode=ro` read | §6.3 |
| `DBF-002` | Control-file hashes byte-identical to report 07 §3.9/§14 | `shasum -a 256` | §6.3 |
| `DBF-003` | Daemon PID 9121 `STAT S`, started 16:49:48Z, never restarted; gate logs `reason:"disabled"` | `ps`, `launchctl list`, `daemon.err` | §6.3 |
| `DBF-004` | **Per-platform publish history** — tiktok last `2026-07-05T02:44:50Z` (n=21); instagram last `2026-07-04T23:52:27Z` (n=16) | `06_published/*/*.json`, 73 records | §2 |
| `DBF-005` | Archive contains **successes only** — 0 `error_reason`, 0 `state` | same | §2 |
| `DBF-006` | 405 error string maps to exactly one line, reachable only post-step-1 | `grep` → `zernio.py:161` | §3.2 |
| `DBF-007` | **`zernio_upload_media` last modified 2026-06-30** (`0e6c2b4`); zero commits in the failure window | `git log -L :zernio_upload_media:…` | §3.4 |
| `DBF-008` | The 2 in-window `zernio.py` commits touch only `/posts` retry logic | `git show 6681cbc`, `git show e556c5d` — full diffs | §3.4 |
| `DBF-009` | **`(method, path)` byte-identical** at `d0391d7` (succeeded) vs HEAD (405s) | `git show <sha>:src/fanops/post/zernio.py` | §3.4 |
| `DBF-010` | Client discards step 1's `uploadUrl`; hardcodes the path | `zernio.py:148,155` + docstring `:125` | §3.3 |
| `DBF-011` | Sibling-parity gap — `:161` withholds body, `:187` redacts-and-includes | code read | §3.7 |
| `DBF-012` | Postiz container `healthy`, `RestartCount 0`, started `2026-07-14T12:55:37Z` | `docker inspect` | §4.2 |
| `DBF-013` | **pm2 `backend` `online`, 0 restarts, 2D uptime — but no `:3000` listener** | `docker exec postiz pm2 list`, `ss -tlnp` | §4.2 |
| `DBF-014` | `backend-out.log` = banner then silence; `backend-error.log` empty | `docker exec … tail` | §4.2 |
| `DBF-015` | nginx: `connect() failed (111: Connection refused) … upstream 127.0.0.1:3000` | `docker logs postiz` | §4.2 |
| `DBF-016` | **`POSTIZ_URL` targets `localhost:4007` — proven from the daemon's own log, no secret read** | `daemon.err` `postiz_metrics … HTTPConnectionPool(host='localhost', port=4007)` | §4.3 |
| `DBF-017` | **No `mastra` / `1600` / column-limit string in any pm2 log** — the stored diagnosis is unsupported for this incident | `docker exec postiz grep -ril` | §4.6 |
| `DBF-017b` | **`mastra_ai_spans` measures `live_cols=21`, `max_attnum=43`** vs the 1600 cap → the prior fix was applied and worked; the known cause is **excluded**. `Integration` + `Post` intact | `docker exec postiz-postgres psql -tAc` (read-only `SELECT`) | §4.6 |
| `DBF-018` | First lifecycle timeout `2026-07-16T13:37:35Z`; 8 total | `daemon.err` | §2, §4.7 |
| `DBF-019` | `_WAIT_S = 150` (caller) vs script `WAIT_S=180` | `postiz_lifecycle.py:25`, `postiz-ondemand.sh` | §4.7 |
| `DBF-020` | `ensure_up` unconditional on due work; `_backend_is_postiz` is a global check | `post/run.py:481-482`, `postiz_lifecycle.py:34-45` | §4.7 |
| `DBF-021` | Daemon ticked throughout the 11-day gap (458 `corpora_refresh_skipped`) | `daemon.err` | §2.2 |

**Secret-handling attestation.** No API key, token, password, or credential was read, printed, or written.
`POSTIZ_URL`'s value was **not read** — its target was established from the daemon's own error text (§4.3).
`ZERNIO_URL`'s absence was carried from report 07 §5.1, not re-read. No `.env` value was accessed in this
frame.

---

## 8. Final Classification

| Dimension | Classification |
|---|---|
| **Zernio** | **`CONFIRMED DOWN — SERVER-SIDE CONTRACT DRIFT PROVEN, REMEDY UNCONFIRMED`** |
| **Postiz** | **`SERVICE CONFIRMED DOWN — PUBLISH PATH UNTESTED, ROOT CAUSE NOT ESTABLISHED`** |
| **Containment** | **`HOLDING`** — A and B both re-verified this frame |
| **Overall** | **`YELLOW — CONTAINED; ONE CAUSE PROVEN, ONE UNDIAGNOSED; IMPLEMENTATION GATED`** |

### 8.1 What this frame changed

1. **ACT-01's cause class moved from inferred to proven.** Report 07 eliminated a stale `ZERNIO_URL`; this
   frame positively proves the client's `(method, path)` is byte-identical across the success→failure
   boundary, so the server changed. It additionally **clears nine in-window publish-path commits** that had
   never been ruled out.
2. **The remedy is one free call away** — and the frame identifies the specific artefact (`uploadUrl`) the
   client has been discarding since 2026-06-30.
3. **Postiz is separated, not escalated.** Its service is provably down, its publish path is provably
   **untested**, and its root cause is **not established**. It becomes Wave 1B rather than a co-defendant.
4. **A stale diagnosis was caught and its candidate excluded.** The stored `mastra`/1600-column fix **was
   already applied on 07-14 and worked**; `mastra_ai_spans` measures 43 `attnum` against a 1600 cap.
   Re-applying it is a **no-op**. Wave 1B starts from "cause unknown, one candidate excluded" — not from
   the stored playbook.
5. **Nothing was fixed.** Every defect in report 07 §17 remains open, plus the two bounded lifecycle defects
   in §4.7.
