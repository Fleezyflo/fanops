---
name: fanops-production-audit
description: >-
  Runs the FanOps full-module production audit. Task orchestration of all 18
  index modules (U0, C1–C10, H-CI, H-DOCS, H-INT, X-SEC, X-DEP, X-REL, X-DATA)
  on Composer 2.5 agents writes the FanOps docs/audit/* deliverables (packs,
  weakness register, master map, production score, open gaps). Use when the
  user invokes /fanops-production-audit.
disable-model-invocation: true
---

# /fanops-production-audit

Input: optional subject ref (default `origin/main`); optional `Phase: Audit` to stay read-only on `src/`.
One job. No approval pause. You are the parent. Do not spawn `fanops-orchestrator` or `fanops-worker`. Do not edit `.cursor/hooks/orchestration_gate.py` or `_WAVE_AGENTS`. If a Task is refused because an orchestration wave is active (`FANOPS_ORCHESTRATED` or `.orchestration/state/ACTIVE`), stop and report that. Do not switch spawn type to get past the gate.

Module catalog is `docs/audit/AUDIT_MODULE_INDEX.md` (Workflow, Dispatch order, Subagent brief template, executive table, per-module Paths / Entry symbols / Pack / Lane / Audit). Fill briefs from that file on this run. Do not reuse a prior chat’s SHA, fingerprint, or finding text.

You measure the subject and you Finalize. You do not audit a module, re-read its symbols, or edit its pack. Each module is one Cursor `Task`: `subagent_type: fanops-audit-module`, `model: composer-2.5` (pass that slug; omitting it or passing `inherit` runs the parent model). One fresh Task per module ID. The child has no session history, so the brief is the whole assignment. The child does not spawn further agents and does not load this skill. If `fanops-audit-module` or `composer-2.5` is refused, stop and report the refusal. Do not audit that module yourself and do not substitute `generalPurpose`.

## 1. Subject

Run these before any Task:

```text
git rev-parse <subject>
git status --short --branch
git diff --stat origin/main...HEAD
curl -fsS --max-time 3 http://127.0.0.1:8787/_fingerprint
```

`<subject>` is `origin/main` unless the user names another ref. A failed curl means Studio is not up; record that and continue. Do not restart launchd, do not `workflow_dispatch`, do not drain or wipe the ledger.

When live ops evidence is in scope (the full 18-module run, or any brief that includes C8, X-DEP, or X-REL), also run `python -m fanops.cli doctor` (same check as `fanops doctor`). Attach the transcript. It is read-only: no `--fix`, no publish, no queue drain. `doctor._deploy_code_check` prints full SHAs only on a miss, via `daemon.stale_code_reason`. For the plane table, read the full values with the signatures those symbols already have (`Config()` as `cli.main` constructs it):

```text
python -c "from fanops.config import Config; from fanops import daemon; c=Config(); print('heartbeat', daemon._last_heartbeat_code(c)); print('disk', daemon._version_signal(c))"
```

Live-checkout disk is `git rev-parse HEAD` in the worktree path the index header already names. Do not restart launchd to make the planes match.

Write the measured SHAs and today’s assessment date into the header plane table of `docs/audit/AUDIT_MODULE_INDEX.md`. Planes that exist there stay: audit subject, Studio fingerprint, pump, live-checkout disk, doctor disk. A fingerprint that differs from the subject is a fact. It is not a restart.

Baseline register and score, for the score ledger only:

```text
git show <subject>:docs/audit/WEAKNESS_REGISTER.md
git show <subject>:docs/audit/PRODUCTION_SCORE.md
```

Working-tree copies of those two files are not symbol truth until Finalize.

## 2. Orchestration

Dispatch exactly one Task per module ID in the index dispatch table. Wait for a wave’s in-flight Tasks before the next wave. Read each return before you treat that module as done.

| Wave | Modules | How |
|------|---------|-----|
| 0 | **U0**, then **H-INT** | Serial. H-INT reads the partition U0 confirms. Do not retag `partition.json` with X-keys. |
| 1 | **C1**, **C2**, **C3**, **C4**, **C5**, **C7**, **C8**, **C9**, **C10**, **H-DOCS**, **X-SEC**, **X-REL**, **X-DATA** | Parallel, except shared hot files. **C1** finishes before **X-DATA** starts (`ledger.py`, picking). **C4** and **C5** never run at the same time (`prompts.py`, picking and rfd). **C10** may edit `views_common.py` only on a publish branch. |
| 2 | **C6**, then **X-DEP** | Serial. X-DEP interprets drainable / `publish_due` / `needs_reconcile` / Postiz / Zernio only after C6’s verdict. `reconcile.py`, `post/zernio.py`, `post/postiz.py`, `post/run.py` are publish. `crosspost.py` is picking. A change that needs both is two branches, not one Task editing both. |
| 3 | **H-CI**, then parent Finalize | H-CI is the last Task. Finalize is you, not a Task. |

Phase on every brief is `Audit then Remediate` in that same unit. If the user passed `Phase: Audit`, every brief is `Phase: Audit` and no unit edits `src/`.

Resolve skills by path and put the path in the brief. Child lens is `production-audit`: the `SKILL.md` under `~/.cursor/plugins/cache/ecc/**/skills/production-audit/SKILL.md` whose frontmatter `name` is `production-audit` (the ECC copy prior chats used). Remediate line is `fix-defect`: `~/.grok/skills/fix-defect/SKILL.md`, or another `SKILL.md` whose `name` is `fix-defect` if that path is gone. Do not point a child at this file.

Copy the fenced template in the index. Fill every field from that module’s section. Add the operational lines below. Do not drop a template field.

```text
Skill: production-audit
Read and follow: <absolute path to production-audit/SKILL.md>
Phase: Audit then Remediate
Module: <ID> — <title from the index>
Subject: <ref> <full sha from git rev-parse>
Repo: <absolute repo path>
Paths: <index Paths>
Entry symbols: <index Entry symbols>
Pack: <docs/audit/packs/….md>
Register IDs: <index Register bullets; status on the subject, not the working-tree register>
Lane: <index Lane, checked against .agents/lanes.json hot_files>
Audit slice: the module’s Audit bullet. On the subject SHA, re-read routes, auth, data writes, external calls, jobs, env, health, rollback, and the user path that bullet names. production-audit scoring bands apply at Finalize, not as a second essay from this unit.
Remediate: Skill: fix-defect. Read and follow: <absolute path to fix-defect/SKILL.md>. One finding, one owner path. A hot file in .agents/lanes.json owned by another lane is reported and not edited. Do not retag lanes.json.
Dead symbol: if this module’s symbols prove an absent or unreachable name (the class of vet_hashtags and _read_queries), run fix-defect in this same unit before you return. Before any delete, grep src/fanops/ for `<name> as`, `import <name>`, and lazy imports, per src/fanops/CLAUDE.md “Zero callers”. A name-based call graph is not enough.
Symbol truth: git show <subject>:<path>. Pack prose and the working-tree register are not truth.
Pack edit: update the pack findings table. Keep its header. Required columns are ID, Severity, Class, Observed, Required, Evidence. Keep a Status column when the pack already has one. The first cell must stay the finding ID so tests/test_production_audit_gates.py `_FINDING_ID` still matches. New ID only when symbols on the subject prove a gap. Re-verify header names this subject SHA and the assessment date. “no pack change” is allowed only when that header already names this subject and the rows still match the symbols.
Do not edit WEAKNESS_REGISTER.md, MASTER_MAP.md, PRODUCTION_SCORE.md, OPEN_GAPS.md, AUDIT_MODULE_INDEX.md, or tests/test_production_audit_gates.py.
Return: findings-table diff or “no pack change”; evidence commands; symbol citations (path and symbol, not a stale line). Remediate also returns the owner path (branch, files, whether ./scripts/check.sh ran, whether python -m tools.arch regen ran). Do not commit unless the user asked. Do not run pytest. Do not spawn agents. Do not load fanops-production-audit.
```

`Phase: Audit` drops the Remediate and Dead-symbol lines and forbids `src/` edits. Docs pack updates stay.

If a return misses the contract, resume that same Task. Do not open a second module ID, and do not batch the miss into Finalize.

## 3. Real audit bar per module

This bar is for the Composer 2.5 module agent. Copy it into that Task’s brief. The parent does not run it.

The child re-reads symbols on the subject SHA. A closed row in an old pack is not evidence. Status written into the pack must match what those symbols do on that SHA.

Checklist, limited to the surfaces the module’s Audit bullet owns:

- routes and auth, including server-side checks on Studio mutations
- data writes, migrations, idempotent retries, and what a failed write leaves behind
- external calls and their failure posture
- jobs, daemon, and schedule behavior the module actually owns
- env names and fail-fast startup
- health and rollback
- the user path (empty, error, and the documented operator command)

FanOps has no payment webhook. Do not invent one. Vendor wire for publish is `posted_text_for` versus `Post.caption`, and Zernio `content` / `mediaItems`, in `src/fanops/post/CLAUDE.md`.

Add a finding ID only when the symbol on the subject is a gap. Doc-rot stays on that module’s docs path. Behaviour that is wrong is `fix-defect` in this unit: one owner path, regression test written beside existing tests, no local pytest. Dead or unreachable symbols in the owning module are fixed in that unit, before the module returns, after the `src/fanops/` zero-callers grep.

Lane stops from `.agents/lanes.json` `guard.hot_files` go in the brief. Report a file another lane owns. Do not take it and do not edit `lanes.json`.

## 4. Parent Finalize

Runs after H-CI’s Task returns. You write these. Children do not.

1. Merge every pack finding ID into `docs/audit/WEAKNESS_REGISTER.md`. Status is the symbol truth on the subject (closed with a symbol, resolved with an `OPEN_GAPS.md` citation, or open with an owner path). Sync the index executive table and every module **Register.** bullet to that register.
2. Refresh `docs/audit/MASTER_MAP.md` component verdicts for the subject SHA. The fingerprint column is the live curl, separate from doctor’s disk SHA.
3. Re-score `docs/audit/PRODUCTION_SCORE.md` from the register. Bands and caps are the `production-audit` skill (Blocked 0–49, Risky 50–69, Launchable With Caveats 70–84, Strong 85–100; cap 69; cap 84). How those caps bind on this repo is `docs/audit/GAP_CLOSURE_PLAN.md` “Why capped at 84”. Do not invent point weights. The numeric ledger is the only arithmetic. Score prose matches closed and open register rows. A cap is a ceiling, not a subtraction you invent.
4. Update `docs/audit/OPEN_GAPS.md` for live and infra rows that are not product-symbol defects. Quote the doctor, `gh`, or fingerprint transcript. Do not leave a deferral with no command output.
5. Extend `tests/test_production_audit_gates.py` last, and only when a new pack file appears or a finding ID would not match `_FINDING_ID`. The existing `test_register_covers_pack_findings` already requires every pack ID in the register. Do not add a duplicate per-ID assertion. `REQUIRED_PACKS` must stay equal to the names in `docs/audit/packs/`.

`./scripts/check.sh` before any commit. `python -m tools.arch regen` when a scanned source line moved. Tests ship with the change; CI runs them. Do not run pytest. Do not commit unless the user asked. Do not push, and do not push to `main`.

Done when all 18 modules have pack evidence on the subject SHA, every register ID is in a pack (register ⊆ packs), every pack finding ID is in the register, `REQUIRED_PACKS` equals `docs/audit/packs/*.md`, and the score prose matches the closed and open register rows.

Final message is that done check: subject SHA, fingerprint SHA, score sentence, open register IDs (or none), and the artifact paths. No second essay.

## 5. Guardrails

From `AGENTS.md` and `.cursor/rules/fanops-contract.mdc`:

- Never wipe or reset the ledger. Ledger state is production data.
- Never commit `00_control/accounts.json`.
- Never push to `main`. Never force-push.
- A hot file another lane owns is reported, not taken.
- `Phase: Audit` is read-only on `src/`. Remediate edits `src/` only through `fix-defect` in that module’s Task.
- Production picks up merged code. Do not restart, kickstart, or hand-run the pipeline.
- Local `main` moves by `git pull --ff-only` only. Do not `git reset --hard`.
- Cite symbols, not line numbers copied into prose.
