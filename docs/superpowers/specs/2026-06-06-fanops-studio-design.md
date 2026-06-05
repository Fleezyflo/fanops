# FanOps Studio — Design Spec

**Date:** 2026-06-06
**Status:** Approved (brainstorm) — ready for implementation plan
**Topic:** A local web UI for Moh Flow FanOps, framed as a content cockpit for the operator-as-curator.

---

## 1. Goal

Give the operator a **local web app** to *watch the clips the pipeline produced, tweak their captions, reschedule/reorder the upcoming post queue, and read performance* — without touching the autonomous machinery that keeps running on cron.

One sentence: **a curator's cockpit over the FanOps ledger, not an ops console.**

## 2. Why (current state)

FanOps today is a ~4,200-LOC Python CLI over one JSON ledger (`MohFlow-FanOps/00_control/ledger.json`), with the only human-readable surfaces being a markdown digest (`ledger_digest.md`) and a JSONL heartbeat log (`07_reports/run.log`). The operator reviews and reschedules content by reading markdown and hand-editing JSON. There is **no UI**. The pipeline (ingest → transcribe → decide moments → render → caption → schedule → publish → measure) is autonomous; the operator's actual job is editorial: see the rendered clips, sanity-check/tweak captions, control the upcoming schedule, and watch what wins.

## 3. Product scope

Two surfaces, one app:

- **Studio (primary, this build):** three tabs — **Review · Schedule · Lift**.
- **Dev portal (Phase 2, deferred):** the backend/health/state/recovery surface — explicitly *out* of the Studio. Specced in §13, built only on a later greenlight.

**Decisions locked during brainstorming:**
- **Form factor:** local web app (the core jobs — watch clips, thumbnails, lift bars — are inherently visual; a TUI can't play video).
- **Access:** local-only, bound to `127.0.0.1`. No auth, no remote, no multi-user.
- **Publish model: auto-publish stays.** The Studio never becomes a release gate. The pipeline keeps publishing on schedule; the Studio edits the *upcoming* (not-yet-due) window.
- **Architecture:** a single Python web service that imports FanOps modules and mutates the ledger **only** through the existing lock-safe `Ledger.transaction` path — never a parallel writer.

## 4. The editorial window (core concept)

`publish_due(now)` ships only posts in state `queued` whose `scheduled_time <= now` (`src/fanops/post/run.py`). Therefore the operator's editable surface is exactly: **posts in `queued` state with a `scheduled_time` still in the future.** The Studio reads and mutates that window. Once a post's time arrives the cron publishes it and it becomes read-only history (moves to the Lift view after `track` flips it to `analyzed`).

**Honest limit (TOCTOU):** editing a post whose `scheduled_time` is already `<= now` can lose to a concurrent cron `publish_due`. The flock serializes the two writers so neither corrupts the ledger, but a just-due post may ship before an edit lands. The Studio is for the *upcoming* window; the UI marks rows whose time is imminent.

## 5. The unit mapping the Studio renders

The ledger is four id→unit maps: `Source → Moment → Clip → Post` (`src/fanops/models.py`). The Studio assembles its views by walking the lineage:

- A **Post** (`queued`) carries `account`, `platform`, `caption`, `hashtags`, `scheduled_time`, `aspect`, `variant_key`, `variant_hook`, `metrics`, `state`, `parent_id` (clip id).
- Its **Clip** (`led.clips[post.parent_id]`) carries `path` (the rendered `.mp4` under `03_clips/`), `aspect`, `held`/`held_reason`, `meta_captions`, `parent_id` (moment id).
- Its **Moment** (`led.moments[clip.parent_id]`) carries `start`, `end`, `reason` (why it was posted), `transcript_excerpt`, `hook`.
- Its **Source** (`led.sources[moment.parent_id]`) carries `source_path` (filename), `language`.
- **Accounts** (`accounts.json` via `Accounts.load`) carry `handle`, `platforms`, `persona`, `status`.

## 6. Tab ① — Review

**Purpose:** see the upcoming content grouped by clip; sanity-check and tweak before it ships.

**Reads:** group all `queued` posts by their clip. For each clip show: the rendered video (inline `<video>` via `/clips/<clip_id>`), the source filename, the moment window `start–end`, the moment `reason`, the language, whether subtitles are burned, and the transcript excerpt. Under it, one row per target `(account, platform)`: the per-surface caption, scheduled time, and persona. Separately, surface **brand-risk holds**: clips with `held=True` (these never reached `queued` — `crosspost` skips held clips), shown with `held_reason` and a play button so the operator can review and release them. (Holds are a *content-review* concern, so they live in the Studio, not the dev portal.)

**Actions:**
- **Edit caption** — set `post.caption` / `post.hashtags` (text only; the burned-in on-screen hook is part of the rendered video and is not edited in v1).
- **Reschedule** — set `post.scheduled_time`.
- **Snooze clip** — reschedule all of a clip's posts far into the future (an honest, reversible "pull from this cycle"; there is no `cancelled` state and v1 does not invent one).
- **Release hold** — `clip.held=False`, `held_reason=None`, state → `captions_requested` (reuses the existing `unhold` logic so the clip re-enters the caption gate).

## 7. Tab ② — Schedule

**Purpose:** see and reorder the upcoming post queue on a timeline.

**Reads:** all `queued` posts sorted by `scheduled_time`, rendered as a chronological list/timeline (grouped by hour), each row showing time · `account/platform` · which clip · imminent-flag.

**Actions:**
- **Move / reschedule** — set `post.scheduled_time` (same action as Review). "Reorder" is expressed as rescheduling individual posts. v1 is pick-a-time inputs; drag-and-drop is a polish follow-on.

## 8. Tab ③ — Lift

**Purpose:** read performance of what shipped. Read-only.

**Reads:** posts in state `analyzed` carrying `metrics["lift_score"]`. Reuse the digest's existing computations (`src/fanops/digest.py`):
- **Lift-by-variant ranking** — analyzed posts that carry a `variant_key`, ranked by `lift_score`, labelled by `variant_hook`, with the per-surface learning-loop state (`variant_learning.best_hooks` / `ucb_rank` / `variant_transfer.transferred_hooks`, fail-open exactly as the digest does).
- **Amplification streaks** — when `FANOPS_VARIANT_AMPLIFY` is on, the per-surface sustained-win streak toward the amplify gate (`variant_amplify.amplify_candidates` + `led.variant_streaks`).

Rendered as CSS/HTML bars — no JS charting library.

## 9. The write surface (small and safe)

Only three mutations, all **`queued`-only** (a guard rejects posts already in `submitting`/`published`/`analyzed`), all implemented as thin lock-safe wrappers in `studio/actions.py`, each opening one `Ledger.transaction(cfg)` exactly like a CLI recovery verb:

| Action | Function | Effect | Guard |
|---|---|---|---|
| Reschedule | `reschedule_post(cfg, post_id, new_time_iso)` | set `post.scheduled_time` | post exists, state is `queued`, `new_time_iso` parses as ISO-8601 (reuse `timeutil.parse_iso`) |
| Edit caption | `edit_caption(cfg, post_id, caption, hashtags)` | set `post.caption`, `post.hashtags` | post exists, state is `queued` |
| Release hold | `release_hold(cfg, clip_id)` | `clip.held=False`, `held_reason=None`, state → `captions_requested` | clip exists, `held` is True |

Each returns a small typed result (`ok: bool`, `error: str | None`, the updated unit) so routes can render success/failure. No other mutation exists in v1: no writes to accounts/env-flags, no new states, no publish blocking, no hook re-render.

## 10. Architecture

- **One Flask + Jinja2 + HTMX app** in a new package `src/fanops/studio/`. Server-rendered partials; HTMX for in-place updates and ~10s polling; native `<video>` for playback. **No JS build step, no SPA.** (FastAPI was the considered alternative; Flask + Jinja is the leaner fit for a server-rendered, single-operator localhost tool. HTMX is vendored under `studio/static/`, not loaded from a CDN.)
- **Reads** use `Ledger.load(cfg)` (no lock needed — the atomic `os.replace` write guarantees a reader sees a complete file). Each request re-loads so the view is always current.
- **Writes** go through `Ledger.transaction(cfg)` only — the same `fcntl.flock`-guarded load→mutate→save the CLI uses — so the Studio can never lose-update against a concurrent cron `fanops run`.
- **Clip files** served by `GET /clips/<clip_id>` → `send_file(led.clips[clip_id].path)`. Ledger-indexed (the route maps an id to a path; it does not expose the raw filesystem). 404 if the id is unknown or the file is missing.
- **Entry point:** a new CLI verb `fanops studio [--host 127.0.0.1] [--port 8787]` (added to `cli.py` dispatch) that constructs `Config()` and runs the app. Default bind `127.0.0.1` (localhost-only; the host flag exists but defaults safe).
- **Dependency:** `flask` added as a new **optional extra** in `pyproject.toml` (`[project.optional-dependencies] studio = ["flask>=3.0"]`), so `pip install -e .` (core CLI) stays lean and only `pip install -e '.[studio]'` pulls Flask.

## 11. File structure

```
src/fanops/studio/
  __init__.py
  app.py            # create_app(cfg) -> Flask; all routes (studio tabs + /clips)
  views.py          # pure read-model builders (no HTTP): review_cards / schedule_rows / lift_rows
  actions.py        # lock-safe mutations: reschedule_post / edit_caption / release_hold
  templates/
    base.html       # shell + tab nav + HTMX include
    review.html     # tab 1 (+ a _card.html partial)
    schedule.html   # tab 2
    lift.html       # tab 3
  static/
    studio.css      # minimal styling + the lift bars
    htmx.min.js     # vendored
src/fanops/cli.py   # + `studio` subcommand -> cmd_studio(cfg, host, port)
pyproject.toml      # + [studio] optional extra (flask)
tests/
  test_studio_views.py    # read models from a hand-built Ledger
  test_studio_actions.py  # mutations + queued-only guards + lock reuse
  test_studio_app.py      # Flask test-client route smoke (200s, renders)
tests/integration/
  test_studio_real.py     # Review tab serves a real rendered clip file end-to-end
```

**Read-model shapes (defined in `views.py`, plain dataclasses or pydantic):**
- `ReviewCard`: `clip_id`, `video_url`, `source_name`, `moment_window` (`"0:12–0:27"`), `reason`, `language`, `subtitles_burned: bool`, `held: bool`, `held_reason`, `transcript_excerpt`, `surfaces: list[SurfacePost]`.
- `SurfacePost`: `post_id`, `account`, `platform`, `persona`, `caption`, `hashtags`, `scheduled_time`, `imminent: bool`.
- `ScheduleRow`: `post_id`, `scheduled_time`, `account`, `platform`, `clip_id`, `imminent: bool`.
- `LiftRow`: `variant_hook`, `account`, `platform`, `lift_score`, `loop_state` (the digest's gate label), plus an `amplify_state` where applicable.

## 12. Security & concurrency

- **Bind 127.0.0.1 only** (no `0.0.0.0`). Localhost-only was the explicit choice; no auth layer in v1.
- **Single writer discipline:** the Studio holds the flock only for the brief mutation transaction; the slow work (video serving, page render) happens lock-free. It cannot serialize behind or be lost by the cron.
- **No secrets in the UI:** the Studio never displays or edits `BLOTATO_API_KEY` or any credential; `account_id` is a non-secret identifier already in `accounts.json`.

## 13. Phase 2 — Dev portal (deferred, sketch only)

A second set of routes under `/dev` on the same Flask app, rendering the backend surface the operator wanted *kept out* of the Studio:
- **Funnel / counts** — the `fanops status` numbers (sources/moments/clips/posts/published/failed/needs_reconcile) as a Source→Moment→Clip→Post funnel.
- **Heartbeat / dead-man's-switch** — tail `07_reports/run.log`; show last heartbeat, `published_in_run`, `last_published_age_hours`, and a stale-cron warning.
- **Stuck-state queues** — `needs_reconcile`, error-quarantined units, published-but-unmeasured posts (the digest already lists these).
- **Flags / backend** — current `FANOPS_POSTER`, `FANOPS_RESPONDER`, and the variant flags (read-only display).
- **Recovery verbs** — POST buttons for `resolve` / `retry-source` / `retry-metrics` / `reconcile` / `adjust` / `gc`, each calling the existing `cli.cmd_*` under transaction.

Not built in this pass. Specced here so the route namespace (`/dev`) and the app factory are designed to accommodate it without a rewrite.

## 14. Non-goals (v1)

No auth, no remote/multi-user access, no SPA/JS build step, no publish gate (auto-publish stays), no editing of the burned-in hook (would need an ffmpeg re-render), no hard `cancel`/`cancelled` post state, no account or env-flag editing from the UI, no writes to any unit other than `queued` posts and `held` clips, no dev portal (Phase 2).

## 15. Testing strategy (TDD)

- `test_studio_views.py` — build a `Ledger` in memory with sources/moments/clips/queued-posts and assert the read-model builders produce the right cards/rows/lift ranking (including held-clip surfacing and imminent flagging).
- `test_studio_actions.py` — assert each mutation works through `Ledger.transaction`, the **queued-only guard** rejects non-`queued` posts, a bad ISO time is rejected, `release_hold` reuses the unhold semantics, and a write is visible on a fresh `Ledger.load` (proving it persisted through the lock).
- `test_studio_app.py` — Flask test client: each tab route returns 200 and renders the expected anchors; `/clips/<id>` returns the file for a known clip and 404 otherwise; a mutation route round-trips and reflects the change.
- `tests/integration/test_studio_real.py` — render a real clip (real ffmpeg, like the existing E2E), queue a post, and assert the Review tab serves the actual `.mp4` bytes; skips cleanly when ffmpeg is absent.

## 16. Success criteria

1. `pip install -e '.[studio]' && fanops studio` serves `http://127.0.0.1:8787` with Review/Schedule/Lift tabs.
2. Review plays a real rendered clip and shows its moment reason + per-surface captions; Schedule lists the upcoming queue chronologically; Lift ranks analyzed variants by `lift_score`.
3. Reschedule, edit-caption, and release-hold each persist through `Ledger.transaction` and are visible to the CLI (and survive a concurrent `fanops run`).
4. The core CLI install (`pip install -e .`, no `[studio]`) is unchanged and Flask-free.
5. The full unit suite stays green; new Studio tests cover views, actions (incl. guards), and routes.

## 17. Open risks / honest limits

- **Just-due edits race the cron** (§4) — accepted; the UI flags imminent rows.
- **Burned-in hook is immutable in v1** — editing it needs an ffmpeg re-render of the variant overlay; deferred.
- **No cancel** — "snooze" (reschedule far out) is the only pull mechanism; a real `cancelled` state is a future decision.
- **`flask` dev server** is used for a single-operator localhost tool (acceptable; not a public-facing deployment). If remote access is ever wanted, that's a separate auth + WSGI design.
