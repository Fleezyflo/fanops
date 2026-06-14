# Setting up Postiz for FanOps

**Postiz** is the open-source social-publishing layer FanOps posts through — and (the load-bearing
reason) the one that exposes the per-post analytics the learning loop feeds on. FanOps never logs
into Instagram/TikTok/etc. itself: **Postiz holds each channel's auth**, FanOps just calls Postiz's
public API to publish and to read back metrics.

You set this up **once**. After it, everything happens in the FanOps Studio — you never touch a
terminal or `.env` to publish.

> New here? Do this guide first, then follow [`RUNBOOK.md`](RUNBOOK.md) top-to-bottom for the full
> upload → publish → learn walkthrough.

---

## Hosted vs self-hosted

| Path | Cost | You run a server? | When |
|---|---|---|---|
| **Self-hosted (Docker)** — *recommended* | Free | Yes (needs Docker + a public URL) | You want the free, in-your-control path. **This is the primary path.** |
| Hosted (postiz.com) — *alternative* | Subscription | No | You'd rather not run any infrastructure. |

The rest of this guide leads with **self-hosted Docker**. If you pick hosted, skip the Docker section
and jump to [Connect each fan channel](#connect-each-fan-channel) — the Public-API step is identical.

---

## Create your Postiz account

- **Self-hosted (primary):** stand up your own instance → [Self-hosted: Docker standup](#self-hosted-docker-standup).
- **Hosted (alternative):** sign up at [postiz.com](https://postiz.com), reach the dashboard, then
  continue at [Connect each fan channel](#connect-each-fan-channel).

---

## Self-hosted: Docker standup

Postiz ships a Docker Compose setup. Follow **Postiz's own README** for the authoritative, current
compose file and full `.env` — it is the source of truth and it changes; this guide only calls out
what matters for FanOps.

```bash
git clone https://github.com/gitroomhq/postiz-app
cd postiz-app
cp .env.example .env          # then edit .env per Postiz's README
docker compose up -d          # start it; `docker compose ps` shows the containers
```

In `.env`, the keys that matter:

- **`MAIN_URL`** — the **public** URL your instance is reachable at (e.g. `https://my-postiz.example.com`).
  ⚠ **This MUST be a public URL, not `localhost`** — the social platforms call it back during OAuth,
  and `localhost` can never complete that handshake. For purely-local *testing* (no real channel
  connect) `http://localhost:3000` works, but real channels need a public `MAIN_URL`.
- **`DATABASE_URL`** / **`SECRET_KEY`** (and the other secrets Postiz's README lists) — set per the
  README. These are Postiz's own secrets; FanOps never sees them.

Open `MAIN_URL` in a browser — the Postiz dashboard should load. If it doesn't, see
[Troubleshooting](#troubleshooting).

---

## Connect each fan channel

In the Postiz dashboard, connect each fan account (Instagram, TikTok, YouTube, …). This is where each
platform's OAuth / app-review happens — the unavoidable "platform tax": **you must onboard each
account on each platform yourself, on any publisher, paid or free.** No tooling removes that step.

Each connected channel becomes an **integration** in Postiz. A handle's Instagram and its TikTok are
**two different integrations** — you'll map each one separately in FanOps later.

---

## Get your Public API key + base URL

In Postiz: **Settings → Developers → Public API** (same location on self-hosted and hosted).

1. Copy the **API key**.
2. Note your instance **base URL** — for self-hosted this is **your own `MAIN_URL`**
   (e.g. `https://my-postiz.example.com`), **never** `https://api.postiz.com`.

> Use placeholders when noting these down, e.g. `POSTIZ_API_KEY=<paste-your-postiz-public-api-key>`.
> Never paste a real key into a doc, a chat, or a commit.

---

## What FanOps needs (and where you paste it)

Exactly two values:

| Value | What it is |
|---|---|
| `POSTIZ_URL` | Your instance base URL — your own `MAIN_URL` (e.g. `https://my-postiz.example.com`). **Never a `*.postiz.com` URL.** |
| `POSTIZ_API_KEY` | The Public API key from Settings → Developers → Public API. |

You paste them into the **Studio → Go Live tab → "1 · Connect Postiz"** (fill **Postiz URL** and
**API key**, click **Save & test**) — **not** into `.env` or the CLI. FanOps dual-writes them durably
for you and tests the key against your instance on the spot.

**The key is write-only.** The Studio stores and tests it but **never shows it back** — the field is a
password input, and once set its placeholder reads `•••••••• (set — leave blank to keep)`. FanOps
surfaces only a `key_set` boolean, never the value. (To change just the URL later, leave the key
blank.)

What FanOps does with these: it calls `{POSTIZ_URL}/public/v1/…` — `integrations` (list your channels),
`upload` + `posts` (publish a clip), and `analytics/post/{postId}` (read per-post metrics that feed the
learning loop). Header is `Authorization: <your key>`.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard / container not reachable | `docker compose ps` (is it up?) · `docker compose logs postiz` (what failed?) |
| Channel OAuth callback fails | `MAIN_URL` must be a **public** URL — `localhost` can't complete platform OAuth |
| "Save & test" → *Postiz auth failed — check POSTIZ_API_KEY* | The key was rejected. It was still saved — re-enter the correct key (Settings → Developers → Public API) |
| "Save & test" → *could not reach Postiz at …* | The URL doesn't point at your running instance — check `POSTIZ_URL` is your `MAIN_URL` and the instance is up |

---

## Next

You have Postiz running, channels connected, and your API key + base URL in hand. Now follow
[`RUNBOOK.md`](RUNBOOK.md) for the full cold walkthrough: upload a video → review → go live → publish →
validate learning → see real per-variant lift.
