# Connecting a YouTube channel (Shorts) via Postiz

FanOps publishes through **Postiz only** (the Meta Graph token is trends-only — no publish path).
YouTube is onboarded exactly like Instagram: connect the channel in Postiz over a public HTTPS
callback, then map its integration id into `accounts.json`. This is the YouTube analog of
[INSTAGRAM_CONNECT.md](INSTAGRAM_CONNECT.md) — read that for the deeper "why" on the funnel and the
recreate rule.

YouTube content ships as **Shorts**: FanOps renders the youtube surface at **9:16** and caps it at
**180s** (`models.PLATFORM_ASPECT` / `PLATFORM_MAX_SECONDS`). YouTube auto-classifies a vertical
≤3-min upload as a Short — there is no Shorts flag in the API or in Postiz.

## "Connected" = three states (same as Instagram)

1. **App-authorized** — a Google Cloud OAuth **web** client exists and its id/secret are in the Postiz
   container env (`YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`).
2. **Channel minted** — the operator runs the Postiz OAuth "Allow" once; Postiz exchanges the code and
   stores the channel as an `Integration` row. Needs a **public HTTPS callback** (the Tailscale Funnel
   `https://molhams-macbook-pro-2.tail72be94.ts.net` → `localhost:4007`; Google refuses plain
   `localhost` redirects).
3. **Mapped** — `accounts.write_integration(cfg, '<handle>', 'youtube', '<postiz_channel_id>')`.

## Setup (status)

### Google Cloud (project `theta-index-500313-b8`)
- [x] OAuth **web** client created (`724576967582-…apps.googleusercontent.com`).
- [ ] **Enable YouTube Data API v3** (+ YouTube Analytics API for lift later) on the project.
- [ ] **Authorized redirect URI** — the registered value is currently the bare domain
  `https://molhams-macbook-pro-2.tail72be94.ts.net`, which will NOT match Postiz's callback. Add the
  **full path**:
  ```
  https://molhams-macbook-pro-2.tail72be94.ts.net/integrations/social/youtube
  ```
  (Google requires the redirect_uri to be byte-identical to what Postiz sends. If a connect attempt
  dies at the callback, read the exact `redirect_uri=` param from the Google authorize URL Postiz
  generates and register *that* — the lesson from the Instagram connect.)
- [ ] **OAuth consent screen** = External; add yourself as a **Test user** (publishing/verifying the
  app is NOT required for a single operator). NB: in Testing mode, refresh tokens **expire every 7
  days** — re-consent weekly, or push the app to Production (needs Google verification) to stop that.

### Postiz container
- [x] `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` wired into
  `~/postiz-selfhost/postiz-docker-compose/docker-compose.yaml` (backup at `*.bak.beforeyoutube`).
- [x] Container **recreated** (`docker compose up -d` — a `restart` does NOT reload env; only a
  recreate does). Verified healthy with both vars set.
- The downloaded `client_secret_*.json` was moved OUT of the repo into `~/postiz-selfhost/` and the
  pattern was added to `.gitignore` — never commit a client secret.

### Connect + map (after the redirect URI is fixed)
1. Open the Postiz dashboard, **Add channel → YouTube**, complete the Google "Allow" (you'll see the
   "unverified app" screen in Testing mode — proceed), then **select the channel**.
2. The channel appears as a Postiz integration with an id (`GET /public/v1/integrations`).
3. Map it: in the Studio **Go Live** tab add the account with `platforms` including `youtube` and map
   the youtube channel, or `accounts.write_integration(cfg, '<handle>', 'youtube', '<id>')`.
4. Route youtube to the postiz backend (the postiz global already bridges youtube), confirm go-live.

## How FanOps fills the YouTube post

| YouTube field | Source |
|---|---|
| `snippet.title` (REQUIRED 2–100) | the account's burned hook (`Post.variant_hook`), floored to `FANOPS_ARTIST_NAME` when a surface has no hook |
| `snippet.description` | the post caption (`Post.caption`) |
| `snippet.tags` | the post hashtags (`#` stripped, deduped, bounded < Postiz's ~500 cap) |
| `status.privacyStatus` | `public` (default) |
| `status.selfDeclaredMadeForKids` | `no` |
| video | the single rendered 9:16 mp4 |

Postiz **rejects a titleless YouTube post** (the YoutubeSettingsDto requires `title`+`type`; there is
no content fallback) — FanOps always sends a title, so this is handled.

## Live probe (operator-gated — the one thing source can't settle)
Publish ONE test Short (set privacy to `unlisted` for the probe if you prefer) and confirm:
- it publishes (Postiz's YouTube settings shape matches this Postiz version),
- the upload renders as a **Short** (9:16, ≤180s) on the channel,
- metrics/reconcile flow back.

## Gotchas
- **Recreate, not restart** — env changes need `docker compose up -d`.
- **Redirect must be byte-identical** — the bare-domain redirect won't work; use the full
  `/integrations/social/youtube` path.
- **7-day token expiry in Testing mode** — verify the Google app to make it durable.
- **Postiz YouTube is flakier than IG** — connect may need retries; a failed upload can be silent, so
  reconcile against the actual channel rather than trusting a "queued" state.
