# C4-SLICE-08 — Daemon health: `alive` must not mean `succeeding`

**Root cause:** `RC-6` · **Severity: MEDIUM** · **Prerequisites: none** · **Buildable in parallel**
**PR title must carry:** `(Unit: daemon-alive-vs-succeeding)`

---

## 0. Before you edit anything
**Reverify the cited lines.** State the root cause in your own words. Then read §3 — **it names a project memory
you must not violate.**

---

## 1. What is broken

`daemon.status` ([daemon.py:462-476](src/fanops/daemon.py:462)):

```python
elif not stale:
    verdict = "alive"                     # :463  fresh loop heartbeat
else:
    alive_mid, progress_line, snap = daemon_progress(cfg)
    if alive_mid:                         # :467  "the newest run.log line of ANY kind is fresh"
        verdict = "alive"                 # :468  ← and no heartbeat has been written in HOURS
```

**A pass that HALTS still writes `run.log` lines** — every stage logs before the halt. So `alive_mid` is `True`
and the verdict is **`alive`** — while:

- **no heartbeat** has been written (`_heartbeat` fires only `if s is not None`,
  [cli.py:1306](src/fanops/cli.py:1306)),
- **no ledger state** has changed,
- **nothing has published.**

**The classic trigger:** a rotated Postiz key → `AuthError` → the pass halts every tick, forever
([cli.py:947-949](src/fanops/cli.py:947), [:1311-1312](src/fanops/cli.py:1311)).

> **The operator asks the system's own liveness question and is told `alive` while nothing is publishing.**

### The root

**Six distinct facts are collapsed into one word:**

`process alive` · `loop ticking` · **`pass started`** · **`pass SUCCEEDED`** · **`state ADVANCED`** ·
**`publishing healthy`**

**`status` reports the first two and names the result `alive`, which the operator reads as the fourth.**

---

## 2. The fix — the data **already exists**; the verdict discards it

🔴 **`heartbeat_age_s` is ALREADY in the returned dict**, and the heartbeat
([cli.py:1306](src/fanops/cli.py:1306)) fires **only when a pass succeeds** — **it already *is* the success
signal.** It is simply **not read as one**: *"it no longer governs the verdict on its own."*

**Mint the missing SECOND word.**

- `health_model.py`: add **`last_success_age_s`** alongside `heartbeat_age_s`.
- `daemon.py status`: **keep `alive` keyed on `daemon_progress`** — see §3 — and **ADD** a second, orthogonal
  verdict, e.g. `alive (no successful pass in {N}m)` when the process is live but `last_success_age_s` exceeds a
  multiple of the interval.

---

## 3. 🔴 The project memory you must not violate

> **Project memory `liveness-verdict-single-owner`:** `daemon_progress` is **THE** mid-pass liveness owner.
> *"the newest run.log line of any kind = alive; fix there, not per-surface; **don't touch
> `_heartbeat_age_s`**."*

**The `alive_mid` override is CORRECT for its stated purpose:** *"that heartbeat only lands after a whole pass
finishes, so it must NEVER flip a fast-logging pass to stale."* **That property must survive this slice.**

> **This slice ADDS a signal. It MOVES none.**
> **Do not change who owns liveness. Do not change `_heartbeat_age_s`'s role in the alive/stale decision.**

---

## 4. Acceptance criteria

1. `status(cfg)` returns **`last_success_age_s`**, distinct from `heartbeat_age_s`.
2. A daemon whose **every pass raises** does **NOT** return a bare verdict `alive`.
3. 🔴 **`daemon_progress` remains the mid-pass liveness owner.**
4. 🔴 **A healthy fast-logging pass is still NOT flipped to `stale`** — the property the `alive_mid` override
   exists to protect. **Pin it with a non-regression test.**

## 5. Tests

| Test | Must fail before? |
|---|---|
| `test_daemon_status_distinguishes_alive_from_succeeding` | ✅ |
| `test_healthy_fast_pass_is_not_flipped_stale` *(non-regression — the `alive_mid` property)* | ⚪ |

## 6. Enumerate before you edit
Every reader of `daemon.status()`'s returned dict (the CLI `daemon status`, the Studio Home banner, `/healthz`,
`/home/daemon-health`) · every writer of the heartbeat record. **Confirm no consumer parses the verdict STRING
for equality with `"alive"`** — if one does, adding a new verdict string **breaks it**, and you must say so.

## 7. Preserve
- The `alive_mid` override's protective property (a fast-logging pass is never flipped to `stale`).
- `heartbeat_age_s` in the returned dict.
- The `not loaded` → **ALARM**, `stage stuck`, and `not running` verdicts.

## 8. 🔴 Forbidden scope expansion
- ❌ Do **not** touch `_heartbeat_age_s`'s role in the alive/stale decision *(project memory)*.
- ❌ Do **not** merely **re-word** the verdict strings without adding the new **SIGNAL** — the audit brief names
  this exactly: *"changing `daemon status` wording without separating progress signals."*
- ❌ Do **not** fix the half-live banner suppression (`C3-OBS-5`). Different surface, separate finding.
- ❌ Do **not** stop the daemon from retrying an unrecoverable `AuthError` — **that is arguably correct** (the key
  may be rotated back). **This slice makes it VISIBLE, which is the actual gap.**
- ⚠️ `S08` **owns `cli.py` exclusively** among the Cycle-4 slices. Coordinate with `S09` (which may want a
  `fanops clean --shrink` verb).

## 9. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets — **`test_internal_prints_routed` pins
`cli.py`'s `print()` count**, and this slice touches `cli.py`. **If you add a print, bump the count with a
rationale.**
**Self-merge on green: YES.** **Verifier: not required.**
**Rollback:** revert.
**State remaining unknowns honestly.**
