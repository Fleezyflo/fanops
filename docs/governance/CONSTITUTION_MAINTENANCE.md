<!-- Constitution Maintenance — the DESIGN (no code) for automation that keeps the constitutional layer
     accurate over time. Base: origin/main @ 04c4092 (#664), 2026-07-16.
     This phase writes NO executable governance code. It specifies checks that EXTEND the existing
     governance planes (tools/arch, tools/ci + the CI control registry) — it does NOT create a second
     registry or a competing validator. Where a check overlaps an existing DC-*/ARCH-*/IMPL-* mechanism,
     it is framed as an extension of that mechanism, keyed to the same precedence order (ADR-0100). -->

# Constitution Maintenance — Automation Design

**The problem this solves.** The constitutional layer is prose. By the repository's own thesis, prose
that is not mechanically re-derived from the current tree rots. This document designs the machinery that
keeps the layer honest, so it does not become the next `anomalies.md` (a frozen snapshot that quietly
diverges from reality).

**Two hard constraints (from the CI governance program, ADR-0100).**
1. **No second registry.** `.github/ci-control-registry.yml` is the single owner of CI control rows; the
   constitution *references* control ids, never restates them. These checks add no competing store.
2. **No competing validator.** CI governance is `tools/ci`; architecture governance is `tools/arch`. The
   maintenance checks are **extensions of those planes** (and a thin, delegating `constitution-lint`),
   sharing their method (DERIVED/DECLARED + a negative control per check). They do not become a third
   governance authority.

**Every check obeys the repo's rules for validators.** Each check below (a) is a predicate a machine
evaluates, (b) ships with a **negative control** that proves it fires on an injected defect (a check that
cannot fail is decoration), and (c) states whether it **blocks** or **reports** (a reviewable diff, never
a silent auto-rewrite — ADR-0100 §Rejected).

## Check catalogue

Host = the existing plane that owns the check. `constitution-lint` = a thin new module that *delegates*
to `tools/arch`'s symbol/derived tables and `tools/ci`'s registry parse — it reads, it does not own truth.

| ID | Check | Host (extends) | Input | Fails when | Block / Report | Negative control |
|---|---|---|---|---|---|---|
| **CM-1** | Constitution schema | `constitution-lint` | `REPOSITORY_CONSTITUTION.md`, `ARCHITECTURAL_LAWS.md` | a rule/law is missing one of its required fields, or `enforcement` ∉ the allowed vocabulary | **block** | inject a rule with no `Enforcement` → must fail |
| **CM-2** | ADR index integrity | `constitution-lint` (mirrors CI `DC-5` generated-view method) | `docs/adr/*.md` frontmatter + `docs/adr/README.md` index | a `NNNN-slug.md` with no README row, a README row pointing at a missing file, or invalid frontmatter (`status`/`date`/`supersedes`/`references`) | **block** | add an ADR file with no index row → must fail |
| **CM-3** | Missing-ADR (Tier-1) | report job (like `ARCH-RECONCILE`) | `FORMALIZATION_ROADMAP.md` Tier-1 + `docs/adr/` | a Tier-1 slug has no standalone file | **report** | mark a slug Tier-1 with no file → appears in report |
| **CM-4** | Dormant-governance detection | `tools/ci` (generalize `DC-2` phantom-control) | registry controls + workflows + governance docs | a declared mechanism (control, hook, gate, convention doc) that nothing references/executes — e.g. the land-gate class, or `ADR-FORMAT.md` untracked | **report** (→ block for CI controls via DC-2) | add a registry control with no workflow job → DC-2 fires |
| **CM-5** | Superseded-decision validation | `constitution-lint` | ADR `supersedes:`/`status:` + constitution "superseded" pointers | a `supersedes:` naming a non-existent ADR, a `status: superseded` with no superseding ADR, or a constitution rule marked superseded with a dangling pointer | **block** | point `supersedes:` at a missing id → must fail |
| **CM-6** | Broken evidence-link detection | `constitution-lint` (uses `tools/arch` symbol table + registry ids) | every citation in the layer (`file`, ADR id, registry control id, test name) | a cited path/ADR/registry-id/test does not resolve | **block** (paths/ids) | cite `LAW-XXX-99` / a missing control id → must fail |
| **CM-7** | Stale symbol/line-anchor | report (uses `tools/arch` symbol index) | `file:line` anchors in the layer | the symbol named is no longer at the cited line (INV-20) | **report** (anchors are hints, per §6 philosophy) | shift a cited symbol's line → appears in report |
| **CM-8** | Cross-plane contradiction | reconciler that **delegates** to `DC-3`, arch policy, registry | constitution enforcement fields ↔ registry classification ↔ arch rule severity ↔ live BP (`DC-3`) | a rule claims `enforced` but its cited CI control is `advisory`/absent, or its ADR is only `proposed`; or a law's status disagrees with the registry/live config | **report** (→ block once stable) | flip a law to `enforced` whose control is advisory → contradiction reported |

## How the checks compose with the existing planes

- **Precedence is inherited, not redefined.** CM-8 resolves a disagreement by ADR-0100's order:
  executable source & tests > live GitHub config > accepted ADRs & registry > generated docs >
  historical prose. The constitution is plane 4/5; when it disagrees with a higher plane, **the higher
  plane wins and the constitution is the thing corrected** — CM-8 only *detects* the divergence, it never
  edits branch protection or the registry.
- **CM-4 is DC-2, widened.** DC-2 already flags a registry control with no workflow job (a phantom
  control). Dormant-governance detection is the same predicate applied to *governance documents*: a
  mechanism declared in prose with no executor. Reuse DC-2's engine; add a doc-scoped input. This is how
  the land-gate (`0096`) and an untracked `ADR-FORMAT.md` surface automatically instead of by memory.
  **CM-4 must run in BOTH directions (added 2026-07-16, `LAW-CI-09`).** As specified above it detects
  *declared-but-unexecuted*. The **inverse — executed-but-undeclared — was live and unrecorded**: the
  `.claude/settings.json` `hooks` block wires four hooks, two of which **block** (`block-hedge-on-stop.py`,
  `decide_dont_ask.py`), and no governance document named them until `LAW-CI-09`. That is this repo's
  signature defect inverted, and it is the more dangerous direction: a *declared-but-dead* mechanism
  merely misleads, whereas an *undeclared-but-live* one changes behavior with no reviewable record.
  The predicate is symmetric and cheap — enumerate executors (`settings.json` hooks, workflow jobs,
  registry controls, wired gates), enumerate declarations (`LAW-*`, registry rows, specs), and **report
  both set differences**, not just one. Two known instances seed the negative controls: `stop-completion-gate.py`
  (tracked, referenced/wired/tested nowhere → the dormant side) and the four wired hooks (→ the undeclared
  side, now declared).
- **CM-2/CM-6 reuse `DC-5`'s generated-view discipline and `tools/arch`'s symbol table.** The ADR index
  and the layer's citations are checked the same way a generated doc is byte-compared and a derived
  number is scanned — no new truth store, just new readers of the existing ones.
- **`constitution-lint` runs where the cheap arch checks run.** Like `tools/arch` (stdlib-only, no
  install), the lint is pure-Python and belongs in the fast unit lane (collected like
  `test_arch_governance.py`) or the `gate` job — never its own heavyweight workflow. It is advisory until
  it has a negative control per check, then promotable via the ADR-0101 §8 criteria.

## Block vs report (the honesty rule)

- **Block** (fail the check): CM-1 (schema), CM-2 (index integrity), CM-5 (supersession integrity), CM-6
  (dangling citations) — these are *mechanical facts* with no judgment, safe to gate.
- **Report** (reviewable diff, a human lands the fix): CM-3 (missing Tier-1 ADR — formalization is a
  judgment call), CM-4 (dormant governance — dormancy is sometimes intended, e.g. 0096), CM-7 (stale
  anchors — anchors are hints), CM-8 (contradiction — the fix may be to correct the doc *or* to change
  reality, a human decides which).
- **Never auto-fix.** A maintenance job never edits the constitution, an ADR, the registry, or branch
  protection to "make itself pass." It reports; a human lands the correction. (Mirrors `ARCH-RECONCILE`
  and ADR-0100's rejection of auto-committing reconciliation.)

## Relationship to the CI governance program (integration, not duplication)

This maintenance layer is a **consumer** of the CI governance program, sequenced *after* it:

- It **depends on** the `tools/ci` validator (`DC-1..DC-6`, #661) landing as designed — CM-4 and CM-8
  reuse `DC-2` and `DC-3` directly.
- It **adds no branch-protection intent** — the required-context set is owned by ADR-0101 + the registry;
  CM-8 only reports when the *constitution's description* of that set drifts from it.
- It **shares the negative-control method** with `tools/arch`/`tools/ci` but stays a separate, thin
  module (per ADR-0100's "share method, not ownership" — the same boundary that keeps `tools/arch` and
  `tools/ci` distinct now keeps `constitution-lint` distinct from both).

## Explicit non-goals (this phase)

- **No executable code** is written here — this is the specification only.
- **No second registry, no competing validator** (the two hard constraints above).
- **No new required check** is proposed for branch protection; promotion of any CM check follows the
  ADR-0101 §8 criteria later.
- The build order and the per-check slices live in
  `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md` (`SLICE-CONSTLINT`, `SLICE-CM-CONTRADICTION`);
  they are gated on the CI program's `DC-*` landing first.
