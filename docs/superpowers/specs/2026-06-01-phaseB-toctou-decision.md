# Phase B — A3 answer-stale TOCTOU: lock-boundary decision record

**Date:** 2026-06-01
**Scope:** The ONE open design decision framed for Phase B. Everything else in Phase B
(the single ledger transaction lock, B1/B2/B3, M1, M2) is locked by
`docs/superpowers/plans/2026-06-01-fanops-live-autonomous.md` and is NOT reopened here.

## The race (grounded in code, not assumption)

`LlmResponder.answer_pending` (src/fanops/responder.py) per gate:

1. `pending(cfg, kind)` → key `s1`
2. `payload = json.loads(request_path(...).read_text())` — payload **P1**, embedded `request_id` **R1**
3. `out = self._model(kind, payload)` — the **slow** `claude -p` call (timeout 180s). `out` is derived from **P1**.
4. **DURING step 3**, an overlapping `fanops run` calls `write_request(...)` (from `moments.py` /
   `adjust.py` / `caption.py`, inside `advance()`), minting a **new** `request_id` **R2** + new payload **P2**,
   and unlinking any prior response (there is none yet).
5. Step 3 returns. Old code: `rid = latest_request_id(...)` → reads **R2** (the new one).
6. Stamps `out` (built from **P1**) with **R2**, writes the response.
7. Later, `read_response` checks `data["request_id"] == latest_request_id()` → **R2 == R2** → **passes**.
   The **P1-derived answer is applied to the R2 request** — a wrong-payload answer accepted as fresh.

The agent-gate files live in `cfg.agent_io/requests`, OUTSIDE the ledger flock (which guards only ledger
writes), so this is reachable by the project's own documented overlapping-cron model. The plan and the
deviation memo (`fanops-build-deviations.md`, "Recorded-for-later → Phase B") both assign this to Phase B.

## Approaches considered

**A. Capture-and-recheck rid (CHOSEN).** Capture `rid_before` BEFORE the model call; after the call,
re-read `rid_after` and SKIP the write if `rid_before != rid_after` (or `rid_after is None`). Stamp the
response with the captured `rid_before`, never a fresh post-call read.

**B. agent_io under a new lock.** Wrap read→model→write under a new `agent_io` flock.
REJECTED: the slow `claude -p` call (up to 180s) would run while holding the lock, so an overlapping
`fanops run`'s `write_request` — which already holds the **ledger** lock inside `advance()` — would block
up to 180s on the agent_io lock or raise `LockBusyError`, serializing the autonomous brain and
re-introducing the exact overlapping-cron stall the ledger lock is engineered to *bound*. Also invites a
lock-ordering hazard (ledger lock held while waiting on agent_io lock).

**C. Justified defer.** REJECTED: the plan + memo explicitly say "Phase B owns" this, and the fix is
cheap and demonstrably closable now — no honest argument to defer.

## Why A closes the window (not merely narrows it)

The dangerous case is "the rid stamped at write-time matches `latest` at read-time but the payload it was
built from is stale." `write_request` mints a **new** rid on every (re)write. So if ANY re-seed happens
during the model call, `rid_after != rid_before`, the write is skipped, and the gate stays pending for the
new request — the stale answer is dropped. Detection is on the rid *value*, not a timestamp, so there is no
"narrow but still open" residue from clock granularity.

**The residual micro-window is provably safe.** Between the `rid_after` check and `write_text`, a re-seed
could land (minting R3). Our write then stamps R2 (== R1). Later `read_response` checks `R2 == latest()`
where `latest()` is now R3 → **fails** → the answer is written but **never applied**. That is the *correct*
outcome (drop a stale answer), enforced by the existing FIX-F21 freshness check. So capture-and-recheck
eliminates the dangerous "stale answer accepted as fresh" case; the only remaining behavior is the benign
"stale answer written but ignored," which the read-side check already handles. (To be re-checked by an
adversarial skeptic constructing interleavings — guardrail 4.)

## Decision

**Approach A.** ~4 lines in `answer_pending`, no new lock surface, no 180s serialization, and it extends the
codebase's established request_id-correlation philosophy (agentstep.py docstring: "the request_id check …
is the real safety net … we intentionally skip the temp-file+os.replace+lock machinery") to the write side.

User input: "make the call" (decision delegated to the orchestrator).

## Test contract (the binding part)

A failing-first test in `tests/test_responder.py` that simulates a mid-call re-seed: the model callable,
when invoked, itself calls `write_request` to bump the gate to R2/P2 (simulating the overlapping run),
returns a P1-derived answer; assert the responder does NOT write a response that `read_response` would
accept as fresh (i.e. the stale answer is dropped, the gate stays pending). Mutation-proof by reverting the
capture-and-recheck and confirming the test goes RED (a stale answer becomes acceptable).
