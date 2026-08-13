# Brief — Single machine-health / observability contract

**Root issue #4 in remediation order** (after config truth → fail-open escalation → derived-signal primitives).

**Principle:** one authoritative **machine-state model** maps every observable condition → a locked **severity** → **one primary operator channel**. Secondary surfaces (Home strip, Go-Live panel, Prometheus, `fanops status`) are *projections* of that model — never independent health owners. Exit codes and WARN/FAIL mean the same thing everywhere. **Unknown never fails open to healthy / LIVE.**

**Grounded on:** current `main` inventory (symbols primary, STD-DOC-01). Not an implementation ticket.

---

## Three lenses

| Lens | Answer |
|---|---|
| Best practice | Single typed health owner; severity enum (not bool+optional warn); freshness/TTL on every snapshot; projections derive from one constructor; CI forbids new ad-hoc channels and `ok=True`+`warn` soft-lies |
| Root cause | MOL-298 declared `health_model` “ONE typed health owner,” but the product still maintains parallel verdicts (doctor checks, strip snapshots, daemon banners, Go-Live doctor embed, `fanops status` counters, `fanops up` planes, `/healthz`, notes). Severity is mostly boolean + optional `warn` that **does not** flip exit. Snapshot reads fail-open to green zeros / hidden banners. Derived age (gate mtime) and label fail-opens (`effective_publish_mode` → `"live"`) defeat honesty |
| Leanest | Keep `HealthReport` / `build_health_report` as the sole constructor; add severity + freshness; make strip/Go-Live/CLI exit consume it (or thin typed projections); ratchet tests so new channels and soft-lies fail CI. Do not invent a second observability framework |

---

## KEEP — prerequisites (must land first)

This brief **depends on roots 1–3**. Do **not** start product work here until those deliverables exist; otherwise this contract will *encode* today’s lies.

| Prerequisite | Brief / status | Must deliver for this contract |
|---|---|---|
| **#1 Config truth plane** | [config-truth-plane.md](config-truth-plane.md) | One registration + parsers + Config façade. Doctor-strict vs runtime-lenient stay dual *policy*, not dual *worlds*. Health checks that cite env must read the same plane doctor validates |
| **#2 Fail-open escalation** | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) | Shared postures (`DEGRADE` / `REFUSE` / `TERMINATE` / `NONZERO`). Progress-blocking conditions must be allowed to flip process health (today: operational sensors stay `ok=True, warn=True` and `report_is_healthy` ignores warn). Gate attempt ceilings not solely mtime-aged |
| **#3 Derived-signal primitives** | [derived-signal-primitives.md](derived-signal-primitives.md) — close residuals: [derived-signal-close-1-2.md](derived-signal-close-1-2.md) | (a) Gate age on `opened_at` logical clock, not request-file **mtime**. (b) Strip / dep / daemon snapshots TTL via `SnapshotFreshness`; stale → `UNKNOWN`, never silent green. (c) No fail-open-to-LIVE (`effective_publish_mode` → `unknown`; half-live compute fail → not solid LIVE). **WP3 blocked until close-1-2 DoD** |

Do **not** expand this brief into implementing 1–3. If a WP needs a missing primitive, mark **BLOCKED** and stop.

Do **not** start here:

- Control vs data plane untangle (#5) — [control-data-plane-untangle.md](control-data-plane-untangle.md) consumes this contract after it exists
- Swallow-ratchet “honesty vs call-name” retarget (#6)
- New monitoring SaaS, OpenTelemetry rewrite, second health package

Product policy that stays:

- No auto-publish / Review approval gate
- `go_live` sole writer of `FANOPS_LIVE`
- Runtime fail-open on some env typos remains *runtime* policy; doctor/health exit must stay loud (post-#1)

---

## Current wiring (the mess, named)

```
                         os.environ / ledger / launchd / run.log / agent gates
                                          |
     +-----------+-----------+------------+------------+-----------+-----------+
     |           |           |            |            |           |           |
 doctor     health_model  health.py   views.strip  views.daemon  pipeline_   daemon.up
 _assemble   build_health  snapshots   build_system  health /     status      READY/
 + sensors   report        (no TTL)    strip         strip        status      NOT-READY
     |           |           |            |            |           |           |
     +----- HealthReport ----+            |            |           |           |
     |   (checks ok/warn,    |            |            |           |           |
     |    notes, deps)       |            |            |           |           |
     v                       v            v            v           v           v
 cli.cmd_doctor         cmd_health    Home strip   Home daemon  fanops      fanops up
 golive_status          /metrics      (zeros on    health       status      (own plane)
 autopilot notes        DepHealth     miss)        (snapshot)   (exit 0)
 setup_state            pills
 init_flow              studio ensure_up
 /healthz = process-up ONLY (orthogonal; keep)
```

**Claimed single owner:** `health_model.build_health_report` / `HealthReport` / `report_is_healthy`.

**Actual parallel owners (evidence):**

| Channel | Symbol(s) | What it claims |
|---|---|---|
| Doctor CLI | `cli.cmd_doctor`, `doctor._assemble_doctor_checks`, `doctor._operational_sensor_checks`, `doctor._doctor_notes` | PASS/WARN/FAIL table; exit via `report_is_healthy` |
| Health CLI | `cli.cmd_health` | Same report; **human print omits checks** — exit can be 1 while stdout shows only deps/notes |
| Typed model | `health_model.HealthReport`, `build_health_report`, `DepHealth`, `report_is_healthy` | “healthy” = no `ok:False` check and no down dep; **ignores `warn`** |
| Dep bring-up | `health.system_health`, `ensure_up`, `refresh_*_snapshot`, `read_*_snapshot` | Live probes on write; snapshot read on Studio — **no TTL** |
| Home strip | `views.build_system_strip`, `health.read_strip_metrics` | LIVE/half-live, blocked gates, failed, errored, Postiz banner, insights |
| Daemon widget | `views.daemon_health`, `views.daemon_health_strip`, `_daemon_health.html` | launchd verdict; strip path is snapshot + live `heartbeat_stale` overlay |
| Go-Live | `views.golive_status` → `doctor.doctor_report` + `daemon_health` + `_half_live_state` | Full doctor embed + deps strip via `do_golive_health` |
| Go-Live deps route | `app_routes_golive.do_golive_health` | Snapshot read; optional `refresh=1` **write-on-read** of all snapshots |
| Status CLI | `cli.cmd_status`, `pipeline_status.source_backlog`, `status_control_lines` | Counter dump + `setup_state` / `setup_next_action`; **always exit 0** |
| Autopilot | `autopilot.autopilot` | Prints doctor checks; **`cmd_autopilot` returns 0 even if checks fail** |
| Init | `init_flow.run_init`, `cli.cmd_init` | `doctor_clean` = no `ok:False` (warns ignored); exit 1 if failed checks |
| Bring-up | `cli.cmd_up`, `daemon.up`, `daemon.format_up_report` | Separate READY/NOT-READY plane set |
| Prometheus | `health_model.render_prometheus_metrics` | Gauges + `fanops_metrics_degraded`; fail-open |
| Process liveness | `studio.app.healthz` | `{ok:True}` process up — not machine health |
| Studio launch | `cli` studio path + `ensure_up` + `system_health` print | Bring-up side effect + live dep print |
| ROOT warn | `daemon.root_divergence` (CLI main) | stderr WARN only; not a doctor check |
| Digest / run.log | `digest.write_digest`, run.log breadcrumbs | Operational narrative; not severity-contracted |
| ActionResult | `actions_common.ActionResult` | Mutation outcome `ok`/`error` only — **not** machine health (keep separate) |

---

## Inventory — severity model today

### Exit / healthy

| Rule | Symbol | Behavior |
|---|---|---|
| Healthy iff | `report_is_healthy` | Any check with `ok` false **or** any `DepHealth.ok` false → unhealthy. **`warn` ignored.** Notes ignored |
| Doctor exit | `cli.cmd_doctor` | `0` if healthy else `1`. `--fix-routing` **always 0** |
| Health exit | `cli.cmd_health` | Same `report_is_healthy` |
| Init exit | `cli.cmd_init` | `doctor_clean` = no failed checks (same hole) |
| Autopilot exit | `cli.cmd_autopilot` | **Always 0** after printing failures |
| Status exit | `cli.cmd_status` | **Always 0** |
| Up exit | `cli.cmd_up` | `0` iff `daemon.up` `ready` (own definition) |

### Check shape

| Shape | Where | Effect |
|---|---|---|
| `{label, ok, hint}` | `doctor._check` | FAIL when `ok` false |
| `ok=True` + `warn` + `warn_hint` | Meta lead-window, PATH≠auth, half-live compute error, `_operational_sensor_checks` | Rendered WARN in `cmd_doctor`; **exit stays 0** |
| Soft-ok with hint text | `_hashtag_scrape_check` not configured / no session | `ok=True` (setup incomplete looks PASS) |
| Notes | `_doctor_notes` | Informational; never exit |

### Fail-open-to-green / LIVE (severity lies)

| Path | Symbol | Lie |
|---|---|---|
| Strip metrics miss | `build_system_strip` except → `blocked=0` | Missing/stale snapshot → no gate alert |
| Half-live except | `_half_live_state` | Exception → `(False, "")` = not half-live |
| Publish mode label | `Config.effective_publish_mode` | Accounts error → `"live"` |
| Half-live doctor compute | `_assemble_doctor_checks` half_live branch | Exception → `half_live=False` + WARN only |
| Dep / Postiz banner | `postiz_health_for_banner` | Snapshot miss → `{show: False}` |
| Daemon strip miss | `daemon_health_strip` | `None` → empty HTML (silent) |
| Operational sensors | `_operational_sensor_checks` | except → **drop sensor** (debug log) |
| Postiz dep skip | `postiz_dep_health` | No creds → `ok=True` “skipped” |
| Metrics endpoint | `render_prometheus_metrics` | except → degrade gauges + `fanops_metrics_degraded=1` (honest flag exists; UI strip lacks equivalent) |

### Derived-signal defects (owned by #3; listed so #4 does not re-encode)

| Defect | Symbol | Effect |
|---|---|---|
| Gate age = request mtime | `pipeline_status._pending_gates`, doctor stale-gate sensor | Reseed/rewrite resets age → WARN never fires |
| Snapshots TTL-less | `health.read_strip_metrics` / `read_dep_snapshot` / `read_daemon_strip_snapshot` | Forever-fresh if file exists |
| Dual writers | `refresh_runtime_snapshots` vs Studio `refresh=1` vs daemon tick | Stale vs fresh split-brain across tabs |

---

## Inventory — state vocabulary (implicit) × surface

| Machine state | Surfaced by | Severity today |
|---|---|---|
| Pending / stale agent gates | doctor WARN sensor; strip `blocked_gates`; `fanops status` `awaiting_*`; Make spine | WARN / danger link / exit 0 |
| Sources `blocked_on_gates` | doctor WARN; `source_backlog`; status; setup_next | WARN / prose |
| Parked reopens (`pending_reopen`) | doctor WARN; status `reopens_parked` | WARN / count |
| Degraded / errored sources | doctor WARN; strip errored; status | WARN / danger |
| Half-live (`is_live` ∧ ¬`live_route_exists`) | doctor FAIL; strip + Go-Live banners | FAIL / danger (except compute fail → WARN) |
| Daemon dead / unloaded / exec_fail | `_daemon_liveness_check` FAIL; daemon widgets; `daemon.status` | FAIL / warn banner |
| Mid-pass wedged | `daemon_progress` + doctor liveness | FAIL |
| PATH≠auth (LLM CLI) | doctor WARN on PATH check | WARN, exit 0 |
| Scrape cooldown (no healthy peer) | doctor WARN; run.log severity ladder | WARN; Studio hashtag UI **omits** reason |
| Scrape soft-stall (creds, no session) | `_hashtag_scrape_check` soft-ok | PASS with hint |
| Credentials soft-stall (Meta/Postiz/Zernio/LLM) | doctor reach/expiry/PATH; digest/runbook | Mixed FAIL/WARN/PASS |
| IG insights blocked | doctor FAIL; strip danger | FAIL / danger |
| ROOT divergence | `daemon.root_divergence` CLI WARN | stderr only |
| Setup ladder | `SetupState` / `setup_state` / `setup_next_action` | Parallel vocab ≠ HealthReport |
| Bring-up planes | `daemon.up` READY | Orthogonal channel |
| Process up | `/healthz` | Not machine health |

---

## Duplication / contradiction (same fact, different answers)

1. **Gates blocked:** Home strip may show `0` (stale/missing `strip_metrics.json`) while `fanops doctor` WARN / `fanops status` show pending.
2. **Deps:** Go-Live/`refresh=1` writes live probe; Home pills may read older snapshot; doctor/`cmd_health` probe live — three freshness classes.
3. **Daemon:** Go-Live `daemon_health` live `daemon.status`; Home `daemon_health_strip` snapshot + overlay — can disagree after unload.
4. **Healthy exit vs WARN table:** Doctor prints WARN and exits 0; monitors keying exit see green.
5. **`fanops health` vs `fanops doctor`:** Same `report_is_healthy`; human health omits checks → confusing stdout.
6. **Autopilot “still needs a human” + exit 0.**
7. **Half-live:** Strip/Go-Live share `_half_live_state`; doctor has parallel compute — exception paths diverge (strip silent vs doctor WARN).
8. **`setup_state` LIVE vs doctor FAIL:** Can be LIVE while daemon liveness / insights / scrape FAIL (setup ≠ health).
9. **Postiz “ok”:** nginx-era folklore fought by `postiz_health_probe`, but skipped-configured deps still `ok=True`.

---

## Defect classes (structural — this brief owns)

### D1 — Multi-channel ownership without projection rule
Many constructors invent operator-facing health. `HealthReport` is a *hub*, not a *law*.

### D2 — Severity is boolean + optional soft-warn
No enum. Progress-blocking and cosmetic share `ok=True, warn=True`. Exit collapses warn to green.

### D3 — Fail-open-to-healthy / fail-open-to-LIVE on observe paths
Missing snapshot, unreadable accounts, sensor except → UI green or LIVE-looking labels.

### D4 — Freshness undefined on observe path
No TTL; write-on-read (`do_golive_health?refresh=1`) mutates control files from CP observe (feeds #5).

### D5 — Parallel vocabularies
`SetupState`, `daemon.status` verdict strings, `up` READY, spine `severity` warn/info/danger, check dicts — no shared machine-state enum.

### D6 — No enforcement against new channels
Nothing fails CI when a subsystem adds another health strip, another `ok`+`warn` shape, or another exit-0 “readiness” printer.

---

## Target end-state

### 1. One machine-state model

Typed owner (extend `HealthReport` / `build_health_report` — do not fork):

- **States** (closed set; names illustrative — lock in WP0): e.g. `HEALTHY`, `DEGRADED`, `BLOCKED`, `MISCONFIGURED`, `DEPENDENCY_DOWN`, `PUMP_DEAD`, `UNKNOWN`, plus setup-only if still needed as *projection* of the same facts.
- Every check / sensor / dep maps into **exactly one** state contribution.
- **Freshness:** each fact carries `observed_at` + `ttl` (from #3). Expired → contribute `UNKNOWN`, never previous green.

### 2. Severity enum (locked)

| Severity | Meaning | Exit (`fanops doctor` / `fanops health` / init readiness) |
|---|---|---|
| `OK` | Confirmed healthy within TTL | 0 |
| `INFO` | Expected steady-state (approval backlog note) | 0 |
| `WARN` | Needs operator attention; machine still progressing | 0 **only if** escalation policy (#2) classifies as non-blocking |
| `FAIL` | Misconfig, dep down, pump dead, progress-blocked, or **UNKNOWN on a required signal** | 1 |
| `UNKNOWN` | Probe/snapshot/ledger unreadable or past TTL | **FAIL for required signals** (never green) |

**Banned:** `ok: bool` + optional `warn: bool` as the public contract. Migration may keep dict `as_dict()` temporarily but severity field is mandatory.

### 3. One primary operator channel

**Primary:** `fanops doctor` (human + `--json`) over `build_health_report` only.

**Allowed secondary projections** (must call the same constructor or a pure projector — no re-probe):

| Projection | Allowed role |
|---|---|
| Home system strip | Compact badges from report + TTL-aware snapshots filled by **data-plane refresh**, not CP invent |
| Go-Live readiness section | Same report fields (stop embedding a second assembly) |
| Daemon partial | Projector of pump/daemon slice |
| `fanops status` | Pipeline backlog counters + **pointer** to doctor for health; or thin projection — not a third healthy definition |
| `/metrics` | Gauges from same model |
| `fanops health` | Alias/subset of doctor (deps focus) — same exit semantics |
| `/healthz` | Process liveness only (explicit non-goal of machine health) |
| `fanops up` | Bring-up plane — must not redefine “machine healthy”; may remain infra READY with clear naming |

### 4. Exit / WARN / FAIL semantics (locked)

- `report_is_healthy` → rename or retarget to **severity-aware**: healthy iff overall severity ∈ {`OK`, `INFO`} (and WARN only when #2 marks non-blocking).
- Progress-blocking (stale gates past ceiling, blocked_on_gates with no progress, pump dead, required UNKNOWN) → `FAIL` / exit 1 (align #2).
- Soft setup incompleteness that is intentional N/A → explicit `N/A` / omit check — **not** `ok=True` with prose hint pretending PASS.
- `cmd_autopilot` / readiness printers: exit non-zero when report unhealthy (or stop claiming readiness).
- `cmd_status` stays exit 0 **only if** it does not claim health; if it prints health summary, it must share exit contract or label itself backlog-only.

### 5. What UNKNOWN means

- Required signal missing, timed out, past TTL, or exception on read → `UNKNOWN`.
- `UNKNOWN` on required class → overall `FAIL` (exit 1).
- **Never** map UNKNOWN → LIVE badge, `effective_publish_mode` `"live"`, strip zero counts, or skipped sensor.

### 6. ActionResult stays out

Studio mutation `ActionResult` is per-action outcome — not machine health. Do not merge. Ban inventing `ActionResult(ok=True, warn=True)` as a health channel.

---

## Enforcement (so the mess cannot return)

Design for FanOps: local `./scripts/check.sh`; tests CI-only; prefer AST/registry ratchets like `tests/test_swallow_ratchet.py`.

| Gate | Mechanism |
|---|---|
| Single constructor | AST/import ratchet: Studio templates/views and CLI health exits may only obtain machine health via `build_health_report` / named projectors in `health_model` (allowlist). Ban new `doctor_report` forks |
| Check shape | Forbid new check dicts without `severity` (or Typed check). Ban new `warn=True` without severity field; baseline shrink-only for legacy |
| Strip contract | `build_system_strip` must read TTL-aware contract / snapshot schema; test: missing/stale snapshot → UNKNOWN/FAIL presentation, **not** zeros |
| No new channels | Registry file or test listing allowed operator health surfaces; adding a CLI/UI health printer outside the list fails CI |
| Exit honesty | Tests: progress-blocking sensor → `report_is_healthy` false; autopilot/init exit tracks report; `cmd_health` human path surfaces failing checks or points to `--json` |
| Label honesty | Tests: accounts read failure must not yield `"live"` publish label (post-#3) |
| Arch | `python -m tools.arch regen` when scanned lines shift |

---

## Explicit non-goals

| Anti-goal | Why |
|---|---|
| Implement config / escalation / mtime-TTL primitives here | Wrong order; mark BLOCKED |
| Merge `/healthz` into doctor | Process ≠ machine |
| Replace launchd/`fanops up` with doctor | Different job; rename clarity only |
| Mass-rewrite 300 fail_open sites | Owned by #2 |
| OpenTelemetry / new metrics stack | Bloat |
| Auto-heal from Studio observe path | Dual-write disease (#5) |
| Cards/dashboards redesign | Projection styling after contract |
| Edit `lanes.json` to steal hot files | Stop and report |

---

## Work packages (ordered inside this brief)

### WP0 — Inventory freeze (read-only)

Freeze channel × symbol × severity table in the PR (this brief’s inventory). Confirm #1–#3 status; list BLOCKED items. No product code.

### WP1 — Severity + healthy predicate

- Add severity to check/dep model; migrate `_check` / sensors / deps.
- Retarget `report_is_healthy` (or successor) per locked table.
- `cmd_doctor` / `cmd_health` / `cmd_init` / `cmd_autopilot` exit honesty.
- CI tests written (not run locally).

### WP2 — Single constructor + projectors

- Strip / Go-Live / daemon partial / metrics consume projectors only.
- Remove duplicate half-live compute (one function feeding model + UI).
- Stop Go-Live embedding a second assembly path.

### WP3 — Freshness wiring (requires #3)

- Consume TTL/UNKNOWN from derived-signal primitives on all snapshot reads.
- Stale/missing → UNKNOWN UI, not green zeros / hidden banners.
- If #3 not landed: **BLOCKED**.

### WP4 — Channel ratchet + folklore purge

- AST/registry enforcement of allowed surfaces.
- Kill “ONE typed health owner” folklore where behavior still multi-channel.
- Docs: one page pointing primary = doctor; secondaries = projections.

### WP5 — Gate

- `./scripts/check.sh`; arch regen if needed; PR on appropriate lane branch.

---

## Acceptance checklist

- [ ] Prerequisites #1–#3 delivered or every dependent WP marked BLOCKED with symbol evidence
- [ ] One constructor for machine health; secondaries are pure projections
- [ ] Severity enum locked; no new `ok`+`warn` without severity
- [ ] Progress-blocking conditions flip exit non-zero (aligned with #2)
- [ ] UNKNOWN on required signals → unhealthy; never LIVE/green
- [ ] Strip/dep/daemon snapshots TTL-enforced (via #3)
- [ ] `fanops health` / doctor / init / autopilot exit semantics agree
- [ ] CI ratchet fails on new health channel or soft-lie shape
- [ ] `/healthz` remains process-only; documented as such
- [ ] No implementation of roots 1–3 inside this PR

---

## Lane / blast radius

| Path | Note |
|---|---|
| `src/fanops/health_model.py`, `health.py`, `doctor.py` | Primary; not in `lanes.json` hot list today — still coordinate waves |
| `src/fanops/cli.py` | Exit semantics |
| `src/fanops/studio/views.py`, templates strip/daemon/golive | Projectors |
| `src/fanops/studio/views_common.py` | **publish** hot — Postiz banner |
| `src/fanops/studio/app_routes_golive.py` | Health route / refresh |
| `src/fanops/config.py` | **publish** hot — only if label honesty touches `effective_publish_mode` (prefer #3) |
| `src/fanops/pipeline_status.py`, `daemon.py` | Age/status inputs (prefer #2/#3) |
| Tests under `tests/test_health*.py`, `test_doctor.py`, strip/golive | CI-only execution |

Prefer `rfd/` or a dedicated health branch; if touching publish hot files, use `publish/` and stop if collision.

---

## Risks

| Risk | Mitigation |
|---|---|
| Landing without #3 | Encodes TTL-less green — refuse WP3; keep BLOCKED |
| WARN→FAIL floods exit 1 | Use #2 classification; only progress-blocking fails |
| Breaking Studio htmx partials | Projector adapters; golden HTML tests in CI |
| Dual-write observe path retained | Explicit ban; refresh only on data-plane tick / explicit operator Refresh |
| Scope creep into plane untangle | Point to #5 brief; do not move snapshot writers here beyond forbidding CP write-on-read |

---

## One-sentence objective

**Make one severity-aware `HealthReport` the only machine-health truth, drive every operator surface and exit code from it, treat UNKNOWN as FAIL, and ratchet CI so multi-channel soft-green cannot return.**
