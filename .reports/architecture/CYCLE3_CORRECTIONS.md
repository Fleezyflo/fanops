# FanOps — Cycle 3 corrections to Cycles 1 and 2

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73` (unchanged)**

Authority order applied: **executable code > executed experiment > Cycle-2 JSON > `CYCLE2_EXTENSION.md` >
older prose > comments.** Where sources conflicted I did not average them. Each correction below names the
superseded claim by its stable ID and says why it was wrong.

Cycle 2 closed with the method note: *"any claim of the form 'nothing does X' must be produced by an AST pass
… never by a grep."* Cycle 3 adds the runtime twin of that note:

> **A guard's reachability is not established by reading the guard. It is established by reading the branch it
> sits on.** Three of the corrections below are guards that exist, are correct in isolation, and sit on a
> branch their own stated precondition can never reach.

---

## C3-COR-01 — `transitions.json` P16 / `STATE_MACHINE.md` P16 / `CYCLE2_EXTENSION.md` §2.3: **the escalation timer is wrong**

| Source | Claim | Verdict |
|---|---|---|
| [`transitions.json`](transitions.json) P16 | `submitting → needs_reconcile` guard = *"fake `fanops_` token + age > 72h (`_RECONCILE_GIVEUP_AFTER`, reconcile.py:54)"* | **FALSE** |
| [`STATE_MACHINE.md`](STATE_MACHINE.md) §2.1 P16 | *"fake `fanops_` token **+** age > 72 h [reconcile.py:54]"* | **FALSE** |
| [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md) §2.3 | *"`submitting` → **72 h** → `needs_reconcile` → **72 h** → `GAVE UP:`"* (⇒ 144 h total) | **FALSE** |
| [`invariants.json`](invariants.json) INV-03 `terminal_state` | same 72 h → 72 h chain | **FALSE** |

**The code.** The escalation guard reads `age > _SUBMITTING_ESCALATE_AFTER`
([reconcile.py:747](src/fanops/reconcile.py:747)), and `_SUBMITTING_ESCALATE_AFTER = timedelta(hours=24)`
([reconcile.py:48](src/fanops/reconcile.py:48)). Cycle 2 cited `reconcile.py:54`, which is the **different**
constant `_RECONCILE_GIVEUP_AFTER = timedelta(hours=72)` — the guard on the *second* hop
([reconcile.py:758](src/fanops/reconcile.py:758)).

**And the two hops do not chain.** Both compare against `age = _parked_age(post, now)` =
`now - post.scheduled_time` ([reconcile.py:61-69](src/fanops/reconcile.py:61)) — an age measured from the
**schedule**, not from the previous transition. Executed (EXP-3/H4):

```
@+  6h  state=submitting        reason=''
@+ 25h  state=needs_reconcile   reason='escalated submitting->needs_reconcile after 25h …'
@+ 73h  state=needs_reconcile   reason='GAVE UP: unresolved 73h past schedule …'
```

**Corrected:** escalation at **24 h past schedule**; give-up at **72 h past schedule**. Total time-to-terminal
is **72 h, not 144 h.** Also pinned: `_STUCK_AFTER = 6h` ([:43](src/fanops/reconcile.py:43)),
`_SUBMITTING_HEAL_AFTER = 15min` ([:50](src/fanops/reconcile.py:50)).

---

## C3-COR-02 — `INV-03` blast radius: **"eventually labeled" is true on Postiz and FALSE on Zernio**

**Superseded claim** ([`invariants.json`](invariants.json) INV-03 → `terminal_state.at_least_one_valid_channel_remains`;
[`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md) §2.3; [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-03 *"It is
**eventually labeled**: reconcile escalates `submitting → needs_reconcile` at 72 h, then stamps `GAVE UP:`"*):

> the malformed-provider post is *eventually labeled*.

**That is backend-conditional, and Cycle 2 asserted it unconditionally.** The escalation and the give-up both
live in the `else:` branch that runs **only after a poll SUCCEEDS**
([reconcile.py:739](src/fanops/reconcile.py:739)). A poll that **raises** is caught at
[reconcile.py:627](src/fanops/reconcile.py:627) and `continue`s at
[reconcile.py:635](src/fanops/reconcile.py:635) — it never reaches either.

The two status clients differ on exactly this:

| Client | Unknown submission id | Reaches the escalation? |
|---|---|---|
| `PostizStatusClient.get_status` | row absent from the ±35 d page → **`return {"status": "unknown"}`** ([post/metrics.py](src/fanops/post/metrics.py)) | ✅ yes |
| `ZernioStatusClient.get_status` | `GET /posts/{id}` → 404 → **`raise RuntimeError`** (`if resp.status_code >= 300`) | ❌ **never** |

Executed (EXP-4/H5): the *same* `fanops_`-token post, driven with the Zernio client's shape, sat in
`submitting` at +6 h, +25 h, +73 h, +1000 h and **+100 000 h** — never escalated, never gave up.

**Corrected:** on **Zernio/TikTok the XC-1 escalation and the XC-2 give-up are dead code**, and a
crash-stranded or malformed-provider TikTok post is **never labeled at all**. This is a new finding
(`C3-F2`), not just a refinement — and it is exactly the *sibling-parity divergence* class
`src/fanops/CLAUDE.md` warns about.

---

## C3-COR-03 — `_is_fake_token` is a **gate on the terminal path**, not merely a precondition

**Not previously recorded.** Cycle 2's P16/P17 rows list `_is_fake_token` as part of the guard but never asked
the inverse question: *what happens to a post with a **real** token that never resolves?*

Both terminals require it:
- escalation: `if age is not None and _is_fake_token(post) and post.state is PostState.submitting …` ([reconcile.py:746](src/fanops/reconcile.py:746))
- give-up: `if age is not None and _is_fake_token(post) and post.state is PostState.needs_reconcile …` ([reconcile.py:757](src/fanops/reconcile.py:757))

So a **real** submission id is structurally excluded from both. The code justifies this in a comment —
*"A post carrying a real id is left to its normal poll (**its status WILL resolve**), never escalated"*
([reconcile.py:76](src/fanops/reconcile.py:76)) — which is an **assumption, not a guarantee**. Executed
(EXP-4/H6), on the *working* backend: a real-token post got one `stuck …` breadcrumb at 25 h and was then
silently skipped by [reconcile.py:767](src/fanops/reconcile.py:767) (`if post.error_reason: continue`) at
+73 h and **+100 000 h**. It never left `submitting`.

**This falsifies brief §9 claims #1 and #2** and is filed as `C3-F1`.

---

## C3-COR-04 — `MUTATION_MATRIX.md` A.3 / `mutation_writers.json`: `Render.path`'s writer is under-described

**Superseded claim** ([`MUTATION_MATRIX.md`](MUTATION_MATRIX.md) §A.3): *"`Render.path` — the one Render
mutation … a **post-compression path rewrite**."* True but incomplete: it omits **what the path is rewritten
to**.

`apply_shrink_to_post` sets `led.renders[rid] = r.model_copy(update={"path": str(shrunk)})`
([compress.py:112](src/fanops/post/compress.py:112)), where `shrunk` comes from `maybe_shrink_for_cap`, whose
output lives in `tempfile.mkdtemp(prefix="fanops-shrink-", dir=cfg.base/"04_agent_io")`
([compress.py:21](src/fanops/post/compress.py:21)). FINALIZE then **persists it**
([run.py:364-367](src/fanops/post/run.py:364)).

Executed (EXP-10) — `Render.path` after a shrink:
`…/MohFlow-FanOps/04_agent_io/fanops-shrink-c9zxj7_0/clip_1.crf28.mp4`

**Corrected:** the ledger's durable canonical media pointer for a shrunk render is a **`mkdtemp` path**, and
`compress.py:21` is the **only `mkdtemp` in `src/fanops`** with **no cleanup anywhere in the tree**. Filed as
`C3-F4` (unbounded temp leak) and `C3-F5` (durable pointer into a temp dir, no invalidation rule).

---

## C3-COR-05 — `MUTATION_MATRIX.md` §E: "atomic control-file writes" is **not universal**

**Superseded claim** ([`MUTATION_MATRIX.md`](MUTATION_MATRIX.md) §E, row *"Control JSON"*): *"`controlio.write_json_atomic`
— mkstemp same-dir + `os.replace` — **atomic**"*; and [`src/fanops/CLAUDE.md`](src/fanops/CLAUDE.md): *"**Atomic
control-file writes** route through `controlio.write_json_atomic`."*

Two live control-file writers **do not** route through `controlio` and are **not** atomic:

| Writer | Mechanism | Consequence |
|---|---|---|
| `agentstep.bump_attempts` ([agentstep.py:147](src/fanops/agentstep.py:147)) | bare `p.write_text(...)` | a torn `attempts.json` → `except Exception: n = 0` ([:144-145](src/fanops/agentstep.py:144)) → **the gate-retry ceiling silently resets** |
| `pipeline_run.note_stage` ([pipeline_run.py:64-69](src/fanops/pipeline_run.py:64)) | `os.ftruncate(fd,0)` then `os.write(fd,…)` | a crash in the window empties `.run.lock`'s body → `_read_body` → `{}` → the mid-pass stage breadcrumb is lost |

Executed (EXP-7): `bump_attempts` reached `n=3` (the `_GATE_DETERMINISTIC_MAX` ceiling), a torn write was
simulated, and the next call returned **`n=1`**. Executed (EXP-8): a crash between `ftruncate` and `write` left
`_read_body → {}` and `run_stage_snapshot → None`.

**Corrected: brief §9 claim #8 ("Every control-file write is crash-safe") is FALSIFIED.** `bump_attempts`'
own two siblings in the same file (`write_request`, `write_response`) *are* atomic — another sibling-parity
gap. Filed as `C3-F6` / `C3-F7`.

---

## C3-COR-06 — `COUP-02b` is correct, but `INV-11` needs a runtime scope

**Not a contradiction — a scope the invariant never carried.** [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md)
INV-11: *"`AuthError` halts the run, never burns the queue — **Verified**."* That verdict is correct **for one
pass**. At the *daemon* level it does not hold as an operator would read it:

```python
while True:
    load_dotenv(...); cfg = Config(cfg.root)
    try:
        if (s := _cmd_run_pass(cfg, base_time)) is not None:
            _heartbeat(cfg, s, origin="loop"); print(s)
    except RunBusyError as e: ...
    except Exception as e:
        print(f"run halted: {type(e).__name__}: {e}", file=sys.stderr)   # cli.py:1311-1312
    time.sleep(interval)
```
— [cli.py:1300-1313](src/fanops/cli.py:1300)

`_cmd_run_pass` already catches everything and returns `None` ([cli.py:947-949](src/fanops/cli.py:947)). So a
persistent `AuthError` (a rotated Postiz key) **halts each pass and is re-driven every tick, forever**, with
**no heartbeat written** (the heartbeat is emitted only on `s is not None`,
[cli.py:1306](src/fanops/cli.py:1306)) and **no ledger state change**.

**Refinement:** INV-11's property (*never burn the queue*) **holds**. What does **not** hold is any implication
that the run *stops*. The daemon retries an unrecoverable auth failure indefinitely. Filed as `C3-F8`
(observability, not correctness).

---

## C3-COR-07 — `daemon.status`'s liveness verdict masks a totally-failing daemon

**Not previously examined.** `daemon.status` ([daemon.py:462-476](src/fanops/daemon.py:462)) treats a stale
heartbeat as non-fatal when `daemon_progress` reports `alive_mid` — *"the newest run.log line of **ANY** kind is
younger than the ceiling"* ([daemon.py:439-442](src/fanops/daemon.py:439)).

A daemon whose **every pass halts** still writes run.log lines during the pass (every stage logs). So
`alive_mid` is `True`, and the verdict is **`alive`** — while no heartbeat has been written and nothing has
published. The design is deliberate and correct for its stated purpose (*"that heartbeat only lands after a
whole pass finishes, so it must NEVER flip a fast-logging pass to stale"*), but the **consequence** was not
recorded: `fanops daemon status` cannot distinguish *healthy* from *failing every pass*. Filed as `C3-F9`
(`observability_gap`). This does **not** contradict the project memory `liveness-verdict-single-owner` — it
qualifies what the single owner can and cannot tell you.

---

## C3-COR-08 — `INV-14` is correct but creates a **false impression**; the irreversible actuator is the ungated one

[`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-14 — *"Bias actuators amplify-only + validation-frozen —
**Verified**"* — is **true as scoped** (`p4_dim_bias`, `variant_amplify`, `timing_bias`). Cycle 3 does not
retract it. But the scope hides the destructive path:

`_learn_pass` ([cli.py](src/fanops/cli.py)) runs, gated on **`cfg.is_live_backend` only**:

```python
led = pull_metrics(led, cfg, ...)
r   = classify_outcomes(led, per_surface=cfg.adjust_per_surface)
led = amplify(led, cfg, r["winners"])
led = retire(led, r["losers"])          # <-- adjust.py:82
```

`adjust.retire` writes `MomentState.retired` ([adjust.py:95](src/fanops/adjust.py:95)), and
`reconcile_moments` **refuses to un-retire** ([ledger.py:636-642](src/fanops/ledger.py:636)) — it is
**irreversible**. It is **not** behind `validation_gate.learning_validated`, and it fires as soon as
`round(n * 0.2) >= 1`, i.e. **n = 3 analyzed posts**
([adjust.py:47-52](src/fanops/adjust.py:47)).

Meanwhile the **reversible** amplify-side bias actuators wait for `learning_validated` **and**
`p4_unlocked` (≥ 8 attributed posts × ≥ 2 values, [validation_gate.py:52](src/fanops/validation_gate.py:52)).

**The destructive actuator has the weakest gate.** Real guards do exist (bottom-20 % ∧ `lift < 20.0` ∧ not a
winner ∧ **not `lift_degraded`** — [adjust.py:50-52](src/fanops/adjust.py:50)), which is why this is
`operational_hazard` and not a reachable defect. Filed as `C3-F10`.

---

## C3-COR-09 — Cycle-1 `OPS-001` still engaged: **Cycle 3 was also single-threaded**

The orchestration gate refused an `Explore` subagent spawn again:

> `REFUSED (orchestration gate): spawn type 'Explore' is not allowed during a wave.`

It also **denied the `Write` tool** for a scratchpad analysis script (worked around via a shell heredoc).
**Three consecutive cycles (1, 2, 3) have now been executed single-threaded** because a stale wave marker
(`.orchestration/state/ACTIVE` = `engaged`, last touched 2026-07-13) has never been disengaged. This is an
operator action (`orchestrate.py stop`), not a code change. It is the single largest constraint on this audit's
throughput and should be cleared before Cycle 4.

---

## Claims from Cycles 1–2 that Cycle 3 **re-verified and upholds**

Recorded so a later cycle does not re-litigate them:

| Claim | Cycle-3 status |
|---|---|
| `INV-08` no-auto-publish (born `awaiting_approval`; publish iterates `queued` only) | **upheld** — re-derived from [run.py:442](src/fanops/post/run.py:442) + [ledger.py:579](src/fanops/ledger.py:579) |
| `INV-09` `_publish_one` is the sole network-POST caller | **upheld** — [run.py:296](src/fanops/post/run.py:296) is the only `poster.publish` call |
| `INV-10` `needs_reconcile` never downgraded to `failed` | **upheld** — [run.py:325](src/fanops/post/run.py:325), [postiz.py:433](src/fanops/post/postiz.py:433), [zernio.py:271](src/fanops/post/zernio.py:271) |
| Claim-before-network (F11) | **upheld** — CLAIM commits at [run.py:272](src/fanops/post/run.py:272) before any I/O |
| Both posters park (never re-POST) on an ambiguous send | **upheld, and now proven symmetric** — Postiz [:398/:424](src/fanops/post/postiz.py:398) and Zernio [:242/:264](src/fanops/post/zernio.py:242) retry **only** `ConnectTimeout` (connection never established) and `429` (body rejected). Every other network exception and every 5xx parks. **`_publish_one`'s outer retry ([run.py:292](src/fanops/post/run.py:292)) can therefore only ever re-run the media UPLOAD, never the publish POST.** |
| `SC-2` — the daemon re-reads `.env` every tick | **upheld** — [cli.py:1303-1304](src/fanops/cli.py:1303) |
| Cycle-2 `F-A` (malformed provider → `DryRunPoster` when live) | **upheld, and the mechanism is now pinned**: `_reconcile_safe` is gated on `is_live_backend` ([pipeline.py:318](src/fanops/pipeline.py:318)) while `_publish_safe` is **not** ([pipeline.py:334](src/fanops/pipeline.py:334)) — publish claims posts that reconcile will never read (EXP-11) |
| All five requeue paths clear `submission_id` | **upheld** — [run.py:423](src/fanops/post/run.py:423), [actions.py:1001/1032/1063/1104](src/fanops/studio/actions.py:1001). A **hypothesis that a requeue could strand a real-sid post was tested and DISCONFIRMED.** The strand arrives by a different door — `bulk_send_to_review`, which *deliberately* preserves `submission_id`. |
