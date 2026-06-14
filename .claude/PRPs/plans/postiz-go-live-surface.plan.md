# Plan: Postiz Go-Live Surface (Studio)

## Summary
Build an in-Studio "Go Live" tab that lets a non-technical operator turn FanOps from dryrun into real
publishing via Postiz — configure the Postiz URL + API key, map each account to its Postiz integration,
see a readiness check, and flip dryrun→live behind an explicit confirm — without env vars, the CLI, or
hand-editing `accounts.json`. The live poster code (`PostizPoster`), readiness checks (`doctor_report`),
durable config writes (`set_env_var`), and the publish path (`publish_now`/`publish_due`) already exist
and are reused as-is; this is purely the operator-facing surface over them.

## User Story
As the **solo, non-technical operator** of the artist's fan accounts,
I want to **connect Postiz and switch FanOps to live publishing from inside the Studio**,
so that **my reviewed clips actually post to the real accounts — without touching env vars, the CLI, or JSON files.**

## Problem → Solution
**Current:** going live requires `export FANOPS_POSTER=postiz`, `export POSTIZ_URL=…`, `export POSTIZ_API_KEY=…`,
and hand-pasting each account's Postiz integration id into `00_control/accounts.json`. A non-technical
operator cannot do this, and there is no in-Studio indicator of dryrun-vs-LIVE. → **Desired:** a Studio
"Go Live" tab that configures Postiz, fetches & maps integrations by picking, shows readiness (reusing
`doctor`), and flips dryrun↔live durably (`.env` + in-process `os.environ`) behind a confirm — with the
manual "Publish by hand" tab unchanged as the zero-infra fallback.

## Metadata
- **Complexity**: Large (new Studio tab: ~2 Postiz API helpers, ~5 actions, 1 read-model, ~5 routes, 2 templates, nav, ~18 tests)
- **Source PRD**: `.claude/prds/review-first-studio.prd.md`
- **PRD Phase**: Milestone 5 (Publish path) — the "real poster, operator-gated" half deferred there
- **Estimated Files**: 9 changed/created (+2 test files)

---

## UX Design

### Before
```
Operator wants to go live →
  opens a terminal → export FANOPS_POSTER=postiz
  → export POSTIZ_URL=… → export POSTIZ_API_KEY=…
  → opens 00_control/accounts.json in an editor
  → pastes a Postiz integration id into each account's "account_id"
  → restarts `fanops studio`
  (a non-technical operator is stuck at step 1)
```

### After
```
Studio nav:  Run · Footage · Review · Schedule · Lift · Gates · Publish · [Go Live]

[Go Live] tab:
  ┌────────────────────────────────────────────────────────┐
  │  MODE:  ● DRYRUN  (writes payloads, posts nothing)      │
  │                                                        │
  │  1. Connect Postiz                                     │
  │     Postiz URL  [https://………………]                      │
  │     API key     [••••••••  (set)]   [Save & test]      │
  │                                                        │
  │  2. Map your accounts  [Refresh from Postiz]          │
  │     @artist.fan / instagram → [ IG Reels (id 42) ▾ ]  │
  │     @artist.fan / tiktok    → [ TikTok (id 51)   ▾ ]  │
  │                                                        │
  │  3. Readiness                                          │
  │     ✓ POSTIZ_URL + POSTIZ_API_KEY set                  │
  │     ✓ accounts.json valid (all active have an id)     │
  │     ✗ claude on PATH …                                 │
  │                                                        │
  │  [ ⚠ GO LIVE — publishes to REAL accounts ]  (confirm)│
  └────────────────────────────────────────────────────────┘
  When LIVE: a persistent "● LIVE (postiz)" banner + a [Back to dryrun] button.
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Switch backend | `export FANOPS_POSTER=postiz` + restart | "Go Live" button (confirmed) writes `.env` + live `os.environ` | takes effect WITHOUT restart |
| Postiz URL/key | env vars | a form; key written to `.env`, never echoed back | secret stays out of HTML |
| Account→integration map | hand-edit `accounts.json` | fetch integrations, pick from a dropdown per surface | the key non-technical win |
| See publish mode | none | a dryrun/LIVE banner on Go Live (+ optionally global) | safety-critical state made visible |
| Publish | unchanged (`publish_now`/Run advance) | unchanged — they just now drive the real PostizPoster | no change to the publish path |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `src/fanops/post/postiz.py` | 1-137 | The live poster + `build_postiz_payload`/`postiz_upload_media`/`_extract_postiz_id`; the API contract + integration-checkpoint notes you extend with `postiz_list_integrations` |
| P0 | `src/fanops/autopilot.py` | 22-49 | `set_env_var` (durable `.env` write, secret-preserving) + the `.env`-AND-`os.environ` dual-write pattern the switch MUST mirror |
| P0 | `src/fanops/config.py` | 21-34, 78-97 | `load_dotenv` once at init; `poster_backend`/`postiz_url`/`postiz_api_key` read `os.getenv` LIVE → why the dual-write makes the switch take effect immediately |
| P0 | `src/fanops/studio/actions.py` | 24-28, 70-77, 135-201, (new) publish_now | `ActionResult`; `edit_caption` (in-lock guard+return); `run_advance`/`run_prepare` (live-confirm + accounts-validate + AuthError→FATAL) — the action idioms to copy |
| P0 | `src/fanops/studio/app.py` | 81-210 | `create_app(cfg)` factory, route + `_result.html` render patterns, the per-action POST shape |
| P1 | `src/fanops/doctor.py` | 14-65 | `doctor_report` → `{checks:[{label,ok,hint}], notes:[str]}` incl. the postiz check — reuse verbatim for the readiness panel |
| P1 | `src/fanops/accounts.py` | 17-23, 35-48, 53-76 | `Account` model (`account_id` = Postiz integration id for postiz), `load`, `validate`; you ADD an atomic raw-JSON writer that preserves unknown fields |
| P1 | `src/fanops/studio/views.py` | 20-49, 312-325 | read-model dataclass style + `pipeline_status` (lock-free read shape) to mirror for `golive_status` |
| P2 | `src/fanops/studio/templates/base.html` | 12-21 | nav tab pattern — add the "Go Live" link |
| P2 | `src/fanops/studio/templates/_run_panel.html` | all | the confirm-checkbox + primary-button + status-line template idiom to mirror |
| P2 | `tests/test_studio_run.py`, `tests/test_studio_publish_now.py` | all | Studio action + route test patterns (monkeypatch env, `create_app`, `test_client`) |

## External Documentation
| Topic | Source | Key Takeaway |
|---|---|---|
| Postiz public API | docs.postiz.com/public-api (cited in postiz.py:9) | `Authorization: {apiKey}` header; `GET /public/v1/integrations` lists connected channels; response shapes are an **integration checkpoint** — lock the SHAPE in offline tests, verify live against the operator's Postiz version (same posture as the existing poster). No external lib — plain `requests`. |

> No further external research needed — the repo already integrates the Postiz REST API; this adds one
> GET endpoint following the exact defensive style of `_extract_postiz_id`/`postiz_upload_media`.

---

## Patterns to Mirror

### DURABLE_CONFIG_WRITE (the dual-write — load-bearing)
```python
// SOURCE: src/fanops/autopilot.py:48-49
set_env_var(cfg.root / ".env", "FANOPS_RESPONDER", "llm")  # durable across restarts
os.environ["FANOPS_RESPONDER"] = "llm"                      # make THIS running process reflect it now
```

### STUDIO_ACTION + LIVE_CONFIRM + AUTH_FATAL
```python
// SOURCE: src/fanops/studio/actions.py:135-165 (run_advance) and the new publish_now
if cfg.poster_backend != "dryrun" and not confirmed:
    return ActionResult(ok=False, error=f"LIVE backend ({cfg.poster_backend}): … tick the confirm box, then run again.")
try:
    ...
except AuthError as exc:
    key = "POSTIZ_API_KEY" if cfg.poster_backend == "postiz" else "BLOTATO_API_KEY"
    return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
```

### POSTIZ_REQUEST + DEFENSIVE_SHAPE (for the new integrations list)
```python
// SOURCE: src/fanops/post/postiz.py:74-86 (postiz_upload_media) + :42-56 (_extract_postiz_id)
headers = {"Authorization": _key(cfg)}
resp = requests.get(f"{_base(cfg)}{_PUBLIC}/integrations", headers=headers, timeout=30)
if resp.status_code == 401:
    raise PostizAuthError("Postiz 401 on integrations — check POSTIZ_API_KEY (body withheld)")
if resp.status_code >= 300:
    raise RuntimeError(f"Postiz integrations failed ({resp.status_code}): {(resp.text or '')[:200]}")
body = resp.json()  # then walk defensively for id/name/identifier like _extract_postiz_id
```

### READINESS_REUSE
```python
// SOURCE: src/fanops/doctor.py:18-52
from fanops.doctor import doctor_report
report = doctor_report(cfg)   # {checks:[{label,ok,hint}], notes:[str]} — render checks as ✓/✗ + hint
```

### STUDIO_ROUTE + RESULT_PARTIAL
```python
// SOURCE: src/fanops/studio/app.py:200-208
@app.post("/golive/live")
def do_go_live():
    return render_template("_result.html",
                           result=actions.go_live(cfg, confirmed=bool(request.form.get("confirm"))))
```

### TEST_STRUCTURE
```python
// SOURCE: tests/test_studio_publish_now.py + tests/test_studio_run.py
def test_go_live_requires_readiness(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is False and "POSTIZ" in res.error      # blocked until configured
```

### ATOMIC_LEDGER_WRITE (mirror for the accounts writer)
```python
// SOURCE: ledger atomic write idiom (os.replace) — accounts.json must update via temp+replace,
// mutating the RAW dict (not Account.model_dump) so unknown/future fields are preserved.
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/fanops/post/postiz.py` | UPDATE | add `postiz_list_integrations(cfg) -> list[dict]` (GET /public/v1/integrations, defensive shape) + `postiz_check_auth(cfg) -> bool` (cheap auth probe reusing the list call) |
| `src/fanops/accounts.py` | UPDATE | add `write_account_id(cfg, handle, account_id)` — atomic raw-JSON update preserving all other fields/accounts |
| `src/fanops/studio/golive.py` | CREATE | go-live actions: `set_postiz_config`, `map_account`, `refresh_integrations`, `go_live`, `go_dryrun` (kept out of the already-large actions.py; imported by app) |
| `src/fanops/studio/views.py` | UPDATE | add `golive_status(cfg)` read-model: mode (dryrun/live), url-set?, key-set? (bool only), per-surface mapping, `doctor_report` checks/notes |
| `src/fanops/studio/app.py` | UPDATE | add routes: `GET /golive`, `POST /golive/config`, `POST /golive/refresh`, `POST /golive/map`, `POST /golive/live`, `POST /golive/dryrun` |
| `src/fanops/studio/templates/golive.html` | CREATE | the tab: mode banner, connect form, mapping table, readiness panel, go-live/back-to-dryrun |
| `src/fanops/studio/templates/_golive_panel.html` | CREATE | htmx-swapped partial (re-render after each action, like `_run_panel.html`) |
| `src/fanops/studio/templates/base.html` | UPDATE | add the "Go Live" nav tab + (optional) a global LIVE pill when `backend != dryrun` |
| `CLAUDE.md` | UPDATE | one line: the Studio "Go Live" tab as the operator path to live publishing |

## NOT Building
- **Blotato config UI** — Blotato is dropped; only the postiz (and dryrun) paths get a surface.
- **Self-hosting/installing Postiz** — the operator brings a running Postiz (hosted or self-hosted) with social accounts already connected in Postiz; we only consume its public API.
- **OAuth/connecting social accounts inside FanOps** — that happens in Postiz; we only list & map the resulting integrations.
- **Editing the publish path** — `publish_now`/`publish_due`/`PostizPoster` are reused unchanged.
- **Auth on the Studio / CSRF tokens** — out of scope (localhost, no-auth posture is pre-existing; see Risks).
- **Scheduling changes** — the Schedule tab + reschedule already exist (milestone 4).

---

## Step-by-Step Tasks

### Task 1: Postiz integrations + auth probe
- **ACTION**: Add `postiz_list_integrations(cfg) -> list[dict]` and `postiz_check_auth(cfg) -> bool` to `post/postiz.py`.
- **IMPLEMENT**: `postiz_list_integrations`: GET `{base}/public/v1/integrations` with `Authorization: {key}`, timeout=30; 401 → `PostizAuthError`; ≥300 → `RuntimeError`; else walk the body DEFENSIVELY (accept a top-level list OR `{integrations:[…]}`) and return `[{"id": str, "name": str, "platform": str}]`, extracting `id` and a display name/`identifier`/platform per item, skipping malformed entries. `postiz_check_auth`: call the list endpoint, return True on 2xx, raise `PostizAuthError` on 401, return False on other failures.
- **MIRROR**: POSTIZ_REQUEST + DEFENSIVE_SHAPE (postiz.py:42-56, 74-86).
- **IMPORTS**: `requests`, `from fanops.errors import PostizAuthError`, the module's `_base`/`_key`/`_PUBLIC`.
- **GOTCHA**: The integrations response shape is an INTEGRATION CHECKPOINT (not pinned in docs) — be defensive like `_extract_postiz_id`; the offline test locks the shape; a live verify happens via `refresh_integrations`. Never raise an uncaught error to the request handler.
- **VALIDATE**: `python -m pytest tests/test_postiz.py -q` (add cases: list parses id/name; 401→PostizAuthError; malformed item skipped).

### Task 2: Atomic accounts.json writer
- **ACTION**: Add `write_account_id(cfg, handle, account_id)` to `accounts.py`.
- **IMPLEMENT**: Read raw JSON from `cfg.accounts_path` (or `{"accounts": []}` if absent); find the account dict whose `handle == handle`; if missing raise `KeyError`; set its `"account_id"` to `account_id` (string); write back via temp file + `os.replace` (atomic). Mutate the RAW dict so any unknown/future keys survive. Return the updated handle.
- **MIRROR**: ATOMIC_LEDGER_WRITE; `Accounts.load` raw-parse (accounts.py:40-43).
- **IMPORTS**: `json`, `os`, `from pathlib import Path` (module already imports json).
- **GOTCHA**: Do NOT round-trip through `Account.model_dump()` — pydantic would drop unknown fields and could reorder/normalize, churning the operator's file. Preserve the raw structure. Also handle a missing-`account_id` account by adding the key.
- **VALIDATE**: a test writes an id, reloads `Accounts`, asserts the id + that a sibling account + an unknown field are untouched.

### Task 3: Go-Live actions module
- **ACTION**: Create `src/fanops/studio/golive.py` with `set_postiz_config`, `map_account`, `refresh_integrations`, `go_live`, `go_dryrun` returning `ActionResult` (import the dataclass from `studio.actions`).
- **IMPLEMENT**:
  - `set_postiz_config(cfg, url, key)`: validate `url` looks like http(s); `set_env_var(.env, "POSTIZ_URL", url)` + `os.environ["POSTIZ_URL"]=url`; if `key` non-empty, same for `POSTIZ_API_KEY` (NEVER log/echo the key); then `postiz_check_auth(cfg)` and report ok/fail (auth tested, key never returned).
  - `refresh_integrations(cfg)`: `postiz_list_integrations(cfg)` → ActionResult(detail={"integrations": [...]}); PostizAuthError→FATAL+POSTIZ_API_KEY; other→clean error.
  - `map_account(cfg, handle, integration_id)`: `accounts.write_account_id(cfg, handle, integration_id)`; clean error on unknown handle.
  - `go_live(cfg, confirmed)`: require `confirmed`; require `doctor_report` postiz readiness PASS (POSTIZ_URL+KEY set) AND `Accounts.load(cfg).validate()` empty (every active account has an id); only then `set_env_var(.env,"FANOPS_POSTER","postiz")` + `os.environ["FANOPS_POSTER"]="postiz"`; return the new mode. Refuse with the specific failing reason otherwise.
  - `go_dryrun(cfg)`: `set_env_var(.env,"FANOPS_POSTER","dryrun")` + `os.environ[...]="dryrun"` (always allowed — safe direction, no confirm).
- **MIRROR**: DURABLE_CONFIG_WRITE (autopilot.py:48-49), STUDIO_ACTION + LIVE_CONFIRM + AUTH_FATAL (actions.py run_advance), `run_prepare`'s accounts-validate gate.
- **IMPORTS**: `os`, `from fanops.studio.actions import ActionResult`, `from fanops.autopilot import set_env_var`, `from fanops.doctor import doctor_report`, `from fanops.accounts import Accounts`, `from fanops import accounts`, `from fanops.post import postiz`, `from fanops.errors import PostizAuthError`.
- **GOTCHA**: (1) The DUAL-WRITE is mandatory — `.env` alone won't change the running Studio (config reads `os.getenv` live but `load_dotenv` ran once at startup). (2) `go_live` must be the ONLY thing that sets `FANOPS_POSTER=postiz`; gate it on readiness+confirm so a stray POST can't go live. (3) NEVER put the API key in an ActionResult/detail/log — only a boolean "set".
- **VALIDATE**: `python -m pytest tests/test_studio_golive.py -q`.

### Task 4: golive_status read-model
- **ACTION**: Add `golive_status(cfg)` to `views.py`.
- **IMPLEMENT**: Return a dict: `mode` (cfg.poster_backend), `is_live` (≠dryrun), `postiz_url` (the URL — non-secret — or None), `key_set` (bool only, `cfg.postiz_api_key is not None`), `surfaces` (from `Accounts.load(cfg).surfaces()` → handle/platform/current account_id), `checks`+`notes` (from `doctor_report(cfg)`). Lock-free read; tolerate a malformed accounts.json (fall back to []).
- **MIRROR**: `pipeline_status` (views.py:312-325) lock-free read shape.
- **IMPORTS**: `from fanops.doctor import doctor_report`, `from fanops.accounts import Accounts`.
- **GOTCHA**: Expose `key_set` as a bool ONLY — never the key itself. `postiz_url` is non-secret and shown to confirm config.
- **VALIDATE**: test asserts `mode=="dryrun"` default and `key_set` reflects the env.

### Task 5: Routes
- **ACTION**: Add the six routes to `app.py` (mirror existing `do_*` handlers).
- **IMPLEMENT**: `GET /golive` → render `golive.html` with `views.golive_status(cfg)`, `tab="golive"`. `POST /golive/config` → `_golive_panel` swap with `golive.set_postiz_config(cfg, request.form["url"], request.form.get("key",""))`. `POST /golive/refresh` → panel with `golive.refresh_integrations`. `POST /golive/map` → panel with `golive.map_account(cfg, form["handle"], form["integration_id"])`. `POST /golive/live` → panel with `golive.go_live(cfg, confirmed=bool(form.get("confirm")))`. `POST /golive/dryrun` → panel with `golive.go_dryrun(cfg)`. Add a `_golive_panel(result)` helper that re-renders `_golive_panel.html` with FRESH `golive_status` (like `_run_panel`).
- **MIRROR**: app.py `_run_panel` (app.py:113-137) + the route block.
- **IMPORTS**: `from fanops.studio import golive` at module top (follows the existing `from fanops.studio import views, actions`).
- **GOTCHA**: After `go_live`/`go_dryrun`/`set_postiz_config`, the panel must re-read `golive_status` so the mode banner + checks update in place.
- **VALIDATE**: route tests: `GET /golive`==200 + contains "Go Live"/"DRYRUN"; `POST /golive/dryrun`==200 sets dryrun; `POST /golive/live` blocked when unconfigured.

### Task 6: Templates + nav
- **ACTION**: Create `golive.html` (extends base) + `_golive_panel.html` (the swappable body); add the nav tab to `base.html`.
- **IMPLEMENT**: `_golive_panel.html`: a prominent mode banner (`status.is_live` → "● LIVE (postiz)" else "● DRYRUN — posts nothing"); section 1 connect form (URL text input; key password input with placeholder "•••• (set)" when `status.key_set`, never the value; "Save & test" → `/golive/config`); section 2 mapping (a "Refresh from Postiz" button → `/golive/refresh`; for each `status.surfaces`, a `<select name=integration_id>` of `result.detail.integrations` (when present) + a hidden `handle`, POST `/golive/map`; show the current id); section 3 readiness (`status.checks` as ✓/✗ + hint, `status.notes`); the GO LIVE form (confirm checkbox + ⚠ button → `/golive/live`) shown when dryrun, or a "Back to dryrun" button when live. `golive.html` includes `_golive_panel.html`. Mirror `_run_panel.html` confirm/primary/`hx-swap="outerHTML"` idiom.
- **MIRROR**: `_run_panel.html` (confirm checkbox + primary button + status line + htmx swap).
- **IMPORTS**: n/a (Jinja). Use `url_for('do_*')`.
- **GOTCHA**: Never render the API key value. The key input is write-only; absence shows "(set)" via `key_set`, not the secret. Autoescape stays on (no `|safe`).
- **VALIDATE**: `GET /golive` HTML contains the banner + "Go live"; the key value never appears in the response.

### Task 7: Tests
- **ACTION**: `tests/test_postiz.py` (extend or create) + `tests/test_studio_golive.py`.
- **IMPLEMENT**: postiz: `postiz_list_integrations` parses id/name; 401→PostizAuthError; malformed item skipped (monkeypatch `requests.get`). golive: `set_postiz_config` writes `.env`+`os.environ` and tests auth (monkeypatch `postiz_check_auth`); key never in result; `map_account` writes accounts.json (preserving siblings/unknown fields); `go_live` blocked when unconfigured / when an active account has no id / when not confirmed; `go_live` flips to postiz when ready+confirmed (monkeypatch readiness); `go_dryrun` always flips back; the six routes (`create_app`, `test_client`) incl. the "key never echoed" assertion.
- **MIRROR**: `tests/test_studio_publish_now.py` + `tests/test_studio_run.py` (env monkeypatch, `create_app`, `test_client`, `_seed`).
- **IMPORTS**: pytest, `Config`, `create_app`, `from fanops.studio import golive`.
- **GOTCHA**: tests mutate `os.environ`/write `.env` under `tmp_path` — set `monkeypatch.chdir(tmp_path)` and use `Config(root=tmp_path)`; `monkeypatch.setenv/delenv` so a test's live switch doesn't leak into other tests (env isolation).
- **VALIDATE**: `python -m pytest tests/test_postiz.py tests/test_studio_golive.py -q`.

### Task 8: Docs + final
- **ACTION**: One CLAUDE.md line (the Studio "Go Live" tab as the live-publishing path); run the full suite + ruff.
- **VALIDATE**: see Validation Commands.

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| list integrations parses | 200 `[{id,name,…}]` | `[{"id","name","platform"}]` | no |
| list integrations 401 | 401 | raises `PostizAuthError` | yes |
| list integrations malformed item | item missing id | skipped, others returned | yes |
| write_account_id preserves siblings | 2 accounts + unknown key | target id set, rest byte-stable | yes |
| set_postiz_config dual-writes | url+key | `.env` + `os.environ` set; result has no key | yes (secret) |
| go_live blocked unconfigured | dryrun, no url/key | ok=False, names POSTIZ_URL/KEY | yes |
| go_live blocked, active acct no id | url+key set, acct id "" | ok=False, names the account | yes |
| go_live needs confirm | ready, confirmed=False | ok=False, "confirm" | yes |
| go_live success | ready + confirmed | mode→postiz (`.env`+env) | no |
| go_dryrun always | live | mode→dryrun, no confirm | no |
| GET /golive | — | 200, banner, no key value | yes (secret) |
| POST /golive/live unconfigured | — | 200 + error partial, still dryrun | yes |

### Edge Cases Checklist
- [x] Empty/blank URL or key → rejected, no partial write
- [x] Malformed accounts.json → status falls back, doesn't 500
- [x] API key never rendered in HTML or returned in ActionResult/detail/log
- [x] go_live is the ONLY path that sets FANOPS_POSTER=postiz; gated on readiness+confirm
- [x] Postiz 401 on refresh/test → FATAL + POSTIZ_API_KEY (type-matched, not substring)
- [x] Switch takes effect in the running process (os.environ) AND persists (.env)
- [x] go_dryrun (safe direction) needs no confirm
- [x] env isolation between tests (no leaked live mode)

---

## Validation Commands

### Static Analysis
```bash
ruff check .
```
EXPECT: All checks passed (compact house style; do NOT reformat).

### Unit Tests (affected area)
```bash
python -m pytest tests/test_postiz.py tests/test_studio_golive.py -q
```
EXPECT: all pass.

### Full Test Suite
```bash
python -m pytest -q -m "not integration"
```
EXPECT: no regressions (current baseline 720 passed).

### Browser Validation
```bash
fanops studio   # localhost:8787 — needs the [studio] extra
```
- [ ] "Go Live" tab loads; banner shows DRYRUN
- [ ] Save a fake Postiz URL+key → "Save & test" surfaces a clean auth result (no key echoed)
- [ ] Readiness panel shows ✓/✗ from doctor
- [ ] GO LIVE blocked until configured; with confirm it flips the banner to LIVE; "Back to dryrun" returns
- [ ] View source: the API key value is NOT present anywhere in the HTML

### Manual Validation (live, operator-gated — NOT in CI)
- [ ] Against a REAL Postiz instance: "Refresh from Postiz" lists the operator's integrations; mapping persists to accounts.json; a single `Publish now` posts for real (confirm the response id), then `Back to dryrun`.

---

## Acceptance Criteria
- [ ] Operator can connect Postiz, map accounts, and go live ENTIRELY in the Studio (no env vars / CLI / JSON edit)
- [ ] The dryrun↔live switch takes effect immediately (os.environ) and survives restart (.env)
- [ ] The API key is never rendered in HTML, returned in a result, or logged
- [ ] go_live is gated on readiness PASS + explicit confirm; go_dryrun is always allowed
- [ ] All validation commands pass; ruff clean; full suite no regressions
- [ ] The existing publish path (`publish_now`/`publish_due`/`PostizPoster`) is reused unchanged

## Completion Checklist
- [ ] Code follows discovered patterns (dual-write, ActionResult, defensive Postiz shape, lock-free read-model)
- [ ] Error handling matches codebase (PostizAuthError→FATAL by type; clean ActionResult errors, no 500s)
- [ ] No secret echoed/logged
- [ ] Tests follow the Studio test patterns; env isolated
- [ ] No hardcoded URLs/keys
- [ ] CLAUDE.md note added
- [ ] Self-contained — no codebase searching needed during implementation

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Postiz `GET /integrations` response shape differs by version (integration checkpoint) | Medium | Medium | Defensive parse (skip malformed, accept list-or-`{integrations}`); offline test locks shape; live verify via "Refresh"; manual paste of integration id remains a fallback in the map UI |
| Accidental go-live (no auth/CSRF on the localhost Studio) | Low | High | go_live requires readiness PASS + explicit confirm checkbox; it's the ONLY setter of FANOPS_POSTER=postiz; dryrun is the default and the safe fallback. Pre-existing no-auth posture is unchanged (documented deferral); optional follow-up: a one-time confirm token |
| Secret leakage (API key in HTML/logs/result) | Low | High | Key is write-only; status exposes `key_set` bool only; explicit tests assert the key never appears in responses |
| Switch doesn't take effect (only .env written) | Medium | Medium | Mandatory dual-write (`.env` + `os.environ`), mirrored from autopilot; test asserts both |
| accounts.json churn/corruption on write | Low | Medium | Atomic temp+os.replace; mutate RAW dict (preserve unknown fields); not via model_dump |

## Notes
- **Reuse, not rebuild**: `PostizPoster`, `publish_now`/`publish_due`/`_submit_one`, `doctor_report`, `set_env_var`, `Accounts` are all reused. The only new backend code is one GET helper + an auth probe + an atomic accounts writer.
- **Manual path stays**: the "Publish by hand" tab + `mark_published` remain the zero-infra fallback; this plan does not touch them.
- **Scope is operator-chosen** (2026-06-14, AskUserQuestion → "Postiz go-live now"); see [[fanops-moviepy-compose-decision]] for the session's decision-logging norm.
- **Confidence**: high for the surface/actions/tests (established idioms); the one live unknown is the integrations endpoint shape, isolated behind a defensive helper + manual fallback.
