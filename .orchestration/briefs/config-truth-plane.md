# Brief — Unify the config truth plane

**Root issue #1 in remediation order** (foundation before health contracts, escalation policy, or observability).

**Principle:** one declaration owns every env key’s meaning (type, default, studio/bootstrap class, parsers). Runtime and doctor are two *policies* over that plane (lenient vs strict) — not two hand-copied worlds. Do **not** collapse doctor-strict into runtime-lenient; that dual *policy* is intentional. Unify *registration + parsers + façade ownership*.

**Grounded on:** `origin/main` post-#961. Symbols primary (STD-DOC-01).

---

## Three lenses

| Lens | Answer |
|---|---|
| Best practice | Single registry; shared parsers; `Config` stays the cheap runtime façade (~92 importers, no pydantic tax); Settings/doctor = strict evaluation of the same declarations |
| Root cause | Docstring claimed Settings was “built per Config()”; wiring never existed. Product runs on live `os.getenv` via `Config` (+ scattered module getenv). Doctor/`fanops config` run on `Settings`. Dual maintenance + incomplete registration = silent drift |
| Leanest | Collapse declaration into one place; route scrape/bootstrap keys through it; demote vestigial knobs; keep fail-open runtime + fail-loud doctor tests green |

---

## KEEP — not in scope for this brief

Do **not** start these here (later roots in the remediation stack):

- Fail-open escalation policy (degrade vs terminate vs non-zero)
- Derived-signal primitives (mtime age, strip TTL, fail-open-to-LIVE)
- Single machine-health / observability contract
- Control-plane vs data-plane untangle
- Swallow-ratchet “honesty vs call-name” retarget

Do **not** change product policy:

- Runtime fail-open on poster typo → dryrun+warn (doctor still fails)
- Runtime fail-open on bad scrape delay → default pacing (doctor still fails)
- `go_live` as sole writer of `FANOPS_LIVE`
- No auto-publish / Review approval

Do **not** invert dependencies:

- `Config` must **not** import `Settings` / pydantic
- Settings may keep importing parse helpers from `config` (or a future tiny leaf)

---

## Current wiring (the mess, named)

```
                    .env / os.environ
                           |
          +----------------+----------------+
          |                                 |
   Config (runtime)                  Settings (doctor / fanops config)
   live getenv per property          EnvVar registry + strict_validate
   ~92 importers                     doctor._env_settings_check only
          |                                 |
   product behavior                  table + exit non-zero on typo
          |
   ALSO: ig_hashtag_scrape / fanops_hashtags / daemon
         read some keys via raw getenv (bypass Config façade)
```

There is **no handoff**. Precedence is not defined because the planes never meet at runtime.

False folklore still in tree:

- Settings docstring / old arch: “built fresh per Config()” — **false**
- Arch refs to `Settings.runtime_load` — **method gone**, zero callers

---

## Defect classes this brief owns

### D1 — Dual declaration / dual parsers (same key, two owners)

| Key / concept | Settings | Runtime |
|---|---|---|
| `responder_mode` | `Settings.responder_mode` + field validators | `Config.responder_mode` (mirrored copy) |
| `_VALID_BACKENDS` | `settings._VALID_BACKENDS` | `config._VALID_BACKENDS` (accounts vs Config can drift) |
| Bool vocabulary | uses `config.bool_word` / `env_bool` | same helpers (good) — pattern to extend |
| Scrape delay/caps | fields + `strict_validate` / field validators | `ig_hashtag_scrape._scrape_delay_range`, `fanops_hashtags._scrape_*` via getenv + shared parsers — **no Config property** |

### D2 — Registration holes (runtime exists; `fanops config` / Settings blind)

| Key | Read site | Gap |
|---|---|---|
| `FANOPS_ROOT` | `Config.__init__` | bootstrap-only; ok as infra class but must be *registered* as such |
| `FANOPS_POSTIZ_ONDEMAND` | `daemon` / doctor note | prose in CONFIG.md; not Settings |
| `FANOPS_STUDIO_GENERATION` | daemon write / `studio.app` read | process-only; must not look operator-tunable |
| `FANOPS_IG_SCRAPE_PASSWORD_<SLUG>` | `ig_hashtag_scrape.scrape_password_for` | dynamic keys; census hole |
| `FANOPS_AUTO_ADOPT` | Settings BoolEnv **and** `daemon.ensure` via `env_bool(getenv)` | registered but **no** `cfg.auto_adopt` — façade bypass |

### D3 — Vestigial / lying surface

| Item | Lie |
|---|---|
| `FANOPS_RESPONDER: StudioStr` | Introspect STUDIO=yes; no Go-Live writer; only legal value llm/unset; CONFIG.md says `.env` |
| `ANTHROPIC_API_KEY` | Marked VESTIGIAL; still a Settings field + CONFIG row — invites dead setup |
| Module docstring leftovers | e.g. `postiz_lifecycle` still narrating `!= "0"` while code uses `env_bool` |

### D4 — Façade bypass (Layer A / daemon)

Any reader that calls `os.getenv` for a registered product knob instead of `cfg.<property>` re-creates dual ownership. Wave 3 shared *parsers*; it did not finish *façade*.

---

## Target end-state

1. **One registration plane** — extend today’s `EnvVar` + `Settings.model_fields` (or extract a leaf `envparse` both import). Every key declares: type, default policy, studio/operator/bootstrap/secret/deprecated class.
2. **Two evaluation policies, one code path** — `policy=strict` (doctor, `fanops config`) vs `policy=lenient` (runtime). Same parsers (`parse_scrape_delay`, `parse_scrape_cap`, `env_bool`, enums).
3. **`Config` remains the runtime façade** — thin properties over the shared reader; still live `os.environ` re-read (Studio `_dual_write` depends on this). No Settings instance cached on Config.
4. **Scrape knobs** — `Config` properties (or one `cfg.hashtag_scrape` bundle); Layer A calls `cfg`, not getenv.
5. **`FANOPS_AUTO_ADOPT`** — `cfg.auto_adopt`; daemon uses it.
6. **Single `_VALID_BACKENDS`** — one leaf constant both boundaries import.
7. **Single `responder_mode` rule** — one function both boundaries call (or Config-only + Settings delegates).
8. **Infra keys** registered as bootstrap/process class so doctor/introspect can see them without pretending they are `.env` Settings table rows.
9. **Demote vestigials** — `FANOPS_RESPONDER` / `ANTHROPIC_API_KEY`: remove StudioStr / operator discoverability; mark `deprecated` in registry; docs match. Prefer delete from Studio mental model entirely.
10. **Docs / arch** — kill “Settings per Config()” and `runtime_load` ghosts; CONFIG.md Set column matches registry.

---

## Explicit non-goals (do not “fix” by accident)

| Anti-goal | Why |
|---|---|
| Make runtime raise on every typo | Breaks autonomy; locked by fail-open tests |
| Make doctor lenient | Loses the only loud boundary |
| Move product to construct Settings every tick | pydantic + stale dual-write risk |
| Edit `lanes.json` to steal `config.py` | Stop and report; publish lane owns it |
| Bundle roots #2–#6 into this PR | Sequencing; this brief is plane 1 only |

---

## Work packages (ordered inside this brief)

### WP0 — Inventory freeze (read-only)

Census: Settings fields ∪ Config getenv ∪ raw getenv outside Config. Classify each key: façade / bypass / settings-only / infra / vestigial. Output a table in the PR; no behavior change.

### WP1 — Leaf ownership

- One `_VALID_BACKENDS`
- One `responder_mode` resolution helper used by Config + Settings
- Confirm scrape parsers already single; no second copy

### WP2 — Façade completion

- Add `Config` properties for scrape delay/try_cap/cotag/parallel; retarget `ig_hashtag_scrape` / `fanops_hashtags`
- Add `Config.auto_adopt`; retarget `daemon.ensure`
- Kill duplicate `FANOPS_LLM_MODEL` raw read in `llm.py` if still present (use `cfg`)

### WP3 — Registry honesty

- Register infra keys (`FANOPS_ROOT`, `FANOPS_POSTIZ_ONDEMAND`, `FANOPS_STUDIO_GENERATION`) with correct class
- Demote `FANOPS_RESPONDER` / `ANTHROPIC_API_KEY` (deprecated / non-studio)
- Fix `config_introspect` + CONFIG.md + `test_config_doc_matches_settings`

### WP4 — Folklore purge

- Settings module docstring truth
- Arch INV / reports that cite `runtime_load` or “per Config()” — regen or patch prose
- Stale `!= "0"` comments

### WP5 — Gate

- `./scripts/check.sh`
- Keep dual-policy tests green; add tests that bypasses are gone (Layer A / daemon go through `cfg`)
- `python -m tools.arch regen` if scanned lines shift
- Publish-lane branch for any `config.py` edit

---

## Acceptance

- [ ] No product knob has two independent parsers or two `_VALID_*` sets
- [ ] No Layer A / daemon product flag reads getenv when a `Config` property exists
- [ ] `fanops config` / Settings registry class matches how the key is actually used (studio / .env / bootstrap / deprecated)
- [ ] Doctor strict + runtime lenient still both true for poster typo and scrape delay (existing tests)
- [ ] Vestigial AI-key / responder “switch” no longer present as Studio-settable lies
- [ ] Docs/arch no longer claim Settings is constructed per Config or `runtime_load`
- [ ] `config.py` changes land on a **publish/** branch (lane hot file)

---

## Tests that encode today’s dual policy (must stay green unless product changes)

| Test | Pins |
|---|---|
| `test_runtime_config_failopen_on_poster_typo_while_doctor_fails` | Doctor fail + Config dryrun |
| `test_scrape_delay_bad_raises_at_settings_validate_but_runtime_fails_open` | Shared parser, dual policy |
| `test_scrape_try_cap_noninteger_raises_at_settings_but_runtime_fails_open` | Same |
| `test_auto_adopt_is_registered_boolenv` | BoolEnv + env_bool |
| `test_config_and_settings_share_one_boolean_vocabulary` | Shared bool_word |
| `test_config_doc_matches_settings` | Doc ↔ Settings fields |
| Responder hard-refuse tests on both planes | Dual copies today — become one helper |

Add (this brief):

- Layer A reads scrape knobs only via Config
- Daemon reads auto_adopt only via Config
- Introspect marks responder/anthropic as non-studio / deprecated as designed

---

## Lane / blast radius

| Path | Constraint |
|---|---|
| `src/fanops/config.py` | **publish** hot (`.agents/lanes.json`) |
| `src/fanops/settings.py` | not hot |
| `src/fanops/config_introspect.py` | not hot |
| `ig_hashtag_scrape` / `fanops_hashtags` / `daemon` | façade retargets; coordinate if other lanes open |

Fan-in: ~92 `Config` importers — keep API stable (`cfg.foo` properties). Prefer add properties over rename.

---

## Risks

| Risk | Mitigation |
|---|---|
| Caching Settings on Config | Don’t; keep live environ re-read |
| “Unify” misread as one policy | Brief non-goals; leave fail-open tests |
| Publish vs scrape lane collision | Façade PRs small; stop if hot files collide |
| Secrets enrichment only on Settings path | Don’t merge keyring paths carelessly; runtime stays `resolve_secret` / Config |
| Arch S02 “do not unify” | Reframe: unify registration, keep dual policy — update contract prose if it still forbids the wrong thing |

---

## Out of order / later briefs (pointers only)

After this lands: **escalation policy** → **derived-signal primitives** → **health contract** → **plane untangle** → **honesty ratchets**.

---

## One-sentence objective

**Make every env key mean one thing in one registry, evaluate it with one parser under two explicit policies, and make `Config` the only runtime door — so doctor and the machine cannot silently disagree about what is configured.**
