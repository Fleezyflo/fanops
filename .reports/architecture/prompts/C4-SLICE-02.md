# C4-SLICE-02 — Canonical backend normalization at the READ boundary

**Root cause:** `RC-3` · **Severity: HIGH** · **Prerequisites: none**
**PR title must carry:** `(Unit: backend-normalize-at-read-boundary)`

---

## 0. Before you edit anything

**Reverify every cited line against current source.** Then **state the root cause in your own words.** If your
statement is *"`get_poster`'s guard is case-sensitive,"* you have described a **symptom**. Read §1.

---

## 1. What is broken

`accounts.set_backend` ([accounts.py:412](src/fanops/accounts.py:412)) is the **only** normalizer:
`.strip().lower()` + membership in `_VALID_BACKENDS`. **It guards the Studio/CLI write path only.**

- `Account.backends` is `dict[str, str]` — **unvalidated at load** ([accounts.py:91](src/fanops/accounts.py:91)).
- `Accounts.validate()` checks the integration/backend **pairing**
  ([accounts.py:241-250](src/fanops/accounts.py:241)) — **never the backend value.**
- `accounts.json` is **explicitly hand-editable** — the *documented* operator channel
  ([accounts.py:112-113](src/fanops/accounts.py:112)).

So a raw string reaches **five** consumers with **four** different unknown-value behaviours:

| Resolver | On an unknown value |
|---|---|
| `get_poster` ([post/\_\_init\_\_.py:28-29](src/fanops/post/__init__.py:28)) | 🔴 **silently returns `DryRunPoster` ON A LIVE SYSTEM** |
| `get_media_uploader` ([:40-41](src/fanops/post/__init__.py:40)) | 🔴 silently returns the dryrun `file://` uploader |
| `_post_provider` ([run.py:158](src/fanops/post/run.py:158)) | passes the raw string through |
| `compress.publish_backend_for_post` ([compress.py:69](src/fanops/post/compress.py:69)) | passes through (`COUP-16`) |
| `Config.poster_backend` ([config.py:241](src/fanops/config.py:241)) | warns → dryrun |
| `Settings.strict_validate` | raises *(doctor-only; never runs at runtime — `INV-05`)* |

**The `get_poster` mechanism:** its live-guard at `:19` is case-**insensitive** on the literal `"dryrun"`;
`PROVIDERS.get()` ([providers.py:56](src/fanops/post/providers.py:56)) is case-**sensitive**. **They disagree.**
So `"Postiz"` passes the guard, misses the registry, and **falls through to `DryRunPoster` on a live system.**

**Worst variant:** `"postiz "` (trailing space) is **visually identical** to `"postiz"` in any UI, JSON dump, or
diff.

> **`providers.py:53-55` says: *"no live account routes to an unknown backend … so this path is a defensive
> default."*** That comment **is the defect.** It is an assumption stated as a fact.

---

## 2. 🔴 The fix you will be tempted to ship, and why it is **insufficient**

> **Tempting:** harden `get_poster`'s guard to raise when `cfg.is_live and get_provider(...) is None`.

It closes the **publish** door and **leaves four siblings divergent.** `get_media_uploader` **still** silently
returns the dryrun uploader. This is the audit brief's explicitly-forbidden shallow fix:

> *"tightening only one provider resolver while leaving sibling resolvers divergent."*

---

## 3. The root fix — a malformed value must not be able to **exist in memory**

Extract `normalize_backend(s) -> str | None` — **the exact rule `set_backend` already applies**
(`.strip().lower()`; `None` if not in `_VALID_BACKENDS`) — and apply it at the **READ** boundary
(`Accounts.load`).

- **Case/whitespace variants NORMALIZE.** `"Postiz"` → `"postiz"`; the channel publishes correctly. *(Today it
  silently does not.)*
- **A genuinely-unknown name is SKIPPED and FLAGGED** via the **existing `skipped_rows` channel**
  ([accounts.py:141-145](src/fanops/accounts.py:141)), which `validate()` **already** promotes to a visible
  doctor/health problem ([accounts.py:230-231](src/fanops/accounts.py:230)).
- Add the **value** check to `Accounts.validate()`'s existing pairing check.
- `set_backend` **calls** `normalize_backend` (delete its inline copy). `_VALID_BACKENDS` collapses to **one
  home** (`COUP-05` reducible).
- Harden `get_poster`'s guard **as defence in depth** — with `load` normalizing, it becomes **unreachable**,
  which is the point.

> **This is not new policy. It moves an existing rule to the door that lacks it.**
> All five divergent resolvers consume the **loaded `Accounts` object** — so fixing the load boundary fixes all
> five **without touching five files.**

---

## 4. 🔴 The interaction that could silently start deleting your learning data

**`S02` ↔ `S10` (`RC-7`).** `_learn_pass` is gated on `cfg.is_live_backend`, which derives from
`live_ready_channels()`, which this slice changes.

> **If you normalize a typo'd backend, a previously-dark channel goes live → `is_live_backend` flips `True` →
> `_learn_pass` starts running — INCLUDING the IRREVERSIBLE `retire()` ([adjust.py:95](src/fanops/adjust.py:95),
> which `reconcile_moments` REFUSES to undo, [ledger.py:636-642](src/fanops/ledger.py:636)) — on a deployment
> where it previously did not.**

**Fixing a typo could silently begin permanently retiring moment lineages.**

**Mandatory if `PD-3` is unanswered:** **log the `is_live_backend` transition loudly**, and say so in the PR so
the operator is told that fixing a malformed backend **can unfreeze the learning pass.**

---

## 5. Acceptance criteria

1. The executed Cycle-2 matrix — `"postiz"`, `"Postiz"`, `"POSTIZ"`, `"postiz "`, `" postiz"`, `"blotato"`,
   `"dryrun"`, `"DryRun"` — across `{Accounts.load, validate, effective_provider, get_poster,
   get_media_uploader}`: **no cell may construct a `DryRunPoster` while `cfg.is_live`.**
2. A case/whitespace variant **normalizes** and the channel publishes correctly.
3. `"blotato"` **skips the channel** and **appears in `Accounts.validate()`'s problems.**
4. `normalize_backend` is the **single home** for the rule; `set_backend` calls it.
5. **Existing valid `accounts.json` files normalize to themselves** — byte-identical behaviour.

## 6. Tests

| Test | Must fail before? |
|---|---|
| `test_backend_normalization_matrix` | ✅ |
| `test_unknown_backend_is_surfaced_not_silent` | ✅ |
| `test_valid_accounts_json_is_unchanged` *(non-regression)* | ⚪ |

## 7. Enumerate before you edit
Every reader of `Account.backends`, `resolve_backend`, `effective_provider`, `live_ready_channels`,
`_VALID_BACKENDS` (**both** definitions — `config.py:72` and `settings.py:18`). Confirm each is fixed by the load
boundary or is explicitly out of scope.

## 8. Preserve
The legacy `FANOPS_POSTER` bridge ([accounts.py:196-201](src/fanops/accounts.py:196)) · the `MOL-79` per-row
leniency posture (**one bad row must never crash the registry**) · `go_live`'s past-due-backlog gate.

## 9. 🔴 Forbidden scope expansion
- ❌ Do **not** make `Account.backends` a typed enum — pydantic would then **refuse to load** a legacy registry,
  converting a soft skip into a hard `ControlFileError`. **That is the exact failure mode `SHIM-005`
  (forward-compat `extra="ignore"`) exists to prevent.**
- ❌ Do **not** add CSRF to `/golive/account/backend` (`F-C` is a recorded, accepted decision).
- ❌ Do **not** touch the legacy bridge.
- ❌ Do **not** touch `pipeline.py` — that is **S07**.

## 10. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge: NO. Verifier: REQUIRED** — this changes **which channels publish live**; the blast radius is a real
external side effect.
**Product gate:** `PD-4` (recommendation: normalize case/whitespace; skip-and-flag an unknown name).
**Operational:** a deployment currently *relying* on a malformed value to stay in dryrun **would start
publishing.** Say this in the PR.
**Rollback:** revert. No persisted state changes.
**State remaining unknowns honestly** — especially whether the live `accounts.json` currently contains any
malformed value (check it **read-only** before you ship).
