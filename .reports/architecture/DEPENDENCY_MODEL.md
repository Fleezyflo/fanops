# FanOps — Dependency Model

**Cycle 5 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`kb/dependencies.json`](kb/dependencies.json)

**No prior cycle built this.** Cycles 1–4 produced findings, root causes, and a remediation sequence, but the
module dependency graph — the thing that determines whether *any* of those fixes is safe to make — was never
derived. This is that graph.

**Method:** an AST pass over all 127 modules of `src/fanops`. Imports are classified by **syntactic position**:
module-level = *compile-time*; inside a `FunctionDef` = *runtime (lazy)*; under `if TYPE_CHECKING:` =
*typing-only*; inside `try/except ImportError` = *optional*.

> **Why you can trust the analyzer.** The same pass, without being told what to expect, **independently
> reproduced four of Cycle 3's censuses exactly**: subprocess sites **32**, `Ledger.transaction` sites **74**,
> `mkdtemp` **1**, `rmtree` **2**. That agreement is the evidence.
>
> **And where it disagreed, it was the analyzer that was wrong first** — see §6.

---

## 0. Three graphs, three meanings — read this first

**Conflating these is itself an architectural error, and the first draft of this document made it.**

| | Definition | What a cycle in it **means** | Is it a DAG? |
|---|---|---|---|
| **G1** — compile-time import graph | module-level (top-of-file) imports | 🔴 **A hard load-order constraint.** Resolved at import time. A cycle here is load-order-sensitive and can become an `ImportError` at process start. | ❌ **No** — one non-trivial SCC |
| **G1c** — the **SCC-condensation** of G1 | G1 with every SCC collapsed to a single node | The **layer level** is defined on this, and **only** on this. | ✅ **Yes — by construction.** A condensation always is. |
| **G2** — static **potential-dependency** graph | compile-time **∪** lazy (in-function) imports | 🔴 **A static OVER-APPROXIMATION of runtime dependency.** A lazy import materializes **only if its enclosing function is called.** An SCC here means *"these modules **could** reach each other"* — **not** that they do. It **bounds** blast radius; it does not **establish** it. | — |
| **G3** — the runtime **call** graph | — | 🔴 **NOT DERIVED by any cycle. G2 is a strict superset of it.** No claim in this KB rests on G3. | — |

> **"FanOps has an 11-level layered architecture"** is true **only of G1c**, and it is an *output* of the import
> structure — **not a constraint anyone enforces.**

---

## 1. The numbers

| | |
|---|---|
| Modules | **127** |
| **G1** compile-time (module-level) edges | **528** |
| **Lazy** (in-function) edges | **323** |
| Typing-only edges | 6 |
| **G1 non-trivial SCCs** | 🔴 **1** — and **124 of 127 modules are singleton SCCs** |
| **G1c levels** | **11** (0 – 10) |
| **Lazy edges to an equal-or-higher level** | 🔴 **107** — **56 strictly upward** (true inversions) **+ 51 lateral** |
| **G2** module SCCs | **8** — largest is **45 modules** *(potential, not demonstrated)* |
| **G2** subsystem SCCs | **1 — all 19** *(potential, not demonstrated)* |
| Subsystem-level cycle in **G1** | **1** — a **7-subsystem** SCC. 🔴 **Real coupling — but NOT an import cycle.** See §5. |

---

## 2. The hubs — "if I change this, who breaks?"

**Fan-in, compile-time.** These are the modules the whole tree rests on.

| Dependents | Module | Note |
|---|---|---|
| **82** | `config` | 🔴 **The most-depended-on module in the system — and it is *depth 0*, with zero compile-time `fanops` imports.** See §4. |
| **53** | `ledger` | |
| **48** | `models` | |
| 32 | `log` | |
| 31 | `errors` | |
| 17 | `timeutil` | fails **closed** to UTC |
| 15 | `accounts` | |
| 11 | `ids` | content-addressing; `hash()` is banned |

**Fan-out, compile-time.** These are the orchestrators.

| Imports | Module |
|---|---|
| **27** | `pipeline` — `advance()` is the whole engine |
| 20 | `cli` |
| 14 | `moments` |
| 13 | `crosspost` · `studio.actions` · `studio.golive` |

---

## 3. The one compile-time cycle — and what it is *not*

```
   personas ──module-level──▶ persona_store    ──module-level──▶ personas
   personas ──module-level──▶ persona_research ──module-level──▶ personas
```

**This is the only non-trivial SCC in G1.** Tarjan over the compile-time graph returns **125 SCCs: 124
singletons + this one triple.** Python resolves it via partial-module binding — it works today. But it is
**load-order sensitive**, and unlike several of the strictly-upward *lazy* imports (some of which carry explicit
"break the cycle" comments), **this one is undefended**: no comment, no test, no note.

> 🔴 **And it has nothing to do with the learning loop.** It sits in the persona/registry subsystem. **The
> domain core — `moments`, `adjust`, `caption`, `clip`, `track`, `moment_hook_learning` — is ACYCLIC at module
> level.** Every one of those modules is a singleton SCC.

**Whether the persona cycle is intentional is not recoverable from the code.** Filed as `UNK-C5-1`, not guessed.

---

## 4. 🔴 The central finding: acyclicity is *purchased*, not designed

**G1c** — the SCC-condensed compile-time graph — is a clean **11-level DAG**. That sounds healthy. **It is an
artifact of the construction.**

**Level is *defined* on the condensation** (`level(SCC) = 1 + max(level(targets))`), and **a condensation is
always a DAG.** So G1c **cannot contain a backward edge, by construction.** The metric is therefore only
meaningful for the **lazy** edges — which is exactly what it is used for. And there:

> **107 of 323 lazy edges point to an equal-or-higher level.**
> **56 are *strictly upward* — true layering inversions.**
> **51 are lateral** (same level; not inversions, recorded separately).

### The steepest inversions

| | | |
|---|---|---|
| `compose` (L0) | ..lazy..▶ | `clip` (L5) |
| `doctor` (L3) | ..lazy..▶ | `pipeline_status` (L8) |
| `init_flow` (L4) | ..lazy..▶ | `studio.golive` (L9) |
| `digest` (L4) | ..lazy..▶ | `variant_amplify` (L8), `p4_dim_bias` (L8) |
| `health_model` (L1) | ..lazy..▶ | `learn_doctor` (L4), `studio.views_review` (L4) |
| 🔴 **`config` (L0)** | **..lazy..▶** | **`accounts` (L2), `meta_graph` (L3)** |

> **The base of the stack calls the middle of it.** `config` has **zero** compile-time `fanops` imports — which
> is *why* 82 modules can import it — and it reaches **up** to `accounts` and `meta_graph` from inside function
> bodies. **The layering is not enforced; it is simulated, one deferred import at a time.**

### What the graph looks like once you count them

```
   G1c — COMPILE-TIME, CONDENSED          G2 — STATIC POTENTIAL DEPENDENCY
   ────────────────────────────           ────────────────────────────────
   an 11-level DAG (by construction)      a 45-MODULE SCC  ⚠ POTENTIAL, not demonstrated
   125 SCCs: 124 singletons + 1 triple    8 SCCs
   the domain core is ACYCLIC             config · ledger · accounts · clip · moments · post.*
                                          · personas · transcribe · framing · caption · … (45)

                                          AND: all 19 subsystems in ONE SCC.
                                          ⚠ This is an UPPER BOUND on coupling. A lazy edge
                                            materializes only if its function is CALLED.
                                            G2 is a SUPERSET of the runtime call graph,
                                            which was NEVER DERIVED.
```

### Why this is a risk, not a curiosity

**Module initialization order is load-bearing, and nothing enforces it.** No test, no lint rule, no layer
declaration. **Hoisting any one of the 56 strictly-upward imports to module level — a change that looks like a
cleanup, and that a reviewer would wave through — breaks the process at start.**

**This supersedes `COUP-09`** (Cycle 2), which recorded *"nine lazy in-function imports exist solely to break
module cycles"* and enumerated nine. **The nine are a subset of the 107.** How many of the rest are deliberate
is **UNKNOWN** (`UNK-C5-2`) — **it is not guessed, and the earlier claim that "most were not deliberate" is
retracted as unsupported.**

---

## 5. The subsystem topology

**19 subsystems (S01–S19; S16 is Studio). Partition is total: 127/127 modules, 0 unassigned, 0 ghosts.**

> 🔴 **A subsystem is an ANALYTIC OVERLAY imposed by Cycle 5.** It is not declared anywhere in the repository,
> no package boundary corresponds to it, and **nothing enforces it.** The **modules** and the **import edges**
> are facts; the **grouping** is a model. That distinction is load-bearing — see §6.

### 🔴 A 7-subsystem cycle — which is real coupling, and is **not** an import cycle

```
   S04 registry ◀────────────────────────────────┐
      │  persona_store → hashtags                │  adjust → accounts
      ▼                                          │
   S10 caption ──── caption → variant_learning ──▶ S14 learning
      │                                          │   ▲
      │ caption → agentstep                      │   │ moments → moment_hook_learning
      ▼                                          │   │
   S07 agent-gate ◀── moments → agentstep ─── S08 selection ◀── adjust → moments  ★ CLOSES THE LOOP
                                                  │   ▲
                                    moments → clip│   │ stitch_render → router
                                                  ▼   │
                                              S09 render
                                                  
   S13 metrics ── fanops_account_stats → accounts ──▶ S04
              ◀── learn_doctor → track ── S14
```

### What it **is**, and what it **is not**

| | |
|---|---|
| ✅ **IS: real coupling.** | These seven subsystems are mutually dependent. **You cannot extract `S08 selection` as a library without `S14 learning`.** Every closing edge was traced to a real module-level edge and verified. |
| 🔴 **IS NOT: an import cycle.** | It is produced by **grouping distinct modules into subsystem nodes.** `moments → moment_hook_learning` and `adjust → moments` are **two different module edges** that collapse into a 2-cycle *only once their modules are aggregated*. **At module level all of them are singleton SCCs.** It carries **none** of the load-order fragility of a real import cycle. |

### 🔴 Two claims that must be kept apart

The first draft of this document ran them together, and got the second one wrong.

| | Claim | Verdict |
|---|---|---|
| **A** | **The DOMAIN has a feedback loop.** select → render → caption → publish → measure → learn → select. | ✅ **Intentional. It IS the product. CERTAIN.** The project's own `CLAUDE.md` documents the reach loop, the bias actuators, and the validation gates. Not inference. |
| **B** | **Therefore this mutual-import wiring is correct / "not a defect."** | 🔴 **NON SEQUITUR. RETRACTED.** |

**A feedback loop in *data/control flow* does not require a cycle in the *static import graph*.** The loop could
equally be closed **by the orchestrator** (`pipeline` calls both `adjust` and `moments`) or **via a shared
abstraction**, leaving the subsystem graph acyclic. **Dependency inversion exists precisely for this.**

> **The wiring is a design choice, not a consequence of the loop existing — and whether it is intentional is
> UNKNOWN** (`UNK-C5-4`). No `CLAUDE.md`, codemap, ADR, or test declares it.
>
> **What a future engineer should take from this:** the loop **is** the product and is not up for debate. The
> mutual-import wiring is **one way** to build it. Inverting it is a **legitimate option** — *a decision to be
> made, not a given to be preserved.* The earlier verdict *"this is not a defect"* would have told them the
> opposite.

### How I know it is real and not something I invented

An initial partition produced a **13**-subsystem cycle. Rather than report it, I traced every closing edge to
its **module-level** source. **Three were my own mis-drawn boundaries**, not inversions:

| Edge that vanished | Why it was fake |
|---|---|
| `S03 persistence → S12 publish` | It was *only* `paths_rebase → post.media`. `paths_rebase` is a **recovery utility**, not the ledger core. **`ledger.py` never imports publish.** |
| `S06 media → S15 orchestration` | It was *only* `transcribe → stage_lock`. `stage_lock` is a **lock primitive** — foundation, not orchestration. |
| `S08 selection → S14 learning` (as drawn) | `moment_hook_learning` **is** a learning module; I had filed it under selection. |

I corrected the partition and re-derived. **The 7-subsystem cycle is what survives.**

> **Aggregating modules into subsystems manufactures cycles that do not exist at module level.** A
> subsystem-level violation must be traced to its module edges *before* it is reported — otherwise the audit
> fabricates the very architecture it was asked not to invent.

---

## 6. Four self-corrections, recorded rather than quietly fixed

### 🔴 `C5-SC-3` — I called G1 "an 11-level DAG (modulo the one persona cycle)". **A graph with a cycle is not a DAG.**

*"Modulo the one cycle"* was hand-waving, and it hid a real defect in the metric. What I actually computed was a
**cycle-cutting DFS** (`if m in seen: return 0`) — which is **entry-order-dependent for nodes inside an SCC**.

The correct construct is the **SCC condensation**, on which level is well-defined for every module. Recomputing
against true condensed levels **changed six module levels** (`personas` 4→3, `caption` 5→4, `studio.views_review`
5→4, `studio.views` 6→5, `studio.actions_approve` 7→6, `studio.app_routes_live` 7→6) — and **moved the headline
count from 106 to 107.**

**And it exposed a second error the single number was hiding:** 107 **conflated two different things.**
Decomposed: **56 strictly upward** (true layering inversions) **+ 51 lateral** (same-level deferred imports,
which are *not* inversions at all).

> **A derived number inherits the soundness of its definition.** I shipped a headline figure computed by a
> metric I had not stated precisely.

### 🔴 `C5-SC-4` — I asserted "the domain core is a compile-time cycle… this is not a defect." **Both halves were wrong.**

1. **At module level the domain core is ACYCLIC.** 124 of 127 modules are singleton SCCs. The 7-subsystem cycle
   exists **only in the aggregated view**, and the *only* real compile-time import cycle (`personas`×3) is
   **unrelated** to the learning loop. I stated a property of my own taxonomy as if it were a property of the
   import graph.
2. **"The loop is the product, therefore the wiring is correct" is a non sequitur** (§5).

> **The comfortable inference is the dangerous one.** *"It's the product, leave it alone"* would have told a
> future engineer that a **design choice** was a **given.**

### `C5-SC-1` — I reported 39 network call sites. The real number is 15.

My first query matched `s.get(...)` / `session.get(...)` — which also matches **plain dict `.get()`**. It
counted `led.get()`, `info.get()` and friends as network calls.

Filtering to literal `requests.<verb>`: **postiz 5, zernio 4, metrics 4, health_model 1, cli 1 = 15** — which
reproduces Cycle 3's count.

> **This is Cycle 2's own method note biting the cycle that wrote it down:** *a census is only as good as its
> query.* It is recorded here rather than silently fixed, because **a quietly-corrected census is
> indistinguishable from one that was never wrong** — and the next reader deserves to know the failure mode is
> live.

### `C5-SC-2` — the 13-subsystem cycle (see §5).

---

## 7. External dependencies

| Class | Packages |
|---|---|
| **Required** | `pydantic`, `requests` (the **sole** HTTP library), `python-dotenv` |
| **Optional, fail-OPEN** | `keyring`, `whisper` / `faster_whisper`, `certifi` |
| **Studio-only, lazy** | `flask` |
| 🔴 **Optional, fail-CLOSED** | **`cv2`** — *the only fail-closed optional dependency in the system.* With `smart_framing` **ON (the default)** and cv2 absent, `framing.require_cv2` raises `ToolchainMissingError` → **exit 2**. Every other extra fails open. (`INV-17`) |

**Not an HTTP dependency:** the **LLM is a subprocess** (`claude -p`), riding the operator's existing
`claude login`. `ANTHROPIC_API_KEY` is *not* required, and `claude --bare -p` provably **fails** because it
never reads the keychain.

---

## 8. What this model does *not* establish

- 🔴 **This is the IMPORT graph, not the CALL graph — and that qualifier is load-bearing on every SCC claim in
  this document.** G2 is a **static over-approximation**: a lazy import materializes only if its enclosing
  function is *called*. **The 45-module SCC and the all-19-subsystem SCC are POTENTIAL mutual reachability.
  They bound the blast radius; they do not establish it.** Where reachability actually mattered (`Render`,
  `intro_match`), prior cycles proved it by AST **writer** census plus a live-ledger read — **not by imports.**
- **Intent.** The **count** is certain (**107** to an equal-or-higher level; **56** strictly upward, **51**
  lateral). **How many of the 56 are deliberate is NOT** (`UNK-C5-2`). Some carry explicit cycle-breaking
  comments; some are lazy-loading for startup cost; some may be incidental. **They were not classified, and
  they are not guessed.**
- **`providers.py` is a structural false-dead-code source** (`COUP-10`): all six backend factories are lazy
  in-function import *lambdas* dispatched from a dict. A **name-based** call graph flags all six as "zero
  callers." **All six are live.** Any future call-graph work must handle this shape.
