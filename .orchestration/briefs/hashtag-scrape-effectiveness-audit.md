# Agent brief — Hashtag scrape effectiveness & wiring audit

**Audience:** an auditor agent (read-only preferred; no `pytest` locally; no `launchctl`; no ledger wipe).  
**Code tip this brief was grounded on:** `origin/main` @ `779527cd` (Fleezyflo/fanops). Re-find symbols; do not trust pasted line numbers.  
**Live data root:** `FANOPS_ROOT=/Users/molhamhomsi/FanOps` → Config appends `MohFlow-FanOps`. Measurement files live under `…/MohFlow-FanOps/00_control/`, not in the git tree.  
**Related tickets:** MOL-793 (live outage / operator `scrape-login`), MOL-742 (Studio cooldown reason — **unbuilt**, blocked on 793), MOL-794 (outage log severity — shipped).

---

## Mission

Audit **effectiveness, wiring, and code fidelity** of FanOps hashtag scraping end-to-end:

1. Does Layer A still measure on the live box, or is it frozen (cooldown / dead session)?
2. Does the code path match the documented two-layer model (measure → derive → select)?
3. Are corpora, captions, Studio, and logs wired to the same truth?
4. What would improve effectiveness without expanding scope past existing contracts?

**Out of scope unless the operator expands:** implementing MOL-742; running `scrape-login` (operator-only); Meta Graph hashtag fetch (deferred by design); changing lane ownership / dual-owns.

---

## Mental model (non-negotiable)

| Layer | Job | Network? | Writer of |
|-------|-----|----------|-----------|
| **A — measure** | Refresh `00_control/hashtags.json` | Yes — instagrapi only | `fanops.fanops_hashtags` (**sole** writer of the cache) |
| **B — derive** | Per-persona `hashtag_corpus` from the cache | **Zero** | `persona_research.derive_corpus` → `persona_store.apply_auto_corpus` → `personas.json` |
| **Select** | ≤4 tags on a caption | No (uses store + corpus) | `hashtags.vet_hashtags_traced` via `caption` |

Meta Graph hashtag helpers may linger in `meta_graph`; **refresh does not call them**. Missing scrape aborts loudly — no silent Graph fallback.

---

## Ordered call graph

### Unattended tick (`fanops run` / launchd `com.fanops.run`)

```
cli._cmd_run_pass
  ├─ hashtag_vocab.expand_vocab_if_due     # prompt vocab only — NOT Layer A search roots (MOL-719)
  ├─ fanops_hashtags.refresh_store_if_due  # LAYER A
  │    ├─ scrape_configured? (user + session|password)
  │    ├─ _read_active_cooldown → skip (never sleep) + scrape_outage log
  │    ├─ _complete_pass_age_s vs 12h (gates on last_complete_pass, NOT mtime)
  │    └─ refresh_store → _pass_lease (flock) → _refresh_pass
  │         ├─ _posting_personas → niche-only persona_terms
  │         ├─ open_client(allow_reauth=False)  # never login() unattended
  │         ├─ resolve_hashtag_scrape / measure_and_harvest_scrape
  │         ├─ write_json_atomic(hashtags.json)  # mid-pass + final
  │         ├─ _persist_cooldown / _clear_cooldown
  │         └─ _rederive_posting_corpora → derive_corpus  # LAYER B once/pass
  ├─ fanops_account_stats.refresh_account_stats_if_due
  └─ persona_research.refresh_corpora_if_due   # LAYER B safety net (fingerprint)
```

### Operator CLI

| Verb | Symbol | Effect |
|------|--------|--------|
| `fanops hashtags refresh` | `cmd_hashtags_refresh` → `refresh_store` | Force Layer A pass; exit 2 on abort |
| `fanops hashtags scrape-login` | `cmd_hashtags_scrape_login` | **Only** `allow_reauth=True`; ignores cooldown; clears cooldown on success |
| `fanops hashtags discover` | `cmd_hashtags_discover` | Read-only derived report; **zero network** |

### Caption / Studio / doctor

- Captions: `caption.request_captions` → pools + `vet_hashtags_traced` (corpus lead ≤3 of 4).
- Studio: `GET /hashtags` → `views_hashtags.hashtags_page` (corpora / store / rotation) — **does not surface cooldown reason** today.
- Doctor: `doctor._hashtag_scrape_check` → `open_client` + probe resolve.

---

## Key symbols (re-find these)

| Concern | Module | Symbols |
|---------|--------|---------|
| Layer A | `src/fanops/fanops_hashtags.py` | `refresh_store`, `_refresh_pass`, `refresh_store_if_due`, `_pass_lease`, `_rederive_posting_corpora`, cooldown helpers, CLI cmds |
| Network | `src/fanops/ig_hashtag_scrape.py` | `open_client`, `scrape_configured`, `resolve_hashtag_scrape`, `measure_and_harvest_scrape`, exception types + classifiers |
| Store / select | `src/fanops/hashtags.py` | `load_measurements`, `vet_hashtags_traced`, `content_tag_candidates`, `RECORD_*`, `TOP_SAMPLE_N` |
| Layer B | `src/fanops/persona_research.py` | `derive_corpus`, `refresh_corpora_if_due`, `persona_terms`, `_aligned_pool`, `_is_candidate` |
| Persist corpus | `src/fanops/persona_store.py` | `apply_auto_corpus` |
| Config | `src/fanops/config.py` | `hashtags_path`, `ig_scrape_session_path`, `ig_scrape_user`, `ig_scrape_password`, `corpus_target` |
| Studio | `src/fanops/studio/views_hashtags.py` | `hashtags_page`, `_store_status`, `_corpora_rows`, `rotation_health` |
| Docs | `docs/CODEMAPS/hashtag-lifecycle.md`, `docs/CONFIG.md` | lifecycle + env table |

---

## Cooldown state machine

**Path:** `00_control/.hashtag_scrape_cooldown.json`  
**Schema:** `{streak, until, updated_at, reason}` — additive; missing `reason` still gates on `until`.

| Reason | Delay | Operator remedy (`_OUTAGE_REMEDY`) |
|--------|-------|-------------------------------------|
| `throttle` | ladder 30m→1h→2h→6h by streak | none (wait) |
| `login_required` | same ladder | `fanops hashtags scrape-login` |
| `checkpoint` | flat **12h** | verify in IG app, then `scrape-login` |

**Invariants to verify in code + tests:**

- Progress ≠ stop-signal (MOL-727): measuring tags can clear cooldown mid-pass, but same-pass throttle/login_required re-arms; partial pass must not reopen scrape next tick.
- `last_complete_pass` advances **only** when `throttled is False`.
- Unattended path never `login()` (`allow_reauth=False`); only scrape-login may reauth.
- Severity escalates on sustained skip (MOL-794): `scrape_outage` info→warning→error — does not fade to quiet forever.
- `refresh_store_if_due` checks cooldown **before** `open_client` and never sleeps the daemon.

---

## Measurement store & discovery rules

- **Cache:** `00_control/hashtags.json` — whole-file rewrite; sibling key `last_complete_pass`.
- **Cadence:** 12h on `last_complete_pass`, not mtime (`_REFRESH_CADENCE_S`).
- **Due tiers:** never-measured anchors → volume backfill → corpus members >24h → rest >7d; retention 90d.
- **Roots:** `persona_terms` = declared `Persona.niche` **only** (MOL-719). Empty niche → abort `discovery_skip_no_niche`. LLM vocab (`hashtag_vocab`) is **not** a search root.
- **Co-tags:** harvested from Top-grid captions; novel tags enqueued (cap); `from` edges are **inbound-only** (niche on a measured tag’s Top) — MOL-643.
- **Relatedness floors:** `MIN_MEDIA_FLOOR`, `CATEGORY_MEDIA_FLOOR` in persona_research.
- **Corpora:** live in `personas.json` (`hashtag_corpus` + meta), not in the measurement cache.
- **Cold/outage derive:** must not empty an existing corpus on a failed Layer A pass.

---

## Env knobs (effectiveness levers)

| Env | Default | Role |
|-----|---------|------|
| `FANOPS_IG_SCRAPE_USER` / `FANOPS_IG_SCRAPE_PASSWORD` | unset | Layer A credentials |
| `FANOPS_CORPUS_TARGET` | 80 | derive ceiling (never pads) |
| `FANOPS_HASHTAG_SCRAPE_TRY_CAP` | 400 | max measure attempts/pass |
| `FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE` | 40 | new co-tags/pass |
| `FANOPS_HASHTAG_SCRAPE_PARALLEL` | 1 | wave size; **still single-client on the wire** |
| `FANOPS_HASHTAG_SCRAPE_DELAY` | `1,3` | instagrapi delay_range |

Not env-tunable (constants): 12h cadence, cooldown ladder, checkpoint 12h, media floors, `TOP_SAMPLE_N`, corpus lead max.

---

## Live outage path (MOL-793) — effectiveness killer

**Mechanism:** stale `ig_scrape_session.json` → `account_info` raises login_required → unattended `open_client` refuses re-login → `ScrapeSessionExpired` → `_persist_cooldown(reason="login_required")` → every tick `store_refresh_skipped` / `scrape_outage` → cache + corpora freeze on last good measure.

**Only clear:** operator `fanops hashtags scrape-login` (ignores cooldown; `_clear_cooldown` on success). Daemon **must not** self-heal via password login (by design — re-login pressure deepens IG locks).

**Auditor live checks (read-only):**

```bash
export FANOPS_ROOT=/Users/molhamhomsi/FanOps
# Inspect (do not delete):
#   MohFlow-FanOps/00_control/.hashtag_scrape_cooldown.json
#   MohFlow-FanOps/00_control/hashtags.json  → last_complete_pass
#   MohFlow-FanOps/00_control/ig_scrape_session.json  (presence only; do not print secrets)
# Tail: MohFlow-FanOps/07_reports/run.log for store_refresh_skipped / scrape_outage / scrape_login_*
```

Compare `last_complete_pass` age to 12h. If `reason=login_required` and streak high → effectiveness is **session-blocked**, not algorithm-blocked.

---

## Studio / MOL-742 gap

`rg cooldown src/fanops/studio/` should be **0 hits**. Typed cooldown lives on disk + logs; Studio hashtag page shows store mtime/age and corpus sizes but **not** cooldown `reason`. Any “why is corpus short?” UI is incomplete until MOL-742 (mirror typed reason only — operator ruling 2026-08-08; no new stored field; no `config.py` edit).

---

## Contract tests (CI-only — do not run locally)

Primary: `tests/test_fanops_hashtags.py` (cooldown ladder, checkpoint, login_required, MOL-727 partial progress, MOL-794 severity, scrape-login clear, lease, single-client, corrupt personas).  
Also: `test_ig_hashtag_scrape.py`, `test_hashtags.py`, `test_hashtag_attribution_severance.py`, `test_hashtag_page.py`, `test_hashtag_lifecycle_e2e.py`, fakes in `tests/hashtag_scrape_fakes.py`.

---

## Suggested audit checklist (ordered)

1. **Live freeze?** Cooldown file + `last_complete_pass` + recent `scrape_outage` lines.
2. **Credentials/session configured?** `scrape_configured` / session file present (no password echo).
3. **Code path fidelity:** sole writer of `hashtags.json`; niche-only roots; no Graph fallback; unattended no-reauth.
4. **Derive wiring:** Layer A end-of-pass rederive + `refresh_corpora_if_due` fingerprint; corpus not emptied on outage.
5. **Selection wiring:** caption path uses corpus + measurements; empty when cold (no pad).
6. **Studio honesty:** observatory matches store; confirm cooldown reason absent (expected until 742).
7. **Effectiveness metrics (once unfrozen):** tags measured/pass, try_cap saturation, cotag enqueue vs novel rate, corpus sizes vs `corpus_target`, `last_complete_pass` advancing each ~12h, throttle frequency.
8. **Doc drift:** `docs/CODEMAPS/hashtag-lifecycle.md` vs symbols above.

---

## Gaps / confirm with operator

1. Has `scrape-login` been run since the MOL-793 report? (Repo cannot answer.)
2. Does MOL-742 ruling still hold (mirror typed `reason` only)?
3. Is any separate launchd for `hashtags discover` expected? (Repo only schedules via main `run --loop`.)
4. Do operator docs imply true scrape parallelism? (Code is single-client serialized.)

---

## Report shape (return this)

- **Verdict:** EFFECTIVE / FROZEN_SESSION / FROZEN_OTHER / WIRING_BUG / DOC_DRIFT  
- **Evidence:** paths + symbols + live file facts (no secrets)  
- **Effectiveness numbers:** if measurable  
- **Recommended next unit:** one Linear-shaped ONE problem (or “operator scrape-login only”)
