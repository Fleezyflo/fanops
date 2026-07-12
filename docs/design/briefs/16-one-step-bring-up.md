# Brief — One-step, self-healing system bring-up

**Unit tag:** `Unit: bringup`
**Status:** brief only — no implementation in this document.
**Author context:** every manual step this brief eliminates was performed by hand on 2026-07-12 to get the system running after a code sync. This brief makes that sequence a single idempotent command that never needs a human again.

---

## 1. The operator's ask (verbatim intent)

> "Everything you just had to do needs to become an automated process… a simple one-step way to get everything up and running correctly [so] they never have to happen again."

**Achieved when:** one command — `fanops up` (name TBD in §7) — takes the machine from *cold or half-broken* to *all four planes healthy and on current code*, self-healing the two faults that today require manual intervention, and printing a single honest READY/NOT-READY verdict. No `docker compose`, no `psql`, no `launchctl kickstart`, no drop-table surgery, ever again.

---

## 2. Forensic account — what "getting everything up" actually required by hand

The bring-up on 2026-07-12 touched **four planes**. Each is listed with the exact manual action taken and the seam that must own it going forward.

| # | Plane | Manual action taken (by hand) | Owning seam today | Gap |
|---|-------|-------------------------------|-------------------|-----|
| A | **Git baseline** | Machine was on a stale branch; `git fetch`, discovered local `main` 7 behind, fast-forwarded `main` → `origin/main` `8c8a7e7` | — (none) | No bring-up step verifies the tree is current before launching services on it |
| B | **Docker + Postiz stack** | Found stack `Exited`; `docker compose up -d`; Postiz API returned **HTTP 502 for >2 min** | [`postiz-ondemand.sh:53 ensure()`](../../../postiz-selfhost/postiz-ondemand.sh) — *already* does Docker-up + idempotent up + honest 200/401 probe | **The Mastra crash-loop self-heal is missing** — see C |
| C | **Postiz `mastra_ai_spans` crash-loop** | Diagnosed `MastraError: tables can have at most 1600 columns`; brought up postgres alone; verified table empty + no FK dependents; `DROP TABLE mastra_ai_spans CASCADE`; full `up -d`; confirmed backend `↺0` + API `200` | Detected but **punts to a human** at [`postiz-ondemand.sh:64-68`](../../../postiz-selfhost/postiz-ondemand.sh) → `docs/POSTIZ_OPS.md §4` | The one true gap. §4 is a documented *manual* runbook for a *recurring* fault (POSTIZ_OPS.md §6: "until Postiz ships a fix, §4 is the workaround") |
| D | **FanOps daemon** | Running 6h on pre-sync code; `launchctl kickstart -k gui/$UID/com.fanops.run` to restart onto `8c8a7e7`+U8; confirmed new PID, clean tick, healthy Postiz re-probe | [`daemon.ensure`](../../../src/fanops/daemon.py) (cli.py:600) ensures *aliveness*, not *freshness* | **No "restart if running on stale code" step** — `ensure` leaves a 6h-old process untouched even after a sync |

**What already works and must NOT be rebuilt** (the brief builds on these, verbatim):
- `postiz-ondemand.sh` `docker_up()` (line 31) — starts Docker Desktop and waits. Proven self-heal.
- `postiz-ondemand.sh` `api_up()` (line 47) — the **honest** probe (`200|401` past nginx, not the lying container health-check). This is the correct readiness signal for every plane's Postiz check.
- `postiz-ondemand.sh` `ensure()` idempotency + `WAIT_S=180` cold-boot budget.
- `fanops daemon {install,ensure,stop,status,tail}` (cli.py:565–606) — launchd lifecycle already exists.
- The launchd job graph: `com.fanops.run` (daemon, RunAtLoad+KeepAlive), `com.fanops.keeper` (`fanops daemon ensure`, RunAtLoad), `com.fanops.postiz-reaper` (`postiz-ondemand.sh reap` @300s, idle-down), `com.fanops.media-sync`.

**The reaper is not a bug.** `postiz-reaper` intentionally stops Postiz after `IDLE_MIN=20` min idle to save RAM on a 16 GB host (`postiz-ondemand.sh:2-4`). So "Postiz is down" is a *normal resting state*, and bring-up must treat starting-from-down as the common path, not an error.

---

## 3. Root-cause verdict (why one-step doesn't exist today)

Two independent gaps, each small:

1. **Plane C — the crash-loop self-heal is documented as manual.** `postiz-ondemand.sh:65` already *detects* `MASTRA_STORAGE_PG_ALTER_TABLE_FAILED`. It then prints a pointer to a human runbook instead of executing it. The recovery is deterministic, safe, and fully specified in POSTIZ_OPS.md §4 — it is automatable as-is. This is the difference between "one step" and "20 minutes of DB surgery."

2. **No composed entrypoint.** The pieces exist (`postiz-ondemand.sh ensure`, `fanops daemon ensure`) but nothing chains them into one ordered, verified sequence with a single verdict. Plane A (git freshness) and Plane D (daemon *freshness*, not just aliveness) have no bring-up owner at all.

Everything else — Docker start, idempotent Postiz up, the honest probe — is already solved. **This is a composition + one-self-heal-branch job, not a rebuild.**

---

## 4. Design — the minimal surgical change

### 4.1 Fault C: make the crash-loop self-heal (the core change)

**Where:** `postiz-ondemand.sh`, replace the punt at lines 64–68 with a bounded self-heal, gated by exactly the POSTIZ_OPS.md §5 preconditions.

**Behaviour (encode POSTIZ_OPS.md §4 as code, once):**
1. After `ensure()`'s wait loop fails AND `docker logs postiz --since 10m | grep -q MASTRA_STORAGE_PG_ALTER_TABLE_FAILED`:
2. **Guard (all must hold — POSTIZ_OPS.md §5):** `postiz-postgres` is up/healthy; `SELECT count(*) FROM mastra_ai_spans` is **0** (empty telemetry); **no FK** references `mastra_ai_spans` (`pg_constraint WHERE confrelid='mastra_ai_spans'::regclass` is empty). If any guard fails → do NOT drop; fall through to the current human-pointer message (the fault is then genuinely novel and worth a human).
3. **Heal:** `docker exec postiz-postgres psql -U postiz-user -d postiz-db-local -c "DROP TABLE IF EXISTS mastra_ai_spans CASCADE;"`, then `compose restart postiz`, then re-enter the `api_up` wait loop **once**.
4. **Idempotency / loop-cap:** attempt the drop **at most once per `ensure` invocation**. If the API still doesn't answer after the post-drop wait → emit NOT-READY with the log tail and stop (never a drop loop).
5. **Credentials:** read DB name/user/password from the compose file's `DATABASE_URL` (do not hardcode — `postiz-ondemand.sh` already resolves `COMPOSE_FILE`). Values confirmed 2026-07-12: db `postiz-db-local`, user `postiz-user`.

**Safety rationale (why the drop is sound, per the live verification):** `mastra_ai_spans` is Mastra AI-span **telemetry**, 0 rows, no dependents; the core publish tables (`Integration`, `Post`, `IntegrationsWebhooks`, `AutoPost`) are separate and untouched; Mastra recreates the table clean on next boot. The guard in step 2 re-proves this at heal-time rather than trusting it.

**Do NOT** attempt the upstream "raise the column cap / disable Mastra telemetry" fix (POSTIZ_OPS.md §6) — that is Postiz's to ship. This brief automates the *workaround*, which is what recurs.

### 4.2 Fault D: daemon freshness (restart-if-stale)

**Where:** the new entrypoint (§4.3), not `daemon.ensure` (leave `ensure`'s aliveness contract intact — the keeper depends on it).

**Behaviour:** after ensuring the daemon is loaded, if it is **already running** and the operator is bringing the system up (implying code may have changed), `launchctl kickstart -k gui/$UID/com.fanops.run` to restart it onto current code, then confirm one clean heartbeat in `07_reports/daemon.err`/`.out` (the `{"heartbeat": …, "fanops_version": …}` line). A restart is safe: posts are born `awaiting_approval`, nothing publishes on restart (CLAUDE.md approval-lifecycle invariant).

**Open decision for the implementer (not the operator):** whether the kickstart is unconditional on every `up`, or conditional on a detected version/mtime delta. Default recommendation: **unconditional on explicit `up`** (cheap, deterministic, the operator asked to "get everything up correctly"), while the keeper's periodic `ensure` stays aliveness-only so it never thrashes a healthy daemon.

### 4.3 The single entrypoint

Add one command that chains the planes in dependency order, each step using the **existing** healthy-probe as its gate, and prints ONE verdict. Two viable homes — the implementer picks:

- **Option 1 (recommended): a `fanops up` CLI verb** — discoverable, testable, lives with the rest of the daemon lifecycle in `daemon.py`/`cli.py`, consistent with `fanops daemon ensure`. Shells out to `postiz-ondemand.sh ensure` for the Postiz plane (don't reimplement Docker/compose in Python).
- **Option 2: a top-level `bin/fanops-up` shell script** — thinner, but less discoverable and untested by the suite.

**Ordered sequence (idempotent, each gated on the prior's healthy signal):**
1. **Git freshness (advisory, non-fatal):** `git fetch` + report if the checkout's `main` is behind `origin/main`. Bring-up must NOT auto-merge or mutate the tree (that is an operator decision — cf. the "stale local main produced a false verdict" and "reset --hard clobbers accounts.json" lessons). Print the delta and continue; the operator syncs if they choose.
2. **Docker + Postiz:** `postiz-ondemand.sh ensure` (which now self-heals per §4.1). Gate: `api_up` returns 200/401.
3. **Daemon:** ensure loaded (`fanops daemon ensure`) + freshness restart (§4.2). Gate: one clean heartbeat line newer than the restart.
4. **Studio:** confirm the Studio port answers; if not, report the exact launch command (`fanops studio --host 127.0.0.1 --port 8787`, already in `.claude/launch.json`). Studio is a foreground dev server, not a launchd job — bring-up **reports** its status; it does not daemonize it unless the implementer adds a `com.fanops.studio` job (out of scope unless the operator wants Studio always-on).
5. **Verdict:** print a 4-line status (git / postiz / daemon / studio) and a single `READY` or `NOT-READY: <first failing plane + its log tail>`.

**Fail posture:** any plane's healthy-probe failing → stop at that plane, print NOT-READY with the diagnostic (the log tail, not a generic message), non-zero exit. Never report READY on an unproven plane (mirrors the `api_up` honesty principle — POSTIZ_OPS.md §3).

---

## 5. Acceptance (binary)

1. **Cold machine** (Docker down, Postiz down, no daemon): one command → Docker starts, Postiz comes up, daemon loads + ticks, verdict READY; Postiz API returns 200 with the key. No manual step.
2. **Crash-loop present** (`mastra_ai_spans` at the 1600-column wall): the same one command self-heals (drops the empty table under guard, restarts, re-probes) and reaches READY — **without** a human running §4. Re-running the command when already healthy is a no-op that re-prints READY (idempotent).
3. **Guard holds:** if `mastra_ai_spans` is non-empty OR has a FK dependent, the command does **not** drop it — it emits NOT-READY pointing at the human runbook (the fault is then genuinely novel).
4. **Stale daemon:** daemon running on old code → the command kickstart-restarts it and confirms a fresh heartbeat; a *healthy current* daemon is not thrashed by the keeper's periodic `ensure`.
5. **Honest verdict:** with Postiz's backend deliberately dead behind nginx, the command reports NOT-READY (never trusts the lying container health-check).
6. **No approval bypass / no publish:** bring-up publishes nothing; all posts remain `awaiting_approval`; no `FANOPS_LIVE`/poster flip.

---

## 6. Non-goals (binding)

- **No upstream Postiz/Mastra fix** (column-cap raise, telemetry disable) — that is Postiz's to ship (POSTIZ_OPS.md §6). This automates the *workaround only*.
- **No auto-merge / tree mutation** during bring-up — git freshness is advisory; the operator decides to sync (cf. accounts.json-clobber and false-verdict lessons).
- **No change to the reaper's idle-down policy** — Postiz-down-when-idle is intended (16 GB host). Bring-up coexists with it.
- **No change to `daemon.ensure`'s aliveness contract** — the keeper depends on it; freshness lives in the new entrypoint.
- **No new always-on Studio daemon** unless the operator explicitly asks — bring-up reports Studio status.
- **No dependency on this conversation** — the entrypoint and self-heal are self-contained.

---

## 7. Files touched (implementation scope — for the worker, not this brief)

| File | Change |
|------|--------|
| `~/postiz-selfhost/postiz-ondemand.sh` | Replace the §4.1 punt (lines 64–68) with the guarded one-shot self-heal |
| `src/fanops/daemon.py` | Add freshness-restart helper (§4.2); a `up()` composer if Option 1 |
| `src/fanops/cli.py` | Register `fanops up` verb (Option 1) |
| `docs/POSTIZ_OPS.md` | §4 → note it is now automated by `ensure`; keep the manual steps as the guard-fails fallback |
| `docs/CONFIG.md` | Document any new env knob (e.g. a `FANOPS_BRINGUP_*` toggle) if added |
| tests | Cover: crash-loop self-heal path, guard-blocks-drop path, idempotent re-run, honest NOT-READY. The Postiz/Docker planes are shelled out — test the Python composer + the shell self-heal branch (bash test harness) separately |

**Naming decision for the implementer:** `fanops up` vs `fanops bringup` vs `bin/fanops-up`. Recommendation: **`fanops up`** (shortest, discoverable, testable).

**Estimated scope:** one self-heal branch in an existing 93-line script + one CLI composer verb + tests. Small. The heavy lifting (Docker self-heal, honest probe, idempotent Postiz up, launchd lifecycle) already exists and is reused verbatim.

---

## 8. Risk notes

- **The drop is destructive by nature** — mitigated by the §4.1 guard (empty + no-dependents re-checked at heal-time) and the once-per-invocation cap. If Mastra ever starts putting real data in `mastra_ai_spans`, the empty-count guard blocks the drop automatically and the command punts to a human — fail-safe.
- **Cold boot is slow** (~180s: Docker + temporal + elasticsearch + 2 postgres + Postiz backend). The entrypoint must use the existing `WAIT_S=180` budget and stream progress, not appear hung.
- **launchctl domain string** is `gui/$(id -u)/com.fanops.run` on this host (confirmed 2026-07-12). The implementer must resolve `$UID` at runtime, not hardcode.
- **zsh gotcha (observed):** don't stuff a `docker exec … psql -c` invocation into a shell variable and re-run it — it won't word-split into argv. Keep psql calls literal in the script.
