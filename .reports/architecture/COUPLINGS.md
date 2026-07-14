# FanOps — Proven Hidden Couplings + Route Architecture

**Cycle 2 · 2026-07-14 · git HEAD `fcffa73`** · Priorities 6 (couplings) + 8 (routes)

**Only proven couplings appear here.** Each carries a `file:line` for *both* ends of the dependency —
a coupling with one end cited is a hypothesis, not a finding, and was dropped.

---

# Part 1 — Hidden couplings (15 proven)

## COUP-01 — Two lock domains that do not exclude each other · **structural**

The ledger (Surface A) and the control files (Surface B) are protected by **different, mutually
invisible** primitives:

| Domain | Primitive | Evidence |
|---|---|---|
| `ledger.sqlite` | SQLite `BEGIN IMMEDIATE` on a dedicated connection | [ledger_sqlite.py:92](src/fanops/ledger_sqlite.py:92) |
| `accounts.json`, `personas.json`, `hashtag_*.json` | `fcntl.flock` on a per-file `.lock` | [accounts.py:367-369](src/fanops/accounts.py:367), [persona_store.py:106](src/fanops/persona_store.py:106) |
| **`ledger.sqlite` — restore path only** | **`fcntl.flock` on `00_control/ledger.lock`** | [ledger.py:551](src/fanops/ledger.py:551) |

**Two consequences, both proven:**

1. **`restore_snapshot` excludes nothing.** It takes an flock on `ledger.lock`, then
   `os.replace(tmp, self.db_path)` ([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)) — swapping
   the database file. A concurrent `Ledger.transaction()` holds a SQLite lock on the **old inode** and
   would commit into a file already unlinked from the path. (Exposure today is nil: `restore_snapshot`
   has **no production caller**. See [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-07.)

2. **The publish path reads `accounts.json` outside any ledger lock.** `publish_due` calls
   `Accounts.load(cfg)` once ([run.py:439](src/fanops/post/run.py:439)) and resolves the per-post
   provider off that snapshot ([run.py:453](src/fanops/post/run.py:453)). A concurrent Go-Live
   `set_backend` / `write_integration` takes only the **accounts flock** — it cannot block, and is not
   blocked by, the publish transaction.

   **This is handled, not ignored:** `_resolve_publish_account_id` **re-resolves** the integration id at
   publish time ([run.py:182-193](src/fanops/post/run.py:182)) and FINALIZE merges `account_id` back with
   an explicit **"in-flight wins"** policy ([run.py:116-119](src/fanops/post/run.py:116)). The coupling
   is real; the mitigation is deliberate and documented in-code.

## ~~COUP-02~~ — **RETRACTED. This claim was FALSE.**

> I claimed *"a Studio process flipping `FANOPS_LIVE=1` cannot reach a separately-running daemon."*
> **That is wrong.** [cli.py:1303-1304](src/fanops/cli.py:1303) — the daemon loop calls
> `load_dotenv(cfg.root / ".env", override=True)` **and rebuilds `Config`** on **every tick**
> (`# operator disk truth each tick`). A `go_live` `.env` write **does** reach the resident daemon within
> one tick. Superseded by [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md) §1 SC-2.

## COUP-02b — A running Studio never re-reads `.env`; the daemon does *(the real coupling, opposite direction)*

| End | Behaviour |
|---|---|
| **Daemon loop** | `load_dotenv(override=True)` **+ `Config` rebuild every tick** ([cli.py:1303-1304](src/fanops/cli.py:1303)) |
| **Studio** | `load_dotenv` **once**, at process entry ([cli.py:795](src/fanops/cli.py:795)), then blocks in `app.run` ([cli.py:1285](src/fanops/cli.py:1285)) |

**A `.env` change made by the CLI or the daemon never reaches a running Studio.**
[golive.py:11](src/fanops/studio/golive.py:11) acknowledges this outright — it is precisely why
`_dual_write` also pokes `os.environ` ([golive.py:66](src/fanops/studio/golive.py:66)): so the *writing*
Studio process reflects its own change immediately. A Studio that did **not** perform the write shows
stale env until restarted.

## COUP-03 — `Post.error_reason` is a structured control channel, not a message

Three independent parsers read machine state out of this free-text field:

| Parser | What it extracts | Evidence |
|---|---|---|
| `transient_daemon_retry_count` | the retry counter `transient_daemon_retry=n/3` | written [run.py:336](src/fanops/post/run.py:336), read [run.py:333](src/fanops/post/run.py:333), [:405](src/fanops/post/run.py:405), [:419](src/fanops/post/run.py:419) |
| `_is_giveup` | the `GAVE UP:` terminal prefix | [reconcile.py:58,85](src/fanops/reconcile.py:58) |
| REST-gate quarantine | a sentinel prefix | [reconcile.py:90-96](src/fanops/reconcile.py:90) |

**Any writer that overwrites `error_reason` with free text silently resets the retry budget and clears
the give-up marker.** `reconcile.py:714` does exactly this **deliberately** on a successful publish
(`"error_reason": None` — *"a transient poll-error reason must not survive a successful publish"*). The
coupling is that ~14 writers share one field whose *format* is load-bearing to three readers.

## COUP-04 — Magic filename shape couples ingest to rebuild

`_SID_RE = re.compile(r"^src_[0-9a-f]{12}$")` ([ledger.py:234](src/fanops/ledger.py:234)) encodes the id
format minted elsewhere (`make_id("src", sha)` = `"src_" + sha1[:12]`, per
[ids.py:7](src/fanops/ids.py:7)). `rebuild_catalog` uses **the filename shape** to distinguish a
genuinely-orphaned source from junk (`.gitkeep`, `.DS_Store`, a hand-dropped file)
([ledger.py:753-758](src/fanops/ledger.py:753)).

**Changing the id length or prefix in `ids.py` silently breaks orphan detection in `ledger.py`** —
`rebuild_catalog` would stop seeing real orphans, with no error.

## COUP-05 — `_VALID_BACKENDS` is defined twice, and the two homes serve different boundaries

| Definition | Used by |
|---|---|
| [config.py:72](src/fanops/config.py:72) | `Config.poster_backend`'s **read**-time validation ([config.py:241](src/fanops/config.py:241)) |
| [settings.py:18](src/fanops/settings.py:18) | `accounts.set_backend`'s **write**-time validation ([accounts.py:13,414](src/fanops/accounts.py:13)) **and** `Settings.strict_validate` |

Both are currently `frozenset({"dryrun","postiz","zernio"})`. **If they drift, the write boundary and
the read boundary disagree about what a legal backend is** — the exact class of gap that INV-03
exploits. `accounts.py` imports from **`settings`**, not from `config`, even though it imports
`_LIVE_BACKENDS`, `_BACKEND_PLATFORMS`, and `FRAMING_NAMES` **from `config`** on the adjacent line
([accounts.py:12-13](src/fanops/accounts.py:12)).

## COUP-06 — The gate-kind registry is triplicated; adding a kind needs 3 edits

A gate kind must be registered in **three** places that nothing links together:

| Registry | Location |
|---|---|
| the **write** site | `write_request(cfg, kind=…)` — [moments.py:329,359](src/fanops/moments.py:329), [caption.py:222](src/fanops/caption.py:222), [intro_match.py:108](src/fanops/intro_match.py:108) |
| the **answer** registry | `responder._SCHEMA` + `_PROMPT` — [responder.py:50-51](src/fanops/responder.py:50) |
| the **ownership** resolver | `gate_keys.gate_source_id` — [gate_keys.py:9-13](src/fanops/gate_keys.py:9) |

**`intro_match` is the proof this coupling is live and unguarded:** it is registered in #1 only, and is
therefore permanently unanswerable (INV-04). Nothing — no test, no type, no assertion — ties the three
registries together.

## COUP-07 — ffmpeg selects its muxer by file **extension**

A temp file must therefore be `<dst>.part.mp4`, not `<dst>.part` (MOL-78). This couples every
render/compress temp-file name to ffmpeg's container inference. Recorded in project memory
(`ffmpeg-muxer-needs-mp4-suffix`) and enforced by convention, **not by a shared constant**.

## COUP-08 — The pipe-delimited composite-key format is an unenforced schema

Three different maps key on hand-built `"a|b"` strings, with **no shared constructor**:

| Map | Key format | Evidence |
|---|---|---|
| `surface_key` (post-id token **and** schedule seed) | `"{account}\|{platform}"` | [ids.py](src/fanops/ids.py), used [crosspost.py:194](src/fanops/crosspost.py:194) |
| `Ledger.tag_log` | `"{account}\|{clip_id}"` | [ledger.py:401-404](src/fanops/ledger.py:401) |
| `Ledger.variant_streaks` | `"{account}\|{platform}"` | [ledger.py:405](src/fanops/ledger.py:405) |

`surface_key` and `variant_streaks` share the *same* format but are minted independently. An account
handle containing `|` would corrupt all three — prevented only by `_ACCOUNT_HANDLE_RE = ^[a-z0-9._-]+$`
([models.py:438](src/fanops/models.py:438)), which is a **different** module. The handle charset is
therefore load-bearing to the ledger key space.

## COUP-09 — Nine lazy in-function imports exist solely to break module cycles

These are **not** style choices — a top-level import would be a hard `ImportError` at module load:

| Site | Cycle broken |
|---|---|
| [config.py:262-263](src/fanops/config.py:262), [:325](src/fanops/config.py:325), [:475](src/fanops/config.py:475) | `config ↔ accounts` |
| [accounts.py:367](src/fanops/accounts.py:367) | `accounts → ledger` (ledger never imports accounts — *"verified one-way"*) |
| [accounts.py:297](src/fanops/accounts.py:297) | `accounts ↔ personas` |
| [ledger.py:627](src/fanops/ledger.py:627) | `ledger → router` |
| [ledger.py:749](src/fanops/ledger.py:749) | `ledger → ingest` (ingest imports ledger) |
| [ledger.py:40](src/fanops/ledger.py:40), [:75](src/fanops/ledger.py:75) | `ledger → timeutil` (kept stdlib-only to stay cycle-safe) |
| [hashtags.py:124](src/fanops/hashtags.py:124), [meta_graph.py:516](src/fanops/meta_graph.py:516), [persona_store.py:106](src/fanops/persona_store.py:106) | reuse `ledger._file_lock` without a top-level cycle |

**Module initialization order is therefore load-bearing.** Hoisting any one of these to a top-level
import breaks the process at start.

## COUP-10 — `providers.py` is a structural false-dead-code source

Every backend factory is a **lazy in-function import lambda**
([providers.py:19-27](src/fanops/post/providers.py:19)) dispatched from a **dict**
([providers.py:45-49](src/fanops/post/providers.py:45)). A name-based call graph flags **all six** as
"zero callers" — **all six are live**. Self-declared in
[post/CLAUDE.md](src/fanops/post/CLAUDE.md). Same trap: `post.compress.persist_post_shrink`
(lazy-imported from `studio/actions.py`).

## COUP-11 — `_BACKEND_PLATFORMS` hardcodes deployment topology inside `config`

`{"postiz": {instagram, youtube}, "zernio": {tiktok}}`
([config.py:89-92](src/fanops/config.py:89)) bounds the legacy `FANOPS_POSTER` bridge so a
provider-less TikTok channel never falls back to an IG-wired Postiz global
([accounts.py:199](src/fanops/accounts.py:199)). **A deployment where Postiz also serves TikTok would
silently refuse to bridge that channel** — the config constant, not the account registry, decides.

## COUP-12 — `PLATFORM_MAX_SECONDS` fails open **by omission**

A platform **absent** from the dict has **no length clamp**
([models.py:157-160](src/fanops/models.py:157), enforced [crosspost.py:183-187](src/fanops/crosspost.py:183)).
Omission is thus a **silent policy decision**, indistinguishable from an oversight. The comment says so
explicitly — and records that the v1 version of this dict was *"declared but never enforced"*, a **false
safety contract** that let a 180 s pick fan out over YouTube's cap.

## COUP-13 — `LIFT_SCORE` is one literal with many readers

`LIFT_SCORE = "lift_score"` ([models.py:138](src/fanops/models.py:138)) — written in exactly one place
(`track.record_metrics`), read by `adjust`/`digest`/`variant_*`/`studio`. The in-code comment states the
failure mode precisely: *a key typo at any read site used to make that scorer silently treat every post
as "no lift data" — indistinguishable from "not enough data yet".* **Silent-degradation coupling: a
misspelled read is not an error, it is a quiet zero.**

## COUP-14 — `01_thirdparty_inbox` is a deliberate directory-naming coupling

It is a **peer** of `01_inbox`, **not** a child, specifically so it falls **outside the native ingest
`rglob`** ([config.py:61-63](src/fanops/config.py:61)). **Moving it under `01_inbox` would silently
relabel every third-party asset as native**, corrupting `origin_kind` — which is the field
`intro_match._candidates` filters on ([intro_match.py:37](src/fanops/intro_match.py:37)). The directory
layout *is* the type system here.

## COUP-15 — `Moment` is the only model with `validate_assignment`

[models.py:211](src/fanops/models.py:211). Every other ledger model silently accepts an invalid
`setattr`. Code that is correct against `Moment` (assign and trust the validator) is **incorrect**
against `Post` (assign and the validator never runs). This asymmetry is the mechanism behind INV-01.

---

# Part 2 — Route architecture (Priority 8)

**149 routes · 108 mutating · 41 read-only · 0 authenticated.**

Extracted by AST from `src/fanops/studio/*.py` (route decorator → handler → call set). Cycle 1 counted
149 and traced none; this is the attribution.

## Cross-cutting facts (true for every route)

| Property | Value |
|---|---|
| **Authorization** | **NONE.** No auth, no session, no CSRF, no `before_request`. See [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-21. |
| **Idempotency** | Not enforced at the route layer. It is inherited from the **action**: content-addressed ids + `setdefault` + in-lock source-state guards make most POSTs naturally idempotent. |
| **Ledger writes** | Always via `Ledger.transaction` inside the action — **never in the handler**. |
| **Error contract** | Actions return `ActionResult(ok, error, detail)`; handlers render a partial. |
| **htmx constraint** | An oversize upload body re-renders at **HTTP 200** with a "too large" message, because htmx 2.x drops non-2xx swaps. A 4xx would be invisible. |

## 2.1 Publish & schedule — the highest-consequence routes

| Route | Handler | Action | Ledger mutation | External effect |
|---|---|---|---|---|
| `POST /posts/approve` | `do_approve_posts` [app_routes_review.py:132](src/fanops/studio/app_routes_review.py:132) | `actions.approve_posts` | **`awaiting_approval → queued`** [ledger.py:591](src/fanops/ledger.py:591) | none |
| `POST /posts/reject` | `do_reject_posts` [:137](src/fanops/studio/app_routes_review.py:137) | `actions.reject_posts` | `awaiting_approval → rejected` (terminal) | none |
| `POST /posts/unapprove/<id>` | `do_unapprove_post` [:141](src/fanops/studio/app_routes_review.py:141) | `actions.unapprove_post` | `queued → awaiting_approval` | none |
| `POST /publish/now/<id>` | `do_publish_now` [app.py:512](src/fanops/studio/app.py:512) | `actions.publish_now` | **full `_publish_one` claim→network→finalize** | **⚠ LIVE POST** |
| `POST /schedule/publish/<id>` | `do_schedule_publish` [app_routes_schedule.py:76](src/fanops/studio/app_routes_schedule.py:76) | `actions.publish_now` | same | **⚠ LIVE POST** |
| `POST /schedule/publish-due` | `do_schedule_publish_due` [:108](src/fanops/studio/app_routes_schedule.py:108) | `actions.publish_due_bucket` | **the whole due bucket** | **⚠ LIVE POSTS (N)** |
| `POST /publish/posted/<id>` | `do_mark_posted` [app.py:507](src/fanops/studio/app.py:507) | `actions.mark_published` | `→ published` (**url required**, R1/D9) | none · **writes `studio_audit.log`** |
| `POST /posts/resolve/<id>` | `do_resolve_post` [app_routes_schedule.py:179](src/fanops/studio/app_routes_schedule.py:179) | `actions.resolve_post` | force-set state | none |
| `POST /posts/recover` | `do_recover_posts` [:185](src/fanops/studio/app_routes_schedule.py:185) | `actions.recover_posts` | `failed → queued` | none |
| `POST /home/reconcile`, `POST /schedule/reconcile` | [app.py:421](src/fanops/studio/app.py:421), [app_routes_schedule.py:84](src/fanops/studio/app_routes_schedule.py:84) | `actions.reconcile_inflight` | `submitting/submitted/needs_reconcile → published\|failed` | **network GET (poll)** |
| `POST /home/pull-metrics`, `POST /run/pull-metrics` | [app.py:417](src/fanops/studio/app.py:417), [app_routes_run.py:107](src/fanops/studio/app_routes_run.py:107) | `actions.pull_metrics_studio` | `published → analyzed` + `metrics` | **Meta Graph GET** |
| `POST /reschedule/<id>`, `/reschedule-surface/<id>`, `/schedule/move/<id>` | review:224,229 · schedule:64 | `actions.reschedule_post` | `scheduled_time` | none |
| `POST /clear/<id>`, `/schedule/clear/<id>` | review:234 · schedule:70 | `actions.clear_time` | **un-approves FIRST, then clears** — so a post is never `queued`-and-timeless | none |
| `POST /schedule/respread` | `do_reschedule_bucket` [:54](src/fanops/studio/app_routes_schedule.py:54) | `actions.reschedule_bucket` | bulk `scheduled_time` (the 40-min stagger — **reachable only here**) | none |
| `POST /schedule/shift/<handle>`, `/schedule/randomize/<handle>`, `/schedule/accept-suggested/<handle>` | schedule:46,98,94 | per-account schedule actions | `scheduled_time` | none |
| `POST /posts/repost/<id>` | `do_repost_post` [:167](src/fanops/studio/app_routes_schedule.py:167) | `actions.repost_post` | **mints a new `awaiting_approval` Post** (epoch-suffixed id) | none |
| `POST /posts/repost-others/<id>`, `/posts/crosspost/<clip>`, `/posts/crosspost-all` | schedule:172,191,197 | `repost_to_other_accounts` / `crosspost_to_account` / `crosspost_all_to_account` | **mints `awaiting_approval` Posts** | none |

## 2.2 Bulk approval (Review)

`POST /posts/approve-with-edits/<id>` · `approve-with-hook/<clip>` · `approve-as-is/<clip>` ·
`approve-batch/<batch>` · `approve-clip/<clip>` · `approve-account` · `approve-moment/<moment>` ·
`approve-channel` — [app_routes_review.py:145-174](src/fanops/studio/app_routes_review.py:145).

All funnel to `Ledger.approve_post`; each is a different **selector** over the post set, not a different
transition. **`approve-with-hook` and `approve-with-edits` additionally trigger a render**
(`render_moment_file`, [crosspost.py:119](src/fanops/crosspost.py:119)) — **filesystem write**, no
network.

## 2.3 Go-Live — the only routes that mutate publish routing

| Route | Action | Mutation | Notes |
|---|---|---|---|
| `POST /golive/live` | `golive.go_live` [app_routes_golive.py:206](src/fanops/studio/app_routes_golive.py:206) | **`.env` + `os.environ`: `FANOPS_LIVE=1`** | **The ONLY setter** (INV-18). Gated: accounts-valid → ≥1 live-ready channel → past-due-backlog → explicit confirm. **Never writes `FANOPS_POSTER`.** |
| `POST /golive/dryrun` | `golive.go_dryrun` | `FANOPS_LIVE=0` | |
| `POST /golive/account/backend` | `golive.set_account_backend` [:107](src/fanops/studio/app_routes_golive.py:107) | `accounts.json` `backends[platform]` | **Validated** against `_VALID_BACKENDS` ([accounts.py:414](src/fanops/accounts.py:414)) — the guarded write boundary INV-03 bypasses via hand-edit |
| `POST /golive/map` | `golive.map_account` | `accounts.json` `integrations[platform]` | |
| `POST /golive/config`, `/golive/zernio-config`, `/golive/account/meta-creds` | `set_postiz_config` / `set_zernio_config` / `set_meta_creds` | **`.env` + keyring** (write-only secrets) | keys **never rendered** |
| `POST /golive/account/{add,remove,promote,demote,persona}` | `accounts.py` mutators | `accounts.json` (flock) | |
| `POST /golive/{casting,responder,learning,amplify,ucb,transfer,llm-transport,clip-profile}` | `golive.set_*` | `.env` + `os.environ` | feature flags |
| `POST /golive/{daemon-install,daemon-uninstall}` | `golive.install_daemon` | **launchd plist** | bakes a full `PATH` at install (launchd supplies a bare one) |
| `POST /golive/{discover,adopt,refresh}` | `golive.discover_channels` / `adopt_channels` | `accounts.json` | **network GET (Postiz)** |
| `POST /golive/validate` | `golive.validate_learning` | **`cutover.json` only — never the ledger** | key never echoed |

## 2.4 Run / ingest / library

`POST /run/{ingest,pull,upload,upload/init,upload/finalize,resume,advance,prepare,bind-queue,release-batch,release-all}`
([app_routes_run.py:41-131](src/fanops/studio/app_routes_run.py:41)) ·
`POST /library/{upload,resume,retire,promote}` ([:167-190](src/fanops/studio/app_routes_run.py:167)).

- **`/run/upload`** streams raw video into `01_inbox/`: video-ext check, traversal-safe `secure_filename`,
  inbox-bound resolve, **atomic `.uploadpart` → `os.replace`**, 2 GiB `MAX_CONTENT_LENGTH`.
- **`/run/advance`** and **`/run/prepare`** drive the whole pipeline — **the widest mutation surface of
  any route** (transcribe → signals → moments → clips → captions → crosspost), and can spawn `claude -p`
  subprocesses and ffmpeg.
- **`/library/retire`** → `retire_source_studio` → `Ledger.retire_source` → **cascade** (moments/clips
  deleted or retired per `_PROTECTED_POST_STATES`; the source file is **left on disk**).
- **`/run/release-*`** → `SourceState.pending → catalogued` (the U4 queue gate).

## 2.5 Destructive

| Route | Action | Effect |
|---|---|---|
| `POST /live-library/wipe/preview` | `actions_wipe.preview_wipe` | read-only tally |
| `POST /live-library/wipe/confirm` | `actions_wipe.confirm_wipe` [app_routes_live.py:40](src/fanops/studio/app_routes_live.py:40) | **`Ledger.snapshot` first** ([actions_wipe.py:61](src/fanops/studio/actions_wipe.py:61)), then wipe. Reversible via `Ledger.restore_snapshot` — which is **not wired to any route** (INV-07). |

## 2.6 Personas / hashtags / stitches / gates

- `POST /personas/{add,edit,delete,compose,connect,migrate,research,recommend,corpus/add,corpus/remove}`
  → `personas.json` (flock). `/personas/research` and `/personas/recommend` make **live Meta Graph
  calls** (budget-gated, token never echoed).
- `POST /hashtags/ban/{add,remove}` → `hashtag_bans.json` (flock).
- `POST /stitches/{approve,dismiss,release}` → `StitchPlan` transitions (guarded, see
  [`STATE_MACHINE.md`](STATE_MACHINE.md) §7).
- `POST /gates/answer/<kind>/<key>` → writes a `.response.json` **by hand**
  ([app.py:532](src/fanops/studio/app.py:532)). **This is the only way an `intro_match` gate could ever
  be answered** (INV-04) — and it resolves its owner via `gate_source_id`
  ([actions.py:1166-1170](src/fanops/studio/actions.py:1166)), which returns `None` for `intro_match`
  and falls back to `or key`.
- `POST /gates/dismiss/<kind>/<key>` → deletes the gate files.

## 2.7 Media-serving GETs (the SSRF/path-traversal surface)

`GET /media/<post_id>` · `/media-preview/<post_id>` · `/clips/<clip_id>` · `/source-media/<source_id>` ·
`/keyframe/<source_id>/<name>` · `/thumb/source/<id>` · `/thumb/clip/<id>` · `/clip-thumb/<id>` ·
`/review-thumb/<eid>` — [app.py:484-615](src/fanops/studio/app.py:484).

All resolve a ledger id → a filesystem path and serve bytes. The guard is a **`_bounded` check** that
the resolved path lies under `cfg.base` — which is why `Config.render_path`
([config.py:179-189](src/fanops/config.py:179)) is documented as *"ALWAYS under self.base, so the Studio
`_bounded` serve check passes."* **That is a coupling: the render path builder and the serve guard must
agree, and only a comment ties them together.**
