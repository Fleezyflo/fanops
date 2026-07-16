# R4 migration record — hashtag corpus governance (2026-07-16)

Durable record of a **live-data migration**. The diagnosis lived only in gitignored `.reports/`; the
architecture is ADR-0104, and this is the operational half: what ran, what changed on disk, and how to undo
it. Kept in `docs/` deliberately — the machine that holds `.reports/` is not the machine that needs this.

## What was wrong

`fanops_hashtags._seed_tags` built the discovery store out of every persona's `hashtag_corpus`;
`persona_research.research_corpus` proposed from `vetted_menu(load_store(cfg))` — the store, re-ranked — and
`refresh_persona_corpus` wrote those proposals back as `auto` corpus entries on a daemon tick.
**corpus → store → corpus**, closed, with no external evidence in it and nothing in the data to show it. The
live store was byte-identical to `seeds + frozen floor`: 53 tags, **0 discovered**, `reach: {}` — while every
proposal it made was presented as research.

What reached production, on a **Syrian rapper's interview catalogue** (derived from the ledger: 347
transcripts give music 94, money 65, arab 30, artists 27, timeless 19, syria 16): `#taylorswift`, `#80s`,
`#instagood`, `#love`, `#explore`, a malformed `#fypppp…` (73 `p`s), and the **entire Wu-Tang Clan** —
a *different artist* — on 93% of two handles' posts.

## PRs

| PR | SHA | What |
|---|---|---|
| [#679](https://github.com/Fleezyflo/fanops/pull/679) | `01484fd` | selector: clip reaches its own line (H1/H2), reach accrues (H3) |
| [#681](https://github.com/Fleezyflo/fanops/pull/681) | `ba17c5d` | **R4**: corpus/store/candidates separated; the circularity cut; hygiene; migration |
| [#687](https://github.com/Fleezyflo/fanops/pull/687) | `cb3df5f` | only personas linked to an **active account** seed the store |
| [#688](https://github.com/Fleezyflo/fanops/pull/688) | `6186431` | keeper could never adopt new code — `etimes` is not a BSD ps keyword |
| [#689](https://github.com/Fleezyflo/fanops/pull/689) | `073a37e` | storm guard must outlast a pass, not the keeper's own tick |
| [#690](https://github.com/Fleezyflo/fanops/pull/690) | `caa3427` | this record |

## Terminal state (frozen 2026-07-16 — R4 is closed)

| | |
|---|---|
| `main` | **`caa3427`**, CI `success`, architecture `success`, arch gate 0 stale / 0 blocking |
| PRs | **six**, all merged normally — no admin bypass |
| Live pump | **one** instance, on **`caa3427`** by its own heartbeat |
| Curated corpora | **22** tags across 8 personas, all `pinned`, `reach: null` |
| Discovery store | 18 tags, **`reach: {}`** |
| Rollback snapshot | `personas.json.r4-bak-20260716T130424Z`, **5369 bytes**, intact |
| Working tree | clean, except the operator's known-untracked `docs/constitution/` work |

## Live files changed

Root `/Users/molhamhomsi/FanOps` (confirmed by the `com.fanops.run` plist `WorkingDirectory` — not inferred).

| File | Before | After |
|---|---|---|
| `00_control/personas.json` | 56 corpus tags / 8 personas | **22** tags / 8 personas, all `pinned`, `reach: null` |
| `00_control/hashtags.json` | 53 tags, `reach: {}` | **18** tags, `reach: {}` |
| `00_control/hashtag_budget.json` | 30 queries | **unchanged** — the rebuild spent no budget |
| `00_control/accounts.json` | — | **untouched** |
| `ledger.sqlite` | — | **untouched** — no post was rewritten |

Corpora, before → after:

```
craft-curator      #lyrics #bars #newmusic #80s #spotify #taylorswift #artist #instagood
                   #songs #explore #explorepage #missviralchallenge          ->  #bars #lyrics #hiphopmusic
underground-zine   #freestyle #undergroundhiphop #trap #methodman #wuwear #90shiphop #rza
                   #wutang #ghostfacekillah #wutangclan #cappadonna #wutangbrand
                                                                             ->  #undergroundhiphop #freestyle #rap
burner-bold        #viral #rapmusic #hiphop #trending #post #fypppp…(73 p's) #explore #love
                   #explorepage #instagood #art #highlights                  ->  #hiphop #rapmusic #rapper
credibility-first  #podcast #interview #facts #science                       ->  #podcast #interview
controversy        #celebritygossip #drama #popculture #entertainment        ->  #hiphop #rap
edutainment        #hiphop #lyrics #music #newmusic                          ->  #hiphop #lyrics #newmusic
cliffhanger        #podcast #storytime #reels #viral                         ->  #podcast #storytime
hype-vibe          #hiphop #rap #bars #undergroundhiphop                     ->  (already valid; unchanged)
```

Corpora are **small on purpose**. Post-#679 the corpus holds 2 slots of brand identity
(`_CORPUS_LEAD_MAX`) while the clip's own vetted picks hold the other 2. Padding to a quota would re-crowd
the clip out of its own line.

## Rollback

```sh
cp 00_control/personas.json.r4-bak-20260716T130424Z 00_control/personas.json   # exact 5369-byte pre-image
fanops hashtags refresh                                                         # store rebuilds from those seeds
```

The snapshot is taken **before any byte moves**, always. `hashtags.json` has no snapshot because it is a pure
function of the corpora + frozen floor — restoring personas.json and refreshing reproduces it exactly.

## Idempotency

`fanops hashtags migrate --apply` run three times: 7 changes → **0 changes** → `personas.json` byte-identical
(`302f0d27defff4e5` both times). It converges on a declared target; it is not a state machine.

## Proof (347 live posts, replaying the REAL recorded model picks from `meta_captions.hashtags_raw`)

| Check | Result |
|---|---|
| off-catalogue (Wu-Tang / Taylor Swift) shipped | **NONE** |
| malformed / generic-engagement shipped | **NONE** |
| discovery-floor tags | `#fyp #reels #viral` — **by design**, one per platform |
| curated identity on every line | **YES**, all 3 posting personas |
| clip-derived tags reaching output | `#arabicmusic #trap` (+ discovery floor) |
| Arabic/regional floor (10 `ar` surfaces) | **HOLDS** |
| zero-budget refresh preserves evidence | **YES** (#679 H3) |
| store→corpus automatic echo | **impossible** — proposal requires `source == "graph-reach"` + unexpired |

## Daemon

Stopped with `fanops daemon stop` (boots the **keeper first**, so it cannot re-bootstrap the pump), confirmed
by `launchctl list` + PID 35278 gone + `.run.lock` PID dead. Never forced; no `kill -9`. Restarted with
`fanops daemon install --interval 600` — the repository's own mechanism, not an improvised command. Post-restart:
**pid 59299, on `073a37e`** (the pump's own heartbeat reports the SHA), one instance, keeper loaded,
`alive | passes completing`, lock owned by 59299, first tick logged `corpora_refresh_skipped` and left the
corpora byte-identical.

`FANOPS_RESPONDER=llm` was **already** the operator's `.env` setting; `daemon install` read it and disclosed
the recurring cost. It was not changed.

### The keeper's adopt, proven in production

Then #690 landed and the pump **adopted it by itself** — the first time that path has ever worked. From the
live `run.log` (UTC; the machine is UTC+4):

```
14:00:58  heartbeat  code=073a37e…   pid 59299
14:11:00  heartbeat  code=073a37e…   pid 59299      three clean 600s ticks
14:21:01  heartbeat  code=073a37e…   pid 59299
          ── caa3427 merged; disk SHA now differs from the heartbeat ──
14:23:03  heartbeat  code=caa3427…   pid 66174      ONE kickstart, and the new pump reports the new SHA
```

Then **held pid 66174 past four keeper cycles** with no further kickstart. That is the shape the design wants
and the shape #689 exists to produce: **one adopt per merge, then settle.** Before #688 this path had been
inert through 18 merges (§ *Two defects*, below); the fix is therefore validated on live data, not merely
merged. Trust it — do not restart by hand to force adoption.

The adopted pump's own first tick logged `corpora_refresh_skipped reason=fresh` and left the corpora
byte-identical, so the *terminal* SHA — not just the one restarted onto — is proven not to refill curated data.

## Two defects found by DOING this, not by reading

1. **The keeper could never adopt new code** (#688). `_pump_pid_age_s` asked `ps -o etimes=`; `etimes` is a
   GNU/procps keyword that does not exist in BSD ps. macOS printed to stderr, exited **0**, left stdout empty
   → `age` was **always** None → the storm guard's `age is None → skip` fired every time. Permanently inert,
   not delayed. This is the mechanical cause of "a merged fix never reaches the running daemon": the pump sat
   on a day-old SHA through 18 merges.
2. **The storm guard was the storm** (#689). It skipped while `age < KEEPER_POLL_INTERVAL_S` (120s) — but the
   keeper *fires* every 120s, so age is ≥120 at the next fire and a kickstart went through every cycle, while
   the pump needs a 600s pass to report its new SHA. Fixing (1) removed the mask and it stormed immediately
   (pids 49425→51695→52493→52886→53266 in ~8 min, `last_exit -15`). Now `settle = interval + one keeper tick`.

## Residuals — ACCEPTED, not unfinished R4 work

These were measured, recorded, and **accepted by the operator as closed-out residue**. R4 is frozen; none of
these reopens it. Residual 1 is handed to a separate program, briefed and not started.

1. **The model repeats itself.** Replaying its real picks: only **6–15 distinct pick-sets per handle over
   66–76 surfaces, 54–76% on one set**. Now the *dominant* cause of near-identical lines, upstream of every
   change here. Partly caused by the polluted corpus (the prompt tells the model to *prefer* the corpus), so
   it should improve on clean corpora — **unproven until captions regenerate**. Handed off to
   [`design/briefs/17-hashtag-model-diversity.md`](../design/briefs/17-hashtag-model-diversity.md), which is a
   **brief only**: it gates all work on regenerating captions against the clean menu first, and it may not
   touch corpus hygiene, reach persistence, or daemon adoption. Note the trap it records — a whole-line
   diversity metric is maximised by **deleting the curated lead**, i.e. by undoing R4.
2. Dormant personas' `intake.genre` is still catalogue-wrong (`science`, `gossip`). They cannot reach the
   store any more (#687), but would drive the wrong niche floor **if activated**. Persona configuration, not
   architecture.
3. The 12h refresh vs the 7-day budget window is harmless now (evidence accrues) but still wasteful.
4. **No measured evidence survived.** The store was `reach: {}` at migration time — the 30 measurements bought
   2026-07-12 were already destroyed by the pre-#679 overwrite. The migration preserved nothing because
   nothing was left, and invented no substitute. Re-measurement is impossible until the budget rolls
   (~2026-07-19); until then `research_corpus` correctly proposes nothing.
