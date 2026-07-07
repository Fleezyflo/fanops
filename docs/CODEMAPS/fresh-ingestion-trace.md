# Fresh-Ingestion End-to-End Trace — one video → posts across the 5 accounts

> Code-verified against `origin/main` @ `760140b` on 2026-07-07. Method: read `pipeline.advance()`
> + every stage fn, two parallel deep-trace agents (fan-out arithmetic + external-service map),
> then re-verified the load-bearing claims (casting teardown, `affinity_admits`, R2 path) against
> the live code. This is the operator's field guide for a fresh live test: drop a video, watch each
> stage fire, tick the observable, run the verify command.

**Live host config confirmed this session:** `is_live=True`, `responder=llm`, `account_casting=ON`,
`smart_framing=ON`, `hook_router=OFF`, `clip_profile=talk`, `operator_tz=America/New_York`,
`media_public_base=https://pub-7b06….r2.dev`. Postiz reachable (localhost:4007), Zernio auth OK,
daemon alive, all 5 channels live-routed, each IG account owns its `ig_user_id`.

---

## 0. Drop point & driver

- **Inbox:** `MohFlow-FanOps/01_inbox/` — drop the video here. (`.ingested/` holds processed drops.)
- **Catalogue:** `fanops ingest` (`ingest.ingest_drops` `ingest.py:205`) — idempotent, content-addressed
  (sha256 dedup), NO original filename (PII). Only CATALOGUES; publishes nothing.
- **Driver:** the launchd daemon (or a manual `fanops run`) runs `pipeline.advance()` (`pipeline.py:380`).
  Slow media work (transcribe/signals/keyframes/framing/render) runs **lock-free** in
  `produce._produce_one`; the single in-lock transaction adopts the warm on-disk artifacts.

---

## 1. The pass, stage by stage (with the observable state at each)

`advance()` DAG (`pipeline.py:391-442`): ingest (short txn) → lock-free prewarm → ONE main txn
(source→moments→hooks→render/caption→structural→crosspost) → reconcile (out-of-lock) →
publish (out-of-lock) → read-only summary.

| # | Stage | What fires (file:line) | Entity state after | Observable |
|---|-------|------------------------|--------------------|-----------|
| 0 | Catalogue | `ingest_drops` (`ingest.py:205`) + ffprobe (`ingest.py:102`) | `Source: catalogued` | `fanops status` sources+1 |
| 1 | Transcribe | whisper / faster-whisper subprocess (`transcribe.py:106/119`, run `:223`) | `catalogued→transcribed` | `04_agent_io/transcripts/<stem>.json` |
| 2 | Signals + keyframes | ffmpeg silence/scene (`signals.py:175`), frame stills (`keyframes.py:39`) | `transcribed→signalled` | `04_agent_io/signals/<sid>.json` |
| 3 | **Moment pick** (LLM · **opus**) | `request_moments`/`ingest_moments` (`moments.py:261/301`), frames attached | `signalled→…→picks_decided`; Moments born `picked`, **owner-stamped** | N moments (see fan-out) |
| 4 | **Moment hooks** (LLM · **opus**) | `_stage_moment_hooks` (`pipeline.py:130`) — per-owner on-screen retention hook | Moment `picked→decided`; `m.hook` set | hook text per moment |
| 5 | Render | ffmpeg per-account cut `render_account_cut` (`clip.py:869`), smart-framing crop (`framing.py`) | Moment `decided→clipped`; Clip born `rendered` | mp4 in `03_clips/<cid>.mp4` |
| 6 | **Captions** (LLM · **sonnet**) | `request_captions`/`ingest_captions` (`caption.py:188`), owner×platform scoped | Clip `rendered→captioned` (or `held`) | `clip.meta_captions[account/platform]`, ≤4 hashtags |
| 7 | Structural hooks | `_stage_structural_hooks` (opt-in; OFF here) | no-op when OFF | — |
| 8 | Crosspost mint | `crosspost_clips`/`_mint_surface_post` (`crosspost.py:250/155`) | Clip `captioned→queued`; Posts born **`awaiting_approval`** | `fanops status` awaiting_approval+ |
| 9 | **YOUR APPROVAL** | Studio **Review** tab → `Ledger.approve_post` (`ledger.py`) | Post `awaiting_approval→queued` | Review worklist |
| 10 | Publish | `publish_due`/`publish_post` → Postiz(IG)/Zernio(TikTok) (`run.py:408/454` via `_publish_one` `:222`) | `queued→submitting→submitted`→**`needs_reconcile`** | poster response, submission_id |
| 11 | Reconcile | `reconcile` back-fills permalink + Graph media_id (`reconcile.py`) | `needs_reconcile→published` + `public_url` | real reel/video URL |
| 12 | Verify-live | `fanops verify-live` → `confirm_post_live` (`meta_graph.py:264`) | (read-only) | `LIVE owner=<handle>` |
| 13 | Metrics | `track.pull_metrics` (`track.py:297`) → Graph insights | `published→analyzed` + lift | reach/views/saves |

**No-auto-publish invariant:** a Post is born `awaiting_approval` at ONE site (`crosspost.py:232`);
publish iterates `queued` only; `Ledger.approve_post` is the SOLE promoter. Nothing publishes — even
live, even with the daemon running — until you approve in Review.

---

## 2. Fan-out arithmetic — 1 video → X moments → Y clips → Z posts

**The 5 surfaces** (`accounts.surfaces()` `accounts.py:268`): one per (active account × platform).
All 5 accounts are single-platform → **exactly 5 surfaces**, all **9:16**:

| Surface | Platform | Backend | persona_id | own ig_user_id |
|---|---|---|---|---|
| markmakmouly | instagram | postiz | craft-curator | 17841414501372977 |
| perca.late | instagram | postiz | underground-zine | 17841435744776610 |
| cisumwolfhom | instagram | postiz | burner-bold | 17841450432031281 |
| backlikeineverleft | tiktok | zernio | underground-zine | — |
| hrmny-blog | tiktok | zernio | craft-curator | — |

**Multipliers (each pinned to code):**

| Multiplier | Value | Where |
|---|---|---|
| moments per video | **M — uncapped, model-driven** (no `_target_pick_count`; validity + within-owner overlap dedup only) | `moments.py:4-5,301`, `validate_pick` `:159`, `_drop_overlaps` `:145` |
| clips per moment (aspects) | **×1** — IG and TikTok both map to `Fmt.r9x16` | `pipeline.py:36` `_aspects_for`, `models.py:156-158` `PLATFORM_ASPECT` |
| surfaces total | **5** | `accounts.py:268`; accounts.json |
| posts per clip — casting ON (default) | **×1** — owner subset (single-owner moment → its owner surface) | `crosspost.py:166`, `casting.affinity_admits` `:22` |
| posts per clip — uncast moment (`affinities==[]`) | **×5** — fan-to-all | `casting.py:21`; `crosspost.py:250` loop |
| post state at birth | `awaiting_approval` | `crosspost.py:232` |

**Worked example:**
- **Typical (casting ON, every moment single-owner):** `1 video → M moments → M clips (×1 aspect) → M posts`
  (one per moment, on its owning account's surface). M=10 → **~10 awaiting_approval posts**, distributed
  across whichever accounts own each moment.
- **Worst case (every moment persona-blind, `affinities==[]`):** `→ 5·M posts` (each clip fans to all 5).
  M=10 → **50 posts** (max 10 per account).

The only >1 hard multiplier for these exact 5 accounts is the fan-to-all fallback; the aspect
multiplier is pinned at 1 because IG and TikTok share 9:16.

---

## 3. THE CASTING MODEL (this is the biggest correction vs the old codemaps)

**The dedicated LLM casting stage is GONE** (P11 teardown / MOL-152). `request_moment_casting`,
`ingest_moment_casting`, the durable `AccountSelection`, `SelectionFact`, and the `cast_moments`
heuristic are all removed. `casting.py` header (`casting.py:1-6`) states this explicitly.

The SOLE routing gate is now one pure predicate — `affinity_admits(cfg, moment, account)` (`casting.py:10`):
```
casting OFF        -> admit all 5           (A2 firewall: OFF ignores persisted affinities)
moment is None     -> DENY
affinities == []   -> fan-to-all            (a legit persona-blind-picked moment)
affinities != []   -> admit iff account in affinities  (single-owner: exactly the owner)
```
`Moment.affinities` is stamped **single-owner at PICK time** (`moments.py:340`, owner =
`(pick.personas or [None])[0]` `:330`), operator-overridable via Studio (P13). The SAME predicate
gates caption scope (`pipeline._owner_caption_surfaces` `:153`) and post minting (`crosspost.py:166`),
so caption-scope and post-minting can never drift.

**Persona→account note:** `affinities` stores the picking account's **handle**; `affinity_admits`
matches on handle equality. A persona shared by two accounts (e.g. `craft-curator` on markmakmouly +
hrmny-blog) produces two separate owner-stamped picks (one per active handle) — each moment still
admits to exactly its one owner handle, not to both accounts sharing the persona.

---

## 4. Per-account differentiation — the three forks

1. **Own rendered cut** — `render_account_cut` (`clip.py:869`), called when `wants_cut = bool(hook)`
   (`crosspost.py:126`). Cuts the source at the owner's `clip_profile` band + own framing, burns the
   owner's hook in one ffmpeg pass. Fails open to a shared-clip burn. The owner's spec was stamped onto
   the Moment at pick via `_stamp_owner_spec` (`moments.py:286`).
2. **Own on-screen hook** — authored per-moment (= per-owner) in PASS 2. `request_moment_hooks` sends
   only the moment's owner to the hook author (`_hook_personas_for_moment` `moments.py:384`, uses
   `m.affinities[0]`). Surfaced as `variant_hook` in Studio.
3. **Own caption** — requested per surface (owner × platform) via `_owner_caption_surfaces`
   (`pipeline.py:146`) → `request_captions`, one caption per surface. Crosspost looks up
   `clip.meta_captions[account/platform]` (`crosspost.py:190`); missing ⇒ no post for that surface.

**`FANOPS_CREATIVE_VARIATION` is documentation-only** — there is NO `getenv("FANOPS_CREATIVE_VARIATION")`
anywhere in `config.py` (the name survives only in comments + a hardcoded `creative_variation=False`
view-model default in `studio/views.py`). Per-account hook/render differentiation is an intrinsic
consequence of the owner-stamped-moment machinery, active whenever `account_casting` is ON. The real
runtime switch is `account_casting`, not a separate CV flag.

---

## 5. External-service map (every touch + its gate)

### The dryrun/live master gate (two independent gates, BOTH must pass for any real POST)
- `_post_provider` returns `"dryrun"` whenever `not cfg.is_live` (`run.py:160`) — global kill switch,
  un-bypassable by a per-channel provider; `publish_due` treats dryrun as a boundary (writes a preview
  sidecar, leaves the post `queued`, `run.py:433`).
- `get_poster` RAISES rather than build a `DryRunPoster` when live + resolved backend is dryrun
  (`post/__init__.py:19`).
- `cfg.is_live` (`config.py:232`) from `FANOPS_LIVE`; may be written ONLY by `studio/golive.go_live`.
- Per-channel provider from accounts.json: IG→postiz, TikTok→zernio.

### Service table

| Service | Caller (file:line) | Stage | Gate / config | Success | Failure |
|---|---|---|---|---|---|
| **LLM — moments** (`claude -p`) | `responder.py:67`→`llm.py:103,117` | pick (vision) | `FANOPS_RESPONDER=llm`; **opus** (`config.py:84`); frames granted | `MomentDecision` JSON; provenance line in run.log | gate pending+quarantine; `frames_unread` breadcrumb |
| **LLM — moment_hooks** | same | hook (vision) | `=llm`; **opus** | `MomentHookDecision`; hook stamped | pending; `hook_frames_unread:True` |
| **LLM — captions** | same | caption (text) | `=llm`; **sonnet** (NOT opus); no frames | `CaptionSet` (caption + ≤4 tags) | pending/quarantine |
| LLM auth | `llm.py:9-21` | — | operator's existing `claude` login (NOT `ANTHROPIC_API_KEY`); `--strict-mcp-config --allowedTools ""` | structured_output JSON | `ToolchainMissingError`/`LlmRateLimitError`(429/503/529) typed |
| ~~casting LLM~~ | **REMOVED** (P11/MOL-152) | — | `affinity_admits` pure predicate | — | — |
| **whisper/faster-whisper** | `transcribe.py:106/119`, run `:223` | transcribe | extra `[asr]` (faster-whisper) or `[transcribe]` (legacy); `FANOPS_ASR_MODEL`; timeout 2700s×scale | transcript JSON, `meta.transcribed=True` | `Source: error` + reason; distinguishes []-no-speech vs None-not-run |
| **ffmpeg** signals | `signals.py:131,175` | signals | ffmpeg on PATH | detect sidecar, `signalled` | fail-open, may quarantine |
| **ffprobe** | `ingest.py:102`, `clip._probe_duration` | catalogue | ffprobe on PATH | duration/dims | rc=124 sentinel, fail-open |
| **ffmpeg** keyframes | `keyframes.py:39,137` (30s) | before vision gates | ffmpeg on PATH | JPG frames attached | fail-open → [] → text-only gate |
| **ffmpeg/OpenCV** framing | `framing.py:152` | render | extra `[framing]` (OpenCV); lazy, fail-open | subject-aware 9:16 crop | fail-open to centered crop |
| **ffmpeg** render | `clip.py:334,406,442,689,857,869`; overlay `overlay.py:385` | render | ffmpeg; optional `[compose]` MoviePy | mp4, `ClipState.rendered` | `ClipState.error`, per-clip quarantine |
| **Postiz** media upload / R2 mirror | `postiz.postiz_upload_media` `postiz.py:224` → `_mirror_media_to_r2` `:156` (SigV4 PUT) → `_postiz_upload_from_url` `:168`; else multipart `/upload` | publish (IG) | R2 path needs `FANOPS_MEDIA_PUBLIC_BASE`+`R2_*`; `POSTIZ_URL`,`POSTIZ_API_KEY` | returns public HTTPS `"id\|path"` | `PostizAuthError` 401 (halts); RuntimeError ≥300 (body withheld) |
| **Postiz** IG publish | `PostizPoster.publish` `postiz.py:364` `POST /public/v1/posts` ← `run._publish_one` `run.py:222` | publish | `is_live` AND provider=postiz; rate `FANOPS_POSTIZ_PUBLISH_PER_MIN` (4); `postiz_lifecycle.ensure_up` starts stack | 2xx → `submitted`+submission_id; **permalink ALWAYS None** → parks `needs_reconcile` | 401→halt; 5xx/net→`needs_reconcile` (never re-POST); other 4xx→`failed` |
| **R2 host rewrite** | `postiz.rewrite_media_base` `postiz.py:102`, applied `:374,183,243` | publish | `FANOPS_MEDIA_PUBLIC_BASE` (`config.py:287`); unset→pass-through | loopback URLs → public R2 base so IG can fetch | if unset, IG can't fetch loopback (SSRF-blocked localhost) |
| **Zernio** media upload | `zernio_upload_media` `zernio.py:123` (token then multipart) | publish (TikTok) | `ZERNIO_API_KEY`,`ZERNIO_URL`; per-account token; size cap | hosted url | 401→`ZernioAuthError`; ≥300→RuntimeError |
| **Zernio** TikTok publish | `ZernioPoster.publish` `zernio.py:227` `POST /posts` (`publishNow:true`) | publish | `is_live` AND provider=zernio | 2xx → `submitted`+submission_id | 401→halt; 5xx/net→`needs_reconcile`; 4xx→`failed` |
| **Postiz** T10 preflight | `doctor._postiz_reach_check` `doctor.py:109` → `postiz_health_probe` → `GET /public/v1/integrations` | pre-publish | only if `POSTIZ_API_KEY` set; probes PAST nginx | `PostizHealth(True,200)` | 401 rejected-key / 5xx unhealthy (key never echoed) |
| **Zernio** T10 | `doctor._zernio_reach_check` `doctor.py:128` → `GET /accounts` | pre-publish | `ZERNIO_API_KEY` | auth ok | 401/error fail-closed |
| **Meta Graph** verify-live | `resolve_ig_media` `meta_graph.py:241` via `confirm_post_live` `:264`; callers `cli.py:181`, `reconcile.py:527` | verify | `META_GRAPH_TOKEN`+ per-account `ig_user_id`; scope `instagram_basic`+`pages_read_engagement` (or `instagram_business_basic`) | `{exists,permalink,username}` → confirmed | fail-closed → None → `{confirmed:False}` |
| **Meta Graph** media_id reconcile | `list_user_media` `:200` / `enumerate_scoped_media` `:301` ← `reconcile.resolve_media_ids` | reconcile | same creds; spends NO hashtag budget | media_id+product_type stamped, `public_url` back-filled | fail-open → []; `ig_media_id_unresolved` note |
| **Meta Graph** insights | `media_insights` `:367` ← `track.py:218` GraphInsightsClient | metrics | scope **`instagram_manage_insights` REQUIRED** (the one external gate) | `{reach,views,saves,...}`; clears `insights_blocked` | transient→None (keeps snapshot); refusal→`MetaInsightsScopeError` LOUD |
| **Meta Graph** hashtag research | `hashtag_id`/`trend_score`/`harvest_cooccurring` (`meta_graph.py:160+`) | NOT on publish path | `FANOPS_HASHTAG_TRENDS` (ON); 30-tag/7-day budget; scope `instagram_basic`+"IG Public Content Access" App Review | trend scores | fail-soft/fail-closed |
| **Meta Graph** token expiry T9 | `debug_token_expiry` `:96` ← `doctor.py:291` | pre-publish | any resolvable Meta token | ok+expiry epoch | expired→FAIL; <10-day→WARN |

### Six load-bearing notes for the live run
1. **Opus-pinned:** moments + moment_hooks = opus; captions = sonnet. Enabled only by `FANOPS_RESPONDER=llm`. The casting LLM gate no longer exists.
2. **Publish fires only when BOTH gates pass:** `is_live` true AND per-channel provider resolves to postiz/zernio; post must be `queued` (approved) and due.
3. **R2 required for real IG:** without `FANOPS_MEDIA_PUBLIC_BASE`, Postiz returns loopback paths IG can't fetch (localhost SSRF-blocked). The mirror is **inline at publish time** in `postiz.py:_mirror_media_to_r2` — there is NO separate media-sync job *in the repo* (the host-level `com.fanops.media-sync` launchd job pre-mirrors uploads to R2 as a convenience; the publish path itself does the rewrite).
4. **Postiz publish parks `needs_reconcile` first** (`_postiz_permalink` always None) — the permalink is back-filled by `reconcile`, the Graph `media_id` stamped by `resolve_media_ids`. Do NOT expect `published` directly off the publish 2xx.
5. **Graph scopes differ by call:** verify-live/reconcile need `instagram_basic`+`pages_read_engagement`; **insights additionally need `instagram_manage_insights`** (a missing insights scope raises `MetaInsightsScopeError` + sets `insights_blocked`, but verify-live/reconcile still work).
6. **T10 preflight** = `fanops doctor`: real Postiz `/integrations` probe (past nginx) + Zernio `/accounts` + Meta token `debug_token` expiry. Run before the live test.

---

## 6. The live-test walkthrough (what to run, what to watch)

```bash
# 0. Preflight — must be green
./.venv/bin/fanops doctor              # T9 token + T10 Postiz/Zernio reachability
./.venv/bin/fanops status              # baseline post counts

# 1. Drop the video, catalogue it
cp <your-video>.mp4 MohFlow-FanOps/01_inbox/
./.venv/bin/fanops ingest              # -> Source: catalogued
./.venv/bin/fanops status              # sources +1

# 2. Drive the pipeline (or let the daemon do it)
./.venv/bin/fanops run                 # transcribe->signals->moments->hooks->render->captions->crosspost
./.venv/bin/fanops status              # awaiting_approval +N   (posts born, UNPUBLISHED)

# 3. Inspect the fan-out before approving
#    Studio Review tab (fanops studio) — moment-grouped, per-account cut/hook/caption chips
#    OR read the ledger: which moments, which owners, how many posts per account

# 4. APPROVE the ones you want live (human gate — nothing publishes without this)
#    Studio Review -> Approve selected   => awaiting_approval -> queued

# 5. Publish + reconcile + verify
./.venv/bin/fanops reconcile           # back-fills permalink + media_id
./.venv/bin/fanops verify-live         # LIVE owner=<handle> per post (Graph/oEmbed truth)
```

**Per-stage tripwire:** if a stage stalls, `fanops status` shows the stuck gate as
`awaiting_moments` / `awaiting_moment_hooks` / `awaiting_captions`. (Known observability gap: a stuck
gate on the removed `moment_casting` kind is NOT surfaced — but that gate no longer exists, so it's moot.)

---

## 7. Ground-truth check run this session (why a fresh test is the right move)

The ledger was rebuilt (MOL-156) since MOL-116/MOL-126 were written. Current: `posts=19, published=2,
failed=0, queued=0`. Graph/oEmbed cross-check found **all 5 accounts already live on-platform**, but
surfaced two real verification defects (candidates for follow-up tickets — NOT blockers for a fresh run):

- **markmakmouly ledger row `post_4eb7c0802e79` stores a phantom media_id** (`17946154791226933` /
  `DaNlhchDUum`) that returns Graph 400 "does not exist". The account IS live (reels `DaY8y2DCiuf`
  2026-07-04, `DZvZ8Itkaxz`) but the stored media_id/permalink don't match either → verify-live reports
  it `unconfirmed`. perca.late (3) and cisumwolfhom (3) ledger media_ids match Graph exactly → LIVE.
- **`verify_tiktok_permalink` owner-match too strict** (`post/metrics.py`) — compares the FanOps account
  name against the oEmbed author, but the real TikTok handles differ (`backlikeineverleft`→`@backlikeineveeft`
  "Left Never I Like Back"; `hrmny-blog`→`@wahed_bared`). All 13 TikTok posts are live (oEmbed 200) but
  report `unconfirmed`. Fix = map FanOps account → real TikTok handle before the author compare.

Net: `fanops verify-live` reports 6/19 LIVE, but the true count is higher — the gap is these two data/
comparison bugs, not publish failures.
