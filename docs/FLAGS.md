# FanOps improvement-flag registry

Per-account differentiation and closed-loop hashtag discovery ship **ON by default** — they are the system's
purpose, not opt-ins. Each keeps a **legacy OFF code path** (an operator escape hatch) guarded by an env var and
pinned by a **firewall test** that proves the OFF behavior stays correct. This page is the single source of truth
for what each flag does, how to disable it, and which test guards its OFF contract.

> **Why this doc exists** (remediation-loop #15): the OFF paths are NOT parallel-code duplication — each flag
> gates *entry* to a feature with an early return, and each has a dedicated firewall test. There was no code to
> consolidate; the real gap was a registry so an operator/maintainer knows every escape hatch and why its
> firewall test exists. The flags' env-var leakage is also neutralized per-test by `tests/conftest.py`
> (`_LEAKY_ENV`) so a value in the operator's repo `.env` never silently flips a unit test.

## Default-ON flags (per-account differentiation + discovery)

| Flag | Env var (OFF) | Default | What ON does | What OFF restores |
|---|---|---|---|---|
| `creative_variation` | `FANOPS_CREATIVE_VARIATION=0` | **ON** | each active account gets its own caption + burned-in on-screen hook per clip (+ its own length/framing cut under M2) | legacy fan-to-all **single shared clip + moment hook**; the Review approve-with-hook restore flow becomes available (it's an OFF-mode feature) |
| `account_casting` | `FANOPS_ACCOUNT_CASTING=0` | **ON** | each active account is cast its **own LLM-selected moments** (RF1 `AccountSelection`); crosspost fans a cast moment **only** to its accounts | legacy fan-to-all — every moment reaches every account; no per-account selection gate |
| `hashtag_trends` | `FANOPS_HASHTAG_TRENDS=0` | **ON** | `hashtags refresh` builds the store from **live Meta Graph** reach (harvest→measure→rank) | frozen reach floor only, no Graph harvest/measure (also the automatic behavior when `META_GRAPH_TOKEN`/`META_IG_USER_ID` are absent — fail-open) |
| `corpus_auto` | `FANOPS_CORPUS_AUTO=0` | **ON** | `fanops run` auto-refreshes each persona's hashtag corpus on a 12h throttle (Graph discovery when creds+budget allow; offline store re-rank when under target without creds) | no automatic corpus writes — operator curation only |

Disable semantics are uniform: the env var disables the flag **only** on the explicit off-words `0`/`false`/`no`/`off`;
unset, empty, or anything else → **ON**.

### `creative_variation`
- **Code:** [config.py:455](../src/fanops/config.py) (`def creative_variation`). Read at the per-account hook/caption/cut sites in `crosspost.py` and `clip.py`.
- **OFF contract:** no per-account `Render` is minted; surfaces carry no provenance/cause chips; render/post media falls back to the shared clip + moment hook.
- **Firewall tests:** `test_cv_off_mints_no_renders` ([test_render_mint.py:87](../tests/test_render_mint.py)), `test_off_firewall_no_cause_chips` ([test_studio_review_legibility.py:130](../tests/test_studio_review_legibility.py)), `test_off_firewall_mints_no_render` ([test_shipped_provenance.py:106](../tests/test_shipped_provenance.py)).
- **Note:** ON vs OFF produce intentionally **different media** (per-account vs shared) — that *is* the feature. The OFF path is the legacy single-copy behavior, not a byte-identical no-op.

### `account_casting`
- **Code:** [config.py:467](../src/fanops/config.py) (`def account_casting`). Gate predicate: `account_selection_admits` in [casting.py](../src/fanops/casting.py) — `if not cfg.account_casting: return True` (admit-all firewall).
- **OFF contract:** the selection gate never discriminates — every (account, moment) is admitted (fan-to-all); the LLM casting request/ingest is inert.
- **Firewall tests:** `test_gate_off_firewall_admits_all` ([test_account_selection.py:244](../tests/test_account_selection.py)), `test_off_firewall_pending_inert_and_fans_all` ([test_casting_application.py:102](../tests/test_casting_application.py)), `test_off_firewall_lanes_still_render_readonly` ([test_review_lanes.py:165](../tests/test_review_lanes.py) — the lanes read-model is config-independent: it still renders, the gate is what flips).
- **Note:** the wired LLM path is **uncapped by design** — there is no per-account moment budget (cost guardrails are a product call, deliberately not imposed).

### `hashtag_trends`
- **Code:** [config.py:255](../src/fanops/config.py) (`def hashtag_trends`). Master switch for the **background** `hashtags refresh` Graph store build only; the on-demand operator lookup (`meta_graph.tag_metrics`) is gated by creds + budget, never this flag.
- **OFF contract:** `refresh_store` writes the frozen reach floor only — no Graph harvest/measure; the `hashtags.json` shape (`{tags, reach}`) is unchanged (`reach` empty). Fail-open: identical to ON when no Meta token is configured.
- **Firewall tests:** `test_hashtag_trends_default_on` + `test_hashtag_trends_explicit_off` ([test_graph_tag_metrics.py:93](../tests/test_graph_tag_metrics.py)).

### `corpus_auto`
- **Code:** [config.py](../src/fanops/config.py) (`def corpus_auto`). Master switch for the **background** persona corpus refresh (`refresh_corpora_if_due` in `persona_research.py`), throttled via `.corpora_refresh.json` mtime (12h).
- **OFF contract:** `refresh_corpora_if_due` returns immediately — `personas.json` is never touched by the auto-refresh path.
- **Firewall tests:** `test_flag_off_byte_identical` ([test_auto_corpus.py](../tests/test_auto_corpus.py)).

## Notable default-OFF flags (opt-in; byte-identical when off)

These are observe-only or experimental; OFF is byte-identical to the pre-feature baseline. Enable only with the
explicit on-words `1`/`true`/`yes`/`on`.

| Flag | Env var (ON) | Default | Purpose |
|---|---|---|---|
| `hook_router` | `FANOPS_HOOK_ROUTER=1` | OFF | read-only Moment hook-strategy classifier (records an annotation, renders nothing) — [config.py:476](../src/fanops/config.py) |
| `variant_learning` | `FANOPS_VARIANT_LEARNING=1` | OFF | the A/B caption-bias learning loop (independent of `creative_variation`) — [config.py](../src/fanops/config.py) |
| `impact_cut` | `FANOPS_IMPACT_CUT=1` | OFF | M4 structural-hooks: the impact-cut PRODUCER (suggest + render operator-approved plans into stitch_draft clips). Needs `hook_router` on; off → no plans, no stitch renders (non-regression) — [config.py:485](../src/fanops/config.py). Firewall: [test_impact_cut.py](../tests/test_impact_cut.py), [test_stitch_render.py](../tests/test_stitch_render.py) |
| `intro_tease` | `FANOPS_INTRO_TEASE=1` | OFF | M6 structural-hooks: the intro-tease PRODUCER (LLM-vision matcher pairs a clean clip with an intro asset, compose-prepends a "wait for it" tease). Needs `hook_router` on + `FANOPS_RESPONDER=llm`; off → no matcher gate, no renders (non-regression) — [config.py:494](../src/fanops/config.py). Firewall: [test_router.py](../tests/test_router.py), [test_intro_match.py](../tests/test_intro_match.py) |

### Validation-frozen actuators (default-OFF AND frozen until `learning_validated`)

A distinct safety class: even with the kill switch ON, the actuator stays inert until `learning_validated`
opens (auto-stamped by the first real non-degraded live metric). `variant_amplify` (re-mines a source) and
`variant_transfer` (injects a cross-surface prior into real captions) are BOTH in this class — acting on a
`lift_score` whose live field-shape is unconfirmed propagates noise. `variant_ucb` is the exception: it only
swaps the caption-bias SCORER on the safe read path, gated by `variant_learning` + the statistical trust gate
(min-posts/min-gap), not `learning_validated`.

> **Investigation-2 B2:** `variant_transfer` previously gated ONLY on its flag — its config docstring promised
> "inert until `learning_validated`" but neither the caption injector nor the digest label enforced it (transfer
> fired on unproven lift). Now `caption._transferred_hooks` AND the digest "borrowing" label both check
> `learning_validated`. The pure scorer `variant_transfer.transferred_hooks` stays validation-agnostic (its
> algorithm tests don't stamp validation); the gate lives at the consumption/injection points, mirroring amplify.

| Flag | Env var (ON) | Default | Frozen-until-validated? | Notes |
|---|---|---|---|---|
| `variant_amplify` | `FANOPS_VARIANT_AMPLIFY=1` | OFF | **YES** (enforced) | re-mines a source on a SUSTAINED variant win; gate at [variant_amplify.py:166](../src/fanops/variant_amplify.py). Firewall: `test_apply_amplify_inert_until_learning_validated` ([test_variant_amplify.py](../tests/test_variant_amplify.py)) |
| `variant_ucb` | `FANOPS_VARIANT_UCB=1` | OFF | NO (scorer swap on the safe read path) | swaps the caption-bias scorer to a UCB1 bandit; gated by `variant_learning` + the statistical trust gate, not `learning_validated` — [config.py:582](../src/fanops/config.py) |
| `variant_transfer` | `FANOPS_VARIANT_TRANSFER=1` | OFF | **YES** (enforced, B2) | injects a cold-start cross-surface prior into real captions; gate at [caption.py `_transferred_hooks`](../src/fanops/caption.py) + the digest label. Firewall: `test_transfer_is_validation_frozen_until_learning_validated` ([test_variant_transfer.py](../tests/test_variant_transfer.py)) — [config.py:611](../src/fanops/config.py) |

(Other opt-in knobs — e.g. concurrent-source processing — follow the same explicit-on-word convention; grep
`config.py` for `os.getenv("FANOPS_` to enumerate.)

## Maintenance contract

- A new improvement flag MUST land with: a `config.py` property documenting its default + OFF/ON words, an OFF
  code path (early return — never a parallel implementation), a firewall test pinning the OFF contract, and a row
  in this table.
- If the flag can leak from a repo `.env` into a unit test and flip behavior, add it to `_LEAKY_ENV` in
  `tests/conftest.py`.
