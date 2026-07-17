# 09 — Zernio Official-Contract Reconciliation (Prompt 08, Phase B.3 → D)

> # REV 4 — IMPLEMENTATION RECORD (gate APPROVED, Wave 1A built)
>
> Operator token **`APPROVE IMPLEMENTATION WITHOUT POSTIZ FIX`** returned 2026-07-17. Wave 1A is built.
>
> **Building it disproved six of this document's own claims.** They are recorded in **§14** — not silently
> fixed — under the same rule the operator imposed on Rev 2 and Rev 3: *the original claim, the evidence
> that refuted it, and the consequence.* Four are the same failure this document was twice rejected for —
> **asserting a fact about the codebase without opening the file** (§14.1). Scope is unchanged: the
> approved change list held; the corrections are to the RECORD, not to the plan.
>
> **The C7a argument is now empirically confirmed, not merely reasoned** (§14.2): the wrapped
> `ConnectionError` returns `_is_transient_publish_error → True`; the operator's literal `RuntimeError`
> remedy returns **`False`**. Executed, not read.
>
> ---
>
> # REV 3 — AMENDED after operator rejection of the Rev 2 gate
>
> Rev 2 was **accepted in substance**. Three defects required amendment; **all three are the operator's
> findings**, and one is a **live security leak Rev 2's proposal would have shipped**.
>
> | # | Defect | Resolution |
> |---|---|---|
> | **C7** | **Signed-URL leak via `requests` TRANSPORT EXCEPTIONS.** Rev 2 sanitized the *response* object; a `Timeout`/`ConnectionError` embeds the full signed `uploadUrl` in `str(exc)` **and** in `exc.request.url`. **Leak path confirmed concrete** (§8.5) | Signed PUT wrapped; **class-preserving** re-raise (§8.4). **6 new tests** (§9.4). Security claims downgraded until the tests exist |
> | **C8** | **TikTok "semantically inert" was OVERREACH.** The current official **Platform Settings guide documents the nested shape verbatim.** Two current official Zernio documents **conflict**; Rev 2 resolved it by fiat in the OpenAPI's favour | **RETRACTED.** Reclassified **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`** (§5.4). Conflict **preserved**, not resolved |
> | **C9** | **Derived counts wrong** — "11 fixes / 8 already-correct" | **12 / 11 / 7 of 30.** All counts recomputed from the rows (§5.5) |
>
> **A correction to the operator's own C7 remedy is proposed** (§8.4): `raise RuntimeError(...) from None`
> as literally specified would **convert a retryable network blip into an immediately-burned post**, because
> `_is_transient_publish_error` classifies `requests` exceptions by **type** but a `RuntimeError` by
> **message substring**. Evidence and the class-preserving alternative: §8.4.1.
>
> **Implementation scope is UNCHANGED across all nine corrections: one function + four helpers, one file.**
>
> ---
>
> **REV 2 — AMENDED 2026-07-16T23:0xZ after operator rejection of the Rev 1 Phase D gate.**
> Rev 1 made **four** documentation claims that the authoritative OpenAPI schema refutes. All four are
> retracted in **§2**, with the original claim, the refuting evidence, and the implementation consequence
> recorded rather than silently replaced. **Rev 1's conclusions are preserved verbatim in §2 so the error is
> auditable.**
>
> **Root-cause classification is UNCHANGED but now better grounded** (§6). **Implementation scope is
> UNCHANGED** — still one function — with **two** corrections applied (`size`, evidence redaction) and
> **two** dispositions made explicit (`x-request-id` deferred on scope-control grounds; TikTok payload
> unchanged per the operator's own rule).
>
> **⛔ RETRACTED HISTORICAL QUOTATION — Rev 2 wrote, and Rev 3 retracted:** *"New serious finding (§5.4):
> the client's TikTok settings are schema-valid but semantically inert — almost certainly never applied.
> Not fixed here; flagged as its own follow-up."* **This claim is FALSE as stated and is preserved only so
> the error is auditable.** It inferred runtime behaviour from schema modelling. The current official
> Platform Settings guide (**S9**) documents the client's exact shape verbatim. Current classification:
> **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`** (§5.4, C8).
>
> **State AS OF REV 2 (historical — superseded by §14.6):** *no code changed · no Zernio call · nothing
> requeued · four failed records untouched · `FANOPS_CORPUS_AUTO=0` · `queued=0` · no Postiz change.*
> **"No code changed" ceased to be true at Rev 4**, when the approved implementation landed. Everything
> else in that line still holds — re-verified at §14.6.

---

## 1. Document Control

| Field | Value |
|---|---|
| **Title** | 09 — Zernio Official-Contract Reconciliation |
| **Revision** | **Rev 4** — implementation record (Rev 1 gate **REJECTED**; Rev 2 gate **REJECTED**; Rev 3 gate **APPROVED** 2026-07-17) |
| **Phase** | Prompt 08, resumed at **B.3**, delivering **A–F** + the **Phase D gate**, now **built** |
| **Governing baselines** | `07_WAVE_0A_CONTAINMENT_RECORD.md` · `08_DUAL_BACKEND_INCIDENT_FRAME.md` (both accepted) |
| **Authoritative source** | **OpenAPI 3.1.0 spec, `Zernio API v1.0.4`**, retrieved `https://docs.zernio.com/api/openapi` |
| **Spec retrieval timestamp** | **2026-07-16T22:51Z** (1,748,956 bytes YAML, parsed locally with PyYAML) |
| **Wave** | **1A — Zernio only.** Postiz appears in no part of this proposal |
| **Zernio API calls** | **ZERO** (Rev 4: still zero — the build is offline, mocked-HTTP only) |
| **Mutations** | **NONE** to the ledger. Rev 4 changes SOURCE only: `post/zernio.py`, its tests, 2 regenerated derived artifacts |

### 1.1 Why Rev 1 was wrong — the methodological failure, named

**Rev 1 read the *guides* and the *changelog*, then asserted absence.** It never fetched the **Create Post**
or **Get Upload URL** API-reference pages, and never fetched the **OpenAPI schema** at all.

Rev 1 §2.7 literally wrote *"example omission is not proof that the field is unsupported"* as a critique of a
supplied lead — **and then committed that exact fallacy four times.** A guide is a curated narrative; the
OpenAPI spec is the contract. **Absence from a guide is not absence from the API.**

**Rule adopted for the remainder of this work: no claim of the form "X is not supported" may rest on
anything but the OpenAPI schema.** All Rev 2 contract claims below cite the spec by path and property.

### 1.2 Provenance caveat (unchanged)

The **written Prompt 08 was never supplied.** Phase labels (B.3, D) and the gate tokens come from the
operator's in-session instruction.

### 1.3 Method note — documentation retrieved, API not called

*"Do not call Zernio"* is read as **do not call the Zernio API**. Retrieving public documentation and the
public OpenAPI spec over HTTPS is the mandated Phase B.3 activity. **No request carried the API key. No
endpoint under `zernio.com/api/` was contacted.** The spec was downloaded once and parsed **locally**.

---

## 2. CORRECTION RECORD

Four Rev 1 claims are retracted. Each row records the original claim, the refuting evidence, and the
consequence.

### 2.1 CORRECTION 1 — `x-request-id` IS documented for `POST /v1/posts`

| | |
|---|---|
| **Rev 1 claim (RETRACTED)** | §2.6: *"`x-request-id` — ❌ **NOT DOCUMENTED ANYWHERE**"*. §2.7 lead 3c: *"**NOT DOCUMENTED — AND SAFETY-CRITICAL**"*. §2.8: *"Official documentation agrees with the client, not with the lead."* §4 row 19: *"✅ **CLIENT IS CORRECT — do NOT add**"*. §8.3: *"❌ **Adding `x-request-id`** — refused on safety grounds"* |
| **Refuting evidence** | **OpenAPI `paths./v1/posts.post.parameters[0]`**, verbatim:<br>`- name: x-request-id`<br>`  in: header`<br>`  required: false`<br>`  schema: {type: string, format: uuid}`<br>Description: *"Optional client-generated request identifier for safe retry (idempotency). When two requests carry the same value, the second is treated as a retry of the first and returns the original post (HTTP 200) instead of creating a duplicate. Window is ~5 minutes from the first request. Generate a UUID per logical call. **SDKs do this automatically; HTTP clients should set it themselves or omit it.**"* |
| **Corroboration** | Operation description §Idempotency documents the full two-layer contract; the `409` response says *"To avoid 409s caused by retry loops, set a unique `x-request-id` per logical request"* |
| **Consequence** | The Rev 1 §2.8 safety argument **collapses**. Adding the header is **not intrinsically unsafe** — it is officially recommended for HTTP clients, which FanOps is. **`test_no_x_request_id_header_sent` is REMOVED.** The client's H5 comment is **not** corroborated by the docs and is now **stale** (§7.8) |
| **Scope change** | **None** — deferred to a follow-up, but on **scope-control** grounds only (§7.9), never on "undocumented" |

### 2.2 CORRECTION 2 — `size` IS a documented optional presign field

| | |
|---|---|
| **Rev 1 claim (RETRACTED)** | §2.3: *"**No `size` field in any sample**"*. §2.7 lead 2c: *"❌ **NOT DOCUMENTED.** … **Proposal omits it**"*. §7.1 test 5: `test_size_field_not_sent` |
| **Refuting evidence** | **OpenAPI `paths./v1/media/presign.post.requestBody…properties.size`**, verbatim:<br>`size:`<br>`  type: integer`<br>`  description: Optional file size in bytes for pre-validation (max 5GB)`<br>`  example: 15234567`<br>(`required: [filename, contentType]` — `size` is optional, not absent) |
| **Consequence** | **`test_size_field_not_sent` is REMOVED.** The proposal now **sends `size`** (§8.2), justified from the schema in §8.3 |
| **Scope change** | **None** — one extra key in an existing JSON body |

### 2.3 CORRECTION 3 — `/v1/media/upload-direct` EXISTS

| | |
|---|---|
| **Rev 1 claim (RETRACTED)** | §2.7 lead 4: *"**No endpoint by that name is documented**"*. §3.4: *"no such endpoint is documented at all"*. §2.6 `ZOC-008`: *"**No `upload-direct` endpoint documented**"* |
| **Refuting evidence** | **OpenAPI `paths./v1/media/upload-direct.post`** — `operationId: uploadMediaDirect`, `summary: Upload media file`, **`tags: [Messages]`**, `security: [bearerAuth]`, `requestBody: multipart/form-data {file: binary, contentType?}`, response `{url, filename, contentType, size}`.<br>Description verbatim: *"Upload a media file using API key authentication and get back a publicly accessible URL. **The URL can be used as `attachmentUrl` when sending inbox messages.** Files are stored in temporary storage and auto-delete after 7 days. **Maximum file size is 25MB.** Unlike `/v1/media/upload` (which uses upload tokens for end-user flows), this endpoint uses standard Bearer token authentication for programmatic use."* |
| **Consequence** | **The Rev 1 verdict (DO NOT SELECT) is UNCHANGED — but its evidence is replaced.** Rev 1 rejected it because it "doesn't exist." It **does** exist; it is rejected because it is **`tags: [Messages]`**, scoped to `attachmentUrl` for **inbox messages**, with a **25 MB** cap. The operator's lead 4 was **accurate in every particular**; Rev 1's refutation was the error |
| **Scope change** | **None** |

### 2.4 CORRECTION 4 — `/v1/media/upload` IS acknowledged in the spec

| | |
|---|---|
| **Rev 1 claim (RETRACTED)** | §5.1: *"Is `POST /media/upload` in the official docs? **NO** — absent from all sources"* |
| **Refuting evidence** | The `upload-direct` description **names it**: *"Unlike **`/v1/media/upload`** (which uses upload tokens for **end-user flows**)…"* |
| **Nuance that survives** | It is **acknowledged in prose but NOT a documented path.** The complete media path list in the spec is exactly: **`/v1/media/presign`**, **`/v1/media/upload-direct`**, **`/v1/tools/validate/media`**. `/v1/media/upload` and `/v1/media/upload-token` have **no path entry, no method, no schema, no stability commitment** |
| **Consequence** | **The root-cause classification is STRENGTHENED, not weakened** (§6). The spec draws the exact distinction this incident is about: `/v1/media/upload` is *"for **end-user flows**"*, while `upload-direct` is *"for **programmatic use**"*. **FanOps is a programmatic client that built on the end-user-flow endpoint.** Rev 1 reached the right verdict via a false premise ("absent"); Rev 2 reaches it via the true one ("acknowledged, but scoped to end-user flows and unspecified") |
| **Scope change** | **None** |

### 2.5 What Rev 1 got right — preserved

Confirmed against the OpenAPI schema and **retained unchanged**: the legacy upload contract must be removed;
`POST /media/presign` → raw signed `PUT` is correct; the PUT must **not** carry the Zernio `Authorization`
header; `publicUrl` flows into `mediaItems`; signed-URL credentials must be scrubbed; **no** legacy fallback;
Postiz stays outside Wave 1A; the 4 MB cap is a separate follow-up; no Zernio call / requeue / deploy /
canary is authorised.

---

## 3. (A) Official Contract Evidence — Amended

### 3.1 Sources

| # | Source | URL | Retrieved |
|---|---|---|---|
| **S0** | **OpenAPI 3.1.0 spec — `Zernio API v1.0.4`** ← **AUTHORITATIVE** | `https://docs.zernio.com/api/openapi` | **2026-07-16T22:51Z** |
| S1 | Media Uploads — Guides | `https://docs.zernio.com/guides/media-uploads` | 22:37:44Z |
| S2 | Zernio API Documentation | `https://docs.zernio.com/` | 22:37:44Z |
| S3 | Changelog | `https://docs.zernio.com/changelog` | 22:37:44Z |
| S4 | TikTok API — Platforms | `https://docs.zernio.com/platforms/tiktok` | 22:37:44Z |
| S5 | Rate Limits — Guides | `https://docs.zernio.com/guides/rate-limits` | 22:37:44Z |
| S6 | Error Handling — Guides | `https://docs.zernio.com/guides/error-handling` | 22:37:44Z |
| S7 | `zernio-dev/zernio-api` (official repo) | `https://github.com/zernio-dev/zernio-api` | 22:37:44Z |
| **S8** | **Create Post — API Reference** | `https://docs.zernio.com/posts/create-post` | **2026-07-16T22:5xZ** |
| **S9** | **Platform Settings — Guides** ← **the source Rev 2 never fetched (C8)** | `https://docs.zernio.com/guides/platform-settings` | **2026-07-16T23:2xZ** |

**Source-reconciliation rule (REV 4 — replaces the Rev 2/3 precedence rule).** The rule that stood here —
*"S0 overrides all others; where a guide and the spec disagree, the spec governs"* — is **RETRACTED**. It is
the rule that produced **C8**: applied literally, it let a schema silently nullify a current official guide,
and turned "the spec doesn't model it" into "the server ignores it". Precedence is **scoped by question**,
not global:

| Question | Governing source | Why |
|---|---|---|
| **What does the machine schema require / permit?** (field names, types, enums, required-ness, status codes) | **S0 — the OpenAPI** | It is the generated, exhaustive contract. **Absence from S0 is evidence of absence** — this is what refuted all four Rev 1 claims |
| **What representations does Zernio accept?** | **S0 *and* the current official references (S8) and guides (S1-S7) TOGETHER** | **A current official guide may document an ACCEPTED VARIANT the schema under-models.** `additionalProperties` is unset (JSON-Schema default: **allowed**), so S0 does not reject what S9 documents. Both can be true |
| **What does the server DO with an accepted value?** | **NO document answers this.** | Application is a **runtime** fact. A schema models a contract; it does not prove what a server ignores. Only a live probe decides |

**When current official sources conflict, PRESERVE the conflict.** Record what each documents, state plainly
what remains unverified, and do **not** resolve it by fiat in favour of whichever source you most recently
learned to trust. Rev 1 erred by treating S1/S3 as **exhaustive**; Rev 2 erred by treating S0 as **total**.
Both are the same mistake — reasoning from one source to a conclusion it cannot support.

### 3.2 Base URL — resolved definitively

```yaml
servers:
- url: https://zernio.com/api      # Production
  description: Production
- url: http://localhost:3000/api   # Local
```

Paths carry the `/v1` prefix (`/v1/media/presign`). **Full URL = `https://zernio.com/api` + `/v1/media/presign`
= `https://zernio.com/api/v1/media/presign`.**

The client's `_base()` = `https://zernio.com/api/v1`, and it appends `/media/presign` → **the identical
URL**. ✅ **`_base()` is correct and needs no change.** The Rev 1 §2.2 "doubled-`v1`" caution stands: the
docs' `/v1/media/presign` shorthand must not be appended to `_base()` literally.

Auth: `bearerAuth` — `Authorization: Bearer <key>`. ✅ Client correct.

### 3.3 `POST /v1/media/presign` — full schema (S0)

```yaml
post:
  operationId: getMediaPresignedUrl
  tags: [Media]
  summary: Get upload URL
  description: Get a presigned URL to upload files directly to cloud storage (up to 5GB). Returns an
    uploadUrl and publicUrl. PUT your file to the uploadUrl, then use the publicUrl in your posts.
  requestBody:
    required: true
    content:
      application/json:
        schema:
          type: object
          required: [filename, contentType]
          properties:
            filename:    {type: string, example: my-video.mp4}
            contentType: {type: string, enum: [image/jpeg, image/jpg, image/png, image/webp, image/gif,
                                               video/mp4, video/mpeg, video/quicktime, video/avi,
                                               video/x-msvideo, video/webm, video/x-m4v, application/pdf],
                          example: video/mp4}
            size:        {type: integer, description: Optional file size in bytes for pre-validation (max 5GB),
                          example: 15234567}
  responses:
    '200':
      uploadUrl: {type: string, format: uri}   # Presigned URL to PUT your file to (expires in 1 hour)
      publicUrl: {type: string, format: uri}   # Public URL where the file will be accessible after upload
      key:       {type: string}                # Storage key/path of the file
      expiresIn: {type: integer}               # Seconds until the presigned uploadUrl expires (always 3600)
    '400': Invalid request (missing filename, contentType, or unsupported content type)
```

| Property | Value |
|---|---|
| `contentType` | **an `enum`** — `video/mp4` **is** a member ✅ (validates the client's constant) |
| `size` | **documented, optional, integer, "pre-validation (max 5GB)"** — **CORRECTION 2** |
| `expiresIn` | **always 3600** |
| Limit | **5 GB** |

**Media tag description (S0):** *"Media referenced in posts. URLs must be publicly reachable over HTTPS. Use
`POST /v1/media/presign` for uploads up to 5GB. **Zernio auto-compresses images and videos that exceed
platform limits** (videos over 200 MB may not be compressed)."*

> That last clause independently reinforces §4.5: **Zernio already auto-compresses.** FanOps' crf-28
> pre-shrink is not merely unnecessary at 5 GB — it duplicates a service the platform performs.

### 3.4 `POST /v1/media/upload-direct` — full schema (S0) — **CORRECTION 3**

```yaml
post:
  operationId: uploadMediaDirect
  summary: Upload media file
  tags: [Messages]                     # <-- NOT Media
  security: [bearerAuth]
  description: |
    Upload a media file using API key authentication and get back a publicly accessible URL.
    The URL can be used as attachmentUrl when sending inbox messages.
    Files are stored in temporary storage and auto-delete after 7 days.
    Maximum file size is 25MB.
    Unlike /v1/media/upload (which uses upload tokens for end-user flows),
    this endpoint uses standard Bearer token authentication for programmatic use.
  requestBody:
    multipart/form-data: {file: {type: string, format: binary}, contentType?: string}
  responses:
    '200': {url, filename, contentType, size}
    '400': No file provided or file too large
```

**`tags: [Messages]`, `attachmentUrl`, 25 MB.** The operator's lead 4 was correct in full.

### 3.5 `POST /v1/posts` — headers, body, responses (S0)

**The `x-request-id` parameter — CORRECTION 1, verbatim:**

```yaml
parameters:
- name: x-request-id
  in: header
  required: false
  schema: {type: string, format: uuid}
  description: >
    Optional client-generated request identifier for safe retry (idempotency). When two requests carry the
    same value, the second is treated as a retry of the first and returns the original post (HTTP 200)
    instead of creating a duplicate. Window is ~5 minutes from the first request. Generate a UUID per
    logical call. SDKs do this automatically; HTTP clients should set it themselves or omit it.
```

**The operation description's idempotency contract, verbatim (S0):**

> **1. Same-request idempotency (5-minute window).** Pass an `x-request-id` header to mark a logical request.
> If a second request arrives with the same `x-request-id` while the first is in-flight (or within ~5 minutes
> of completion), we return **HTTP 200** with the original post in the `existingPost` field — no new post is
> created. … If you're using a generic HTTP client (curl, n8n's HTTP node, Zapier, custom code), either:
> Set a unique `x-request-id` per logical call (recommended — UUIDv4 is fine); **Or simply omit the header —
> we'll treat each request as new**.
>
> **Common pitfall**: if your workflow tool uses a single execution-level request ID and reuses it across
> multiple HTTP nodes (e.g. one ID for the whole run, shared across 6 different platform calls), every call
> after the first will look like a retry of the first and return its post. **Generate a fresh ID per node.**
>
> **2. Content-hash dedup (24-hour window).** Independently, we hash `(platform, accountId, content + media
> URLs)` and reject duplicates within 24 hours with **HTTP 409**. … Returns `error`, `accountId`, `platform`,
> and `existingPostId`.
>
> **Order: same-`x-request-id` retries (200) are checked first; if no idempotency match, the content-hash
> dedup (409) runs.**

**Response codes (S0 `responses` map): `201, 400, 401, 403, 409, 429`.**

| Code | Meaning |
|---|---|
| **201** | **Post created** — the *fresh-create* status |
| 400 | Validation error |
| 401 | Unauthorized |
| 403 | Forbidden — distinguish by `code`: `ACCOUNT_DISCONNECTED`, `PROFILE_OVER_LIMIT`, or no code (accountId not owned) |
| **409** | Duplicate content within 24 h on `(platform, accountId, content-hash)` **AND not an `x-request-id` retry**. Body: `error`, **`details.accountId`**, **`details.platform`**, **`details.existingPostId`** |
| 429 | Rate limit — API limit, **velocity limit (15 posts/hour per account)**, cooldown, or daily platform limits |

> **Two spec-level gaps, material to the `x-request-id` decision:**
> 1. **`200` is NOT in the `responses` map** — only `201, 400, 401, 403, 409, 429`. The idempotent-replay
>    status is described in prose only.
> 2. **`existingPost` is NOT schematised.** It occurs **4 times in the entire 1.75 MB spec** — twice in the
>    prose idempotency description, and twice as **`existingPostId`** (the *409* field, `{type: string}`).
>    **There is no schema for the `existingPost` object anywhere.**
>
> Consequence: implementing `x-request-id` means **parsing an unspecified field on an unenumerated status
> code**. That is a legitimate, evidence-based scope-control argument — and it is the **only** one this
> document makes (§7.9).

**Note — S8 vs S0 disagree on the 409 body.** S8 (the reference page) renders it flat
(`error, accountId, platform, existingPostId`); S0 nests under `details`
(`details.accountId`, `details.platform`, `details.existingPostId`). **S0 governs.** Recorded because a
future 409 handler must parse the nested shape.

**Request body top-level properties (S0)** — `required: None`:

| Property | Type |
|---|---|
| `title`, `content` | string |
| `mediaItems` | array\<MediaItem\> |
| `platforms` | array\<PlatformTarget\> — *"Required for non-draft posts (returns 400 if empty)"* |
| `scheduledFor`, `timezone` | string |
| `publishNow`, `isDraft`, `crosspostingEnabled` | boolean |
| `tags`, `hashtags`, `mentions` | array |
| `metadata` | object |
| **`tiktokSettings`** | **`$ref: TikTokPlatformData`** — *"Root-level TikTok settings applied to all TikTok platforms. **Merged into each platform's `platformSpecificData`, with platform-specific settings taking precedence.**"* |
| `facebookSettings` | `$ref: FacebookPlatformData` |
| `recycling`, `queuedFromProfile`, `queueId` | — |

### 3.6 `PlatformTarget` schema (S0)

```yaml
PlatformTarget:
  type: object
  properties:
    platform:   {type: string}   # twitter, threads, instagram, youtube, facebook, linkedin,
                                 # pinterest, reddit, tiktok, bluesky, googlebusiness, telegram
    accountId:  {oneOf: [{type: string}, {$ref: SocialAccount}]}
    customContent: {type: string}
    customMedia:   {type: array, items: {$ref: MediaItem}}
    scheduledFor:  {type: string, format: date-time}
    platformSpecificData:
      description: Platform-specific overrides and options.
      oneOf: [TwitterPlatformData, ThreadsPlatformData, FacebookPlatformData, InstagramPlatformData,
              LinkedInPlatformData, PinterestPlatformData, YouTubePlatformData, GoogleBusinessPlatformData,
              TikTokPlatformData, TelegramPlatformData, ...]
```

> **`platformSpecificData` IS the `TikTokPlatformData` object directly.** There is **no `tiktokSettings`
> property** on `PlatformTarget` or inside `TikTokPlatformData`. See §5.4.

### 3.7 `TikTokPlatformData` schema (S0) — all 17 properties

`draft` · `privacyLevel` · `allowComment` · `allowDuet` · `allowStitch` · `commercialContentType`
(enum: `none|brand_organic|brand_content`) · `brandPartnerPromote` · `isBrandOrganicPost` ·
**`contentPreviewConfirmed`** · **`expressConsentGiven`** · `mediaType` · `videoCoverTimestampMs` ·
`videoCoverImageUrl` · `photoCoverIndex` · `autoAddMusic` · `videoMadeWithAi` · `description`

`required: None` · **`additionalProperties`: unset → JSON-Schema default = ALLOWED**

Description: *"…`privacyLevel` must match creator_info options. **Both camelCase and snake_case accepted.**
… **The field `publish_type` is NOT supported.** Use `draft: true` for Creator Inbox flow."*

**All six of the client's fields map to real properties** (snake_case is accepted):

| Client field | Schema property | In schema? |
|---|---|---|
| `privacy_level` | `privacyLevel` | ✅ |
| `allow_comment` | `allowComment` | ✅ |
| `allow_duet` | `allowDuet` | ✅ |
| `allow_stitch` | `allowStitch` | ✅ |
| `content_preview_confirmed` | `contentPreviewConfirmed` | ✅ |
| `express_consent_given` | `expressConsentGiven` | ✅ |

**The field names are right. The nesting is not (§5.4).**

---

## 4. (B) Real Media-Size Analysis — Unchanged, Verdict Re-Grounded

*(Measurements unchanged from Rev 1; only the `upload-direct` reasoning is corrected.)*

### 4.1 The four failed prepared uploads

| Post | Prepared asset | Size |
|---|---|---|
| `post_04b29c9f7f2d` | `clip_df783647bdb2.mp4` | **3,851,706 B (3.67 MiB)** |
| `post_07e45c69ac0d` | `clip_64960b1fd132.mp4` | **1,583,720 B (1.51 MiB)** |
| `post_0943840705ce` | `clip_e4c6db743ced.mp4` | **1,811,719 B (1.73 MiB)** |
| `post_0a12cff53619` | base `clip_564a91798b1a.mp4` | 4,237,875 B (4.04 MiB) |
| ” | **shrunk, actually sent:** `clip_564a91798b1a.crf28.mp4` | **2,908,332 B (2.77 MiB)** |

**Largest asset actually presented: 3.67 MiB.** Size was never a factor in the 405 — and **405 is not in
Zernio's documented error taxonomy at all** (§3.5); an oversize body would be a `400` ("file too large") per
`upload-direct`, or a storage-level rejection on the signed PUT.

### 4.2 The 67 parked records

| Statistic | Bytes | MiB |
|---|---|---|
| min | 1,430,597 | **1.36** |
| median | 4,317,523 | **4.12** |
| p95 | 5,742,376 | **5.48** |
| max | 6,075,863 | **5.79** |
| mean | 4,098,307 | 3.91 |
| measured / missing | **67 / 0** | |

All 137 TikTok-routed clips: min **0.92** · median **4.46** · max **7.41 MiB**.

### 4.3 Against the two documented limits

| Limit | Source | Result |
|---|---|---|
| **`/v1/media/presign` — 5 GB** | S0 | **0 of 67 anywhere near.** Largest (5.79 MiB) = **0.11 %** of the limit |
| **`/v1/media/upload-direct` — 25 MB** | S0 | **0 of 67 over** (decimal or binary). Largest = **24.3 %** of the cap |
| TikTok platform — 4 GB / 10 min | S4 | 0 of 67 over |

### 4.4 Verdict on direct upload — **DO NOT SELECT** (verdict unchanged, evidence corrected)

| Operator's condition | Verdict |
|---|---|
| (a) every prepared asset safely under the documented limit | ✅ **MET** — max 7.41 MiB vs 25 MB, ~3.4× headroom |
| (b) **official documentation confirms it is supported for social-post `mediaItems`** | ❌ **NOT MET** — S0 tags it **`Messages`**, scopes its output to **`attachmentUrl` for inbox messages**, and contrasts it with presign, which the Media tag names as *the* path for post media |

**It is unsuitable on documented scope, not on size** — and it is moot: presign covers 5 GB, **675× the
largest asset**, for exactly this use case.

### 4.5 The 4 MB cap — a second legacy artifact, now doubly indicted

`config.py:1052-1058`, `.env` → **`FANOPS_ZERNIO_MAX_UPLOAD_MB=4`**, commented *"DEFAULT 4 MB (live-discovered
Zernio 413 ceiling)"*.

**`413` is not in Zernio's documented taxonomy** (§3.5), and the endpoint that produced it
(`/v1/media/upload`) is an **end-user flow** with no published contract. The cap is therefore a constant
reverse-engineered from an unsupported endpoint's undocumented behaviour.

Against the real contract: **presign = 5 GB (1,250×)**, and the Media tag says **Zernio auto-compresses**
oversize media anyway.

Measured cost on the parked queue:

| Cap | Clips re-encoded at `crf=28` |
|---|---|
| **4 MiB (current)** | **37 of 67 — 55 %** |
| 5 MiB | 8 of 67 — 12 % |
| 8 MiB | **0 of 67 — 0 %** |

**More than half of Moh Flow's TikTok output is visibly re-compressed to satisfy a limit the supported
contract does not impose, to avoid a status code the API does not document, on an endpoint that no longer
exists.** Not bundled — §8.6.

---

## 5. (C) Amended Current-Client Contract Matrix

`✅` client correct · `❌` fix in this PR · `⚠️` real gap, deliberately deferred

| # | Dimension | Current client | **OpenAPI (S0)** | Verdict |
|---|---|---|---|---|
| 1 | Token/presign endpoint | `POST {base}/media/upload-token` `{accountId}` (`:141`) | **`POST /v1/media/presign` `{filename, contentType, size?}`** — the token endpoint has **no path entry** | ❌ **FIX** |
| 2 | **Upload method** | **`POST`** (`:155`) | **`PUT`** to `uploadUrl` | ❌ **FIX — this is the 405** |
| 3 | Upload URL | hardcoded `{base}/media/upload`; `uploadUrl` **discarded** (`:148`) | use returned `uploadUrl` verbatim | ❌ **FIX** |
| 4 | Upload credential | `?token=` query param | signature embedded in `uploadUrl` | ❌ **FIX** |
| 5 | **Auth header on upload** | **sends `Bearer <key>`** (`:155`) | **none** — signed URL | ❌ **FIX — leaks the key off-origin** |
| 6 | Body encoding | multipart field `files` | **raw binary** | ❌ **FIX** |
| 7 | Content-Type | multipart part type | **`video/mp4` on the PUT**, matching presign | ❌ **FIX** |
| 8 | publicUrl source | `body["files"][0]["url"]` | **`publicUrl` from presign** (known pre-upload) | ❌ **FIX** |
| 9 | `accountId` on presign | **required** (`:132`) | **not a presign field** | ❌ **FIX** |
| **10** | **`size` on presign** | **not sent** | **`size?: integer` — "pre-validation (max 5GB)"** | ❌ **FIX — CORRECTION 2** |
| 11 | `contentType` value | `video/mp4` | **enum member** ✅ | ✅ |
| 12 | Base URL | `https://zernio.com/api/v1` | `servers: https://zernio.com/api` + `/v1/...` | ✅ **identical** |
| 13 | API auth | `Bearer` | `bearerAuth` | ✅ |
| 14 | Post endpoint | `POST {base}/posts` | `POST /v1/posts` | ✅ |
| 15 | `mediaItems` | `[{"type":"video","url":u}]` | `array<MediaItem>` | ✅ |
| 16 | `platforms[]` | `{platform, accountId}` | `PlatformTarget{platform, accountId, …}` | ✅ |
| 17 | `publishNow` | `True` | boolean | ✅ |
| 18 | TikTok field **names** | 6 snake_case fields | all 6 map to real properties; **snake_case accepted** | ✅ |
| **19** | **TikTok field NESTING** | **`platformSpecificData.tiktokSettings.{…}`** | **CONFLICT.** S0 models `platformSpecificData` **as** `TikTokPlatformData`; **S9 (Platform Settings guide) documents the client's nested wrapper verbatim**; S8 documents a root-level `tiktokSettings` | ⚠️ **OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, APPLICATION UNVERIFIED — §5.4. NOT changed** |
| 20 | Fresh-create status | accepts `200` **or** `201` (`:246`) | **`201`** = created; `200` = idempotent replay | ✅ **tolerant — correct** |
| 21 | Post-id parsing | `_id`/`id`/`postId` + nested `post.*` | response carries `post._id` | ✅ |
| **22** | **`x-request-id`** | **not sent** | **documented, optional UUID, ~5 min, → 200 + `existingPost`; "HTTP clients should set it themselves or omit it"** | ⚠️ **DEFERRED on scope control — §7.9. CORRECTION 1** |
| **23** | **`existingPost` parsing** | **absent** — `_extract_zernio_id` has no `existingPost` branch | prose-described; **NOT schematised** | ⚠️ **Deferred WITH #22 — they are inseparable (§7.5)** |
| 24 | 409 content-hash dedup | **unhandled** — falls to generic `break` → `failed` | `409` + `details.existingPostId`, 24 h | ⚠️ **Deferred — §8.6** |
| 25 | `platformPostUrl` | discarded; placeholder + reconcile (`:257`) | *"Immediate posts (`publishNow: true`) include `platformPostUrl`"* | ⚠️ **Deferred — §8.6** |
| 26 | 429 handling | blind exponential backoff (`:268`) | honour **`Retry-After`**; velocity limit 15/h per account | ⚠️ **Deferred — §8.6** |
| 27 | Error redaction | upload `:161` **withholds body**; accounts `:187` **redacts + includes** | error body `{error, type, code, param, docUrl}` | ❌ **FIX — sibling parity** |
| 28 | Signed-URL redaction | **none** — `redact()` knows only the API key | `uploadUrl` carries `X-Amz-Signature` | ❌ **FIX** |
| 29 | Size preflight | **4 MB** → crf-28 re-encode | **5 GB**; Zernio auto-compresses | ⚠️ **Deferred — §4.5, §8.6** |
| 30 | Media lifetime | not modelled | temp 7 d; permanent on publish; `uploadUrl` 1 h | ✅ **no change** — FanOps uploads seconds before posting |

### 5.5 Tally — **REV 3: RECOMPUTED FROM THE ROWS (C9)**

Rev 2 stated *"11 fixes … 8 already-correct rows, 7 deferred"*. **Both the 11 and the 8 were wrong, and they
did not sum to 30.** Recounted by enumerating every row rather than editing the sentence:

| Verdict | Rows | **Count** |
|---|---|---|
| ❌ **FIX in this PR** | 1, 2, 3, 4, 5, 6, 7, 8, 9, **10**, 27, 28 | **12** |
| ✅ **Already correct — no change** | 11, 12, 13, 14, 15, 16, 17, 18, 20, 21, **30** | **11** |
| ⚠️ **Deferred** | 19, 22, 23, 24, 25, 26, 29 | **7** |
| | **TOTAL** | **30** ✅ |

**12 + 11 + 7 = 30.** Rev 2's "11 fixes" omitted **row 10 (`size`)** — the very row **C2** had just added,
and its "8 already-correct" simply undercounted (it missed rows 18, 20, 30). **Both errors are arithmetic
drift from editing prose instead of recounting the rows.**

**Derived counts appearing elsewhere in this document, all recomputed:**

| Quantity | Value | Basis |
|---|---|---|
| Fixes | **12** | rows above |
| Already correct | **11** | rows above |
| Deferred | **7** | rows above |
| Total matrix rows | **30** | §5 |
| Corrections (Rev 2 + Rev 3) | **9** — C1-C6 (Rev 2), **C7-C9 (Rev 3)** | §10 |
| Deferred follow-ups | **6** distinct items | §8.6 |
| **Files touched** | **2** — `src/fanops/post/zernio.py`, `tests/test_zernio_presign.py` | §11.2 |
| **Functions rewritten** | **1** — `zernio_upload_media` | §8.2 |
| **Helpers added** | **4** — `_scrub_signed`, `_evidence`, `_scrubbed_transport`, `_put_signed` | §8.4 |
| **Helpers removed** | **1** — `_extract_zernio_media_url` (dead once the PUT body is no longer parsed; **C15**) | §14.1 |
| **Tests** | **47** | §9.11 |

### 5.4 Row 19 — the TikTok payload — **REV 3: RECLASSIFIED (C8)**

#### 5.4.1 What Rev 2 claimed — RETRACTED

| Rev 2 claim | Status |
|---|---|
| *"`SCHEMA-VALID BUT SEMANTICALLY INERT`"* | ❌ **RETRACTED** |
| *"Are the six settings CONVEYED? **Almost certainly NOT**"* | ❌ **RETRACTED** |
| *"the server accepts the object and reads **none** of the settings, falling back to defaults"* | ❌ **RETRACTED** |
| *"the **wrong level**"* / *"the nesting bug"* | ❌ **RETRACTED** — presupposes the conclusion |
| *"This is NOT a secondary supported representation"* | ❌ **RETRACTED** |
| *"probably never applied"* (`privacyLevel`) | ❌ **RETRACTED** |

**Refuting evidence — S9, the current official Platform Settings guide**
(`https://docs.zernio.com/guides/platform-settings`, retrieved 2026-07-16T23:2xZ) **documents the client's
exact shape, verbatim:**

```json
{
  "accountId": "tiktok-012",
  "platformSpecificData": {
    "tiktokSettings": {
      "privacy_level": "PUBLIC_TO_EVERYONE",
      "allow_comment": true,
      "allow_duet": true,
      "allow_stitch": true,
      "content_preview_confirmed": true,
      "express_consent_given": true,
      "description": "Full description here..."
    }
  }
}
```

The guide states explicitly: **"TikTok settings are nested inside `platformSpecificData.tiktokSettings`"** —
including the snake_case field names the client uses.

**This is byte-for-byte the FanOps payload.** The client is not "wrong"; it implements a **currently
documented official representation**.

#### 5.4.2 The error Rev 2 made — and it is the *same* error twice

Rev 1 over-trusted the **guides** and asserted absence. Rev 2, having been corrected, over-corrected into
treating the **OpenAPI as sole authority** and **inferred runtime behaviour from schema modelling** — never
fetching the Platform Settings guide, despite having *seen it in a search result*.

**Both revisions committed the same underlying error: reasoning from one source and asserting a conclusion
the source could not support.** A schema models a contract; it does not prove what a server ignores.
**The correct response to two conflicting official documents is to preserve the conflict, not to resolve it
by fiat in favour of whichever source one has most recently learned to trust.**

#### 5.4.3 The conflict, stated without resolution

| Source | Current? | Official? | What it documents |
|---|---|---|---|
| **S0** — OpenAPI 3.1.0, `v1.0.4` | ✅ | ✅ | `PlatformTarget.platformSpecificData: oneOf[… TikTokPlatformData …]` — models it **directly** as `TikTokPlatformData` (17 camelCase properties, **no `tiktokSettings` property**) |
| **S8** — Create Post reference | ✅ | ✅ | **Root-level** `tiktokSettings: $ref TikTokPlatformData` — *"merged into each platform's `platformSpecificData`, with platform-specific settings taking precedence"* |
| **S9** — Platform Settings guide | ✅ | ✅ | **`platformSpecificData.tiktokSettings.{…}`** — the **nested wrapper**, snake_case, **exactly the client's shape** |

**Three current official Zernio documents describe three arrangements. They are not reconcilable from
documentation alone.** Contributing factors: `TikTokPlatformData.additionalProperties` is unset (JSON-Schema
default **ALLOWED**), so the nested wrapper **validates** against the OpenAPI model too — meaning **the
OpenAPI does not reject the guide's shape**. Both can be true simultaneously if Zernio's server accepts both
forms and the OpenAPI simply under-models it.

> **CLASSIFICATION: `OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`**

**The record:**

1. **OpenAPI (S0) models `platformSpecificData` directly as `TikTokPlatformData`.**
2. **The official Platform Settings guide (S9) documents the nested `tiktokSettings` wrapper** — the shape
   FanOps sends.
3. **21 prior TikTok publishes (2026-06-29 → 07-05) prove the payload is ACCEPTED in production.**
4. **Neither source proves which values were APPLIED at the platform.** Acceptance is proven; application
   is not. **This document does not claim the settings were ignored, nor that they were honoured.**
5. **No TikTok payload change is included in Wave 1A.**
6. **A separate bounded verification is required only if the setting semantics matter operationally** —
   e.g. if `privacyLevel` or the consent flags must be provably transmitted rather than defaulted. That
   verification needs a live observation (does Zernio echo applied settings?) and is **out of scope here**.

**The OpenAPI does not automatically nullify another current official Zernio document.** Where S0 and S9
diverge, **the conflict stands unresolved and is recorded as such.**

#### 5.4.4 Disposition — unchanged, and now on firmer ground

**NOT CHANGED in Wave 1A.** The operator's rule: *"Change it only if the authoritative schema or tests prove
the current shape is not accepted."*

**Rev 3 satisfies that rule more strongly than Rev 2 did.** Rev 2 argued "the schema doesn't prove it's
unaccepted." Rev 3 shows **an official guide affirmatively documents it** and **21 production publishes
accepted it**. There is no basis for a change — and changing it would mean *departing from a documented
official representation* on the strength of a schema that does not reject it.

**One Rev 2 observation survives, demoted to a question:** the client's comment claims *"omitting them yields
400 'require media content'"* — still an odd error for missing TikTok settings. That is a **curiosity about a
06-29 note**, not evidence of a defect, and it does not bear on the shape's validity.

---

## 6. (D) Revised Root-Cause Determination

### 6.1 Proven obsolete legacy upload contract — **YES** (evidence corrected)

| Question | S0 answer |
|---|---|
| Complete media path list in the spec | **`/v1/media/presign`, `/v1/media/upload-direct`, `/v1/tools/validate/media`** — exactly three |
| Is `/v1/media/upload` a documented **path**? | **NO** — no path entry, no method, no schema |
| Is it **acknowledged**? | **YES** — in `upload-direct`'s description: *"Unlike `/v1/media/upload` (**which uses upload tokens for end-user flows**)"* — **CORRECTION 4** |
| Is `/v1/media/upload-token` anywhere? | **NO** |
| Client's own provenance | *"Two-step contract **DISCOVERED LIVE 2026-06-29**"*; module docstring: *"**operator-pasted docs**"* |

**The spec draws the exact distinction this incident is about.** `upload-direct` exists *because*
`/v1/media/upload` is **for end-user flows**; `upload-direct` is **"for programmatic use."**
**FanOps is a programmatic client that built on the end-user-flow endpoint** — the one Zernio implicitly
tells programmatic callers not to use.

### 6.2 A separate obsolete post-creation contract — **NO** (unchanged)

Rows 14-18, 20, 21 are ✅ against S0. `POST /v1/posts` with `content` / `platforms[{platform, accountId}]` /
`publishNow` / `mediaItems[{type, url}]`, snake_case TikTok field names, and `post._id` parsing all match.
**Only the upload is obsolete.** The post path needs **no contract change** — which is why `x-request-id`
(row 22) is an *addition*, not a *correction*.

The one post-path defect (row 19) is **nesting, not contract obsolescence** — and it is accepted by the
server today.

### 6.3 Unproven assumptions — named

| Assumption | Status |
|---|---|
| `/media/presign` **replaced** the legacy endpoint | **UNPROVEN — evidence points the other way.** S3 records **no** presign introduction and **no** media-upload change 06-29→07-16. Presign was likely **always** the supported path; the two coexisted, one published, one not |
| Zernio "broke" or "drifted" a contract it owed us | **REFUTED.** It never published a contract for `/v1/media/upload` |
| The legacy `/media/upload-token` still returns 2xx | **PROVEN** (the `:161` 405 is only reachable past it) — consistent with an end-user flow still serving Zernio's own UI. **Irrelevant to the fix** |
| The client's TikTok settings are applied | **UNPROVEN — and probably false** (§5.4) |
| 4 MB applies to presign | **UNPROVEN and contradicted** — S0 says 5 GB |
| Presign's real ceiling matches 5 GB | **UNPROVEN** — untestable without a live call |
| `existingPost`'s shape | **UNSPECIFIED** — prose only, no schema (§3.5) |

### 6.4 Classification — **`UNSUPPORTED LEGACY CONTRACT`** (retained, better grounded)

Report 08 §3.4 proved the cause class is **server-side** (client `(method, path)` byte-identical across the
success→failure boundary). **That proof stands.** But "server-side drift" implies Zernio changed a contract
it owed us. S0 shows something more specific:

| Test | S0 | Implication |
|---|---|---|
| Was the failing endpoint a published path? | **No** — 3 media paths, not including it | We were never owed its stability |
| Is it acknowledged? | **Yes — as an *end-user flow*** | It exists for Zernio's UI, not for API clients |
| Does the changelog record its removal? | **No** | Consistent — an unpublished endpoint has no deprecation duty |
| Is **405** in the documented taxonomy? | **No** — `201, 400, 401, 403, 409, 429` | A 405 is **not an API error**; it is a router refusing a method on a path outside the published surface |
| Is there a supported alternative? | **Yes — presign, tagged `Media`, named in the Media tag as *the* path for post media** | The remedy is **migration**, not adaptation |

> **VERDICT: `UNSUPPORTED LEGACY CONTRACT` supersedes `SERVER-SIDE CONTRACT DRIFT`.**
> FanOps built its upload on an endpoint Zernio scopes to **end-user flows** and never published a contract
> for. Zernio changed it — as it was free to do, with no changelog duty — and the client, pinned to a
> hardcoded path discovered by observation, began receiving a router-level 405 that isn't in the API's error
> vocabulary. **The four burned posts are the cost of depending on an unpublished surface.**

**Why this is load-bearing, not semantic:**

1. **It changes the fix** — migrate to the published contract; never restore the legacy path.
2. **It kills the fallback question** (§8.5).
3. **It predicts recurrence.** Every live-discovered constant from 06-29 is suspect and must be re-checked
   against the official contract: **(i)** the **4 MB cap** (§4.5) vs a documented 5 GB — **confirmed** a
   second legacy artifact; **(ii)** the **TikTok nesting** (§5.4) — re-checked and **NOT** an artifact: a
   current official guide (S9) documents that exact shape, and 21 publishes prove it accepted. **Whether
   the platform applies the values is unverified and unverifiable from documentation.** Both trace to the
   same session; only the first is a defect.
4. **It reassigns responsibility.** No vendor escalation. This is ours.

### 6.5 The 405 mechanism

Most likely, **consistent with all evidence, still unconfirmed**: Zernio migrated end-user uploads and
withdrew `POST` on that path. `POST` → **405 Method Not Allowed**, and RFC 9110 requires the response to
carry **`Allow`**. The client discards header and body (row 27), which is why four burns produced one
integer. **This does not gate the fix** — the fix is to use the published contract regardless.

---

## 7. `x-request-id` Audit (the operator's seven questions)

### 7.1 Does FanOps ever re-POST within one logical publication attempt? — **YES, in exactly two branches**

`ZernioPoster.publish` (`zernio.py:233-269`) loops `for attempt in range(_MAX_RETRIES)` (**4**) around
`requests.post(f"{self.base}/posts", …)`. Branch-by-branch:

| Line | Condition | Action | Re-POSTs? |
|---|---|---|---|
| **239** | `ConnectTimeout` **and** `attempt < _MAX_RETRIES-1` | `sleep(delay+jitter); delay*=2; continue` | ✅ **YES** |
| 244 | any other `RequestException` | `needs_reconcile`; `return` | ❌ |
| 255 | `200/201` but no id | `needs_reconcile`; `return` | ❌ |
| 259 | `200/201` + id | `submitted`; `return` | ❌ |
| 261 | `401` | **raise** `ZernioAuthError` (halts the run) | ❌ |
| 266 | `5xx` | `needs_reconcile`; `return` | ❌ |
| **268** | **`429`** | `sleep(delay+jitter); delay*=2; continue` | ✅ **YES** |
| 269 | other `4xx` | `break` → `failed` | ❌ |

**Two re-POST branches: `ConnectTimeout` (:239) and `429` (:268).** Both send a byte-identical payload with
**no idempotency header**. Outside the loop, the never-re-POST invariant holds: `_publish_one`'s CLAIM
re-reads under the ledger lock and publishes **only if still `queued`**, so a `submitting`/`submitted`/
`needs_reconcile` post is structurally never re-driven.

> **So the exposure is real but narrow:** a `429` whose first request actually landed (rate-limiter fired
> after the create), or a `ConnectTimeout` that was really a slow success, would double-post. `x-request-id`
> is precisely the documented remedy — and the `409` docs name it: *"To avoid 409s caused by retry loops,
> set a unique `x-request-id` per logical request."*

### 7.2 Where could a stable logical-attempt identifier be stored? — **It already exists**

`crosspost.py:243-246`:

```python
# AUDIT H1: stamp a stable, content-addressed CLIENT idempotency token at birth so
# an ambiguous publish is ALWAYS pollable (a real backend id overwrites it on
# publish). pid is content-addressed -> a re-run computes the identical token.
submission_id=f"fanops_{_hash('idemp', pid)}",
```

**`Post.submission_id` is born as a stable, content-addressed, per-post client idempotency token** — its own
comment calls it that. Properties:

| Property | Assessment |
|---|---|
| Stable across retries | ✅ persisted on the Post; identical on every read |
| **Unique per logical post** | ✅ derived from `pid`, which is **per-surface** — this **avoids the docs' "Common pitfall"** (one ID shared across platform calls) **by construction** |
| Deterministic across re-runs | ✅ content-addressed |
| **UUID format** | ❌ **`fanops_<hash>` is not a UUID** — the schema is `format: uuid` |
| **Survives publish** | ❌ **overwritten** — `:258` `post.submission_id = sid` replaces it with the real backend id |

### 7.3 How would the same UUID be reused across safe retries?

Derive deterministically, don't store a second field:

```python
_ZERNIO_NS = uuid.UUID("…")                       # fixed namespace constant
req_id = str(uuid.uuid5(_ZERNIO_NS, post.id))     # stable, UUID-format, per-post
```

Computed **once before the retry loop** and reused on every iteration → both re-POST branches (:239, :268)
carry the identical header. `uuid5` is deterministic, so it needs no persistence and cannot drift.

**Do not derive from `submission_id`** — it is overwritten at `:258` (§7.2), so it is stable only *before*
the first success. `post.id` is immutable.

### 7.4 How does it stay unique for a genuinely new intentional publication?

**It doesn't need to — the 5-minute window does that work.** `uuid5(ns, post.id)` is stable *forever*, but a
deliberate republication of the same post is necessarily **minutes-to-days later**, long past the ~5-minute
idempotency window, so Zernio treats it as new. Within the window, "the same post" *is* a retry — exactly
the intended semantics.

**The real duplicate guard for an intentional re-post is the 24-hour content-hash 409** (§7.7), which is
independent of `x-request-id` and keyed on `(platform, accountId, content + media URLs)`.

### 7.5 How is HTTP 200 `existingPost` parsed? — **It was not. SHIPPED 2026-07-17 (report 11).**

> **STATUS — CLOSED.** Retained as the record of why the header and the parser had to ship together. Both
> shipped in one PR (report 11 §4/§11): `_parse_create_body` classifies `200 + existingPost` as an
> `IdempotentReplay` → `submitted`, the same ledger state a first-time create takes. This section's central
> finding — **shipping the header alone would be strictly worse than shipping neither** — is exactly why they
> were never split. Its second finding also stands and is not "solved" by shipping: **`existingPost` remains
> unschematised** (prose-only; `200` still absent from the `responses` map), so the parser is deliberately
> tolerant and fails to `needs_reconcile`, never to `failed`, and the shape stays an **integration
> checkpoint** the operator confirms at the first live publish (report 11 §13.1).

Current (pre-fix): `:246` `if resp.status_code in (200, 201):` → `_extract_zernio_id(resp.json())`, which searches
`_id`/`id`/`postId` at top level, then nested **`post`**. **There is no `existingPost` branch.**

**So an idempotent replay would be parsed as a FAILURE:**

```
200 {"existingPost": {"_id": "abc"}}  ->  _extract_zernio_id() -> None
                                      ->  :255 needs_reconcile
                                      ->  "zernio 2xx but no recognizable post id"
```

A **successful, correctly-de-duplicated** publish would be misfiled as ambiguous and parked for manual
reconcile — **a bug introduced by adding the header alone**.

> **Therefore `x-request-id` and `existingPost` parsing are INSEPARABLE.** Shipping the header without the
> parser is strictly worse than shipping neither. And **`existingPost` has no schema** — 4 occurrences in
> 1.75 MB, all prose or the unrelated `existingPostId`; `200` isn't even in the `responses` map (§3.5). We
> would be parsing an **unspecified field on an unenumerated status**, unverifiable without a live call.

### 7.6 How does the 5-minute limit interact with `needs_reconcile`?

**It does not overlap with it at all — and this is the decisive scoping fact.**

| Scenario | Gap | Covered by `x-request-id`? |
|---|---|---|
| In-loop retry (`:239`, `:268`) | **milliseconds–seconds** | ✅ **YES** — deep inside the ~5-min window |
| A `needs_reconcile` post re-driven on a later pass | **≥ 10 min** (daemon interval 600 s) | ❌ **NO** — window long expired |
| An operator requeue days later | days | ❌ **NO** |

> **`x-request-id` protects *within-attempt* retries only. It does NOT protect *cross-pass* republication.**
> **The never-re-POST invariant must therefore remain exactly as it is** — `x-request-id` cannot justify
> relaxing the `needs_reconcile` park, because by the time a park is revisited the window has expired.
>
> Rev 1's §2.8 *conclusion* (don't relax the invariant) survives. Rev 1's §2.8 *reasoning* ("the feature
> doesn't exist") is retracted. **The feature exists; it simply does not cover the case the invariant
> guards.**

### 7.7 How is the separate 24-hour content-hash 409 handled? — **It was not. SHIPPED 2026-07-17 (report 11).**

> **STATUS — CLOSED.** This section is the point-in-time audit that scoped the fix; it is retained as the
> record. The defect it names (**R-3**) is fixed: a 409 now yields `ReconciliationRequired` →
> `needs_reconcile`, never `failed`, and `details.existingPostId` is preserved as
> `Post.reconcile_candidate_id`. See report 11 §3/§5/§9.

`409` fell through every branch to `break` → `failed` with `error_reason = "zernio 409 (body withheld)"`.
The `details.existingPostId` — which names the post Zernio already holds — was **discarded**.

**⛔ RETRACTED CLAIM — this section asserted, until 2026-07-17:**

> *"A 409 means the content is already live."*

**False, and corrected in report 11 §3.** Zernio is a hosted **scheduler**, so its duplicate check runs over
**its own post records**. A 409 proves only that **Zernio holds a matching record** within its 24h window —
**not** social-platform publication, **not** ownership by *this* FanOps record, **not** completion. The
matched record may be queued, failed, or rejected downstream; the key `(platform, accountId, content-hash)`
matches another FanOps record or an operator's manual post identically; and *our* request was **rejected**,
so nothing of ours completed. `existingPostId` is therefore a **candidate pointer, never an identity** — which
is why the shipped fix parks `needs_reconcile` with evidence rather than adopting the id as a
`submission_id`, and why this section's own suggestion of *"`submitted` with the recovered `existingPostId`"*
was **also wrong**: `submission_id` is a poll key, and `_RECONCILABLE` includes `needs_reconcile`, so
reconcile would have polled that id, found it live (of course — that is *why* we were rejected) and promoted
our row to `published` carrying **another post's permalink**. Silent misattribution.

The rest of the section stands: `failed` **is** re-queueable, so filing a duplicate-content 409 as `failed`
is a licence to post it again — a real, pre-existing defect independent of the 405 and of `x-request-id`. It
was **not reachable** while `queued = 0`, which is what made deferring it safe at the time.

### 7.8 The client's H5 comment was stale — **CORRECTED in Rev 4**

**⛔ RETRACTED HISTORICAL QUOTATION — the comment `zernio.py` carried until 2026-07-17:**

```python
# H5: Zernio carries NO client/server idempotency key on publishNow, so a re-POST would DOUBLE-publish.
```

**Its premise was FALSE as of Zernio's 2026-05-15 changelog entry** — `x-request-id` is documented. Rev 1
§2.8 cited this comment as *corroborated by the docs*; **retracted**. Its *conclusion* (a re-POST would
double-publish, so the queued-only claim carries the invariant) remained **true and necessary** (§7.6) — the
comment was right for the wrong reason.

**Rev 4 corrects the DOCUMENTATION only; it does NOT implement idempotency.** `zernio.py`'s module docstring
and `build_zernio_payload` comment now state the bounded truth: Zernio **documents** optional `x-request-id`
same-attempt idempotency · **FanOps does not send it yet** · FanOps therefore **continues to rely on the
queued-only claim check and `needs_reconcile`** for cross-pass safety · `x-request-id` + `existingPost`
parsing + 409 handling is a **required separate follow-up before the first production requeue**.

> The Rev 3 reasoning for deferring the comment fix — *"editing it here without shipping the feature would
> leave a comment describing code that doesn't exist"* — was **wrong**, and is retracted. It confused
> *describing the code* with *describing the vendor contract*. The corrected comment describes exactly what
> is true today: **what Zernio offers, what FanOps does not yet do, and what therefore still carries the
> invariant.** Leaving a knowingly false claim in active source until some future PR is not scope control;
> the same false premise is what let Rev 1 cite it as corroboration in the first place.

### 7.9 Recommendation: **B — defer to a separate, narrowly scoped follow-up** — **DISCHARGED 2026-07-17**

> **STATUS — the deferral is spent.** The "separate, narrowly scoped follow-up" this section recommended is
> report 11, designed and landed as one PR under its own operator gate. Grounds 1–4 below were the *deferral*
> grounds; here is what became of each:
>
> | # | Ground | Disposition |
> |---|---|---|
> | 1 | `existingPost` is unspecified | **Still true — not resolved by shipping.** The parser is tolerant and fails to `needs_reconcile`; the shape is an integration checkpoint (report 11 §13.1) |
> | 2 | Header + parser are inseparable; blast radius grows past the upload | **Accepted and paid deliberately**, in a PR of its own rather than inside the upload fix. The final surface is narrower than first designed: the typed result is **private to the Zernio backend**, so `postiz.py` / `dryrun.py` / the `Poster` protocol are untouched (report 11 §0 D7, §10) |
> | 3 | Unverifiable here; correct behaviour is only observable live | **Still true.** 71 offline tests lock the shape; the first live publish remains an operator-gated checkpoint (report 11 §13.6) |
> | 4 | Zero live exposure today (`queued = 0`) | **This is what expires.** It made deferral safe *while nothing could publish*. Requeueing the four burned records reopens the window, which is why report 11 was required **before** any production requeue |

**Grounds — scope control and verification risk only. NOT "undocumented": it is documented, officially
recommended for HTTP clients, and FanOps is an HTTP client.**

| # | Ground |
|---|---|
| 1 | **`existingPost` is unspecified.** 4 prose occurrences, no schema, `200` absent from the `responses` map. Implementing means parsing an unspecified field on an unenumerated status (§7.5) |
| 2 | **Header + parser are inseparable** (§7.5). Together they touch `ZernioPoster.publish` **and** `_extract_zernio_id` — expanding the blast radius from *one upload function* to **three call sites across the post path**, which §6.2 establishes is otherwise **already correct**. Wave 1A's whole thesis is that only the upload is broken |
| 3 | **Unverifiable here.** Correct behaviour is only observable on a real 429/ConnectTimeout retry against the live API — no Zernio call is authorised, and the branches can't be reached offline except by mock, which proves only that we send what we think we send |
| 4 | **Zero live exposure today.** `queued = 0`. `publish_due` iterates `queued` only, so **:239 and :268 are unreachable**. Deferring closes no window that is currently open |
| 5 | **No safety cost.** The never-re-POST invariant is unchanged either way (§7.6), and omitting the header is **explicitly sanctioned**: *"Or simply omit the header — we'll treat each request as new"* |
| 6 | **The follow-up is well-formed**: `uuid5(ns, post.id)` + an `existingPost` branch + 409 handling (§7.7) + the H5 comment correction — one coherent idempotency PR, testable as a unit, landing **before** any requeue |

**Requeue interlock:** the two branches `x-request-id` protects become reachable **the moment anything is
requeued**. The follow-up must therefore land **before the first requeue**, not merely "eventually." That is
a sequencing constraint on the requeue decision, not on this PR.

---

## 8. (E) Revised Implementation Proposal

**Scope: `zernio_upload_media` + FOUR module-private helpers (`_scrub_signed`, `_evidence`,
`_scrubbed_transport`, `_put_signed`), minus one dead one (`_extract_zernio_media_url`). One source file.
No Postiz. No requeue.**

### 8.1 Change list

| # | Change | Row(s) |
|---|---|---|
| 1 | Rewrite `zernio_upload_media` → presign + signed PUT | 1-9 |
| 2 | **Send `size`** on presign — **CORRECTION 2** | 10 |
| 3 | Add `_evidence` + `_scrub_signed`; apply to presign and PUT | 27, 28 |
| 4 | Delete the legacy path — **no fallback** | — |
| 5 | Tests (§9) | — |

### 8.2 The rewrite

```python
def zernio_upload_media(cfg: Config, path: Path, *, account_id: str | None = None) -> str:
    """Upload to Zernio via the OFFICIAL presigned flow (OpenAPI 3.1.0 `Zernio API v1.0.4`, retrieved
    2026-07-16; paths./v1/media/presign). Supersedes the reverse-engineered /media/upload-token +
    POST /media/upload contract — an END-USER-FLOW endpoint Zernio never published a contract for, which
    now returns 405 (report 09 §6).
      1) POST {base}/media/presign {"filename","contentType","size"} -> {uploadUrl, publicUrl, key, expiresIn}
      2) PUT <uploadUrl> raw bytes, Content-Type MUST match presign's contentType, and NO Authorization
         header (the URL is pre-signed; sending the key would leak it to third-party storage).
      3) caller puts publicUrl in mediaItems[].
    account_id is accepted for signature compatibility and is UNUSED — presign is account-agnostic."""
    ctype = "video/mp4"                               # an enum member of presign's contentType
    cap = cfg.zernio_max_upload_bytes                 # UNCHANGED — 4 MB legacy artifact, see §8.6
    path = maybe_shrink_for_cap(cfg, path, cap, label="zernio")
    size = path.stat().st_size                        # POST-shrink: the size we will actually PUT
    if size > cap:
        raise RuntimeError(f"zernio oversize: {size} bytes > {cap} — re-render short")

    # Step 1 — presign (Bearer REQUIRED). size is optional but documented for pre-validation.
    # The presign URL is NOT a credential, so bounded redacted transport evidence is safe here — but the
    # API key must still never appear, hence redact() on the transport path too.
    try:
        r = requests.post(f"{_base(cfg)}/media/presign",
                          headers={"Authorization": f"Bearer {_key(cfg)}", "Content-Type": "application/json"},
                          json={"filename": path.name, "contentType": ctype, "size": size}, timeout=30)
    except requests.RequestException as exc:
        raise _scrubbed_transport(exc, "presign", cfg) from None
    if r.status_code == 401:
        raise ZernioAuthError("Zernio 401 on media presign — check ZERNIO_API_KEY (response body withheld)")
    if r.status_code >= 300:
        raise RuntimeError(f"Zernio presign failed ({r.status_code}): {_evidence(cfg, r)}")
    try:
        body = r.json(); upload_url = body.get("uploadUrl"); public_url = body.get("publicUrl")
    except Exception:
        upload_url = public_url = None
    if not upload_url or not public_url:
        raise RuntimeError("Zernio presign 2xx but no uploadUrl/publicUrl (body withheld)")

    # Step 2 — signed PUT. NO Authorization header (spec: the URL carries the signature).
    # C7: the transport MUST be wrapped. A requests exception embeds the full signed uploadUrl in BOTH
    # str(exc) AND exc.request.url, and run.py:360 redacts only the two API KEYS — so an unwrapped
    # Timeout/ConnectionError writes X-Amz-Signature straight into the ledger's error_reason (§8.5).
    resp = _put_signed(upload_url, path, ctype)
    if resp.status_code >= 300:
        raise RuntimeError(f"Zernio signed upload failed ({resp.status_code}): {_evidence(cfg, resp)}")

    # Step 3 — publicUrl came from step 1; the PUT body is NOT parsed for it.
    return public_url
```

### 8.3 `size` disposition — **SEND IT**, justified from the schema

**Decision: send `size = path.stat().st_size`, computed AFTER `maybe_shrink_for_cap`.**

| Justification | Schema basis |
|---|---|
| It is **supported** | `properties.size: {type: integer}` — present in the request schema |
| It is **safe to send** | `required: [filename, contentType]` — `size` is **optional**; omitting or sending are both valid |
| It is **for exactly this** | description: *"Optional file size in bytes **for pre-validation** (max 5GB)"* |
| **Post-shrink is the correct value** | Pre-validation is only meaningful for the bytes actually PUT. `maybe_shrink_for_cap` may rewrite the file, so the pre-shrink size would validate **a file we will not send** — worse than omitting it |
| It **cannot** trip the limit | max 5 GB vs our max 7.41 MiB (0.15 %) |
| **Benefit** | A mismatch fails at **presign** (a cheap `400`) instead of after a multi-MB PUT to storage |

> The operator's default recommendation is adopted, with the schema-grounded refinement that the value must
> be read **after** the shrink. Rev 1's omission is retracted.

### 8.4 Bounded, redacted evidence + the transport-exception wrap — **REV 3 (C7)**

```python
_SIGNED_Q = re.compile(r"([?&](?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|Signature|sig)=)[^&\s\"']+",
                       re.I)

def _scrub_signed(s: str) -> str:
    return _SIGNED_Q.sub(r"\1<redacted>", s)

def _evidence(cfg: Config, resp) -> str:
    """Bounded, redacted RESPONSE evidence. Includes Allow on a 405 — RFC 9110 requires the server to name
    the permitted methods there, and discarding it is why 4 burns yielded one integer (report 09 §6.5)."""
    allow = resp.headers.get("Allow")
    body = _scrub_signed(redact(resp.text or "", cfg.zernio_api_key))[:400]
    return (f"Allow={allow!r} " if allow else "") + f"body={body!r}"

def _scrubbed_transport(exc: "requests.RequestException", stage: str, cfg: Config | None = None):
    """C7: a requests exception is NOT safe to propagate from a SIGNED url. str(exc) embeds the full URL
    ('...Max retries exceeded with url: /temp/x.mp4?X-Amz-Signature=...') and exc.request.url holds it too;
    run.py:360 redacts only the two API KEYS, so it would land in the ledger's error_reason (§8.5).

    Re-raise the SAME CLASS, not RuntimeError: run.py's _is_transient_publish_error classifies a
    RequestException by TYPE (ConnectionError/Timeout -> transient, retried) but a RuntimeError by MESSAGE
    SUBSTRING — so wrapping in RuntimeError would silently make a ConnectionError terminal (§8.4.1). The
    fresh instance carries NO request/response, so exc.request.url cannot leak either."""
    name = type(exc).__name__
    if stage == "signed-put":
        msg = f"Zernio signed upload transport failed ({name})"      # class + stage ONLY. No str/repr/host/URL.
    else:
        detail = _scrub_signed(redact(str(exc), cfg.zernio_api_key if cfg else ""))[:200]
        msg = f"Zernio {stage} transport failed ({name}): {detail}"  # presign URL is not a credential
    return type(exc)(msg)

def _put_signed(upload_url: str, path: Path, ctype: str):
    """The signed PUT, with its transport exception scrubbed (C7). NO Authorization header."""
    with open(path, "rb") as fh:
        try:
            return requests.put(upload_url, data=fh, headers={"Content-Type": ctype}, timeout=300)
        except requests.RequestException as exc:
            raise _scrubbed_transport(exc, "signed-put") from None
```

| Rule | Rationale |
|---|---|
| `redact(…, cfg.zernio_api_key)` | House helper, already used at `:187` — closes the sibling-parity gap (row 27) |
| **`_scrub_signed`** | **`redact()` knows only the API key.** A signed `uploadUrl` carries `X-Amz-Signature` — an **upload credential** (row 28) |
| **Signed-PUT transport → class + stage ONLY** | **C7.** No `str(exc)`, no `repr(exc)`, no host, no URL |
| **`from None`** | Drops `__context__`, so no traceback prints the original exception's URL-bearing message |
| **Fresh instance, no `request`/`response`** | `exc.request.url` is a **second** copy of the signed URL. Constructing a bare instance discards it |
| **Presign transport → bounded redacted `str(exc)`** | Its URL (`…/api/v1/media/presign`) is **not** a credential. Capped at 200 chars; API key still redacted |
| 400-char cap on response evidence | Bounded, per instruction |
| **`Allow` on 405** | The answer four burns failed to read |
| **401 still withholds the body** | Matches existing auth-error discipline (`:144`, `:159`, `:185`) |

#### 8.4.1 A correction to the operator's specified remedy — `RuntimeError` would burn retryable posts

The operator specified:

```python
except requests.RequestException as exc:
    raise RuntimeError(f"Zernio signed upload transport failed ({type(exc).__name__})") from None
```

**The redaction intent is exactly right and is adopted verbatim.** But the **exception class** must be
preserved, because `run.py:71-99` classifies by type for `requests` errors and by **message substring** for
`RuntimeError`:

```python
if isinstance(exc, requests.exceptions.RequestException):
    return isinstance(exc, (ConnectionError, ConnectTimeout, Timeout, ReadTimeout))   # by TYPE
if isinstance(exc, RuntimeError):
    ...  re.search(r'\((\d{3})\)', msg)  ...                                          # by SUBSTRING
    if any(x in lower for x in (..., "max retries exceeded", "connection refused",
                                "connection reset", "connection aborted")): return True
    if "timed out" in lower or "timeout" in lower: return True
    return False
```

Trace `RuntimeError("Zernio signed upload transport failed (ConnectionError)")`:

| Check | Result |
|---|---|
| `isinstance(exc, RequestException)` | **False** — it is a `RuntimeError` now |
| `re.search(r'\((\d{3})\)')` on `(ConnectionError)` | **no match** — not 3 digits |
| substring vs `connection refused` / `connection reset` / `connection aborted` / `max retries exceeded` | **no match** — the string is `connectionerror` |
| `"timed out" in lower or "timeout" in lower` | **no match** |
| **`_is_transient_publish_error`** | **→ `False`** |

**Consequence: a `ConnectionError` on the signed PUT — transient TODAY, retried up to
`_PUBLISH_TRANSIENT_MAX` (`run.py:67` = **3**; corrected in Rev 4 §14.1, C10 — this document said 4, which is
`zernio.py`'s same-named constant, a different one) — would become terminal, burning the post on the first
network blip.**

Worse, it would be **inconsistent**: `RuntimeError("… (Timeout)")` lowercases to `… (timeout)`, which **does**
match `"timeout" in lower` → still transient. So **timeouts would retry and connection errors would burn** —
a split that exists in neither the current code nor the intent.

**Fix: `raise type(exc)(msg) from None`.** Same class → same type-based classification → **behaviour
byte-identical to today**, with the message scrubbed and `request`/`response` dropped. Test **20** (§9.4)
pins this as a regression guard.

> This is the operator's finding and the operator's remedy; only the **exception class** is amended, with
> the trace above as the evidence.

### 8.5 The leak path — confirmed concrete, not theoretical

`run.py:351-372`, the handler that catches everything `_ensure_media` raises:

```python
except Exception as exc:
    if _is_fatal_auth_error(exc): raise
    if _is_transient_publish_error(exc) and attempt < _PUBLISH_TRANSIENT_MAX - 1: ...continue
    if post.state is not PostState.needs_reconcile:
        if _is_transient_publish_error(exc):
            red = redact(str(exc), cfg.postiz_api_key, cfg.zernio_api_key)   # <-- ONLY the two API keys
            ...
            post.error_reason = "publish failed: " + red                     # <-- WRITTEN TO THE LEDGER
```

**`redact()` receives only `cfg.postiz_api_key` and `cfg.zernio_api_key`. It has no concept of
`X-Amz-Signature`.** So, with Rev 2's proposal (response sanitized, transport not):

```
requests.put(signed_url, ...) raises ConnectionError
  str(exc) = "HTTPSConnectionPool(host='media.zernio.com', port=443): Max retries exceeded with
              url: /temp/1752...my-video.mp4?X-Amz-Signature=a1b2c3...&X-Amz-Credential=...  (Caused by ...)"
  -> propagates through _ensure_media, unwrapped
  -> run.py:360  redact(str(exc), <postiz key>, <zernio key>)   # signature untouched
  -> post.error_reason = "publish failed: ...X-Amz-Signature=a1b2c3..."
  -> PERSISTED TO THE LEDGER, rendered in the Studio UI, and emitted to the daemon log
```

**Rev 2 would have shipped this.** Its §8.4 claim — *"The signed `uploadUrl` is never logged in full, never
written to the ledger"* — was **false for the transport path**. Retracted; the claim is re-scoped in §8.5.1.

#### 8.5.1 Security claims — downgraded until the tests exist

| Rev 2 claim | Rev 3 status |
|---|---|
| *"The signed `uploadUrl` is never logged in full, never written to the ledger, never returned."* | ❌ **RETRACTED as stated** — true for responses, **false for transport exceptions** |
| **Rev 3 claim** | *"With `_put_signed` (§8.4) and tests 15-20 (§9.4) green, the signed `uploadUrl` and its `X-Amz-*` credentials cannot reach `error_reason`, the ledger, or the logs via either the response path or the transport path. **Until those tests exist and pass, this is an intent, not a guarantee.**"* |

**Per the operator's instruction, no claim that the signed URL cannot reach the ledger is made until tests
15-20 exist.** The only thing returned is `publicUrl` — unsigned and intentionally public.

### 8.6 No legacy fallback

**No fallback to `/media/upload-token` + `POST /media/upload`.** Permitted only if *"current official
evidence requires it"* — the evidence forbids it:

1. It is **not a published path** (§6.1) — there is nothing in the supported API to fall back to.
2. The spec scopes it to **end-user flows**, explicitly contrasted with **programmatic use** (§3.4).
3. It **currently returns 405** — a fallback could only ever fail.
4. It is the **proven cause** of four burned posts.

**Delete it.** Fallback code that can only fail is worse than none: it invites a retry loop and muddies the
error.

### 8.7 Deliberately NOT bundled — **six** items

| Finding | Row | Why deferred |
|---|---|---|
| **`x-request-id` + `existingPost` + 409 handling** | 22, 23, 24 | §7.9 — **scope control**: unspecified `existingPost`, expands blast radius to the already-correct post path, unverifiable offline, **currently unreachable (`queued=0`)**. **Must land before the first requeue** |
| **TikTok nesting — settings likely inert** | 19 | §5.4 — the schema does **not** prove the shape is unaccepted (permissive `additionalProperties`; 21 live successes). Operator rule → no change. **Needs its own live probe** |
| **4 MB cap → 55 % re-encoded** | 29 | §4.5 — changes **output quality**, a different risk surface. Land the transport fix with the cap intact (**zero quality delta**), prove presign, then raise on measured evidence. 8 MiB → 0 % |
| `platformPostUrl` discarded | 25 | Reconcile-path change, not an upload fix |
| 429 ignores `Retry-After` | 26 | Real, but not on the 405 path |
| H5 comment is stale | — | §7.8 — belongs with the idempotency PR |

---

## 9. (F) Revised Test Matrix — **REV 3**

Offline, mocked HTTP. **No live Zernio call. CI-only** (project rule). Target
`tests/test_zernio_presign.py`. **47 tests.**

**Removed in Rev 2:** ~~`test_size_field_not_sent`~~ (**C2**) · ~~`test_no_x_request_id_header_sent`~~ (**C1**).
Both asserted the *absence* of documented features and would have **locked the errors in as regressions**.

**Added in Rev 3:** **§9.4 — six transport-exception tests (C7)**, including **test 20**, the regression
guard for the class-preserving re-raise (§8.4.1). **Renumbered throughout** rather than patched.

**Reworded in Rev 3:** **test 39** — the TikTok characterisation test no longer labels the shape inert or
correct (**C8**).

### 9.1 Success path

| # | Test | Asserts |
|---|---|---|
| 1 | `test_presign_then_put_returns_public_url` | presign called once; **`PUT`** to the returned `uploadUrl`; returns **`publicUrl`** |
| 2 | `test_presign_url_is_base_plus_media_presign` | exactly `https://zernio.com/api/v1/media/presign` — **pins the doubled-`v1` trap** (§3.2) |
| 3 | `test_public_url_flows_into_media_items` | `mediaItems:[{"type":"video","url":<publicUrl>}]` |
| 4 | `test_account_id_not_sent_to_presign` | `accountId` absent (row 9) |
| 5 | `test_size_sent_and_is_post_shrink_bytes` | **C2.** `size` present, integer, **== post-shrink `st_size`**, not the original |
| 6 | `test_content_type_is_enum_member` | `contentType == "video/mp4"` — a presign enum member |

### 9.2 PUT header rules — security-critical

| # | Test | Asserts |
|---|---|---|
| 7 | `test_put_carries_no_authorization_header` | **`Authorization` NOT in the PUT headers** — pins *"no auth header needed"*; prevents leaking the key to third-party storage |
| 8 | `test_put_content_type_matches_presign` | PUT `Content-Type` == presign `contentType` |
| 9 | `test_put_body_is_raw_bytes_not_multipart` | raw file object; **no `files=`**, no boundary |
| 10 | `test_put_method_is_put_not_post` | **directly pins the 405 regression** |

### 9.3 Signed-URL redaction — RESPONSE path

| # | Test | Asserts |
|---|---|---|
| 11 | `test_signed_url_signature_never_in_response_error` | a failing PUT whose **body** echoes the signed URL → `X-Amz-Signature` value absent, `<redacted>` present |
| 12 | `test_signed_url_never_in_ledger_error_reason_from_response` | `post.error_reason` carries no `X-Amz-Signature`/`X-Amz-Credential` |
| 13 | `test_api_key_never_in_error_evidence` | `redact()` still strips the key |
| 14 | `test_evidence_is_bounded` | ≤400 chars for a 1 MB error body |

### 9.4 Signed-URL redaction — TRANSPORT-EXCEPTION path — **NEW (C7)**

Each test raises the exception from the mocked `requests.put` with a **realistic** message embedding the full
signed URL — e.g. `HTTPSConnectionPool(host='media.zernio.com', port=443): Max retries exceeded with url:
/temp/1752_abc_v.mp4?X-Amz-Signature=DEADBEEF&X-Amz-Credential=AKIA%2F...&X-Amz-Security-Token=TOK (Caused by ...)`
— **and** sets `exc.request.url` to the same signed URL, so the second leak vector is covered too.
Assertions sweep **the raised exception, `post.error_reason` after a full `_publish_one`, and the captured
log stream**.

| # | Test | Asserts |
|---|---|---|
| **15** | `test_put_timeout_signature_absent_from_exception_error_reason_and_logs` | `requests.Timeout` → **`X-Amz-Signature`** appears in **none** of: `str(raised)`, `post.error_reason`, the log stream |
| **16** | `test_put_connection_error_credential_and_token_absent_everywhere` | `requests.ConnectionError` → **`X-Amz-Credential`** and **`X-Amz-Security-Token`** appear in none of the three sinks |
| **17** | `test_put_transport_error_never_contains_full_upload_url` | the **full `uploadUrl`** (and its host, path, and any query string) appears in none of the three sinks |
| **18** | `test_put_transport_error_message_is_class_and_stage_only` | `str(raised)` **== exactly** `"Zernio signed upload transport failed (<ClassName>)"` — no `str(exc)`, no `repr(exc)`, no host, no URL. Also: `raised.request is None` and `raised.response is None` (**`exc.request.url` cannot leak**), and `raised.__cause__ is None` (`from None`) |
| **19** | `test_put_transport_failure_never_calls_posts` | **`POST /posts` is never called** after a transport failure |
| **20** | `test_put_connection_error_remains_transient` | **REGRESSION GUARD (§8.4.1).** `_is_transient_publish_error(raised)` **is `True`** — the class-preserving re-raise keeps a `ConnectionError` transient. **Fails if the re-raise is ever changed to `RuntimeError`** |

### 9.5 Status-code matrix

| # | Code | Surface | Asserts |
|---|---|---|---|
| 21 | **405** | PUT | `error_reason` includes **`Allow=`** — *the regression that cost four posts* |
| 22 | **405** | presign | same, redacted |
| 23 | **400** | presign | redacted `{error, type, code}`; post → `failed`. **The documented presign 400**: *"missing filename, contentType, or unsupported content type"* |
| 24 | **401** | presign | **`ZernioAuthError`** (typed, halts the run); **body withheld** |
| 25 | **401** | PUT | `ZernioAuthError` **NOT** raised — a signed URL has no auth; classified as an upload failure |
| 26 | **413** | PUT | clean failure + evidence. **Not in Zernio's taxonomy (§3.5) — must not be special-cased** |
| 27 | **429** | presign | surfaces with evidence; never silently succeeds |
| 28 | **500** | presign | post → `failed`, never `published` |
| 29 | **502** | presign | `platform_error` is not a success |

### 9.6 Malformed responses

| # | Test | Asserts |
|---|---|---|
| 30 | `test_presign_2xx_but_no_upload_url` | clean `RuntimeError`, **no `PUT` attempted** |
| 31 | `test_presign_2xx_but_no_public_url` | clean `RuntimeError`, **no `PUT` attempted** |
| 32 | `test_presign_2xx_non_json` | no unhandled `JSONDecodeError` |
| 33 | `test_presign_2xx_empty_body` | clean failure |
| 34 | `test_put_2xx_body_ignored` | step-1 `publicUrl` returned even if the PUT body is empty/garbage |

### 9.7 Transport — presign side

| # | Test | Asserts |
|---|---|---|
| 35 | `test_presign_transport_error_is_bounded_and_redacted` | presign `ConnectionError` → bounded (≤200 ch) redacted detail; **API key absent**; presign URL **may** appear (not a credential) |
| 36 | `test_presign_transport_error_preserves_class` | `_is_transient_publish_error` still `True` — same class-preserving rule (§8.4.1) |
| 37 | `test_presign_transport_failure_never_calls_put_or_posts` | neither the signed PUT nor `/posts` is reached |

### 9.8 mediaItems + TikTok — regression guards on unchanged code

| # | Test | Asserts |
|---|---|---|
| 38 | `test_media_items_not_legacy_media_key` | `mediaItems`, never `media` |
| **39** | **`test_tiktok_payload_shape_unchanged`** | **CHARACTERISATION — REWORDED IN REV 3 (C8).** Pins the **current, production-accepted** shape `platformSpecificData.tiktokSettings.{6 snake_case fields}` **exactly as it is**. Docstring must read: *"Pins the shape FanOps sends today. The official Platform Settings guide documents this nested form verbatim; the OpenAPI models `platformSpecificData` directly as `TikTokPlatformData`; the two current official sources **conflict** (report 09 §5.4). 21 production publishes prove it is **accepted**; **which values the platform applies is unverified**. This test makes any change deliberate and visible — it does **not** assert the shape is correct, incorrect, or inert."* |
| 40 | `test_platforms_shape` | `[{"platform":"tiktok","accountId":…}]` |

> Rev 1 asserted the shape was **correct**. Rev 2 asserted it was **inert**. **Rev 3 asserts only that it is
> unchanged, and records the conflict.**

### 9.9 Idempotency — the invariant, **not** the absence

| # | Test | Asserts |
|---|---|---|
| 41 | `test_needs_reconcile_post_is_never_republished` | **EXISTING — must stay green.** The never-re-POST invariant (§7.6) |
| 42 | `test_ambiguous_5xx_parks_needs_reconcile` | ambiguous 5xx → `needs_reconcile`, **never** `failed` |
| 43 | `test_publish_only_from_queued` | **EXISTING** — the queued-only filter; the invariant's real foundation |

> **Rev 1's `test_no_x_request_id_header_sent` is GONE.** It would have pinned the **absence** of a
> documented, officially-recommended feature — converting a documentation error into an enforced regression
> and actively blocking the §7.9 follow-up.

### 9.10 Ledger integrity

| # | Test | Asserts |
|---|---|---|
| 44 | `test_upload_failure_sets_failed_with_reason` | `state=failed`, reason set, `media_id`/`public_url`/`published_at` all `None` |
| 45 | `test_upload_failure_leaves_other_posts_untouched` | no collateral change |
| 46 | `test_success_transitions_queued_to_submitted_only` | never skips to `published` |
| 47 | `test_no_publish_from_awaiting_approval` | **EXISTING** — the parked 343 stay unpublishable |

### 9.11 Consistency check against the §5 matrix — **RECOMPUTED (C9)**

| §9 section | Tests | Count |
|---|---|---|
| 9.1 Success | 1-6 | 6 |
| 9.2 PUT headers | 7-10 | 4 |
| 9.3 Redaction — response | 11-14 | 4 |
| **9.4 Redaction — transport (NEW)** | **15-20** | **6** |
| 9.5 Status codes | 21-29 | 9 |
| 9.6 Malformed | 30-34 | 5 |
| 9.7 Transport — presign | 35-37 | 3 |
| 9.8 mediaItems + TikTok | 38-40 | 3 |
| 9.9 Idempotency | 41-43 | 3 |
| 9.10 Ledger | 44-47 | 4 |
| | **TOTAL** | **47** |

6+4+4+6+9+5+3+3+3+4 = **47**, numbered 1-47 with no gaps or repeats.

**Row coverage:**

| §5 rows | Coverage |
|---|---|
| 1-9 (upload defects) | 1-4, 7-10 |
| **10 (`size`)** | **5** |
| 11-18, 20, 21, 30 (already correct) | 6, 38, 40 — regression guards |
| **19 (TikTok conflict)** | **39 — characterisation only, no verdict** |
| 22, 23 (x-request-id / existingPost) | **intentionally untested — deferred (§7.9)** |
| 24, 25, 26, 29 (409 / permalink / Retry-After / cap) | **intentionally untested — deferred (§8.7)** |
| **27, 28 (redaction — response AND transport)** | **11-14, 15-20, 35-37** |

**Two invariants of this matrix:**
1. **No test asserts the absence of a documented feature.** ✅
2. **No test asserts a verdict the evidence does not support** — test 39 pins behaviour, not correctness. ✅

## 10. Correction Ledger

| # | Rev 1 claim | Refuting evidence (S0) | Implementation consequence | Test consequence | Scope |
|---|---|---|---|---|---|
| **C1** | `x-request-id` **not documented**; adding it is unsafe; client is correct | `paths./v1/posts.post.parameters[0]` — optional UUID header, ~5 min, → 200 + `existingPost`; *"HTTP clients should set it themselves or omit it"* | **Deferred on scope control** (§7.9), **not** on "undocumented". H5 comment marked **stale** | **`test_no_x_request_id_header_sent` REMOVED** | **None** |
| **C2** | `size` **not documented**; proposal omits it | `paths./v1/media/presign…properties.size: {type: integer, description: "Optional file size in bytes for pre-validation (max 5GB)"}` | **`size` IS SENT**, post-shrink (§8.3) | **`test_size_field_not_sent` REMOVED**; **`test_size_sent_and_is_post_shrink_bytes` ADDED** | **None** |
| **C3** | `/v1/media/upload-direct` **does not exist** | `paths./v1/media/upload-direct.post` — `tags:[Messages]`, 25 MB, `attachmentUrl` | **Verdict unchanged (DO NOT SELECT); evidence replaced** — rejected on **scope**, not non-existence | none | **None** |
| **C4** | `/v1/media/upload` **absent from all sources** | `upload-direct` description: *"Unlike `/v1/media/upload` (which uses upload tokens for **end-user flows**)"* | **Root cause STRENGTHENED** (§6.4) — acknowledged but unpublished, scoped to end-user flows | none | **None** |
| **C5** | *(Rev 1 silent)* TikTok payload *"✅ ALREADY CORRECT"* | `PlatformTarget.platformSpecificData: oneOf[…TikTokPlatformData…]`; `TikTokPlatformData` has **no `tiktokSettings`**; root `tiktokSettings` **does** exist | **NEW FINDING — schema-valid but inert** (§5.4). **Not changed** (operator rule); flagged as a follow-up | **`test_tiktok_settings_all_six_present` → `test_tiktok_payload_shape_unchanged`** (characterisation, not endorsement) | **None** |
| **C6** | *(Rev 1 silent)* 409 unexamined | `409` + `details.existingPostId`, 24 h, checked after `x-request-id` | **NEW FINDING** — 409 → `failed` is wrong (§7.7). Deferred | none | **None** |

### 10.1 Rev 3 corrections (C7-C9) — all three are the operator's findings

| # | Rev 2 claim | Refuting evidence | Implementation consequence | Test consequence | Scope |
|---|---|---|---|---|---|
| **C7** | §8.4: *"The signed `uploadUrl` is **never** logged in full, **never written to the ledger**"* — and the proposal wrapped **only** the response | **`requests` embeds the full signed URL in `str(exc)`** (`…Max retries exceeded with url: /temp/x.mp4?X-Amz-Signature=…`) **and in `exc.request.url`**. **`run.py:360`** does `redact(str(exc), cfg.postiz_api_key, cfg.zernio_api_key)` — **only the two API keys** — then `post.error_reason = "publish failed: " + red` → **the ledger**. Leak path traced end-to-end in **§8.5** | **`_put_signed` + `_scrubbed_transport` added** (§8.4). Signed-PUT transport → **class + stage only**, `from None`, fresh instance (no `request`/`response`). Presign transport → bounded redacted. **Security claim downgraded to an intent until tests 15-20 pass** (§8.5.1) | **+6 tests (15-20)**, incl. **20**, the class-preservation regression guard | **+2 helpers, same file** |
| **C7a** | *(operator's specified remedy)* `raise RuntimeError(…) from None` | `run.py:71-99` classifies `RequestException` **by type** but `RuntimeError` **by message substring**. `"…failed (ConnectionError)"` matches **no** substring → **`_is_transient_publish_error` → `False`** → a retryable blip becomes an **immediately-burned post**; and `"…(Timeout)"` **does** match `"timeout"` → **inconsistent** | **`raise type(exc)(msg) from None`** — same class, scrubbed message. Redaction intent adopted verbatim; only the class amended (§8.4.1) | **test 20** | **none** |
| **C8** | §5.4: *"`SCHEMA-VALID BUT SEMANTICALLY INERT`"*, *"almost certainly **NOT** conveyed"*, *"the server … reads **none** of the settings"*, *"the **wrong level**"* | **S9 — the current official Platform Settings guide** documents the client's **exact** shape verbatim, incl. snake_case fields, and states: **"TikTok settings are nested inside `platformSpecificData.tiktokSettings`"**. Rev 2 **never fetched it**, despite seeing it in a search result | **RETRACTED.** Reclassified **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`** (§5.4.3). **The conflict is preserved, not resolved.** OpenAPI does **not** nullify another current official document. **Still no TikTok change** — now on *firmer* ground | **test 39 reworded** — pins the accepted shape; labels it neither inert nor correct | **none** |
| **C9** | §5: *"11 fixes … 8 already-correct rows, 7 deferred"* | Rows 1-10 + 27 + 28 = **12**, not 11 (it omitted **row 10**, the row **C2** had just added). Already-correct = **11**, not 8. **11+8+7 = 26 ≠ 30** | **All counts recomputed from the rows, not edited in prose** (§5.5). Every derived count in the document recomputed (§5.5 table) | test total recomputed **41 → 47** (§9.11) | **none** |

### 10.2 Net scope — **recomputed across all nine corrections (C9)**

| Quantity | Rev 1 | Rev 2 | Rev 3 *(planned)* | **Rev 4 — ACTUAL PR #694** |
|---|---|---|---|---|
| **Files touched** | 2 | 2 | 2 *(understated — **C14**)* | **9**, enumerated in §14.3: **1** source · **2** tests (1 new, 1 deleted) · **3** derived/rendered (regenerated, never hand-edited) · **3** incident records |
| Functions rewritten | 1 | 1 | 1 | **1** — `zernio_upload_media` |
| **Helpers added** | 2 | 2 | 3 *(miscounted)* | **4** — `_scrub_signed`, `_evidence`, `_scrubbed_transport`, `_put_signed` |
| **Helpers removed** | 0 | 0 | 0 *(unanticipated — **C15**)* | **1** — `_extract_zernio_media_url` |
| Fixes | 11 *(wrong)* | 11 *(wrong)* | **12** | **12** |
| Tests | 40 | 41 | 47 | **47 rows** = **43 new** + **4 pre-existing** (**C13**); **45 functions** in the new file (43 + 2 carried survivors) |
| **Source LOC** | — | — | — | **+203 / −0** in `zernio.py` |
| Postiz changes | 0 | 0 | 0 | **0** |
| `run.py` changes | 0 | 0 | 0 | **0** — C7a is solved inside `zernio.py` by preserving the exception class |
| Ledger mutations | 0 | 0 | 0 | **0** |
| Zernio API calls | 0 | 0 | 0 | **0** |

> **Rev 3's "2 files" was wrong, not merely terse** (C14): it counted the source file and the new test
> file, and missed the deleted test file, the two derived artifacts the line-shift invalidates, the
> rendered doc that carries the source fingerprint, and the records themselves. **The count is now derived
> from `git diff main --stat`, not asserted.**

**Net scope change across all nine corrections: ZERO new files, ZERO new functions rewritten, ZERO Postiz.**
C7 adds two module-private helpers **inside the function already being rewritten**; C2 adds one JSON key;
C1/C5/C6/C8 are dispositions; C9 is arithmetic.

---

## 11. Prompt 08 — Phase D Approval Gate (Reissued — Rev 3)

### 11.1 Reissue preconditions — each verified

| Precondition | Status |
|---|---|
| Zero unsupported documentation claims remain | ✅ Every contract claim in Rev 2 cites the **OpenAPI spec** by path/property (§3). The §1.1 rule is enforced throughout |
| `x-request-id` disposition explicit | ✅ **DEFERRED — option B**, on scope control (§7.9). Retraction recorded (§2.1, C1). Feature acknowledged as **documented and officially recommended** |
| `existingPost` parsing addressed if idempotency included | ✅ **Idempotency is NOT included**, so no parser ships. §7.5 proves header-without-parser is **strictly worse than neither**, and §7.9 ground 1 makes the unspecified shape a stated deferral reason |
| `size` disposition explicit | ✅ **SEND IT**, post-shrink, justified from the schema (§8.3) |
| TikTok payload shape reconciled | ✅ **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`** (§5.4). **Unchanged** per the operator's rule — and the no-change disposition now rests on *firmer* ground than Rev 2's: an official guide (S9) affirmatively documents this shape, and 21 publishes accepted it |
| Revised test matrix internally consistent | ✅ §9.11 — **47 tests, numbered 1-47, no gaps**. Two invariants hold: no test asserts the absence of a documented feature; no test asserts a verdict the evidence can't support |
| **Signed-URL transport leak closed (C7)** | ✅ `_put_signed` + `_scrubbed_transport` (§8.4); leak path traced (§8.5); **security claim downgraded to an intent until tests 15-20 pass** (§8.5.1) |
| **TikTok conclusion corrected (C8)** | ✅ *"inert"* / *"never applied"* / *"ignored"* **all retracted**. Reclassified **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`**; **conflict preserved** (§5.4) |
| **Counts recomputed (C9)** | ✅ **12 / 11 / 7 = 30** from the rows; every derived count recomputed (§5.5) |

### 11.2 Token equivalence — stated explicitly

The §8 proposal contains **no Postiz change of any kind**. It touches exactly:

- `src/fanops/post/zernio.py` — `zernio_upload_media` + `_evidence` + `_scrub_signed`
- `tests/test_zernio_presign.py` — new

No Postiz module, config, container, lifecycle, or route is read or written. Postiz remains **Wave 1B**.

> **Therefore `APPROVE IMPLEMENTATION` and `APPROVE IMPLEMENTATION WITHOUT POSTIZ FIX` authorise the
> byte-identical change set. They are functionally equivalent for this proposal.**

**Recommended canonical token: `APPROVE IMPLEMENTATION WITHOUT POSTIZ FIX`** — self-documenting: it records
the Wave 1A/1B separation in the decision record itself rather than leaving a future reader to infer it.

### 11.3 What approval authorises

| # | Action |
|---|---|
| 1 | Rewrite `zernio_upload_media` → presign + signed PUT, **sending `size`** (§8.2) |
| 2 | Add `_scrub_signed` + `_evidence` (response evidence) **and `_put_signed` + `_scrubbed_transport` (C7 — the signed-PUT transport wrap, class-preserving)** (§8.4) |
| 3 | Delete the legacy path — **no fallback** (§8.5) |
| 4 | Add `tests/test_zernio_presign.py` (§9) |
| 5 | Branch + PR; **CI-only** test execution |

### 11.4 What approval does NOT authorise

- ❌ Any Zernio API call · ❌ Any requeue · ❌ Any change to the 4 failed records
- ❌ Any Postiz change · ❌ Reverting `FANOPS_CORPUS_AUTO=0` · ❌ Deployment or canary
- ❌ **`x-request-id` / `existingPost` / 409** (§7.9 — deferred, **must land before the first requeue**)
- ❌ **TikTok payload change** (§5.4 — official-contract conflict; the shape is production-accepted) · ❌ **Raising the 4 MB cap** (§4.5) · ❌ `platformPostUrl` · ❌ `Retry-After`
- ❌ **Any change to `run.py`** — incl. `_is_transient_publish_error`. C7a is solved **inside `zernio.py`** by preserving the exception class (§8.4.1)

### 11.5 Residual risk

| Risk | Assessment |
|---|---|
| Contract read from the spec, **never exercised live** | **Real.** OpenAPI 3.1.0 `v1.0.4` is authoritative and internally consistent, but first live proof arrives only on a real upload — which needs a requeue, **not requested here** |
| `Allow` may be absent on the 405 | `_evidence` degrades cleanly — the redacted body still lands |
| Legacy path removed with no fallback | **Deliberate** (§8.5) |
| **TikTok setting APPLICATION unverified** | **Open, and unresolvable from documentation** (§5.4). Two current official sources conflict; the shape is **production-accepted** (21 publishes). **This document makes no claim that the settings are or are not applied.** Not a regression; this PR neither changes nor worsens it. A bounded live verification is warranted **only if the setting semantics matter operationally** |
| **Signed-URL leak — closed in design, unproven until tested** | **C7.** `_put_signed` closes both vectors (`str(exc)` and `exc.request.url`), but per §8.5.1 **no guarantee is claimed until tests 15-20 exist and pass**. Rev 2 would have shipped the leak |
| **Two re-POST branches remain unguarded** | **Real but unreachable** — `queued=0` (§7.9 ground 4). **The §7.9 follow-up must land before the first requeue** |
| **Verification still requires a requeue** | **The honest limit.** After this lands, Zernio publishing is **believed** fixed, **not proven** |

### 11.6 Containment — re-verified at the gate

| Check | Value |
|---|---|
| `queued` | **0** ✅ |
| `failed` | **4** — same IDs, untouched ✅ |
| `awaiting_approval` | **343** ✅ |
| `FANOPS_CORPUS_AUTO` | **`0`** ✅ |
| Postiz | **untouched** ✅ |
| 2026-07-19T17:25:18Z rollover | unaffected |

### 11.7 The gate

**No code will be written until one of these exact tokens is returned:**

```
APPROVE IMPLEMENTATION
APPROVE IMPLEMENTATION WITHOUT POSTIZ FIX      <- recommended canonical token
DO NOT IMPLEMENT
```

---

## 12. Evidence Ledger

| ID | Evidence | Method | § |
|---|---|---|---|
| `ZOC-101` | **OpenAPI 3.1.0, `Zernio API v1.0.4`**, 1,748,956 B | fetched `docs.zernio.com/api/openapi`, parsed locally (PyYAML) | §3.1 |
| `ZOC-102` | `servers: https://zernio.com/api`; paths carry `/v1` → client `_base` **correct** | S0 | §3.2 |
| `ZOC-103` | presign: `required:[filename,contentType]`; **`size?: integer` "pre-validation (max 5GB)"**; `contentType` **enum** incl. `video/mp4` | S0 | §3.3, **C2** |
| `ZOC-104` | presign response `{uploadUrl, publicUrl, key, expiresIn(always 3600)}`; PUT, 1 h, 5 GB | S0 | §3.3 |
| `ZOC-105` | Media tag: *"**Zernio auto-compresses** … that exceed platform limits"* | S0 | §3.3, §4.5 |
| `ZOC-106` | **`/v1/media/upload-direct` EXISTS** — `tags:[Messages]`, 25 MB, `attachmentUrl` | S0 | §3.4, **C3** |
| `ZOC-107` | **`/v1/media/upload` acknowledged** — *"upload tokens for **end-user flows**"*; **not a path** | S0 | §6.1, **C4** |
| `ZOC-108` | Complete media path list: **presign, upload-direct, tools/validate/media** | S0 | §6.1 |
| `ZOC-109` | **`x-request-id`** — `in: header`, `required: false`, `format: uuid`, ~5 min, → 200 + `existingPost`; *"HTTP clients should set it themselves or omit it"* | S0 | §3.5, **C1** |
| `ZOC-110` | **`existingPost` occurs 4× in 1.75 MB — never as a schema**; `200` absent from the `responses` map (`201,400,401,403,409,429`) | S0, raw grep | §3.5, §7.5 |
| `ZOC-111` | 409: 24 h `(platform, accountId, content-hash)`, **`details.existingPostId`**, checked **after** x-request-id | S0 | §3.5, §7.7 |
| `ZOC-112` | S8 (flat 409 body) **contradicts** S0 (nested under `details`) — **S0 governs** | S0 vs S8 | §3.5 |
| `ZOC-113` | `PlatformTarget.platformSpecificData: oneOf[…TikTokPlatformData…]`; **no `tiktokSettings` property** | S0 | §3.6, §5.4 |
| `ZOC-114` | `TikTokPlatformData` — 17 camelCase properties; *"Both camelCase and snake_case accepted"*; **`additionalProperties` unset → ALLOWED** | S0 | §3.7, §5.4 |
| `ZOC-115` | Root `tiktokSettings: $ref TikTokPlatformData` — *"merged into each platform's `platformSpecificData`"* | S0 | §3.5, §5.4 |
| `ZOC-116` | All 6 client TikTok fields map to real `TikTokPlatformData` properties. **The nesting is NOT "wrong"** — S9 documents it verbatim, S0 models it differently, `additionalProperties` is unset (JSON-Schema default: allowed) so S0 does not reject S9's form. **CONFLICT, preserved** | S0 + S9 + code | §5.4, **C8** *(supersedes C5's "nesting wrong" — RETRACTED)* |
| `ZOC-117` | **Two re-POST branches**: `ConnectTimeout` `:239`, `429` `:268` | `zernio.py:233-269` | §7.1 |
| `ZOC-118` | **A stable client idempotency token already exists** — `submission_id=f"fanops_{_hash('idemp', pid)}"` (AUDIT H1), **per-surface**, but **not a UUID** and **overwritten at `:258`** | `crosspost.py:243-246`, `models.py:384-393` | §7.2 |
| `ZOC-119` | `_extract_zernio_id` has **no `existingPost` branch** → a replay would park `needs_reconcile` | `zernio.py:52-66,246-259` | §7.5 |
| `ZOC-120` | 409 falls to `:269 break` → **`failed`** (re-queueable) and discards `existingPostId` | `zernio.py:269-274` | §7.7 |
| `ZOC-121` | Four failed assets 1.51-4.04 MiB; 67 parked min/median/p95/max = 1.36/4.12/5.48/5.79 MiB; **0 over 25 MB** | `os.path.getsize` | §4.1-4.3 |
| `ZOC-122` | `FANOPS_ZERNIO_MAX_UPLOAD_MB=4` → **55 % (37/67) re-encoded**; 8 MiB → 0 % | `config.py:1052`, `.env`, size scan | §4.5 |
| `ZOC-123` | Containment at the gate: `queued 0`, `failed 4`, `awaiting 343`, `FANOPS_CORPUS_AUTO=0` | `sqlite3` `mode=ro`, `grep` | §11.6 |
| **`ZOC-124`** | **S9 — official Platform Settings guide documents `platformSpecificData.tiktokSettings.{6 snake_case fields}` VERBATIM**: *"TikTok settings are nested inside `platformSpecificData.tiktokSettings`"* — **the client's exact shape** | `docs.zernio.com/guides/platform-settings`, 2026-07-16T23:2xZ | §5.4, **C8** |
| **`ZOC-125`** | **Three current official sources conflict** on the TikTok arrangement (S0 direct · S8 root-level · **S9 nested wrapper**); `additionalProperties` unset means **the OpenAPI does not reject S9's shape** | S0 vs S8 vs S9 | §5.4.3 |
| **`ZOC-126`** | **Signed-URL transport leak path, end-to-end**: `requests` exception `str()` embeds the signed URL **and** `exc.request.url` holds it → `run.py:360` `redact(str(exc), <postiz key>, <zernio key>)` — **no `X-Amz-*` awareness** → `post.error_reason` → **ledger + Studio + logs** | `run.py:351-372` read | §8.5, **C7** |
| **`ZOC-127`** | **`_is_transient_publish_error` classifies `RequestException` by TYPE but `RuntimeError` by MESSAGE SUBSTRING** — so a `RuntimeError("…(ConnectionError)")` wrap returns **`False`** (terminal), while `"…(Timeout)"` returns **`True`**. **The operator's literal remedy would burn retryable posts, inconsistently** | `run.py:71-101` read + trace | §8.4.1, **C7a** |
| **`ZOC-128`** | Derived counts recomputed from the rows: **12 fixes / 11 correct / 7 deferred = 30**; tests **47** (1-47, no gaps) | row enumeration | §5.5, §9.11, **C9** |

**Attestation.** No Zernio API call. No key transmitted — the OpenAPI spec is public and was fetched
anonymously. No secret read, printed, or written (`FANOPS_ZERNIO_MAX_UPLOAD_MB` is a size limit). No code
modified. Nothing requeued. Four failed records untouched. `FANOPS_CORPUS_AUTO=0` intact. No Postiz change.

---

## 13. Final Classification (Rev 3)

| Dimension | Classification |
|---|---|
| **Zernio root cause** | **`UNSUPPORTED LEGACY CONTRACT`** — retained; grounded in the spec's own **end-user-flow vs programmatic-use** distinction (§6.4) |
| **Obsolete surface** | **Upload only.** Post creation is **contract-current** (§6.2) |
| **Remedy** | **PROVEN by the OpenAPI schema** — `POST /v1/media/presign` + signed `PUT` |
| **`x-request-id`** | **DOCUMENTED** (C1 retraction) · **DEFERRED on scope control** (§7.9) · **must land before the first requeue** |
| **`size`** | **DOCUMENTED** (C2 retraction) · **SENT**, post-shrink (§8.3) |
| **`upload-direct`** | **EXISTS** (C3 retraction) · **NOT SELECTED** — `tags:[Messages]` scope, not size |
| **`/v1/media/upload`** | **ACKNOWLEDGED, NOT PUBLISHED** (C4 retraction) — an **end-user flow** |
| **TikTok payload** | **`OFFICIAL-CONTRACT CONFLICT — ACCEPTED IN PRODUCTION, SETTING APPLICATION UNVERIFIED`** (C8 retraction) · **conflict preserved** · **unchanged** |
| **Signed-URL transport leak** | **CLOSED IN DESIGN, UNPROVEN UNTIL TESTED** (C7) · claim downgraded to an intent (§8.5.1) |
| **Derived counts** | **RECOMPUTED** (C9) — 12 / 11 / 7 = 30 · 47 tests |
| **Live verification** | **STILL PENDING** — requires a requeue, not requested |
| **Postiz** | **Wave 1B — untouched, unbundled** |
| **Implementation** | **GATED** at §11.7 |
| **Overall** | **`YELLOW — CONTAINED; ROOT CAUSE RESOLVED TO THE PUBLISHED CONTRACT; NINE CORRECTIONS APPLIED; IMPLEMENTATION AWAITING APPROVAL`** |

### 13.1 What Rev 3 changed

1. **Closed a live security leak Rev 2 would have shipped (C7).** The signed `uploadUrl` — carrying
   `X-Amz-Signature`/`-Credential`/`-Security-Token` — would have reached the **ledger**, the Studio UI, and
   the daemon log via any `requests` transport exception on the PUT, because `run.py:360` redacts only the
   two API keys. Both vectors (`str(exc)` **and** `exc.request.url`) are now closed, and **no guarantee is
   claimed until tests 15-20 pass**.
2. **Corrected the operator's own remedy (C7a)** — `raise RuntimeError(…)` would have made a
   `ConnectionError` terminal while leaving `Timeout` transient, burning retryable posts inconsistently.
   Class-preserving re-raise keeps behaviour byte-identical to today.
3. **Retracted an overreach (C8).** *"Semantically inert"* and *"almost certainly never applied"* were
   **inferences from schema modelling**, contradicted by a current official guide that documents the
   client's exact shape. **The conflict is now preserved rather than resolved by fiat.**
4. **Recomputed every derived count (C9)** from the rows rather than editing prose.
5. **Named the recurring error.** Rev 1 over-trusted **guides**; Rev 2 over-corrected and over-trusted the
   **spec**. Both asserted conclusions a single source could not support. **The discipline is: read every
   current official source, and when they conflict, record the conflict.**
6. **Scope is unchanged.** Nine corrections, **zero** net scope change: still one function, one source file,
   no Postiz. *(Rev 4 note: the **implementation-scope** claim held; the **file-inventory** claim did not —
   see C14/C15 in §14.1. The PR touches **10** files, enumerated in §14.3.)*

---

## 14. Rev 4 — Implementation Record

Gate token **`APPROVE IMPLEMENTATION WITHOUT POSTIZ FIX`**, returned 2026-07-17. Built as approved.

### 14.1 Six claims this document made that building it disproved (C10-C15)

**Four of these six are the SAME failure this document was rejected for twice: asserting a fact without
opening the file.** Rev 1 asserted API facts from guides it had not read exhaustively. Rev 2 asserted runtime
behaviour from a schema. **Rev 3 asserted facts about FanOps' own source and test suite that a `grep` refutes
in one second.** The operator caught the first two classes; the third survived to the approved gate.

| # | The claim | The evidence that refuted it | Consequence |
|---|---|---|---|
| **C10** | §8.4.1: a `ConnectionError` is *"retried up to `_PUBLISH_TRANSIENT_MAX` (4)"* | `run.py:67` = **3**. **4** is `zernio.py:31`'s **same-named constant** — a different module's different value | **Argument unaffected** (transient-vs-terminal is the point, not the count). Number corrected in place |
| **C11** | §8.4.1 cites `run.py:71-101` | The function spans **`71-99`** | Citation corrected |
| **C12** | §8.4's `_evidence` code caps evidence at **200**, while §8.4's own table says **400** | `errors.redact(text, *secrets, limit=200)` — **the default truncates at 200**, so `[:400]` was dead code. **The spec's code contradicted the spec's table** | **Implementation follows the table**: `redact(..., limit=400)` explicitly. Also **scrub-before-truncate** (`redact(_scrub_signed(text), key, limit=400)`) so a signature straddling the cut cannot survive it — the same rule `redact()`'s own docstring states for keys |
| **C13** | §9 marks tests **41, 43, 47** *"EXISTING"* and implies **42** is new | **Three of the four names do not exist.** Real: 41 ✅ `test_channel_provider.py:192`; 42 **already exists** as `test_publish_5xx_parks_needs_reconcile` (`test_zernio.py:100`); 43 → `test_publish_due_ignores_awaiting_approval` (`test_post_approval.py:116`); 47 → `test_publish_now_rejects_awaiting_approval` (`test_studio_approval.py:49`) | **The invariants ARE covered — the names were invented.** **4 of 47** rows are pre-existing, so **43 are new**. The real file:line of each is recorded in the new test file's header |
| **C14** | §8.1 change list: *"One file"* + *"Tests (§9)"* | **`tests/test_zernio_media.py` pins the DEAD contract** — 11 tests asserting `/media/upload-token`, the `files` multipart field, and body-withholding. They are **regression guards FOR the 405** | **The file is deleted**, not edited: its stated premise (*"Contract DISCOVERED LIVE 2026-06-29 is TWO-step"*) is the thing being removed. Its **2 survivors** (uploader dispatch, oversize preflight) are carried into `test_zernio_presign.py`. §8.1 understated the blast radius |
| **C15** | §8.1 lists the source change as `zernio_upload_media` + helpers | **`_extract_zernio_media_url` becomes dead** — it exists only to parse the legacy upload response, which no longer exists (`publicUrl` comes from presign; the PUT body is never parsed) | **Deleted**, after the alias/lazy-import sweep `src/fanops/CLAUDE.md` requires ("zero callers is a LEAD, not a verdict"): no aliased import, no lazy import, no dict dispatch — only the legacy path being removed |

**C12 and C14 are the two that mattered.** C12 would have shipped a redaction bound half the reviewed size.
C14 would have left the suite pinning the exact contract the fix removes — **the tests would have failed, and
the failure would have looked like the FIX was wrong.**

### 14.2 C7a — confirmed by execution, not by reading

Rev 3 argued from reading `_is_transient_publish_error` that the operator's literal `RuntimeError` remedy
would burn retryable posts. **That argument is now executed:**

```
transient(wrapped ConnectionError) : True     <- the class-preserving re-raise, as built
transient(RuntimeError variant)    : False    <- the operator's literal remedy: TERMINAL
```

The fresh instance also carries `request=None, response=None`, closing the `exc.request.url` vector.
**The claim is no longer an inference.**

### 14.3 What was built

**Exact changed-file inventory — 10 files, derived from `git diff main --stat`, not asserted:**

| # | Artifact | Class | Change |
|---|---|---|---|
| 1 | `src/fanops/post/zernio.py` | **source** | `zernio_upload_media` rewritten to presign + signed PUT (**+203 / −0**); `+_SIGNED_Q`, `+_scrub_signed`, `+_evidence`, `+_scrubbed_transport`, `+_put_signed` (**4 helpers**); `−_extract_zernio_media_url`; legacy path deleted, no fallback. **Rev 4 closure:** module docstring + `build_zernio_payload` comment corrected to the bounded idempotency/`mediaItems` truth (§7.8) — **documentation only, no idempotency implemented** |
| 2 | `tests/test_zernio_presign.py` | **test** | **NEW** — **45 functions** = 43 new + 2 carried survivors |
| 3 | `tests/test_zernio_media.py` | **test** | **DELETED** — pinned the dead contract (**C14**) |
| 4 | `.reports/architecture/derived/MANIFEST.json` | **derived** | **Regenerated** (`python -m tools.arch regen`) — never hand-edited |
| 5 | `.reports/architecture/derived/side_effects.json` | **derived** | **Regenerated** — see the census note below |
| 6 | `docs/ARCHITECTURE_GOVERNANCE.md` | **rendered** | **Re-rendered** (`python -m tools.arch docs`) — carries the source fingerprint |
| 7 | `docs/reconciliation/07_WAVE_0A_CONTAINMENT_RECORD.md` | **record** | Newly tracked. **Rev 4 closure:** malformed `ACT-03` / `ACT-04`-`ACT-05` rows repaired (3 cells against a 4-column header) |
| 8 | `docs/reconciliation/08_DUAL_BACKEND_INCIDENT_FRAME.md` | **record** | Newly tracked — a governing baseline this document cites |
| 9 | `docs/reconciliation/09_ZERNIO_OFFICIAL_CONTRACT_RECONCILIATION.md` | **record** | This file — Rev 4 |
| 10 | `docs/reconciliation/10_ZERNIO_UPLOAD_CANARY_PLAN.md` | **record** | The upload-canary plan — **Rev 2**. **Not executed; authorises nothing.** Rev 1's file count of "9" omitted this file itself; Rev 2 §0 retracts that and three further Rev 1 defects |

**Why the records are tracked:** `zernio.py` now cites *"report 09 §8.5"* and *"§7"* in active comments. A
**tracked source citing an untracked authority** is precisely the dangling-citation failure `CLAUDE.md`
forbids. Reports 01-05 (a different program) and `docs/constitution/` (a superseded draft) stay untracked.

**The regenerated `side_effects.json` is independent evidence of the fix.** `fanops.post.zernio`'s network
census changed from **3×`requests.post` + 1×`requests.get`** to **1×`requests.put` + 2×`requests.post` +
1×`requests.get`** — the dead `POST /media/upload` is gone from a machine-derived artifact, not merely from
prose.

### 14.4 Deviations from the approved §8 spec — three, all disclosed

| Deviation | Why |
|---|---|
| `redact(..., limit=400)` + scrub-before-truncate | **C12** — the spec's code capped at 200, contradicting its own 400-char table |
| `_scrubbed_transport` falls back to `RequestException(msg)` if `type(exc)(msg)` raises | 2 lines. `requests.exceptions.JSONDecodeError` requires `(msg, doc, pos)` and would `TypeError` **inside the error handler**. It cannot be raised by `requests.put`, so this changes behaviour for **no** real transport error; the fallback is non-transient = terminal = the safe direction |
| `tests/test_zernio_media.py` deleted; `_extract_zernio_media_url` deleted | **C14, C15** — necessary consequences of the approved "delete the legacy path, no fallback" |

**No deviation expands the blast radius.** No Postiz change. No `run.py` change — C7a is solved entirely
inside `zernio.py` by preserving the exception class.

### 14.5 Gates run locally (lint + governance only — the suite is CI-only per project rule)

| Gate | Result |
|---|---|
| `ruff check` (zernio.py, test_zernio_presign.py) | **PASS** |
| `tests/test_swallow_ratchet.py` (zernio.py budget) | **3 = 3 baseline — PASS.** 2 silent handlers removed, 2 added |
| `python -m tools.arch ci` | **PASS** (was FAIL on 2 stale derived artifacts + 1 stale rendered doc — regenerated, not hand-edited) |
| `pytest` | **NOT RUN LOCALLY — CI-only.** *(Superseded: CI is now green on the PR head — every required check passes, including `unit` and the real-tooling E2E. The 45 tests are proven to RUN: main's baseline **5321** − 11 deleted + 45 added = **5355**, and CI reports exactly **5355 passed, 1 skipped**. `pytest -q` prints no filenames on success, so this arithmetic — not an eyeball — is what rules out a silent skip.)* |

### 14.6 Containment — and the runtime adoption this build did NOT avoid

**Containment holds:** `queued = 0` · the four `failed` records **untouched** (all four still carry the *old*
405 message — proof nothing re-ran) · `awaiting_approval = 343` · `FANOPS_CORPUS_AUTO=0` · **Postiz
untouched** · **zero Zernio calls** — every test mocks `requests`.

> ## ⚠ UNMERGED PR CODE IS ALREADY ACTIVE IN THE RESIDENT DAEMON VIA EDITABLE INSTALL
>
> **This must not be described as "not deployed."** No deployment *action* was performed — but **runtime
> adoption occurred**, and it is ongoing.

`fanops` is an **editable install**: `fanops.__file__` resolves into this worktree, currently checked out on
`fix/zernio-presign-upload`. The daemon (`.venv/bin/fanops run --loop --interval 600`, `FANOPS_LIVE=1`) is
respawned automatically by launchd (`KeepAlive`, `ThrottleInterval 60`) — **three restarts observed in 50
minutes**. `zernio.py` has not changed since **10:43:13**, so **every start after that moment imports the
PR-head presign code**, and the current resident instance does. This is a standing property of working in the
live tree, not a one-time event.

**Why this is contained anyway — structurally, not by luck:** `publish_due` iterates `queued` only;
`Ledger.approve_post` is the sole promoter into `queued`; it fires only from the Studio Review tab. At
`queued = 0`, `_publish_one` is never entered and `zernio_upload_media` is unreachable.

**What it costs:** the Studio Approve button is now the only thing between un-canaried code and a live Zernio
call. **"Not merged" ≠ "not loaded on the operator's machine."** Report 10 §2 carries the full classification
and the operator hold that follows from it.

### 14.7 The fix IS now proven against the live 405 — §11.5's residual is CLOSED

> ⛔ **SUPERSEDED:** *"The fix is not yet proven against the live 405."* True when written; **false since
> 2026-07-17**.

The upload canary ran under the `APPROVE UPLOAD CANARY` gate (report 10 §10) and returned **`LIVE UPLOAD
CONTRACT VERIFIED`**: presign → 2xx with `uploadUrl` + `publicUrl`; signed PUT → 2xx; `publicUrl` → **206**
with a `Content-Range` total equal to the uploaded asset size, `Content-Type: video/mp4`. **§11.5's residual
— *"contract read from the spec, never exercised live"* — is closed.** The 405 pair is replaced by a pair
the server honours, which is precisely what a **routing** verdict required.

> ⛔ **RETRACTED:** this section previously read *"byte-exactly the asset PUT."* **Overclaim.** The canary
> requested `Range: bytes 0-0`, never iterated the body, and computed no hash — **it read no stored bytes at
> all.** `Content-Range` is a *declaration* by the server, not a measurement. **Proved: a retrievable media
> object of the expected declared length and media type at the server-returned URL. Did NOT prove byte-level
> identity** (report 10 §10.4). The canary is **not** rerun to strengthen this: byte identity answers a
> storage-corruption question the 405 never raised.

**What the canary found that no source stated (report 10 §10.1):**

- **`[OBS]`** — the `uploadUrl` hostname ended in **`r2.cloudflarestorage.com`**; the `publicUrl` hostname
  was **`media.zernio.com`**; **the upload and serving hosts were different**.
- **`[INFER]`** — the upload hostname **strongly indicates Cloudflare R2-compatible storage**. *Zernio's
  storage architecture is **not** claimed: one hostname from one presign response is not an architecture.*
- **`[CONCLUSION]`** — needing only the `[OBS]`: FanOps must treat `uploadUrl` and `publicUrl` as **opaque
  server-returned values**, must **not derive one from the other**, and must **not require both to use the
  same hostname**. S0 types both as opaque strings and never says they differ. The shipped code already
  obeys this — it returns the server's `publicUrl` verbatim and **never parses the PUT target** — and is
  right regardless of who runs the storage.

**Still not established, unchanged:** social posting · production publishing recovered · backlog recovery
ready · **idempotency**. The upload contract is proven; `POST /posts` was never called. **`x-request-id` +
`existingPost` parsing + 409 handling remains MANDATORY before the first production requeue** (§7.8) — a
verified upload does not authorise re-running the four burned posts.
