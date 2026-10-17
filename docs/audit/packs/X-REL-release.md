# Pack: X-REL — release

## Findings

| ID | Status | Owner | Evidence |
|----|--------|-------|----------|
| OPS-SHA | closed | code | `doctor._deploy_code_check` |
| REL-UNDEPLOYED | deferred | operator | PR #1316 head is not fingerprint `f65411d5806cce087a475261bf4c96c98f0bcf97`. Closes when the merge SHA is the fingerprint. |
| REL-PROT | closed | operator | 2026-10-17 `gh api repos/Fleezyflo/fanops/branches/main/protection` exit 0: `strict` true, contexts `["unit (fast, no toolchain)"]`. Same context in `.github/ci-control-registry.yml`. |
| REL-CI-E2E | closed | operator | 2026-10-17 `gh run list --workflow ci-e2e.yml -L 5`: run `38876056441`, event `schedule`, conclusion `success`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97` (`2026-10-16T08:47:37Z`). |
| REL-CI-ASR | deferred | operator | 2026-10-17 nightly run `38868030626`, event `schedule`, conclusion `failure`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97` (`2026-10-16T07:33:59Z`). Job `[asr] toolchain smoke (nightly)` failed. `dependency audit (pip-audit)` succeeded. |
| REL-CI-ARCH | deferred | operator | Cron `17 5 * * 1` fired. Schedule run `38253871938` (2026-10-12) failed DC-3 with `gh` HTTP 403. Not a cap rule. |

## Rollback

`docs/GOLIVE.md` section “Rollback / safety” is the rollback path. That file was not edited.

## Audit

On HEAD `d2cded31`, `doctor._deploy_code_check` returned a pass unless the daemon was loaded and both `_last_heartbeat_code` and `_version_signal()[0]` existed and differed. A loaded pump with either SHA missing was a pass. `daemon.ensure` kickstarted only when both SHAs were present and different.

A loaded daemon now fails when either SHA is missing or the two differ. The hint names the missing side (`running SHA`, `disk SHA`) or the two 12-character SHAs when they differ. An unloaded daemon still returns None. Both SHAs present and equal still pass. `daemon.ensure` kickstarts on that same miss-or-mismatch when the pump is loaded.

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

`pid` is 1225. `phase` and `ready` are absent from this body. Studio sha equals `origin/main` `f65411d5`. REL-UNDEPLOYED stays deferred until this branch is that sha.

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
completed	success	Nightly ASR smoke: keep TTS wav out of the inbox, pin av<19	architecture	ci/e2e-spoken-sample	pull_request	38388756295	13s	2026-10-13T11:13:41Z
completed	success	Merge pull request #1314 from Fleezyflo/ci/keeper-fast-forward	architecture	main	push	38388123845	10s	2026-10-13T11:08:08Z
```

Newest JSON: event `pull_request`, conclusion `success`, headSha `5c85eb85c96953feb409cdb061d5a07ae6d6acce`. The `main` push in that window is `38388123845`, conclusion `success`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`. `architecture.yml` schedule is `17 5 * * 1`. None of these five events is `schedule`.
