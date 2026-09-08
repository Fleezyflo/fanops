# Meta Graph credentials — rotation & ops runbook

FanOps uses the **Meta Graph API** for operator-only paths: imported-media insights (`fanops map-media`),
live-link verification, and hashtag research. **Publishing and authored-post metrics go through Postiz/Zernio**
— a lapsed Graph token does not block the ship route.

No doctor command introspects token expiry (`debug_token` was removed).

## The two credential kinds

| | What | Secret? | Where it lives |
|---|---|---|---|
| **IG user id** | the IG Business account id (`META_IG_USER_ID`, or per-account `ig_user_id`) | No (like a Postiz integration id) | `.env` (global) / `accounts.json` (per-account) |
| **Access token** | the Graph token (`META_GRAPH_TOKEN`, or per-handle `META_GRAPH_TOKEN__<SLUG>`) | **Yes — write-only** | `.env` + `os.environ`, **never** echoed/logged/returned |

`<SLUG>` = the handle uppercased with `@`/punctuation stripped (e.g. `@perca.late` → `META_GRAPH_TOKEN__PERCALATE`).
A handle with no per-handle token falls back to the global.

## Mint a long-lived token

Meta short-lived tokens last ~1 hour; long-lived ones last ~60 days and must be re-minted before they lapse.

1. In the Meta App (developers.facebook.com → your app), confirm the token grants **`instagram_basic`**
   (identification) and **`instagram_manage_insights`** (reach/retention — without it imported insights freeze at the
   last snapshot). For hashtag discovery also grant the *Instagram Public Content Access* App-Review feature.
2. Get a short-lived **User** token from the Graph API Explorer (or your login flow) for the IG-linked user.
3. Exchange it for a long-lived token:
   `GET https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<SHORT_LIVED_TOKEN>`
   → the response `access_token` is the long-lived token (~60 days).
4. (Optional) Derive a **never-expiring Page token** from the long-lived User token via `GET /me/accounts`.

## Set it in FanOps

- **Access token** (secret): OS keychain — see [CONFIG.md](CONFIG.md) § Secrets storage (`META_GRAPH_TOKEN` or per-handle `META_GRAPH_TOKEN__<SLUG>`).
- **IG user id** (non-secret): per-account `ig_user_id` in `accounts.json` (global fallback: `META_IG_USER_ID` in `.env`).
- Restart the Studio/daemon after rotating so a fresh `Config` reloads creds.

## After rotating

1. Confirm `00_control/insights_blocked.json` is absent (or delete it after granting scope).
2. Open Studio Home — the system strip should not show an IG-insights-blocked danger badge.
3. (Optional) Run an imported-media insights pull (`fanops map-media` / reconcile path) to confirm scope on a live row.

The token value never appears in doctor output, logs, or any ActionResult.
