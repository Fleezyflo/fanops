# Plan — de-curate the scrape auth path

**Principle:** our exception describes OUR state. Instagram's exception describes Instagram's state and
propagates UNTOUCHED — not re-typed, not re-worded, not truncated.

**Grounded on:** instagrapi 2.18.12 (installed). 91 typed exception classes; `ClientError` is the root of
everything Instagram-originated. `ClientError.__init__` does `setattr(self, k, v)` for every key of
`last_json`, so the raised exception IS the response body: `.message`, `.raw_message`, `.error_type`,
`.code` (HTTP status), `.challenge` (`{url, api_path, lock, native_flow}`), `.feedback_message`.

---

## KEEP — not in scope, do not touch

These are from operating experience (the 2026-07-29 lock). They stay exactly as they are:

| Constant | Value |
|---|---|
| `_SCRAPE_DAY_BUDGET` | 40 / account / UTC day |
| `_COOLDOWN_DELAYS_S` | ladder 30m → 1h → 2h → 6h |
| `_CHECKPOINT_DELAY_S` | flat 12h |
| `_SCRAPE_TRY_CAP` | 25 / pass |
| `_SCRAPE_COTAG_ENQUEUE_CAP` | 40 |
| `_scrape_delay_range` | `1,3` |

Also unchanged: `_pass_lease`, 12h cadence, due tiers, retention, `allow_reauth=False` unattended.

---

## REMOVE

### R1 — `_is_throttle` (the invented constraint)

`ig_hashtag_scrape.py:128`. Greps the bare substring `"rate"` across class name AND message.
Verified false positives: `accelerated`, `generated`, `moderate`, `separate`, `corporate` all return True.
Runs FIRST in `_classify_auth_exc`, so it outranks a genuine lock:
`ChallengeRequired('moderate activity detected')` → `ScrapeThrottled` → 30m ladder instead of the 12h freeze.

Delete the function and both call sites (`:287`, `:320`).

### R2 — truncation

`_trunc` (`:35`, n=160) and all 9 call sites. It cuts the platform's answer mid-sentence and is the reason
errors are unreadable during development. Nothing gets truncated. Log lines that need a bound get it at the
logger, not at the source.

### R3 — the local taxonomy

Delete `ScrapeThrottled`, `ScrapeCheckpoint`, `ScrapeSessionExpired`, `ScrapeRefused`, and
`_classify_auth_exc` / `_is_checkpoint` / `_is_login_required`. These re-describe, by substring, a taxonomy
instagrapi already ships as types. `_is_checkpoint` currently works only because instagrapi's English prose
happens to contain the word "challenge" — and it already misses 3 of 8 `ChallengeError` subclasses
(`SelectContactPointRecoveryForm`, `SubmitPhoneNumberForm`, `LegacyForceSetNewPasswordForm`).
`"consent_required"` matches no class in the library at all.

**KEEP exactly one:** `ScrapeUnavailable`, for OUR OWN states — user unset, no session file, instagrapi not
installed, user not in the configured list, all accounts frozen. That is not a description of Instagram.

Everything from Instagram propagates as the instagrapi type it already is.

### R4 — the wasted round trip

`open_client:217` — `client.account_info()` on the `allow_reauth=False` path. Unattended we never re-auth, so
the probe's only effect is to raise slightly earlier than the first real call would. It spends one private-API
request per account per pass and changes no outcome. Delete it on that path.

Keep it on `allow_reauth=True` only, where it decides whether to call `login()` — that decision is why
MOL-727 exists.

`_fetch` already spends `hashtag_info` + `hashtag_medias_top` together per due visit (MOL-856). No change.

### R5 — the duplicate detector

`fanops_hashtags.py:697` — `if "login_required" in msg.lower()`. Diverges from `_is_login_required` (which
strips underscores from the class name). Verified: a real `LoginRequired` mid-pass produces
`ScrapeRefused("LoginRequired: Unknown ({'message': ''})")` → detect = **False** → the pass logs
`unresolved/refused` and keeps burning the try cap against a dead session. Deleted with R3.

---

## What the decision points read instead

No mapping table. Each decision reads the platform's own field where the decision is made.

| Decision | Reads |
|---|---|
| freeze duration | `(exc.challenge or {}).get("lock") is True` → `_CHECKPOINT_DELAY_S`; else the ladder |
| `reason` written to the cooldown blob | `exc.error_type or exc.raw_message or exc.message` — **verbatim, untruncated** |
| refusal code in `cmd_hashtags_refresh` | `exc.code` (HTTP status, already on `ClientError`) |
| `_OUTAGE_REMEDY` | keyed on those verbatim tokens; an unknown token gets **no remedy line**, just the message |

Cooldown blob schema is unchanged — only the value of `reason` changes, from our invented word to
Instagram's own token:

```json
{"accounts": {"<user>": {"streak": 1, "until": "2026-01-01T00:00:00+00:00",
                         "updated_at": "2026-01-01T00:00:00+00:00",
                         "reason": "rate_limit_error",
                         "day": "2026-01-01", "used": 12}}}
```

Consumers collapse from five `except` clauses to two — `except ClientError` (Instagram) and
`except ScrapeUnavailable` (us): `fanops_hashtags.py:668,670,789,854,1024,1028`, `doctor.py:106–113`.

---

## R6 — the per-account scope check

Removed, per operator ruling. Every call we make is a per-tag call, so every error arrives from a per-tag
operation — there is no second, account-level check to perform. Delete `_measure_slice`'s `stop_reason`
return value and `_charge_user`'s branching on it.

One path: platform error → record it against the tag, verbatim → arm the freeze from the platform's own
fields → stop. No scope classification, no `NotFoundError` special case.

Confirmed against the library: `hashtag_info_v1` and `hashtag_medias_top_v1` carry no `except` of their own,
so every failure already propagates as an instagrapi type. Nothing needs to be caught in order to re-raise.

---

## Tests

Delete the synthetic strings in `tests/hashtag_scrape_fakes.py` (`"checkpoint_required"`, bare `BadPassword`).
They were written to match the greps, so they can only confirm them.

Fixtures are constructed **directly from `instagrapi.exceptions`**, with real `last_json` shapes:

```python
E.RateLimitError(**{"message": "", "error_type": "rate_limit_error", "status": "fail"})
E.ChallengeRequired(**{"message": "challenge_required", "status": "fail",
                       "challenge": {"url": "...", "api_path": "/challenge/", "lock": True}})
E.LoginRequired(**{"message": "login_required", "status": "fail"})
E.FeedbackRequired(**{"message": "feedback_required", "feedback_message": "..."})
E.SentryBlock(**{"message": "", "error_type": "sentry_block"})
```

One parametrized test over the real class list. A library upgrade that adds a challenge form or rewords a
message then fails CI instead of silently un-freezing a locked account.

---

## Order

1. R2 (truncation) — standalone, zero behaviour change, makes the rest debuggable
2. R1 (`_is_throttle`) — the invented constraint
3. R3 + R5 (taxonomy + duplicate detector) — one commit; consumers move to `ClientError`
4. R4 (probe) — standalone
5. Fixtures rebuilt from `instagrapi.exceptions`
