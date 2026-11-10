# Pack: X-REL — release

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `doctor._deploy_code_check`, `daemon.stale_code_reason`, `daemon.ensure`, `daemon._version_signal`, `tools/ci/live.py` `required_contexts`, `.github/ci-control-registry.yml` `required_contexts` (`unit (fast, no toolchain)` only), `docs/GOLIVE.md` “Rollback / safety”, `nightly.yml` `asr-smoke`. REL-CI-ASR is resolved. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| OPS-SHA | info | ops | closed on subject symbols | Loaded pump fails doctor when heartbeat SHA and disk SHA differ or either is missing; `daemon.ensure` kickstarts on the same `stale_code_reason` when the pump was already loaded | `doctor._deploy_code_check` → `daemon.stale_code_reason(daemon._last_heartbeat_code(cfg), daemon._version_signal(cfg)[0])`; `daemon.ensure` stale-code branch uses the same helper. 2026-10-18 live checkout: pump heartbeat `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, disk `7aa5d3239a1c61f54613d03d864375ba4420cadf` (`git-head`) — deploy-code miss is disk≠pump; detection path works. Launchd was not restarted for this audit | closed |
| REL-UNDEPLOYED | info | deploy | closed | Studio serving SHA equals `origin/main` | `GET /_fingerprint` sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, pid 65307, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-17T22:25:22.259438+00:00`. [OPEN_GAPS.md](../OPEN_GAPS.md#rel-undeployed) | closed |
| REL-PROT | info | ci | closed | Branch protection required contexts match registry | 2026-10-18 `gh api repos/Fleezyflo/fanops/branches/main/protection`: `strict` true, contexts `["unit (fast, no toolchain)"]`. Same sole context in `.github/ci-control-registry.yml` and `tools/ci/live.py` `required_contexts` | closed |
| REL-CI-E2E | info | ci | closed | Scheduled real-tooling E2E succeeds on subject SHA | Latest `ci-e2e.yml` schedule run `39112132315` success, headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (`2026-10-18T08:36:59Z`). Merge gate remains `unit (fast, no toolchain)` only | closed |
| REL-CI-ASR | low | ci | resolved | Nightly ASR smoke failure is scheduled CI hygiene, not a merge-gate or product gap | `nightly.yml` schedule run `39107733972` failed on headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (`2026-10-18T07:27:35Z`). Merge gate is `unit (fast, no toolchain)`. [OPEN_GAPS.md](../OPEN_GAPS.md#rel-ci-asr) | resolved |
| REL-CI-ARCH | info | ci | closed | Architecture reconcile can skip non-authoritative DC-3 and still succeed | `workflow_dispatch` run `39074333775` on `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` printed `[SKIP] DC-3` (HTTP 403) and exited 0. Scheduled `architecture.yml` on main remains failure history (`38253871938`); proof is the dispatch run. [OPEN_GAPS.md](../OPEN_GAPS.md#rel-ci-arch) | closed |

## Rollback

`docs/GOLIVE.md` section “Rollback / safety” documents dryrun fallback, preflight exit 2, Go-Live back-to-dryrun, cutover throwaway probe, crash-safe publish, and `needs_reconcile` operator path. Present on subject SHA.

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/GOLIVE.md | rg -n 'Rollback / safety' -A 20
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:.github/ci-control-registry.yml | rg required_contexts -A2
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/doctor.py  # _deploy_code_check
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/daemon.py   # stale_code_reason, ensure, _version_signal
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:tools/ci/live.py      # required_contexts
git rev-parse HEAD origin/main
curl -sS http://127.0.0.1:8787/_fingerprint
python -c 'from fanops.config import Config; from fanops import daemon; c=Config(); print(daemon._version_signal(c), daemon._last_heartbeat_code(c), daemon.stale_code_reason(daemon._last_heartbeat_code(c), daemon._version_signal(c)[0]))'
gh api repos/Fleezyflo/fanops/branches/main/protection
gh run view 39112132315 --repo Fleezyflo/fanops --json headSha,conclusion,event,createdAt
gh run view 39107733972 --repo Fleezyflo/fanops --json headSha,conclusion,event
gh run list --repo Fleezyflo/fanops --workflow architecture.yml --event schedule -L 3
```

## Audit

On HEAD `d2cded31`, `doctor._deploy_code_check` returned a pass unless the daemon was loaded and both `_last_heartbeat_code` and `_version_signal()[0]` existed and differed. A loaded pump with either SHA missing was a pass. `daemon.ensure` kickstarted only when both SHAs were present and different.

A loaded daemon now fails when either SHA is missing or the two differ. The hint names the missing side (`running SHA`, `disk SHA`) or the two 12-character SHAs when they differ. An unloaded daemon still returns None. Both SHAs present and equal still pass. `daemon.ensure` kickstarts on that same miss-or-mismatch when the pump was loaded.

Local `git rev-parse` (no `gh`, no merge): HEAD `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`, `origin/main` `f65411d5806cce087a475261bf4c96c98f0bcf97`.

## 2026-10-17 re-read

```
$ git rev-parse HEAD
d2cded31e29e027e4ed55bfdb99b9a93ac4f024e

$ git status --short --branch
## audit/production-audit-2026-09...origin/audit/production-audit-2026-09
```

Working tree is dirty (audit docs, fanops sources, tests). `git diff --stat origin/main...HEAD` is 29 files, +904 / −26. That range does not include the uncommitted working tree.

```
$ curl -sS -D - http://127.0.0.1:8787/_fingerprint
HTTP/1.1 200 OK
Server: Werkzeug/3.1.8 Python/3.12.8
Date: Fri, 16 Oct 2026 22:45:48 GMT
Content-Type: application/json
Content-Length: 175
Connection: close

{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":1225,"sha":"f65411d5806cce087a475261bf4c96c98f0bcf97","start_time":"2026-10-16T15:57:11.959355+00:00"}
```

That curl was the pre-merge body. The close is fingerprint sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. [OPEN_GAPS.md](../OPEN_GAPS.md#rel-undeployed).

## 2026-10-17 operator commands

`fanops` is not on PATH. Doctor invocation and Postiz/queue lines are in `X-DEP-deps.md`.

```
$ git rev-parse HEAD
d2cded31e29e027e4ed55bfdb99b9a93ac4f024e

$ git rev-parse origin/main
f65411d5806cce087a475261bf4c96c98f0bcf97
```

```
$ gh api repos/Fleezyflo/fanops/branches/main/protection
... "required_status_checks":{"strict":true,"contexts":["unit (fast, no toolchain)"],"checks":[{"context":"unit (fast, no toolchain)","app_id":15368}]} ...
```

Exit 0. The body is the full protection object. The slice above is `required_status_checks`. `.github/ci-control-registry.yml` lists `unit (fast, no toolchain)` and `branch_protection_context: "unit (fast, no toolchain)"`.

```
$ gh run list --repo Fleezyflo/fanops --workflow ci-e2e.yml -L 5
completed	success	CI E2E	CI E2E	main	schedule	38876056441	4m53s	2026-10-16T08:47:37Z
```

JSON for that run: event `schedule`, conclusion `success`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`.

```
$ gh run list --repo Fleezyflo/fanops --workflow nightly.yml -L 5
completed	failure	nightly	nightly	main	schedule	38868030626	2m47s	2026-10-16T07:33:59Z
```

JSON: event `schedule`, conclusion `failure`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`. `gh run view 38868030626`: `[asr] toolchain smoke (nightly)` conclusion `failure`; `dependency audit (pip-audit)` conclusion `success`.

```
$ gh run list --repo Fleezyflo/fanops --workflow architecture.yml -L 5
completed	success	Refresh the production audit on origin/main	architecture	audit/production-audit-2026-09-close	pull_request	39122872226	10s	2026-10-18T11:20:58Z
```

Newest JSON: event `pull_request`, conclusion `success`, headSha `5c85eb85c96953feb409cdb061d5a07ae6d6acce`. The `main` push in that window is `38388123845`, conclusion `success`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`. `architecture.yml` schedule is `17 5 * * 1`. This default list is pull and push traffic. Schedule run `38253871938` (2026-10-12) is the old failure. REL-CI-ARCH is closed on dispatch `39074333775`. [OPEN_GAPS.md](../OPEN_GAPS.md#rel-ci-arch).
