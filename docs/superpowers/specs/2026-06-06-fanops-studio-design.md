# FanOps Studio — Design Spec

**Date:** 2026-06-06
**Status:** Approved (brainstorm) + hardened by adversarial verification — ready for implementation plan
**Topic:** A local web UI for Moh Flow FanOps, framed as a content cockpit for the operator-as-curator.

> **Revision note (2026-06-06):** the first draft of this spec was stress-tested by a 6-agent
> adversarial review against the actual code. One load-bearing claim **broke** (the "editable
> window" — see §4) and five carried **caveats**. Every finding is folded in below; the changes
> are summarised in §18.

---

## 1. Goal

Give the operator a **local web app** to *watch the clips the pipeline produced, tweak their captions, reschedule/reorder the upcoming post queue, and read performance* — without touching the autonomous machinery that keeps running on cron.

One sentence: **a curator's cockpit over the FanOps ledger, not an ops console.**

## 2. Why (current state)

FanOps today is a ~4,200-LOC Python CLI over one JSON ledger (`MohFlow-FanOps/00_control/ledger.json`), with the only human-readable surfaces being a markdown digest (`ledger_digest.md`) and a JSONL heartbeat log (`07_reports/run.log`). The operator reviews and reschedules content by reading markdown and hand-editing JSON. There is **no UI**. The pipeline (ingest → transcribe → decide moments → render → caption → schedule → publish → measure) is autonomous; the operator's actual job is editorial: see the rendered clips, sanity-check/tweak captions, control the upcoming schedule, and watch what wins.

## 3. Product scope

Two surfaces, one app:

- **Studio (primary, this build):** three tabs — **Review · Schedule · Lift**.
- **Dev portal (Phase 2, deferred):** the backend/health/state/recovery surface — explicitly *out* of the Studio. Specced in §14, built only on a later greenlight.

**Decisions locked during brainstorming:**
- **Form factor:** local web app (the core jobs — watch clips, thumbnails, lift bars — are inherently visual; a TUI can't play video).
- **Access:** local-only, bound to `127.0.0.1`. No auth, no remote, no multi-user.
- **Publish model: auto-publish stays.** The Studio never becomes a release gate. The pipeline keeps publishing on schedule; the Studio edits the *upcoming* (not-yet-due) window.
- **Architecture:** a single Python web service that imports FanOps modules and mutates the ledger **only** through the existing lock-safe `Ledger.transaction` path — never a parallel writer.
- **One enabling pipeline change (approved):** a default-off `FANOPS_PUBLISH_LEAD_MINUTES` knob (see §4) — the *only* core-code change; everything else is the new `fanops.studio` package.

## 4. The editorial window — and the pipeline change that creates it

**The problem the verification exposed.** `publish_due` ships a post the instant its `scheduled_time` reaches **real wall-clock now** (`post/run.py:31,51`), and `advance()` runs `crosspost` then `publish_due` **in the same pass** (`pipeline.py:112,137`). With the repo's *default* `--base-time` of `2026-06-02T18:00:00Z` (in the past), every freshly-queued post is already due and flashes `queued → published` in one pass — so **the "queued, not-yet-due" window the Studio edits would be empty.** The original spec wrongly assumed this window "reliably exists."

**The fix (approved): a publish lead-time.**
- **`Config.publish_lead_minutes`** — reads `FANOPS_PUBLISH_LEAD_MINUTES`, **default `0` (today's exact behavior)**; non-int or negative → `0` (fail-safe, like the other env knobs).
- **`crosspost.surface_time`** adds the lead as a **constant offset** on the anchor: `scheduled_time = base + lead + (seed % _ANCHOR_SPAN) + index*_STEP_MIN + jitter`. Because the lead is a constant (not wall-clock), the schedule stays **content-addressed and byte-deterministic** (the project's core invariant — re-runs are identical) and the existing monotonicity proof (`jitter < step`) is untouched (a constant shifts all times equally).
- **Operating convention (documented requirement):** to have a real window, cron must run `advance`/`run` with `--base-time` ≈ **now or future** (never the past default), and set `FANOPS_PUBLISH_LEAD_MINUTES` to the desired buffer (e.g. `120`). Then each new post sits in `queued` for ≈ `lead` minutes before it's due. With the default `lead=0` and/or a past base-time, the pipeline behaves exactly as today and the Studio's editable window is empty by design (Review/Schedule then show read-only history only — see below).

**Never-empty cockpit.** Even when the editable window is thin, **Review and Schedule also surface the most recent published/analyzed posts as read-only context** (e.g. last 24 h), so the operator always sees "what just shipped" alongside "what's about to."

**Imminent rows are edit-disabled (hard, not soft).** The verification showed the front of the queue is the common TOCTOU case, not an edge. So a post whose `scheduled_time` is within an *imminent threshold* of now (e.g. ≤ 5 min) — or already due — is rendered **read-only** in the UI (edit/reschedule disabled with a "shipping now / already shipped" label), because a concurrent cron `publish_due` may ship it before an edit lands. The flock guarantees no ledger corruption; this rule prevents the operator from editing something that's already gone.

## 5. The unit mapping the Studio renders

The ledger is four id→unit maps: `Source → Moment → Clip → Post` (`models.py`). The Studio assembles its views by walking the lineage:

- A **Post** carries `account`, `platform`, `caption`, `hashtags`, `scheduled_time`, `aspect`, `state`, `media_urls`, `variant_key`, `variant_hook`, `metrics`, `parent_id` (clip id).
- Its **Clip** (`led.clips[post.parent_id]`) carries `path` (the rendered `.mp4` under `03_clips/`), `aspect`, `held`/`held_reason`, `meta_captions`, `parent_id` (moment id).
- Its **Moment** (`led.moments[clip.parent_id]`) carries `start`, `end`, `reason`, `transcript_excerpt`, `hook`.
- Its **Source** (`led.sources[moment.parent_id]`) carries `source_path`, `language`.
- **Accounts** (`accounts.json` via `Accounts.load`) carry `handle`, `platforms`, `persona`, `status`.

## 6. Tab ① — Review

**Purpose:** see the upcoming content grouped by clip; sanity-check and tweak before it ships; see what just shipped.

**Reads (three buckets):**
1. **Editable:** `queued` posts that are **not imminent** (scheduled_time > now + imminent threshold), grouped by clip.
2. **Read-only recent:** the most recent `published`/`analyzed` posts (e.g. last 24 h) — "what just shipped", no actions.
3. **Held for review:** clips with `held=True` (these never reached `queued` — `crosspost` skips held clips), shown with `held_reason`, the play button, and the offending caption. **Review-only in v1** (see the note below).

For each clip show: the rendered video (inline `<video>`), source filename, moment window `start–end`, the moment `reason`, language, subtitles-burned flag, transcript excerpt. Under it, one row per `(account, platform)`: persona, the per-surface caption, scheduled time, and an imminent/shipped badge.

**Which video file to play (verification fix #3).** For a **variant** post (`FANOPS_CREATIVE_VARIATION` on, `post.media_urls` set), the bytes that actually ship are the per-account overlay file at `post.media_urls[0]` (`file://…`), **not** `led.clips[post.parent_id].path` (the un-hooked base clip). So each surface row plays its own media via `/media/<post_id>` (which resolves to the variant file when present, else the base clip). The card-level preview uses the base clip; held-clip review uses `/clips/<clip_id>`.

**Actions (editable bucket only):**
- **Edit caption** — set `post.caption` (text only). Confirmed end-to-end: all three posters read `post.caption` fresh at publish (`blotato_rest.py:87`, `blotato_mcp.py:24`, `dryrun.py:16`); the burned-in subtitles/hook are baked into the `.mp4` at render time, independent of the caption, so **no re-render**. **Hashtags are not editable in v1** — `post.hashtags` is a dead field in the publish path (the Blotato payload has no hashtags arg; `payload.py` sends only `text=post.caption`), so editing it would be a silent no-op. Stored hashtags are shown read-only, labelled "stored, not posted". (To post hashtags, type them into the caption text, where they do ship.)
- **Reschedule** — set `post.scheduled_time` (see §9 for the tz-aware normalization that is mandatory).
- **Snooze clip** — reschedule all of a clip's posts far into the future (a default `+365 d`), an honest, reversible "pull from this cycle" (no `cancelled` state in v1).

> **Held-clip release is deferred (verification fix #5).** Setting a held clip back to `captions_requested` (the CLI `unhold`) does **not** re-flow it for the brand-risk / language holds the Review tab surfaces: the next `advance` re-runs `ingest_captions` on the **unchanged cached caption**, the deterministic brand-risk regex re-fires, and the clip **re-holds** (a release→re-hold loop, `caption.py:65,204`). The correct fix is to *edit the offending caption and then release* — which needs a held-clip caption editor (the held caption lives in `clip.meta_captions` / the cached response, not a `Post`). That is a fast follow-on; **v1 makes held clips review-only** rather than shipping a release button that bounces back.

## 7. Tab ② — Schedule

**Purpose:** see and reorder the upcoming post queue on a timeline, with recent history for context.

**Reads:** `queued` posts sorted by `scheduled_time` (chronological timeline grouped by hour), plus the most recent `published`/`analyzed` posts as read-only past rows. Each row: time · `account/platform` · clip · an imminent/shipped badge.

**Actions:** **Move / reschedule** — set `post.scheduled_time` on a non-imminent `queued` post (same action as Review). "Reorder" = rescheduling individual posts. v1 is pick-a-time inputs; drag-and-drop is a polish follow-on.

## 8. Tab ③ — Lift

**Purpose:** read performance of what shipped. Read-only.

**Reads:** posts in state `analyzed` carrying `metrics["lift_score"]`, reusing the digest's computations (`digest.py:108–139`):
- **Lift-by-variant** — analyzed posts with a `variant_key`, ranked by `lift_score`, labelled by `variant_hook`, annotated with the per-surface loop state (`variant_learning.best_hooks`/`ucb_rank`, `variant_transfer.transferred_hooks`; fail-open exactly as the digest does).
- **Amplification streaks** — when `FANOPS_VARIANT_AMPLIFY` is on, the per-surface sustained-win streak (`variant_amplify.amplify_candidates` + `led.variant_streaks`).

Rendered as CSS/HTML bars — no JS charting library.

**Preconditions + honest empty states (verification fix #4).** Under the **default** config (dryrun poster, no `BLOTATO_API_KEY`, variant flags off) there are **no analyzed posts**, so Lift is empty. The `lift_rows` builder and template must render an explicit, non-error empty state naming the reason, per sub-view:
- *No analyzed posts at all* → "No analyzed posts yet — a live metrics backend (`FANOPS_POSTER` ≠ `dryrun` **and** `BLOTATO_API_KEY`) or fed metrics is required."
- *Analyzed posts exist but none carry a `variant_key`* → "Creative variation (`FANOPS_CREATIVE_VARIATION`) was off when these posts were crossposted — no per-variant lift."
- *Amplify section* → mirror the digest's `if cfg.variant_amplify:` gate (`digest.py:124`): the section is **absent**, not blank, when the flag is off.

## 9. The write surface (small and safe)

**Two** mutations in v1, both **`queued`-and-not-imminent only**, both thin lock-safe wrappers in `studio/actions.py`. Each opens **one** `Ledger.transaction(cfg)` and does its existence check + state guard + mutation **inside** the `with` block, on the in-lock freshly-loaded ledger (mirroring the CLI recovery verbs `cli.py:285,298`) — so it cannot lose-update against a concurrent cron `fanops run`.

| Action | Function | Effect | Guard |
|---|---|---|---|
| Reschedule | `reschedule_post(cfg, post_id, new_time)` | set `post.scheduled_time` to a **tz-aware UTC `Z` string** | post exists; state is `queued`; not imminent; `new_time` parses **and** is normalized to aware via `timeutil.iso_z` |
| Edit caption | `edit_caption(cfg, post_id, caption)` | set `post.caption` (text only) | post exists; state is `queued`; not imminent |

**Reschedule normalization is mandatory (verification fix #5).** A *naive* timestamp passes `timeutil.parse_iso` but then makes `publish_due` raise `TypeError` and mark the post **`failed`** — destroying the very post being rescheduled (`run.py:51–54`). So `reschedule_post` must: parse the input, ensure it is timezone-aware (reject or coerce naive to UTC), and re-emit via `timeutil.iso_z` (the canonical `…Z` aware form crosspost already uses), then persist that. A test must prove a naive input never reaches the ledger as-is.

**Snooze** is `snooze_clip(cfg, clip_id)` — a thin helper, not a new primitive: it calls `reschedule_post` on each of the clip's non-imminent `queued` posts with `now + 365 d` (tz-aware `Z`), so it inherits the same guard + normalization.

Each returns a small typed result (`ok`, `error`, the updated unit) so routes render success/failure. **No other mutation in v1:** no hashtag edit (dead field), no held-clip release (deferred), no writes to accounts/env-flags, no new post states, no publish blocking, no hook re-render.

## 10. Architecture

- **One Flask + Jinja2 + HTMX app** in a new package `src/fanops/studio/`. Server-rendered partials; HTMX for in-place updates and ~10 s polling; native `<video>` for playback. **No JS build step, no SPA.** HTMX is vendored under `studio/static/`, not loaded from a CDN. (FastAPI was the considered alternative; Flask + Jinja is the leaner fit for a server-rendered, single-operator localhost tool.)
- **Lazy Flask import is a hard requirement (verification fix #6).** The `studio` CLI handler MUST import `create_app` (and anything Flask-touching) **inside** the dispatch branch / `cmd_studio` body — never at `cli.py` module top — mirroring the existing in-dispatch `from fanops.discover import …` idiom (`cli.py:325,334`). A module-top import would make `import fanops.cli` (hence *every* verb) fail on a core, no-`[studio]` install. A guard test pins this.
- **Reads** use `Ledger.load(cfg)` (no lock — the atomic `os.replace` write guarantees a reader sees a complete file). Each request re-loads.
- **Writes** go through `Ledger.transaction(cfg)` only — the same `fcntl.flock` the CLI uses — single-writer-safe.
- **Clip files** served two ways, both ledger-indexed (the URL id is only a dict-key lookup → `KeyError` → 404; the path comes from the trusted ledger, never concatenated from the URL, so no traversal):
  - `GET /media/<post_id>` → the **variant** file `post.media_urls[0]` (strip `file://`) when set, else `led.clips[post.parent_id].path`. This is "what actually ships for this surface."
  - `GET /clips/<clip_id>` → `led.clips[clip_id].path` (base clip; used for card preview + held-clip review).
  - Both must `stat` the resolved path and return **404 (not 500)** when it's missing/stale (paths are absolute but frozen at render time, so a moved repo or different launch cwd can dangle).
- **Playback codec is a guarded invariant.** Inline `<video>` relies on the pipeline emitting **H.264 video + AAC audio in `.mp4` with `+faststart`** (`clip.py:47`, `overlay.py:210`). The integration test asserts this so a future codec change to the render args that would break playback fails CI.
- **Entry point:** `fanops studio [--host 127.0.0.1] [--port 8787]` (new `cli.py` subcommand → lazy-imported `cmd_studio`). Default bind `127.0.0.1` only (localhost; no auth).
- **Dependency:** `flask` added as a new **optional extra** `[project.optional-dependencies] studio = ["flask>=3.0"]`, so `pip install -e .` (core CLI) stays Flask-free; only `pip install -e '.[studio]'` pulls it.

## 11. File structure

```
src/fanops/config.py        # + publish_lead_minutes property (default 0, neg/non-int -> 0)
src/fanops/crosspost.py     # surface_time: + constant `lead` offset on the anchor
src/fanops/cli.py           # + `studio` subcommand -> cmd_studio (LAZY import of studio.app)
pyproject.toml              # + [studio] optional extra (flask)
src/fanops/studio/
  __init__.py
  app.py            # create_app(cfg) -> Flask; routes: tabs + /media/<post_id> + /clips/<clip_id>
  views.py          # pure read-model builders (no HTTP): review_buckets / schedule_rows / lift_rows
  actions.py        # lock-safe mutations: reschedule_post / edit_caption (+ snooze_clip helper)
  templates/        # base.html + review.html (+ _card.html) + schedule.html + lift.html
  static/           # studio.css + htmx.min.js (vendored)
tests/
  test_studio_views.py    # read models (incl. held bucket, recent bucket, imminent flag, empty Lift)
  test_studio_actions.py  # mutations + queued/not-imminent guards + naive-time rejection + lock reuse
  test_studio_app.py      # Flask test-client route smoke + /media variant resolution + 404 + flask-absent core-CLI guard
  test_crosspost.py       # (extend) lead-time: default 0 == today byte-identical; lead>0 shifts deterministically
tests/integration/
  test_studio_real.py     # Review serves a real rendered H.264/AAC mp4 end-to-end
```

**Read-model shapes (`views.py`, dataclasses/pydantic):**
- `ReviewCard`: `clip_id`, `preview_url` (base clip), `source_name`, `moment_window`, `reason`, `language`, `subtitles_burned`, `held`, `held_reason`, `transcript_excerpt`, `surfaces: list[SurfacePost]`, `bucket: "editable"|"recent"|"held"`.
- `SurfacePost`: `post_id`, `account`, `platform`, `persona`, `caption`, `hashtags` (read-only display), `scheduled_time`, `media_url` (`/media/<post_id>`), `state`, `imminent: bool`, `editable: bool`.
- `ScheduleRow`: `post_id`, `scheduled_time`, `account`, `platform`, `clip_id`, `state`, `imminent`, `editable`.
- `LiftRow`: `variant_hook`, `account`, `platform`, `lift_score`, `loop_state`, optional `amplify_state`. Plus an `empty_reason` carried when a section has no rows.

## 12. Security & concurrency

- **Bind `127.0.0.1` only** (no `0.0.0.0`); localhost-only, no auth in v1.
- **In-lock guards:** every mutation re-checks existence + `queued` + not-imminent **inside** the `Ledger.transaction` block, so a concurrent cron pass that published/changed the post is detected and the action is rejected cleanly.
- **Single-writer:** the Studio holds the flock only for the brief mutation; render/serving is lock-free.
- **No secrets in the UI:** never displays/edits `BLOTATO_API_KEY` or any credential; `account_id` is a non-secret id already in `accounts.json`.

## 13. Non-goals (v1)

No auth/remote/multi-user, no SPA/JS build step, no publish gate (auto-publish stays), no hashtag editing (dead field), no held-clip *release* (review-only; release+caption-edit is a follow-on), no editing the burned-in hook (needs an ffmpeg re-render), no hard `cancel`/`cancelled` state, no account/env-flag editing, no wall-clock in the schedule (lead is a constant offset), no writes to any unit other than non-imminent `queued` posts, no dev portal (Phase 2).

## 14. Phase 2 — Dev portal (deferred, sketch)

A second route group `/dev` on the same Flask app, rendering the backend surface kept *out* of the Studio: the `fanops status` funnel; heartbeat / dead-man's-switch from `run.log`; stuck-state queues (`needs_reconcile`, errors, published-but-unmeasured); read-only flags/backend; and POST buttons for the recovery verbs (`resolve`/`retry-source`/`retry-metrics`/`reconcile`/`adjust`/`gc`) calling the existing `cli.cmd_*` under transaction. Not built this pass; the route namespace and app factory are designed to accommodate it without a rewrite.

## 15. Testing strategy (TDD)

- `test_crosspost.py` (extend) — `publish_lead_minutes` default `0` produces **byte-identical** schedules to today (regression guard on the determinism invariant); `lead>0` shifts every time by exactly the constant and preserves ordering/monotonicity; non-int/negative env → `0`.
- `test_studio_views.py` — read models from an in-memory `Ledger`: editable/recent/held bucketing, imminent flag math, variant `media_url` resolution, and the **empty-Lift** states (default config) plus a fed-metrics populated case.
- `test_studio_actions.py` — each mutation through `Ledger.transaction`; the queued-and-not-imminent guard rejects non-`queued`/imminent posts; a **naive** ISO input is rejected/normalized and never persisted naive; a write is visible on a fresh `Ledger.load`.
- `test_studio_app.py` — Flask test client: each tab returns 200 and renders; `/media/<post_id>` returns the **variant** file when present else the base, and 404 on unknown/missing; a mutation route round-trips; **and a guard test that `import fanops.cli` + a non-studio verb succeed with Flask absent** (block `flask` in `sys.modules`).
- `tests/integration/test_studio_real.py` — real ffmpeg render → queue a post → assert Review serves the actual H.264/AAC `.mp4` bytes; skips cleanly when ffmpeg is absent.

## 16. Success criteria

1. `pip install -e '.[studio]' && fanops studio` serves `http://127.0.0.1:8787` with Review/Schedule/Lift.
2. With `FANOPS_PUBLISH_LEAD_MINUTES>0` and a now/future base-time, freshly-queued posts appear as **editable** rows; Review plays the correct (variant-aware) clip and shows the moment reason + caption; Schedule lists them chronologically; both also show recent shipped posts read-only.
3. Reschedule and edit-caption persist through `Ledger.transaction` (tz-aware `Z` for reschedule), are visible to the CLI, and survive a concurrent `fanops run`; imminent/already-due posts are non-editable.
4. Lift ranks analyzed variants by `lift_score` when data exists, and shows an explicit reason-bearing empty state otherwise.
5. `FANOPS_PUBLISH_LEAD_MINUTES` default `0` leaves the pipeline byte-identical to today; the core install (`pip install -e .`, no `[studio]`) stays Flask-free and every existing verb still imports/runs.
6. The full unit suite stays green; new tests cover lead-time determinism, views (incl. empty Lift), actions (incl. naive-time + guards), routes (incl. variant `/media` + flask-absent core CLI).

## 17. Honest limits / known gaps

- **The editable window exists only by convention** (§4): `FANOPS_PUBLISH_LEAD_MINUTES>0` **and** cron run with a now/future base-time. Default config → empty editable window (Review/Schedule show read-only history only).
- **Front-of-queue edits race the cron** — handled by the hard imminent-disable rule, not a soft warning.
- **Hashtags don't post** — `post.hashtags` is unused by the publish path; shown read-only. Folding hashtags into the Blotato `text` is a follow-up.
- **Held-clip release is deferred** — review-only in v1 because release-without-caption-edit re-holds (§6). Release+caption-edit is the immediate follow-on.
- **Variant playback depends on `media_urls`** — only set when `FANOPS_CREATIVE_VARIATION` was on at crosspost; otherwise the base clip is the shipped media (correct to serve).
- **Burned-in hook is immutable in v1** (needs an ffmpeg re-render).
- **`flask` dev server** for a single-operator localhost tool (acceptable; remote access would be a separate auth + WSGI design).

## 18. Changelog — corrections from adversarial verification (2026-06-06)

A 6-agent review tested the first draft against the code. Outcome: **1 break, 5 caveats**, all addressed:
1. **Editable window (BREAK)** — posts publish same-pass under the default base-time. Added the default-off `FANOPS_PUBLISH_LEAD_MINUTES` lead-time (constant offset, determinism-safe), the base-time=now operating convention, read-only recent-history context, and the hard imminent-disable rule (§4, §6, §7).
2. **Caption edit (caveat)** — `post.caption` posts correctly (no re-render); `post.hashtags` is a dead field → dropped from the editor, shown read-only (§6, §9).
3. **Clip playback (caveat)** — variant posts ship `media_urls[0]`, not `clip.path` → added the `/media/<post_id>` variant-aware route; pinned the H.264/AAC/+faststart codec invariant; 404 on stale paths (§6, §10).
4. **Lift data (caveat)** — empty under default config → added explicit preconditions, per-section gates, reason-bearing empty states, and an empty-case test (§8, §15).
5. **Mutation safety (caveat)** — lock-safety holds; but reschedule must normalize to tz-aware `Z` (a naive time marks the post `failed`), and held-clip release re-holds → reschedule hardened, release deferred to follow-on (§9, §6).
6. **Flask dependency (caveat)** — clean only if the Flask import is lazy → made lazy-import a hard requirement + a flask-absent guard test (§10, §15).
