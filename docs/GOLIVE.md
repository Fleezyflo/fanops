# FanOps — Go-Live Runbook

How to take FanOps from the offline `dryrun` default to actually publishing. Pick **one** of three
publishing paths. Everything before the publish step is identical and runs offline with no account,
key, or service.

> The honest constant across all paths: **you must onboard each fan account on each platform
> yourself** (create it, connect it via the platform's OAuth). That "platform tax" is unavoidable on
> any publisher — paid or free. And coordinated multi-account posting is detectable by the platforms
> regardless of how you publish (see `00_control/RISK.md`). No tooling removes either fact.

---

## 0. Prerequisites (all paths)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,transcribe,studio]'      # transcribe = whisper; studio = the web cockpit
brew install ffmpeg                             # macOS; ffmpeg >= 6 (ffprobe ships with it)
fanops doctor                                   # read-only health screen — fix anything it flags
```

`fanops doctor` checks the toolchain, `accounts.json`, the poster + key for your chosen backend, and
prints the exact next action for anything missing. Run it whenever a path step fails.

## 1. Produce content offline (no publishing yet)

Either from the **Run** tab in `fanops studio` (drop files → Ingest → Run pass → answer Gates → Run
pass …) or from the CLI:

```bash
cp ~/footage/*.mp4 MohFlow-FanOps/01_inbox/ && fanops ingest   # or: fanops pull <url>
fanops advance        # transcribe → moments gate
fanops respond        # (llm responder) or answer the moments gate in Studio → Gates
fanops advance        # render clips → captions gate
fanops respond        # answer captions
fanops advance        # crosspost → queue; in dryrun this writes payloads, posts NOTHING
```

Finished clips are real `.mp4`s in `MohFlow-FanOps/03_clips/` and visible in **Studio → Review**.
At this point you have a full content pipeline with zero external dependencies. Now choose a path.

---

## Path A — Blotato (paid SaaS, full learning loop)

The only path with the automated metrics/learning loop (lift-by-variant, amplify, reconcile).

1. Create the fan accounts; connect each in the Blotato dashboard; copy each **numeric** `account_id`.
2. Put them in `MohFlow-FanOps/00_control/accounts.json` with `"status":"active"`.
3. `.env`: `FANOPS_POSTER=rest` and `BLOTATO_API_KEY=...`.
4. **Validate live, safely, before trusting anything** — the `cutover` harness:
   ```bash
   fanops cutover auth                          # 1. prove the key authenticates (read-only)
   fanops cutover post <THROWAWAY_ID> --i-understand-this-posts-to-a-real-account
   #   2. ONE post, hardcoded 2099 schedule (delete it in the Blotato UI before then)
   fanops cutover metrics <submission_id>       # 3. pull the row, reconcile fields vs track._W
   fanops cutover lift <submission_id>          # 4. compute one real lift_score end-to-end
   ```
   Step 3's reconciliation tells you which lift weights are real; re-tune `00_control/tuning.json`
   `lift_weights` before enabling the learning loop. Until `cutover metrics` runs, the speculative
   learning toggles (`FANOPS_VARIANT_AMPLIFY/UCB/TRANSFER`) stay inert by design (OFF-until-proven).
5. Go live: `fanops run --base-time <past-T>` (cron/launchd entry point; emits a heartbeat each run).

## Path B — Postiz (free, self-hosted, no learning loop)

Same role as Blotato, no paid SaaS. You run the open-source [Postiz](https://github.com/gitroomhq/postiz-app)
yourself; FanOps posts to it.

1. **Self-host Postiz** (Docker — see its README). Get a public API key: Settings → Developers → Public API.
2. In Postiz, **connect each fan account** (this is where each platform's OAuth/app-review happens).
   Copy each connection's **integration id** (from `GET /public/v1/integrations`).
3. Put each integration id into `accounts.json`'s `account_id` (same field Blotato uses for its id),
   `"status":"active"`.
4. `.env`: `FANOPS_POSTER=postiz`, `POSTIZ_URL=https://your-instance`, `POSTIZ_API_KEY=...`. Run
   `fanops doctor` to confirm both are set.
5. Go live: `fanops advance` / `fanops run`. FanOps uploads each clip to Postiz and schedules the post.

**Postiz caveats (by design):** the Blotato-only learning loop, automated `reconcile`, and `cutover`
do not run on this backend. A post left in `needs_reconcile` after a network blip is surfaced in
`fanops status` + the digest and cleared by hand: `fanops resolve <post_id> published|failed`.

## Path C — Manual (free, zero service)

No external service at all. FanOps produces the clips + captions; you post them by hand.

- **Studio → Publish**: each queued post shows the clip (download) + the caption (copy) + a
  **Mark posted** button (optionally paste the live URL).
- Or CLI: `fanops publish-queue` lists the worklist; post each by hand, then
  `fanops resolve <post_id> published --url <live-url>`.

Stays in `dryrun` (`FANOPS_POSTER=dryrun`) — nothing is ever transmitted automatically.

---

## The zero-terminal flow (Studio)

`pip install -e '.[studio]'` → `fanops studio` → http://127.0.0.1:8787:

**Footage** (approve discover candidates) → **Run** (Ingest → Run pass) → **Gates** (answer moments,
then captions, re-running a pass between) → **Review** (watch the cuts) → **Run** again to publish
due (a confirm checkbox guards a live backend), or **Publish** to post by hand.

## Rollback / safety

- The default `dryrun` backend transmits nothing — exercise the entire flow offline first.
- `advance`/`run` refuse to start (exit 2) if the env would publish credentialless nothing (preflight).
- The `cutover` probe post is 2099-scheduled and deletable before it could ever publish; its state
  lives in `00_control/cutover.json`, never the ledger.
- Every publish is crash-safe (submit intent persisted before the network call) and never blind-retries
  an ambiguous failure (no double-post).
