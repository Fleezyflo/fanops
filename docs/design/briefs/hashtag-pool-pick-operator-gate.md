# Hashtag pool→pick operator gate (G10 / WP7)

**Ticket:** hashtag-pool-pick-e2e · **Gate:** G10  
**Scope:** Live operator ledger + Studio cockpit. **No** `persona.hashtag_corpus` removal in this wave.

Tracked copy of `.superpowers/sdd/wp7-operator-checklist.md` (that path is gitignored). Run on the operator workspace (`FANOPS_ROOT` set). Capture evidence in the PR body or a dated operator note.

**Codemap:** [hashtag-lifecycle.md](../../CODEMAPS/hashtag-lifecycle.md)  
**Automated proxy (CI):** `tests/test_source_tags.py`, `tests/test_source_tag_lock.py`, `tests/test_caption.py` (pool→pick→ship block from WP5).

---

## Pre-flight

- [ ] `FANOPS_ROOT` points at the live operator tree (not a throwaway tmp clone).
- [ ] `./scripts/setup-hooks.sh` already run in this worktree (if landing from a dev branch).
- [ ] `fanops doctor` exit `0` on that root (baseline health before hashtag-specific checks).

---

## G10 checklist

### 1 · Sidecar row completeness (`researched_at`)

Every **native** source in the operator ledger must have a completed sidecar row in `00_control/source_tag_locks.json` — `researched_at` set (ISO timestamp). Empty `lock: []` after scrape is valid completion.

**How**

```bash
cd "$FANOPS_ROOT"
fanops hashtags discover
```

Or inspect the sidecar directly:

```bash
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["FANOPS_ROOT"])
sidecar = json.loads((root / "00_control/source_tag_locks.json").read_text())
from fanops.config import Config
from fanops.ledger import Ledger
cfg = Config(root=root)
led = Ledger.load(cfg)
native = [s.id for s in led.sources.values() if getattr(s, "origin_kind", "native") != "third_party"]
missing = [sid for sid in native if not (isinstance(sidecar.get(sid), dict) and (sidecar[sid].get("researched_at") or "").strip())]
print(f"native={len(native)} incomplete={len(missing)}")
for sid in missing[:20]:
    print("  MISSING researched_at:", sid)
PY
```

**Pass**

- [ ] `incomplete=0` (every native source has `researched_at`).
- [ ] `fanops hashtags discover` logs `state=ready` or `state=empty` for each native source (never `missing` / `in_progress` on a steady-state ledger).

---

### 2 · Studio `/hashtags` lock rows honest

Lock panel must show **`ready`** (lock has tags) or honest **`empty`** (scrape finished, zero admits). Rows must **not** sit in `missing` or `in_progress` once the unattended walk has had time to run.

**How**

1. `fanops studio` (or confirm resident Studio on `http://127.0.0.1:8787`).
2. Open **Hashtags** tab (`/hashtags`).
3. Read the header: `N of M sources have a completed lock`.
4. Scan the per-source table — note each row's state chip.

**Pass**

- [ ] `N == M` (all native sources completed).
- [ ] No row stuck `missing` or `in_progress` after at least one `fanops run` / daemon tick with scrape configured.
- [ ] `empty` rows are intentional (source produced zero admits), not a stuck scrape.

---

### 3 · Caption requests carry the lock menu (`hashtag_store`)

For a **sample** of clips past the caption gate, on-disk caption requests must embed the source lock as each surface's `hashtag_store` (same list as sidecar `lock`, not persona store ∪ corpus).

**How**

Pick 2–3 clips in `captions_requested` or `captioned` (Studio **Run** tab agent-gates list, or ledger query). For each `clip_id`:

```bash
REQ="$FANOPS_ROOT/00_control/agent_io/requests/captions__<clip_id>.request.json"
python3 -m json.tool "$REQ" | rg 'hashtag_store|lock_fingerprint|surface'
```

Cross-check against sidecar:

```bash
python3 - <<'PY'
import json, os, sys
from pathlib import Path
clip_id = sys.argv[1]
root = Path(os.environ["FANOPS_ROOT"])
req = json.loads((root / f"00_control/agent_io/requests/captions__{clip_id}.request.json").read_text())
sidecar = json.loads((root / "00_control/source_tag_locks.json").read_text())
from fanops.config import Config
from fanops.ledger import Ledger
cfg = Config(root=root)
led = Ledger.load(cfg)
clip = led.clips[clip_id]
moment = led.moments[clip.parent_id]
src_id = led.sources[moment.parent_id].id
lock = sidecar[src_id]["lock"]
for s in req.get("surfaces", []):
    store = s.get("hashtag_store")
    assert store == lock, (s.get("surface"), store, lock)
print("OK", clip_id, "surfaces=", len(req.get("surfaces", [])))
PY
<clip_id>
```

**Pass**

- [ ] Every sampled request surface has `hashtag_store` present.
- [ ] `hashtag_store` **equals** sidecar `lock` for that clip's source (byte-identical list order).
- [ ] When WP4 fingerprint is present: `lock_fingerprint` matches a fresh hash of the current lock (stale requests should have been reopened by `caption_request_stale` on the next pass).

---

### 4 · Ingested tags respect the lock (`meta_captions.hashtags`)

Shipped clip tags must be model picks **intersected** with the source lock, pick order, **≤ 4** tags.

**How** (sample the same clips as §3, or any `captioned` clip with `meta_captions`):

```bash
python3 - <<'PY'
import os, sys
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.caption_compose import _source_lock_tags
from fanops.hashtags import _norm
clip_id = sys.argv[1]
cfg = Config(root=os.environ["FANOPS_ROOT"])
led = Ledger.load(cfg)
clip = led.clips[clip_id]
moment = led.moments[clip.parent_id]
src = led.sources[moment.parent_id]
lock = set(_norm(t) for t in _source_lock_tags(cfg, src))
mc = clip.meta_captions or {}
for surf, body in mc.items():
    tags = body.get("hashtags") or []
    assert len(tags) <= 4, (surf, len(tags))
    bad = [t for t in tags if _norm(t) not in lock]
    assert not bad, (surf, bad, list(lock)[:8])
    print("OK", surf, tags)
PY
<clip_id>
```

**Pass**

- [ ] `len(hashtags) ≤ 4` on every sampled surface.
- [ ] Every tag ∈ source lock (normalized `#` form).
- [ ] No surface ships tags outside the lock menu.

---

### 5 · IG/TT wire text = `posted_text_for` re-intersection

Published (or dryrun-previewed) Instagram/TikTok text must match `posted_text_for` — sentence + `ship_from_lock(post.hashtags, source lock)`.

**How**

1. Pick one **queued** or **published** IG/TT post tied to a captioned clip (Studio **Review** / **Schedule** / **Posted**).
2. Read Studio preview caption (Review uses `posted_text_for` for IG/TT).
3. Confirm against compose helper:

```bash
python3 - <<'PY'
import os, sys
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.caption_compose import posted_text_for
post_id = sys.argv[1]
cfg = Config(root=os.environ["FANOPS_ROOT"])
led = Ledger.load(cfg)
post = led.posts[post_id]
print(posted_text_for(cfg, led, post))
PY
<post_id>
```

4. **Live:** compare Postiz/Zernio published `content` for that post to the string above (per `src/fanops/post/CLAUDE.md`).

**Pass**

- [ ] Studio preview text matches `posted_text_for` output for the sampled post.
- [ ] Live permalink caption (IG/TT) matches `posted_text_for` (tags are lock ∩ picks, not raw model overflow).
- [ ] YouTube posts are out of scope (they send `Post.caption` raw).

---

### 6 · Scrape plane healthy (`FANOPS_IG_SCRAPE_USER`, no perpetual `no_scrape`)

Lock produce and remesure share the Safari scrape plane. Session must be configured and not abort every source with `no_scrape`.

**How**

```bash
# Config present
rg '^FANOPS_IG_SCRAPE_USER=' "$FANOPS_ROOT/.env"

# Doctor (when scrape is configured — check is N/A if unset)
fanops doctor | rg -i 'hashtag Layer A scrape'

# Session recovery if doctor omits the check or scrape aborts
fanops hashtags scrape-login   # Safari tab; operator logs in manually

# Run log — should NOT see no_scrape stamped on every lock walk forever
rg 'no_scrape' "$FANOPS_ROOT/00_control/run.log" | tail -20
```

**Pass**

- [ ] `FANOPS_IG_SCRAPE_USER` set (comma-separated peers OK).
- [ ] `fanops doctor` shows **PASS** for `hashtag Layer A scrape session present` when scrape is configured (N/A is acceptable only before first `scrape-login`).
- [ ] After `scrape-login`, lock walks advance (`researched_at` stamps) — not perpetual `no_scrape` on every native source.
- [ ] `fanops hashtags refresh` (manual remesure) does not abort `no_scrape` when session is healthy.

---

## Sign-off

| Field | Value |
|-------|-------|
| Operator | |
| Date (UTC) | |
| `FANOPS_ROOT` | |
| Branch / PR | `hashtag-pool-pick-e2e` |
| Native sources (count) | |
| Sample clip ids (§3–§4) | |
| Sample post id (§5) | |
| All G10 boxes checked | ☐ |

**Explicit non-goals for this gate:** deleting or editing `persona.hashtag_corpus`; Layer B / `vet_hashtags` revival; persona-store ∪ corpus on the posted line.
