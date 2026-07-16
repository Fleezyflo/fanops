# FanOps

**MOH FLOW FAN OPS** — an intelligent clip + cross-post engine for fan accounts. FanOps ingests long-form video, picks the best moments, cuts platform-ready clips (per-account length, framing, and hooks), writes captions, and cross-posts to Instagram (via Postiz) and TikTok (via Zernio). A local web cockpit (**FanOps Studio**) is where you review clips, approve the queue, schedule posts, manage personas, and go live — without touching JSON or env vars by hand.

## Requirements

- Python 3.12 or 3.13
- ffmpeg / ffprobe on `PATH` (for clip rendering)
- Optional: OpenAI Whisper CLI (`pip install -e '.[transcribe]'`) for transcription

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,studio,transcribe,framing]'
./scripts/setup-hooks.sh            # repo policy hooks (idempotent)
```

## v0.1 quickstart

Prototype acceptance path — full checklist in **[docs/design/v0.1-ship-route.md](docs/design/v0.1-ship-route.md)**. Extended operator script: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

1. **Install** — `python3.12 -m venv .venv && source .venv/bin/activate` then `pip install -e '.[dev,studio,transcribe,framing]'` and `./scripts/setup-hooks.sh`
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
| `fanops doctor` | Read-only health / readiness screen |
| `fanops studio` | Local web UI (localhost:8787) |
| `fanops run` | One pipeline pass (ingest → clip → crosspost → publish due) |
| `fanops status` | Ledger counts |
| `fanops daemon` | Hands-off launchd driver (macOS) |

Full CLI surface: `fanops --help`. Environment variables and defaults: [docs/CONFIG.md](docs/CONFIG.md).

## Docs map

| Doc | What it covers |
|-----|----------------|
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | First end-to-end run (canonical operator script) |
| [docs/GOLIVE.md](docs/GOLIVE.md) | Publishing paths and safety |
| [docs/POSTIZ_SETUP.md](docs/POSTIZ_SETUP.md) | Stand up Postiz |
| [CLAUDE.md](CLAUDE.md) | Architecture notes for contributors |
| [AGENTS.md](AGENTS.md) | Agent / PR workflow |

## Governance & Constitution

The authoritative account of how the system is intended to be engineered, reconciled against the current
tree. Start with the Constitution; see the Laws for what is mechanically enforced.

| Doc | What it covers |
|-----|----------------|
| [docs/REPOSITORY_CONSTITUTION.md](docs/REPOSITORY_CONSTITUTION.md) | The rules (18 sections), each with its **true enforcement status** |
| [docs/ENGINEERING_PHILOSOPHY.md](docs/ENGINEERING_PHILOSOPHY.md) | The design instincts — the *why* behind the rules |
| [docs/ARCHITECTURAL_LAWS.md](docs/ARCHITECTURAL_LAWS.md) | The enforceable subset, with stable IDs + mechanisms (cross-refs the CI registry) |
| [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) | The **code-craft layer** (`STD-*`): naming, layout, boundaries, versioning, flags, test craft, observability — references the Laws/ADRs/registry, never restates them |
| [docs/adr/](docs/adr/) | Decision records (0100–0103) + the catalogue; formalization order in [FORMALIZATION_ROADMAP.md](docs/adr/FORMALIZATION_ROADMAP.md) |
| [docs/governance/](docs/governance/) | Evidence reconciliation, maintenance-automation design, implementation + standards roadmaps, and the [engineering scorecard](docs/governance/ENGINEERING_SCORECARD.md) |
| [.github/ci-control-registry.yml](.github/ci-control-registry.yml) | The CI control plane (single owner of control rows) |
| [docs/ARCHITECTURE_GOVERNANCE.md](docs/ARCHITECTURE_GOVERNANCE.md) | Generated architecture view (`tools/arch`) |

## Development

```bash
ruff check .                                    # lint
python -m pytest -q -m "not integration"        # fast unit suite (~CI)
./scripts/check.sh                              # scoped check before commit
```

## License

Private operator project — see repository settings.
