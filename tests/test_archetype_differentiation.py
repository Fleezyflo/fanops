# tests/test_archetype_differentiation.py — P15: credibility_first vs controversy_seeking diverge at the prompt.
from fanops.config import Config
from tests.test_persona_fixtures import ensure_archetype_personas
from fanops.moments import _pick_personas
from fanops.prompts import moment_pick_prompt, moment_hook_prompt
from fanops.persona_directives import casting_directive, hook_directive


def _base_source_payload():
    return {"duration": 90.0, "transcript": [{"start": 0, "end": 30, "text": "they lied about the deal"}],
            "signal_peaks": [{"t": 12.0, "kind": "scene_cut", "score": 0.7}],
            "language": "en", "guidance": ""}


def _seed_archetype_accounts(cfg):
    return ensure_archetype_personas(cfg)


def test_credibility_vs_controversy_pick_prompts_diverge(tmp_path):
    cfg = Config(root=tmp_path); accts = _seed_archetype_accounts(cfg)
    specs = _pick_personas(cfg, accts)
    assert len(specs) == 2
    by_handle = {s["handle"]: s for s in specs}
    trust_scope = (by_handle["trust"]["selection_scope"] or "").lower()
    drama_scope = (by_handle["drama"]["selection_scope"] or "").lower()
    assert trust_scope != drama_scope
    assert "sensational" in trust_scope or "accurate" in trust_scope
    assert "inflammatory" in drama_scope or "rivalry" in drama_scope

    pick_trust = moment_pick_prompt({**_base_source_payload(), "personas": [by_handle["trust"]]})
    pick_drama = moment_pick_prompt({**_base_source_payload(), "personas": [by_handle["drama"]]})
    assert pick_trust != pick_drama
    assert by_handle["trust"]["selection_scope"] in pick_trust or "sensational" in pick_trust.lower()
    assert by_handle["drama"]["selection_scope"] in pick_drama or "inflammatory" in pick_drama.lower()


def test_credibility_vs_controversy_hook_prompts_diverge(tmp_path):
    cfg = Config(root=tmp_path); accts = _seed_archetype_accounts(cfg)
    window = {"start": 10.0, "end": 22.0, "reason": "the rivalry line", "transcript_excerpt": "they started it",
              "language": "en", "guidance": "", "frames": [], "signal_peaks": []}
    trust_a = next(a for a in accts.accounts if a.handle == "trust")
    drama_a = next(a for a in accts.accounts if a.handle == "drama")
    trust_p = {"handle": "trust", "persona": str(hook_directive(trust_a))}
    drama_p = {"handle": "drama", "persona": str(hook_directive(drama_a))}
    hook_trust = moment_hook_prompt({**window, "personas": [trust_p]})
    hook_drama = moment_hook_prompt({**window, "personas": [drama_p]})
    assert hook_trust != hook_drama
    trust_cd = casting_directive(trust_a)
    drama_cd = casting_directive(drama_a)
    assert trust_cd.scope_lens and drama_cd.scope_lens
    assert trust_cd.scope_lens != drama_cd.scope_lens
