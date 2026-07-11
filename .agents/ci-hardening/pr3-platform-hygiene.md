# CI Hardening PR-3 — Orchestrator Prompt (Platform Hygiene)

**You are the ORCHESTRATOR.** Land **one PR** covering MOL-187, MOL-188, MOL-189, and MOL-190. Delegate **every** sub-task to a sub-agent via `Task`. This PR is **ci.yml only** — no test logic changes.

**Prerequisite:** PR-2 merged to `main` (rebase onto `origin/main` before starting — ci.yml conflicts expected).

**Linear:** MOL-187, MOL-188, MOL-189, MOL-190  
**Branch:** `ci/pr3-platform-hygiene` (off `origin/main`)  
**Worktree:** `../fanops-ci-pr3`

---

## Guardrails (non-negotiable)

Same as PR-1. Additionally:

- **ci.yml ONLY** — do not touch tests, conftest, or scripts in this PR.
- Do NOT bundle MOL-191 Dependabot (separate ticket).
- If pip cache already exists on main, **consolidate** — do not duplicate cache layers.

---

## Evidence snapshot

| Location | What |
|---|---|
| `.github/workflows/ci.yml` | no `concurrency:`, `permissions:`, `timeout-minutes` |
| `.github/workflows/ci.yml:25-30,54-59` | manual `~/.cache/pip` cache may exist — verify on origin/main |
| `.github/workflows/ci.yml:50-53` | `apt-get update && install` every e2e run |
| `.github/workflows/ci.yml` | floating `actions/checkout@v4`, `setup-python@v5`, `cache@v4` |
| `.github/workflows/ci.yml:68` | static `whisper-tiny-v1` key (optional MOL-192 fix if zero conflict) |

---

## Orchestrator sequence

```
SA-0 Setup worktree + baseline ci.yml audit
  → SA-1 concurrency + permissions + job timeouts (MOL-187)
  → SA-2 pip cache audit/consolidate (MOL-188)
  → SA-3 apt cache for E2E toolchain (MOL-189)
  → SA-4 SHA-pin all GitHub Actions (MOL-190)
  → SA-5 Optional: whisper cache key bust (MOL-192 micro)
  → SA-6 YAML validation + diff review
  → SA-7 Commit + push + PR
```

---

## SA-0 — Worktree + ci.yml baseline audit

**Sub-agent type:** `shell`

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops

OBJECTIVE: Create worktree and audit current ci.yml on origin/main.

COMMANDS:
git fetch origin
git worktree add ../fanops-ci-pr3 -b ci/pr3-platform-hygiene origin/main
cd ../fanops-ci-pr3
git show origin/main:.github/workflows/ci.yml > /tmp/ci-main.yml
echo "=== concurrency ===" && rg -n "concurrency|permissions|timeout-minutes" .github/workflows/ci.yml || echo "NONE"
echo "=== pip cache ===" && rg -n "cache/pip|cache: pip" .github/workflows/ci.yml || echo "NONE"
echo "=== action pins ===" && rg -n "uses: actions/" .github/workflows/ci.yml
echo "=== apt ===" && rg -n "apt-get" .github/workflows/ci.yml

ACCEPTANCE:
- Worktree ready at ../fanops-ci-pr3
- Baseline report: what exists vs missing

Return: full SA-0 audit text for orchestrator.
```

---

## SA-1 — concurrency + permissions + timeouts (MOL-187)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-0

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Add workflow-level concurrency, permissions, and per-job timeouts to .github/workflows/ci.yml.

ADD after `on:` block (before `jobs:`):
```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read
```

ADD to each job:
```yaml
  unit:
    timeout-minutes: 15
  e2e:
    timeout-minutes: 25
```

ACCEPTANCE:
- concurrency + permissions present
- unit timeout-minutes: 15, e2e timeout-minutes: 25
- No other logic changed in this sub-task

FILES: .github/workflows/ci.yml only
```

---

## SA-2 — Pip cache audit + consolidate (MOL-188)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-0

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Ensure exactly ONE pip caching strategy in ci.yml. Do not duplicate.

AUDIT FIRST:
- If manual actions/cache on ~/.cache/pip exists AND key uses hashFiles('pyproject.toml') → KEEP it, document in commit message
- If NO pip cache → add setup-python cache:
  ```yaml
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
      cache: pip
      cache-dependency-path: pyproject.toml
  ```
- NEVER have both setup-python cache AND manual ~/.cache/pip cache

ACCEPTANCE:
- One pip cache mechanism, both jobs covered
- cache-dependency-path or hashFiles busts on pyproject.toml change

FILES: .github/workflows/ci.yml (cache steps only)
Return: which strategy chosen and why.
```

---

## SA-3 — Apt cache for E2E (MOL-189)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-0

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Add apt cache step BEFORE apt-get install in e2e job.

INSERT before "Install real toolchain" step:
```yaml
      - uses: actions/cache@v4
        with:
          path: |
            /var/cache/apt
            /var/lib/apt/lists
          key: apt-ffmpeg-espeak-${{ hashFiles('.github/workflows/ci.yml') }}-${{ runner.os }}
          restore-keys: |
            apt-ffmpeg-espeak-${{ runner.os }}-
```

Keep existing apt-get update && install step unchanged after cache.

ACCEPTANCE:
- Apt cache step present in e2e job only
- ffmpeg/espeak install step still present after cache

FILES: .github/workflows/ci.yml (e2e job only)
```

---

## SA-4 — SHA-pin all GitHub Actions (MOL-190)

**Sub-agent type:** `shell`  
**Blocked by:** SA-1, SA-2, SA-3 (apply pins to final ci.yml state)

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Replace ALL floating actions/*@vN tags with full commit SHAs. Comment the tag after each SHA.

COMMANDS:
# Resolve current release SHAs:
gh api repos/actions/checkout/git/refs/tags/v4 --jq '.object.sha'
gh api repos/actions/setup-python/git/refs/tags/v5 --jq '.object.sha'
gh api repos/actions/cache/git/refs/tags/v4 --jq '.object.sha'

# Then edit .github/workflows/ci.yml — every uses: line becomes:
# - uses: actions/checkout@<sha> # v4

VERIFY:
cd ../fanops-ci-pr3
rg "uses: actions/" .github/workflows/ci.yml
# Must show ZERO @v4 or @v5 floating tags — only SHAs with comment tags

ACCEPTANCE:
- All action uses SHA-pinned
- Comment after each pin shows original tag (e.g. # v4)
- ci.yml still valid YAML

FILES: .github/workflows/ci.yml only
Return: list of SHA pins applied.
```

---

## SA-5 — Optional: whisper cache key bust (MOL-192)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-4  
**Skip if:** orchestrator wants minimal diff — this is optional micro-fix.

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE (OPTIONAL): Replace static whisper cache key with pyproject-busted key.

FIND:
          key: whisper-tiny-v1

REPLACE:
          key: whisper-tiny-${{ hashFiles('pyproject.toml') }}-${{ runner.os }}
          restore-keys: |
            whisper-tiny-${{ runner.os }}-

ACCEPTANCE:
- whisper cache still present
- key includes hashFiles('pyproject.toml')

FILES: .github/workflows/ci.yml (whisper cache block only)
If already fixed on main, return "SKIP — already done".
```

---

## SA-6 — YAML validation + scoped diff review

**Sub-agent type:** `code-reviewer` (readonly) or `fanops-worker`  
**Blocked by:** SA-1 through SA-5

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Validate ci.yml and confirm diff scope before commit.

COMMANDS:
cd ../fanops-ci-pr3
git diff .github/workflows/ci.yml
# YAML syntax check (install if needed):
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('YAML OK')" 2>&1
rg -n "concurrency|permissions|timeout-minutes|apt-ffmpeg|whisper-tiny" .github/workflows/ci.yml
rg "@v[0-9]" .github/workflows/ci.yml && echo "FAIL: floating tags remain" || echo "OK: no floating tags"

REVIEW CHECKLIST:
- [ ] concurrency cancel-in-progress
- [ ] permissions: contents read
- [ ] unit 15min / e2e 25min timeouts
- [ ] apt cache in e2e
- [ ] pip cache (one strategy)
- [ ] all actions SHA-pinned
- [ ] ONLY .github/workflows/ci.yml changed

Return: checklist pass/fail, any issues found.
```

---

## SA-7 — Commit, push, open PR

**Sub-agent type:** `shell`  
**Blocked by:** SA-6 green

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr3

OBJECTIVE: Commit PR-3, push, open PR.

COMMANDS:
cd ../fanops-ci-pr3
git add .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
chore(ci): concurrency, permissions, timeouts, caches, SHA pins (MOL-187)

Cancel in-progress runs. Minimal permissions. Job timeouts.
Apt cache for E2E. Consolidated pip cache. Pin actions to full SHAs.
EOF
)"
git push -u origin ci/pr3-platform-hygiene
gh pr create --title "chore(ci): platform hygiene — concurrency, cache, SHA pins (MOL-187/188/189/190)" --body "$(cat <<'EOF'
## Summary
- concurrency: cancel-in-progress on new pushes
- permissions: contents read only
- Job timeouts: unit 15m, e2e 25m
- Apt cache for E2E ffmpeg/espeak install
- Pip cache consolidated (one strategy)
- All GitHub Actions SHA-pinned

## Linear
- MOL-187, MOL-188, MOL-189, MOL-190

## Test plan
- [ ] ci.yml valid YAML
- [ ] No floating @vN action tags
- [ ] gh pr checks green
- [ ] Rapid push cancels prior run (verify in Actions UI)
EOF
)"

Return: PR URL, commit SHA.
```

---

## Orchestrator completion checklist

- [ ] SA-0 … SA-7 all green
- [ ] Only `.github/workflows/ci.yml` in diff
- [ ] PR open to `main` (rebased on PR-2 merge)
- [ ] `gh pr checks` green
- [ ] Linear MOL-187/188/189/190 updated
- [ ] Verify cancel-in-progress: push empty commit, confirm prior run cancelled

## Rollback

```bash
git worktree remove ../fanops-ci-pr3 --force
```

## Post-merge note for orchestrator

After all 3 PRs merge, post in Linear project:
`CI PR-1/2/3 merged, CI green, worktrees removed.`
