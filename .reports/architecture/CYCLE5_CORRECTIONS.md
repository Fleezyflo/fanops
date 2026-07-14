# FanOps — Cycle 5 corrections to Cycles 1–4

**Cycle 5 · 2026-07-14 · git HEAD `fcffa73` (unchanged)**

Authority order applied: **executable code > executed experiment > AST census > prior JSON > prior prose >
comments.** Each correction names the superseded claim by its stable ID and says why it was wrong.

Cycle 4 closed with the method note:

> *"A defect's reachability is not established by reading the code that would do it. It is established by
> proving something reaches that code. And a passing test is not evidence the property holds."*

**Cycle 5 adds the structural twin:**

> **An architectural claim about the SHAPE of the codebase is not established by reading the code. It is
> established by a census over the WHOLE tree.** Cycles 1–4 characterised the dependency structure from the
> couplings they happened to trip over. `COUP-09` recorded **nine** lazy cycle-breaking imports. **There are
> 107 lazy edges to an equal-or-higher level.** The nine were not wrong — they were *a sample presented as an
> inventory*.
>
> **And a taxonomy is an architectural claim.** A subsystem boundary drawn carelessly *manufactures* cycles
> that do not exist. Cycle 5 caught itself doing exactly that (`C5-SC-2`).
>
> **And a derived number inherits the soundness of its definition.** Cycle 5 shipped *"an 11-level DAG"* for a
> graph that **contains a cycle**, computed by a metric it had not stated precisely — and the headline count
> was wrong as a direct result (`C5-SC-3`).

---

## 🔴 REVISION — this document was corrected after first issue

**Corrections `C5-COR-06` … `C5-COR-09` and self-corrections `C5-SC-3` / `C5-SC-4` were applied to the Cycle-5
KB after review.** They are **semantic** corrections to the architectural model — not a new investigation, and
no code analysis was repeated. Two of them **retract overclaims Cycle 5 made** and one **changes a headline
number (106 → 107)**. Where the original Cycle-5 text conflicts with these, **these win.**

---

## `C5-COR-01` — the network-site census: 15, and Cycle 3's list had a hole

**Superseded claim** ([`side_effects.json`](side_effects.json) → `network_call_sites.count: 15`;
[`SIDE_EFFECT_GRAPH.md`](SIDE_EFFECT_GRAPH.md) §2, which enumerates the 15).

**The count is right. The list is not.** An AST census of literal `requests.<verb>` calls finds:

| Module | Sites |
|---|---|
| `post/postiz.py` | 152, 182, 242, 266, 392 (**5**) |
| `post/zernio.py` | 141, 155, 183, 235 (**4**) |
| `post/metrics.py` | 89, 160, 286, 513 (**4**) |
| `health_model.py` | 160 (**1**) |
| **`cli.py`** | **183 — a `recording_get` probe closure. NOT IN CYCLE 3'S LIST.** (**1**) |

That is **15 literal sites**. Cycle 3's list of 15 **omitted `cli.py:183`** and instead counted
`meta_graph.py:156,410` — which is an **injectable `get`**, not a literal `requests.*` call, and is therefore
invisible to an AST pass over `requests.*`.

**Both are real.** The honest statement is: **15 literal `requests.*` call sites, PLUS `meta_graph`'s injectable
network seam.** Neither cycle had it exactly right.

**Materiality: LOW.** `cli.py:183` is a cutover-verification probe, not a publish path. **No finding changes.**
Recorded because a census that is off by one site is a census whose method is not trusted.

---

## `C5-COR-02` — 🔴 `COUP-09` is a **sample presented as an inventory**. The real number is **107** (56 strictly upward).

> **⚠️ REVISED.** This entry originally said **106**. The corrected figure is **107**, decomposed into **56
> strictly upward + 51 lateral**. See `C5-SC-3` below for why the original number was wrong. The *substance* of
> this correction — that `COUP-09` reported a sample as an inventory — is **unchanged and strengthened.**

**Superseded claim** ([`couplings.json`](couplings.json) `COUP-09`; [`COUPLINGS.md`](COUPLINGS.md) §COUP-09):

> *"**Nine** lazy in-function imports exist solely to break module cycles … Module initialization order is
> therefore load-bearing. Hoisting any one of these to a top-level import breaks the process at start."*

**The characterisation is correct and important. The number is off by an order of magnitude.**

**AST census over all 127 modules:**

| | |
|---|---|
| In-function (lazy) `fanops → fanops` import edges | **323** |
| Of those, pointing to an **equal-or-higher level** | 🔴 **107** |
| ↳ **strictly upward** (true layering inversions) | 🔴 **56** |
| ↳ lateral (same level; **not** inversions) | 51 |
| `COUP-09`'s enumerated nine | **a subset of the 107** |

**Why it matters, and it is not pedantry.** `COUP-09` framed the lazy imports as a **deliberate, bounded,
nine-instance device**. That framing makes them sound *managed*. They are not:

- **`config`** — **level 0, fan-in 82, the most-depended-on module in the system** — reaches **up** to
  `accounts` (L2) and `meta_graph` (L3).
- `compose` (L0) → `clip` (L5). `doctor` (L3) → `pipeline_status` (L8). `init_flow` (L4) → `studio.golive` (L9).
- `health_model` alone carries **10** upward lazy imports.

**The *SCC-condensed* compile-time graph (`G1c`) is an 11-level DAG *only because* those imports are deferred.**
Count them and the **static potential-dependency graph** (`G2`) contains a **45-module SCC**, with all 19
subsystems in one — *potentially* (see `C5-COR-08`).

> **Nothing enforces this.** No test, no lint rule, no layer declaration. **Hoisting any one of the 56
> strictly-upward imports — a change that looks like a cleanup and that a reviewer would wave through — breaks
> the process at start.** `COUP-09`'s warning was right; its scope was 9/107 of the truth.

**How many of the 56 are *deliberate* is UNKNOWN** (`UNK-C5-2`). Several carry explicit cycle-breaking comments;
some are lazy-loading for startup cost; some may be incidental. **They were not classified, and they are not
guessed** — and the earlier gloss *"most were not deliberate"* is **retracted as unsupported** (`C5-SC-3`).

---

## `C5-COR-03` — the module dependency graph was **never built**, and nobody said so

**Not previously recorded as a gap.** Cycles 1–4 produced an inventory, a state machine, a mutation matrix, an
invariant audit, a coupling list, execution paths, failure semantics, a root-cause graph, and a remediation
sequence.

**None of them derived the import graph.** `COUPLINGS.md` recorded 15 *hidden* couplings — real, and valuable —
but a hidden coupling is a *finding*, not a *model*. There was no answer to *"if I change `config`, who
breaks?"* short of re-reading the tree.

**Cycle 1's completeness table reported "31 uninspected modules" and "75.6 % any-coverage"** — an honest metric
of *reading*, but it left the structural question open. **Cycle 5's whole-tree AST census closes it:**

> **Structural coverage is now 127/127 = 100 %.** Every `Ledger.transaction`, lock, network call, subprocess,
> `mkdtemp`, `rmtree`, env read and env write in `src/fanops` is enumerated.
>
> **And that buys a strictly stronger statement about the ~27 never-read modules than any prior cycle could
> make:** they contain **no ledger writer, no lock, no network call, no status client, no subprocess.** Cycles
> 1–4 could only say *"no claim rests on them."* Cycle 5 can say *"they provably contain none of the dangerous
> primitives."* **Their business logic remains unread** (`UNK-C5-3`) — that part is not fixed, and is not
> pretended away.

---

## `C5-SC-1` — 🔴 Cycle 5's **own** first network census said **39**. It was wrong.

**Self-correction, recorded rather than quietly fixed.**

My first query matched `s.get(...)` / `session.get(...)`. That also matches **plain dict `.get()`** — so it
counted `led.get()`, `info.get()`, `d.get()` as network calls, and reported **39** sites across modules with no
network code at all (`ledger`, `moments`, `prompts`, `caption`).

Filtering to literal `requests.<verb>`: **15**.

> **This is Cycle 2's method note biting the cycle that wrote it down.** *"A census is only as good as its
> query. Any claim of the form 'nothing does X' must be produced by an AST pass … never by a grep."* An AST pass
> **with a sloppy predicate is a grep with extra steps.**
>
> It is recorded because **a silently-corrected census is indistinguishable from one that was never wrong** —
> and the next reader deserves to know the failure mode is live.

---

## `C5-SC-2` — 🔴 Cycle 5's **own** first subsystem partition manufactured a **13-subsystem cycle**. Three of its edges were fake.

**Self-correction. This is the most instructive error of the cycle.**

My initial partition produced a **13-subsystem compile-time SCC** — persistence, registry, ingestion, media,
gate, selection, render, caption, crosspost, publish, metrics, learning, orchestration. **That is a spectacular
finding, and it would have been a lie.**

Before reporting it, I traced every closing edge to its **module-level** source. **Three were my own mis-drawn
boundaries:**

| Subsystem edge | The only module edge behind it | Verdict |
|---|---|---|
| `S03 persistence → S12 publish` | `paths_rebase → post.media` | ❌ **FAKE.** `paths_rebase` is a *recovery utility*, not the ledger core. **`ledger.py` never imports publish.** |
| `S06 media → S15 orchestration` | `transcribe → stage_lock` | ❌ **FAKE.** `stage_lock` is a *lock primitive* — foundation, not orchestration. |
| `S08 selection → S14 learning` (as drawn) | `moment_hook_learning → variant_learning` | ❌ **FAKE.** `moment_hook_learning` **is** a learning module; I had filed it under selection. |

I corrected the partition and re-derived. **A 7-subsystem cycle survives** — and *every* edge in it is a genuine
domain edge (`adjust → moments`, `moments → moment_hook_learning`, `stitch_render → router`, `caption →
variant_learning`).

> **The lesson, and it generalises:** **aggregating modules into subsystems MANUFACTURES cycles that do not
> exist at module level.** A subsystem-level violation must be traced to its module edges **before** it is
> asserted — otherwise the audit **fabricates the very architecture it was told not to invent.**
>
> **The brief's rule — *"never invent architecture that cannot be demonstrated"* — bites hardest on the
> taxonomy, because a taxonomy *feels* like description while it is quietly doing inference.**

---

## ~~`C5-COR-04`~~ — 🔴 **SUPERSEDED BY `C5-COR-09`. The original text below is RETRACTED.**

> **What it originally said:** *"What survives the boundary correction is a real **compile-time** strongly-connected
> component… **This is not a defect.** FanOps **is** select → render → caption → publish → measure → learn →
> select. **The import graph is telling the truth about the product.**"*
>
> **Both halves are wrong.** It is **not** a compile-time (module-level) cycle — the domain core is **acyclic**
> at module level. And *"the loop is the product, therefore the wiring is correct"* is a **non sequitur**.
>
> **See `C5-COR-09`.** The one part that survives — *the coupling is real and it is nowhere documented* — is
> carried forward there.

---

## `C5-COR-05` — `OPS-001`: **five** consecutive cycles, and it blocked Cycle 5's independent verification

**Extends** [`CYCLE4_CORRECTIONS.md`](CYCLE4_CORRECTIONS.md) `C4-COR-09`.

The orchestration gate refused, **this cycle**:

1. a compound shell command, because it **named** `.orchestration/state/` — *for a read-only `ls`*;
2. a `general-purpose` subagent spawn: *"spawn type 'general-purpose' is not allowed during a wave."*

**The second refusal has a direct cost to this cycle's evidentiary standard.** Cycle 5 attempted to spawn an
**independent verifier** whose entire brief was to **refute** the four dependency claims above — the honest way
to test a novel structural finding.

**It was refused.** Cycle 5 fell back to **cross-validation**: the same AST pass independently reproduced four
of Cycle 3's censuses exactly (subprocess **32**, `Ledger.transaction` **74**, `mkdtemp` **1**, `rmtree` **2**).

> **That is meaningful, and it is weaker than an independent agent, and it is recorded as such.** A verifier
> could have refuted; a self-consistency check can only fail to contradict.

**Cycles 1, 2, 3, 4 and 5 have all been executed single-threaded** because a stale wave marker (last touched
**2026-07-13**) has never been disengaged. **Disengage is `orchestrate.py stop` — an operator action, not a code
change.**

---

---
---

# REVISION BLOCK — corrections to **Cycle 5's own** architectural model

Applied after review. **Semantic corrections, not a new investigation.** No code analysis was repeated; the
existing AST census was re-interpreted under corrected definitions, and one derived number moved as a result.

---

## `C5-COR-06` — "compile-time DAG" → **"SCC-condensed compile-time DAG"**

**Superseded claim** (Cycle-5 `DEPENDENCY_MODEL.md` §4, `kb/dependencies.json` v1):

> *"The compile-time graph is a clean **11-level DAG** (modulo the one persona cycle)."*

🔴 **A graph containing a cycle is not a DAG.** *"Modulo the one cycle"* is hand-waving, and it concealed a real
defect in the metric — see `C5-SC-3`.

**Corrected model — three graphs, named and separated:**

| | Definition | A cycle in it means |
|---|---|---|
| **G1** compile-time import graph | module-level imports | **A hard load-order constraint.** **G1 is NOT a DAG:** it has **one** non-trivial SCC (`personas`×3). |
| **G1c** the **SCC-condensation** of G1 | each SCC collapsed to one node | **A DAG by construction.** **This — and only this — is the graph the 11 "levels" are defined on.** |
| **G2** static **potential-dependency** graph | compile ∪ lazy | see `C5-COR-08`. |

**FanOps has an 11-level layered compile-time structure *only in the G1c sense*** — and that level is an
**output** of the import structure, **not a constraint anyone enforces.**

---

## `C5-COR-07` — the subsystem count is **19**, and a subsystem is an **analytic overlay**

**Superseded:** Cycle 5 variously wrote *"18 subsystems"*, *"18 subsystems + Studio"*, and *"19 subsystems"*.
**Sloppy, and precisely the drift this audit exists to eliminate.**

**Normalized, once, everywhere:**

> **A subsystem is a partition class over the 127 modules of `src/fanops`, assigned by architectural
> responsibility. The partition is TOTAL and DISJOINT — every module belongs to exactly one.**
> **COUNT: 19. IDs: S01–S19, contiguous. S16 is Studio.**

🔴 **And the caveat that makes the number honest:**

> **Subsystems are an ANALYTIC OVERLAY imposed by Cycle 5. They are NOT declared anywhere in the repository.**
> No package boundary, no `__init__`, no config, and no test corresponds to them, and **nothing enforces them.**
>
> **The modules and the import edges are FACTS. The grouping is a MODEL.**

**This is not pedantry.** The brief forbids inventing architecture that cannot be demonstrated — and **a
taxonomy feels like description while it is quietly doing inference.** Cycle 5's first partition manufactured a
13-subsystem cycle out of three mis-filed modules (`C5-SC-2`). **Any claim made at subsystem level must be
traced to module-level edges before it is trusted.**

---

## `C5-COR-08` — the full-graph SCC is a **static potential-dependency** SCC

**Superseded claim** (Cycle-5 `kb/dependencies.json` v1, `ARCHITECTURE_MANIFEST.md` §5):

> *"Include them: a **45-module SCC**, and all 19 subsystems collapse into one. There is no subsystem you can
> change in isolation."*

**Stated flatly, that overclaims.** **G2 = compile ∪ lazy is a *static over-approximation of runtime
dependency*.** A lazy import **materializes only if its enclosing function is called.** An SCC in G2 therefore
means:

> ✅ *"These modules **could** reach each other."*
> ❌ **NOT** *"these modules **do** depend on each other at runtime."*

**G2 is a strict SUPERSET of the runtime call graph — which no cycle has derived.** So:

- The **45-module SCC** and the **all-19-subsystem SCC** are **POTENTIAL mutual reachability.**
- They **BOUND the blast radius.** They **do not ESTABLISH it.**

**Cycle 5 already said this in `DEPENDENCY_MODEL.md` §8 — and then failed to carry the qualifier into the SCC
claim itself, where it is load-bearing.** That is the same doc-drift shape the audit keeps finding: *the
caveat exists somewhere, and the headline doesn't carry it.*

**Also recorded (and it is why the qualifier matters):** `COUP-10` — `providers.py` dispatches six backend
factories via lazy in-function import **lambdas** from a dict. **A name-based call graph flags all six as "zero
callers." All six are live.** Any future G3 work must handle this shape.

---

## `C5-COR-09` — 🔴 **separate "the domain loop is intentional" from "the import cycle is intentional."** The second is **retracted**.

**Superseded claim** (Cycle-5 `C5-COR-04`, `ARCHITECTURE_MANIFEST.md` §1/§2.2, `kb/dependencies.json` v1):

> *"**This is not a defect.** FanOps **is** select → render → caption → publish → measure → learn → select. **The
> import graph is telling the truth about the product.**"*

**This ran together two claims with different truth values and different confidence. One of them is false.**

| | Claim | Verdict |
|---|---|---|
| **A** | The **DOMAIN** has a feedback loop: select → render → caption → publish → measure → learn → select. | ✅ **INTENTIONAL. It IS the product. CERTAIN.** The project's own `CLAUDE.md` documents the reach loop, the bias actuators and the validation gates. **Not inference.** |
| **B** | **Therefore** the module import graph must contain a cycle, and **this wiring is correct**. | 🔴 **DOES NOT FOLLOW.** |

### Why **B** is a non sequitur

**A feedback loop in *data / control flow* does not require a cycle in the *static import graph*.** The loop
could equally be closed:

- **by the orchestrator** — `pipeline` calls both `adjust` and `moments`, neither importing the other; or
- **via a shared abstraction** — both depend on an interface, not on each other.

**Dependency inversion exists precisely for this.** **The mutual-import wiring is a DESIGN CHOICE, not a
consequence of the loop existing.**

### And the factual half was wrong too

🔴 **At MODULE level the domain core is ACYCLIC.** Tarjan over G1 returns **125 SCCs: 124 singletons + the
`personas` triple.** `moments`, `adjust`, `caption`, `clip`, `track`, `moment_hook_learning` are **all
singletons.** **There is no module-level import cycle among them.**

The 7-subsystem cycle exists **only in the aggregated view** — it is produced by *grouping* distinct modules
into subsystem nodes. **I stated a property of my own taxonomy as if it were a property of the import graph.**

### What is left standing

| | |
|---|---|
| ✅ **REAL COUPLING.** | The seven subsystems **are** mutually dependent. You **cannot extract `S08 selection` without `S14 learning`.** Every closing edge was traced to a real module edge and verified. |
| ✅ **UNDOCUMENTED.** | No `CLAUDE.md`, codemap, or ADR acknowledges it. A future engineer **will** try to reason about selection independently of learning, and **will be wrong.** |
| 🔴 **NOT an import cycle.** | It carries **none** of the load-order fragility of one. |
| 🔴 **Intent of the wiring: UNKNOWN.** | `UNK-C5-4`. **Not guessed.** |

> **What a future engineer should take from this — and it is the opposite of what the original text told them:**
> the loop **is** the product and is not up for debate. The **mutual-import wiring is one way to build it.**
> **Inverting it is a legitimate option — a decision to be MADE, not a given to be PRESERVED.**
>
> 🔴 **"It's the product, leave it alone" is exactly the comfortable inference this audit exists to refuse.**

---

## `C5-SC-3` — 🔴 the headline count was **106**. It is **107** — and the number was hiding two things.

**Self-correction.**

The v1 level metric was a **cycle-cutting DFS** (`if m in seen: return 0`). That is **entry-order-dependent for
nodes inside an SCC** — it assigns a module's depth based on *which path reached it first*.

Recomputed against the **true SCC condensation** (well-defined for every module):

| | |
|---|---|
| Module **levels that changed** | **6** — `personas` 4→3 · `caption` 5→4 · `studio.views_review` 5→4 · `studio.views` 6→5 · `studio.actions_approve` 7→6 · `studio.app_routes_live` 7→6 |
| Lazy edges to an **equal-or-higher** level | **106 → 107** |

**And the single number was conflating two different things:**

| | |
|---|---|
| **56** | 🔴 **strictly upward** — `level(target) > level(source)`. **True layering inversions.** |
| **51** | **lateral** — same level. **NOT inversions.** (2 of them are inside the `personas` SCC.) |

**Every risk statement now cites 56**, not 107, where it means *inversions*.

> **A derived number inherits the soundness of its definition.** I shipped a headline figure computed by a
> metric I had not stated precisely — and *"modulo the one cycle"* was the tell.

**Also retracted as unsupported:** `risks.json` `AR-04` said *"the real number is 106, **and most were not
deliberate**."* **I do not know that.** The intent distribution is exactly what `UNK-C5-2` records as
**unknown.** **A number I measured, sitting next to an intent I assumed.**

---

## `C5-SC-4` — 🔴 I asserted a property of my **taxonomy** as a property of the **import graph**

**Self-correction.** Covered in full at `C5-COR-09`. Recorded separately in the self-correction series because
its *failure mode* is distinct from `C5-SC-2`'s:

- **`C5-SC-2`** — a careless boundary **manufactured a cycle** that did not exist. *(Caught before publishing.)*
- **`C5-SC-4`** — a **correct** boundary produced a **real subsystem cycle**, which I then **described using the
  vocabulary of the import graph** ("compile-time cycle") and **excused with an inference the evidence did not
  support** ("not a defect"). *(Shipped, and now retracted.)*

> **The second is more dangerous than the first**, because the underlying finding was *true* — which is exactly
> what makes the overclaim attached to it easy to wave through.

---

## Claims from Cycles 1–4 that Cycle 5 **re-verified and upholds**

Recorded so no later cycle re-litigates them. Each was **re-derived by the Cycle-5 AST census**, not merely
copied.

| Claim | Cycle-5 status |
|---|---|
| `INV-18` — `golive` is the ONLY setter of `FANOPS_LIVE` | **UPHELD** — AST: **exactly 2** `os.environ[…]` write sites in `src/` |
| `RC-10` / `C3-F4` — `compress.py:21` is the ONLY `mkdtemp`, with no `rmtree` touching it | **UPHELD** — AST: 1 `mkdtemp`, 2 `rmtree` (both in `transcribe`) |
| `RC-4` — `restore_snapshot` is the sole consumer of `ledger.lock` | **UPHELD** — AST lock census: `ledger.py:551` `_file_lock` vs `:484`/`:540` `store.lock` |
| `INV-17` — `[framing]`/cv2 is the ONLY fail-CLOSED optional dep | **UPHELD** — AST: the other `try/except ImportError` sites (whisper, flask, certifi) all fail open |
| Cycle-3's subprocess census (32) and txn census (74) | **UPHELD** — reproduced **exactly**, independently |
| `FIND-001` — `RenderState` is driverless / `Render` is never minted | **UPHELD** (5 cycles) |
| `INV-08` — no-auto-publish | **UPHELD** |
| `INV-09` — `_publish_one` is the sole network-POST caller | **UPHELD** |
| Cycle-4's root-cause set (`RC-1`…`RC-10`) and remediation sequence | **UPHELD — unchanged.** Cycle 5 designed no fixes and touched no production code. |

---

## Method notes carried forward to Cycle 6+

1. **A census must cover the WHOLE tree, or it is a sample.** `COUP-09` reported 9 of 107. The nine were real;
   the *inventory* was not. **Ask of every count: was this enumerated, or encountered?**
2. 🔴 **A taxonomy is an architectural claim, and it can fabricate findings.** Cycle 5's first subsystem
   partition manufactured a 13-subsystem cycle out of three mis-filed modules. **Trace every aggregate claim to
   its atomic edges before asserting it.**
3. 🔴 **State the DEFINITION before you report the NUMBER.** *"An 11-level DAG (modulo the one cycle)"* was
   hand-waving over a graph that **contains a cycle**, computed by an **entry-order-dependent** metric — and the
   headline count was wrong as a direct result (106 → 107), while also **conflating 56 true inversions with 51
   laterals** (`C5-SC-3`). **A derived number inherits the soundness of its definition.**
4. 🔴 **Never let a measured number sit next to an assumed intent.** `AR-04` shipped *"the real number is 106,
   **and most were not deliberate**."* The count was measured. **The intent was invented** — and `UNK-C5-2`
   already recorded it as unknown, three files away.
5. 🔴 **A TRUE finding is the easiest place to smuggle a FALSE inference.** The 7-subsystem coupling is real. The
   verdict bolted onto it — *"this is not a defect, the import graph is telling the truth about the product"* —
   **did not follow, and was wrong on the facts too** (the domain core is *acyclic* at module level). **A
   feedback loop in data flow does NOT require a cycle in the import graph.** (`C5-SC-4` / `C5-COR-09`.)
6. **An AST pass with a sloppy predicate is a grep with extra steps** (`C5-SC-1`).
7. **Record your own corrections.** A silently-fixed census is indistinguishable from one that was never wrong.
8. **The import graph is a SUPERSET of the call graph** — and that qualifier must ride on **every SCC claim**,
   not sit in an appendix (`C5-COR-08`). `providers.py` proves the converse trap: six lazy in-function import
   *lambdas* dispatched from a dict mean a **name-based call graph flags all six as "zero callers" — and all six
   are live** (`COUP-10`).
