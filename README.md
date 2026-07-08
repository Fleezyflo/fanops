# FanOps

**MOH FLOW FAN OPS** — an intelligent clip + cross-post engine for fan accounts. FanOps ingests long-form video, picks the best moments, cuts platform-ready clips (per-account length, framing, and hooks), writes captions, and cross-posts to Instagram (via Postiz) and TikTok (via Zernio). A local web cockpit (**FanOps Studio**) is where you review clips, approve the queue, schedule posts, manage personas, and go live — without touching JSON or env vars by hand.

## Requirements

- Python 3.12 or 3.13
- ffmpeg / ffprobe on `PATH` (for clip rendering)
- Optional: OpenAI Whisper CLI (`pip install -e '.[transcribe]'`) for transcription

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,studio]'    # studio = the web cockpit (Flask)
./scripts/setup-hooks.sh            # repo policy hooks (idempotent)
```

## Launch sequence

Follow these steps once to go from zero to a live post. The full walkthrough with exact UI controls and success criteria is in **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

1. **Health check** — `fanops doctor` (read-only; fix anything flagged).
2. **Open Studio** — `fanops studio` → http://127.0.0.1:8787
3. **Connect Postiz** — Go-Live tab → paste Postiz URL + API key → Save & test. See [docs/POSTIZ_SETUP.md](docs/POSTIZ_SETUP.md) if Postiz is not running yet.
4. **Add footage** — Run tab → Upload video → Ingest inbox.
5. **Prepare clips** — Run tab → Prepare everything (or answer gates in the Gates tab if autopilot is off).
6. **Review & approve** — Review tab → watch clips → Approve selected.
7. **Map accounts** — Go-Live tab → add handles, map each channel to its Postiz integration.
8. **Go live** — Go-Live tab → confirm → **GO LIVE** (publishes to real accounts only after approval).
9. **Publish** — Run tab → Prepare everything (with live confirm) or let the daemon tick.

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

## Development

```bash
ruff check .                                    # lint
python -m pytest -q -m "not integration"        # fast unit suite (~CI)
./scripts/check.sh                              # scoped check before commit
```

## License

Private operator project — see repository settings.
