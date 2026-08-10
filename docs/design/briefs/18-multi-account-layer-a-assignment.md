# Layer A multi-account assignment

**Status:** implementing on `ci/mol-900-refresh-pass-ready-user-walk`  
**Linear:** [MOL-899](https://linear.app/molham-homsi/issue/MOL-899) → [MOL-900](https://linear.app/molham-homsi/issue/MOL-900)

---

## Contract

`_refresh_pass` must not bind one scrape account for the whole measure loop. On the live path (`scrape_client is None`), for each user in `scrape_users(cfg)` order that has a session file, is not `scrape_user_blocked`, and has remaining UTC day room under `_SCRAPE_DAY_BUDGET`: `open_client(cfg, user=U, now=now)` (no `allow_reauth`); run the existing resolve/measure loop on the existing due-queue cursor until `user_tried` hits `min(_scrape_try_cap(), room)`, the queue is exhausted, or that user hits throttle / login_required; charge that user with `used_delta=user_tried` via existing `_persist_cooldown` / `_clear_cooldown`; on that user’s stop, continue with the next qualifying user on the same cursor. Do not `break`/`return` the whole pass while another qualifying user remains.

Leave alone: due-queue construction; injected `scrape_client` path; `_pick_healthy_scrape_user` for `_read_active_cooldown` and `open_client(user=None)`.

No new functions. No round-robin. No Graph. No parallel clients.

---

## Failure (traced)

Today `_refresh_pass`: one `_pick_healthy_scrape_user`, one `open_client`, one `while` on that `client`, one end charge with `used_delta=tried`. Peer `accounts[user].used` never moves while the head stays healthy. `stop` → `break` ends the pass for everyone.

---

## Cap

`room` = `_SCRAPE_DAY_BUDGET - used` when `accounts[U].day` is today, else `_SCRAPE_DAY_BUDGET` (`_account_rec`; missing used → 0).  
`user_cap` = `min(_scrape_try_cap(), room)`.

So each qualifying user may spend up to one try_cap worth of attempts in this pass, bounded by that user’s day budget — not one shared try_cap for the whole roster.

---

## SuccessRate: 100%

CI must prove: two qualifying users + enough due tags → one `_refresh_pass` opens both, both `accounts[u].used` increase, head throttle/checkpoint still lets the peer continue on the same cursor; injected client does not walk users; one listed user matches today’s single-user spend ∩ room.

---

## After APPROVE

1. Edit `_refresh_pass` in `src/fanops/fanops_hashtags.py` only (live path).  
2. Add the CI tests above in `tests/test_fanops_hashtags.py`.  
3. Align `docs/CONFIG.md` and `docs/CODEMAPS/hashtag-lifecycle.md` with the contract (list order = who measures first; try_cap applies per qualifying user per pass ∩ day budget).  
4. `ci/mol-900-…` → `./scripts/check.sh` → PR → CI. No launchctl restart.
