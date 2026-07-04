# Meta Graph credentials — rotation & ops runbook

FanOps reads Instagram performance (reach, retention, saves) and verifies live-linked media through
the **Meta Graph API**. Publishing goes through **Postiz's own OAuth**, so when a Graph token lapses
**Postiz keeps posting while Graph verification + metrics silently go dark** — the exact failure this
runbook prevents. `fanops doctor` now flags an expired/near-expiry token (`debug_token` preflight,
WARN ≤10 days out, FAIL when expired); rotate on the WARN, never wait for the FAIL.

## The two credential kinds

| | What | Secret? | Where it lives |
|---|---|---|---|
| **IG user id** | the IG Business account id (`META_IG_USER_ID`, or per-account `ig_user_id`) | No (like a Postiz integration id) | `.env` (global) / `accounts.json` (per-account) |
| **Access token** | the Graph token (`META_GRAPH_TOKEN`, or per-handle `META_GRAPH_TOKEN__<SLUG>`) | **Yes — write-only** | `.env` + `os.environ`, **never** echoed/logged/returned |

`<SLUG>` = the handle uppercased with `@`/punctuation stripped (e.g. `@perca.late` → `META_GRAPH_TOKEN__PERCALATE`).
A handle with no per-handle token falls back to the global — see the sibling doctor check that FAILS when
≥2 active IG accounts share one id (each account must carry its **own** `ig_user_id`, not borrow the global).

## Mint a long-lived token

Meta short-lived tokens last ~1 hour; long-lived ones last ~60 days and must be re-minted before they lapse.

1. In the Meta App (developers.facebook.com → your app), confirm the token grants **`instagram_basic`**
   (identification) and **`instagram_manage_insights`** (reach/retention — without it insights freeze at the
   last snapshot). For hashtag discovery also grant the *Instagram Public Content Access* App-Review feature.
2. Get a short-lived **User** token from the Graph API Explorer (or your login flow) for the IG-linked user.
3. Exchange it for a long-lived token:
   `GET https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<SHORT_LIVED_TOKEN>`
   → the response `access_token` is the long-lived token (~60 days).
4. (Optional, recommended) Derive a **never-expiring Page token** from the long-lived User token via
   `GET /me/accounts` — `debug_token` reports `expires_at: 0` for these, and the doctor check treats `0` as
   "does not expire" (no WARN). Use only if your setup supports Page-token insights.

Verify before wiring it in:
`GET https://graph.facebook.com/v21.0/debug_token?input_token=<NEW_TOKEN>&access_token=<NEW_TOKEN>`
→ check `data.is_valid == true` and read `data.expires_at`. (This is the same call the doctor preflight makes.)

## Set it in FanOps — no shell editing

**Studio Go-Live tab → per-account Meta creds** is the supported path. It calls
`studio.golive.set_meta_creds(cfg, handle, ig_user_id, token)`, which:

- writes the **id** (non-secret) to `accounts.json` first (validates the handle exists), then
- dual-writes the **token** to the per-handle `.env` key `META_GRAPH_TOKEN__<SLUG>` **and** `os.environ`
  (write-only — the tab never renders it back; a blank token leaves the existing one untouched so you can
  update just the id).

For the **global** token, set `META_GRAPH_TOKEN` + `META_IG_USER_ID` in `.env` (the global fallback used by
any account with no per-account creds). Restart the Studio/daemon so a fresh `Config` reloads `.env`.

## After rotating

Run `fanops doctor` — the *Meta Graph token valid + not near expiry* check should be green (no WARN/FAIL).
The token value never appears in the doctor report, logs, or any ActionResult by construction; only the
handle label and the human expiry date are shown.
