# FanOps — Side-Effect Graph and Lock Domains

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`side_effects.json`](side_effects.json)

Every external / network / subprocess / filesystem / ledger effect, its ordering relative to the durable
writes, and which lock domain it sits in.

---

## 1. The four lock domains

| Domain | Primitive | Guards | Held across I/O? |
|---|---|---|---|
| **L1 — ledger** | SQLite `BEGIN IMMEDIATE` (30 s typed `LockBusyError`) | all 8 entity maps | **NO** — by design |
| **L2 — control files** | `fcntl.flock` per `<file>.lock` | `accounts.json`, `personas.json`, `hashtag_*.json` | no |
| **L3 — run lease** | `fcntl.flock LOCK_NB` on `00_control/.run.lock` | the respond→advance converge loop | **YES** — the whole pass |
| **L4 — stage lock** | `fcntl.flock` on `<agent_io>/.locks/<stage>/<key>.lock` | one producer per (stage, source) | **YES** — across whisper/ffmpeg |

**L1 and L2 do not exclude each other** (`COUP-01`). **`restore_snapshot` takes an flock on a *fifth* file
(`ledger.lock`) that excludes nothing at all** (`F-B`) — a live `BEGIN IMMEDIATE` writer's `commit()` succeeds
and its data is silently discarded.

**The cardinal rule the codebase actually honours:** *no network or heavy subprocess ever runs inside L1.*
Verified at every site.

---

## 2. Network call sites (15 — `requests` is the sole HTTP library)

| Site | Call | Lock held | Ordering vs durable write |
|---|---|---|---|
| **[postiz.py:392](src/fanops/post/postiz.py:392)** | `POST /public/v1/posts` — **THE PUBLISH** | **none** | **AFTER** the `submitting` claim commits |
| [postiz.py:152](src/fanops/post/postiz.py:152) | `PUT` → Cloudflare R2 (SigV4, no boto3) | none | before the publish |
| [postiz.py:182](src/fanops/post/postiz.py:182) | `POST /upload-from-url` | none | before the publish |
| [postiz.py:242](src/fanops/post/postiz.py:242) | `POST /upload` (multipart) | none | before the publish |
| [postiz.py:266](src/fanops/post/postiz.py:266) | `GET /integrations` | none | Go-Live / health probe |
| **[zernio.py:235](src/fanops/post/zernio.py:235)** | `POST /posts` — **THE PUBLISH** | **none** | **AFTER** the claim commits |
| [zernio.py:141](src/fanops/post/zernio.py:141) | `POST /media/upload-token` | none | before the publish |
| [zernio.py:155](src/fanops/post/zernio.py:155) | `POST /media/upload` | none | before the publish |
| [zernio.py:183](src/fanops/post/zernio.py:183) | `GET /accounts` | none | Go-Live |
| [metrics.py:160](src/fanops/post/metrics.py:160) | `GET /public/v1/posts` (**Postiz status**, ±35 d window) | **none** — pre-polled | **BEFORE** the apply txn |
| [metrics.py:286](src/fanops/post/metrics.py:286) | `GET /posts/{id}` (**Zernio status**) | none | before the apply txn |
| [metrics.py:89](src/fanops/post/metrics.py:89) | `GET` analytics | none | before the apply txn |
| [metrics.py:513](src/fanops/post/metrics.py:513) | `GET` Zernio analytics | none | " |
| [meta_graph.py:156,410](src/fanops/meta_graph.py:156) | Meta Graph (injectable `get`) | none | " |
| [health_model.py:160](src/fanops/health_model.py:160) | `GET` docker/postiz health | none | read-only |

**Every network call is outside L1.** The two publish POSTs are the only ones that mutate the outside world.

---

## 3. Subprocess call sites (32)

| Module | Count | What | Lock |
|---|---|---|---|
| `clip.py` | 13 | ffmpeg render / crop / concat / subtitle burn | **L4** (stage lock), never L1 |
| `daemon.py` | 12 | `launchctl` | none |
| `ingest.py` | 10 | `ffprobe` | none (lock-free stage) |
| `overlay.py` | 5 | ffmpeg drawtext | L4 |
| `llm.py` | 5 | **`claude -p`** — the LLM is a *subprocess*, not an HTTP API | none |
| `transcribe.py` | 4 | whisper | L4 |
| `keyframes.py` | 4 | ffmpeg frame grid | L4 |
| `discover.py` | 4 | ffprobe | none |
| `signals.py` | 3 | ffmpeg | L4 |
| `health.py` / `health_model.py` | 4 | `docker` | none |
| `actions_run.py` | 3 | ffprobe/ffmpeg | none |
| **`compress.py`** | **1** | **ffmpeg shrink** ([compress.py:28](src/fanops/post/compress.py:28)) | **none — runs in the lock-free NETWORK phase of `_publish_one`** |
| `postiz_lifecycle.py` | 1 | **`docker` — shelled from `publish_due`** ([run.py:447-449](src/fanops/post/run.py:447)) | none |

**The LLM is `claude -p` riding the operator's existing login.** `ANTHROPIC_API_KEY` is *not* required, and
`claude --bare -p` provably **fails** because it never reads the keychain ([llm.py:12-23](src/fanops/llm.py:12)).

---

## 4. The publish side-effect ordering (the one that matters)

```
 T0  Ledger.load(cfg)                          [L1: none]      read
 T1  ┌ BEGIN IMMEDIATE ────────────────────────[L1: HELD]
     │  guard: state is queued  ∧  due
     │  post.state = submitting
 T2  └ COMMIT ─────────────────────────────────[L1: released]   ◄── DURABILITY POINT (F11)
 ─────────────────────────────────────────────────────────────────────────────────────
 T3  Ledger.load(cfg)  (throwaway)             [no lock]        read
 T4  apply_shrink_to_post → ffmpeg             [no lock]        ⚠ SUBPROCESS + mkdtemp LEAK
 T5  ensure_clip_media → R2 PUT / Postiz upload[no lock]        ⚠ NETWORK (idempotent for R2: content-addressed key)
 T6  _publish_throttle_wait                    [no lock]        sleep (in-process dict — per-PROCESS only)
 T7  poster.publish → POST /posts              [no lock]        ⚠⚠ THE EXTERNAL EFFECT. NOT IDEMPOTENT. NO KEY.
 ─────────────────────────────────────────────────────────────────────────────────────
 T8  ┌ BEGIN IMMEDIATE ────────────────────────[L1: HELD]
     │  merge ONLY _NET_POST_FIELDS into a FRESHLY LOADED ledger   (B4 lost-update fix)
     │  clip.media_url / render.media_url  (first-writer-wins)
     │  renders[rid].path  ← the mkdtemp path      ⚠ C3-F5
 T9  └ COMMIT ─────────────────────────────────[L1: released]   ◄── DURABILITY POINT
 T10 _archive_published(cfg, post)             [no lock]        fs write, fail-open, OUTSIDE the txn
```

**The crash-critical window is T2→T8.** A crash anywhere in it leaves `submitting` — which `publish_due` never
re-drives. That is *correct*. The defect is that reconcile, the only reader of `submitting`, cannot always
terminate it (`C3-F1`/`C3-F2`).

**T5 idempotency is asymmetric:**
- **R2** — the key is `fanops/{sha256(file)[:32]}.mp4` ([postiz.py:171](src/fanops/post/postiz.py:171)) →
  **content-addressed → a re-upload overwrites the same object.** ✅ truly idempotent.
- **Postiz multipart `/upload`** ([postiz.py:242](src/fanops/post/postiz.py:242)) → **not** content-addressed
  → a retry creates a **second media object**. Orphaned-media leak, not a double-publish.

**T6 is per-process.** `_publish_throttle_last` is a module-level dict. **N concurrent publisher processes
publish N× the Postiz rate limit.** Self-declared in `post/CLAUDE.md`; single-process by design today.

---

## 5. Filesystem effects, by durability class

| Effect | Atomic? | Cleaned up? |
|---|---|---|
| control JSON (`controlio`) | ✅ mkstemp + `os.replace` | n/a |
| gate `.request.json` / `.response.json` | ✅ tmp + `os.replace` | ✅ `discard_gate` |
| **gate `.attempts.json`** | ❌ **bare `write_text`** | ✅ `clear_attempts` |
| clip / render `.mp4` | ✅ `.part.mp4` + `os.replace` | ✅ cascade unlink, **deferred until after commit** |
| Studio upload | ✅ `.uploadpart` + `os.replace` | ⚠ a partial upload leaves a `.uploadpart` — best-effort `tmp.unlink()` at [actions_run.py:391](src/fanops/studio/actions_run.py:391) |
| `06_published/<day>/<pid>.json` | ✅ `O_CREAT\|O_TRUNC, 0o600` | never (intended) |
| `studio_audit.log` | append | never (intended) |
| `ledger.sqlite` | ✅ WAL + `chmod 0o600` after every write | n/a |
| **`04_agent_io/fanops-shrink-*/`** | n/a | 🔴 **NEVER — `C3-F4`.** The only `mkdtemp` in `src/fanops`, with **no `rmtree` anywhere in the tree.** And the ledger's `Render.path` is rewritten **into** it (`C3-F5`). |
| `<agent_io>/keyframes/`, `/framing/`, `/signals/` | content-addressed caches | ⚠ `purge_source_artifacts` only, on an explicit T0 reset |

**Cache invalidation rules (brief §9 claim #9):**

| Cache | Key | Invalidation |
|---|---|---|
| render mp4 | `_render_fingerprint` (focus/track + `ct`/`geom` only when a zoom applies) | ✅ fingerprint change |
| `<src>.detect.json` | source id | ✅ `purge_source_artifacts` |
| keyframe grid | content-addressed `<hash>/` | ✅ content address |
| **`Clip.media_url` / `Render.media_url`** (the F44 upload cache) | first-writer-wins (`not c.media_url`) | 🔴 **NONE.** *"if the hosted URL expires, nothing invalidates the cache."* A dead CDN URL is reused forever. |
| media-cache validity across backends | `_media_cache_hit` identifies a Postiz URL **by it containing a literal `"|"`** ([media.py:58](src/fanops/post/media.py:58)) | ⚠ `COUP-17` — an undocumented URL-shape convention |

**Claim #9 ("every filesystem cache has an invalidation rule") is FALSIFIED** for the upload cache.

---

## 6. Environment effects

| Writer | Site | Scope |
|---|---|---|
| `golive._dual_write` | [golive.py:47-66](src/fanops/studio/golive.py:47) | `.env` **and** `os.environ`. Secrets go to the **OS keyring only**, never plaintext `.env`. |
| `autopilot` | `autopilot.py:80` | `FANOPS_RESPONDER` |

**Exactly 2 `os.environ[...]` writes in `src/`.** Only `golive` writes `FANOPS_LIVE` (`INV-18`, upheld).

**Propagation:** the **daemon** re-reads `.env` (`override=True`) and rebuilds `Config` **every tick**
([cli.py:1303-1304](src/fanops/cli.py:1303)). A **running Studio** `load_dotenv`s **once** at entry and never
again (`COUP-02b`). `Config` properties are `os.getenv` **per access** (74 sites, uncached), so a `go_live`
write is visible to the *writing* process immediately.
