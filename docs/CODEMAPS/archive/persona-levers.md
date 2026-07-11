# Codemap — persona levers: what each one is, what it does, and where it bites

The persona is the operator's control surface over per-account output. Each **lever** is a validated field on
`Persona` ([src/fanops/personas.py](../../src/fanops/personas.py)) that hydrates onto a linked `Account`
(`accounts._hydrate_from_personas`) and drives one downstream stage. This map is the source of truth for
"what does 'bold' / 'tasteful' equal in the end result." The Studio **Personas** tab exposes it live — see
`lever_catalog()` (the option→effect catalog) and `compose_breakdown()` (the live composed translation).

> **Post-P15 / P11 note:** persona levers compile into the **moment-pick** and **hook** gates (single-owner
> `Moment.affinities` stamped at pick). There is no separate LLM casting stage. Retired persona fields
> (`tag_lean`, per-persona `clip_profile`/`framing` pins, freeform directive overrides) were removed in M3.

## The levers → stage → effect → code site

| Lever | Stage | What it does | Effect string lives in | Compiler / resolver |
|---|---|---|---|---|
| `content_focus` (multi) | moment pick | which moment KINDS to favor + derived cut length/framing | `_FOCUS_CLAUSE` | `casting_directive()` → `moments._pick_personas` pick lenses |
| `selection_scope` | moment pick | credibility vs controversy lens on WHICH windows to pick | `_SCOPE_CLAUSE` | `casting_directive()` → pick payload `scope_lens` |
| `hook_angle` | hook | the on-screen hook's strategy | `_ANGLE_CLAUSE` | `hook_directive()` → owner-only `moment_hooks` gate |
| `hashtag_corpus` (list) | caption | curated tags that LEAD the hashtags | the persona's own pool | `vet_hashtags(corpus=)` (corpus-first) |
| `voice` | all | the freeform base of every directive | the persona itself | `_base_voice()` → compilers |

Cut length and framing are **derived** from `content_focus` at pick time (`derive_cut_spec` /
`resolved_cut_spec` → stamped on `Moment.clip_profile`/`framing` → `render_account_cut`).

## The firewall

A persona with no levers compiles to the **bare voice** (`_join`/`_base_voice`), so every existing persona's
pick/hook/caption payload stays byte-identical when levers are empty. Asserted across
[tests/test_persona_levers.py](../../tests/test_persona_levers.py).

## The exposure surface (Studio Personas tab)

| Function | File | What it provides |
|---|---|---|
| `lever_catalog()` | personas.py | the code-derived catalog: every lever + each option's **engine-true effect** |
| `compose_breakdown(cfg, p)` | personas.py | the **live composed translation**: directives + cut + lead tags, each fragment traced to its lever |
| `preview_compose(cfg, form)` | studio/personas.py | runs `compose_breakdown` on a **transient** persona from the unsaved editor form |
| `POST /personas/compose` | studio/app.py | htmx route that re-renders `_persona_compose.html` on every lever change |

**Parity guarantee:** what the operator reads is what the pipeline runs.
`compose_breakdown(...)[dim].text == <dim>_directive(p)`; enforced by
[tests/test_persona_lever_exposure.py](../../tests/test_persona_lever_exposure.py).
