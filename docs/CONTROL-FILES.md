<!-- Generated: 2026-06-16 -->
# Control files — what steers FanOps at runtime, and its contract

Every file outside `src/` that affects runtime behaviour, classified. The contract:
a **load-bearing config** is validated at its read boundary and fails *visibly*
(loud warning or typed error) — never silently; an **inert doc** does not change
runtime and, when it duplicates code values, is **mirror-tested** so it cannot drift.

| File | Class | Reader | Validation | Failure mode |
|---|---|---|---|---|
| `00_control/context.md` | load-bearing config (the #1 output lever — injected verbatim into every moment + caption + hookedit prompt) | `control.load_guidance` ([control.py](../src/fanops/control.py)) | present + non-empty + ≤32 KiB bound | **fail-open + LOUD**: missing/empty/oversize/unreadable → warning + `""`/bounded; absence is also a `fanops doctor` readiness failure |
| `00_control/tuning.json` | load-bearing config (optional overrides: brand-risk regexes, lift weights) | `Config.tuning` ([config.py](../src/fanops/config.py)) | object-shaped; `offbrand_*` regexes must compile; `lift_weights` values must be numeric | **fail-open + warn+drop**: invalid entries dropped individually, good ones kept; corrupt file → `{}` (defaults) |
| `00_control/accounts.json` | load-bearing config (active surfaces + personas + integration ids) | `Accounts.load` ([accounts.py](../src/fanops/accounts.py)) | schema + `Accounts.validate` (active channel mapped) | **fail-loud**: malformed → `ControlFileError` (one clean stderr line); doctor surfaces mapping gaps |
| `00_control/ledger.json` | load-bearing state store (the only state store) | `Ledger.load` ([ledger.py](../src/fanops/ledger.py)) | schema version + migration; flock on write | **fail-loud**: malformed → `ControlFileError`; newer schema → refuse (no silent field drop) |
| `00_control/cutover.json` | load-bearing gate (freezes the learning loop until live metrics confirmed) | `validation_gate.learning_validated` | `metrics_confirmed` presence | **fail-safe**: absent/unset → learning stays inert (no actuation) |
| `.claude/skills/fanops-hook-hashtag/SKILL.md` | **inert doc** (source of truth is code: `hashtags.VETTED` + `prompts._hook_spec`) | not read at runtime | mirror-tested by [test_skill_drift.py](../tests/test_skill_drift.py) against the code constants | drift between doc and code → red test (cannot ship) |
| `.claude/workflows/*.js` | build/CI tooling (NOT runtime content) | the workflow runner, on demand | tracked + load-bearing per project CLAUDE.md; never deleted | a broken workflow fails its own run, not the engine |

## The rule going forward
A new file that changes what the engine produces gets one of two contracts before it ships:
1. **load-bearing config** → a single validated reader, bounded, fail-*visible* (warn or typed error), with a boundary test; or
2. **inert doc** → not read at runtime, and if it restates a code value, a mirror/drift test.

No third option (an unvalidated file silently steering output) is allowed — that was the defect this contract closed.
