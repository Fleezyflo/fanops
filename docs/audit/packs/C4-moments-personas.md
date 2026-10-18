# Pack: C4 — moments, casting & personas

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `persona_directives.persona_facts`, `moments._hook_script_mismatch`, `actions_edit.regenerate_hook`. `prompts.py` was not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C4-F01 | low | swallow | closed on origin/main | `persona_facts` returns `persona_terms` and does not catch | `persona_directives.persona_facts` returns `persona_terms(p, cfg)` with no try/except |
| C4-F02 | low | wrong-output | closed on origin/main | `archetype_crosswalk_rows` keeps `content_focus` as one free-text item; `list(str)` is gone | `lever_docs.archetype_crosswalk_rows` |
| C4-F03 | medium | wrong-output | closed on origin/main | a non-Arabic hook is kept when the source language is outside `{en, ar}`; `en`/`ar` mismatches still null | `moments._hook_script_mismatch` |
| C4-F04 | low | wrong-output | closed on origin/main | `regenerate_hook` uses `_hook_script_mismatch`. A Latin hook on a non-en/ar source is kept | `actions_edit.regenerate_hook` calls `moments._hook_script_mismatch` and returns before `reburn_hook` on a mismatch |

## Evidence commands run

```
git rev-parse HEAD
git show HEAD:src/fanops/persona_directives.py | sed -n '294,300p'
git show HEAD:src/fanops/moments.py | sed -n '323,406p;517,556p;623,793p'
git show HEAD:src/fanops/casting.py
git show HEAD:src/fanops/persona_research.py
git show HEAD:src/fanops/lever_docs.py | sed -n '57,80p'
git grep -n 'apply_auto_corpus(' HEAD -- src
rg -n 'archetype_crosswalk|content_focus as' src/fanops/CLAUDE.md src/fanops
PYTHONPATH=src python -c 'from tests.test_lever_docs import test_crosswalk_content_focus_keeps_free_text_intact; test_crosswalk_content_focus_keeps_free_text_intact()'
```

No pytest.

## Full checklist 2026-10-17

Subject `d2cded31`. Product reads are `git show HEAD:path`. Working-tree diffs on `moments.py` (comment only) and `persona_directives.py` (`hook_author_slot` no longer returns `hook_directive`) were left in place.

### Moment ingest

`request_moments` writes one `moments` gate per targeted active account when `cfg.account_casting` is on (`{source_id}.{handle}`), otherwise one bare source gate. `FANOPS_QUEUE_GATE` (default on) parks a non-operator call on `src.meta["pending_reopen"]` and returns without `write_request`. `ingest_moments` waits until every dotted gate has a `MomentDecision`; a missing response returns the ledger unchanged (`SourceState.moments_requested`). Invalid picks on the bare gate set `SourceState.error` with the reason. All-empty sets `moments_empty` and logs `zero_moments`. Valid picks go through `_reconcile_valid_picks` → `reconcile_moments` + `picks_decided`. A speech-trust miss logs `pick_speech_mismatch` and does not reject the pick. Cue edges, when both sets exist, reject a pick that is not a cue start/end.

### Hook pass

`source_needs_hook_pass` is true for `picks_decided`, a disk `hook=None` answer on an unhooked decided/clipped moment, or `hook_frames_unread`. `request_moment_hooks` reopens those, then writes one `moment_hooks` gate per `picked` moment (write-once). No trusted excerpt and `source_needs_asr_retry` defers that moment (`hook_deferred_asr_retry`) instead of writing `hook=None`. `ingest_moment_hooks` waits for every pick, then promotes all to `decided` and the source to `moments_decided`. Weak, brand-risk, and Arabic/Latin mismatch hooks are stored on `hook_removed` and ship with `hook=None`. `accounts` on `ingest_moment_hooks` is unused; affinities are not rewritten here. `clip_profile` on `MomentHookRequest` is the global profile; `models.MomentRequest` calls that field leftover and `prompts.py` on HEAD does not read it.

### Persona facts

HEAD `persona_facts` returns `length_band=""`, derived `framing`, `lead_tags=[]`, and `terms` from `persona_terms` (`niche` bodies only). It does not catch. `cfg` on `persona_terms` is unread. C4-F01 stays closed. `docs/CODEMAPS/anomalies.md` records that return. The old `except Exception: store = None` sentence is gone.

### Data writes

Persona and account mutators take `control_file_txn` and `write_json_atomic`. `add_persona` / `update_persona` / `ensure_baked_personas` persist `hashtag_corpus: []`. `apply_auto_corpus` still replaces `hashtag_corpus`, and `git grep apply_auto_corpus HEAD -- src` hits only its definition. Tests still call it. It was not deleted. `src/fanops/CLAUDE.md` alias rule was read first; no symbol was removed. Ledger writes on this path are moment reconcile, source/moment state, batch mint (`create_batch` / `resolve_or_mint_drop_batch`), and `pending_reopen` inside the caller's transaction. `lever_docs.write_docs` rewrites `docs/LEVERS.md` and `docs/LEVER-THRESHOLDS.md` only from `cmd_lever_docs`.

### External calls

No HTTP client in these modules. Frame stills are `keyframes.extract_keyframes` (one ffmpeg per still, 30s). Missing ffmpeg, timeout, or no source file returns `[]`. The hook window logs `hook_window_frames_empty`. Agent gates are local JSON under `cfg.agent_io`. `read_response` logs `corrupt_response` / `invalid_response` and returns None, which ingest treats as not-yet.

### Env

Read through `Config`, not raw `getenv`, in these modules. `FANOPS_QUEUE_GATE` default on. `FANOPS_ACCOUNT_CASTING` default on (`casting.affinity_admits` admits everyone when off). `FANOPS_ISOLATE_VOCALS` default on gates the ASR retry. `FANOPS_CLIP_PROFILE` is the global leftover on the hook payload; per-account length is the picked window (`_stamp_owner_spec` stores `clip_profile=None`). `FANOPS_CORPUS_TARGET` is `config.corpus_target` and is not read by a live C4 writer. `_CORPUS_CAP = 80` applies only inside `apply_auto_corpus`.

### Launch-critical path

`pipeline._stage_source_to_moments` → `request_moments` (from `signalled`) → `_stage_ingest_moments` → `_stage_moment_hooks` → `_stage_render_and_caption` on `MomentState.decided`. A raise in those stages quarantines the source or moment to `error`. An unanswered gate does not. Casting off is the bare source gate and fan-to-all admit. Third-party sources are skipped before the moment request.

C4-F02 was the one wrong output on this pass. HEAD `archetype_crosswalk_rows` did `list(p.content_focus)`, so `docs/LEVERS.md` and `studio/views.py` `_lever_detail_rows` (`", ".join`) showed the free-text focus as characters (`| m, o, m, e, n, t, s,`). The row now holds one stripped string. Both joins render the sentence. `test_crosswalk_content_focus_keeps_free_text_intact` failed on that character cell before the edit and passed after. `docs/LEVERS.md` was regenerated from `render_levers`. No launch blocker on the pick → hook → decided path.

### Lane

`casting.py`, `moments.py`, and `prompts.py` were not edited. No defect on those files required a skipped patch.

## Wave 1 evidence (dirty tree)

Subject working tree at `d2cded31`. Product reads are the dirty files. This pass edited `moments.py`. The lane note above is the prior HEAD pass. No pytest.

### Moment ingest

`request_moments` / `ingest_moments` / `_ingest_moments_dotted` match the checklist above on this tree. A missing dotted response still returns before reconcile. Cue-edge membership (`round` to 3 decimals vs the printed cue) had 0 mismatches across 60,000 millisecond starts. Speech-trust miss still logs `pick_speech_mismatch` and does not reject (`test_validate_pick_logs_pick_speech_mismatch`).

### Hook pass

C4-F03. `ingest_moment_hooks` compared `_hook_lang_base` (`ar`, or `en` for every other script) to the whisper base code with `!=`. A Latin hook on `fr` / `es` / `zh` / `ja` was stored on `hook_removed` and shipped `hook=None`. Probe before the edit: `fr` + `the part you will replay` → strip. `_hook_script_mismatch` keeps a non-Arabic hook when the source code is outside `{en, ar}`. `en`/`ar` mismatches still null. An Arabic-script hook on any other code still nulls. `test_non_en_ar_source_keeps_latin_hook` and `test_non_arabic_source_strips_arabic_script_hook` cover that. The four existing language-gate tests still pass when called directly.

C4-F04 is closed. `actions_edit.regenerate_hook` uses `moments._hook_script_mismatch`. A Latin hook on a source outside `{en, ar}` is kept. An Arabic-script hook on any other code is still rejected. `tests/test_studio_regenerate_hook.py` covers both.

`hook_author_slot` still returns voice only (dirty tree, `test_hook_author_slot_excludes_hook_angle`). `mechanism_lean` still rides the hook payload and `moment_hook_prompt` still drops it (`test_hook_prompt_reads_owner_voice_not_mechanism_lean_or_demos`). That is the locked contract, not a new defect.

### Persona facts

`persona_facts` is unchanged versus the checklist. It returns `length_band=""`, `resolved_cut_spec` framing, `lead_tags=[]`, and `persona_terms` (`niche` only). It does not catch. C4-F01 stays closed.

### prompts.py (not edited)

C5 recorded this as not a defect. Both frame lines say “SEE them.” `tests/test_prompts.py` `test_hook_prompt_with_frames_keeps_stills_claim` still requires the stills claim when frames are attached. There is no open C5 prompt finding.

### Commands

```
git rev-parse HEAD
git diff -- src/fanops/moments.py src/fanops/persona_directives.py src/fanops/lever_docs.py src/fanops/prompts.py
PYTHONPATH=src python -   # cue round-trip over 60000 ms starts: 0 mismatches; fr+Latin strip=True before the fix
PYTHONPATH=src python -   # tests.test_hook_language_gate called with a tempfile Path, not pytest: all ok
```

No pytest.
