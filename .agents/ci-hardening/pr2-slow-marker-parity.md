# CI Hardening PR-2 — Orchestrator Prompt (Slow Marker + Local Parity)

**You are the ORCHESTRATOR.** Land **one PR** covering MOL-183, MOL-185, and MOL-199. Delegate **every** sub-task to a sub-agent via `Task`. Do not implement yourself except conflict resolution and PR open.

**Prerequisite:** PR-1 merged to `main` (or rebase this branch onto `origin/main` after PR-1 merge).

**Linear:** MOL-183, MOL-185, MOL-199  
**Branch:** `ci/pr2-slow-marker-parity` (off `origin/main`)  
**Worktree:** `../fanops-ci-pr2`

---

## Guardrails (non-negotiable)

Same as PR-1 (see `.agents/ci-hardening/pr1-false-confidence.md`). Additionally:

- Rebase onto fresh `origin/main` if PR-1 landed while you were working.
- Do NOT add a third CI job — slow tests run inside existing `e2e` job.

---

## Evidence snapshot

| Location | What |
|---|---|
| `pyproject.toml:72-75` | `slow` marker already registered |
| `tests/test_account_first_e2e.py:10` | `pytestmark = pytest.mark.slow` |
| `tests/test_hashtag_lifecycle_e2e.py:10` | `pytestmark = pytest.mark.slow` |
| `tests/test_review_lanes_e2e.py:8` | `pytestmark = pytest.mark.slow` |
| `tests/test_per_persona_e2e.py:6` | `pytestmark = pytest.mark.slow` |
| `tests/test_e2e_transcript_assertion.py` | FAST unit guard — do NOT mark slow |
| `.github/workflows/ci.yml:40` | unit still `-m "not integration"` + `--cov` theater |
| `.github/workflows/ci.yml:75-79` | e2e runs integration only, no slow step |
| `scripts/check-full.sh:24-31` | has slow marker logic, missing `FANOPS_REQUIRE_STUDIO=1` |

**Verify on your base:** `slow` marker may be partially landed on some branches. Sub-agents must diff against `origin/main` and only commit the delta.

---

## Orchestrator sequence

```
SA-0 Setup worktree (off origin/main)
  → SA-1 Audit slow marker coverage on all cross-face proofs
  → SA-2 ci.yml unit job: exclude slow + drop coverage (MOL-183/199)
  → SA-3 ci.yml e2e job: add slow pytest step (MOL-183)
  → SA-4 check-full.sh: FANOPS_REQUIRE_STUDIO + honest docs (MOL-185)
  → SA-5 tests/CLAUDE.md + stale comment fixes
  → SA-6 Full verification
  → SA-7 Commit + push + PR
```

---

## SA-0 — Worktree + venv setup

**Sub-agent type:** `shell`

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops

OBJECTIVE: Create isolated worktree for CI PR-2 off fresh origin/main.

COMMANDS:
git fetch origin
git worktree add ../fanops-ci-pr2 -b ci/pr2-slow-marker-parity origin/main
cd ../fanops-ci-pr2
python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'
git config --local core.hooksPath .githooks
git log -1 --oneline
./.venv/bin/python -m pytest --markers 2>/dev/null | rg slow || true

ACCEPTANCE:
- Branch ci/pr2-slow-marker-parity at origin/main HEAD
- slow marker visible in pytest --markers (if already on main)

Return: HEAD sha, slow marker present Y/N.
```

---

## SA-1 — Audit + complete slow marker on cross-face proofs

**Sub-agent type:** `explore` then `fanops-worker`  
**Blocked by:** SA-0

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Ensure every slow cross-face proof has pytestmark = pytest.mark.slow. Do NOT mark fast unit guards.

MUST have @pytest.mark.slow (module-level pytestmark):
- tests/test_account_first_e2e.py
- tests/test_hashtag_lifecycle_e2e.py
- tests/test_review_lanes_e2e.py
- tests/test_per_persona_e2e.py

MUST NOT be slow:
- tests/test_e2e_transcript_assertion.py (fast unit guard for transcript contract)

ALSO:
- Fix stale header comment in test_account_first_e2e.py if it still says unit CI runs slow via -m "not integration"
- If pyproject.toml slow marker missing, add it (unlikely on current main)

COMMANDS:
cd ../fanops-ci-pr2
rg -l "pytestmark.*slow" tests/
rg "not integration" tests/test_account_first_e2e.py tests/test_hashtag_lifecycle_e2e.py

ACCEPTANCE:
- All 4 cross-face files have slow marker
- test_e2e_transcript_assertion.py has NO slow marker
- Paste rg output

FILES: tests/test_*_e2e.py, pyproject.toml (only if marker missing)
```

---

## SA-2 — ci.yml unit job: exclude slow + remove coverage (MOL-183 / MOL-199)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-1

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Update unit job in .github/workflows/ci.yml:
1. pytest marker: -m "not integration and not slow"
2. REMOVE --cov=src/fanops --cov-report=term-missing (coverage theater)
3. Rename step from "Unit tests (with coverage report)" to "Unit tests"

BEFORE (anchor .github/workflows/ci.yml:37-40):
        run: python -m pytest -q -m "not integration" --cov=src/fanops --cov-report=term-missing

AFTER:
        run: python -m pytest -q -m "not integration and not slow"

COMMANDS:
cd ../fanops-ci-pr2
git diff .github/workflows/ci.yml

ACCEPTANCE:
- No --cov in unit job
- Marker excludes slow
- FANOPS_REQUIRE_STUDIO=1 env unchanged

FILES: .github/workflows/ci.yml (unit test step only)
```

---

## SA-3 — ci.yml e2e job: add slow pytest step (MOL-183)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-1

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Add slow cross-face proof step to e2e job AFTER integration suite in .github/workflows/ci.yml.

INSERT after integration step (after line ~79):
```yaml
      - name: Slow cross-face proofs (hermetic, no toolchain)
        env:
          FANOPS_REQUIRE_STUDIO: "1"
        run: python -m pytest -q -m slow
```

NOTES:
- Do NOT set FANOPS_REQUIRE_E2E=1 on slow step (hermetic tests, no real toolchain)
- Slow step needs [studio] extra (already installed in e2e job)

COMMANDS:
cd ../fanops-ci-pr2
# Local smoke (no ffmpeg needed for slow proofs):
FANOPS_REQUIRE_STUDIO=1 ./.venv/bin/python -m pytest -q -m slow --co -q | tail -5

ACCEPTANCE:
- New step present in ci.yml
- Collects slow tests (account_first, hashtag_lifecycle, review_lanes, per_persona)

FILES: .github/workflows/ci.yml (e2e job append only)
```

---

## SA-4 — check-full.sh honest CI parity (MOL-185)

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-2

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Update scripts/check-full.sh to match CI unit job honestly.

REQUIRED CHANGES:
1. Wrap pytest invocation with FANOPS_REQUIRE_STUDIO=1:
   FANOPS_REQUIRE_STUDIO=1 "$PY" -m pytest -q -m "$MARKER"
2. Default MARKER='not integration and not slow' (already may exist — keep)
3. CHECK_FULL_SLOW=1 → MARKER='not integration' (include slow, full unit parity)
4. Update header comments:
   - Mirrors CI unit job (not e2e)
   - Documents FANOPS_REQUIRE_STUDIO=1
   - Points to optional future scripts/check-e2e.sh (do not create unless trivial)

COMMANDS:
cd ../fanops-ci-pr2
# Without studio in venv this should abort — with studio should pass:
FANOPS_REQUIRE_STUDIO=1 ./scripts/check-full.sh 2>&1 | tail -10

ACCEPTANCE:
- FANOPS_REQUIRE_STUDIO=1 exported for pytest
- Marker matches ci.yml unit job post-SA-2

FILES: scripts/check-full.sh only
```

---

## SA-5 — Documentation: tests/CLAUDE.md + local coverage note

**Sub-agent type:** `fanops-worker`  
**Blocked by:** SA-2, SA-3

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Update tests/CLAUDE.md to reflect new CI job split.

DOCUMENT:
- Unit CI: python -m pytest -q -m "not integration and not slow"
- E2E CI: integration (-m integration) + slow (-m slow) in e2e job
- Local coverage (optional, NOT in CI):
  python -m pytest -q -m "not integration" --cov=src/fanops --cov-report=term-missing
- check-full.sh: FANOPS_REQUIRE_STUDIO=1, CHECK_FULL_SLOW=1 for slow proofs

Do NOT edit root CLAUDE.md unless a one-line CI command reference is stale.

ACCEPTANCE:
- tests/CLAUDE.md accurate
- No contradictions with ci.yml

FILES: tests/CLAUDE.md
```

---

## SA-6 — Full verification gate

**Sub-agent type:** `team-qa`  
**Blocked by:** SA-1 through SA-5

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Verify PR-2 before commit.

COMMANDS:
cd ../fanops-ci-pr2
./scripts/check.sh
FANOPS_REQUIRE_STUDIO=1 ./.venv/bin/python -m pytest -q -m "not integration and not slow" --co -q | tail -1
FANOPS_REQUIRE_STUDIO=1 ./.venv/bin/python -m pytest -q -m slow --co -q | tail -1
FANOPS_REQUIRE_STUDIO=1 ./scripts/check-full.sh 2>&1 | tail -5
rg "cov" .github/workflows/ci.yml
git diff --name-only

ACCEPTANCE:
- check.sh green
- Unit marker excludes slow (count < full not-integration count)
- slow tests collect > 0
- No --cov in ci.yml unit step
- Changed files scoped to: ci.yml, check-full.sh, tests/CLAUDE.md, test header comments

Return: test counts, pass/fail, file list.
```

---

## SA-7 — Commit, push, open PR

**Sub-agent type:** `shell`  
**Blocked by:** SA-6 green

### Sub-agent prompt

```
Full Repository Path: /Users/molhamhomsi/Moh Flow Fanops/../fanops-ci-pr2

OBJECTIVE: Commit PR-2, push, open PR.

COMMANDS:
cd ../fanops-ci-pr2
git add .github/workflows/ci.yml scripts/check-full.sh tests/CLAUDE.md
git add tests/test_account_first_e2e.py tests/test_hashtag_lifecycle_e2e.py tests/test_review_lanes_e2e.py tests/test_per_persona_e2e.py pyproject.toml
# only add files actually changed
git commit -m "$(cat <<'EOF'
fix(ci): exclude slow tests from unit job + drop coverage theater (MOL-183)

Unit CI: -m "not integration and not slow", no --cov. E2E job runs -m slow.
check-full.sh sets FANOPS_REQUIRE_STUDIO=1 for honest local parity (MOL-185).
EOF
)"
git push -u origin ci/pr2-slow-marker-parity
gh pr create --title "fix(ci): slow marker in CI + check-full parity (MOL-183/185/199)" --body "$(cat <<'EOF'
## Summary
- Unit job excludes @pytest.mark.slow cross-face proofs
- E2E job runs slow hermetic proofs
- Removed coverage theater from unit CI
- check-full.sh: FANOPS_REQUIRE_STUDIO=1 + honest marker docs

## Linear
- MOL-183, MOL-185, MOL-199

## Test plan
- [ ] Unit marker count < previous not-integration count
- [ ] FANOPS_REQUIRE_STUDIO=1 pytest -q -m slow green
- [ ] ./scripts/check.sh + check-full.sh green
- [ ] gh pr checks green
EOF
)"

Return: PR URL, commit SHA.
```

---

## Orchestrator completion checklist

- [ ] SA-0 … SA-7 all green
- [ ] PR open to `main` (after PR-1 merged or rebased)
- [ ] `gh pr checks` green before merge
- [ ] Linear MOL-183/185/199 updated

## Rollback

```bash
git worktree remove ../fanops-ci-pr2 --force
```
