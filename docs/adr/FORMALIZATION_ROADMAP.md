<!-- ADR Formalization Roadmap — which catalogue decisions become standalone ADR files, in what order.
     Base: origin/main @ 04c4092 (#664), 2026-07-16.
     Inputs: docs/adr/README.md (the 99-decision historical catalogue), the existing ADRs 0100-0103,
     .agents/skills/domain-modeling/ADR-FORMAT.md (the repo's own ADR test).
     This roadmap does NOT auto-generate 99 ADR files. It triages by the repo's own test and prioritizes. -->

# ADR Formalization Roadmap

**Purpose.** `docs/adr/README.md` is the **historical decision catalogue** — 99 back-filled decisions
reconstructed from evidence, each with a stable slug and a suggested number. It is deliberately *not* 99
files. This roadmap decides **which of those decisions earn a standalone `docs/adr/NNNN-slug.md`, and in
what order**, using the repository's own ADR test, and cross-references what is already formalized so
nothing is duplicated.

## The test (the repo's own, not invented here)

From `.agents/skills/domain-modeling/ADR-FORMAT.md`, a decision earns a standalone ADR only when **all
three** hold:

1. **Hard to reverse** — meaningful cost to change your mind later.
2. **Surprising without context** — a future reader looks at the code and asks "why on earth this way?".
3. **The result of a real trade-off** — genuine alternatives existed and one was chosen for reasons.

A decision that fails the test stays in the catalogue as a record; it does not get a file. "The value is
in recording *that* a decision was made and *why* — not in filling out sections."

## Already formalized — cross-reference, do NOT duplicate

| Standalone ADR | Status | Catalogue slugs it covers | Note |
|---|---|---|---|
| **0100** CI Governance Authority & Control Registry | accepted-in-principle | `GOV-CI-CONTROL-PLANE-GAP` (0099) | mechanism for the control plane |
| **0101** Required Checks & Merge-Gate Policy | accepted-in-principle | `GOV-TWO-REQUIRED-GATES` (0089), `GOV-CI-ONLY-APPROVAL` (0097) | 5-context policy; does **not** revive 0096 |
| **0102** Merge Strategy & History Policy | accepted-in-principle | *(no prior slug — filled the absent decision)* | references `GOV-MULTI-AGENT-ORCHESTRATION` (0095) |
| **0103** Reframe Subject/Layout-Aware Framing | **proposed** | *(no prior slug — implicit design made explicit)* | gated on the reframe roadmap |

**Do not re-formalize** any slug above. The dormant enforcement gate `GOV-ENFORCEMENT-GATE-DISABLED`
(0096) is **not** a formalization target — it is recorded as dormant-by-decision (Constitution §17 / R2)
and explicitly not revived by 0101/0102.

## Prerequisite — settle the numbering policy (a real decision, ~1 paragraph ADR)

There is a genuine collision to resolve *before* the first back-fill file is cut, and it is itself a
small hard-to-reverse decision (so it earns an ADR):

- `ADR-FORMAT.md` says *"scan `docs/adr/` for the highest existing number and increment"* → the next file
  would be **0104**.
- The catalogue pre-assigned **0001–0099** to the back-fill slugs (slug = the stable anchor; numbers are
  "opaque handles a maintainer may adopt or renumber").

**Recommended policy (to be ratified as `ADR-0104 — ADR back-fill numbering`):** the catalogue's
**0001–0099 are reserved formalization numbers** for those slugs — a formalized back-fill decision uses
its catalogue number (e.g. `docs/adr/0015-state-no-auto-publish.md`), preserving slug↔number stability;
**0100+ are for net-new decisions** discovered/made after the archaeology (0100–0103 already, this
numbering ADR next). This keeps ADR-FORMAT's "increment from highest" true for *new* work while honoring
the catalogue's stable anchors. Everything below is therefore keyed by **slug (catalogue №)**, not by cut
order.

## Tier 1 — formalize first (safety · data integrity · authority · irreversible)

These are the decisions where a future "cleanup" that reverses them silently breaks the system. Each
passes all three tests; each maps to an enforced Architectural Law. Cut these as standalone files first.

| Slug (№) | Decision | Why it qualifies (hard-reverse / surprising / trade-off) | Law | Supersedes / depends |
|---|---|---|---|---|
| `STATE-NO-AUTO-PUBLISH` (0015) | born `awaiting_approval`; publish iterates `queued` only | reversing it auto-publishes on a live backend; the "born un-published" default is surprising | LAW-STATE-01 | — |
| `PUBLISH-CLAIM-NETWORK-FINALIZE` (0041) | claim committed **before** the network call | the reversed whole-pass-txn design was the prior art (#89); non-obvious ordering | LAW-RECON-01 | supersedes whole-pass-txn |
| `PUBLISH-DOUBLE-PUBLISH-DEFENCE` (0047) | refuse at CLAIM (RC-1/RC-3b); heal → `needs_reconcile` | a double-publish is an irreversible platform action; the claim-time refusal is subtle | LAW-RECON-01/03 | depends 0041 |
| `PUBLISH-FAILURE-LADDER` (0042) | network-ambiguity table; AuthError halts; no-downgrade | ambiguity-as-success strands posts; the table is deeply non-obvious | LAW-RECON-02, §7 | — |
| `PERSIST-GATED-WIPE` (0013) | snapshot + typed-`REMOVE` + server-verified token; restore serializes | data-loss on the documented rollback path was RC-4/RC-5 (now fixed); irreversible | LAW-PERSIST-02 | RC-4/RC-5 (#653–655) |
| `FOUND-NO-IO-IN-LEDGER-LOCK` (0007) | no network/heavy subprocess under the ledger lock | the whole concurrency model rests on it; "looks fine to hold the lock" is the trap | LAW-PERSIST-01 | reverses whole-pass-txn |
| `RENDER-CV2-FAILCLOSED` (0034) | cv2 required — refuse loudly, never silent centre-crop | the ONE fail-closed dependency; every other extra fails open — genuinely surprising | LAW-FAIL-03 | related 0103 |
| `PROVIDER-GOLIVE-SOLE-SETTER` (0060) | `go_live` is the only `FANOPS_LIVE=1` setter, 4-step gate | flipping to live publishing is the highest-consequence action; single-source is a deliberate constraint | LAW-PROV-01 | — |
| `SELECT-SINGLE-OWNER-PICKING` (0019) | per-persona single owner (`affinities` len==1) | the anchor of the P4–P15 rebuild; reversing re-introduces "the ghost"; a large trade-off vs LLM casting | LAW-OWN-02 | supersedes `SELECT-CASTING-TEARDOWN` chain (0020) |
| `OPS-ENV-RELOAD` (0069) | per-tick `.env` reload reaches the resident daemon | a live-flip that never reaches the daemon is a silent no-op — the exact prior bug | LAW-PROV-01 (go-live realization) | depends 0068 |

## Tier 2 — architectural boundaries & provider semantics

Formalize after Tier 1. These define shape and integration; reversing them is costly but not a safety
event.

- **Persistence substrate & ladder:** `PERSIST-SINGLE-LEDGER-SUBSTRATE` (0008), `PERSIST-SQLITE-MIGRATION`
  (0009), `PERSIST-SCHEMA-MIGRATION-LADDER` (0010), `PERSIST-TXN-SCOPED-LOCK` (0011), `PERSIST-NO-WIPE`
  (0012) → LAW-PERSIST-01/03/04.
- **State model:** `STATE-PUBLISHED-REQUIRES-URL` (0016) → LAW-STATE-02; `STATE-CASCADE-PRESERVE-RETIRE`
  (0017) → §5.4.
- **Providers:** `PROVIDER-PER-CHANNEL-ROUTING` (0061), `PROVIDER-META-PER-ACCOUNT` (0065),
  `PROVIDER-R2-MEDIA` (0064), `PROVIDER-PUBLISH-VS-MEASURE` (0057) → LAW-PROV-*.
- **Reconcile & recovery:** `PUBLISH-PER-POST-SCHEDULING` (0043), `PUBLISH-RECONCILE-STRATEGY` (0044),
  `PUBLISH-IDENTITY-VERIFICATION` (0045), `PUBLISH-TERMINAL-LADDER` (0046), `OPS-LIVENESS-SINGLE-OWNER`
  (0070) → LAW-RECON-02.
- **Framing/render:** `RENDER-SMART-FRAMING` (0031), `RENDER-STATIC-CROP` (0032), `RENDER-FINGERPRINT`
  (0035) → §4 (fingerprint) — note `RENDER-STATIC-CROP` records the reverted per-frame chase (§15.4).
- **Selection:** `SELECT-ATOMIC-HOOK-INGEST` (0022), `SELECT-HASHTAG-ATTRIBUTION-SEVERANCE` (0026) →
  LAW-OWN-03; `SELECT-HOOK-RETENTION-VISION-AUTHOR` (0023).
- **Learning:** `LEARN-AUTO-VALIDATION` (0051), `LEARN-BIAS-ACTUATOR-CONTRACT` (0055) → §11/§10.4.
- **Governance (arch):** `GOV-ARCH-ENGINE` (0090), `GOV-DEADLOCK-TIMEOUT` (0093) → LAW-CI-02, LAW-SOT-*.

## Tier 3 — historical / low-risk (record in catalogue; formalize only on demand)

The remaining ~55 slugs (foundational primitives already load-bearing but unsurprising, Studio UI/IA
choices, ops niceties, superseded experiments) stay in the catalogue. Formalize one **only** if it later
becomes contested or a reader asks "why." Examples: `FOUND-CONTENT-ADDRESSED-IDS` (0001, foundational but
unsurprising), the Studio cluster `STUDIO-*` (0078–0087), `OPS-*` niceties (0071–0077),
`SELECT-CONTENT-HASHTAGS-REVERTED` (0028, superseded — kept as a reversal record per §15.4),
`STUDIO-NEXTJS-SHELVED` (0087, a recorded non-adoption).

## Supersession & dependency links (to preserve when files are cut)

- `SELECT-SINGLE-OWNER-PICKING` (0019) **supersedes** the `SELECT-CASTING-TEARDOWN` chain (0020) and
  `SELECT-OPERATOR-CASTING` (0021) is its operator surface.
- `PUBLISH-CLAIM-NETWORK-FINALIZE` (0041) and `FOUND-NO-IO-IN-LEDGER-LOCK` (0007) **supersede** the
  whole-pass-transaction design (recorded reversal, #89).
- `PERSIST-SQLITE-MIGRATION` (0009) **supersedes** the JSON store; the `FANOPS_LEDGER_BACKEND` selector
  was added then removed (a documented add-then-remove).
- `RENDER-STATIC-CROP` (0032) **supersedes** the per-frame chase (reverted #228).
- ADR-0100 **advances** 0099 (PROP → mechanism); ADR-0101 **formalizes** 0089+0097; both **do not revive**
  0096.
- `SELECT-CONTENT-HASHTAGS-REVERTED` (0028) is itself a supersession record (shipped then reverted to
  corpus-only).

## Explicit non-goals

- **No bulk generation of 99 ADR files.** The catalogue is the register; only the Tier-1 set (10) is cut
  first, then Tier 2 on demand.
- **No renumbering of 0100–0103.** They are net-new and stable.
- **No formalization of dormant/superseded decisions as "active"** — a reversal or a dormant gate is
  recorded as such (0096, 0028, the per-frame chase), never re-presented as current policy.

## Execution note

Cutting a Tier-1 ADR is a **documentation** action (a new `docs/adr/NNNN-slug.md` + a `README.md` index
row), tracked as `ADR-FORMALIZE-*` slices in `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`. The
numbering-policy ADR (`0104`) is the single prerequisite and lands first.
