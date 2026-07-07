# tests/test_no_ghosts.py — P15 ghost-sweep: removed symbols stay absent (audit corrections excluded).
import ast
import importlib
from pathlib import Path
import pytest
from fanops.models import Post, Moment, MomentHookDecision
from fanops.prompts import moment_pick_prompt, moment_hook_prompt, caption_prompt

_ROOT = Path(__file__).resolve().parents[1] / "src" / "fanops"

_GHOSTS = [
    "hooks_by_persona", "hooks_by_persona_removed",
    "AccountSelection", "SelectionMethod", "SelectionFact",
    "_stage_casting", "request_moment_casting", "ingest_moment_casting",
    "moment_casting_prompt", "scoped_caption_surfaces",
    "casting_bias", "casting_reach_prior",
    "MomentCastingRequest", "MomentCastingDecision",
    "variant_key", "moments_wait_cycles", "moments_skipped_handles",
]

_PIPELINE_GHOSTS = [
    "variant_hook", "creative_variation", "FANOPS_CREATIVE_VARIATION", "HookSource.per_account",
]

_KEEP = {"degraded_reason", "render_account_cut", "account_render_spec", "render_account_file", "is_account_cut"}


def _strip_docstrings(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            node.body.pop(0)
    return ast.unparse(tree)


def _py_files(*, exclude_studio: bool = False):
    for p in _ROOT.rglob("*.py"):
        if exclude_studio and "studio" in p.parts:
            continue
        yield p


def _find_ghosts(text: str, ghosts: list[str]) -> list[str]:
    return [g for g in ghosts if g not in _KEEP and g in text]


def test_model_fields_have_no_hooks_by_persona():
    assert "hooks_by_persona" not in Moment.model_fields
    assert "hooks_by_persona_removed" not in Moment.model_fields
    assert "hooks_by_persona" not in MomentHookDecision.model_fields
    assert "variant_hook" not in Post.model_fields
    assert "variant_key" not in Post.model_fields


def test_ghost_types_not_importable():
    import fanops.models as models
    for name in ("AccountSelection", "SelectionMethod", "SelectionFact",
                 "MomentCastingRequest", "MomentCastingDecision"):
        assert not hasattr(models, name)
    with pytest.raises(ImportError):
        from fanops.casting import scoped_caption_surfaces  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("fanops.casting_bias")


def test_pipeline_ghosts_absent_outside_studio():
    hits = {}
    for p in _py_files(exclude_studio=True):
        body = _strip_docstrings(p.read_text(encoding="utf-8", errors="replace"))
        found = _find_ghosts(body, _GHOSTS + _PIPELINE_GHOSTS)
        if found:
            hits[str(p.relative_to(_ROOT.parents[1]))] = found
    assert hits == {}, f"pipeline ghosts found: {hits}"


def test_core_ghosts_absent_in_executable_src():
    hits = {}
    for p in _py_files(exclude_studio=False):
        body = _strip_docstrings(p.read_text(encoding="utf-8", errors="replace"))
        found = _find_ghosts(body, _GHOSTS)
        if found:
            hits[str(p.relative_to(_ROOT.parents[1]))] = found
    assert hits == {}, f"core ghosts found: {hits}"


def test_prompts_have_no_bilingual_rapper_preamble():
    payload = {"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""}
    hook = {**payload, "start": 0, "end": 7, "reason": "r", "frames": [], "transcript_excerpt": "x"}
    cap = {"clip_id": "c1", "language": "en", "guidance": "", "transcript_excerpt": "x",
           "surfaces": [{"surface": "a/instagram", "platform": "instagram"}]}
    for fn, pl in ((moment_pick_prompt, payload), (moment_hook_prompt, hook), (caption_prompt, cap)):
        low = fn(pl).lower()
        assert "bilingual" not in low and "en/ar rapper" not in low


def test_caption_prompt_has_no_genre_recipe_force_backfill():
    p = caption_prompt({"clip_id": "c1", "language": "en", "guidance": "", "transcript_excerpt": "x",
                        "surfaces": [{"surface": "a/instagram", "platform": "instagram", "genre": "gossip"}]})
    assert "#hiphop/#rap" not in p and "#rapper/#bars" not in p
