# C4-SLICE-05 — Status-client parity: an unknown id is `unknown`, not an error

**Root cause:** `RC-2` (sibling) · **Severity: MEDIUM**
**🔴 PREREQUISITE: `C4-SLICE-04` MUST BE MERGED FIRST. THIS SLICE MUST NOT SHIP ALONE.**
**PR title must carry:** `(Unit: zernio-404-is-unknown)`

---

## 0. 🔴 Why this slice must not ship alone

> **Alone, it fixes the fake-token Zernio case and leaves BOTH the real-token case (`C3-F1`) AND the
> 500-forever case unterminated.**
>
> That is precisely the audit brief's explicitly-forbidden shallow fix: *"adding another special case for a real
> submission token **without defining submission lifecycle ownership**."*

**Confirm `S04` is merged before you open this PR.** If it is not, **stop.**

---

## 1. Correcting the record before you start

**Cycle 3 attributed `C3-F2` to *"a sibling-parity divergence between the two status clients."* That is
FALSE.** Verified at source:

| | `PostizStatusClient` | `ZernioStatusClient` |
|---|---|---|
| `401` | `PostizAuthError` | `ZernioAuthError` |
| **`>= 300`** | **`raise RuntimeError`** ([metrics.py:164-165](src/fanops/post/metrics.py:164)) | **`raise RuntimeError`** ([metrics.py:516-517](src/fanops/post/metrics.py:516)) |

**Identical.** The divergence is the **remote endpoint shape**:

- **Postiz** has *no per-post status endpoint*. An unknown id is a **row absent from a 200-OK *list* page** →
  `{"status": "unknown"}`. **No raise.**
- **Zernio** *has* a true per-post lookup. An unknown id is a **404** on `GET /posts/{id}` → **raise.**

**FanOps code is symmetric. The APIs are not.** The right place to absorb a remote-API difference is **at the
FanOps boundary** — which is what this slice does.

---

## 2. The fix

`ZernioStatusClient.get_status` ([metrics.py:511-531](src/fanops/post/metrics.py:511)) — handle **404**
**before** the generic `>= 300` raise:

```python
if resp.status_code == 401:  raise ZernioAuthError(...)      # UNCHANGED
if resp.status_code == 404:  return {"status": "unknown"}    # ← NEW: matches Postiz's semantics
if resp.status_code >= 300:  raise RuntimeError(...)         # UNCHANGED (5xx still raises)
```

**A 404 semantically *is* `unknown`** — "the backend does not know this id" — **not an error.** Returning it also
lets the `published` / `failed` detection work at all on Zernio.

---

## 3. Acceptance criteria

1. `ZernioStatusClient.get_status` returns `{"status": "unknown"}` on a **404**.
2. 🔴 **A 5xx STILL raises** — per-post-isolated by `reconcile_posts` → parked, never guessed `failed`.
   **Only 404 changes.**
3. 🔴 **A 401 STILL raises `ZernioAuthError`** — the halt path is untouched.

## 4. Tests

| Test | Must fail before? |
|---|---|
| `test_zernio_404_is_unknown_not_an_error` | ✅ |
| `test_zernio_5xx_still_raises` *(non-regression)* | ⚪ |
| `test_zernio_401_still_halts` *(non-regression)* | ⚪ |

## 5. Preserve
The 5xx → `RuntimeError` path (parked, never failed) · the 401 → `ZernioAuthError` halt · the
`{status, publicUrl, tiktokUsername?}` return shape.

## 6. 🔴 Forbidden scope expansion
- ❌ Do **not** change the 5xx or 401 handling. **Only the 404.**
- ❌ Do **not** touch `PostizStatusClient` — it is **already correct**.
- ❌ Do **not** touch `reconcile.py` — that is **S04**, and it must already be merged.

## 7. Process
**CI:** `unit`. Never run the suite locally. Replay both AST ratchets.
**Self-merge on green: YES — but ONLY if S04 is already merged.** Otherwise this is a shallow fix and must be
blocked.
**Verifier: not required** *(given S04 has landed)*.
**Rollback:** revert.
**State remaining unknowns honestly** — in particular, whether Zernio returns 404 (vs 410 or 200-with-error) for
a deleted post. **This is an INTEGRATION CHECKPOINT**, and `post/metrics.py`'s own docstring says the response
shapes are *"locked offline here; the operator verifies live at first publish."* **If you cannot verify it live,
say so.**
