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
| `hashtag_trends` | `FANOPS_HASHTAG_TRENDS=0` | **ON** | `hashtags refresh` samples **live Meta Graph** hashtag trends (closed-loop discovery) | own-reach-only refresh (also the automatic behavior when `META_GRAPH_TOKEN`/`META_IG_USER_ID` are absent — fail-open) |

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
- **Code:** [config.py:255](../src/fanops/config.py) (`def hashtag_trends`). Gates the **background** `hashtags refresh` Graph sampling only; the on-demand operator lookup (`meta_graph.tag_metrics`) is gated by creds + budget, never this flag.
- **OFF contract:** refresh is own-reach-only; the output `hashtags.json` shape is byte-identical to the live-sampled one (trends only re-rank, never change the schema). Fail-open: identical to ON when no Meta token is configured.
- **Firewall tests:** `test_hashtag_trends_default_on` + `test_hashtag_trends_explicit_off` ([test_graph_tag_metrics.py:93](../tests/test_graph_tag_metrics.py)).

## Notable default-OFF flags (opt-in; byte-identical when off)

These are observe-only or experimental; OFF is byte-identical to the pre-feature baseline. Enable only with the
explicit on-words `1`/`true`/`yes`/`on`.

| Flag | Env var (ON) | Default | Purpose |
|---|---|---|---|
| `hook_router` | `FANOPS_HOOK_ROUTER=1` | OFF | read-only Moment hook-strategy classifier (records an annotation, renders nothing) — [config.py:476](../src/fanops/config.py) |
| `variant_learning` | `FANOPS_VARIANT_LEARNING=1` | OFF | the A/B caption-bias learning loop (independent of `creative_variation`) — [config.py](../src/fanops/config.py) |

(Other opt-in knobs — e.g. concurrent-source processing — follow the same explicit-on-word convention; grep
`config.py` for `os.getenv("FANOPS_` to enumerate.)

## Maintenance contract

- A new improvement flag MUST land with: a `config.py` property documenting its default + OFF/ON words, an OFF
  code path (early return — never a parallel implementation), a firewall test pinning the OFF contract, and a row
  in this table.
- If the flag can leak from a repo `.env` into a unit test and flip behavior, add it to `_LEAKY_ENV` in
  `tests/conftest.py`.
