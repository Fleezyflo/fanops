# Connecting Instagram accounts to FanOps — the complete picture

Single source of truth for **how a fan IG account becomes publishable**, what is connected, and the
**root cause + fix** of the connect failure that blocked the second and third accounts for ~a day.
Last verified: **2026-06-21** — all three fan accounts connected and mapped.

Companion: [`CODEMAPS/account-connection.md`](CODEMAPS/account-connection.md) (token-lean version).

---

## 1. Current state — who can publish

| Account | Postiz channel | accounts.json | Can publish? |
|---|---|---|---|
| **markmakmouly** | `cmqeb1uuv0001o579bjcdj7my` (instagram-standalone) | mapped | **YES** |
| **perca.late** | `cmqno51tb0001p68ez9tx5a6k` (instagram-standalone) | mapped | **YES** |
| **cisumwolfhom** | `cmqno5ops0003p68ea2k3kgzs` (instagram-standalone) | mapped | **YES** |

All three verified in the Postiz Postgres `Integration` table and in `accounts.json`
(`integrations['instagram']` per handle). perca.late + cisumwolfhom were connected 2026-06-21 10:53–10:54.

---

## 2. "Connected" means three different things (the #1 source of confusion)

Publishing to an account needs **all three**:

1. **App-authorized** — the IG account is authorized on the Meta app (Instagram app id
   `976142145032195`). A capability, **not** a credential. Doing this alone connects nothing.
2. **Channel minted** — a real per-account token exists and is stored as a **Postiz channel**.
   Instagram Login mints **one token per account**; "the app has access" does not connect any account.
3. **Mapped** — `accounts.json integrations['instagram'] = <postiz_channel_id>` so FanOps knows which
   channel to target.

**FanOps publishes through Postiz ONLY.** The `META_GRAPH_TOKEN` in `.env` is trends-only (hashtag
search); it has no publishing path.

---

## 3. How connecting an account works (the flow that succeeds)

1. Postiz mints an Instagram authorize URL with `client_id=976142145032195`,
   `redirect_uri=<FUNNEL>/integrations/social/instagram-standalone`, scopes
   `instagram_business_basic, …content_publish, …manage_comments, …manage_insights`, single-use `state`.
2. Open it in a browser **logged into the target IG account** → **Allow all permissions**.
3. Instagram redirects to the funnel → Postiz exchanges the code for a token (`POST
   https://api.instagram.com/oauth/access_token`) → long-lived exchange → stores the channel.
4. Map it: `accounts.write_integration(cfg, '<handle>', 'instagram', '<new_id>')`.

**The funnel** is the public HTTPS callback `https://molhams-macbook-pro-2.tail72be94.ts.net`
(Tailscale Funnel → `localhost:4007`). Instagram refuses plain `localhost` redirects. Restore with
`tailscale funnel --bg 4007`. The **only** non-scriptable step is the human "Allow".

> This flow works on **stock Postiz**. No provider patches are required (see §6).

---

## 4. ROOT CAUSE of the connect failure (resolved 2026-06-21)

**The `INSTAGRAM_APP_SECRET` baked into the running Postiz container's environment was the WRONG secret
for Instagram app `976142145032195`.** Meta's token endpoints reject it, so the OAuth code-for-token
exchange failed for every new connect.

### Why the error message lied to us

Meta returned, on the code exchange:

```text
{"error_type":"OAuthException","code":400,
 "error_message":"Error validating verification code. Please make sure your redirect_uri is identical
                  to the one you used in the OAuth dialog request"}
```

That is Meta's **generic error for a bad `client_secret`** on `oauth/access_token` — it is **not** a
redirect problem. The redirect was byte-for-byte identical at authorize and exchange (proven from the
provider's own logs). Postiz then surfaced it in the UI as *"Could not add provider / Not enough
scopes,"* which is a third layer of misdirection (no token → `permissions` undefined → `checkScopes`
throws).

### Why markmakmouly worked but the other two didn't
markmakmouly connected **2026-06-14**, when the container held the **correct** secret. Its long-lived
token was saved — **saved tokens never re-validate the app secret**, so it kept publishing. The
container was **recreated 2026-06-18**, reloading a compose/`.env` that carried the **wrong** secret.
Every connect attempt from 06-18 onward hit the bad secret and died at the exchange. Same app, same
redirect, same flow — only the account-creation date (i.e. which secret was live at the time) differed.

### How it was proven (no operator clicks, no secret printed)
Validate a candidate secret directly against Meta **inside the container** (the host has no route to
Meta), using markmakmouly's stored token, and read which error comes back:

```bash
# inside the postiz container; secret/token never printed, only the error class matters
GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token
    &client_id=$INSTAGRAM_APP_ID&client_secret=$INSTAGRAM_APP_SECRET&access_token=<stored_token>
```

- container's live secret → `{"error":{"message":"Error validating client secret.","code":100}}` → **wrong**.
- secret from `docker-compose.yaml.bak.presecret.*` → `{"error":{"message":"Session key invalid…"}}`
  (a *token/session* error, **past** secret validation) → **that secret is correct**.

The correct secret had been preserved in `docker-compose.yaml.bak.presecret.*` — a prior session's edit
on ~06-17 had overwritten the live value with a wrong one (most likely the **Facebook** app secret from
Settings → Basic instead of the **Instagram** app secret).

### The fix (exact steps)
1. Put the correct secret back into `~/postiz-selfhost/postiz-docker-compose/docker-compose.yaml`
   (`INSTAGRAM_APP_SECRET`), restored from `docker-compose.yaml.bak.presecret.*`.
2. **Recreate** the container so it reloads the corrected env:
   `docker compose -f ~/postiz-selfhost/postiz-docker-compose/docker-compose.yaml up -d`.
   A `docker restart` / `pm2 restart` is **NOT enough** — the wrong value lives in the container's
   startup environment; only a recreate reloads it from compose. (DB is a separate container; data and
   markmakmouly's channel are untouched by the recreate.)
3. Connect each account normally (Add Channel → Instagram → Allow). Succeeds on stock Postiz.
4. Map each: `accounts.write_integration(cfg, '<handle>', 'instagram', '<channel_id>')`.

Verify the live secret is correct (consistent hashing; value never printed):
```bash
python3 - <<'PY'
import subprocess, re, glob, hashlib
fp = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
cur = subprocess.run(["docker","exec","postiz","printenv","INSTAGRAM_APP_SECRET"],capture_output=True,text=True).stdout.strip()
ok  = re.search(r"INSTAGRAM_APP_SECRET\s*[:=]\s*['\"]?([A-Za-z0-9]{20,})",
                open(sorted(glob.glob("/Users/molhamhomsi/postiz-selfhost/postiz-docker-compose/docker-compose.yaml.bak.presecret.*"))[0]).read()).group(1)
print("MATCH =", cur == ok)   # must be True
PY
```

---

## 5. What misled us for a day — do NOT re-tread these

- **"redirect_uri identical" is a redirect bug.** ❌ It is Meta's generic **bad-client-secret** error on
  `oauth/access_token`. The redirect was proven byte-identical. Treat this message as "check the secret."
- **"A dummy code returns 'Invalid authorization code', so the secret is fine."** ❌ FALSE elimination
  (this exact line was in the previous version of this doc and cost the most time). The client-secret
  error only appears with a **real** code; a malformed dummy code fails on the code regardless of the
  secret. **Never use a dummy code to clear the secret** — validate it with `ig_exchange_token` + a real
  stored token instead.
- **The popup consumes the code twice / it expires.** ❌ Single exchange confirmed in the provider logs
  (one `authenticate` call per click).
- **`pm2 restart --update-env` will apply the new secret.** ❌ The backend's effective env comes from the
  container's **startup** environment, not an injected process env; `dotenv -e ../../.env` finds no
  `/app/.env` and falls back to it. Only `docker compose up -d` (recreate) corrects it.
- **Account type / Professional / scope toggles.** ❌ The failure was before any scope/account check.
- **Host (the machine) can reach Meta to validate secrets.** ❌ The host returned `URLError`; only the
  **container** has the route. Run all Meta validation via `docker exec postiz …`.

---

## 6. Postiz modifications — NOT required for connecting

Earlier sessions applied two non-stock patches (a `.ts.net` cookie fix for funnel **dashboard login**,
and a `data[]`-unwrap in the IG provider). The 2026-06-21 recreate reverted the container to **stock**
and **both accounts connected on stock** — so neither patch is needed for the OAuth connect. They are
optional:

| Patch | File (inside container) | What it addresses | Needed for connect? |
|---|---|---|---|
| Cookie (`.ts.net`) | `…/subdomain/subdomain.management.js` → `return url.hostname;` | funnel dashboard login cookie rejected because tldts treats `.ts.net` as a public suffix | **No.** May matter only for a *fresh* funnel login; unverified this session (operator's session was already authenticated). |
| `data[]` unwrap | `…/integrations/social/instagram.standalone.provider.js` | if the IG token response is wrapped `{data:[{…}]}` | **No.** Stock read the response flat and connect succeeded. |

A recreate (`docker compose up`) reverts to stock and wipes both. Re-apply **only** if a concrete,
reproduced symptom returns (funnel login failing; or `permissions undefined` *after* a successful token
exchange). `grep 'FANOPS PATCH'` / `grep 'return url.hostname'` shows what, if anything, is modified.

---

## 7. Quick reference — commands

```bash
# What's actually connected (source of truth — the Postiz Integration table):
docker exec postiz-postgres psql -U postiz-user -d postiz-db-local -c \
  'SELECT profile, id, "providerIdentifier" FROM "Integration" ORDER BY "createdAt";'

# Map a connected channel into accounts.json:
python -c "from fanops.config import Config; from fanops import accounts; accounts.write_integration(Config(), '<handle>', 'instagram', '<postiz_id>')"

# Recreate Postiz after a compose/secret change (the documented way to apply env changes):
docker compose -f ~/postiz-selfhost/postiz-docker-compose/docker-compose.yaml up -d

# Funnel: point at Postiz / check status:
tailscale funnel --bg 4007 ; tailscale funnel status
```
