# `.githooks/` — repo-owned git hooks (opt-in)

**Hooks enforce policy. Scripts run tests. CI proves everything.**

These hooks do **not** run tests. Not the full suite, not a scoped subset, not `ruff check .`. Test
execution lives in explicit scripts you run by hand and in CI — never at push time. This is deliberate:
a push-time test gate is slow, is routinely bypassed (and a bypass culture rots the gate), and once
crashed a 16 GB host by running the codebase-wide suite under the wrong interpreter. So it's gone.

## Wire the hooks (per repo)

```bash
git config --local core.hooksPath .githooks
```

⚠️ Re-pointing `core.hooksPath` means git looks **only** in `.githooks/` for this repo, so any
machine-global `pre-commit` secret scanner stops firing. This repo's `.githooks/pre-commit` already
includes secret scanning, so opting in keeps you covered — just don't delete it.

## `pre-commit` — secrets + staged lint (fast, <10s)

1. **Secret scan** on staged diffs — blocks OpenAI / GitHub / AWS keys, private-key blocks, and
   generic `api_key=/secret=/password=/token=` assignments in *added* lines.
2. **Staged ruff** — lints only the `.py` files you staged, under the project `.venv`. Not the whole
   tree (that's CI). Skips lint if the venv is absent; the secret scan still runs.

No test execution. Bypass the secret scan only in a real emergency: `ECC_SKIP_PRECOMMIT=1 git commit`.

## `pre-push` — policy guards ONLY (no tests, ever)

Refuses:
- a **direct push to `main`** (open a PR; merge on green CI), and
- a **force-push (non-fast-forward) to `main`**.

That's the whole hook. It runs no ruff and no pytest, so there is **no `FANOPS_SKIP_PREPUSH` /
`ECC_SKIP_PREPUSH` bypass** — nothing here is skippable because nothing here is slow. The only override
is the human-only `FANOPS_ALLOW_MAIN_PUSH=1` for a deliberate main push.

## Where tests actually run

| Gate | What | When |
|------|------|------|
| `./scripts/check.sh` | **scoped** ruff + pytest on changed modules (vs `origin/main` merge-base) | you run it **before every commit** — seconds |
| `./scripts/check-full.sh` | **full** `ruff check .` + `pytest -q -m "not integration"` (CI parity) | optionally, before a big PR — minutes; never git-hooked |
| **CI** (`.github/workflows/ci.yml`) | `unit` (ruff + full fast suite) + `e2e` (real ffmpeg/whisper integration) | **every PR to `main`** — the sole authoritative gate |

Push freely. If CI is green, the change is proven. `check.sh` just keeps CI from coming back red.
