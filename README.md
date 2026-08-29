# FanOps

**MOH FLOW FAN OPS** — an intelligent clip + cross-post engine for fan accounts. FanOps ingests long-form video, picks the best moments, cuts platform-ready clips (per-account length, framing, and hooks), writes captions, and cross-posts to Instagram (via Postiz) and TikTok (via Zernio). A local web cockpit (**FanOps Studio**) is where you review clips, approve the queue, schedule posts, manage personas, and go live — without touching JSON or env vars by hand.

## Requirements

- Python 3.12 or 3.13
- ffmpeg / ffprobe on `PATH` (for clip rendering)
- `[asr]` extra (faster-whisper + Demucs) — required to cut clips. There is no whisper-CLI fallback.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,studio,asr,framing]'
./scripts/setup-hooks.sh            # repo policy hooks (idempotent)
```

## v0.1 quickstart

Prototype acceptance path — full checklist in **[docs/design/v0.1-ship-route.md](docs/design/v0.1-ship-route.md)**. Extended operator script: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

1. **Install** — `python3.12 -m venv .venv && source .venv/bin/activate` then `pip install -e '.[dev,studio,asr,framing]'` and `./scripts/setup-hooks.sh`
2. **Doctor** — `fanops doctor` exits 0 (green)
3. **Studio + Go Live intake (S04)** — `fanops studio` → http://127.0.0.1:8787; Go-Live tab: connect Postiz (**Save & test**), add account(s), map each channel to Postiz integration; channel readiness matrix shows all active channels ready
4. **Upload (S02)** — Run tab: Upload video → Ingest inbox (chunked resumable upload)
5. **Prepare + Review focus (S07)** — Run tab: Prepare everything; Review tab: bare `/review` account-first picker/auto-focus, approve selected
6. **Schedule → publish** — Go live if not already (confirm checkbox); Schedule tab or Run tab publish with live confirm; post ships to real account
7. **Metrics + S06 rotation** — Posted tab shows live URL; metrics arrive (timestamp); two consecutive posts for same account show differing hashtag tag lines

Learning ships frozen-as-built (gated P4 dims + timing; hashtag judge = live Graph reach).

> **Safety:** Nothing auto-publishes. Every post is born `awaiting_approval`; only posts you approve in Review enter the publish queue.

## Daily loop

After the first run: upload → prepare → review/approve → schedule (if needed) → run pass. The Studio **Home** tab shows counts and links to whatever needs attention.

## Commands (quick reference)

| Command | Purpose |
|---------|---------|
| `fanops doctor` | **Primary** machine-health / readiness screen |
| `fanops studio` | Local web UI (localhost:8787) — strip/Go-Live project doctor |
| `fanops run` | One pipeline pass (ingest → clip → crosspost → publish due) |
| `fanops status` | Ledger backlog counts (not a third healthy definition) |
| `fanops daemon` | Hands-off launchd driver (macOS) |

Full CLI surface: `fanops --help`. Environment variables and defaults: [docs/CONFIG.md](docs/CONFIG.md).
Machine-health channels: [docs/MACHINE_HEALTH.md](docs/MACHINE_HEALTH.md) (`/healthz` = process-only).

## Docs map

| Doc | What it covers |
|-----|----------------|
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | First end-to-end run (canonical operator script) |
| [docs/GOLIVE.md](docs/GOLIVE.md) | Publishing paths and safety |
| [docs/POSTIZ_SETUP.md](docs/POSTIZ_SETUP.md) | Stand up Postiz |
| [docs/MACHINE_HEALTH.md](docs/MACHINE_HEALTH.md) | Primary = doctor; projections; `/healthz` process-only |
| [docs/ENFORCEMENT.md](docs/ENFORCEMENT.md) | What actually enforces each rule |
| [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) | Craft guidance (not a gate) |

## Rules and how they are enforced

A rule ships as a mechanism or it does not ship. The governance-prose layer — constitution, laws, philosophy,
decision records — was deleted in the 2026-07 theatre census because its stated gates were backed by nothing;
`tests/test_governance_tombstone.py` keeps those namespaces dead, so do not recreate them. Decision rationale
now lives in the pull request that lands the change: main history is the decision record.

| Doc | What it covers |
|-----|----------------|
| [docs/ENFORCEMENT.md](docs/ENFORCEMENT.md) | **The index of what actually enforces** — merge gates, unit-lane validators, runtime product gates |
| [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) | The **code-craft layer** (`STD-*`): naming, layout, boundaries, versioning, flags, test craft, observability — guidance, not a gate |
| [.github/ci-control-registry.yml](.github/ci-control-registry.yml) | The CI control plane (single owner of control rows) |

## Development

```bash
ruff check .                                    # lint
./scripts/check.sh                              # scoped check before commit
```

**Do not run the test suite locally.** `pytest` executes only in GitHub CI on a pull request — parallel local
suites crash the operator's machine. The `unit` job is the required check; the real-tooling `e2e` job runs on
manual dispatch and nightly only. See [docs/ENFORCEMENT.md](docs/ENFORCEMENT.md) and [tests/CLAUDE.md](tests/CLAUDE.md).

## License

Private operator project — see repository settings.
