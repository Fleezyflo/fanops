# FanOps — Invariant Audit

**Cycle 2 · 2026-07-14 · git HEAD `fcffa73`**

> ## ⚠ SUPERSEDED IN PART — read [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md) first
>
> The extension pass corrected this document in four places. Where they conflict, **the extension wins**.
> Machine-readable twin: [`invariants.json`](invariants.json).
>
> - **`INV-03` reclassified.** This document said the dryrun-safety property "survives." **Withdrawn — too
>   generous.** The accurate statement: *the malformed-provider path does not currently create a false
>   published row, but it violates the live-mode fail-closed contract and causes a delayed operational
>   failure.* Now a **currently reachable defect**, with an executed variant matrix (case, whitespace,
>   unknown) and a terminal state that depends on deployment shape — including a **permanent, unlabeled
>   strand** when every channel is malformed (reconcile is gated on `is_live_backend`, `pipeline.py:318`).
> - **`INV-07` upgraded from "locks are mutually invisible" to a PROVEN silent-data-loss race.** Executed:
>   a live `BEGIN IMMEDIATE` writer's `commit()` **succeeded** and its data was **silently discarded**.
> - **`INV-01` generalized (`INV-01b`).** The bypass is **systemic**: `model_copy` skips **every validator
>   on every model** — including `Moment`'s, despite `validate_assignment=True`.
> - **New `INV-22` / `INV-23`: two self-retractions.** `PostState.retired` **does** have a writer
>   (`cli.py:395`, via `fanops resolve <id> retired`), and the daemon **does** re-read `.env` every tick
>   (`cli.py:1303`), so the `COUP-02` claim was false.

Every declarative safety claim in `CLAUDE.md` (root + `src/fanops/` + `post/`), `docs/`, and in-code
comments beginning **Invariant / Always / Never / Only / Must / Cannot / Every / Exactly / Single /
Unique / Atomic / Immutable** — classified against the code.

**Classification:**
- **Verified** — the claim is true as written.
- **Refined** — the *safety property* holds, but **not by the mechanism the doc claims**. A future
  edit that trusts the stated mechanism will break it.
- **False** — the claim is untrue and the property does not hold as stated.
- **Unknown** — could not be resolved this cycle.

Cycle 1 found one false invariant (`FIND-002`). **Cycle 2 found four more Refined and two more False.**
The recurring failure mode is identical every time: *the doc names a mechanism that does not exist,
while the property survives via a different one.*

| ID | Invariant | Verdict |
|---|---|---|
| INV-01 | R1 published-URL invariant "bound at the type level" | **Refined** (+ a **False** sub-claim) |
| INV-02 | "Single writer of `queued`" (Cycle-1 `FIND-002`) | **False** — re-confirmed, refined |
| INV-03 | "`get_poster` RAISES rather than build a `DryRunPoster` when live" | **False** |
| INV-04 | `intro_match` gate is live | **False** (structurally unanswerable) |
| INV-05 | `Settings` is "constructed per `Config()`" | **False** |
| INV-06 | Cascade protection gates on live **clip** states | **Refined** (clip check is a dead guard) |
| INV-07 | "`00_control/ledger.lock` is vestigial" (Cycle-1 `DEAD-005`) | **False** — it is live, and excludes nothing |
| INV-08 | No-auto-publish: an unapproved post is structurally unpublishable | **Verified** |
| INV-09 | `_publish_one` is the SOLE network-POST caller | **Verified** |
| INV-10 | `needs_reconcile` is never downgraded to `failed` | **Verified** |
| INV-11 | `AuthError` halts the run, never burns the queue | **Verified** |
| INV-12 | Ledger writes are atomic (`BEGIN IMMEDIATE` + full replace) | **Verified** |
| INV-13 | A newer on-disk schema is refused, never downgraded | **Verified** |
| INV-14 | Bias actuators are amplify-only + validation-frozen | **Verified** |
| INV-15 | `_JITTER_MAX < _STEP_MIN` ⇒ schedule monotonic in index | **Verified** (assert-enforced) |
| INV-16 | `ids.py`: builtin `hash()` is banned (PEP 456 salt) | **Verified** |
| INV-17 | `[framing]` is the only fail-CLOSED dependency | **Verified** |
| INV-18 | `go_live` is the ONLY setter of `FANOPS_LIVE=1` | **Verified** |
| INV-19 | Every ledger model is forward-compatible (`extra="ignore"`) | **Verified** |
| INV-20 | Doc line-number references | **False** (systematically stale) |
| INV-21 | Studio route authorization | **Verified-absent** (none exists, by decision) |

---

## INV-01 — R1 published-URL invariant · **Refined**, with a **False** sub-claim

**Claim** ([models.py:356-375](src/fanops/models.py:356)):
> "bind that meaning at the type level so **no door** (DryRunPoster.publish, _publish_one,
> actions.mark_published, cli.cmd_resolve, a stray Post(...) constructor) **can** produce the ghost row
> `Post(state=published, public_url='')`"

**Sub-claim** ([actions.py:280-281](src/fanops/studio/actions.py:280), repeated at
[cli.py:392-393](src/fanops/cli.py:392)):
> "set the URL BEFORE the state flip so the @model_validator sees a consistent shape on the next
> ledger save (**Pydantic re-validates the modified instance on serialization**)"

**Method.** `Post` carries a `@model_validator(mode="after")` ([models.py:357](src/fanops/models.py:357))
that raises when `state ∈ {published, analyzed}` and `public_url` is blank. `Post` does **not** set
`validate_assignment` (only `Moment` does, [models.py:211](src/fanops/models.py:211)). I ran the three
doors against the installed pydantic **2.13.4**:

| Door | Mechanism | Result |
|---|---|---|
| Construction `Post(**v)` | the validator | **RAISES** ✅ |
| `model_copy(update={"state": published})` — used by `Ledger.set_post_state` [ledger.py:572](src/fanops/ledger.py:572) | — | **NO RAISE** ❌ |
| `post.state = PostState.published` — used by [run.py:306](src/fanops/post/run.py:306), [postiz.py:412](src/fanops/post/postiz.py:412), [actions.py:280](src/fanops/studio/actions.py:280), [track.py:193](src/fanops/track.py:193), [cli.py:398](src/fanops/cli.py:398) | — | **NO RAISE** ❌ |
| `model_dump()` of the bad row | — | **serializes cleanly** ❌ |
| **next `Ledger.load` of that row** | the validator, at construction | **RAISES** → wrapped `ControlFileError` [ledger.py:465](src/fanops/ledger.py:465) |

**Verdict.**
- The **sub-claim is FALSE**: pydantic v2 does **not** re-validate on serialization. Two code comments
  assert a mechanism that does not exist.
- The **main claim is FALSE as written** for **4 of the 5 doors it names**. Only "a stray `Post(...)`
  constructor" is actually blocked by the type.
- **The safety property nevertheless HOLDS** — via four *independent manual guards* at the call sites:

| Door | Its real guard |
|---|---|
| `DryRunPoster.publish` | post-M2 it sets **no state at all** ([dryrun.py:39-41](src/fanops/post/dryrun.py:39)) |
| `_publish_one` | `if (post.public_url or "").strip():` ([run.py:305](src/fanops/post/run.py:305)) |
| `actions.mark_published` | rejects a blank url up front ([actions.py:268-270](src/fanops/studio/actions.py:268)) |
| `cli.cmd_resolve` | `--url` REQUIRED for any terminal state ([cli.py:384-388](src/fanops/cli.py:384)) |
| `track` (`published→analyzed`) | guarded transitively — `prior is published` ([track.py:192](src/fanops/track.py:192)) implies a URL already exists |

**Why this matters, precisely.** The failure mode is not a rejected write — it is a **load-time poison
pill**. A 6th door added without a manual guard would **save successfully** and then make the entire
ledger **unloadable on the next process start** (`ControlFileError`), taking down the daemon and every
Studio page at once. The type-level validator is a *tripwire at load*, not a *gate at write*.

**Classification: Refined** (property holds, mechanism misstated) **+ False** (the serialization
sub-claim).

---

## INV-02 — "Single writer of `queued`" · **False** (Cycle-1 `FIND-002` re-confirmed and refined)

**Claim** ([src/fanops/CLAUDE.md](src/fanops/CLAUDE.md)):
> "do NOT set a post's state to `queued` anywhere except `Ledger.approve_post`"

Cycle 1 found 7 writers. **Cycle 2 confirms all 7 and the safety argument.** Each non-`approve_post`
writer is guarded on a *source* state that is **never `awaiting_approval`**:

| Writer | Source-state guard |
|---|---|
| [ledger.py:591](src/fanops/ledger.py:591) `approve_post` | `awaiting_approval` ([:579](src/fanops/ledger.py:579)) |
| [actions.py:1000](src/fanops/studio/actions.py:1000) | `failed`/`error` |
| [actions.py:1031](src/fanops/studio/actions.py:1031) | `failed`/`error` |
| [actions.py:1062](src/fanops/studio/actions.py:1062) | `failed`/`error` + transient |
| [actions.py:1103](src/fanops/studio/actions.py:1103) | `classify_failure == "oversize"` |
| [run.py:238](src/fanops/post/run.py:238) `_unclaim_no_integration` | `submitting` ([:237](src/fanops/post/run.py:237)) |
| [run.py:422](src/fanops/post/run.py:422) daemon retry | `failed` ([:413](src/fanops/post/run.py:413)) + bounded |

**Verdict: the doc's mechanism (single-writer) is FALSE; the property (an unapproved post is
structurally unpublishable) is TRUE and is enforced by `approve_post`'s guard at
[ledger.py:579](src/fanops/ledger.py:579).** Identical shape to INV-01.

---

## INV-03 — "`get_poster` RAISES rather than build a `DryRunPoster` when live" · **False** *(resolves `UNK-006`)*

**Claim** ([src/fanops/CLAUDE.md](src/fanops/CLAUDE.md), [post/CLAUDE.md](src/fanops/post/CLAUDE.md)):
> "`get_poster` raises rather than build a `DryRunPoster` when live (`post/__init__.py:19`)"

**Code** ([post/__init__.py:13-29](src/fanops/post/__init__.py:13)):

```python
resolved = backend or cfg.poster_backend
if cfg.is_live and (resolved or "").lower() == "dryrun":     # :19  ← ONLY the literal string
    raise RuntimeError(...)
provider = get_provider(cfg, resolved)                       # :25  PROVIDERS.get(name) — CASE-SENSITIVE
if provider is not None:
    return provider.make_poster(cfg)
from fanops.post.dryrun import DryRunPoster
return DryRunPoster(cfg)                                     # :29  ← UNGUARDED. Reached when live.
```

The guard at `:19` fires **only** for the exact string `dryrun` (case-insensitively). `get_provider`
is `PROVIDERS.get(name)` ([providers.py:56](src/fanops/post/providers.py:56)) — a **case-sensitive**
dict with lowercase keys. Therefore any backend string that is **not in `PROVIDERS`** — `"Postiz"`,
`"postiz "`, `"blotato"`, `""` — returns `None` and **falls through to `DryRunPoster` on a LIVE
system, without raising.** The guard and the lookup disagree on case.

**Reachability (proven chain):**

1. `Account.backends: dict[str, str]` ([accounts.py:91](src/fanops/accounts.py:91)) — pydantic
   validates the *type*, never membership in `_VALID_BACKENDS`.
2. `Accounts.load` → `Account(**x)` ([accounts.py:143](src/fanops/accounts.py:143)) — accepts any string.
3. `accounts.json` is **explicitly hand-edited by the operator** ([accounts.py:112-113](src/fanops/accounts.py:112)).
4. `resolve_backend` returns the raw value ([accounts.py:181](src/fanops/accounts.py:181));
   `effective_provider` returns it unvalidated ([accounts.py:192-194](src/fanops/accounts.py:192)).
5. `_post_provider` (live) returns it ([run.py:167](src/fanops/post/run.py:167)).
6. `publish_due` sees a provider that is neither `None` nor `"dryrun"` → calls `_publish_one`
   ([run.py:470](src/fanops/post/run.py:470)) → `get_poster(cfg, "Postiz")` → **`DryRunPoster`**.

The **write** boundary *is* guarded — `set_backend` raises on an unknown backend
([accounts.py:414-415](src/fanops/accounts.py:414)). But `Accounts.validate()` checks only that the
integration/backend pair is *both-set-or-both-unset* ([accounts.py:241-250](src/fanops/accounts.py:241));
it **never validates the backend value**. So a hand-edit passes `validate()` and reaches publish.

**Blast radius (traced, not assumed).** It is **not** the phantom-published row the `ROOT FIX` comment
at [post/__init__.py:14-17](src/fanops/post/__init__.py:14) fears:

- `_ensure_media` takes the upload branch (`backend != "dryrun"`, [run.py:208](src/fanops/post/run.py:208)),
  but `get_media_uploader` **also** falls back to `_dryrun_uploader`
  ([post/__init__.py:38-41](src/fanops/post/__init__.py:38)) → media becomes a **`file://` URL**.
- `DryRunPoster.publish` post-M2 sets **no state, no submission_id, no public_url**
  ([dryrun.py:39-41](src/fanops/post/dryrun.py:39)) — so the R1 gate at
  [run.py:298](src/fanops/post/run.py:298) never fires and **no ghost `published` row is created**.
- The post is therefore **left in `submitting`** and FINALIZE persists it
  ([run.py:355-357](src/fanops/post/run.py:355)). `publish_due` never re-drives `submitting`
  ([run.py:442](src/fanops/post/run.py:442)).
- It is **eventually labeled**: reconcile escalates `submitting → needs_reconcile` at 72 h
  ([reconcile.py:746-750](src/fanops/reconcile.py:746)), then stamps `GAVE UP:`
  ([reconcile.py:757-762](src/fanops/reconcile.py:757)).

**Verdict: FALSE as written.** GATE-2 is not a live/dryrun gate — it is a *literal-string-`dryrun`*
gate. The dryrun-safety property survives (via `DryRunPoster`'s post-M2 no-op contract, **not** via
the raise), but the **liveness** property does not: a typo'd backend silently never publishes and
takes 72 h to surface. Note the sibling function's docstring
([post/__init__.py:34-35](src/fanops/post/__init__.py:34)) *openly documents* the unknown-backend
fallback for the uploader — so the fallback is known; it is only `get_poster`'s **doc** that overstates.

---

## INV-04 — `intro_match` gate · **False** (structurally unanswerable) *(resolves `UNK-002`)*

`intro_match` is **wired into the live pipeline**: `pipeline.py:242-244` calls `request_intro_match`
then `ingest_intro_match`, and `request_intro_match` writes a real gate file
(`write_request(cfg, kind="intro_match", key=key, ...)`, [intro_match.py:108](src/fanops/intro_match.py:108)).

But **nothing can answer it**:

| Registry | Contains `intro_match`? | Evidence |
|---|---|---|
| `responder._SCHEMA` (what `answer_pending` iterates) | ❌ | [responder.py:50](src/fanops/responder.py:50), consumed at [:216-217](src/fanops/responder.py:216) |
| `responder._PROMPT` | ❌ | [responder.py:51](src/fanops/responder.py:51) |
| `gate_keys.gate_source_id` | ❌ no branch | [gate_keys.py:9-13](src/fanops/gate_keys.py:9) |

`answer_pending` builds its work list from `_SCHEMA.items()` only
([responder.py:216-217](src/fanops/responder.py:216)) — so `LlmResponder` **never sees an
`intro_match` gate**. `ingest_intro_match`'s `read_response` returns `None` forever
([intro_match.py:124-125](src/fanops/intro_match.py:124)), `Moment.intro_matches` is never written,
and `stitch_render._intro_tease_candidates` ([stitch_render.py:112](src/fanops/stitch_render.py:112))
never gets a candidate. **No intro_tease plan can ever be produced.**

Secondary: the gate key is `_hash("intro_match", m.id, …)` ([intro_match.py:58](src/fanops/intro_match.py:58))
— a 12-hex digest, **not** a clip id. `gate_source_id` falls into its clip branch, `led.clips.get(<digest>)`
returns `None`, and the function returns `None`. Per `pipeline_status.py:69`, gates whose owner can't
be resolved are **omitted from `by_source`** — so the orphaned gates are also invisible in the
status view.

**Classification (against the brief's options): UNFINISHED.** The request half is wired; the answer
half was never registered. Not *dead* (it is called), not *orphaned* (its caller is live), not
*intentionally unreachable* (nothing says so).

**Live blast radius: NIL by default.** `cfg.intro_tease` is **DEFAULT OFF**
([config.py:731-732](src/fanops/config.py:731)), and `request_intro_match` returns early unless
`intro_tease AND responder_mode == "llm"` ([intro_match.py:90](src/fanops/intro_match.py:90)).
With `FANOPS_INTRO_TEASE=1` **and** `FANOPS_RESPONDER=llm`, unanswerable `.request.json` files
**accumulate in `04_agent_io/requests/` forever**, never answered, never garbage-collected, and
invisible to the by-source status view.

---

## INV-05 — "`Settings` is constructed per `Config()`" · **False** *(resolves `UNK-007`)*

**Claim** ([settings.py:1](src/fanops/settings.py:1)): *"typed env boundary (constructed per Config(),
never import-cached)"*; ([settings.py:142-143](src/fanops/settings.py:142)): *"Built fresh per Config()
after load_dotenv(override=True)"*.

**`Config.__init__` never touches `Settings`.** [config.py:144-177](src/fanops/config.py:144) sets
paths only. **All 74 `os.getenv` calls in config.py** read the environment **directly**.
`Settings.runtime_load` — the method whose docstring makes the claim — has **zero callers in `src/`**.

**Ownership / precedence / lifetime — proven:**

| Question | Answer |
|---|---|
| **Who owns runtime env reads?** | **`Config`, exclusively.** Every property is `os.getenv(...)`, evaluated **per access** (no caching). |
| **Precedence between them?** | **None — they never meet.** `Settings` does not feed `Config`. There is no runtime conflict to adjudicate. |
| **Construction** | `Config()` per call site; `Settings` only inside its 3 consumers below. |
| **Lifetime** | `Config` properties are re-read on **every attribute access** — so a `go_live` `os.environ[...]` write ([golive.py:66](src/fanops/studio/golive.py:66)) is visible **immediately**, without a new `Config`. |
| **Duplication** | **Real.** Both hand-roll parse logic for the same keys. |
| **Possible disagreement** | **Yes, structurally** — but only where a `Settings` consumer runs. |

**The three (and only three) `Settings` consumers:**

1. [accounts.py:13](src/fanops/accounts.py:13) — imports the **constant** `_VALID_BACKENDS` only, not the class.
2. [config_introspect.py:11,82,87](src/fanops/config_introspect.py:11) — read-only **docs/introspection** surface built from `Settings.model_fields`.
3. [doctor.py:23,27](src/fanops/doctor.py:23) — `Settings.strict_validate()`, a **preflight validator**.

**The strict/lenient split is intentional, not drift.** `Config.poster_backend` warns and falls back
to `dryrun` on an unknown value ([config.py:241-245](src/fanops/config.py:241)); `Settings.strict_validate`
**raises** ([settings.py:388-389](src/fanops/settings.py:388)). Runtime is lenient, doctor is strict.
That is coherent.

**The genuine hazards:**
- **`_VALID_BACKENDS` is defined twice** — [config.py:72](src/fanops/config.py:72) and
  [settings.py:18](src/fanops/settings.py:18). `config.poster_backend` validates against its own copy;
  `accounts.set_backend` validates against `settings`'. If they ever drift, the *write* boundary and
  the *read* boundary disagree about what a legal backend is. (See `COUP-05`.)
- **`FANOPS_ROOT` is absent from `Settings`** though `Config.__init__` reads it
  ([config.py:145](src/fanops/config.py:145)) — so it is invisible to `config_introspect`'s generated
  config surface. (Consistent with it being shell-only by design.)

**Verdict: FALSE.** The docstring describes a wiring that does not exist. **No correctness hazard
today** (there is no runtime disagreement, because there is no runtime handoff) — the hazard is
maintenance: two hand-maintained parsers for one env surface.

---

## INV-06 — Cascade protection gates on live **clip** states · **Refined**

**Claim** ([ledger.py:645-648](src/fanops/ledger.py:645)): *"Clip/Post states that mean 'live on the
platform' … these are **NEVER** cascade-deleted"*.

`_LIVE_CLIP_STATES = (ClipState.published, ClipState.analyzed)` ([ledger.py:649](src/fanops/ledger.py:649)).
**Nothing in `src/` writes either state** (see [`STATE_MACHINE.md`](STATE_MACHINE.md) §1), so
`clip_live` ([ledger.py:665](src/fanops/ledger.py:665)) is **always `False`** and every branch it
gates is unreachable.

**The property holds anyway**, entirely via the **post** check `p.state in _PROTECTED_POST_STATES`
([ledger.py:667](src/fanops/ledger.py:667), [:675](src/fanops/ledger.py:675)). And it is *sound*: when
no protected post hangs off a clip, the cascade deletes **the posts too**
([ledger.py:670](src/fanops/ledger.py:670)) — nothing is orphaned.

**Verdict: Refined.** The clip half of the guard is dead code. `PostState.retired`'s membership in
`_PROTECTED_POST_STATES` is likewise inert (no writer). The CLAUDE.md instruction *"must keep gating
deletes on `_PROTECTED_POST_STATES`"* is **correct and load-bearing** — that tuple is what actually
protects.

---

## INV-07 — "`00_control/ledger.lock` is vestigial" · **False** *(corrects Cycle-1 `DEAD-005`)*

**Claim** ([config.py:159](src/fanops/config.py:159)): `self.lock_path = self.control / "ledger.lock"
# vestigial; accounts/personas use flock`. Cycle 1 filed this as `DEAD-005`, *"Confirmed dead —
declared **and labelled vestigial in code**"* — resting on the comment, not on a call-graph check.

**It has exactly one live consumer:** `Ledger.restore_snapshot` →
`with _file_lock(cfg.lock_path):` ([ledger.py:551](src/fanops/ledger.py:551)).

**It is worse than dead — it is a lock that excludes nothing.** Every *other* ledger writer serializes
on the SQLite `BEGIN IMMEDIATE` write transaction ([ledger_sqlite.py:92](src/fanops/ledger_sqlite.py:92)).
`restore_snapshot` takes an **`fcntl.flock` on a different file** and then calls
`os.replace(tmp, self.db_path)` ([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)), swapping the
whole database file. **The two locks are mutually invisible.** A concurrent `Ledger.transaction()`
holds its SQLite lock on the *old inode* and would commit into a file that has been unlinked from the
path.

**Mitigating fact:** `restore_snapshot` has **no production caller** — the only reference outside its
definition is a docstring at [ledger_wipe.py:246](src/fanops/ledger_wipe.py:246). It is an operator
break-glass API. So exposure today is nil; the **classification** is what was wrong.

**Reclassify `DEAD-005`: not dead — a live single-holder lock providing no mutual exclusion against
the real ledger writers.**

---

## INV-08 — No-auto-publish · **Verified**

Three mint sites, all born `awaiting_approval`: [crosspost.py:238](src/fanops/crosspost.py:238),
[actions.py:491](src/fanops/studio/actions.py:491), [actions.py:570](src/fanops/studio/actions.py:570).
The model default is `awaiting_approval` ([models.py:298](src/fanops/models.py:298)), so even a bare
`Post(...)` is unpublishable. Both publish entry points iterate `queued` **only**
([run.py:442](src/fanops/post/run.py:442); `publish_post` claims via the same `_publish_one` guard at
[run.py:266](src/fanops/post/run.py:266)). Combined with INV-02's guard analysis: **no path moves a
post out of `awaiting_approval` except `approve_post`.** ✅

Defence-in-depth confirmed: a **timeless** `queued` post is **not** due — it parks with a breadcrumb
rather than auto-publishing ([run.py:381-384](src/fanops/post/run.py:381)).

## INV-09 — `_publish_one` is the sole network-POST caller · **Verified**

`publish_due` ([run.py:470](src/fanops/post/run.py:470)) and `publish_post`
([run.py:502](src/fanops/post/run.py:502)) both funnel into `_publish_one`, the only caller of
`poster.publish` ([run.py:296](src/fanops/post/run.py:296)). ✅

## INV-10 — `needs_reconcile` never downgraded to `failed` · **Verified**

Guarded at both layers: [run.py:325](src/fanops/post/run.py:325) (`if post.state is not
PostState.needs_reconcile:`) and [postiz.py:432](src/fanops/post/postiz.py:432) /
[zernio.py:270](src/fanops/post/zernio.py:270). ✅

## INV-11 — `AuthError` halts the run · **Verified**

`_is_fatal_auth_error` matches by **type**, not substring ([run.py:102-110](src/fanops/post/run.py:102))
— the H8 fix. It **re-raises** ([run.py:320-321](src/fanops/post/run.py:320)), propagating out of
`publish_due`. ✅

## INV-12 — Atomic ledger writes · **Verified**

`BEGIN IMMEDIATE` ([ledger_sqlite.py:92](src/fanops/ledger_sqlite.py:92)) + `DELETE`-then-re-`INSERT`
full replace ([ledger_sqlite.py:64-71](src/fanops/ledger_sqlite.py:64)) + WAL + `synchronous=FULL`
([ledger_sqlite.py:27-28](src/fanops/ledger_sqlite.py:27)). Save runs **only on clean exit**
([ledger.py:487](src/fanops/ledger.py:487)); any raise rolls back
([ledger_sqlite.py:103-105](src/fanops/ledger_sqlite.py:103)). ✅

## INV-13 — Newer schema refused · **Verified**

[ledger.py:436-440](src/fanops/ledger.py:436) raises `_NewerSchema` rather than load-and-drop. ✅

## INV-14 — Bias actuators amplify-only + validation-frozen · **Verified**

`p4_dim_bias`, `variant_amplify`, `timing_bias` are each DEFAULT-OFF
([config.py:897](src/fanops/config.py:897), [:778](src/fanops/config.py:778),
[:907](src/fanops/config.py:907)) and gated on `validation_gate.learning_validated`. The documented
exception is honest and self-declared: **`variant_ucb` is NOT validation-frozen**
([config.py:821-827](src/fanops/config.py:821)) — a scorer swap on the safe caption-bias read path,
gated by statistics alone. ✅

## INV-15 — Schedule monotonicity · **Verified** (the strongest invariant in the codebase)

`_STEP_MIN = 40`, `_JITTER_MAX = 30` ([crosspost.py:28-29](src/fanops/crosspost.py:28)), backed by a
**module-level `assert`** ([crosspost.py:30](src/fanops/crosspost.py:30)) — the only invariant enforced
at import time. Reinforced by a defensive `while t <= prev_slot: t += _STEP_MIN` loop
([crosspost.py:68-69](src/fanops/crosspost.py:68)). ✅

## INV-16 — builtin `hash()` banned · **Verified**

[ids.py:2](src/fanops/ids.py:2). SHA-1 with `usedforsecurity=False`, truncated to 12 hex
([ids.py:7](src/fanops/ids.py:7)). `crosspost._seed` uses SHA-1 explicitly and says why
([crosspost.py:36-37](src/fanops/crosspost.py:36)). ✅

## INV-17 — `[framing]` is the only fail-CLOSED dependency · **Verified**

`framing.require_cv2` → `ToolchainMissingError` → exit 2 when `smart_framing` is ON (default,
[config.py:611-612](src/fanops/config.py:611)) and cv2 is absent. Every other optional extra fails
open. ✅

## INV-18 — `go_live` is the only `FANOPS_LIVE=1` setter · **Verified**

`os.environ[...]` is written in exactly **three** places in `src/`:
[autopilot.py:80](src/fanops/autopilot.py:80) (`FANOPS_RESPONDER`),
[golive.py:66](src/fanops/studio/golive.py:66) (the generic `_dual_write` setter), and nothing else.
Only `golive` writes `FANOPS_LIVE`. ✅

## INV-19 — Ledger forward-compat · **Verified**

No ledger model sets `extra=`; pydantic v2's default is `extra="ignore"`
([models.py:171-176](src/fanops/models.py:171)). An older binary parsing a newer ledger drops unknown
keys rather than crashing. Load-bearing (Cycle-1 `SHIM-005`). ✅

---

## INV-20 — Doc line-number references · **False** (systematically stale)

Not one safety claim's line reference in `CLAUDE.md` still resolves. This matters because the citations
are the only way a future editor locates the guard they are told not to break.

| Doc | Claims | Actual |
|---|---|---|
| `src/fanops/CLAUDE.md` | `approve_post` at `ledger.py:503`, promotes at `:519` | `:575`, promotes `:591` |
| `src/fanops/CLAUDE.md` | `_delete_moment_cascade` at `ledger.py:614`; `_PROTECTED_POST_STATES` at `:612` | `:662`; `:660` |
| `src/fanops/CLAUDE.md` | `_post_provider` returns dryrun at `run.py:120` | `:166` |
| `src/fanops/CLAUDE.md` | `validation_gate.py:22` | `learning_validated` per Cycle 1 is at `:18` |
| `post/CLAUDE.md` | `publish_due` at `run.py:337` | `:433` |
| `post/CLAUDE.md` | `_publish_one` at `run.py:213` / `:227` | `:242` |
| `post/CLAUDE.md` | CLAIM at `run.py:241-247`; NETWORK `:248-321`; FINALIZE `:322+` | `:264-272`; `:273-342`; `:351+` |
| `post/CLAUDE.md` | `_publish_throttle_last` at `run.py:83`; `reset_publish_throttle` at `:88` | `:123`; `:131` |
| `post/CLAUDE.md` | `_post_provider` at `run.py:113`, returns `:120` | `:158`, returns `:166` |
| `post/CLAUDE.md` | `publish_now` at `studio/actions.py:361` | `publish_now` is at `:378`-ish (`_studio_publish_guard` at `:288`) |

The **function names and the semantics are right**; only the line numbers rotted. Recorded as a fact,
not a recommendation.

---

## INV-21 — Studio route authorization · **Verified-absent**

There is **no authentication, no session, no CSRF token, and no `before_request` hook** anywhere in
`studio/app.py` — grep for `before_request|csrf|login|auth|secret_key` returns only two unrelated
comment lines ([app.py:45](src/fanops/studio/app.py:45), [:56](src/fanops/studio/app.py:56)).

**All 108 mutating routes are unauthenticated** (full map in [`COUPLINGS.md`](COUPLINGS.md) §Routes).
That includes `POST /golive/live` (flip the system to live publishing), `POST /schedule/publish-due`
(publish the whole due bucket), and `POST /live-library/wipe/confirm` (destructive wipe).

The mitigation is **binding, not authorization**: the server is launched via `app.run(host=…, port=…)`
([cli.py:1285](src/fanops/cli.py:1285)) with an operator-supplied host, conventionally `localhost:8787`.
Per project memory this was a **deliberate, recorded decision** ("DECLINED localhost-only CSRF/SSRF/
rate-limit"). Recorded here as ground truth, not as a recommendation: **the security boundary is the
network interface, and any change to `--host` removes the only control.**
