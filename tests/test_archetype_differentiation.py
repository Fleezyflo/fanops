# tests/test_archetype_differentiation.py — P15: credibility_first vs controversy_seeking diverge at the prompt.
import re
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


def _strip_handles(text, *handles):
    """Replace account handles so a handle-only difference cannot pass as lens divergence."""
    out = text
    for h in handles:
        out = re.sub(rf"@?{re.escape(h)}\b", "@H", out)
    return out


def test_credibility_vs_controversy_pick_prompts_diverge(tmp_path):
    cfg = Config(root=tmp_path); accts = _seed_archetype_accounts(cfg)
    specs = _pick_personas(cfg, accts)
    assert len(specs) == 3
    by_handle = {s["handle"]: s for s in specs}
    trust_a = next(a for a in accts.accounts if a.handle == "trust")
    drama_a = next(a for a in accts.accounts if a.handle == "drama")
    trust_cd, drama_cd = casting_directive(trust_a), casting_directive(drama_a)
    # identical compiled lenses must fail — compare directive bodies, not handles
    assert str(trust_cd) != str(drama_cd)
    assert trust_cd.scope_lens != drama_cd.scope_lens
    assert trust_cd.select_rule != drama_cd.select_rule
    pick_trust = moment_pick_prompt({**_base_source_payload(), "personas": [by_handle["trust"]]})
    pick_drama = moment_pick_prompt({**_base_source_payload(), "personas": [by_handle["drama"]]})
    assert "@trust" in pick_trust and "@drama" in pick_drama
    assert _strip_handles(pick_trust, "trust", "drama") != _strip_handles(pick_drama, "trust", "drama")


def test_credibility_vs_controversy_hook_prompts_diverge(tmp_path):
    cfg = Config(root=tmp_path); accts = _seed_archetype_accounts(cfg)
    window = {"start": 10.0, "end": 22.0, "reason": "the rivalry line", "transcript_excerpt": "they started it",
              "language": "en", "guidance": "", "frames": [], "signal_peaks": []}
    trust_a = next(a for a in accts.accounts if a.handle == "trust")
    drama_a = next(a for a in accts.accounts if a.handle == "drama")
    trust_hook, drama_hook = hook_directive(trust_a), hook_directive(drama_a)
    assert str(trust_hook) != str(drama_hook)
    assert trust_hook.mechanism_lean != drama_hook.mechanism_lean
    trust_p = {"handle": "trust", "persona": str(trust_hook)}
    drama_p = {"handle": "drama", "persona": str(drama_hook)}
    hook_trust = moment_hook_prompt({**window, "personas": [trust_p]})
    hook_drama = moment_hook_prompt({**window, "personas": [drama_p]})
    assert _strip_handles(hook_trust, "trust", "drama") != _strip_handles(hook_drama, "trust", "drama")
