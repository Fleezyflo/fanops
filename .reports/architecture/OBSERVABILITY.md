# FanOps — Observability: failure → signal mapping

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73`**

Every significant failure and stuck state, mapped to **every** signal available to the operator. Each claim was
verified by tracing the **reader** (the CLI/Studio surface that renders it), not only the writer — per the
brief.

**Signal classes:** `immediate+explicit` · `delayed+explicit` · `system-level only` · `per-item only` ·
**`silent`** · **`misleading success`**.

---

## 1. The signal surfaces that exist

| Surface | Reader | What it can tell you |
|---|---|---|
| `run.log` (JSON lines) | `get_logger(cfg)` → `cfg.log_path` | every stage event; the primary forensic record |
| **heartbeat** | `_heartbeat` → run.log `stage=="heartbeat", origin=="loop"` | emitted **only when a pass completes** ([cli.py:1306](src/fanops/cli.py:1306)) |
| `fanops daemon status` | [daemon.py:437](src/fanops/daemon.py:437) | PID-primary liveness + readiness |
| `fanops status` | `pipeline_status` | per-source stage + gate backlog |
| `fanops doctor` | `doctor.py` | strict preflight; `Settings.strict_validate` |
| Studio **Home** banner | `views_common.postiz_health_for_banner` | one **cached live GET** — the only surface that probes the backend |
| Studio **Schedule/Posted** | `views_results` | per-post state + `error_reason` |
| `studio_audit.log` | `audit.read_audit_tail`, `fanops audit tail` | one JSON line per state-changing Studio action |
| `Post.error_reason` | everywhere | **a 4-way-overloaded control channel** (see §5) |
| `06_published/<day>/<pid>.json` | operator, by hand | day-bucketed shipped record |
| `RunSummary` / digest | `_build_summary` → `write_digest` | per-state tallies, incl. **`gave_up`** as a disjoint bucket |

---

## 2. The failure → signal matrix

| Failure / stuck state | Ledger | `error_reason` | Audit | run.log | CLI status | Studio | Class |
|---|---|---|---|---|---|---|---|
| **`submitting` + real sid, poll never resolves** (`C3-F1`) | `submitting` — **forever** | `stuck …` **stamped ONCE, never updated** | ✗ | one `poll-error`/`left:` line **per pass** | counted in `submitting` | in-flight lane, stale reason | ⚠ **per-item only, and MISLEADING** — a post stuck 3 days and one stuck 3 years look **identical** |
| **`submitting` + fake token on Zernio** (`C3-F2`) | `submitting` — **forever** | `reconcile poll error: …` | ✗ | `poll-error` **every pass** | `submitting` | in-flight lane | ⚠ **per-item only.** The `gave_up` digest bucket that *should* catch this **never fires on Zernio** |
| **all channels malformed → reconcile never runs** (`F-A`) | `submitting` — **permanently, unlabeled** | **`None`** | ✗ | ✗ | `submitting` | **half-live banner DOES fire** at system level ([doctor.py:322](src/fanops/doctor.py:322), [views.py:723](src/fanops/studio/views.py:723)) | ⚠ **system-level only — nothing marks the POST** |
| **one channel malformed, a valid sibling remains** (`F-A`) | `submitting` → 24 h → `needs_reconcile` → 72 h → `GAVE UP:` | ✅ eventually | ✗ | ✅ | ✅ | ✅ `gave_up` bucket | ⚠ **delayed+explicit (72 h). NO half-live warning fires** — a valid route exists, so the operator sees a *healthy* system while one channel silently publishes nothing |
| **rotated API key → `AuthError` every tick** (`C3-F8`) | **no change** | **`None`** | ✗ | `run halted: PostizAuthError…` on **stderr** → the plist's `StandardErrorPath` | **verdict: `alive`** ⚠ | Home banner **does** show the 401 (live GET) | 🔴 **MISLEADING SUCCESS** at `daemon status`; rescued only by the Studio banner |
| **daemon halting every pass** | no change | — | ✗ | run.log lines **are** written (each stage logs) → `daemon_progress` sees `alive_mid` | **verdict: `alive`** ⚠ | — | 🔴 **MISLEADING SUCCESS** — see §3 |
| **`intro_match` gates accumulate** (`INV-04`) | no change | — | ✗ | ✗ | **✗ — `gate_source_id` returns `None`, so `pipeline_status` OMITS them from `by_source`** | ✗ | 🔴 **SILENT** (dormant: `FANOPS_INTRO_TEASE` default OFF) |
| **gate ceiling reset by a torn `attempts.json`** (`C3-F6`) | no change | — | ✗ | ✗ | ✗ | ✗ | 🔴 **SILENT** |
| **`fanops-shrink-*` temp dirs accumulating** (`C3-F4`) | no change | — | ✗ | ✗ | ✗ | ✗ | 🔴 **SILENT** — unbounded disk growth with **no signal of any kind** |
| **`_requeue_transient_failed_for_daemon` txn failed** (`C3-F3`) | no change | — | ✗ | **✗ — the only handler in `post/run.py` with no log** | ✗ | ✗ | 🔴 **SILENT** (blast radius nil today — the return value is discarded) |
| **audit write failed** (disk full) | state **did** change | — | **✗ — the line is lost** | ✗ | ✗ | ✗ | 🔴 **SILENT** — *contract-correct* (`audit.py:46` docstring), but the record of a state change vanishes |
| **`adjust.retire` permanently retires a moment** (`C3-F10`) | `MomentState.retired` | — | ✗ | ✅ (learn pass logs) | ✗ | moment disappears from the render pool | ⚠ **per-item only**, and **irreversible** |
| clip render failure | `ClipState.error` | ✅ | ✗ | ✅ | ✅ | ✅ | ✅ immediate+explicit |
| source stage failure | `SourceState.error` | ✅ typed reason | ✗ | ✅ | ✅ | ✅ Resume button | ✅ immediate+explicit |
| hook burn failed | `Clip.hook_burn_failed` | — | ✗ | ✅ | ✅ `RunSummary.hook_burn_failed` | ✅ | ✅ |
| publish failed (non-transient) | `PostState.failed` | ✅ **redacted** | ✗ | ✅ | ✅ | ✅ Recover cockpit | ✅ immediate+explicit |
| **`GAVE UP:` terminal (Postiz only)** | `needs_reconcile` | ✅ `GAVE UP:` | ✗ | ✅ (logged **once**) | ✅ **`gave_up` is a disjoint digest bucket** | ✅ | ✅ **delayed+explicit — this is the system's best failure surface** |

---

## 3. The two misleading-success surfaces (ranked)

### 🔴 #1 — `daemon status` reports **`alive`** while every pass halts

`daemon.status` ([daemon.py:462-476](src/fanops/daemon.py:462)) deliberately overrides a stale heartbeat when
`daemon_progress` reports `alive_mid` — *"the newest run.log line of **ANY** kind is younger than the ceiling"*.

The intent is sound and is documented: *"that heartbeat only lands after a whole pass finishes, so it must
NEVER flip a fast-logging pass to stale."* **But the consequence was never recorded:** a daemon whose *every
pass halts* still writes run.log lines during the pass (every stage logs before the halt). So `alive_mid` is
`True`, the verdict is **`alive`**, and **no heartbeat has been written in hours**.

`heartbeat_age_s` **is** in the returned dict — but it *"no longer governs the verdict on its own."* So the
data is there and the **verdict discards it**.

> **The operator asking the system's own liveness question gets `alive` while nothing is publishing.**
> Rescued only by the Studio Home banner (which does an independent live GET) — a *different* surface the
> operator may not be looking at.

### 🔴 #2 — the half-live banner does **not** fire when a valid sibling channel exists

`live_ready_channels()` returning **non-empty** is enough to suppress the half-live warning. So the
single-malformed-channel case (Cycle-2 `F-A`, the *more likely* one) produces **a healthy-looking system while
one channel silently publishes nothing for 72 h**.

---

## 4. What is genuinely SILENT (no signal of any kind)

Ranked by consequence:

1. **`fanops-shrink-*` temp-dir accumulation** (`C3-F4`) — unbounded disk growth under `04_agent_io/`, and the
   ledger's `Render.path` points **into** those dirs (`C3-F5`). No log, no metric, no doctor check, no wipe
   path. The only `mkdtemp` in the tree with no cleanup anywhere.
2. **Gate-retry ceiling reset by a torn `attempts.json`** (`C3-F6`) — the bounded 3-attempt escalation becomes
   unbounded, silently.
3. **`intro_match` gate accumulation** (`INV-04`) — and it is *doubly* invisible: `gate_source_id` returns
   `None` for it, so `pipeline_status` **omits it from `by_source`** entirely. Dormant behind a default-OFF
   flag.
4. **A lost audit line** — contract-correct, but the record of a state change vanishes with no trace.

---

## 5. `Post.error_reason` is **four** things at once

The single most overloaded field in the system. `~14 writers`, and **four** distinct machine semantics:

| Semantics | Reader |
|---|---|
| a **retry counter** — `transient_daemon_retry=n/3` | `transient_daemon_retry_count` ([run.py:333](src/fanops/post/run.py:333)) |
| a **terminal marker** — the `GAVE UP:` prefix | `_is_giveup` ([reconcile.py:85](src/fanops/reconcile.py:85)) |
| a **quarantine sentinel** — the `unverified:` prefix | the REST gate ([reconcile.py:99](src/fanops/reconcile.py:99)) |
| **a do-not-look-at-me-again latch** *(new, Cycle 3)* | `if post.error_reason: continue` ([reconcile.py:767](src/fanops/reconcile.py:767)) |

**The fourth is the one that hurts.** Because *any* non-empty `error_reason` permanently suppresses further
reconcile attention, the **breadcrumb that tells the operator a post is stuck is the same mechanism that stops
the system from ever looking at it again.** A `stuck …` reason stamped at hour 7 is still verbatim at hour
100 000 (proven, EXP-4/H6) — so **the reason string is not a status, it is a fossil**, and nothing in the UI
says so.

---

## 6. Observability gaps, as a list

| ID | Gap | Class |
|---|---|---|
| `C3-OBS-1` | `daemon status` reports `alive` while every pass halts | **misleading success** |
| `C3-OBS-2` | A stuck post's `error_reason` is stamped once and never refreshed — no age, no escalation | **misleading (stale) per-item** |
| `C3-OBS-3` | No signal at all for the `fanops-shrink-*` disk leak | **silent** |
| `C3-OBS-4` | No signal when a torn `attempts.json` resets the gate ceiling | **silent** |
| `C3-OBS-5` | The half-live banner is suppressed by any single valid channel | **system-level blind spot** |
| `C3-OBS-6` | `_requeue_transient_failed_for_daemon` is the one `post/run.py` handler with no log line | **silent** (nil blast radius today) |
| `C3-OBS-7` | The `gave_up` digest bucket — the system's best failure surface — **never fires on Zernio** | **silent for a whole backend** |
| `C3-OBS-8` | A lost audit line is unrecoverable and unsignalled | **silent** (contract-correct) |
