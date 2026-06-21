<!-- Generated: 2026-06-21 | Files scanned: config.py, accounts.py, post/postiz.py, meta_graph.py, crosspost.py, post/run.py | Token estimate: ~950 | Full guide: docs/INSTAGRAM_CONNECT.md -->
# FanOps Account Connection

How a fan IG account becomes **publishable**, the verified state, and the resolved connect issue.
Plain-language full version: [`../INSTAGRAM_CONNECT.md`](../INSTAGRAM_CONNECT.md).

## Publishing routes through Postiz ONLY

```text
clip --crosspost--> Post(account_id = accounts.json integrations['instagram'])
                         |
                  post/run.py publish --> Postiz channel (stored token) --> IG
```

- Only publish backend in use: **Postiz** (self-hosted `localhost:4007`, public via Tailscale funnel).
- `meta_graph.py` (`META_GRAPH_TOKEN`) is **trends-only** — no publish path, not referenced in `post/`
  or `crosspost.py`. A Meta token that can publish does NOT make FanOps publish.

## "Connected" = THREE states (publishing needs all three)

| # | State | Where | Credential? |
|---|-------|-------|-------------|
| 1 | App-authorized | Meta app (Instagram app id `976142145032195`, used in OAuth `client_id`) | No — capability only |
| 2 | Channel minted | Postiz `Integration` table (per-account token) | **Yes** |
| 3 | Mapped | `accounts.json integrations['instagram'] = <postiz_id>` | No — routing |

Instagram Login mints **one token per account**; there is no single multi-account authorization here
(the Facebook path needs `FACEBOOK_APP_*`, which are empty).

## Structural gate

`crosspost` stamps `post.account_id` from `integrations[platform]` at birth; `accounts.validate()` /
`go_live` BLOCK go-live while any active account is unmapped ⇒ an unmapped account is structurally
unpublishable.

## Verified state — 2026-06-21 (all connected)

| Account | Postiz channel | accounts.json | Publishable |
|---------|----------------|---------------|-------------|
| markmakmouly | `cmqeb1uuv0001o579bjcdj7my` (instagram-standalone) | mapped | **YES** |
| perca.late | `cmqno51tb0001p68ez9tx5a6k` (instagram-standalone) | mapped | **YES** |
| cisumwolfhom | `cmqno5ops0003p68ea2k3kgzs` (instagram-standalone) | mapped | **YES** |

Confirmed in the Postiz Postgres `Integration` table and `accounts.json`.

## Connect flow

`GET /public/v1/social/instagram-standalone` → IG authorize URL (funnel redirect) → Allow → funnel →
Postiz exchanges code → channel stored → `accounts.write_integration(...)`. Only non-scriptable step:
the human "Allow". Works on **stock Postiz**.

## RESOLVED connect failure (was blocking perca.late / cisumwolfhom)

**Root cause: the wrong `INSTAGRAM_APP_SECRET` was baked into the running Postiz container env.** The
code-for-token exchange (`api.instagram.com/oauth/access_token`) returned `OAuthException 400 "Error
validating verification code … redirect_uri … identical"` — which is Meta's **generic bad-client-secret
error**, NOT a redirect issue (redirect was byte-identical). markmakmouly worked because it connected
2026-06-14 with the correct secret and its saved token never re-checks the secret; the container was
recreated 2026-06-18 with a wrong secret, breaking all new connects.

**Proof:** in-container Meta validation (`graph.instagram.com/access_token?grant_type=ig_exchange_token`):
live secret → `"Error validating client secret" (code 100)`; secret in `docker-compose.yaml.bak.presecret.*`
→ a session error (past secret validation) ⇒ the backup held the correct secret.

**Fix:** restore the correct secret in `docker-compose.yaml` (from `*.bak.presecret.*`) → `docker compose
up -d` to **recreate** (a `restart`/`pm2 restart` won't reload the startup env) → connect normally → map.
Beware false eliminations (see [`../INSTAGRAM_CONNECT.md`](../INSTAGRAM_CONNECT.md) §5): a dummy code's
"Invalid authorization code" does NOT clear the secret; the host can't reach Meta (use `docker exec`).

## Non-stock Postiz patches — NOT required for connect

The 2026-06-21 recreate reverted to stock and both accounts connected on stock. Optional:
`subdomain.management.js → return url.hostname;` (funnel dashboard login cookie) and an
`instagram.standalone.provider.js` `data[]` unwrap. Re-apply only on a reproduced symptom; detail in
[`../INSTAGRAM_CONNECT.md`](../INSTAGRAM_CONNECT.md) §6.
