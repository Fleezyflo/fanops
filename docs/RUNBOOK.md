# FanOps Runbook — your first end-to-end run

One linear walkthrough: a brand-new video → reviewed → published live on Postiz → real per-variant
lift in the Lift tab. Follow it top-to-bottom **once**; after that the daily loop is just steps 2–5 +
9. Everything but install and the Postiz standup happens in the browser — no terminal, no JSON editing.

**Before you start:** stand up Postiz and get your API key + base URL via
[`POSTIZ_SETUP.md`](POSTIZ_SETUP.md). Choosing a publishing path / safety reference lives in
[`GOLIVE.md`](GOLIVE.md). This file is the script you actually run.

Each step below names **the exact control**, **what success looks like**, and **which readiness check
turns green** (Go-Live tab → "4 · Readiness", which mirrors `fanops doctor`).

---

### 0 · Install + stand up Postiz

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[studio,transcribe]'      # studio = the web cockpit; transcribe = whisper
fanops doctor                              # read-only health screen — fix anything it flags
fanops studio                              # open http://127.0.0.1:8787
```

Stand up Postiz per [`POSTIZ_SETUP.md`](POSTIZ_SETUP.md) and have your `POSTIZ_URL` + `POSTIZ_API_KEY`
ready. · **Success:** `fanops doctor` shows ffmpeg/ffprobe/whisper ✓ and the Studio loads.

### 1 · Connect Postiz

Go-Live tab → **"1 · Connect Postiz"** → paste **Postiz URL** + **API key** → **Save & test**.
· **Success:** a green result — *connected `<your-url>`*. (The key is stored write-only; you'll never
see it again — the field shows `•••••••• (set — leave blank to keep)`.)

### 2 · Upload footage

Run tab → **Upload video** (pick one or more video files) → then **Ingest inbox**.
· **Success:** the result reads *N saved, 0 skipped*, and the **sources** count goes up. (No Finder
needed — the file streams into `01_inbox/` and is catalogued.)

### 3 · Prepare the cut

Run tab → **Prepare everything**. · **Success:** finished clips appear in the **Review** tab — the
system writes its own captions and finishes the clips; no gates to answer by hand.

> Auto-answering uses your `claude` login. Run `fanops autopilot` **once** to turn it on. Without it,
> the moment/caption gates wait for you in the **Gates** tab — see step 4.

### 4 · Answer gates (only if you skipped autopilot)

Gates tab → answer the **moments** gate → Run tab **Prepare everything** → answer the **captions** gate
→ **Prepare everything** again. · **Success:** clips render and land in **Review**.

### 5 · Review the cut

Review tab → watch each rendered clip; tweak the caption if you want. · **Success:** you're happy with
the clip + caption.

### 6 · Add + map your accounts

Go-Live tab → **"2 · Add an account"**: type the **Handle**, tick the **platform** checkboxes, click
**Add account**. Then **"3 · Map each channel to Postiz"**: click **Refresh from Postiz**, pick the
right integration for each channel (a handle's Instagram and TikTok are different integrations),
**Save channel mappings**. · **Success:** each row shows its mapped integration.

### 7 · Check readiness

Go-Live tab → **"4 · Readiness"**. · **Success:** *accounts.json valid (every active channel mapped to
an id)* ✓. (The Postiz-specific checks appear after you go live — step 8.)

### 8 · Go live

Go-Live tab → tick **⚠ I understand this publishes to REAL accounts** → **GO LIVE — publish for real**.
· **Success:** the banner flips to **● LIVE (postiz)**, and "4 · Readiness" now also shows
*POSTIZ_URL + POSTIZ_API_KEY set* ✓ and *Postiz learning ready* (still ✗ until step 10).

### 9 · Publish

Run tab → **Prepare everything** (or **Run one pass (advanced)**). On a live backend each carries a
confirm checkbox **⚠ publish to REAL accounts (backend: postiz)** — tick it, then run. Equivalently
`fanops run` from the CLI. · **Success:** the **published** count goes up; the post is live on Postiz.

### 10 · Validate learning

Go-Live tab → **"5 · Validate learning"**: pick the throwaway channel to probe, tick **⚠ Validate
publishes ONE real throwaway post…**, click **Validate learning (posts a probe)**. This posts one
operator-selected throwaway, reconciles its real analytics labels, and writes `cutover.json
metrics_confirmed`. · **Success:** *Postiz learning ready* ✓ in Readiness — speculative learning
(variant-amplify) may now act on real lift. (Operator-gated; never auto-fires.)

### 11 · See Lift

Lift tab. · **Success:** after the platform reports metrics (hours, not minutes), the tab populates
with real per-variant lift. Until then it shows an honest empty state — that's expected, not a bug.

---

## If a step stalls

| Step | First thing to check |
|---|---|
| Any | `fanops doctor` / Go-Live "4 · Readiness" — it names the exact missing piece |
| 1 (Connect) | *auth failed* → re-enter the key · *could not reach* → fix `POSTIZ_URL` (see [`POSTIZ_SETUP.md`](POSTIZ_SETUP.md#troubleshooting)) |
| 2 (Upload) | only video files are accepted; oversize uploads re-render the panel with a "too large" note |
| 3/4 (Prepare) | no autopilot → the gates wait in the **Gates** tab |
| 9 (Publish) | preflight refuses a live run with no key — Readiness shows what's unset |
| 11 (Lift) | empty is normal until the platform reports; come back later |
