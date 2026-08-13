# Agent execution contract — CP/DP untangle (adversarial bidding)

**Work brief (source of truth for *what*):** [control-data-plane-untangle.md](control-data-plane-untangle.md)  
**This file (source of truth for *how*):** force lean, correct wiring via **sub-agent bidding**. No lone hero implementer.

**Operator gate:** wait for `APPROVE` before any `src/` / `tests/` write. Inventory/docs-only prep is allowed.

---

## 0. Three lenses (locked for the executor)

| Lens | Answer |
|------|--------|
| Best practice | Orchestrator delegates; every WP is bid-tested by independent skeptics before code; verifier ≠ implementer |
| Root cause | Unattended agents invent frameworks, dual paths, and “cleanup” diffs when the brief already named the lean fix |
| Leanest | Prerequisites → one WP at a time → bid → minimal patch → independent verify → next WP. Prefer delete/delegate over new modules |

---

## 1. Role of the parent agent (you)

You are the **execution orchestrator for this brief only**. You:

1. Read [control-data-plane-untangle.md](control-data-plane-untangle.md) end-to-end (and [config-truth-plane.md](config-truth-plane.md) + health-contract when it exists).
2. Enforce **STOP IFs** (§3) before any implement spawn.
3. Spawn **sub-agents** for every unit (§4). You do **not** hand-write the product diff yourself unless a bidder round already passed and the implementer failed twice on the same micro-fix.
4. Never merge to `main`, never force-push, never run local `pytest` (CI only). Prefer `./scripts/check.sh` only.
5. Obey `AGENTS.md` (worktree + venv, lane hot files, cite symbols).

You are **not** authorized to expand scope into escalation policy, derived-signal, honesty ratchets, scrape taxonomy, or auth-decurate — those are other briefs.

---

## 2. Bidding protocol (mandatory before every code WP)

For **each** work package (WP0–WP6 in the work brief), run this loop. No shortcuts.

### Step A — Implementer proposes (no edits yet)

Spawn sub-agent **`propose`** with:

- Objective of this WP only (quote acceptance lines from the work brief)
- Allowed paths (α-ordered)
- Forbidden paths / FORBIDDEN patterns (§5)
- Instruction: return a **CHANGE BID** ≤ 40 lines:

```
BID_ID: CPDP-WP<n>-<slug>
INTENT: <one sentence>
TOUCH: <α paths>
DIFF_SHAPE: <bullets: delete X / redirect Y→Z / add test T — no speculative files>
NON_GOALS: <what this WP explicitly will not do>
PROOF: <rg/commands that will be green after>
LINES_BUDGET: <soft max LOC touched; default ≤80 net for code WPs unless ratchet needs more>
```

If the proposal invents a new framework, second `ensure_up`, new config plane, or “while we’re here” cleanup → parent **rejects** without spawning bidders; force a thinner bid.

### Step B — Parallel bidder sub-agents (read-only)

Spawn **three** skeptics in parallel on the same CHANGE BID. Each returns only `PASS` or `REJECT` + evidence. No alternate mega-designs unless rejecting.

| Bidder | Mandate — REJECT if the bid… |
|--------|------------------------------|
| **bloat** | Adds modules/helpers/abstractions not required by the WP acceptance; duplicates an existing symbol; “flexibility” / strangler / dual-path “for safety”; new docs beyond the folklore purge WP; comments that restate code |
| **scope** | Touches files outside TOUCH; edits `lanes.json` / hooks / orchestration enforcement; lands config-truth or health-contract work inside this WP; “fixes” unrelated doctor/publish bugs; expands CPDP IDs mid-flight |
| **wiring** | Chooses the less-ideal seam (second writer, raw `os.getenv` when `cfg` exists, observe path that mutates, publish owning Docker policy, snapshot write-on-read without naming it, fail-open that hides ownership); paper-fixes with comments instead of moving the write; leaves dual `ensure_up` policy bodies |

**Hard rule:** any one `REJECT` from **wiring** or **scope** blocks. **bloat** REJECT blocks unless parent records a one-line override with the work-brief acceptance quote that forces the LOC (rare; default = no override).

### Step C — Revise or proceed

- Any blocking REJECT → spawn `propose` again with bidder objections inlined (max **2** revisions per WP). Third failure → STOP and report to operator.
- All PASS → spawn **`implement`** with the frozen BID text (no improvisation). Implementer may only shrink the bid, never grow it.

### Step D — Independent verify

Spawn **`verify`** (must differ from implementer id). Verifier:

1. Diff vs frozen BID + work-brief acceptance for that WP only
2. Re-runs the BID’s PROOF greps
3. Fails on any file not in TOUCH, any new dual writer, any doctor mutation, any second ensure_up body
4. Does **not** “improve” the code — FAIL or PASS only

PASS → parent marks WP done, opens/updates PR as needed, advances. FAIL → `fix` sub-agent bound to verifier gaps only, then re-verify.

---

## 3. STOP IFs (do not spawn implement)

| # | Condition | Action |
|---|-----------|--------|
| S1 | [config-truth-plane.md](config-truth-plane.md) not landed (or operator has not waived in writing) | STOP — execute that brief first |
| S2 | Health-contract brief missing or not landed | STOP — author/land health-contract before WP2–WP4 of untangle |
| S3 | Open PR already owns a hot file this WP needs | STOP and report lane collision; do not edit `lanes.json` |
| S4 | Bid failed 2 revisions | STOP — operator decision |
| S5 | Implementer wants to “also” do the next WP in the same diff | STOP — one WP per PR preferred; never batch WP2+WP3+WP4 |
| S6 | Local pytest requested or started | STOP — CI only |

**Allowed without S1/S2:** WP0 inventory freeze (read-only) and drafting the missing health-contract brief as a *separate* doc PR if operator APPROVEs that slice only.

---

## 4. Sub-agent roster (names for Task briefs)

Use Task tool; pin `model: inherit` unless operator overrides. Prefer `explore` / `generalPurpose` for propose+bidders; implement/fix/verify as `generalPurpose` or repo `fanops-worker` when an orchestration wave is engaged.

| Role | Spawn when | Writes? |
|------|------------|---------|
| `propose` | Start of each WP | No |
| `bloat` | After every bid | No |
| `scope` | After every bid | No |
| `wiring` | After every bid | No |
| `implement` | Bid PASS | Yes (unit paths only) |
| `fix` | Verify FAIL | Yes (gap-only) |
| `verify` | After implement/fix | Record only (if wave); else report |

**Parallelism:** bloat + scope + wiring always parallel. Never parallel two `implement`s that share a path.

---

## 5. FORBIDDEN (instant REJECT — bidders and verifier)

- New config registry, Settings/Config merge, or `config.py` edits unless the frozen BID quotes a work-brief line that requires it (default: **zero** `config.py` in this program)
- Second Postiz/Docker bring-up implementation; wrappers that re-encode policy instead of delegating
- Doctor / `/golive/health` / health observe path that writes cooldown, accounts, ledger, or `.env`
- Strangler / dual-path “temporary” ownership
- Mass refactors, renames-for-clarity, reformatting, drive-by comment polish
- New framework modules (`planes.py`, `ownership.py`, DI containers, event buses)
- Editing `.agents/lanes.json`, hooks, `.githooks/`, `scripts/orchestrate.py`
- Inventing Linear taxonomy / scrape auth changes / MOL-909 work
- “Make runtime strict” or “make doctor lenient” (dual policy is locked)
- Touching `FANOPS_LIVE` writers other than preserving `go_live` as sole `=1` setter
- Local `pytest`; `git reset --hard`; push to `main`

---

## 6. Ideal wiring bar (what `wiring` bidder enforces)

Quote these when rejecting:

1. **Observe never mutates** — doctor and snapshot *readers* do not persist control state; if a strip must refresh, the BID must name a single writer role (not publish, not doctor probe).
2. **One infra ensure** — one policy body; all call sites thin delegates.
3. **Config door** — product knobs via `cfg` / shared parsers after config-truth; no new getenv façades.
4. **CP→DP bridges** — kick / run / publish-now / cutover stay explicit and audited; not hidden inside health.
5. **Lean persistence** — prefer deleting a write over adding a sync/cache layer.

---

## 7. WP spawn order (from work brief)

| Order | WP | Bidder focus |
|-------|-----|--------------|
| 0 | Inventory freeze (read-only) | scope (no edits) |
| 1 | Classify bridges (doc/table only unless BID says otherwise) | bloat (no new taxonomy engines) |
| 2 | Observe-only doctor | wiring (must remove `_persist_cooldown` side effect) |
| 3 | Single infra bring-up | wiring + bloat (delegate, don’t redesign Docker) |
| 4 | Affect-graph ratchet tests | bloat (minimal tests; no new harness framework) |
| 5 | Docs / folklore purge | scope (only listed docs) |
| 6 | Gate (`check.sh`, arch regen if needed) | scope |

Do not start WP2 until S1+S2 clear.

---

## 8. Definition of done (program)

- [ ] Every WP has frozen BID + three bidder PASS artifacts (paste or path under `.orchestration/briefs/` / PR body)
- [ ] Verifier ≠ implementer for each code WP
- [ ] Work-brief acceptance checkboxes for landed WPs are ticked with evidence
- [ ] No FORBIDDEN pattern in final diff (`rg` proof in PR)
- [ ] `./scripts/check.sh` green; CI green on PR
- [ ] PR summary lists CPDP IDs closed — nothing else

---

## 9. Copy-paste spawn templates

### Propose

```
ROLE: propose (no edits)
WP: <n> from control-data-plane-untangle.md
Read: .orchestration/briefs/control-data-plane-untangle.md §WP<n>
       .orchestration/briefs/control-data-plane-execute.md §5–6
Return CHANGE BID only (schema in execute brief). Prefer delete/delegate. LOC budget ≤80 net unless ratchet.
```

### Bidder (bloat|scope|wiring)

```
ROLE: <bloat|scope|wiring> bidder — READ ONLY
Judge this CHANGE BID against control-data-plane-execute.md §2 table for your role + §5 FORBIDDEN + §6 wiring bar.
Return exactly: PASS or REJECT, then ≤10 evidence bullets (symbols/paths). No counter-design essay.
BID:
<paste>
```

### Implement

```
ROLE: implement — ONLY the frozen BID below. Shrink OK; grow FORBIDDEN.
Worktree + venv per AGENTS.md. No local pytest. ./scripts/check.sh before push.
Frozen BID:
<paste>
```

### Verify

```
ROLE: verify — you did not implement. Diff vs frozen BID + WP acceptance.
FAIL on TOUCH violations, dual ensure_up, doctor mutations, bloat files.
Return PASS/FAIL + evidence. No code edits.
```

---

## 10. One-sentence objective

**Execute the CP/DP untangle brief by making every change win a three-bidder fight against bloat, scope creep, and lazy wiring—so the land is the lean ownership contract, not an agent’s improvisation.**
