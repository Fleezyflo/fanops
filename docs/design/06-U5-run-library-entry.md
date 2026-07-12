# 06-U5 — Run → Library entry

Wire Run tab recoverable-source affordances to the existing Library per-source pipeline map (`/library/<source_id>`) as the canonical diagnostic entry — mirroring the S07 review-entry pattern. One `run_next_step` ladder insert plus template deep-links only; no new routes, actions, or detail-page duplication.

## Objective

When sources are recoverable (`error` / `moments_empty`), the operator's next action is diagnosis and recovery. The Library per-source detail page already exposes the pipeline map, transcript slice, and stage context. Run tab should deep-link there instead of leaving recovery as a bare `/run` scroll hunt.

## Scope

### In scope

1. **`run_next_step` ladder** — insert `recover` after `add`, before `gate`:
   | Priority | key | when |
   |----------|-----|------|
   | 1 | add | `native_total + third_party == 0` |
   | 2 | recover | `sources_recoverable > 0` (existing count; fail-open via `_n`) |
   | 3 | gate | pending gate counts > 0 |
   | 4 | review | `awaiting > 0` |
   | 5 | prepare | default |

   Return shape unchanged: `{key, label, hint}`. Hint names Library explicitly.

2. **`build_system_strip`** — when `pipeline_status` succeeds, set `errored_first_id = (ps.get("errored") or [{}])[0].get("id")` alongside existing `errored_sources` count. No extra `Ledger.load`.

3. **Templates (no new endpoints)**
   - `_run_next.html`: When `step.key == 'recover'` and `status.errored`, link first source name → `url_for('library_source', source_id=status.errored[0].id)`; plural count in label.
   - `_run_panel.html`: Wrap errored `<strong>{{ e.name }}</strong>` in `<a href="...library_source...">`; keep Resume/Reset forms on Run unchanged. Add `recover` to `is-live` class map (`_next.key in ('add',)` → include `'recover'`).
   - `_system_strip.html`: Change errored alert href from `run_panel` → first errored source's `library_source` when id known; fallback `run_panel` if list empty.

4. **`studio.css`** — Add `.run-next-recover` tint using existing warn/danger tokens (mirror `.run-next-gate`). No layout changes.

### Out of scope

- Resume/Reset on `library_source_panel`
- New `/library?bucket=recoverable` filter
- `spine` / `build_spine` changes
- `library_handoff()` helper
- v0.1 ship-route / README edits
- Codemap regeneration

## Acceptance checklist

- `sources_recoverable > 0` → `run_next_step.key == "recover"` (beats gate, review, prepare)
- `/run` errored source names link to `/library/<id>`; Resume/Reset still work on Run
- System strip errored alert links to first source's library detail (not bare `/run`)
- `recover` absent when `sources_recoverable == 0`
- `./scripts/check.sh` green; no new swallow sites

## Files touched

- `docs/design/06-U5-run-library-entry.md` (this document)
- `src/fanops/studio/views.py`
- `src/fanops/studio/templates/_run_next.html`
- `src/fanops/studio/templates/_run_panel.html`
- `src/fanops/studio/templates/_system_strip.html`
- `src/fanops/studio/static/studio.css`
- `tests/test_studio_run.py`
- `tests/test_studio_errored_sources.py`

## Tests

### `tests/test_studio_run.py`

```python
def test_run_next_step_recover_before_gate():
    n = views.run_next_step(_st(sources=1, sources_recoverable=1, pending_moments=2))
    assert n["key"] == "recover"

def test_run_next_step_recover_before_prepare():
    assert views.run_next_step(_st(sources=2, sources_recoverable=1))["key"] == "recover"

def test_run_next_step_recover_before_review():
    assert views.run_next_step(_st(sources=2, sources_recoverable=1, awaiting=4))["key"] == "recover"
```

### `tests/test_studio_errored_sources.py`

```python
def test_strip_errored_links_library_detail(tmp_path, monkeypatch):
    # seed one errored source; GET / → strip alert href contains /library/src_1

def test_run_errored_name_links_library_detail(tmp_path, monkeypatch):
    # GET /run → errored row name href contains /library/src_1; Resume form still present

def test_run_next_recover_links_library(tmp_path, monkeypatch):
    # stub pipeline_status with recoverable; GET /run → run-next-recover + /library/ link
```

## Pattern reference (S07 review-entry)

S07 wired `run_next_step.key == 'review'` to deep-link into Review via `review_handoff` in `_run_next.html`. U5 mirrors that pattern for recoverable sources → Library detail, reusing `pipeline_status["errored"]` rows already on the Run panel.
