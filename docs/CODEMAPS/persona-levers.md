# Codemap — persona levers: what each one is, what it does, and where it bites

The persona is the operator's control surface over per-account output. Each **lever** is a validated field on
`Persona` ([src/fanops/personas.py](../../src/fanops/personas.py)) that hydrates onto a linked `Account`
(`accounts._hydrate_from_personas`) and drives one downstream stage. This map is the source of truth for
"what does 'bold' / 'tasteful' equal in the end result." The Studio **Personas** tab exposes it live — see
`lever_catalog()` (the option→effect catalog) and `compose_breakdown()` (the live composed translation).

## The levers → stage → effect → code site

| Lever | Stage | What it does | Effect string lives in | Compiler / resolver |
|---|---|---|---|---|
| `content_focus` (multi) | casting | which moment KINDS to clip for | `_FOCUS_CLAUSE` (personas.py) | `casting_directive()` injects into the casting prompt |
| `energy` | casting | bias toward calm vs peak-intensity (`low`/`high`) | `_ENERGY_CLAUSE` | `casting_directive()` |
| `hook_angle` | hook | the on-screen hook's strategy | `_ANGLE_CLAUSE` | `hook_directive()` injects into the hook prompt |
| `hook_tone` | hook | the on-screen hook's voice | `_TONE_CLAUSE` | `hook_directive()` |
| `clip_profile` | cut | the deterministic cut-length band | `bands._PROFILES` / `band_for()` | `config.resolve_clip_profile` → `clip.fit_window` |
| `framing` | cut | the deterministic vertical crop | `config.FRAMING_NAMES` | `config.resolve_top_bias` → `clip.reframe_filter` |
| `tag_lean` | caption | floats a flavor pool to the front of the hashtags | `hashtags._LEANS` | `hashtags.vet_hashtags(lean=)` (deterministic, not in the prompt) |
| `hashtag_corpus` (list) | caption | curated tags that LEAD the hashtags | the persona's own pool | `vet_hashtags(corpus=)` (corpus-first) |
| `voice` / `brief` | all | the freeform base of every directive | the persona itself | `_base_voice()` → all three compilers |
| `casting_directive` / `hook_directive` / `caption_directive` (overrides) | per-dimension | replace the compiled directive VERBATIM; **SHADOW** the structured levers feeding that dimension | the persona itself | the override branch of each compiler |

## The firewall

A persona with no levers and no override compiles to the **bare voice** (`_join`/`_base_voice`), so every
existing persona's casting/hook/caption payload is byte-identical. Asserted across
[tests/test_persona_levers.py](../../tests/test_persona_levers.py).

## The exposure surface (Studio Personas tab)

| Function | File | What it provides |
|---|---|---|
| `lever_catalog()` | personas.py | the code-derived catalog: every lever + each option's **engine-true effect** (read from the clause maps / `band_for` / `_LEANS`) |
| `compose_breakdown(cfg, p)` | personas.py | the **live composed translation**: the exact 3 directives + cut + lead tags, each fragment traced to its lever, with override-shadow + no-op flags. `text` is the compiler's own output (parity — no drift) |
| `preview_compose(cfg, form)` | studio/personas.py | runs `compose_breakdown` on a **transient** persona from the unsaved editor form — never persists; merges an existing persona's curated corpus by `id` |
| `POST /personas/compose` | studio/app.py | htmx route that re-renders `_persona_compose.html` on every lever change |
| `_LEVERS` / `_LEVER_EFFECTS` / `_LEVER_REF` | studio/app.py | all derived from `lever_catalog()` — the macro option lists, the per-option effects, and the reference catalog |

**Parity guarantee:** what the operator reads is what the pipeline runs.
`compose_breakdown(...)[dim].text == <dim>_directive(p)`; each option effect `== ` the engine clause;
`clip_profile` effect `== band_for(...)`; `tag_lean` effect lists `_LEANS[...]`. Enforced by
[tests/test_persona_lever_exposure.py](../../tests/test_persona_lever_exposure.py).
