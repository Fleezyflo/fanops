# `.githooks/` — repo-owned git hooks (opt-in)

**Hooks enforce policy. Scripts run tests. CI proves everything.**

`pre-push` does **not** run tests — guards only. `pre-commit` runs **scoped** tests via
`./scripts/check.sh` when you stage `src/` or `tests/` `.py` (not the full suite, not `ruff check .`).
Push-time test gates are deliberately gone: slow, routinely bypassed, and once crashed a 16 GB host by
running the codebase-wide suite under the wrong interpreter.

## Wire the hooks (per repo)

```bash
./scripts/setup-hooks.sh
# equivalent manual form:
#   git config --local core.hooksPath .githooks
#   git config --local merge.ours.driver true   # MOL-833: arm .gitattributes merge=ours
```

⚠️ Re-pointing `core.hooksPath` means git looks **only** in `.githooks/` for this repo, so any
machine-global `pre-commit` secret scanner stops firing. This repo's `.githooks/pre-commit` already
includes secret scanning, so opting in keeps you covered — just don't delete it.

## `pre-commit` — secrets + staged lint + scoped check (seconds, not minutes)

1. **Secret scan** on staged diffs — blocks OpenAI / GitHub / AWS keys, private-key blocks, and
   generic `api_key=/secret=/password=/token=` assignments in *added* lines.
2. **Staged ruff** — lints only the `.py` files you staged, under the project `.venv`. Not the whole
   tree (that's CI). Skips lint if the venv is absent; the secret scan still runs.
3. **Scoped `check.sh`** — when any staged file is under `src/` or `tests/` and ends in `.py`, runs
   `BASE=HEAD ./scripts/check.sh` (scoped ruff + pytest on changed modules vs `HEAD`). Skips when
   only docs / scripts / other `.py` are staged. Requires `.venv`; failure blocks the commit.

Bypass the whole hook only in a real emergency: `ECC_SKIP_PRECOMMIT=1 git commit`.

## `pre-push` — policy guards ONLY (no tests, ever)

Refuses:
- a **direct push to `main`** (open a PR; merge on green CI), and
- a **force-push (non-fast-forward) to `main`**.

That's the whole hook. It runs no ruff and no pytest, so there is **no `FANOPS_SKIP_PREPUSH` /
`ECC_SKIP_PREPUSH` bypass** — nothing here is skippable because nothing here is slow. The only override
is the human-only `FANOPS_ALLOW_MAIN_PUSH=1` for a deliberate main push.

## `post-merge` — regen architecture derived artifacts (MOL-833)

Paired with `.gitattributes` `merge=ours` on `.reports/architecture/derived/**`. Concurrent PRs that
each shift a scanned source line both rewrite those generated files; `merge=ours` keeps the merge
clean, and this hook runs `python -m tools.arch regen` against the **merged** source so the working
tree holds a fresh artifact. It does **not** auto-commit — review and commit the refresh (or CI's
drift gate fails loudly if the commit is skipped).

`post-merge` runs only after a *clean* merge. A real source conflict still fails loudly; this hook
does not fire and cannot regenerate the conflict away. Emergency skip: `FANOPS_SKIP_POST_MERGE_ARCH_REGEN=1`
(or `ECC_SKIP_GIT_HOOKS=1`).

## Where tests actually run

| Gate | What | When |
|------|------|------|
| `./scripts/check.sh` | **scoped** ruff + pytest (`-m "not integration and not slow"`) on changed modules | **pre-commit** when `src/`/`tests/` `.py` staged (`BASE=HEAD`); also run by hand — seconds |
| `./scripts/check-full.sh` | **full** `ruff check .` + pytest (`-m "not integration and not slow"`; `CHECK_FULL_SLOW=1` for CI unit parity) | optionally, before a big PR — minutes; never git-hooked |
| **CI** (`.github/workflows/ci.yml`) | `unit` (`pytest -m "not integration"` — includes slow) + `e2e` (real ffmpeg/whisper integration) | **every PR to `main`** — the sole authoritative gate |

Push freely. If CI is green, the change is proven. `check.sh` just keeps CI from coming back red.
