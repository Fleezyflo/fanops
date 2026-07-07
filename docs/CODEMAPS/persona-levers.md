# Codemap — persona levers: what each one is, what it does, and where it bites

The persona is the operator's control surface over per-account output. Each **lever** is a validated field on
`Persona` ([src/fanops/personas.py](../../src/fanops/personas.py)) that hydrates onto a linked `Account`
(`accounts._hydrate_from_personas`) and drives one downstream stage. This map is the source of truth for
"what does 'bold' / 'tasteful' equal in the end result." The Studio **Personas** tab exposes it live — see
`lever_catalog()` and `compose_breakdown()`.

## The levers → stage → effect → code site

| Lever | Stage | What it does | Compiler / resolver |
|---|---|---|---|
| `content_focus` (multi) | moments pick | which moment KINDS to favor + DERIVES cut length/framing | `_FOCUS_CLAUSE` → pick lenses (`persona_directives`); `_FOCUS_PROFILE` → `derive_cut_spec` → owner `clip_profile` at pick |
| `selection_scope` | moments pick | selection CONSTRAINT (open / subject_locked / credibility_first / …) | `_SCOPE_CLAUSE` → `_pick_personas` in `request_moments` |
| `hook_angle` | hook | on-screen hook strategy | `_ANGLE_CLAUSE` → `hook_directive()` → owner-only hook gate (`moments._hook_personas_for_moment`) |
| `hashtag_corpus` (list) | caption | curated tags that LEAD the hashtags | `vet_hashtags(corpus=)` (deterministic) |
| `voice` | all | freeform register base | `_base_voice()` → hook/caption compilers |
| `clip_profile` (catalog) | cut (global) | Go-Live default band — NOT a per-persona pin | `config.clip_profile` / `bands.band_for` |

**Retired (M3/MOL-170):** per-persona `clip_profile`/`framing` pins, `energy`, `tag_lean`, freeform `*_directive` persona-field overrides, and the LLM **casting** stage (`casting_directive` compiler removed P11). `casting_directive()` as a **function** may still appear in historical docs — the live pick path uses `selection_directive` / per-persona pick lenses.

## The firewall

A persona with no levers compiles to the **bare voice** (`_join`/`_base_voice`), so every existing persona's
pick/hook/caption payload is byte-identical when unlinked. Asserted in `tests/test_persona_levers.py`.

## The exposure surface (Studio Personas tab)

| Function | File | What it provides |
|---|---|---|
| `lever_catalog()` | personas.py | code-derived catalog: every lever + option effects |
| `compose_breakdown(cfg, p)` | persona_directives.py | live composed translation + provenance fragments |
| `preview_compose(cfg, form)` | studio/personas.py | transient unsaved-form preview |
| `POST /personas/compose` | studio/app_routes_personas.py | htmx re-render on lever change |

**Parity guarantee:** `compose_breakdown(...)[dim].text == <dim>_directive(p)` for hook/caption; pick lenses use the same registry. Enforced by `tests/test_persona_lever_exposure.py`, `tests/test_archetype_differentiation.py`.
