# C4-SLICE-06 — A recovery action must not leave a post carrying a stale status string

**Root cause:** `RC-8` / `C4-F1a` · **Severity: MEDIUM** · **Prerequisite: S03**
**PR title must carry:** `(Unit: revert-clears-stale-reason)`

---

## 0. Before you edit anything
**Reverify the cited lines.** State the root cause in your own words.

---

## 1. What is broken

`bulk_send_to_review` ([actions.py:944-951](src/fanops/studio/actions.py:944)) clears **four** fields on revert:

```python
p.state = PostState.awaiting_approval
p.scheduled_time = None
p.public_url = ""
p.metrics = {}
p.published_at = None
# Don't touch submission_id / batch_id — keep the lineage        ← deliberate, and FINE (see §3)
```

**`error_reason` is the one omission — and it is the one that latches.**

### Why that one omission is the whole defect

`Post.error_reason` is not a message. It is a **four-way-overloaded control channel** (`COUP-03`), and its
**fourth** semantic is a **permanent suppression latch**:

> [reconcile.py:767](src/fanops/reconcile.py:767) — `if post.error_reason: continue`
> **Any** non-empty value makes reconcile **skip the post forever.**

**Executed (EXP-C4-1):** a post reverted from `failed` and re-approved reaches `submitting` **still carrying**
`"reconciled: poster reports failed (IG rejected: unsupported aspect ratio)"`.

That stale string:
1. **Lies about the post's state** — the operator sees a post in `submitting` whose reason says *"poster reports
   failed."*
2. **Trips the `:767` latch on the VERY FIRST reconcile pass** — so the post **never even earns the `stuck …`
   breadcrumb** Cycle 3 assumed it would get. **It is silent from pass one.**

---

## 2. The fix

**One line.** Add `p.error_reason = None` to the field-clearing block.

**A revert means "this post is going back to Review." Every field describing its previous life is cleared — and
`error_reason` describes its previous life more than any of them.**

---

## 3. What you must **NOT** change, and why

🔴 **`submission_id` and `batch_id` stay preserved.** *("keep the lineage" — `actions.py:949-950`.)*

- Clearing `submission_id` would let a re-approved post **re-POST** — and if the post was **actually live**, that
  is a **duplicate on the platform.**
- That question is **`PD-1`, and it is undecided.**
- 🔴 **`S03` makes preserving it SAFE:** with the claim refusing a real-sid post, a preserved `submission_id`
  simply means the post **stays visibly `queued`** instead of being silently stranded.

**This slice is HYGIENE. `S03` is the SAFETY fix. `S06` without `S03` is not harmful — merely incomplete.**

🔴 **Do NOT fix `reconcile.py:767` here.** **`S04` owns it** (same file, same branch it rewrites). Duplicating it
would create a merge conflict and split one change across two PRs.

---

## 4. Acceptance criteria

1. After `bulk_send_to_review`, `post.error_reason` **is `None`**.
2. `submission_id` and `batch_id` are **still preserved**.
3. `reconcile.py` is **not touched**.

## 5. Tests

| Test | Must fail before? |
|---|---|
| `test_revert_clears_stale_reason` — *encodes EXP-C4-1's observation* | ✅ |

## 6. Enumerate before you edit
Every other state-reverting action in `studio/actions.py` — **do any of them have the same omission?**
*(Check `unapprove_post`, the four requeue paths, `recover_posts`. The requeue paths **do** clear
`error_reason` — [actions.py:1033](src/fanops/studio/actions.py:1033),
[:1105](src/fanops/studio/actions.py:1105) — which is **more evidence that `bulk_send_to_review`'s omission is a
bug, not a policy.** **Say this in the PR.**)*

## 7. 🔴 Forbidden scope expansion
- ❌ Do **not** clear `submission_id` — `PD-1`, undecided.
- ❌ Do **not** touch `reconcile.py:767` — **S04** owns it.
- ❌ Do **not** split `error_reason`'s four semantics into typed fields. Larger change, needs a migration.
  **Deferred and filed.**
- ❌ Do **not** touch `actions.py:1029-1034` or `:1100-1102` — that is **S09** (same file, ~80 lines away).

## 8. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge on green: YES.** **Verifier: not required.**
**Rollback:** revert.
**State remaining unknowns honestly.**
