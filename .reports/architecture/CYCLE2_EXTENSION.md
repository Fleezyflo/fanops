# Cycle 2 — Extension: deep verification, self-corrections, classified findings

**2026-07-14 · git HEAD `fcffa73` · supersedes the first-pass claims it names**

The first pass of Cycle 2 was **wrong in three places**. Two of them were the *same methodological error
Cycle 1 made and Cycle 2 documented as a method note* — asserting a property from a grep or a comment
without proving it. This document is the correction, and it is not append-only where it must overrule:
**where this document contradicts `STATE_MACHINE.md`, `MUTATION_MATRIX.md`, `INVARIANT_AUDIT.md`, or
`COUPLINGS.md`, this document wins.**

Machine-readable twins (authoritative for CI/compilation):
[`transitions.json`](transitions.json) · [`mutation_writers.json`](mutation_writers.json) ·
[`route_contract.json`](route_contract.json) · [`invariants.json`](invariants.json) ·
[`couplings.json`](couplings.json)

---

## 0. The classification correction (accepted verbatim)

The first pass said the malformed-provider path's "safety property survives." **That was too generous
and it is withdrawn.** The accurate statement is:

> **The malformed-provider path does not currently create a false published row, but it violates the
> live-mode fail-closed contract and causes a delayed operational failure.**

A live system that silently substitutes a dry-run implementation has already failed. The absence of a
false `published` row is a *smaller* claim than "the property holds," and conflating them is exactly the
doc-drift pattern this audit exists to catch. Reclassified: **currently reachable defect.**

---

## 1. Self-corrections (3)

### SC-1 — "`PostState.retired` has zero writers" · **FALSE**

The first-pass census grepped for **literal** enum references (`\.state = PostState\.`). An AST census
over all 127 modules — covering dynamic assignment, `setattr` with a variable field name, `model_copy`
with a variable dict, constructor `state=` with a variable, and **enum-valued keyword defaults** — found
**five generic writers the grep could not see**:

| Site | Expression | Bounded by |
|---|---|---|
| [cli.py:395](src/fanops/cli.py:395) | `p.state = PostState(args.status)` | **argparse `choices=["published","failed","analyzed","retired"]`** ([cli.py:702](src/fanops/cli.py:702)) |
| [actions.py:870](src/fanops/studio/actions.py:870) | `p.state = st` | `st` restricted to published\|failed ([actions.py:858](src/fanops/studio/actions.py:858)) |
| [run.py:357](src/fanops/post/run.py:357) | `setattr(p, f, v)` | `_NET_POST_FIELDS`, which **contains `"state"`** |
| [reconcile.py:725](src/fanops/reconcile.py:725) | `model_copy(update=upd)` | `upd` built at [:712](src/fanops/reconcile.py:712) with `state=published` |
| [pipeline.py:151](src/fanops/pipeline.py:151) | `model_copy(update={'state': error_state})` | `_quarantine` — **state is a parameter** |

**Therefore `fanops resolve <post_id> retired` is a first-class CLI verb, and `cli.py:395` is the SOLE
writer of `PostState.retired`.** `PostState.analyzed` likewise has **two** writers (`track.py:193` **and**
`cli.py:395`), not one.

**What survives the re-census:**

| Member | Verdict | Proof |
|---|---|---|
| `PostState.error` | **still reserved** | not in the argparse choices; the Studio twin `resolve_post` explicitly refuses anything but published/failed ([actions.py:858-859](src/fanops/studio/actions.py:858)) |
| `ClipState.published` / `.analyzed` | **still reserved** | `born_state` is a **keyword default** ([clip.py:707](src/fanops/clip.py:707) = `rendered`), overridden **only** to `stitch_draft` ([stitch_render.py:251,333](src/fanops/stitch_render.py:251)). Every `set_clip_state` call site passes a literal. |
| `BatchState.closed` / `.error` | **still reserved** | no writer *and* no reader |
| `RenderState.{queued,published,analyzed,retired}` | **still reserved** | AST-confirmed: no ctor `state=`, no `model_copy` state key, no assignment. **Cycle-1 `FIND-001` stands.** |
| `_LIVE_CLIP_STATES` is a dead guard ([ledger.py:665](src/fanops/ledger.py:665)) | **stands** | still no writer of `ClipState.published/analyzed` |

There is exactly **one** enum-valued keyword default in the whole tree (`born_state`), so Cycle-1's
`FIND-007` trap class is now **fully enumerated**.

### SC-2 — `COUP-02` ("`os.environ` has no cross-process propagation") · **RETRACTED**

[cli.py:1303-1304](src/fanops/cli.py:1303):

```python
while True:
    load_dotenv(cfg.root / ".env", override=True)   # operator disk truth each tick (B01 C1)
    cfg = Config(cfg.root)                          # side-effect-free; re-read after dotenv
```

The daemon loop **re-reads `.env` with `override=True` and rebuilds `Config` every tick.** A Studio
`go_live` **does** reach the resident daemon within one tick. My claim was false.

**The real coupling runs the opposite direction** (`COUP-02b`): a running **Studio** calls `load_dotenv`
exactly **once**, at process entry ([cli.py:795](src/fanops/cli.py:795)), then blocks in `app.run`
([cli.py:1285](src/fanops/cli.py:1285)). So a `.env` change made by the **CLI or the daemon never reaches
a running Studio**. [golive.py:11](src/fanops/studio/golive.py:11) says so outright — it is precisely why
`_dual_write` also pokes `os.environ`, so the *writing* Studio reflects its own change immediately.

### SC-3 — the `model_copy` bypass is **systemic**, not specific to the published-URL rule

Executed against pydantic 2.13.4. **`model_copy` bypasses every validator on every model — including
`Moment`'s, despite `validate_assignment=True`.** `validate_assignment` protects `setattr` **only**; it
has no effect on `model_copy`.

| Invariant | ctor | `setattr` | `model_copy` |
|---|---|---|---|
| `Post`: published requires `public_url` | raise | **BYPASS** | **BYPASS** |
| `Post.account` canonicalization | raise | **BYPASS** | **BYPASS** |
| `Moment.affinities` canonicalization | raise | enforced | **BYPASS** |
| `Moment.segments` validity + start/end envelope | raise | enforced | **BYPASS** |

**Not reachable today**, and the reason is good engineering, not luck: `set_segments`
([actions_segments.py:19](src/fanops/studio/actions_segments.py:19)) deliberately round-trips through
`Moment.model_validate` — which *does* run validators — and uses `model_copy` only for `content_token`
(no validator). `clear_segments` sets `segments=[]`, where the envelope validator is a no-op anyway.

**Latent, and proven:** `model_copy(update={"segments": [(2.0, 4.0)]})` leaves `start=0.0, end=5.0` — a
**stale envelope**. The Moment's declared window and its segments would disagree, and the clip would
render the wrong span.

**And it is load-bearing right now** (`COUP-07`): `cast_add`/`cast_remove`
([actions_casting.py:26,44](src/fanops/studio/actions_casting.py:26)) mutate `affinities` by direct
`setattr`, and are correct **only because** `Moment.validate_assignment=True`. Converting them to
`model_copy` "for consistency with the other 57 sites" would silently stop canonicalizing affinities —
and `affinity_admits` (the crosspost gate) matches canonical handles, so a non-canonical `"@Foo"` would
make that moment post **nowhere**, silently.

---

## 2. The malformed-backend path, traced end-to-end and **executed**

Every row below was produced by running the real code against a real `Config`/`Accounts`, `is_live=True`.

### 2.1 The write boundary is **sound** — malformed values are hand-edit only

`set_backend` ([accounts.py:412](src/fanops/accounts.py:412)) does `.strip().lower()` **before**
validating:

| `set_backend(…)` input | Stored in `accounts.json` |
|---|---|
| `"postiz"`, `"Postiz"`, `"POSTIZ"`, `"postiz "`, `" postiz"` | **`"postiz"`** (normalized) |
| `"blotato"` | **`ValueError`** — rejected |
| `""` | override cleared |

**So no Studio or CLI path can produce a malformed backend.** It requires a hand-edit of `accounts.json`
— which the code documents as the operator's normal channel
([accounts.py:112-113](src/fanops/accounts.py:112)).

### 2.2 The hand-edit path is fully unguarded — executed

| `backends[instagram]` | `Accounts.validate()` | `effective_provider()` | `get_poster()` on a **LIVE** system |
|---|---|---|---|
| `"postiz"` | clean | `postiz` | `PostizPoster` ✅ |
| `"Postiz"` | **clean — NO FLAG** | `Postiz` | **`DryRunPoster`** ⚠ |
| `"POSTIZ"` | **clean — NO FLAG** | `POSTIZ` | **`DryRunPoster`** ⚠ |
| `"postiz "` *(trailing space)* | **clean — NO FLAG** | `postiz ` | **`DryRunPoster`** ⚠ |
| `" postiz"` *(leading space)* | **clean — NO FLAG** | ` postiz` | **`DryRunPoster`** ⚠ |
| `"blotato"` | **clean — NO FLAG** | `blotato` | **`DryRunPoster`** ⚠ |
| `"dryrun"` | clean | `dryrun` | `RuntimeError` — **live-guard fires** |
| `"DryRun"` | clean | `DryRun` | `RuntimeError` — **live-guard fires** |

**The guard fires only on the two values that are least dangerous** (an explicit `dryrun` — an operator
saying "don't publish"). **Every value that *looks like* a real backend sails through.** The guard is
case-**insensitive**; the `PROVIDERS` lookup ([providers.py:56](src/fanops/post/providers.py:56)) is
case-**sensitive**. They disagree.

`get_media_uploader` follows the same fallback → media resolves to a **`file://` URL** on a live system.

**Whitespace is the worst variant:** `"postiz "` is visually identical to `"postiz"` in any UI, JSON
dump, or diff.

### 2.3 Terminal state — and it depends on deployment shape

`live_ready_channels()` returns **`[]`** for every malformed value, which feeds `cfg.is_live_backend`
([config.py:459](src/fanops/config.py:459)), which gates the reconcile pass
([pipeline.py:318](src/fanops/pipeline.py:318)).

| Deployment | Terminal state |
|---|---|
| **≥1 valid channel remains** | `is_live_backend=True` → reconcile runs → `submitting` → **72 h** → `needs_reconcile` ([reconcile.py:748](src/fanops/reconcile.py:748)) → **72 h** → `GAVE UP:` ([reconcile.py:759](src/fanops/reconcile.py:759)). **No half-live warning fires** — a valid route exists, so the operator sees a healthy system while one channel silently publishes nothing. **Delayed operational failure.** |
| **All channels malformed** | `is_live_backend=False` → **the reconcile pass never runs** → the post is stranded in `submitting` **permanently**: no escalation, no `error_reason`, no terminal state. The half-live banner *does* fire at system level ([doctor.py:322](src/fanops/doctor.py:322), [views.py:723](src/fanops/studio/views.py:723)), but **nothing marks the post.** Reachable only by hand-editing *after* going live (`go_live` gates on ≥1 live-ready channel). |

---

## 3. `restore_snapshot` vs a live SQLite writer — **executed**

You asked me not to stop at "the locks are mutually invisible." I ran it.

**Setup:** seed the ledger, snapshot it, advance the live ledger past the snapshot. Then start a real
writer holding `BEGIN IMMEDIATE` across a slow mutation, and fire `restore_snapshot` inside its open
transaction.

**Observed interleaving:**

```
W: BEGIN IMMEDIATE held
R: restore_snapshot START  (fcntl.flock on ledger.lock — NOT the sqlite lock)
R: restore_snapshot DONE   (os.replace swapped the db file)
W: about to commit 'signalled'
W: COMMITTED ok                      <-- no exception
FINAL on-disk state: 'catalogued'    <-- the snapshot's, NOT the writer's
```

**Result: the writer's `commit()` SUCCEEDED and its data was SILENTLY DISCARDED.** `os.replace`
([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)) unlinked the old dir entry; the writer's open
connection kept the old **inode** alive and committed into it. Nothing raised. Nothing warned.
`restore` also unlinks the `-wal`/`-shm` sidecars ([ledger_sqlite.py:148-150](src/fanops/ledger_sqlite.py:148))
while another connection may hold them open.

**So it can overwrite, it can race, and it does restore while another transaction proceeds — and the
losing writer is told it succeeded.**

**Exposure:** `restore_snapshot` has **no production caller** (operator break-glass), but
[ledger_wipe.py:246](src/fanops/ledger_wipe.py:246) advertises it as *the* wipe rollback path — so an
operator following the documented recovery procedure while the daemon is running hits this.

---

## 4. Route contract — all 149 verified

Full per-route contract: [`route_contract.json`](route_contract.json).

| Dimension | Result |
|---|---|
| Routes | **149** (108 mutating, 41 read-only) |
| **Authorization** | **0 / 149.** No auth, no session, no token, no `before_request` hook. Recorded decision — [studio/CLAUDE.md](src/fanops/studio/CLAUDE.md): *"no auth by design — don't add CSRF/rate-limit tickets; declined as out-of-scope for localhost."* Boundary = the network interface (`app.run(host=…)`, [cli.py:1285](src/fanops/cli.py:1285); default `127.0.0.1:8787`). |
| **CSRF** | **All 108 mutating routes exposed.** Plain htmx POSTs, no token. Any page the operator visits while the Studio runs can forge a POST to `/golive/live`, `/schedule/publish-due`, or `/live-library/wipe/confirm`. |
| Ledger mutation | 44 / 108 |
| Filesystem mutation | 13 / 108 |
| Network / external effect | 11 / 108 |
| Subprocess (ffmpeg / `claude -p` / launchctl / docker) | 9 / 108 |
| Env / `.env` write | 12 / 108 |
| Audit-logged | 16 / 108 |
| Confirm gate | 18 / 108 |
| Input validation present | 97 / 108 |
| **Idempotency** | Not enforced at the route layer. **Inherited from the action**: content-addressed ids + `setdefault` + in-lock source-state guards make most POSTs naturally idempotent. |

**Known server-side gap (MOL-71, self-declared in [studio/CLAUDE.md](src/fanops/studio/CLAUDE.md)):**
`do_wipe_confirm` has **no server-side check that `do_wipe_preview` ran first** — "preview before
confirm" is a **UI convention only**. The typed-word (`REMOVE`) and mandatory-snapshot gates are
unaffected.

---

## 5. Findings, classified (the 6 categories)

### 5.1 Currently reachable defect (3)

| ID | Finding | Reachability |
|---|---|---|
| `F-A` | **Malformed provider → `DryRunPoster` on a live system.** Violates the live-mode fail-closed contract. Does **not** create a false `published` row; **does** strand the post — 72 h to a `GAVE UP:` label with a valid sibling channel, or **permanently, unlabeled** if all channels are malformed. | Hand-edit of `accounts.json` (the documented operator channel). `set_backend` cannot produce it. |
| `F-B` | **`restore_snapshot` silently discards a concurrent committed transaction.** Executed and proven; the losing writer's `commit()` returns success. | Operator runs the documented wipe-rollback while the daemon is running. |
| `F-C` | **CSRF on 108 mutating routes**, including go-live, publish-due, and wipe. | Any page visited while the Studio runs. Accepted risk per `studio/CLAUDE.md`. |

### 5.2 Dormant defect behind a disabled flag (1)

| ID | Finding | Flag |
|---|---|---|
| `F-D` | **`intro_match` gates are written but structurally unanswerable** — absent from `responder._SCHEMA`. With the flag on, unanswerable `.request.json` files accumulate forever and are invisible to the by-source status view (`gate_source_id` → `None`). | `FANOPS_INTRO_TEASE` — **DEFAULT OFF** ([config.py:731](src/fanops/config.py:731)) |

### 5.3 Reserved / unwired surface (4)

`RenderState.{queued,published,analyzed,retired}` · `ClipState.{published,analyzed}` (⇒
`_LIVE_CLIP_STATES` is a **dead guard**, [ledger.py:665](src/fanops/ledger.py:665)) · `PostState.error` ·
`BatchState.{closed,error}`. Plus **`Ledger.set_post_state` has zero callers**
([ledger.py:572](src/fanops/ledger.py:572)).

### 5.4 Documentation error (6)

`INV-01` (R1 "type level" — the validator fires at **load**, not at write; two comments assert a pydantic
behaviour that does not exist) · `INV-02` (single-writer of `queued` — 7 writers) · `INV-03` (`get_poster`
raises when live — it does not) · `INV-05` (`Settings` constructed per `Config()` — it never is;
`runtime_load` has zero callers) · `INV-20` (**10 of 10** CLAUDE.md line refs are stale) · plus **this
cycle's own two retractions** (SC-1, SC-2).

### 5.5 Security exposure (2)

`F-C` (CSRF, above) · **`COUP-15`**: the Studio's path-traversal guard depends on `Config.render_path`
always building under `cfg.base` — and **only a comment** ties the builder to the `_bounded` serve check.

### 5.6 Operational hazard (7)

`model_copy` bypasses every validator on every model (`SC-3`) · `Post.error_reason` is a structured
control channel parsed by 3 readers (`COUP-03`) · `_VALID_BACKENDS` defined twice (`COUP-05`) · the
gate-kind registry is triplicated and unlinked (`COUP-06`) · `cast_add` is safe only via
`validate_assignment` (`COUP-07`) · `_publish_throttle_last` is per-process, so N publisher processes
publish N× the rate limit · a running Studio never re-reads `.env` (`COUP-02b`).

---

## 6. Modules inspected this extension (previously uninspected)

`daemon.py` — **a launchd installer/manager, not a pipeline driver.** It shells `launchctl`, renders
plists, and reports status. **Zero ledger writes.** The actual tick loop is
[cli.py:1302-1313](src/fanops/cli.py:1302).

`post/metrics.py` — **a pure HTTP transport layer.** `PostizMetricsClient` / `ZernioMetricsClient` /
oEmbed verification. **Zero `PostState` writes** — `track.py:193` owns the `analyzed` transition.

`post/media.py` — upload cache. Surfaced `COUP-17`: `_media_cache_hit` identifies a Postiz URL by it
**containing a literal `"|"`** ([media.py:58](src/fanops/post/media.py:58)), and rejects a Zernio URL that
contains one ([media.py:49](src/fanops/post/media.py:49)).

`post/compress.py` — fail-open ffmpeg shrink. Surfaced `COUP-16`: `publish_backend_for_post`
([compress.py:69-76](src/fanops/post/compress.py:69)) is a **second provider resolver that never checks
`cfg.is_live`**. It is **not** a publish bypass — its three consumers only decide whether to *shrink* a
file — but it is a divergent second answer to "which backend publishes this post."

`golive.py` — the `go_live` gate order is confirmed: ledger-load (torn → refuse, logged) → **past-due
queued backlog → refuse** (anti machine-gun) → explicit `confirmed` → `_dual_write("FANOPS_LIVE","1")` →
scrape a stale `FANOPS_POSTER=dryrun`. `_dual_write` ([golive.py:47-66](src/fanops/studio/golive.py:47))
routes the three secrets to the **OS keyring only**, never plaintext `.env`, and leaves `os.environ`
untouched if the durable write fails.

`actions_segments.py` / `actions_casting.py` — the two `Moment`-mutating surfaces; both correct, and both
load-bearing to `SC-3` / `COUP-07`.

**Still uninspected:** `meta_graph.py`, `variant_*.py`, `adjust.py`, `digest.py`, `hookscore.py`,
`persona_*.py`, most `views_*.py`. No claim in this cycle rests on them.
