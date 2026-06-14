# FanOps — Go-Live Runbook

How to take FanOps from the offline `dryrun` default to actually publishing. Pick **one** of two
publishing paths. Everything before the publish step is identical and runs offline with no account,
key, or service.

> **Just want the linear first-run script?** See [`RUNBOOK.md`](RUNBOOK.md) — this file is the
> "choose a path + safety" reference; the runbook is the step-by-step you follow once.

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

Either from the **Run** tab in `fanops studio` (Upload video → Ingest inbox → Prepare everything) or
from the CLI:

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

## Path A — Postiz (free; publishes AND learns)

The full path: Postiz publishes your clips **and** exposes the per-post analytics that drive the
learning loop (lift-by-variant, amplify, reconcile). No paid SaaS.

1. **Stand up Postiz** (self-hosted Docker — the primary path) and get your Public API key + base URL:
   follow [`POSTIZ_SETUP.md` → Self-hosted: Docker standup](POSTIZ_SETUP.md#self-hosted-docker-standup).
2. **Connect Postiz in the Studio** — Go-Live tab → **"1 · Connect Postiz"**: paste your **Postiz URL**
   + **API key** → **Save & test**. The key is stored write-only (never shown back).
3. **Add + map accounts in the Studio** — **"2 · Add an account"** (handle + platforms), then
   **"3 · Map each channel to Postiz"** (Refresh from Postiz → pick the integration per channel → Save).
   Every channel maps to its own Postiz integration — a handle's Instagram and TikTok are different
   integrations. **No JSON editing**: the mapping lives entirely in this tab.
4. **Go live** — Go-Live → tick the confirm box → **GO LIVE — publish for real**. The banner flips to
   `● LIVE (postiz)`. Publish from the Run tab (**Prepare everything**, confirm checkbox) or `fanops run`.
5. **The learning loop runs on Postiz** — real `analytics/post` metrics → `lift_score` → the **Lift**
   tab populates (after the platform reports, hours later).
6. **Unfreeze speculative learning** — Go-Live → **"5 · Validate learning"** posts one operator-selected
   throwaway probe, reconciles its real labels, and sets `metrics_confirmed`. Until then variant-amplify
   stays inert by design (OFF-until-proven).

The full cold walkthrough is [`RUNBOOK.md`](RUNBOOK.md).

**Go autonomous (optional):** `fanops autopilot` enables the `llm` responder durably (the pipeline
answers its own moment/caption gates) **and** installs the supervising launchd daemon (every 10m,
survives logout, restarts on crash). It is dryrun-safe — it only publishes once you've gone live above.
The pieces by hand: `fanops daemon install --interval 10m` / `fanops daemon status` / `fanops daemon stop`.

## Path B — Manual (free, zero service)

No external service at all. FanOps produces the clips + captions; you post them by hand. No metrics,
no learning loop by definition.

- **Studio → Publish**: each queued post shows the clip (download) + the caption (copy) + a
  **Mark posted** button (optionally paste the live URL).
- Or CLI: `fanops publish-queue` lists the worklist; post each by hand, then
  `fanops resolve <post_id> published --url <live-url>`.

Stays in `dryrun` (`FANOPS_POSTER=dryrun`) — nothing is ever transmitted automatically.

---

## The zero-terminal flow (Studio)

`pip install -e '.[studio]'` → `fanops studio` → http://127.0.0.1:8787:

**Run** (Upload video → Ingest inbox → Prepare everything) → **Gates** (answer moments, then captions,
re-running a pass between — *only if you skipped `fanops autopilot`*) → **Review** (watch the cuts) →
**Go Live** (Connect Postiz → Add account → Map channels → GO LIVE) → **Run** again to publish due (a
confirm checkbox guards a live backend), or **Publish** to post by hand → **Go Live → Validate
learning** (unfreeze the loop) → **Lift** (real per-variant lift, once the platform reports).

## Rollback / safety

- The default `dryrun` backend transmits nothing — exercise the entire flow offline first. An
  unknown/typo'd `FANOPS_POSTER` falls back to dryrun (never a false LIVE banner).
- `advance`/`run` refuse to start (exit 2) if the env would publish credentialless nothing (preflight).
- **Back to dryrun anytime:** Go-Live → **Back to dryrun (stop publishing)** — the safe direction, no
  confirm needed.
- The **Validate learning** probe (and the underlying `cutover`) posts one 2099-scheduled throwaway,
  deletable before it could ever publish; its state lives in `00_control/cutover.json`, never the ledger.
- Every publish is crash-safe (submit intent persisted before the network call) and never blind-retries
  an ambiguous failure (no double-post).
