# Pack: C5 — caption, hooks & hashtags

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C5-F01 | low | design-note | closed on this tree | `vet_hashtags` is absent. `ship_from_lock` is picks ∩ lock, cap n, no floors, no backfill | `hashtags.ship_from_lock` |
| C5-F02 | high | stall | fixed on this working tree | A pass of only exact misses writes `exact_miss` and `remaining`. The next tick searches the rest of the catalog and does not re-research. | `source_tags_scrape.ensure_source_lock` |

## Evidence commands run

```
rg 'vet_hashtags' src/fanops/hashtags.py
tests/test_hashtags.py tests/test_fanops_hashtags.py
```

## Full checklist 2026-10-17

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Product code was read with `git show HEAD:path`. Unrelated dirty worktree edits were not reverted and were not treated as the subject. The HEAD pass filed no further id. C5-F01 stays closed. Wave 1 below filed C5-F02 on the dirty tree.

### Ship path (lock → `posted_text_for`)

`ship_from_lock` (`hashtags.py`) is picks ∩ lock, pick order, cap `n` (default 4). Empty lock yields `[]`. No floors, no backfill. `vet_hashtags` is absent.

`request_captions` stays closed until `_source_lock_completed` (`researched_at` set). A completed empty lock opens the gate and omits `hashtag_store`. The menu on each surface is `_source_lock_tags`, not the pile.

`ingest_captions` stores `ship_from_lock(picks, lock)` on `meta_captions[surface].hashtags` and the stripped model sentence on `caption`. Crosspost copies that pair onto `Post`. `posted_text_for` walks post → clip → moment → source, calls `ship_from_lock(post.hashtags, lock, n=4)`, then `compose_posted_caption`. A non-empty intersection strips `#` tokens from the sentence and appends at most four tags. An empty ship result returns the stored sentence with hashes kept. YouTube on Postiz sends `post.caption` raw and does not call `posted_text_for`; Zernio and dry-run call `posted_text_for` (`post/CLAUDE.md`).

HEAD skill `.claude/skills/fanops-hook-hashtag/SKILL.md` matches that contract: cap 4, empty lock keeps stored hashes, no AR floor, no mega slot, no backfill. `prompts._hook_spec` contains “keep the viewer watching”. Caption language is declared in `prompts_caption.caption_prompt` and held in `ingest_captions` when both base subtags are known and differ. An undeclared `item.language` is the documented residual, not a silent ship of the wrong language.

### Source-lock scrape errors vs `play_count`

`ensure_source_lock` admits with `lock_from_shortlist`: positive `play_count` only, caller order, cap `_LOCK_N` (12). `like_count` and `graph_metric` do not admit. `_apply_scrape_metrics` runs only after `measure_and_harvest_scrape` returns. An exception does not write a number.

A generic search or measure error (`search_fail` / `measure_fail`) sets `retryable_fail`, does not pop the name, and `_write_in_progress` keeps `remaining` so a later tick can store `play_count`. That is the #1303 behavior (`aa593d1f`, `c6d5773a`), covered by `test_search_runtime_error_leaves_name_for_later_play_count` and `test_measure_runtime_error_keeps_name_pending`.

`LoginRequired` and `ScrapeUnavailable` take `stop_dead`, do not set `retryable_fail`, and do not write the sidecar when nothing is verified (`test_ensure_source_lock_login_required_stops_the_tick`, `test_ensure_source_lock_scrape_unavailable_stops_the_tick`). They do not invent `play_count`. A real empty search page is not an error: the name is popped and stays off the lock.

### Data writes

| Write | Path | Posture |
|---|---|---|
| Source lock | `00_control/source_tag_locks.json` via `write_json_atomic` | Stamp sets `researched_at`. In-progress keeps `remaining` and does not set it. `hydrate` writes `hydrated_at` and must not open the caption gate. |
| Graph cache | `graph_hashtag_cache.json` | Separate from scrape meters. Quota is a file read. Graph death still stamps. |
| Measurement cache | `hashtags.json` under `hashtags.lock` (`fcntl` lease) | Error status does not insert a row. `load_measurements` drops a record with no evidence. Corrupt file → `{}` and a log. |
| Caption request | agent I/O `captions` request | Not written until the lock row is completed. |
| Ledger captions | `meta_captions` on the clip; posts at crosspost / `recaption._sync_posts` | Recaption skips queued posts inside the imminent window. |
| Recaption journal | `.recaption_progress.json` | Torn journal logs `journal_unreadable` and starts empty. |

`load_source_tag_locks` uses `fail_open` and returns `{}` on a corrupt read. That is the house logged fail-open. A corrupt file is already unreadable as a table; this pass did not find a path that stores an exception as `play_count`.

### External calls

- Caption and shortlist LLM: `claude_json` / `claude_json_meta` (`claude -p` or `cursor-agent` per `FANOPS_LLM_TRANSPORT`). Missing binary raises `ToolchainMissingError`. Frames unread raise `LlmFramesUnreadError`. Schema failure raises `LlmSchemaError`. No `ANTHROPIC_API_KEY`.
- Lock walk and Layer A: `ig_hashtag_scrape.open_client` (instagrapi). HEAD `source_tags_walk._iter_lock_clients` defaults to `open_client`. `open_web_session` is the Safari duck-type and is not the default opener (`test_lock_default_opener_is_open_client`).
- Safari XHR: `ig_safari_shell.safari_xhr` / `safari_fetch`. One tick slot (`lock` or `remesure`).
- Graph confirm: `meta_graph.resolve_hashtag` / `measure_and_harvest`. `GraphQuotaExhausted`, `GraphThrottled`, `GraphRefused`, and `GraphUnreachable` log and break. They do not withhold a finished scrape.
- Publish wire (callers, not this module’s files): Postiz and Zernio HTTP. C5 does not edit `post/zernio.py`, `post/postiz.py`, or `reconcile.py`.

### Env

| Name | Effect on this module |
|---|---|
| `FANOPS_IG_SCRAPE_USER` | Comma-separated scrape identities. Unset → `ScrapeUnavailable` / tick `no_scrape`. |
| `FANOPS_IG_SCRAPE_PASSWORD` and `FANOPS_IG_SCRAPE_PASSWORD_<USER>` | Login only. Not logged. |
| `FANOPS_HASHTAG_SCRAPE_DELAY` | instagrapi `delay_range`. Malformed value logs and keeps the default. |
| `FANOPS_HASHTAG_SCRAPE_TRY_CAP`, `_COTAG_ENQUEUE`, `_PARALLEL` | Pass caps. Parallel is read and unused; fetch stays sequential. |
| `FANOPS_LOCK_TAGS_PER_PASS` | `cfg.lock_tags_per_pass`. Uninjected walk stops after that many tags. |
| `FANOPS_LLM_TRANSPORT`, `FANOPS_LLM_MODEL` | One CLI. No silent cross-transport fallback. |
| `FANOPS_VARIANT_LEARNING`, `FANOPS_VARIANT_UCB`, `FANOPS_VARIANT_TRANSFER` | Caption-request hints only. A raising scorer logs and returns `[]`. It does not hold the clip. |
| `META_GRAPH_TOKEN`, Meta IG user id | Graph attach. Absent → scrape still stamps. |

### Launch-critical caption path

Incomplete lock: clip stays `rendered`, no request file. Response missing: `ingest_captions` returns the ledger unchanged. Declared language mismatch, brand-risk, tags-only, unknown surface, or a missing requested surface: clip goes `held` with a reason. Off-lock picks die at `ship_from_lock` before they are stored. Send-time `posted_text_for` intersects again. Empty lock does not replace a stored hashtag line. Learning and transfer failures do not block the request.

No second grid of caption code was found that still calls `vet_hashtags` or backfills from the pile, the corpus, or a size band. `src/fanops/CLAUDE.md` names `posted_text_for` vs `Post.caption` as the vendor-wire alias; nothing in this pass was deleted.

### Evidence commands

```
git rev-parse HEAD
git grep -n 'def ship_from_lock' HEAD -- src/fanops/hashtags.py
git grep -n vet_hashtags HEAD -- src/fanops/hashtags.py
git show HEAD:src/fanops/hashtags.py
git show HEAD:src/fanops/caption_compose.py
git show HEAD:src/fanops/caption.py
git show HEAD:src/fanops/source_tags_scrape.py
git show HEAD:.claude/skills/fanops-hook-hashtag/SKILL.md
git grep -n 'retryable_fail\|search_fail\|measure_fail\|stop_dead' HEAD -- src/fanops/source_tags_scrape.py
git grep -n posted_text_for HEAD -- src/fanops/post/postiz.py src/fanops/post/zernio.py src/fanops/post/dryrun.py
git show HEAD:src/fanops/prompts.py
```

## Wave 1 evidence (dirty tree)

Subject working tree at `d2cded31`. Product reads are the dirty files. This pass edited `source_tags_scrape.py` and `tests/test_source_tags.py`. `prompts.py` was not edited. No pytest. No symbol was deleted. `ensure_source_lock` is imported by `source_tags.py` and called from `source_tags_walk.lock_ready_sources`.

### Frame-grounding verdict

Not a defect. The skill (Part 1) requires the hook author to see the clip's frames and write one short line (`keep the viewer watching`). It does not require the sentence "write the hook true to what is on screen."

Dirty `moment_hook_prompt` still says "SEE them" on both frame lines when frames are attached (`prompts.py`). The no-frames path still forbids claiming the screen. `_hook_spec` still contains "keep the viewer watching". The Claude and cursor first-pass prefixes say to read each frame and return JSON. The Claude re-ask prefix still says to ground the answer in the frames (`llm.py`). That is the skill's see-the-frames requirement. No C5 id for it.

### C5-F02

An unattended pass (`FANOPS_LOCK_TAGS_PER_PASS=2`, no injected client) whose first tags were exact misses returned `scrape_unfinished` and wrote no sidecar. `verified` was empty and `retryable_fail` was false, so the `exact_miss` write never ran. The next tick called research again and searched `missa` / `missb` again. `hitc` / `hitd` never started. Probe before the edit: sidecar absent, `research` count 2, second search list `['missa', 'missb']`.

The unfinished write now runs when `misses` is non-empty. The same probe after the edit, as `test_unattended_exact_miss_pass_persists_and_advances`, stores `exact_miss` `#missa` `#missb` and `remaining` `#hitc` `#hitd`, then the next tick searches only `hitc` and `hitd` and does not re-research. Called directly with a tempfile `Path`. No pytest.

### Rest of the dirty caption surface

C5-F01 stays closed. `rg vet_hashtags` on `hashtags.py`, `caption.py`, `caption_ingest.py`, and `prompts_caption.py` found no matches. `ship_from_lock` is still picks ∩ lock, cap, then artist-norm drop, then `play_rank_key`. No floors, no backfill.

`caption.ingest_captions` still holds a declared language mismatch. `request_captions` attaches this window's frames and does not copy `moment.hook`. `prompts_caption.caption_prompt` tells the model the choose-key is `play_count`, the tie is `current_top_reel_play_max_7d`, and menu position is not a reason. `hookcheck.py` is a docstring change; `is_weak_hook` is still empty / exact-dup / opening-cluster only. `recaption.run_recaption` still drives `request_captions` / `ingest_captions`. `states` defaults to awaiting-approval and queued. `source_tags_walk._iter_lock_clients` defaults to `open_web_session`, which matches the skill's Safari lock walk. `IgWebSession` duck-types `search_hashtags` / `hashtag_medias_top`. `ig_hashtag_scrape.py` is not dirty. `source_tags.py` is not dirty.

### Commands

```
git rev-parse HEAD
git status --short --branch
git diff HEAD -- src/fanops/prompts.py .claude/skills/fanops-hook-hashtag/SKILL.md
git diff HEAD -- src/fanops/hashtags.py src/fanops/hookcheck.py src/fanops/caption_ingest.py src/fanops/source_tags_walk.py src/fanops/source_tags_shortlist.py src/fanops/source_tags_sidecar.py
git diff HEAD -- src/fanops/caption.py src/fanops/recaption.py src/fanops/source_tags_scrape.py
rg -n 'vet_hashtags|write the hook true|SEE them' src/fanops/hashtags.py src/fanops/prompts.py src/fanops/llm.py
rg -n 'vet_hashtags' src/fanops/hashtags.py src/fanops/caption.py src/fanops/caption_ingest.py src/fanops/prompts_caption.py
rg -n 'ensure_source_lock' src/fanops --glob '*.py'
PYTHONPATH=src python -   # miss-only pass: sidecar absent; second tick research=2 searches ['missa','missb']
PYTHONPATH=src:tests python -   # test_unattended_exact_miss_pass_persists_and_advances: KeyError src_1 before the fix; PASS after
```

No pytest.
