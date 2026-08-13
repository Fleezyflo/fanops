# Brief — Close derived-signal seams 1 & 2 (gate age + snapshot freshness)

**Role:** finish what `publish/derived-signal-primitives` started for **seams 1 and 2 only**.  
**Depends on:** that branch (or its PR) landed / available as base — do not re-implement the three primitives from scratch.  
**Not this brief:** seam 3 (LIVE→`unknown` / half-live) is already closed enough for #3; machine-health contract (#4); fail-open escalation (#2); config truth (#1); hashtag/doctor-exit redesign.

**Symptom this closes:** gate age can still remint or stay forever-unknown; snapshot UNKNOWN still collapses into calm UI on secondary surfaces (spine badge, recoverable count, daemon “off”, Postiz outer except, Go-Live empty pills).

**Governing rules:** [AGENTS.md](../../AGENTS.md). Cite symbols (STD-DOC-01). Publish-lane worktree for `config.py` / `views_common.py` if touched; prefer **not** re-touching publish hot files unless a consumer residual forces it.

---

## Three lenses (locked)

| Lens | Binding answer |
|------|----------------|
| **Best practice** | One logical clock for gate age; one typed freshness enum for every snapshot consumer; **UNKNOWN is a first-class UI state**, never mapped to calm-zero / optional-off / empty-healthy |
| **Root cause** | Seam 1/2 landed the *write/read primitives* but left *compat escape hatches* and *secondary consumers* that still fail open to confident defaults |
| **Leanest** | Kill the escape hatches + wire every consumer already on the affect graph. No new package, no health-contract schema, no new `FANOPS_*` (TTL stays a constant beside `_read_snapshot`) |

---

## What already landed (do not redo)

| Seam | Done | Symbols |
|------|------|---------|
| 1 Gate age | Stamp + age from stamp | `agentstep.write_request` writes/preserves `opened_at`; `pipeline_status._gate_opened_epoch` / `_pending_gates`; doctor stale sensor uses epoch |
| 2 Snapshot freshness | Typed read + TTL | `health.SnapshotFreshness` / `SnapshotRead` / `_read_snapshot` / `read_*`; strip `strip_metrics_unknown`; Postiz non-FRESH → show unknown (when channel routes to postiz) |
| 3 LIVE label | Out of scope here | `effective_publish_mode` → `"unknown"`; `_half_live_state` / doctor half-live compute fail |

---

## Residual inventory (the real close-out work)

### Seam 1 — Gate age (still incomplete)

| Residual | Why it still lies | Required close |
|----------|-------------------|----------------|
| **R1a** Corrupt/unreadable prior request → `fail_open("agentstep.write_request.opened_at")` remints `opened_at` | Rewrite of a torn file looks “fresh”; stale WARN never fires | **Fail-closed preserve:** if file exists but stamp unreadable, do **not** mint `now`. Lean: write a fixed ancient ISO (`1970-01-01T00:00:00Z`) so age is *old*, not young |
| **R1b** Legacy request without `opened_at` → epoch `0.0` | Doctor treats falsy epoch as “not stale” (`if oldest_opened and age_s > …`) | `_gate_opened_epoch` returns `None` on miss; doctor/pending put unknown-age first and WARN “gate age unknown” (not silent green). No mtime fallback |
| **R1c** Comments still claim mtime-as-age | Folklore | Grep `mtime` beside `_pending_gates` / `PendingIndex`; fix only claims that mtime **is** age. No CODEMAP refresh |

**Acceptance (seam 1):**

- [ ] N `write_request` rewrites after forced corrupt prior JSON → age is **not younger than** first successful stamp (or is explicitly ancient / unknown-stale — never “now”)
- [ ] Request missing `opened_at` → doctor does **not** pass the stale-gate sensor as clean silence; WARN unknown-age or treat as stale
- [ ] `rg 'st_mtime' src/fanops/pipeline_status.py src/fanops/agentstep.py` → no age truth (comment-only OK)
- [ ] Tests: corrupt-prior remint forbidden; missing-stamp unknown/stale WARN

### Seam 2 — Snapshot freshness (still incomplete)

| Residual | Why it still lies | Required close |
|----------|-------------------|----------------|
| **R2a** Spine: `app.py` maps `strip_metrics_unknown` → `blocked_gates=0` | Rail / Make spine stays calm while strip says unknown | Teach `build_spine` / badge an **unknown** state. **Cut** the int-cast calm-zero. Lean: `blocked_gates=None` + `strip_metrics_unknown`; badge “?” / “!” → gates — never `0` |
| **R2b** `build_system_strip` zeros `recoverable` when metrics unknown | Errored-source alert can go quiet | Lock **ledger fallthrough** for errored sources when metrics unknown (verify current path; add regression). Do not invent a second snapshot |
| **R2c** `daemon_health_strip` → `{verdict:"unknown", installed:False, loaded:False}` | `_daemon_health.html` maps that to **“Hands-off processing is off (optional)”** — calm opt-in, worst remaining lie | Template arm: `verdict == "unknown"` → WARN (“daemon health unknown — snapshot missing/stale”). Never reuse the off/opt-in branch |
| **R2d** `build_system_strip` outer except → `postiz_down={"show": False}` | Raise-shield hides Postiz when helper throws | Outer except → unknown show **if** channel routes to postiz (or route-check failed); else hide |
| **R2e** Go-Live `do_golive_health` non-FRESH → `health=[]` | Empty pills read as “all deps fine” | One unknown pill / “deps unknown (snapshot …)” when not FRESH. No probe on render path |
| **R2f** TTL flap / UI copy | Ops polish | **No new env.** Keep `_SNAPSHOT_TTL_S`. Copy polish only in touched templates. PR note only for refresh cadence |

**Acceptance (seam 2):**

- [ ] Missing/ancient strip metrics → no surface shows calm `blocked_gates=0` (strip, spine/rail badge, Make severity)
- [ ] Daemon snapshot unknown → WARN, never “off (optional)”
- [ ] Postiz: routes-to-postiz + miss/stale/helper-raise → show unknown; never silent hide
- [ ] Go-Live deps non-FRESH → unknown presentation, not empty-healthy
- [ ] No `refresh_*` / `write_json_atomic` on strip / banner / `daemon_health_strip` render path
- [ ] Tests: spine unknown badge; daemon template unknown arm; outer Postiz except → show; golive non-FRESH unknown

---

## Non-goals (hard refuse)

- Machine-health severity enum / `HealthReport` sole constructor (#4)
- New `FANOPS_SNAPSHOT_TTL` / Settings field
- Dual-path “mtime OR opened_at” forever
- Mass fail_open audit / swallow-ratchet theatre
- Seam 3 rework unless a test regresses
- CODEMAP / CONFIG.md rewrites (no new knobs)
- Editing `.agents/lanes.json`

---

## Sealed change surface (α-order allowlist)

Touch **only** what a residual requires:

| Path | Residuals | Lane note |
|------|-----------|-----------|
| `src/fanops/agentstep.py` | R1a | unrestricted |
| `src/fanops/pipeline_status.py` | R1b | unrestricted |
| `src/fanops/doctor.py` | R1b | unrestricted |
| `src/fanops/studio/views.py` | R2b, R2c, R2d | unrestricted |
| `src/fanops/studio/app.py` | R2a | unrestricted |
| `src/fanops/studio/app_routes_golive.py` | R2e | unrestricted |
| `src/fanops/studio/templates/_system_strip.html` | R2a/R2b if copy | unrestricted |
| `src/fanops/studio/templates/_daemon_health.html` | R2c | unrestricted |
| `src/fanops/studio/templates/_health_pills.html` and/or `_golive_health.html` | R2e | unrestricted |
| `src/fanops/studio/views_common.py` | only if R2d needs shared helper | **publish** hot |
| matching `tests/test_*` | triad extensions | unrestricted |

Do **not** touch `health.py` unless forced (prefer leave `_SNAPSHOT_TTL_S` as-is).

---

## Execution order

1. Worktree from the landed derived-signal branch (or merge it first). Publish prefix only if `views_common.py` is required.
2. Close **R1a → R1b** (gate age escape hatches) + tests.
3. Close **R2c → R2a → R2d → R2e** (worst UI lie first: daemon opt-in → spine calm-zero → Postiz hide → Go-Live empty).
4. R2b verify + lock with one regression.
5. `./scripts/check.sh`. No local pytest.
6. One DIFF MINIMALIST pass (CUT outside allowlist). One HONESTY HUNTER on R1/R2 only. Stop.

---

## Forbidden laziness

- Logging “unknown” while still rendering `0` / “off” / empty pills
- Keeping spine calm-zero “because build_spine needs int” — change the spine type/template instead
- Reminting `opened_at` on any path where a request file already existed
- Treating missing `opened_at` as “not stale”
- New freshness framework / second snapshot file / mtime compatibility shim

---

## Rollback

Revert the close-out commit(s) on the branch. No `reset --hard`, no force-push to `main`.

---

## Report format (end)

1. Residuals closed (R1a…R2e) with symbols  
2. Diff stat + files (α-order)  
3. CUT list from DIFF MINIMALIST (must be empty)  
4. Explicit still-deferred (#4 health contract, TTL flap ops note)  
5. Risks: unknown-age WARN noise on legacy gates until answered; spine “?” UX  

---

## Operator one-liner

**Best:** every derived age/freshness consumer speaks Fresh|Stale|Missing|Unreadable|Unknown-age — never calm.  
**Root:** delete remint + falsy-epoch pass + calm secondary UIs.  
**Lean:** allowlist above; constants already exist; finish the graph, don’t rebuild it.
