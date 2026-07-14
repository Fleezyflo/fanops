# C4-SLICE-03 — The publish CLAIM refuses a post it will not POST

**Root cause:** `RC-1` · **Severity: HIGH** · **Prerequisites: none** · **Sequential with S04/S05**
**PR title must carry:** `(Unit: claim-refuses-real-submission-id)`

---

## 0. Before you edit anything

**Reverify every cited line.** Then **state the root cause in your own words.** If your statement is *"a post
with a real submission id gets stranded,"* that is the **symptom**. The root is in §1.

---

## 1. What is broken

`_publish_one` ([run.py:242](src/fanops/post/run.py:242)) has three phases. The **CLAIM** commits, then the
**NETWORK** phase decides not to act — and **nothing un-claims.**

```python
# CLAIM  (run.py:264-272)  — a TRANSACTION
if is_real_submission_id(post.submission_id):
    log("republish_with_real_id")           # :270-271  ← LOGS AND **PROCEEDS**
post.state = PostState.submitting           # :272      ← COMMITS on txn exit
# ── the claim is now DURABLE ──────────────────────────────────────────────
# NETWORK  (run.py:287-288)  — lock-free
if is_real_submission_id(post.submission_id):
    log("skip_resubmit_existing_id")        # :287-288  ← **SKIPS THE POST ENTIRELY**
# FINALIZE (run.py:355-357)
    ...persists `submitting`...             # and returns the SUCCESS-SHAPED string "submitting"
```

### 🔴 The contradiction

**Two guards. One predicate. Adjacent phases. Opposite intents.**

| Line | Comment | Action |
|---|---|---|
| `:270-271` | *"repost-freely OK, log it"* | **PROCEEDS** |
| `:287-288` | *"MOL-115 idempotency: never double-POST"* | **REFUSES** |

**The claim is authorized by the first and invalidated by the second.** The post is left `submitting`, having
done nothing, and `_publish_one` returns `"submitting"` — a **success-shaped value** — so `publish_due` counts it
as **neither published nor failed**. It **vanishes from the tally.**

### It is reachable through an ordinary operator workflow (proven, EXP-C4-1)

```
1. post publishes             → submitted, REAL submission_id
2. reconcile polls            → THE BACKEND REPORTS FAILED  (IG rejects the aspect ratio; TikTok flags audio)
                                → PostState.failed, real sid PRESERVED   (reconcile.py:735)
3. operator: "send back to Review"  → awaiting_approval, sid PRESERVED   (actions.py:949)
4. operator: re-approve             → queued,            sid PRESERVED   (ledger.py:591)
5. publish_due → CLAIM              → submitting  (COMMITTED)
6.               NETWORK            → SKIP the POST
7.               FINALIZE           → submitting  FOREVER
```
**Executed: still `submitting` at +100 000 h.** *(`_REVIEW_REVERT_BLOCKED` blocks `needs_reconcile` — the door is
**`failed`**.)*

---

## 2. The root fix — move the predicate INTO the claim

The precondition — *does this post already carry a real backend id?* — is **fully knowable at claim time.**
**Refusing there is a clean no-op. Refusing after the claim is a state mutation with no owner.**

```python
# run.py CLAIM (:264-272)
if is_real_submission_id(post.submission_id):
    get_logger(cfg)("publish", post_id, "skip_resubmit_existing_id", sub=post.submission_id)
    return None                    # ← txn exits with NO mutation. The post stays `queued`.
post.state = PostState.submitting
```

**Then DELETE both of the following:**
- the now-dead skip at `:287-288`
- the contradictory `republish_with_real_id` log at `:270-271` — *it authorizes what `:287` then forbids*

**And make `publish_due` COUNT the refusal** into its tally (a `skipped_real_sid` bucket). Today the post
vanishes from the tally entirely — **that silence is half the defect.**

> ### The file already contains the correct model
> `_missing_integration_id` is checked **BEFORE** the claim
> ([run.py:256-262](src/fanops/post/run.py:256)) and returns `None`. `is_real_submission_id` is checked
> **AFTER**. **Same function. Two preconditions. Two different phases.** This slice brings one predicate into an
> established pattern — it does not invent one.

---

## 3. Acceptance criteria

1. A `queued` post carrying a **real** `submission_id` is **NOT claimed**: after `publish_due` it is **still
   `queued`**, `poster.publish` was **not** called, and the transaction committed **no mutation**.
2. `publish_due`'s tally **surfaces** the refusal.
3. A fake-token (`fanops_`) post **still publishes normally** *(non-regression)*.
4. `run.py:270-271` and `run.py:287-288` are **deleted**.

## 4. Tests

| Test | Must fail before? |
|---|---|
| `test_claim_refuses_real_submission_id` — *this is EXP-C4-1, encoded* | ✅ *today the post becomes `submitting`* |
| `test_publish_due_tally_surfaces_the_refusal` | ✅ |
| `test_claim_still_publishes_a_fake_token_post` *(non-regression)* | ⚪ |

## 5. Enumerate before you edit
Every caller of `_publish_one` (`publish_due` [run.py:470](src/fanops/post/run.py:470); `publish_post`
[:502](src/fanops/post/run.py:502)) · every writer of `Post.submission_id` · every reader of `_publish_one`'s
**return value**.

## 6. Preserve
- 🔴 **The crash-during-network path into `submitting` is LEGITIMATE (F11) and MUST REMAIN.** A crash between
  the claim-commit and the network is exactly why `submitting` is persisted before I/O. **Do not remove it.**
- The CLAIM's other guards (`state is queued`; the due re-check under lock). **Both correct.**
- The no-double-POST guarantee (`MOL-115`) — **strengthened**, not weakened: the POST is still never sent.

## 7. 🔴 Forbidden scope expansion
- ❌ Do **not** build a "republish (mint a new submission)" action — that is **`PD-1`, undecided.**
- ❌ Do **not** change `reconcile.py`. That is **S04**. *(Posts already stranded are **not** healed by this
  slice — S04 heals them. Say so in the PR.)*
- ❌ Do **not** change `studio/actions.py`. That is **S06**.
- ❌ Do **not** remove the crash-into-`submitting` path.

**Optional, same-file batch:** `C3-F3` — `_requeue_transient_failed_for_daemon`
([run.py:428-429](src/fanops/post/run.py:428)) is the **only** handler in `post/run.py` with **no log line**.
One line. Blast radius nil today. Batch it **only** if it does not obscure the diff.

## 8. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge: NO. Verifier: REQUIRED** — this touches the publish path, the highest-consequence code in the
system.
**Product gate:** `PD-1` **does not block.** Ship without a republish action; the claim-refusal is safe either
way and strictly safer than today.
**Rollback:** revert. No persisted state changes.
**State remaining unknowns honestly** — in particular: **how many posts are ALREADY stranded in `submitting` on
the live ledger?** Check **read-only** and report the count. S04 will act on every one of them.
