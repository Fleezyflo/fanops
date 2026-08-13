# EXEC — Config truth plane (orchestrator + subagents)

**You are the orchestrator.** You do not implement. You spawn subagents, verify their diffs against this brief + the product brief, refuse bloat, and land only when acceptance is met.

**Product brief (source of truth for WHAT):** [`.orchestration/briefs/config-truth-plane.md`](config-truth-plane.md)  
**This brief (source of truth for HOW):** execute that brief with subagents under the constraints below.

**Repo law:** [`AGENTS.md`](../../AGENTS.md) — never push `main`; publish lane for `config.py`; no local `pytest`; `./scripts/check.sh` before commit; cite symbols not rotting lines.

---

## Mission (one sentence)

Unify env **registration + parsers + Config façade** while keeping **dual policy** (runtime lenient / doctor strict). Nothing else.

---

## Hard bans (instant reject / re-spawn)

If a subagent does any of the following, **stop them**, discard or revert the slice, re-brief with the ban called out:

| Ban | Why |
|---|---|
| Collapse doctor-strict into runtime-lenient (or the reverse) | Wrong root; breaks autonomy / loud boundary |
| `Config` imports `Settings` / pydantic | Import tax + cycles; ~92 importers |
| Cache a `Settings` instance on `Config` | Stales Studio `_dual_write` |
| New framework, new config package, codegen, “envparse microservice”, YAML schema dump | Bloat |
| Drive-by refactors unrelated to WP acceptance | Scope creep |
| Symptom fixes from later roots (escalation, health contract, strip TTL, swallow ratchet allowlist, pending-forever responder) | Wrong brief / wrong order |
| Mass search-replace / “add a log everywhere” / AST theater | Lazy |
| Second copy of a parser “just for Settings” | Recreates the disease |
| Edit `.agents/lanes.json` to take `config.py` | Stop and report |
| `git add -A`, commit secrets, force-push, push to `main` | Safety |
| Local `pytest` / `check-full.sh` | Machine-killer; CI-only tests |
| “Helpful” comments essays, unused helpers, compatibility shims for deleted lies | Bloat |
| Renaming public `cfg.*` properties without need | Fan-in blast |

**Default stance:** if a change is not required for a checked acceptance line in the product brief, **do not make it**.

---

## Quality bar (every WP)

Before accepting a subagent’s work, you must answer **yes** to all:

1. **Root, not symptom** — does this remove dual ownership / façade bypass / vestigial lie, or only paper over it?
2. **Direct wiring** — can you draw a one-line path `env key → registry → parser → Config property → sole caller`? If callers still `os.getenv` the same key, fail.
3. **Minimal diff** — smallest edit that meets WP acceptance; no speculative files.
4. **Dual policy preserved** — existing fail-open runtime + fail-loud doctor tests still intended green.
5. **Readable** — a stranger can find the one owner of a key without spelunking three modules.

If any answer is no → reject and re-spawn with a focused delta brief.

---

## Subagent model

| Role | Spawns | Does |
|---|---|---|
| **You (orchestrator)** | all below | brief, sequence, reject bloat, merge order, PR |
| **inventory** | WP0 only | read-only census table; no code edits |
| **implementer** | one WP at a time | code + tests written, not run locally; `check.sh` |
| **adversary / review** | after each implementer | read-only: hunt bans, dual getenv, bloat; PASS/FAIL with evidence |
| **land** | after all WPs + adversary PASS | publish branch, commit named files only, PR |

**Rules for spawning:**

- **One WP per implementer.** Do not parallelize WP1–WP3 on the same hot files (`config.py`).
- WP0 (inventory) may run alone first; its table is the implementer’s map.
- Adversary never edits; if FAIL, spawn a **fix** implementer with the FAIL list only.
- Do **not** set a custom model on subagents unless the human says so — inherit parent.
- Prefer `generalPurpose` or `explore` for inventory/adversary; implementer = `generalPurpose` or `shell` for land.
- If ECC write hooks break, authorized: `ECC_GATEGUARD=off` + `ECC_DISABLED_HOOKS=…` (same set used in prior waves) — still no ban evasion.

---

## Sequence (mandatory)

```
WP0 inventory (read-only)
    ↓
WP1 leaf ownership (_VALID_BACKENDS, responder_mode helper, parser single-copy check)
    ↓ adversary
WP2 façade (Config scrape props + auto_adopt; retarget Layer A / daemon / llm getenv)
    ↓ adversary
WP3 registry honesty (infra class + demote vestigials + CONFIG/introspect)
    ↓ adversary
WP4 folklore purge (docstrings / stale comments / arch prose)
    ↓ adversary
WP5 gate (check.sh, arch regen if needed, publish/ branch, PR)
```

Do not skip adversary between implementers. Do not start WP2 until WP1 PASS.

---

## Per-WP spawn prompts (copy-adapt)

### WP0 — inventory

```
Read-only. Product brief: .orchestration/briefs/config-truth-plane.md
Produce a census table: every env key in Settings.model_fields ∪ Config getenv ∪ raw getenv outside Config.
Classify: façade | bypass | settings-only | infra | vestigial | secret-dynamic.
No code changes. No commits. Return the table only.
```

### WP1 — leaf ownership

```
Implement WP1 only from .orchestration/briefs/config-truth-plane.md
One _VALID_BACKENDS; one responder_mode resolution helper used by Config + Settings.
Do not add Config scrape properties yet (WP2). No Settings←Config inversion.
Publish lane if touching config.py. check.sh. No pytest. No commit unless orchestrator says.
Bans: see config-truth-plane-EXEC.md Hard bans.
```

### WP2 — façade

```
Implement WP2 only. Config properties for scrape knobs + auto_adopt; retarget
ig_hashtag_scrape / fanops_hashtags / daemon (and llm FANOPS_LLM_MODEL if still raw getenv).
Sole door = cfg. No new parsers. Dual policy unchanged.
Bans: EXEC hard bans. Minimal diff.
```

### WP3 — registry honesty

```
Implement WP3 only. Register infra keys with correct class; demote FANOPS_RESPONDER /
ANTHROPIC_API_KEY (non-studio / deprecated); align config_introspect + CONFIG.md + doc drift tests.
Do not invent a new config framework.
```

### WP4 — folklore

```
Implement WP4 only. Truth Settings docstring; kill runtime_load / “per Config()” ghosts;
stale != "0" comments. Docs/comments only unless a one-line code comment sits on a lie.
```

### Adversary (after each WP1–4)

```
Read-only review of the current uncommitted/PR diff for config-truth-plane.
FAIL if any EXEC hard ban appears, if any product key still has dual getenv owners,
if diff includes files outside the WP, or if wiring is indirect/lazy.
PASS with evidence (symbols) otherwise. No edits.
```

### Land

```
publish/config-truth-plane (config.py is publish hot). Stage only named program files.
HEREDOC commit. check.sh. Push. gh pr create. Do not force-push main.
Exclude junk (*.crt, unrelated briefs churn, secrets).
```

---

## Done means (orchestrator checklist)

Mirror product brief acceptance — all must be true:

- [ ] No product knob with two parsers or two `_VALID_*` sets
- [ ] No Layer A / daemon product flag uses getenv when `cfg.*` exists
- [ ] Registry class matches real use (studio / .env / bootstrap / deprecated)
- [ ] Poster typo + scrape delay dual-policy tests still intended
- [ ] Responder / anthropic not Studio-settable lies
- [ ] No “Settings per Config” / `runtime_load` folklore
- [ ] Branch `publish/…`; `check.sh` green; PR open
- [ ] Diff contains **no** later-root work (health, escalation, swallow ratchet, etc.)

---

## What “less than ideal wiring” looks like (reject examples)

- Shared parser but Layer A still `os.getenv` + parser (façade unfinished) — **WP2 incomplete**
- Helper duplicated into Settings “for clarity” — **ban**
- Deprecated key still `StudioStr` so introspect lies — **WP3 incomplete**
- New `ConfigLoader` class wrapping Settings wrapping Config — **bloat / wrong architecture**
- “While here” doctor sensor or responder ceiling fix — **wrong brief**

---

## Human one-liner to paste to the orchestrator agent

> Execute `.orchestration/briefs/config-truth-plane-EXEC.md`: spawn subagents per WP, enforce hard bans, adversary after each implementer, land on `publish/config-truth-plane`. Product WHAT is `config-truth-plane.md`. No bloat, no later roots, no lazy getenv leftovers.
