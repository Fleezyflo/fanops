# C4-SLICE-09 — The shrink temp dir gets an owner

**Root cause:** `RC-10` + `C4-F5` · **Severity: LOW** · **Prerequisites: none** · **Buildable in parallel**
**PR title must carry:** `(Unit: shrink-tempdir-lifetime)`

---

## 0. Before you edit anything

**Reverify the cited lines.** Then read §2 — **it tells you which of Cycle 3's two claims about this defect is
FALSE**, and it will save you from building a migration you do not need.

---

## 1. What is broken

**(a) The leak.** `compress.maybe_shrink_for_cap` ([compress.py:19-21](src/fanops/post/compress.py:19)):

```python
shrink_root = cfg.base / "04_agent_io"
tmp = Path(tempfile.mkdtemp(prefix="fanops-shrink-", dir=str(shrink_root)))
```

**Nothing in the tree ever removes it.** AST-confirmed: it is the **only** `mkdtemp` in `src/fanops`, and the
only `rmtree` calls are in `transcribe.py` (demucs/keyframes). **One dir per oversize (> 4 MiB) TikTok/Zernio
upload, forever.** No log, no metric, no doctor check, no wipe path.

**(b) A durable pointer into it.** The Studio **oversize-retry** path persists `Post.media_urls` into that dir
**inside a committed transaction**:

```python
# studio/actions.py:1024   with Ledger.transaction(cfg) as led:
# :1029                        apply_shrink_to_post(cfg, led, p)      → compress.py:113 sets
#                                                      p.media_urls = ["file://<mkdtemp>/…"]
# :1034                        p.media_urls = [u for u in … if not u.startswith("http")]   ← KEEPS the file:// one
#                          ↳ COMMITS
```
*(Same shape at [:1100-1102](src/fanops/studio/actions.py:1100).)*

---

## 2. 🔴 Two Cycle-3 claims about this defect are FALSE. Read this before you design.

### ❌ FALSE #1 — *"`Render.path` is durably rewritten into the mkdtemp dir"* (`C3-F5`)

**`Render.path` is UNREACHABLE.**

| Check | Result |
|---|---|
| `Ledger.add_render` callers (AST) | **0** |
| `Render(...)` constructor sites (AST) | **2** — *both deserializers* |
| [crosspost.py:225](src/fanops/crosspost.py:225) | **`render_id = None`** — hardcoded on every minted Post |
| **Live ledger** (read-only) | **0 renders · 0 of 347 posts carry a `render_id`** |

Every `Render.path` write sits behind an `if r is not None:` / `if post.render_id:` guard that is **always
false.** *(Cycle 3's experiment hand-built a `Render` row — that proves the **mechanism**, not the
**reachability**.)*

### ❌ FALSE #2 — *"the fix for the leak would BREAK the pointer"*

**Refuted by execution (EXP-C4-4).** Delete the temp dir and the system **self-heals**:

- `media_path_for_post` ([compress.py:61-65](src/fanops/post/compress.py:61)) falls through its `media_urls`
  branch to the **clip** branch and returns `clip.path`;
- the next `apply_shrink_to_post` **re-points `media_urls` at a fresh, existing file.**

> ### ✅ Therefore: **the cleanup is a SAFE, INDEPENDENT slice.**
> **No migration. No path relocation. No atomicity requirement.** If you find yourself designing a migration,
> **stop — you are solving a refuted problem.**

**Residual harm of the stale pointer:** a broken Studio media preview + one re-shrink per publish (disk churn).
**Real, but LOW — not a correctness break.**

---

## 3. The fix

**The code that CREATES the dir must own its lifetime.**

- Remove the temp dir once its output has been uploaded (i.e. once `media_urls` no longer references it) — **or**
  write to a deterministic, owned location instead of `mkdtemp`.
- **Stop persisting the `file://` temp URL** in the two Studio retry paths
  ([actions.py:1034](src/fanops/studio/actions.py:1034), [:1102](src/fanops/studio/actions.py:1102)).
- Add a **doctor check** + a `fanops clean --shrink` verb for the **already-leaked** dirs on the live tree.
  ⚠️ **`S08` owns `cli.py`** among the Cycle-4 slices — **coordinate.**

---

## 4. Acceptance criteria

1. The shrink temp dir is **removed** once its output is uploaded (or is not a `mkdtemp` at all).
2. **No persisted `Post.media_urls` entry resolves under a directory matching `fanops-shrink-*`.**
3. 🔴 **The publish path STILL WORKS after the shrink dir is removed** — **pin the self-heal with a test**, so a
   future change cannot silently break the fallback this slice depends on.
4. A `fanops clean --shrink` verb (or equivalent) removes the already-leaked dirs, and a doctor check surfaces
   them.

## 5. Tests

| Test | Must fail before? |
|---|---|
| `test_shrink_tempdir_is_cleaned` | ✅ |
| `test_no_persisted_media_url_points_into_a_shrink_dir` | ✅ |
| `test_publish_still_works_after_the_shrink_dir_is_removed` *(pins the self-heal)* | ⚪ |

> 🔴 **Write `test_no_persisted_media_url_points_into_a_shrink_dir` so it would ALSO catch a `Render.path`
> regression if `Render`s are ever minted.**
> **Why:** the `Render.path` writes are dormant **because nothing mints a `Render`** — *not because they are
> safe*. If a future reframe slice gives `Ledger.add_render` its first caller, `C3-F5` **reactivates**. This test
> is the **cheapest possible guard**, and it costs the reframe stream nothing. *(Interference finding `IF-1`.)*

## 6. Enumerate before you edit
Every reader of `Post.media_urls` (`studio/app.py:74`, `preview_media.py:21`, `compress.media_path_for_post:50`,
`run.py:205-224`, `postiz.py:384`, `zernio.py:230`, `paths_rebase.py:53`) · every caller of
`apply_shrink_to_post` (`run.py:204`, `actions.py:1029`, `actions.py:1100`) and `persist_post_shrink`
(`actions.py:407`).

## 7. Preserve
The CRF ladder (28→32→36→40) and its **fail-open** contract (returns the **original** path if all fail) · the
`.mp4` suffix requirement on any temp file (**ffmpeg selects its muxer by extension** — `COUP-07` / `MOL-78`) ·
`media_path_for_post`'s **clip fallback** — 🔴 **this slice DEPENDS on it. Do not "simplify" it away.**

## 8. 🔴 Forbidden scope expansion
- ❌ Do **not** "defensively" repair the `Render.path` writes ([compress.py:112](src/fanops/post/compress.py:112),
  [:131](src/fanops/post/compress.py:131), [run.py:367](src/fanops/post/run.py:367)). **They are unreachable.**
  Fixing unreachable code is over-engineering **and it would conflict with whatever the reframe stream decides
  `Render` should mean.**
- ❌ Do **not** add a cron/tmpwatch janitor. It is now *safe* — but it is the **wrong home**.
- ❌ Do **not** touch the Postiz multipart orphaned-media leak (`R-03`) — a **remote** leak, out of scope.
- ❌ Do **not** touch `actions.py:944-951` — that is **S06** (same file, ~80 lines away).

## 9. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge on green: YES.** **Verifier: not required.**
**Migration:** a **one-time clean** of the already-leaked tree. **Report how many `fanops-shrink-*` dirs exist on
the live tree** (read-only) in the PR.
**Rollback:** revert.
**State remaining unknowns honestly.**
